from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

from core_types import (
    AssetCategory,
    ErrorCode,
    InboundMessage,
    NotificationChannel,
    NotificationDeliveryAttempt,
    NotificationMessage,
    RiskLevel,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.notification_repo import NotificationRepository
from app.schemas.assets import AssetCreateRequest, AssetQueryRequest, CapabilityGrantCreateRequest
from app.schemas.notifications import (
    InboundMessageCreateRequest,
    NotificationChannelCreateRequest,
    NotificationChannelTestRequest,
    NotificationChannelUpdateRequest,
    NotificationMessageCreateRequest,
)
from app.services.approvals import ApprovalService
from app.services.asset import AssetService
from app.services.asset_broker import AssetBrokerService
from app.services.audit import AuditEventService
from app.services.capability import CapabilityGraphService

DEFAULT_CHANNEL_POLICY = {
    "allow_inbound": True,
    "allow_outbound": True,
    "max_message_length": 2000,
    "requires_approval_for_external": True,
    "external_provider_default": "disabled",
}

SECRET_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bapi[_-]?key\s*[:=]\s*[^\s]+",
        r"\btoken\s*[:=]\s*[^\s]+",
        r"\bcookie\s*[:=]\s*[^\s]+",
        r"\bprivate[_-]?key\s*[:=]\s*[^\s]+",
        r"\bmnemonic\s*[:=]\s*[^\s]+",
        r"sk-[A-Za-z0-9_\-]{10,}",
        r"C:\\Users\\[^\\\s]+\\[^\s]+",
    ]
]


@dataclass(frozen=True)
class ProviderDeliveryResult:
    status: str
    provider_message_id: str | None = None
    response_summary: dict[str, Any] | None = None
    error_code: str | None = None
    error_summary: str | None = None
    latency_ms: int = 0


class ChannelProvider(Protocol):
    async def send(
        self,
        *,
        channel: NotificationChannel,
        message: NotificationMessage,
    ) -> ProviderDeliveryResult:
        ...


class LocalMockProvider:
    async def send(
        self,
        *,
        channel: NotificationChannel,
        message: NotificationMessage,
    ) -> ProviderDeliveryResult:
        del channel
        return ProviderDeliveryResult(
            status="sent",
            provider_message_id=f"local:{message.notification_id}",
            response_summary={"stored_locally": True},
        )


class DisabledProvider:
    def __init__(self, provider: str) -> None:
        self._provider = provider

    async def send(
        self,
        *,
        channel: NotificationChannel,
        message: NotificationMessage,
    ) -> ProviderDeliveryResult:
        del channel, message
        return ProviderDeliveryResult(
            status="failed",
            error_code="provider_disabled",
            error_summary=f"{self._provider} provider is disabled in local backend",
            response_summary={"degraded": True, "fallback": "local_pending_notification"},
        )


class NotificationGatewayService:
    def __init__(
        self,
        *,
        repo: NotificationRepository,
        asset_service: AssetService,
        asset_broker: AssetBrokerService,
        capability: CapabilityGraphService,
        approval_service: ApprovalService,
        trace_service: TraceService,
        audit_service: AuditEventService,
        task_engine: Any | None = None,
    ) -> None:
        self._repo = repo
        self._assets = asset_service
        self._asset_broker = asset_broker
        self._capability = capability
        self._approvals = approval_service
        self._trace = trace_service
        self._audit = audit_service
        self._task_engine = task_engine
        self._providers: dict[str, ChannelProvider] = {
            "local_mock": LocalMockProvider(),
            "webhook": DisabledProvider("webhook"),
            "email_smtp": DisabledProvider("email_smtp"),
        }

    def set_task_engine(self, task_engine: Any) -> None:
        self._task_engine = task_engine

    def register_provider(self, provider: str, runtime: ChannelProvider) -> None:
        self._providers[provider] = runtime

    async def create_channel(
        self,
        request: NotificationChannelCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> NotificationChannel:
        _reject_inline_secret_config(request.provider_config)
        now = utc_now_iso()
        if request.asset_id and not request.create_asset:
            asset_id = request.asset_id
        else:
            asset = await self._assets.create_asset(
                AssetCreateRequest(
                    asset_type=AssetCategory.ACCOUNT,
                    display_name=f"消息渠道：{request.display_name}",
                    provider=request.provider,
                    sensitivity=request.sensitivity,
                    config={
                        "platform": "message_channel",
                        "username": request.display_name,
                        "auth_type": "notification_gateway",
                        "provider": request.provider,
                    },
                    secret_value=request.secret_value,
                    owner_scope_type="member",
                    owner_scope_id=request.created_by_member_id,
                    visibility="private",
                    risk_level=RiskLevel.R2,
                    summary_text=f"{request.display_name} message channel asset",
                    capabilities=[
                        "message_channel",
                        "notification.outbound",
                        "notification.inbound",
                    ],
                    policy={"notification_gateway": True},
                    metadata={"provider": request.provider, "channel_type": request.channel_type},
                ),
                trace_id=trace_id,
            )
            asset_id = asset.asset_id
            await self._grant_channel_actions(
                asset_id,
                request.created_by_member_id,
                trace_id=trace_id,
            )
        channel_id = new_id("nch")
        policy = {**DEFAULT_CHANNEL_POLICY, **request.policy}
        data = {
            "channel_id": channel_id,
            "organization_id": "org_default",
            "asset_id": asset_id,
            "provider": request.provider,
            "display_name": request.display_name,
            "channel_type": request.channel_type,
            "status": "active",
            "sensitivity": request.sensitivity,
            "policy": redact(policy),
            "provider_config": redact(request.provider_config),
            "last_health_status": (
                "healthy"
                if request.provider in {"local_mock", "wechat_mock"}
                else "unknown"
                if request.provider == "wechat"
                else "disabled"
            ),
            "last_error": None,
            "created_by_member_id": request.created_by_member_id,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_channel(data)
        await self._audit.write_event(
            actor_type="system",
            action="notification.channel.created",
            object_type="notification_channel",
            object_id=channel_id,
            summary="通知渠道已创建",
            risk_level=RiskLevel.R2,
            payload={
                "channel_id": channel_id,
                "provider": request.provider,
                "asset_id": asset_id,
            },
            trace_id=trace_id,
        )
        return await self.get_channel(channel_id)

    async def list_channels(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[NotificationChannel]:
        return [
            NotificationChannel(**row)
            for row in await self._repo.list_channels(status=status, limit=limit)
        ]

    async def get_channel(self, channel_id: str) -> NotificationChannel:
        row = await self._repo.get_channel(channel_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "通知渠道不存在", status_code=404)
        return NotificationChannel(**row)

    async def update_channel(
        self,
        channel_id: str,
        request: NotificationChannelUpdateRequest,
        *,
        trace_id: str | None = None,
    ) -> NotificationChannel:
        await self.get_channel(channel_id)
        fields = request.model_dump(exclude_unset=True, mode="json")
        if "provider_config" in fields and fields["provider_config"] is not None:
            _reject_inline_secret_config(fields["provider_config"])
            fields["provider_config"] = redact(fields["provider_config"])
        if "policy" in fields and fields["policy"] is not None:
            fields["policy"] = redact(fields["policy"])
        fields["updated_at"] = utc_now_iso()
        await self._repo.update_channel(channel_id, fields)
        await self._audit.write_event(
            actor_type="system",
            action="notification.channel.updated",
            object_type="notification_channel",
            object_id=channel_id,
            summary="通知渠道已更新",
            risk_level=RiskLevel.R1,
            payload={"changed_fields": sorted(fields)},
            trace_id=trace_id,
        )
        return await self.get_channel(channel_id)

    async def update_channel_status(
        self,
        channel_id: str,
        status: str,
        *,
        trace_id: str | None = None,
    ) -> NotificationChannel:
        await self.get_channel(channel_id)
        await self._repo.update_channel(
            channel_id,
            {"status": status, "updated_at": utc_now_iso()},
        )
        await self._audit.write_event(
            actor_type="system",
            action="notification.channel.status_updated",
            object_type="notification_channel",
            object_id=channel_id,
            summary="通知渠道状态已更新",
            risk_level=RiskLevel.R2,
            payload={"status": status},
            trace_id=trace_id,
        )
        return await self.get_channel(channel_id)

    async def test_channel(
        self,
        channel_id: str,
        request: NotificationChannelTestRequest,
        *,
        trace_id: str | None = None,
    ) -> NotificationMessage:
        return await self.create_message(
            NotificationMessageCreateRequest(
                channel_id=channel_id,
                message_type="system_degraded",
                recipient=request.recipient,
                subject=request.subject,
                body=request.body,
                metadata={"test_message": True},
            ),
            trace_id=trace_id,
        )

    async def create_message(
        self,
        request: NotificationMessageCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> NotificationMessage:
        channel = await self.get_channel(request.channel_id)
        policy = channel.policy or {}
        if not policy.get("allow_outbound", True):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "通知渠道禁止出站消息",
                status_code=403,
            )
        subject, body, dlp_summary = _redact_outbound(
            subject=request.subject,
            body=request.body,
            max_length=int(policy.get("max_message_length") or 2000),
        )
        blocked = bool(dlp_summary.get("blocked"))
        notification_id = new_id("ntf")
        now = utc_now_iso()
        status = "blocked" if blocked else "queued"
        data = {
            "notification_id": notification_id,
            "organization_id": channel.organization_id,
            "channel_id": channel.channel_id,
            "task_id": request.task_id,
            "scheduled_task_id": request.scheduled_task_id,
            "scheduled_run_id": request.scheduled_run_id,
            "approval_id": request.approval_id,
            "message_type": request.message_type,
            "recipient": str(redact(request.recipient)),
            "status": status,
            "subject_redacted": subject,
            "body_redacted": body,
            "dlp_summary": dlp_summary,
            "retry_count": 0,
            "max_retries": 3,
            "failure_reason": "dlp_sensitive_content_blocked" if blocked else None,
            "metadata": redact(request.metadata),
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_message(data)
        await self._audit.write_event(
            actor_type="system",
            action="notification.message.created",
            object_type="notification_message",
            object_id=notification_id,
            summary="通知消息已创建",
            risk_level=(
                RiskLevel.R2
                if request.message_type == "approval_required"
                else RiskLevel.R1
            ),
            payload={
                "notification_id": notification_id,
                "channel_id": channel.channel_id,
                "message_type": request.message_type,
                "status": status,
            },
            trace_id=trace_id,
        )
        if request.send_immediately and not blocked:
            return await self.send_message(notification_id, trace_id=trace_id)
        return await self.get_message(notification_id)

    async def list_messages(
        self,
        *,
        channel_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[NotificationMessage]:
        return [
            NotificationMessage(**row)
            for row in await self._repo.list_messages(
                channel_id=channel_id,
                status=status,
                limit=limit,
            )
        ]

    async def get_message(self, notification_id: str) -> NotificationMessage:
        row = await self._repo.get_message(notification_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "通知消息不存在", status_code=404)
        return NotificationMessage(**row)

    async def send_message(
        self,
        notification_id: str,
        *,
        trace_id: str | None = None,
    ) -> NotificationMessage:
        message = await self.get_message(notification_id)
        channel = await self.get_channel(message.channel_id)
        if message.status == "blocked":
            return message
        if channel.status != "active":
            return await self._mark_delivery_failure(
                message,
                channel=channel,
                error_code="channel_not_active",
                error_summary=f"channel_{channel.status}",
                response_summary={"retryable": True, "channel_status": channel.status},
                trace_id=trace_id,
            )
        await self._validate_channel_asset(channel, trace_id=trace_id)
        provider = self._providers.get(channel.provider, DisabledProvider(channel.provider))
        started = time.perf_counter()
        result = await provider.send(channel=channel, message=message)
        latency_ms = result.latency_ms or int((time.perf_counter() - started) * 1000)
        attempts = await self._repo.list_attempts(notification_id)
        await self._repo.insert_attempt(
            {
                "attempt_id": new_id("ntfat"),
                "organization_id": message.organization_id,
                "notification_id": notification_id,
                "channel_id": channel.channel_id,
                "provider": channel.provider,
                "attempt_index": len(attempts) + 1,
                "status": result.status,
                "request_summary": {
                    "message_type": message.message_type,
                    "recipient_hash": _stable_hash(message.recipient),
                },
                "response_summary": redact(result.response_summary or {}),
                "error_code": result.error_code,
                "error_summary": (
                    str(redact(result.error_summary)) if result.error_summary else None
                ),
                "latency_ms": latency_ms,
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )
        status = "sent" if result.status == "sent" else "failed"
        retry_count = message.retry_count + (0 if status == "sent" else 1)
        next_retry_at = None
        if status == "failed" and retry_count < message.max_retries:
            next_retry_at = (
                utc_now() + timedelta(seconds=min(60 * (2 ** max(retry_count - 1, 0)), 3600))
            ).isoformat()
        await self._repo.update_message(
            notification_id,
            {
                "status": status,
                "provider_message_id": result.provider_message_id,
                "retry_count": retry_count,
                "next_retry_at": next_retry_at,
                "failure_reason": result.error_summary if status == "failed" else None,
                "updated_at": utc_now_iso(),
                "sent_at": utc_now_iso() if status == "sent" else None,
            },
        )
        return await self.get_message(notification_id)

    async def retry_due(
        self,
        *,
        limit: int = 50,
        trace_id: str | None = None,
    ) -> list[NotificationMessage]:
        processed: list[NotificationMessage] = []
        rows = await self._repo.list_retryable_messages(now=utc_now_iso(), limit=limit)
        for row in rows:
            message = NotificationMessage(**row)
            try:
                if message.status == "queued":
                    processed.append(
                        await self.send_message(message.notification_id, trace_id=trace_id)
                    )
                else:
                    processed.append(
                        await self.retry_message(message.notification_id, trace_id=trace_id)
                    )
            except Exception as exc:
                channel = await self.get_channel(message.channel_id)
                processed.append(
                    await self._mark_delivery_failure(
                        message,
                        channel=channel,
                        error_code=exc.__class__.__name__,
                        error_summary=str(redact(str(exc))),
                        response_summary={"worker_retry_exception": True},
                        trace_id=trace_id,
                    )
                )
        return processed

    async def retry_message(
        self,
        notification_id: str,
        *,
        trace_id: str | None = None,
    ) -> NotificationMessage:
        message = await self.get_message(notification_id)
        if message.status == "blocked":
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "DLP 阻断的通知不能重试",
                status_code=403,
            )
        if message.retry_count >= message.max_retries:
            raise AppError(ErrorCode.TASK_STATE_INVALID, "通知重试次数已用尽", status_code=409)
        await self._repo.update_message(
            notification_id,
            {"status": "queued", "updated_at": utc_now_iso()},
        )
        return await self.send_message(notification_id, trace_id=trace_id)

    async def list_attempts(self, notification_id: str) -> list[NotificationDeliveryAttempt]:
        await self.get_message(notification_id)
        return [
            NotificationDeliveryAttempt(**row)
            for row in await self._repo.list_attempts(notification_id)
        ]

    async def _mark_delivery_failure(
        self,
        message: NotificationMessage,
        *,
        channel: NotificationChannel,
        error_code: str,
        error_summary: str,
        response_summary: dict[str, Any] | None,
        trace_id: str | None,
    ) -> NotificationMessage:
        attempts = await self._repo.list_attempts(message.notification_id)
        now = utc_now_iso()
        await self._repo.insert_attempt(
            {
                "attempt_id": new_id("ntfat"),
                "organization_id": message.organization_id,
                "notification_id": message.notification_id,
                "channel_id": channel.channel_id,
                "provider": channel.provider,
                "attempt_index": len(attempts) + 1,
                "status": "failed",
                "request_summary": {
                    "message_type": message.message_type,
                    "recipient_hash": _stable_hash(message.recipient),
                    "failure_source": "notification_retry_worker",
                },
                "response_summary": redact(response_summary or {}),
                "error_code": error_code,
                "error_summary": str(redact(error_summary)),
                "latency_ms": 0,
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        retry_count = message.retry_count + 1
        next_retry_at = None
        if retry_count < message.max_retries:
            next_retry_at = (
                utc_now() + timedelta(seconds=min(60 * (2 ** max(retry_count - 1, 0)), 3600))
            ).isoformat()
        await self._repo.update_message(
            message.notification_id,
            {
                "status": "failed",
                "retry_count": retry_count,
                "next_retry_at": next_retry_at,
                "failure_reason": str(redact(error_summary)),
                "updated_at": now,
            },
        )
        return await self.get_message(message.notification_id)

    async def receive_inbound(
        self,
        request: InboundMessageCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> InboundMessage:
        channel = await self.get_channel(request.channel_id)
        if not channel.policy.get("allow_inbound", True):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "通知渠道禁止入站消息",
                status_code=403,
            )
        content = str(redact(request.content))
        parsed_intent = _parse_inbound_intent(content)
        pending = await self._repo.pending_approvals_for_channel(channel.channel_id)
        binding_status = "unmatched"
        matched_approval_id = None
        matched_task_id = None
        action_result: dict[str, Any] = {}
        if not pending:
            binding_status = "no_pending_action"
            action_result = {"reason": "no_pending_action"}
        elif len(pending) > 1:
            binding_status = "clarification_required"
            action_result = {"reason": "multiple_pending_actions"}
        else:
            approval = pending[0]
            matched_approval_id = approval["approval_id"]
            matched_task_id = approval["task_id"]
            binding_status, action_result = await self._apply_inbound_intent(
                approval,
                parsed_intent=parsed_intent,
                content=content,
                sender_ref=request.sender_ref,
                trace_id=trace_id,
            )
        action_result = _phase48_inbound_action_result(
            action_result,
            binding_status=binding_status,
            matched_task_id=matched_task_id,
        )
        inbound_id = new_id("inmsg")
        now = utc_now_iso()
        data = {
            "inbound_message_id": inbound_id,
            "organization_id": channel.organization_id,
            "channel_id": channel.channel_id,
            "sender_ref": str(redact(request.sender_ref)),
            "provider_message_id": (
                str(redact(request.provider_message_id))
                if request.provider_message_id
                else None
            ),
            "received_at": request.received_at or now,
            "content_redacted": content,
            "parsed_intent": parsed_intent,
            "binding_status": binding_status,
            "matched_approval_id": matched_approval_id if binding_status == "matched" else None,
            "matched_task_id": matched_task_id if binding_status == "matched" else None,
            "action_result": redact(action_result),
            "risk_summary": {
                "untrusted_external_content": True,
                "pending_count": len(pending),
                "fail_closed": binding_status != "matched",
            },
            "untrusted_external_content": True,
            "trace_id": trace_id,
            "created_at": now,
        }
        await self._repo.insert_inbound(data)
        await self._repo.insert_inbound_event(
            {
                "event_id": new_id("inmevt"),
                "inbound_message_id": inbound_id,
                "organization_id": channel.organization_id,
                "event_type": "inbound.parsed",
                "payload": {
                    "parsed_intent": parsed_intent,
                    "binding_status": binding_status,
                    "matched": bool(data["matched_approval_id"]),
                },
                "payload_redacted": {
                    "parsed_intent": parsed_intent,
                    "binding_status": binding_status,
                    "matched": bool(data["matched_approval_id"]),
                },
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        await self._audit.write_event(
            actor_type="external",
            actor_id=str(redact(request.sender_ref)),
            action="notification.inbound.received",
            object_type="inbound_message",
            object_id=inbound_id,
            summary="外部入站消息已解析",
            risk_level=RiskLevel.R2,
            payload={
                "channel_id": channel.channel_id,
                "parsed_intent": parsed_intent,
                "binding_status": binding_status,
            },
            trace_id=trace_id,
        )
        return await self.get_inbound(inbound_id)

    async def get_inbound(self, inbound_message_id: str) -> InboundMessage:
        row = await self._repo.get_inbound(inbound_message_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "入站消息不存在", status_code=404)
        return InboundMessage(**row)

    async def notify_approval_required(
        self,
        approval: Any,
        *,
        trace_id: str | None = None,
    ) -> NotificationMessage | None:
        channel = await self.ensure_default_channel(trace_id=trace_id)
        body = (
            f"有一个操作需要你确认：{approval.summary}。"
            "如要允许，请明确回复“只允许这一次”并说明动作对象；也可以回复“拒绝”。"
        )
        return await self.create_message(
            NotificationMessageCreateRequest(
                channel_id=channel.channel_id,
                message_type="approval_required",
                recipient="user_local_owner",
                subject="有一个操作需要你确认",
                body=body,
                task_id=approval.task_id,
                approval_id=approval.approval_id,
                metadata={
                    "technical_detail": {
                        "approval_id": approval.approval_id,
                        "risk_level": approval.risk_level.value
                        if hasattr(approval.risk_level, "value")
                        else approval.risk_level,
                    }
                },
            ),
            trace_id=trace_id,
        )

    async def notify_scheduled_run(
        self,
        *,
        scheduled_task_id: str,
        scheduled_run_id: str,
        task_id: str | None,
        status: str,
        summary: str,
        trace_id: str | None = None,
    ) -> NotificationMessage | None:
        channel = await self.ensure_default_channel(trace_id=trace_id)
        return await self.create_message(
            NotificationMessageCreateRequest(
                channel_id=channel.channel_id,
                message_type="scheduled_summary",
                recipient="user_local_owner",
                subject="定时任务更新",
                body=f"定时任务状态：{status}。{summary}",
                task_id=task_id,
                scheduled_task_id=scheduled_task_id,
                scheduled_run_id=scheduled_run_id,
                metadata={"status": status},
                send_immediately=False,
            ),
            trace_id=trace_id,
        )

    async def notify_checkpoint_rollback(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        rollback_id: str,
        status: str,
        restored_items: int,
        conflict_items: int,
        trace_id: str | None = None,
    ) -> NotificationMessage | None:
        channel = await self.ensure_default_channel(trace_id=trace_id)
        body = (
            f"任务回滚已完成，状态：{status}。"
            f"已恢复 {restored_items} 项，冲突 {conflict_items} 项。"
        )
        return await self.create_message(
            NotificationMessageCreateRequest(
                channel_id=channel.channel_id,
                message_type="checkpoint_rollback_summary",
                recipient="user_local_owner",
                subject="任务回滚摘要",
                body=body,
                task_id=task_id,
                metadata={
                    "governance_chain": "phase48",
                    "technical_detail": {
                        "checkpoint_id": checkpoint_id,
                        "rollback_id": rollback_id,
                        "status": status,
                        "restored_items": restored_items,
                        "conflict_items": conflict_items,
                    },
                },
                send_immediately=False,
            ),
            trace_id=trace_id,
        )

    async def ensure_default_channel(self, *, trace_id: str | None = None) -> NotificationChannel:
        channels = await self.list_channels(status="active", limit=20)
        for channel in channels:
            if channel.provider == "local_mock":
                return channel
        return await self.create_channel(
            NotificationChannelCreateRequest(
                provider="local_mock",
                display_name="本地通知箱",
                channel_type="local_inbox",
                sensitivity="medium",
            ),
            trace_id=trace_id,
        )

    async def _apply_inbound_intent(
        self,
        approval: dict[str, Any],
        *,
        parsed_intent: str,
        content: str,
        sender_ref: str,
        trace_id: str | None,
    ) -> tuple[str, dict[str, Any]]:
        risk = RiskLevel(approval.get("risk_level") or RiskLevel.R1.value)
        if parsed_intent == "approval_deny":
            await self._approvals.deny(
                approval["approval_id"],
                actor_type="external_message",
                actor_id=sender_ref,
                reason="external inbound deny",
                trace_id=trace_id,
            )
            resume = await self._notify_task_engine(approval["approval_id"], trace_id=trace_id)
            return "matched", {"status": "denied", "task_resume": resume}
        if parsed_intent in {"approval_always", "approval_session"} and _risk_order(risk) >= 3:
            return "blocked", {
                "reason": "persistent_or_session_grant_blocked_for_high_risk",
                "risk_level": risk.value,
            }
        if parsed_intent not in {"approval_once", "approval_session"}:
            return "clarification_required", {"reason": "unsupported_or_ambiguous_intent"}
        if _risk_order(risk) >= 3 and not _content_matches_approval(content, approval):
            return "clarification_required", {
                "reason": "high_risk_requires_explicit_action_object",
                "risk_level": risk.value,
            }
        await self._approvals.approve(
            approval["approval_id"],
            actor_type="external_message",
            actor_id=sender_ref,
            reason="external inbound approval",
            trace_id=trace_id,
        )
        resume = await self._notify_task_engine(approval["approval_id"], trace_id=trace_id)
        return "matched", {"status": "approved", "scope": parsed_intent, "task_resume": resume}

    async def _notify_task_engine(
        self,
        approval_id: str,
        *,
        trace_id: str | None,
    ) -> dict[str, Any]:
        if self._task_engine is None:
            return {"attempted": False, "status": "task_engine_unavailable"}
        try:
            await self._task_engine.handle_approval_resolved(approval_id, trace_id=trace_id)
            return {"attempted": True, "status": "resume_notified"}
        except Exception:
            return {"attempted": True, "status": "resume_failed"}

    async def _validate_channel_asset(
        self,
        channel: NotificationChannel,
        *,
        trace_id: str | None,
    ) -> None:
        if not channel.asset_id:
            raise AppError(ErrorCode.ASSET_ACCESS_DENIED, "通知渠道缺少资产绑定", status_code=403)
        await self._asset_broker.query(
            AssetQueryRequest(
                subject_type="member",
                subject_id=channel.created_by_member_id or "mem_xiaoyao",
                asset_type=AssetCategory.ACCOUNT,
                requested_actions=["message_send"],
                keywords=[channel.display_name],
                context={"notification_channel_id": channel.channel_id},
            ),
            trace_id=trace_id,
        )

    async def _grant_channel_actions(
        self,
        asset_id: str,
        member_id: str,
        *,
        trace_id: str | None,
    ) -> None:
        for action in ("message_send", "message_receive", "approval.reply"):
            await self._capability.create_grant(
                CapabilityGrantCreateRequest(
                    subject_type="member",
                    subject_id=member_id,
                    object_type="asset",
                    object_id=asset_id,
                    action=action,
                    effect="allow",
                    risk_level=RiskLevel.R2,
                    source_type="notification_gateway",
                    source_id=asset_id,
                ),
                trace_id=trace_id,
            )


def _redact_outbound(
    *,
    subject: str | None,
    body: str,
    max_length: int,
) -> tuple[str | None, str, dict[str, Any]]:
    raw = f"{subject or ''}\n{body}"
    sensitive_matches = sum(len(pattern.findall(raw)) for pattern in SECRET_PATTERNS)
    redacted_subject = str(redact(subject))[:200] if subject else None
    redacted_body = str(redact(body))[:max_length]
    changed = int(redacted_body != body[:max_length]) + int((redacted_subject or None) != subject)
    blocked = sensitive_matches > 0
    return redacted_subject, redacted_body, {
        "redaction_count": max(changed, sensitive_matches),
        "blocked": blocked,
        "blocked_reason": "sensitive_content" if blocked else None,
        "policy": "trace_service.redact",
    }


def _parse_inbound_intent(content: str) -> str:
    text = content.strip().lower()
    if any(token in text for token in ["拒绝", "deny", "不允许", "取消这次"]):
        return "approval_deny"
    if any(token in text for token in ["始终", "always", "永久"]):
        return "approval_always"
    if any(token in text for token in ["本会话", "session"]):
        return "approval_session"
    if any(token in text for token in ["只允许这一次", "这一次", "确认", "允许", "approve"]):
        return "approval_once"
    if text in {"好的", "继续", "ok", "好"}:
        return "approval_ambiguous"
    if any(token in text for token in ["把", "改成", "edit"]):
        return "approval_edit"
    if any(token in text for token in ["取消任务", "停止任务", "cancel task"]):
        return "task_cancel"
    return "unknown"


def _phase48_inbound_action_result(
    action_result: dict[str, Any],
    *,
    binding_status: str,
    matched_task_id: str | None,
) -> dict[str, Any]:
    result = {
        "governance_chain": "phase48",
        "capability_entrypoint": "approval_bound_notification_channel",
        "pending_action_binding": (
            "unique_approval" if binding_status == "matched" else binding_status
        ),
        "task_resume_attempted": bool(
            isinstance(action_result.get("task_resume"), dict)
            and action_result["task_resume"].get("attempted")
        ),
        "matched_task_bound": bool(matched_task_id and binding_status == "matched"),
        **action_result,
    }
    task_resume = result.get("task_resume")
    if isinstance(task_resume, dict):
        result["task_resume_attempted"] = bool(task_resume.get("attempted"))
    return redact(result)


def _content_matches_approval(content: str, approval: dict[str, Any]) -> bool:
    text = content.lower()
    action = str(approval.get("requested_action") or "").lower()
    payload = approval.get("payload_redacted") or {}
    tokens = {action, action.rsplit(".", 1)[-1]}
    if "download" in action:
        tokens |= {"下载", "download"}
    if "delete" in action:
        tokens |= {"删除", "delete"}
    if "login" in action:
        tokens |= {"登录", "login"}
    for value in payload.values() if isinstance(payload, dict) else []:
        if isinstance(value, str) and value and len(value) < 80:
            tokens.add(value.lower())
    return any(token and token in text for token in tokens)


def _reject_inline_secret_config(config: dict[str, Any]) -> None:
    forbidden = {"token", "api_key", "password", "cookie", "private_key", "mnemonic", "secret"}
    allowed_refs = {"provider_state_ref", "secret_ref", "channel_account_ref"}
    if ({key.lower() for key in config} - allowed_refs) & forbidden:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "通知渠道配置不能包含明文 secret，请使用 secret_value/secret_ref",
            status_code=422,
        )


def _risk_order(risk: RiskLevel) -> int:
    try:
        return int(risk.value.removeprefix("R"))
    except ValueError:
        return 0


def _stable_hash(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
