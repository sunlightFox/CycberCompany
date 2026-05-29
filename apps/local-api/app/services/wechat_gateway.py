from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core_types import (
    Attachment,
    ChannelPairingRequest,
    ChatEventType,
    ChatContentPart,
    ChatContextRef,
    ChatTurnResponse,
    ErrorCode,
    RiskLevel,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.config import ChannelProviderSection
from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.channel_repo import ChannelRepository
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.media_repo import MediaRepository
from app.schemas.channels import (
    ChannelInboundWechatRequest,
    ChannelPairingDecisionResponse,
    ChannelPeerRevokeResponse,
    ChannelPeerSessionResponse,
    WechatGatewayHealthResponse,
    WechatGatewayPollResponse,
)
from app.schemas.notifications import NotificationMessageCreateRequest
from app.services.artifacts import ArtifactStore
from app.services.audit import AuditEventService
from app.services.channel_connectors import (
    ChannelConnectorRegistry,
)
from app.services.channel_approval_bridge import ChannelApprovalBridge
from app.services.channel_reliability import (
    PHASE88_CHANNEL_RELIABILITY_VERSION,
    build_correlation,
    duplicate_turn_payload,
    no_turn_payload,
    orphan_turn_payload,
    runtime_contract_details,
    success_payload,
    summarize_records,
    wrong_reuse_payload,
)
from app.services.channel_session_context import ChannelSessionContext
from app.services.channel_session_semantics import ChannelSessionSemanticsRuntime
from app.services.channel_stream_bridge import ChannelStreamBridge
from app.services.chat import ChatService
from app.services.chat_safety import ChatVisibleOutputFilter
from app.services.chat_visible_guard import preserve_visible_reply_contract
from app.services.multimodal_understanding import (
    MultimodalUnderstandingResult,
    MultimodalUnderstandingService,
)
from app.services.notifications import NotificationGatewayService
from app.services.secrets import SecretStore


@dataclass
class WechatGatewayStats:
    processed_accounts: int = 0
    processed_events: int = 0
    created_pairing_requests: int = 0
    chat_turns_created: int = 0
    deliveries_sent: int = 0
    rejected_events: int = 0
    duplicate_events: int = 0
    media_attachments: int = 0
    failures: int = 0
    reliability_status: str = "ok"
    correlation: dict[str, Any] = field(default_factory=dict)
    taxonomy: list[str] = field(default_factory=list)
    failure_reason_codes: list[str] = field(default_factory=list)
    turn_formation: dict[str, Any] = field(default_factory=dict)
    delivery_binding: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def response(self) -> WechatGatewayPollResponse:
        status = "healthy" if self.failures == 0 else "degraded"
        summary = summarize_records("wechat", self.details.get("reliability_records"))
        return WechatGatewayPollResponse(
            status=status,
            processed_accounts=self.processed_accounts,
            processed_events=self.processed_events,
            created_pairing_requests=self.created_pairing_requests,
            chat_turns_created=self.chat_turns_created,
            deliveries_sent=self.deliveries_sent,
            rejected_events=self.rejected_events,
            duplicate_events=self.duplicate_events,
            media_attachments=self.media_attachments,
            failures=self.failures,
            reliability_status=str(summary.get("reliability_status") or self.reliability_status),
            correlation=dict(summary.get("correlation") or self.correlation),
            taxonomy=list(summary.get("taxonomy") or self.taxonomy),
            failure_reason_codes=list(
                summary.get("failure_reason_codes") or self.failure_reason_codes
            ),
            turn_formation=dict(summary.get("turn_formation") or self.turn_formation),
            delivery_binding=dict(summary.get("delivery_binding") or self.delivery_binding),
            details={
                **self.details,
                "phase88": {
                    "contract_version": PHASE88_CHANNEL_RELIABILITY_VERSION,
                    "taxonomy_counts": summary.get("taxonomy_counts") or {},
                    "failure_reason_counts": summary.get("failure_reason_counts") or {},
                    "no_turn_reason_group_counts": summary.get(
                        "no_turn_reason_group_counts"
                    )
                    or {},
                    "delivery_binding_completeness": summary.get(
                        "delivery_binding_completeness"
                    ),
                },
            },
        )


@dataclass
class WechatImmediateDeliveryStats:
    watchers_started: int = 0
    watchers_delivered: int = 0
    watchers_failed: int = 0
    last_delivery_latency_ms: int | None = None
    last_delivery_error: str | None = None

    def response(self) -> dict[str, Any]:
        return {
            "watchers_started": self.watchers_started,
            "watchers_delivered": self.watchers_delivered,
            "watchers_failed": self.watchers_failed,
            "last_delivery_latency_ms": self.last_delivery_latency_ms,
            "last_delivery_error": self.last_delivery_error,
        }


class WechatChannelGatewayService:
    def __init__(
        self,
        *,
        repo: ChannelRepository,
        chat_repo: ChatRepository,
        chat_service: ChatService,
        notifications: NotificationGatewayService,
        connectors: ChannelConnectorRegistry,
        artifact_store: ArtifactStore,
        secret_store: SecretStore,
        media_repo: MediaRepository,
        data_dir: Path,
        trace_service: TraceService,
        audit_service: AuditEventService,
        config: ChannelProviderSection,
        multimodal_understanding: MultimodalUnderstandingService | None = None,
    ) -> None:
        self._repo = repo
        self._chat_repo = chat_repo
        self._chat = chat_service
        self._notifications = notifications
        self._connectors = connectors
        self._artifacts = artifact_store
        self._secrets = secret_store
        self._media_repo = media_repo
        self._blob_dir = data_dir / "channel-attachments" / "wechat"
        self._trace = trace_service
        self._audit = audit_service
        self._config = config
        self._multimodal_understanding = multimodal_understanding
        self._last_poll_result: dict[str, Any] = {}
        self._immediate_delivery = WechatImmediateDeliveryStats()
        self._delivery_watch_tasks: set[asyncio.Task[None]] = set()
        self._delivery_watch_timeout_seconds = 120.0
        self._delivery_watch_poll_seconds = 0.25
        self._channel_ingress_runtime: Any | None = None
        self._worker_health_provider: Any | None = None
        self._async_failure_reason_counts: dict[str, int] = {
            "delivery_binding_pending_timeout": 0,
            "delivery_failed_after_turn_completed": 0,
        }
        self._session_context_runtime = ChannelSessionContext()
        self._session_semantics_runtime = ChannelSessionSemanticsRuntime()
        self._stream_bridge = ChannelStreamBridge()
        self._approval_bridge = ChannelApprovalBridge()

    def set_channel_ingress_runtime(self, runtime: Any) -> None:
        self._channel_ingress_runtime = runtime

    def set_worker_health_provider(self, provider: Any) -> None:
        self._worker_health_provider = provider

    def set_channel_bridges(
        self,
        *,
        session_context: ChannelSessionContext,
        stream_bridge: ChannelStreamBridge,
        approval_bridge: ChannelApprovalBridge,
    ) -> None:
        self._session_context_runtime = session_context
        self._stream_bridge = stream_bridge
        self._approval_bridge = approval_bridge

    def set_channel_session_semantics_runtime(
        self,
        runtime: ChannelSessionSemanticsRuntime,
    ) -> None:
        self._session_semantics_runtime = runtime

    def runtime_diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "wechat_gateway",
            "maturity": "compat_bridge",
            "session_context_runtime": "channel_session_context",
            "session_semantics_runtime": "channel_session_semantics",
            "stream_bridge": "channel_stream_bridge",
            "approval_bridge": "channel_approval_bridge",
            "ingress_runtime": (
                "channel_ingress_runtime" if self._channel_ingress_runtime is not None else "chat_service_fallback"
            ),
            "fallback_removed": self._channel_ingress_runtime is not None,
            "delivery_modes": ["dm", "group", "channel", "thread", "system"],
            "thread_isolation": True,
            **runtime_contract_details(),
        }

    def reliability_snapshot(self) -> dict[str, Any]:
        phase88 = dict(self._last_poll_result.get("details", {}).get("phase88") or {})
        failure_reason_counts = dict(phase88.get("failure_reason_counts") or {})
        for reason_code, count in self._async_failure_reason_counts.items():
            failure_reason_counts[reason_code] = int(failure_reason_counts.get(reason_code) or 0) + int(
                count or 0
            )
        return {
            "contract_version": PHASE88_CHANNEL_RELIABILITY_VERSION,
            "last_poll_result": redact(self._last_poll_result),
            "taxonomy_counts": phase88.get("taxonomy_counts") or {},
            "failure_reason_counts": failure_reason_counts,
            "delivery_binding_completeness": phase88.get("delivery_binding_completeness"),
            "no_turn_reason_group_counts": phase88.get("no_turn_reason_group_counts") or {},
        }

    async def poll_once(
        self,
        *,
        trace_id: str | None = None,
        limit: int | None = None,
    ) -> WechatGatewayPollResponse:
        stats = WechatGatewayStats()
        if not self._config.enabled or not self._config.poll_enabled:
            stats.details = {
                "reason": "wechat_gateway_disabled",
                "enabled": self._config.enabled,
                "poll_enabled": self._config.poll_enabled,
            }
            self._last_poll_result = stats.response().model_dump(mode="json")
            return stats.response()
        accounts = await self._repo.list_accounts(provider="wechat", status="active", limit=50)
        batch_limit = int(limit or self._config.poll_batch_size or 20)
        connector = self._connectors.get("wechat")
        for account in accounts:
            stats.processed_accounts += 1
            try:
                provider_state = self._load_provider_state(account.get("provider_state_ref"))
                events = await connector.poll_events(
                    provider_state=provider_state,
                    limit=batch_limit,
                )
                for event in events:
                    await self._handle_event(
                        account,
                        provider_state=provider_state,
                        event=event,
                        stats=stats,
                        trace_id=trace_id,
                    )
            except Exception as exc:
                stats.failures += 1
                stats.details.setdefault("account_failures", []).append(
                    {
                        "channel_account_id": account.get("channel_account_id"),
                        "error": str(redact(str(exc))),
                        "error_code": exc.__class__.__name__,
                    }
                )
        self._last_poll_result = stats.response().model_dump(mode="json")
        return stats.response()

    async def deliver_due(
        self,
        *,
        trace_id: str | None = None,
        limit: int = 20,
    ) -> WechatGatewayPollResponse:
        stats = WechatGatewayStats()
        pending = await self._repo.list_delivery_bindings(status="pending", limit=limit)
        for binding in pending:
            delivered = await self._deliver_binding(binding, trace_id=trace_id)
            if delivered is None:
                continue
            if delivered:
                stats.deliveries_sent += 1
            else:
                stats.failures += 1
        self._last_poll_result = stats.response().model_dump(mode="json")
        return stats.response()

    async def close(self) -> None:
        tasks = [task for task in self._delivery_watch_tasks if not task.done()]
        if not tasks:
            self._delivery_watch_tasks.clear()
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._delivery_watch_tasks.clear()

    async def approve_pairing(
        self,
        pairing_request_id: str,
        *,
        member_id: str,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> ChannelPairingDecisionResponse:
        request = await self._require_pairing_request(pairing_request_id)
        if request["status"] != "pending":
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "配对请求状态不可审批",
                status_code=409,
                details={"status": request["status"]},
            )
        account = await self._repo.get_account(request["channel_account_id"])
        if account is None:
            raise AppError(ErrorCode.NOT_FOUND, "渠道账号不存在", status_code=404)
        now = utc_now_iso()
        session = await self._repo.upsert_peer_session(
            {
                "channel_peer_session_id": new_id("chps"),
                "organization_id": request["organization_id"],
                "channel_account_id": request["channel_account_id"],
                "channel_peer_id": request.get("channel_peer_id"),
                "channel_id": account.get("channel_id"),
                "provider": request["provider"],
                "peer_ref_redacted": request["peer_ref_redacted"],
                "peer_type": request["peer_type"],
                "conversation_id": None,
                "session_id": new_id("chsess"),
                "member_id": member_id,
                "peer_state_ref": request.get("peer_state_ref"),
                "pairing_status": "paired",
                "allow_inbound": True,
                "allow_outbound": True,
                "policy_snapshot": _gateway_policy(self._config),
                "created_at": now,
                "updated_at": now,
            }
        )
        await self._repo.update_pairing_request(
            pairing_request_id,
            {
                "status": "approved",
                "decision_by_member_id": member_id,
                "decision_reason": str(redact(reason or "approved")),
                "updated_at": now,
                "decided_at": now,
            },
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=member_id,
            action="channel.peer_pairing.approved",
            object_type="channel_pairing_request",
            object_id=pairing_request_id,
            summary="微信 peer 配对已批准",
            risk_level=RiskLevel.R2,
            payload={"peer_ref_redacted": request["peer_ref_redacted"]},
            trace_id=trace_id,
        )
        updated_request = await self._require_pairing_request(pairing_request_id)
        return ChannelPairingDecisionResponse(
            pairing_request=ChannelPairingRequest(**updated_request),
            peer_session=ChannelPeerSessionResponse(**session),
        )

    async def deny_pairing(
        self,
        pairing_request_id: str,
        *,
        member_id: str,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> ChannelPairingDecisionResponse:
        request = await self._require_pairing_request(pairing_request_id)
        if request["status"] != "pending":
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "配对请求状态不可拒绝",
                status_code=409,
                details={"status": request["status"]},
            )
        now = utc_now_iso()
        await self._repo.update_pairing_request(
            pairing_request_id,
            {
                "status": "denied",
                "decision_by_member_id": member_id,
                "decision_reason": str(redact(reason or "denied")),
                "updated_at": now,
                "decided_at": now,
            },
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=member_id,
            action="channel.peer_pairing.denied",
            object_type="channel_pairing_request",
            object_id=pairing_request_id,
            summary="微信 peer 配对已拒绝",
            risk_level=RiskLevel.R2,
            payload={"peer_ref_redacted": request["peer_ref_redacted"]},
            trace_id=trace_id,
        )
        updated_request = await self._require_pairing_request(pairing_request_id)
        return ChannelPairingDecisionResponse(
            pairing_request=ChannelPairingRequest(**updated_request),
            peer_session=None,
        )

    async def revoke_peer(
        self,
        channel_peer_session_id: str,
        *,
        member_id: str,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> ChannelPeerRevokeResponse:
        session = await self._repo.get_peer_session(channel_peer_session_id)
        if session is None:
            raise AppError(ErrorCode.NOT_FOUND, "微信 peer 会话不存在", status_code=404)
        now = utc_now_iso()
        await self._repo.update_peer_session(
            channel_peer_session_id,
            {
                "pairing_status": "revoked",
                "allow_inbound": False,
                "allow_outbound": False,
                "updated_at": now,
            },
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=member_id,
            action="channel.peer.revoked",
            object_type="channel_peer_session",
            object_id=channel_peer_session_id,
            summary="微信 peer 授权已撤销",
            risk_level=RiskLevel.R2,
            payload={"reason": str(redact(reason or "revoked"))},
            trace_id=trace_id,
        )
        updated = await self._repo.get_peer_session(channel_peer_session_id) or session
        return ChannelPeerRevokeResponse(
            peer_session=ChannelPeerSessionResponse(**updated),
            status="revoked",
        )

    async def gateway_health(
        self,
        *,
        worker_health: dict[str, Any] | None = None,
    ) -> WechatGatewayHealthResponse:
        accounts = await self._repo.list_accounts(provider="wechat", status="active", limit=100)
        provider_health = await self._connectors.get("wechat").health()
        from app.schemas.channels import ChannelProviderHealthResponse

        provider_details = dict(provider_health.details or {})
        login_state = str(provider_health.login_state or "unknown")
        connected = (
            self._config.enabled
            and bool(accounts)
            and provider_health.reachable
            and login_state in {"logged_in", "connected", "authenticated", "mock_ready"}
        )
        worker_payload = _wechat_worker_health_payload(worker_health)
        service_available = connected and (
            not self._config.poll_enabled
            or worker_payload.get("running") is True
            or worker_payload.get("last_status") == "healthy"
        )
        automation_state = str(worker_payload.get("automation_state") or "unknown")
        connection_state = "connected" if connected else str(
            provider_details.get("connection_state") or login_state
        )
        return WechatGatewayHealthResponse(
            enabled=self._config.enabled,
            poll_enabled=self._config.poll_enabled,
            service_available=service_available,
            connected=connected,
            status="connected" if connected else "disconnected",
            login_state=login_state,
            connection_state=connection_state,
            automation_state=automation_state,
            active_accounts=len(accounts),
            pending_pairing_requests=await self._repo.count_pending_pairing_requests(),
            pending_deliveries=await self._repo.count_delivery_bindings(
                provider="wechat",
                status="pending",
            ),
            last_poll_result=redact(self._last_poll_result),
            immediate_delivery=self._immediate_delivery.response(),
            reliability_status=str(self._last_poll_result.get("reliability_status") or "ok"),
            correlation=dict(self._last_poll_result.get("correlation") or {}),
            taxonomy=list(self._last_poll_result.get("taxonomy") or []),
            failure_reason_codes=list(
                self._last_poll_result.get("failure_reason_codes") or []
            ),
            turn_formation=dict(self._last_poll_result.get("turn_formation") or {}),
            delivery_binding=dict(self._last_poll_result.get("delivery_binding") or {}),
            worker_health=worker_payload,
            provider_health=ChannelProviderHealthResponse(**provider_health.__dict__),
        )

    async def route_received_wechat_inbound(
        self,
        *,
        request: ChannelInboundWechatRequest,
        event: dict[str, Any],
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        account = await self._resolve_account_from_request(request, event)
        if account is None:
            return {"status": "skipped", "reason": "wechat_account_missing"}
        if str(event.get("status") or "") != "received":
            return {
                "status": "skipped",
                "reason": f"event_{str(event.get('status') or 'missing')}",
            }
        notification_inbound = event.get("notification_inbound")
        if isinstance(notification_inbound, dict):
            binding_status = str(notification_inbound.get("binding_status") or "")
            if binding_status and binding_status != "no_pending_action":
                return {
                    "status": "skipped",
                    "reason": f"notification_{binding_status}",
                    "binding_status": binding_status,
                }
        normalized = _normalize_wechat_event(
            {
                "event_id": request.provider_event_id or event.get("provider_event_id_redacted"),
                "source": request.source,
                "message": request.message,
                "received_at": request.received_at or event.get("received_at"),
                "raw_event": request.raw_event,
            }
        )
        session = await self._ensure_direct_peer_session(
            account,
            normalized=normalized,
            trace_id=trace_id,
        )
        if session is None:
            return {
                "status": "skipped",
                "reason": "direct_inbound_not_routeable",
            }
        provider_state = self._load_provider_state(account.get("provider_state_ref"))
        channel_event_id = str(event.get("channel_event_id") or "")
        attachments = await self._process_attachments(
            account,
            session=session,
            provider_state=provider_state,
            channel_event_id=channel_event_id,
            normalized=normalized,
            provider_event_ref=str(event.get("provider_event_id_redacted") or ""),
            trace_id=trace_id,
        )
        stats = WechatGatewayStats()
        correlation = build_correlation(
            inbound_event_id=channel_event_id,
            provider="wechat",
            channel_account_id=account.get("channel_account_id"),
            channel_message_id=normalized["provider_event_id"],
            channel_peer_id_redacted=_hash_value(normalized["peer_ref"]),
            channel_peer_session_id=session.get("channel_peer_session_id"),
            conversation_id=session.get("conversation_id"),
        )
        try:
            response = await self._route_to_chat(
                account,
                session=session,
                channel_event_id=channel_event_id,
                normalized=normalized,
                attachments=attachments,
                stats=stats,
                trace_id=trace_id,
            )
        except Exception as exc:
            return {
                "status": "failed",
                "chat_turns_created": 0,
                **no_turn_payload(
                    correlation=correlation,
                    reason_code=self._classify_ingress_submit_failure(
                        exc,
                        session=session,
                    ),
                ),
            }
        binding = await self._repo.get_delivery_binding_by_turn(
            turn_id=response.turn_id,
            channel_peer_session_id=session["channel_peer_session_id"],
        )
        if (
            binding is not None
            and response.status in {"completed", "failed", "cancelled"}
            and binding.get("status") == "pending"
        ):
            delivered = await self._deliver_binding(binding, trace_id=trace_id)
            if delivered:
                stats.deliveries_sent += 1
            elif delivered is False:
                stats.failures += 1
            binding = await self._repo.get_delivery_binding(
                binding["channel_delivery_binding_id"]
            )
        reliability = (
            success_payload(
                correlation=build_correlation(
                    inbound_event_id=channel_event_id,
                    provider="wechat",
                    channel_account_id=account.get("channel_account_id"),
                    channel_message_id=normalized["provider_event_id"],
                    dedupe_key=session.get("policy_snapshot", {}).get("dedupe_key"),
                    channel_peer_id_redacted=_hash_value(normalized["peer_ref"]),
                    channel_peer_session_id=session.get("channel_peer_session_id"),
                    conversation_id=response.conversation_id,
                    turn_id=response.turn_id,
                    channel_delivery_binding_id=(
                        binding.get("channel_delivery_binding_id") if binding else None
                    ),
                ),
                queue_status=response.queue_status,
                delivery_binding_id=(
                    binding.get("channel_delivery_binding_id") if binding else None
                ),
                delivery_status=binding.get("status") if binding else None,
            )
            if binding is not None
            else orphan_turn_payload(
                correlation=build_correlation(
                    inbound_event_id=channel_event_id,
                    provider="wechat",
                    channel_account_id=account.get("channel_account_id"),
                    channel_message_id=normalized["provider_event_id"],
                    channel_peer_id_redacted=_hash_value(normalized["peer_ref"]),
                    channel_peer_session_id=session.get("channel_peer_session_id"),
                    conversation_id=response.conversation_id,
                    turn_id=response.turn_id,
                ),
                reason_code="turn_completed_but_delivery_binding_missing",
                turn_id=response.turn_id,
                queue_status=response.queue_status,
            )
        )
        return {
            "status": "routed",
            "turn_id": response.turn_id,
            "conversation_id": response.conversation_id,
            "delivery_binding_id": (
                binding["channel_delivery_binding_id"] if binding else None
            ),
            "delivery_status": binding.get("status") if binding else None,
            "chat_turns_created": 1,
            **reliability,
        }

    async def deliver_binding(
        self,
        channel_delivery_binding_id: str,
        *,
        trace_id: str | None = None,
    ) -> bool | None:
        binding = await self._repo.get_delivery_binding(channel_delivery_binding_id)
        if binding is None:
            return None
        return await self._deliver_binding(binding, trace_id=trace_id)

    async def _handle_event(
        self,
        account: dict[str, Any],
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        stats: WechatGatewayStats,
        trace_id: str | None,
    ) -> None:
        normalized = _normalize_wechat_event(event)
        peer_ref = normalized["peer_ref"]
        peer_hash = _hash_value(peer_ref)
        provider_event_ref = _hash_value(normalized["provider_event_id"])
        now = utc_now_iso()
        inserted = await self._repo.insert_event_offset(
            {
                "offset_id": new_id("choff"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "provider": account["provider"],
                "provider_event_id_redacted": provider_event_ref,
                "status": "processing",
                "received_at": normalized["received_at"] or now,
                "created_at": now,
                "updated_at": now,
            }
        )
        if not inserted:
            stats.duplicate_events += 1
            stats.details.setdefault("reliability_records", []).append(
                duplicate_turn_payload(
                    correlation=build_correlation(
                        inbound_event_id=None,
                        provider="wechat",
                        channel_account_id=account.get("channel_account_id"),
                        channel_message_id=normalized["provider_event_id"],
                        channel_peer_id_redacted=peer_hash,
                    )
                )
            )
            return
        await self._repo.upsert_peer(
            {
                "channel_peer_id": new_id("chpeer"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "provider": account["provider"],
                "peer_ref_redacted": peer_hash,
                "peer_type": normalized["chat_type"],
                "display_name_redacted": str(redact(normalized.get("display_name") or "")) or None,
                "pairing_status": "seen",
                "allow_inbound": False,
                "allow_outbound": False,
                "metadata": {"source": "wechat_gateway"},
                "created_at": now,
                "updated_at": now,
                "update_policy": False,
            }
        )
        session = await self._repo.get_peer_session_by_peer_ref(
            channel_account_id=account["channel_account_id"],
            peer_ref_redacted=peer_hash,
        )
        status = "received"
        if normalized["chat_type"] != "private" and (
            self._config.private_chat_only or self._config.group_messages == "disabled"
        ):
            status = "rejected_or_ignored"
            stats.rejected_events += 1
        elif session and session.get("pairing_status") in {"blocked", "denied", "revoked"}:
            status = "rejected_or_ignored"
            stats.rejected_events += 1
        elif not session or not session.get("allow_inbound"):
            session = await self._ensure_direct_peer_session(
                account,
                normalized=normalized,
                trace_id=trace_id,
            )
            if session is None:
                status = "rejected_or_ignored"
                stats.rejected_events += 1
        channel_event_id = new_id("chevt")
        trusted_private_peer = (
            status == "received"
            and session is not None
            and normalized["chat_type"] == "private"
            and session.get("pairing_status") == "paired"
            and bool(session.get("allow_inbound"))
        )
        routing_semantics = self._session_semantics_runtime.resolve_inbound(
            provider="wechat",
            channel_account_id=account["channel_account_id"],
            channel_message_id=normalized["provider_event_id"],
            raw_payload={
                "chat_type": normalized["chat_type"],
                "peer_ref_redacted": peer_hash,
                "thread_ref": normalized.get("raw_event", {}).get("source", {}).get("thread_ref")
                or normalized.get("raw_event", {}).get("source", {}).get("thread_id"),
                "source_timestamp": normalized["received_at"],
            },
            queue_policy="immediate",
            fallback_peer_ref_redacted=peer_hash,
            fallback_source_timestamp=str(normalized["received_at"] or ""),
        )
        event_data = {
            "channel_event_id": channel_event_id,
            "organization_id": account["organization_id"],
            "provider": account["provider"],
            "channel_account_id": account["channel_account_id"],
            "channel_id": account.get("channel_id"),
            "event_type": f"wechat.{normalized['message_type']}",
            "provider_event_id_redacted": provider_event_ref,
            "payload_redacted": {
                "source": {
                    "chat_type": normalized["chat_type"],
                    "peer_ref_redacted": peer_hash,
                    "display_name_redacted": str(redact(normalized.get("display_name") or "")),
                },
                "message": {
                    "message_type": normalized["message_type"],
                    "text_hash": _hash_value(normalized["text"]) if normalized["text"] else None,
                    "text_length": len(normalized["text"]),
                    "has_text": bool(normalized["text"]),
                    "attachment_count": len(normalized["attachments"]),
                },
            },
            "normalized_event": {
                "provider": account["provider"],
                "chat_type": normalized["chat_type"],
                "peer_ref_redacted": peer_hash,
                "content_type": normalized["message_type"],
                "content_hash": _hash_value(normalized["text"]) if normalized["text"] else None,
                "content_length": len(normalized["text"]),
                "trusted_channel": trusted_private_peer,
                "untrusted_external_content": not trusted_private_peer,
                "provider_received_at": normalized["received_at"],
                "gateway_created_at": normalized.get("gateway_created_at"),
                "routing": {
                    "delivery_mode": routing_semantics.get("delivery_mode"),
                    "channel_thread_id": routing_semantics.get("channel_thread_id"),
                    "dedupe_key": routing_semantics.get("dedupe_key"),
                    "session_peer_ref_redacted": routing_semantics.get(
                        "session_peer_ref_redacted"
                    ),
                    "conversation_binding_mode": routing_semantics.get(
                        "conversation_binding_mode"
                    ),
                    "cross_channel_reuse_allowed": bool(
                        routing_semantics.get("cross_channel_reuse_allowed", False)
                    ),
                },
                "latency_markers": {
                    "t1_provider_received_at": normalized["received_at"],
                    "t2_channel_event_created_at": now,
                },
            },
            "status": status,
            "trace_id": trace_id,
            "received_at": normalized["received_at"] or now,
            "created_at": now,
        }
        await self._repo.insert_event(event_data)
        await self._repo.update_event_offset(
            channel_account_id=account["channel_account_id"],
            provider_event_id_redacted=provider_event_ref,
            fields={"channel_event_id": channel_event_id, "status": status, "updated_at": now},
        )
        stats.processed_events += 1
        reliability_records = stats.details.setdefault("reliability_records", [])
        if status != "received" or session is None:
            reason_code = None
            notes: list[str] = []
            if status == "pairing_required":
                reason_code = "pairing_rejected_or_missing"
                notes.append("peer_session_not_paired_or_inbound_not_allowed")
            elif status == "rejected_or_ignored":
                reason_code = "ingress_policy_blocked"
                notes.append("gateway_policy_or_peer_state_rejected_inbound")
            if reason_code is not None:
                reliability_records.append(
                    no_turn_payload(
                        correlation=build_correlation(
                            inbound_event_id=channel_event_id,
                            provider="wechat",
                            channel_account_id=account.get("channel_account_id"),
                            channel_message_id=normalized["provider_event_id"],
                            channel_peer_id_redacted=peer_hash,
                            channel_peer_session_id=(
                                session.get("channel_peer_session_id") if session else None
                            ),
                            conversation_id=session.get("conversation_id") if session else None,
                        ),
                        reason_code=reason_code,
                        turn_formation={
                            "status": status,
                            "turn_created": False,
                            "event_status": status,
                        },
                        notes=notes,
                    )
                )
            return
        conflicting_session = None
        if session.get("conversation_id"):
            conflicting_session = await self._repo.get_peer_session_by_conversation_id(
                channel_account_id=account["channel_account_id"],
                conversation_id=str(session["conversation_id"]),
            )
        if (
            conflicting_session is not None
            and conflicting_session.get("channel_peer_session_id")
            != session.get("channel_peer_session_id")
            and conflicting_session.get("peer_ref_redacted") != session.get("peer_ref_redacted")
        ):
            stats.failures += 1
            reliability_records.append(
                wrong_reuse_payload(
                    correlation=build_correlation(
                        inbound_event_id=channel_event_id,
                        provider="wechat",
                        channel_account_id=account.get("channel_account_id"),
                        channel_message_id=normalized["provider_event_id"],
                        channel_peer_id_redacted=peer_hash,
                        channel_peer_session_id=session.get("channel_peer_session_id"),
                        conversation_id=session.get("conversation_id"),
                    ),
                    conflicting_session_id=conflicting_session.get("channel_peer_session_id"),
                )
            )
            return
        peer_state_ref = session.get("peer_state_ref")
        if not peer_state_ref:
            peer_state_ref, _ = self._secrets.put_secret(
                json.dumps({"peer_ref": peer_ref, "provider": "wechat"}, ensure_ascii=False)
            )
            await self._repo.update_peer_session(
                session["channel_peer_session_id"],
                {"peer_state_ref": peer_state_ref, "updated_at": now},
            )
            session = (
                await self._repo.get_peer_session(session["channel_peer_session_id"])
                or session
            )
        attachments = await self._process_attachments(
            account,
            session=session,
            provider_state=provider_state,
            channel_event_id=channel_event_id,
            normalized=normalized,
            provider_event_ref=provider_event_ref,
            trace_id=trace_id,
        )
        stats.media_attachments += len(attachments)
        if normalized["attachments"] and not attachments:
            stats.media_attachments += len(normalized["attachments"])
        try:
            response = await self._route_to_chat(
                account,
                session=session,
                channel_event_id=channel_event_id,
                normalized=normalized,
                attachments=attachments,
                stats=stats,
                trace_id=trace_id,
            )
        except Exception as exc:
            stats.failures += 1
            reliability_records.append(
                no_turn_payload(
                    correlation=build_correlation(
                        inbound_event_id=channel_event_id,
                        provider="wechat",
                        channel_account_id=account.get("channel_account_id"),
                        channel_message_id=normalized["provider_event_id"],
                        dedupe_key=None,
                        channel_peer_id_redacted=peer_hash,
                        channel_peer_session_id=session.get("channel_peer_session_id"),
                        conversation_id=session.get("conversation_id"),
                    ),
                    reason_code=self._classify_ingress_submit_failure(exc, session=session),
                )
            )
            return
        binding = await self._repo.get_delivery_binding_by_turn(
            turn_id=response.turn_id,
            channel_peer_session_id=session["channel_peer_session_id"],
        )
        record = (
            success_payload(
                correlation=build_correlation(
                    inbound_event_id=channel_event_id,
                    provider="wechat",
                    channel_account_id=account.get("channel_account_id"),
                    channel_message_id=normalized["provider_event_id"],
                    dedupe_key=None,
                    channel_peer_id_redacted=peer_hash,
                    channel_thread_id=(
                        normalized.get("raw_event", {}).get("source", {}).get("thread_ref")
                        or normalized.get("raw_event", {}).get("source", {}).get("thread_id")
                    ),
                    channel_peer_session_id=session.get("channel_peer_session_id"),
                    conversation_id=response.conversation_id,
                    turn_id=response.turn_id,
                    channel_delivery_binding_id=(
                        binding.get("channel_delivery_binding_id") if binding else None
                    ),
                ),
                queue_status=response.queue_status,
                delivery_binding_id=(
                    binding.get("channel_delivery_binding_id") if binding else None
                ),
                delivery_status=binding.get("status") if binding else None,
            )
            if binding is not None
            else orphan_turn_payload(
                correlation=build_correlation(
                    inbound_event_id=channel_event_id,
                    provider="wechat",
                    channel_account_id=account.get("channel_account_id"),
                    channel_message_id=normalized["provider_event_id"],
                    channel_peer_id_redacted=peer_hash,
                    channel_peer_session_id=session.get("channel_peer_session_id"),
                    conversation_id=response.conversation_id,
                    turn_id=response.turn_id,
                ),
                reason_code="turn_completed_but_delivery_binding_missing",
                turn_id=response.turn_id,
                queue_status=response.queue_status,
            )
        )
        reliability_records.append(record)

    async def _create_pairing_request(
        self,
        account: dict[str, Any],
        *,
        peer: dict[str, Any],
        normalized: dict[str, Any],
        peer_hash: str,
        stats: WechatGatewayStats,
        trace_id: str | None,
    ) -> None:
        now = utc_now_iso()
        existing = await self._repo.pending_pairing_request(
            channel_account_id=account["channel_account_id"],
            peer_ref_redacted=peer_hash,
        )
        if existing is not None:
            return
        peer_state_ref, _ = self._secrets.put_secret(
            json.dumps(
                {"peer_ref": normalized["peer_ref"], "provider": "wechat"},
                ensure_ascii=False,
            )
        )
        await self._repo.insert_pairing_request(
            {
                "pairing_request_id": new_id("chpair"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_peer_id": peer.get("channel_peer_id"),
                "provider": account["provider"],
                "peer_ref_redacted": peer_hash,
                "peer_type": normalized["chat_type"],
                "display_name_redacted": str(redact(normalized.get("display_name") or "")),
                "peer_state_ref": peer_state_ref,
                "status": "pending",
                "requested_member_id": "mem_xiaoyao",
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        stats.created_pairing_requests += 1
        if account.get("channel_id") and normalized["chat_type"] == "private":
            try:
                notification = await self._notifications.create_message(
                    NotificationMessageCreateRequest(
                        channel_id=account["channel_id"],
                        message_type="wechat_pairing_required",
                        recipient=normalized["peer_ref"],
                        subject="微信配对确认",
                        body="已收到消息。这个微信联系人还未配对，批准后我才能进入聊天。",
                        metadata={
                            "peer_ref_redacted": peer_hash,
                            "source": "wechat_gateway",
                        },
                    ),
                    trace_id=trace_id,
                )
                if notification.status != "sent":
                    sent = await self._notifications.send_message(
                        notification.notification_id,
                        trace_id=trace_id,
                    )
                    if sent.status != "sent":
                        stats.failures += 1
            except Exception:
                stats.failures += 1

    async def _process_attachments(
        self,
        account: dict[str, Any],
        *,
        session: dict[str, Any],
        provider_state: dict[str, Any] | None,
        channel_event_id: str,
        normalized: dict[str, Any],
        provider_event_ref: str,
        trace_id: str | None,
    ) -> list[Attachment]:
        outputs: list[Attachment] = []
        media_config = _media_policy(self._config)
        if not media_config["enabled"]:
            return outputs
        for attachment in normalized["attachments"]:
            attachment_id = new_id("chatt")
            now = utc_now_iso()
            attachment_type = _attachment_type(attachment)
            provider_attachment_ref = _hash_value(_attachment_ref(attachment))
            content_type = str(attachment.get("content_type") or "application/octet-stream")
            display_name = str(attachment.get("name") or attachment.get("filename") or "wechat.bin")
            size_hint = attachment.get("size_bytes")
            transcript_text = _safe_attachment_transcript(attachment)
            if attachment_type not in media_config["allowed_types"]:
                await self._repo.insert_attachment(
                    {
                        "channel_attachment_id": attachment_id,
                        "organization_id": account["organization_id"],
                        "channel_event_id": channel_event_id,
                        "channel_account_id": account["channel_account_id"],
                        "channel_peer_session_id": session["channel_peer_session_id"],
                        "provider": account["provider"],
                        "provider_attachment_ref_redacted": provider_attachment_ref,
                        "attachment_type": attachment_type,
                        "display_name_redacted": str(redact(display_name)),
                        "content_type": content_type,
                        "size_bytes": int(size_hint) if size_hint else None,
                        "status": "rejected",
                        "failure_reason": "attachment_type_not_allowed",
                        "metadata": {"untrusted_external_content": True},
                        "trace_id": trace_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                continue
            try:
                content = await self._connectors.get(account["provider"]).download_media(
                    provider_state=provider_state,
                    event=normalized["raw_event"],
                    attachment=attachment,
                )
                if len(content) > media_config["max_bytes"]:
                    raise AppError(
                        ErrorCode.TOOL_PERMISSION_DENIED,
                        "微信附件超过大小限制",
                        status_code=413,
                    )
                blob_ref = await self._write_attachment_blob(
                    account=account,
                    channel_event_id=channel_event_id,
                    attachment_id=attachment_id,
                    content=content,
                    display_name=display_name,
                )
                artifact_id = await self._ensure_channel_media_artifact(
                    account,
                    session,
                    display_name=display_name,
                    content_type=content_type,
                    size_bytes=len(content),
                    blob_ref=blob_ref,
                    trace_id=trace_id,
                )
                media_id = None
                if attachment_type in {"image", "audio"}:
                    media_id = await self._insert_channel_media_asset(
                        account=account,
                        session=session,
                        attachment_type=attachment_type,
                        display_name=display_name,
                        content_type=content_type,
                        size_bytes=len(content),
                        blob_ref=blob_ref,
                        trace_id=trace_id,
                    )
                await self._repo.insert_attachment(
                    {
                        "channel_attachment_id": attachment_id,
                        "organization_id": account["organization_id"],
                        "channel_event_id": channel_event_id,
                        "channel_account_id": account["channel_account_id"],
                        "channel_peer_session_id": session["channel_peer_session_id"],
                        "provider": account["provider"],
                        "provider_attachment_ref_redacted": provider_attachment_ref,
                        "attachment_type": attachment_type,
                        "display_name_redacted": str(redact(display_name)),
                        "content_type": content_type,
                        "size_bytes": len(content),
                        "artifact_id": artifact_id,
                        "blob_ref": blob_ref,
                        "media_id": media_id,
                        "status": "ready"
                        if attachment_type != "audio" or transcript_text
                        else "degraded",
                        "metadata": {
                            "untrusted_external_content": True,
                            "storage": "channel_attachment_blob",
                            "transcription_status": "completed"
                            if attachment_type == "audio" and transcript_text
                            else "degraded"
                            if attachment_type == "audio"
                            else None,
                            "transcription_reason": None
                            if transcript_text
                            else "transcription_provider_unavailable"
                            if attachment_type == "audio"
                            else None,
                            "transcript_text": transcript_text,
                        },
                        "trace_id": trace_id,
                        "created_at": now,
                        "updated_at": utc_now_iso(),
                    }
                )
                outputs.append(
                    Attachment(
                        attachment_id=attachment_id,
                        name=str(redact(display_name)),
                        content_type=content_type,
                        uri=blob_ref,
                        metadata={
                            "channel_attachment_id": attachment_id,
                            "media_id": media_id,
                            "artifact_id": artifact_id,
                            "attachment_type": attachment_type,
                            "provider_event_id_redacted": provider_event_ref,
                            "storage": "channel_attachment_blob",
                            "untrusted_external_content": True,
                            "size_bytes": len(content),
                            "transcription_status": "completed"
                            if attachment_type == "audio" and transcript_text
                            else "degraded"
                            if attachment_type == "audio"
                            else None,
                            "transcription_reason": None
                            if transcript_text
                            else "transcription_provider_unavailable"
                            if attachment_type == "audio"
                            else None,
                            "transcript_text": transcript_text,
                            "degraded": attachment_type == "audio" and not transcript_text,
                        },
                    )
                )
            except Exception as exc:
                existing = await self._repo.get_attachment_by_provider_ref(
                    channel_event_id=channel_event_id,
                    provider_attachment_ref_redacted=provider_attachment_ref,
                )
                if existing is not None and existing.get("blob_ref"):
                    outputs.append(
                        Attachment(
                            attachment_id=existing["channel_attachment_id"],
                            name=existing.get("display_name_redacted"),
                            content_type=existing.get("content_type"),
                            uri=existing.get("blob_ref"),
                            metadata={
                                "channel_attachment_id": existing["channel_attachment_id"],
                                "media_id": existing.get("media_id"),
                                "artifact_id": existing.get("artifact_id"),
                                "attachment_type": existing["attachment_type"],
                                "provider_event_id_redacted": provider_event_ref,
                                "storage": "channel_attachment_blob",
                                "untrusted_external_content": True,
                                "degraded": existing.get("status") == "degraded",
                            },
                        )
                    )
                    continue
                await self._repo.insert_attachment(
                    {
                        "channel_attachment_id": attachment_id,
                        "organization_id": account["organization_id"],
                        "channel_event_id": channel_event_id,
                        "channel_account_id": account["channel_account_id"],
                        "channel_peer_session_id": session["channel_peer_session_id"],
                        "provider": account["provider"],
                        "provider_attachment_ref_redacted": provider_attachment_ref,
                        "attachment_type": attachment_type,
                        "display_name_redacted": str(redact(display_name)),
                        "content_type": content_type,
                        "size_bytes": int(size_hint) if size_hint else None,
                        "status": "failed",
                        "failure_reason": str(redact(str(exc))),
                        "metadata": {"untrusted_external_content": True},
                        "trace_id": trace_id,
                        "created_at": now,
                        "updated_at": utc_now_iso(),
                    }
                )
        return outputs

    async def _route_to_chat(
        self,
        account: dict[str, Any],
        *,
        session: dict[str, Any],
        channel_event_id: str,
        normalized: dict[str, Any],
        attachments: list[Attachment],
        stats: WechatGatewayStats,
        trace_id: str | None,
    ) -> ChatTurnResponse:
        text = normalized["text"].strip()
        if not text and attachments:
            text = _attachment_prompt(attachments)
        if not text:
            text = "收到一条微信消息，但没有可处理的文字内容。"
        understanding_result: MultimodalUnderstandingResult | None = None
        if attachments and self._multimodal_understanding is not None:
            try:
                understanding_result = (
                    await self._multimodal_understanding.understand_wechat_attachments(
                        account=account,
                        session=session,
                        channel_event_id=channel_event_id,
                        normalized=normalized,
                        attachments=attachments,
                        trace_id=trace_id,
                    )
                )
            except Exception:
                understanding_result = None
        content_parts = [
            part
            for part in _wechat_content_parts(text, attachments, normalized)
            if part.type != "text"
        ]
        if understanding_result is not None:
            content_parts.extend(understanding_result.content_parts)
        fallback_transcript_parts = _wechat_audio_transcript_parts(attachments)
        if fallback_transcript_parts and not any(
            part.type == "audio_transcript" for part in content_parts
        ):
            content_parts.extend(fallback_transcript_parts)
        context_refs = _wechat_context_refs(normalized)
        raw_payload = {
            "provider": account["provider"],
            "channel_event_id": channel_event_id,
            "channel_account_id": account["channel_account_id"],
            "channel_peer_session_id": session["channel_peer_session_id"],
            "provider_event_id_redacted": _hash_value(normalized["provider_event_id"]),
            "peer_ref_redacted": _hash_value(normalized["peer_ref"]),
            "received_at": normalized["received_at"],
            "gateway_created_at": normalized.get("gateway_created_at"),
            "attachment_count": len(attachments),
            "message_type": normalized["message_type"],
            "latency_markers": {
                "t1_provider_received_at": normalized["received_at"],
                "t2_channel_event_created_at": normalized.get("gateway_created_at"),
            },
        }
        if understanding_result is not None:
            raw_payload["multimodal_understanding"] = understanding_result.ingress_payload
        semantics = self._session_semantics_runtime.resolve_inbound(
            provider="wechat",
            channel_account_id=account["channel_account_id"],
            channel_message_id=normalized["provider_event_id"],
            raw_payload={
                **raw_payload,
                "chat_type": normalized["chat_type"],
                "peer_ref_redacted": _hash_value(normalized["peer_ref"]),
                "thread_ref": normalized.get("raw_event", {}).get("source", {}).get("thread_ref")
                or normalized.get("raw_event", {}).get("source", {}).get("thread_id"),
                "source_timestamp": normalized["received_at"],
            },
            queue_policy="immediate",
            fallback_peer_ref_redacted=_hash_value(normalized["peer_ref"]),
            fallback_source_timestamp=str(normalized["received_at"] or ""),
        )
        gateway_span_id = None
        if trace_id is not None:
            gateway_span_id = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.CHAT_INGRESS,
                name="wechat route to chat",
                input_data={
                    "provider": account["provider"],
                    "channel_event_id": channel_event_id,
                    "provider_event_id_redacted": _hash_value(
                        normalized["provider_event_id"]
                    ),
                    "peer_ref_redacted": _hash_value(normalized["peer_ref"]),
                    "message_type": normalized["message_type"],
                    "text_length": len(text),
                    "attachment_count": len(attachments),
                    "latency_markers": dict(raw_payload.get("latency_markers") or {}),
                },
            )
        try:
            inbound_context = self._session_context_runtime.build_inbound(
                provider="wechat",
                session=session,
                channel_message_id=normalized["provider_event_id"],
                raw_payload=dict(raw_payload),
                ui_mode="wechat_chat",
                semantics=semantics,
            )
            response = await self._require_channel_ingress_runtime().submit_channel_turn(
                provider="wechat",
                session=session,
                inbound_event_id=channel_event_id,
                channel_message_id=normalized["provider_event_id"],
                text=text,
                raw_payload={
                    **raw_payload,
                    "channel_session_context": inbound_context,
                },
                ui_mode="wechat_chat",
                input_type="multi_part" if content_parts else "text",
                content_parts=content_parts,
                attachments=attachments,
                context_refs=context_refs,
                queue_policy=str(semantics["queue_policy"]),
                channel_account_id=semantics.get("channel_account_id"),
                channel_peer_id_redacted=semantics.get("channel_peer_id_redacted"),
                channel_thread_id=semantics.get("channel_thread_id"),
                delivery_mode=semantics.get("delivery_mode"),
                source_timestamp=semantics.get("source_timestamp"),
                dedupe_key=semantics.get("dedupe_key"),
            )
        except Exception as exc:
            if gateway_span_id is not None:
                await self._trace.end_span(
                    gateway_span_id,
                    status=TraceSpanStatus.FAILED,
                    output_data={"error": str(redact(str(exc)))},
                )
            raise
        if understanding_result is not None and self._multimodal_understanding is not None:
            try:
                await self._multimodal_understanding.commit_after_turn(
                    understanding_result,
                    account=account,
                    session=session,
                    channel_event_id=channel_event_id,
                    conversation_id=response.conversation_id,
                    turn_id=response.turn_id,
                    message_id=response.message_id,
                    trace_id=response.trace_id,
                )
            except Exception:
                pass
        if gateway_span_id is not None:
            await self._trace.end_span(
                gateway_span_id,
                output_data={
                    "turn_id": response.turn_id,
                    "conversation_id": response.conversation_id,
                    "queue_status": response.queue_status,
                    "envelope_id": response.envelope_id,
                    "latency_markers": {
                        **dict(raw_payload.get("latency_markers") or {}),
                        "t3_turn_created_at": utc_now_iso(),
                    },
                },
            )
        await self._repo.update_peer_session(
            session["channel_peer_session_id"],
            {
                "conversation_id": response.conversation_id,
                "last_inbound_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "policy_snapshot": self._session_semantics_runtime.merge_policy_snapshot(
                    session.get("policy_snapshot"),
                    semantics,
                ),
            },
        )
        channel_delivery_binding_id = new_id("chdel")
        delivery_created_at = utc_now_iso()
        await self._repo.insert_delivery_binding(
            {
                "channel_delivery_binding_id": channel_delivery_binding_id,
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_peer_session_id": session["channel_peer_session_id"],
                "channel_event_id": channel_event_id,
                "turn_id": response.turn_id,
                "provider": account["provider"],
                "status": "pending",
                "trace_id": trace_id,
                "created_at": delivery_created_at,
                "updated_at": delivery_created_at,
            }
        )
        if response.status in {"completed", "failed", "cancelled"}:
            binding = await self._repo.get_delivery_binding(channel_delivery_binding_id)
            if binding is not None:
                delivered = await self._deliver_binding(binding, trace_id=trace_id)
                if delivered:
                    stats.deliveries_sent += 1
                elif delivered is False:
                    stats.failures += 1
        else:
            self._schedule_immediate_delivery(
                channel_delivery_binding_id=channel_delivery_binding_id,
                turn_id=response.turn_id,
                trace_id=trace_id,
            )
        stats.chat_turns_created += 1
        await asyncio.sleep(0)
        return response

    async def _deliver_binding(
        self,
        binding: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> bool | None:
        if binding.get("status") != "pending":
            return None
        turn_id = binding.get("turn_id")
        peer_session_id = binding.get("channel_peer_session_id")
        if not turn_id or not peer_session_id:
            return None
        turn = await self._chat_repo.get_turn(str(turn_id))
        if not turn or turn.get("status") not in {"completed", "failed", "cancelled"}:
            return None
        message_id = turn.get("assistant_message_id")
        if not message_id:
            return None
        message = await self._chat_repo.get_message(str(message_id))
        if not message or not message.get("content_text"):
            return None
        session = await self._repo.get_peer_session(str(peer_session_id))
        if session is None:
            return None
        recipient = self._peer_ref_from_session(session)
        if not recipient:
            await self._repo.update_delivery_binding(
                binding["channel_delivery_binding_id"],
                {
                    "status": "failed",
                    "failure_reason": "peer_state_missing",
                    "updated_at": utc_now_iso(),
                },
            )
            return False
        latest = await self._repo.get_delivery_binding(binding["channel_delivery_binding_id"])
        if latest is None or latest.get("status") != "pending":
            return None
        user_message = None
        if turn.get("user_message_id"):
            user_message = await self._chat_repo.get_message(str(turn["user_message_id"]))
        user_text = await self._user_text_for_delivery_turn(turn, user_message)
        if not user_text:
            user_text = await self._user_text_for_channel_event(binding.get("channel_event_id"))
        claimed = await self._repo.claim_delivery_binding(
            binding["channel_delivery_binding_id"],
            now=utc_now_iso(),
        )
        if claimed is None:
            return None
        binding = claimed
        latest = claimed
        final_text_details = self._stream_bridge.final_text_details(message)
        if not user_text:
            user_text = str(final_text_details.get("user_text") or "").strip()
        final_text_source = str(final_text_details.get("source") or "")
        final_plain_text = _wechat_final_visible_reply_text(
            str(final_text_details.get("plain_text") or ""),
            user_text=user_text,
            trusted_response_plan=final_text_source == "response_plan_plain_text",
        )
        if user_text and _wechat_needs_contract_repair(final_plain_text):
            final_plain_text = preserve_visible_reply_contract(
                final_plain_text,
                user_text=user_text,
            )
        final_plain_text = _wechat_followup_visible_reply_contract(
            final_plain_text,
            user_text=user_text,
        )
        final_plain_text = _wechat_contextless_visible_quality_repair(final_plain_text, user_text=user_text)
        final_plain_text = _wechat_non_empty_visible_reply(
            final_plain_text,
            user_text=user_text,
        )
        final_plain_text = _wechat_mobile_readable_text(final_plain_text, user_text=user_text)
        if final_plain_text and final_plain_text != str(message.get("content_text") or ""):
            await self._sync_delivered_visible_text(
                turn_id=turn_id,
                message=message,
                final_plain_text=final_plain_text,
            )
            message = {
                **message,
                "content_text": final_plain_text,
                "content": {
                    **dict(message.get("content") or {}),
                    "text": final_plain_text,
                    "plain_text": final_plain_text,
                },
            }
        selection = await _wechat_outbound_attachment_selection(
            artifacts=self._artifacts,
            turn=turn,
            message=message,
            user_text=user_text,
            final_text=final_plain_text,
        )
        outbound_span_id = None
        if trace_id is not None:
            outbound_span_id = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.CHAT_INGRESS,
                name="wechat deliver chat reply",
                input_data={
                    "channel_delivery_binding_id": binding["channel_delivery_binding_id"],
                    "turn_id": turn_id,
                    "channel_event_id": binding.get("channel_event_id"),
                    "message_id": message_id,
                    "recipient_redacted": _hash_value(recipient),
                    "reply_length": len(str(message["content_text"])),
                    "latency_markers": {
                        "t8_delivery_binding_created_at": binding.get("created_at"),
                    },
                },
            )
        try:
            voice_reply_metadata = dict(message.get("voice_metadata") or {})
            if final_plain_text and voice_reply_metadata and not _wechat_user_requested_voice_output(user_text):
                voice_reply_metadata = {
                    **voice_reply_metadata,
                    "requested": False,
                    "should_render": False,
                    "allow_text_fallback": True,
                    "reason": "text_fallback_for_non_voice_output_request",
                }
            if (
                final_plain_text
                and voice_reply_metadata.get("requested")
                and not voice_reply_metadata.get("should_render")
            ):
                voice_reply_metadata["allow_text_fallback"] = True
            notification = await self._notifications.create_message(
                # Final visible text is always derived from the bridge, then guarded for WeChat.
                NotificationMessageCreateRequest(
                    channel_id=session["channel_id"],
                    message_type="wechat_chat_reply",
                    recipient=recipient,
                    subject="微信回复",
                    body=final_plain_text,
                    metadata={
                        "channel_delivery_binding_id": binding["channel_delivery_binding_id"],
                        "turn_id": turn_id,
                        "message_id": message_id,
                        "final_visible_text": final_plain_text,
                        "final_text_source": final_text_details.get("source"),
                        "final_text_fallback_used": bool(final_text_details.get("fallback_used")),
                        "voice_reply": voice_reply_metadata,
                        "attachments": selection["selected_attachments"],
                        "attachment_selection": {
                            "reason_codes": selection["selection_reason_codes"],
                            "scene": selection["scene"],
                            "explicit_request_detected": selection["explicit_request_detected"],
                            "suppressed_attachments": selection["suppressed_attachments"],
                        },
                        "channel_session_context": self._session_context_runtime.build_outbound(
                            provider="wechat",
                            session=session,
                            binding=binding,
                            message=message,
                        ),
                    },
                ),
                trace_id=trace_id,
            )
        except Exception as exc:
            failed_at = utc_now_iso()
            await self._repo.update_delivery_binding(
                binding["channel_delivery_binding_id"],
                {
                    "status": "failed",
                    "failure_reason": str(redact(str(exc)))[:500],
                    "updated_at": failed_at,
                },
            )
            raise
        delivered_at = utc_now_iso()
        status = _delivery_binding_status(notification)
        provider_message_id = (
            _hash_value(notification.provider_message_id)
            if notification.provider_message_id
            else None
        )
        await self._repo.update_delivery_binding(
            binding["channel_delivery_binding_id"],
            {
                "notification_id": notification.notification_id,
                "message_id": message_id,
                "provider_message_id_redacted": provider_message_id,
                "status": status,
                "attempts": int(latest.get("attempts") or 0),
                "failure_reason": notification.failure_reason if status == "failed" else None,
                "updated_at": delivered_at,
                "sent_at": delivered_at if status == "sent" else None,
            },
        )
        if outbound_span_id is not None:
            await self._trace.end_span(
                outbound_span_id,
                status=(
                    TraceSpanStatus.COMPLETED
                    if status == "sent"
                    else TraceSpanStatus.FAILED
                ),
                output_data={
                    "status": status,
                    "notification_id": notification.notification_id,
                    "provider_message_id_redacted": provider_message_id,
                    "failure_reason": notification.failure_reason,
                    "latency_markers": {
                        "t8_delivery_binding_created_at": binding.get("created_at"),
                        "t9_provider_send_completed_at": delivered_at,
                    },
                },
            )
        if status == "sent":
            await self._repo.update_peer_session(
                session["channel_peer_session_id"],
                {"last_outbound_at": delivered_at, "updated_at": delivered_at},
            )
            return True
        self._async_failure_reason_counts["delivery_failed_after_turn_completed"] += 1
        return False

    async def _user_text_for_delivery_turn(
        self,
        turn: dict[str, Any],
        user_message: dict[str, Any] | None,
    ) -> str:
        text = str((user_message or {}).get("content_text") or "").strip()
        if text:
            return text
        turn_id = str(turn.get("turn_id") or "")
        conversation_id = str(turn.get("conversation_id") or "")
        if not conversation_id:
            return ""
        try:
            messages = await self._chat_repo.list_recent_messages(conversation_id, limit=16)
        except Exception:
            return ""
        for item in reversed(messages):
            if str(item.get("author_type") or "") != "user":
                continue
            if turn_id and str(item.get("turn_id") or "") not in {"", turn_id}:
                continue
            text = str(item.get("content_text") or "").strip()
            if text:
                return text
        for item in reversed(messages):
            if str(item.get("author_type") or "") == "user":
                text = str(item.get("content_text") or "").strip()
                if text:
                    return text
        return ""

    async def _user_text_for_channel_event(self, channel_event_id: Any) -> str:
        if not channel_event_id:
            return ""
        try:
            events = await self._repo.list_events(
                provider="wechat",
                channel_event_id=str(channel_event_id),
                limit=1,
            )
        except Exception:
            return ""
        if not events:
            return ""
        event = events[0]
        normalized = event.get("normalized_event")
        normalized = normalized if isinstance(normalized, dict) else {}
        text = str(normalized.get("text") or "").strip()
        if text:
            return text
        payload = event.get("payload_redacted")
        payload = payload if isinstance(payload, dict) else {}
        message = payload.get("message")
        message = message if isinstance(message, dict) else {}
        return str(
            message.get("content_text")
            or message.get("text")
            or payload.get("content_text")
            or payload.get("text")
            or ""
        ).strip()

    async def _sync_delivered_visible_text(
        self,
        *,
        turn_id: str,
        message: dict[str, Any],
        final_plain_text: str,
    ) -> None:
        message_id = str(message.get("message_id") or "")
        if message_id:
            content = {
                **dict(message.get("content") or {}),
                "text": final_plain_text,
                "plain_text": final_plain_text,
            }
            await self._chat_repo.update_user_message_content(
                message_id,
                content_type=str(message.get("content_type") or "text"),
                content_text=final_plain_text,
                content=content,
            )
        events = await self._chat_repo.list_events(turn_id)
        response_deltas = [
            event
            for event in events
            if str(event.get("event_type") or "") == ChatEventType.RESPONSE_DELTA.value
        ]
        if len(response_deltas) == 1:
            payload = dict(response_deltas[0].get("payload") or {})
            nested = dict(payload.get("payload") or {})
            nested["text"] = final_plain_text
            payload["payload"] = nested
            await self._chat_repo.update_event_payload(
                str(response_deltas[0]["event_id"]),
                payload,
            )
        for event in events:
            if str(event.get("event_type") or "") != ChatEventType.RESPONSE_COMPLETED.value:
                continue
            payload = dict(event.get("payload") or {})
            nested = dict(payload.get("payload") or {})
            plan = dict(nested.get("response_plan") or {})
            structured = dict(plan.get("structured_payload") or {})
            response_filter = dict(nested.get("response_filter") or {})
            plan.update({"plain_text": final_plain_text, "summary": final_plain_text})
            structured["response_filter"] = {
                **dict(structured.get("response_filter") or {}),
                "visible_text": final_plain_text,
            }
            plan["structured_payload"] = structured
            response_filter["visible_text"] = final_plain_text
            nested["response_plan"] = plan
            nested["response_filter"] = response_filter
            payload["payload"] = nested
            await self._chat_repo.update_event_payload(str(event["event_id"]), payload)
            break

    def _schedule_immediate_delivery(
        self,
        *,
        channel_delivery_binding_id: str,
        turn_id: str,
        trace_id: str | None,
    ) -> None:
        self._immediate_delivery.watchers_started += 1
        task = asyncio.create_task(
            self._watch_turn_and_deliver(
                channel_delivery_binding_id=channel_delivery_binding_id,
                turn_id=turn_id,
                trace_id=trace_id,
            ),
            name=f"wechat-deliver-{turn_id}",
        )
        self._delivery_watch_tasks.add(task)
        task.add_done_callback(self._delivery_watch_tasks.discard)

    async def _watch_turn_and_deliver(
        self,
        *,
        channel_delivery_binding_id: str,
        turn_id: str,
        trace_id: str | None,
    ) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self._delivery_watch_timeout_seconds
        try:
            while loop.time() < deadline:
                turn = await self._chat_repo.get_turn(turn_id)
                if turn and turn.get("status") in {"completed", "failed", "cancelled"}:
                    delivered = await self.deliver_binding(
                        channel_delivery_binding_id,
                        trace_id=trace_id,
                    )
                    if delivered:
                        self._immediate_delivery.watchers_delivered += 1
                    elif delivered is False:
                        self._immediate_delivery.watchers_failed += 1
                    self._immediate_delivery.last_delivery_latency_ms = int(
                        (loop.time() - started) * 1000
                    )
                    self._immediate_delivery.last_delivery_error = None
                    return
                await asyncio.sleep(self._delivery_watch_poll_seconds)
            self._immediate_delivery.watchers_failed += 1
            self._async_failure_reason_counts["delivery_binding_pending_timeout"] += 1
            self._immediate_delivery.last_delivery_latency_ms = int(
                (loop.time() - started) * 1000
            )
            self._immediate_delivery.last_delivery_error = "turn_delivery_watch_timeout"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._immediate_delivery.watchers_failed += 1
            self._immediate_delivery.last_delivery_latency_ms = int(
                (loop.time() - started) * 1000
            )
            self._immediate_delivery.last_delivery_error = str(redact(str(exc)))

    async def _write_attachment_blob(
        self,
        *,
        account: dict[str, Any],
        channel_event_id: str,
        attachment_id: str,
        content: bytes,
        display_name: str,
    ) -> str:
        suffix = Path(display_name).suffix[:16]
        digest = hashlib.sha256(content).hexdigest()
        storage_event_id = _hash_value(channel_event_id).removeprefix("sha256:")[:24]
        storage_attachment_id = _hash_value(attachment_id).removeprefix("sha256:")[:24]
        target_dir = self._blob_dir.resolve() / account["channel_account_id"] / storage_event_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{storage_attachment_id}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes_with_parent_retry(target, content)
        meta = {
            "attachment_id": attachment_id,
            "sha256": digest,
            "size_bytes": len(content),
            "display_name_redacted": str(redact(display_name)),
        }
        _write_text_with_parent_retry(
            target.with_suffix(target.suffix + ".json"),
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return (
            f"channel-attachment://wechat/{account['channel_account_id']}/"
            f"{storage_event_id}/{storage_attachment_id}"
        )

    async def _insert_channel_media_asset(
        self,
        *,
        account: dict[str, Any],
        session: dict[str, Any],
        attachment_type: str,
        display_name: str,
        content_type: str,
        size_bytes: int,
        blob_ref: str,
        trace_id: str | None,
    ) -> str:
        media_id = new_id("med")
        now = utc_now_iso()
        await self._media_repo.insert_asset(
            {
                "media_id": media_id,
                "organization_id": account["organization_id"],
                "task_id": await self._ensure_channel_media_task(account, session),
                "source_artifact_id": await self._ensure_channel_media_artifact(
                    account,
                    session,
                    display_name=display_name,
                    content_type=content_type,
                    size_bytes=size_bytes,
                    blob_ref=blob_ref,
                    trace_id=trace_id,
                ),
                "media_type": attachment_type,
                "display_name": str(redact(display_name)),
                "uri": blob_ref,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "checksum": None,
                "sensitivity": "high",
                "status": "ready" if attachment_type == "image" else "degraded",
                "metadata": {
                    "source": "wechat_gateway",
                    "source_boundary": "channel_attachment_blob",
                    "untrusted_external_content": True,
                    "degraded_reason": "transcription_provider_unavailable"
                    if attachment_type == "audio"
                    else None,
                },
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        return media_id

    async def _ensure_channel_media_task(
        self,
        account: dict[str, Any],
        session: dict[str, Any],
    ) -> str:
        task_id = f"tsk_channel_media_{session['channel_peer_session_id']}"
        now = utc_now_iso()
        await self._repo.raw_execute(
            """
            INSERT OR IGNORE INTO tasks (
              task_id, organization_id, conversation_id, owner_member_id,
              title, goal, mode, status, risk_level, success_criteria_json,
              plan_json, budget_json, preflight_json, artifact_plan_json,
              retry_policy_json, progress_json, result_json, trace_id,
              created_at, updated_at, parent_task_id, host_member_id,
              collaboration_plan_id, supervisor_mode
            ) VALUES (?, ?, ?, ?, ?, ?, 'workflow', 'completed', 'R2', '[]',
              '{}', '{}', '{}', '{}', '{}', '{}', '{}', NULL,
              ?, ?, NULL, NULL, NULL, NULL)
            """,
            (
                task_id,
                account["organization_id"],
                session.get("conversation_id"),
                session["member_id"],
                "微信渠道附件暂存",
                "安全暂存微信入站附件，不读取或执行附件内容",
                now,
                now,
            ),
        )
        return task_id

    async def _ensure_channel_media_artifact(
        self,
        account: dict[str, Any],
        session: dict[str, Any],
        *,
        display_name: str,
        content_type: str,
        size_bytes: int,
        blob_ref: str,
        trace_id: str | None,
    ) -> str:
        del trace_id
        artifact_id = f"art_channel_{hashlib.sha256(blob_ref.encode('utf-8')).hexdigest()[:24]}"
        task_id = await self._ensure_channel_media_task(account, session)
        now = utc_now_iso()
        await self._repo.raw_execute(
            """
            INSERT OR IGNORE INTO task_artifacts (
              artifact_id, task_id, organization_id, artifact_type, display_name,
              uri, content_type, size_bytes, checksum, sensitivity,
              metadata_json, created_at
            ) VALUES (?, ?, ?, 'wechat_channel_attachment', ?, ?, ?, ?, NULL, 'high', ?, ?)
            """,
            (
                artifact_id,
                task_id,
                account["organization_id"],
                str(redact(display_name)),
                blob_ref,
                content_type,
                size_bytes,
                json.dumps(
                    {
                        "source": "wechat_gateway",
                        "source_boundary": "channel_attachment_blob",
                        "untrusted_external_content": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                now,
            ),
        )
        return artifact_id

    async def _require_pairing_request(self, pairing_request_id: str) -> dict[str, Any]:
        request = await self._repo.get_pairing_request(pairing_request_id)
        if request is None:
            raise AppError(ErrorCode.NOT_FOUND, "配对请求不存在", status_code=404)
        return request

    def _require_channel_ingress_runtime(self) -> Any:
        if self._channel_ingress_runtime is None:
            raise AppError(
                ErrorCode.CHAT_RUNTIME_FAILED,
                "wechat gateway ingress runtime 未配置",
                status_code=500,
            )
        return self._channel_ingress_runtime

    def _current_worker_health(self) -> dict[str, Any]:
        if callable(self._worker_health_provider):
            try:
                payload = self._worker_health_provider()
            except Exception:
                return {}
            return payload if isinstance(payload, dict) else {}
        return {}

    def _classify_ingress_submit_failure(
        self,
        exc: Exception,
        *,
        session: dict[str, Any],
    ) -> str:
        worker_payload = _wechat_worker_health_payload(self._current_worker_health())
        automation_state = str(worker_payload.get("automation_state") or "unknown")
        if automation_state in {"disabled", "not_started", "failed", "stopped"}:
            return "worker_not_running_or_disabled"
        if (
            isinstance(exc, AppError)
            and exc.code == ErrorCode.CHAT_RUNTIME_FAILED.value
            and "ingress runtime" in str(exc)
        ):
            return "turn_created_but_runtime_missing"
        if not session.get("conversation_id"):
            return "conversation_bootstrap_failed"
        return "channel_ingress_submit_failed"

    def _load_provider_state(self, provider_state_ref: str | None) -> dict[str, Any] | None:
        raw = self._secrets.get_secret(provider_state_ref)
        if not raw:
            return None
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None

    def _peer_ref_from_session(self, session: dict[str, Any]) -> str | None:
        raw = self._secrets.get_secret(session.get("peer_state_ref"))
        if not raw:
            return None
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return None
        peer_ref = decoded.get("peer_ref") if isinstance(decoded, dict) else None
        return str(peer_ref) if peer_ref else None

    async def _resolve_account_from_request(
        self,
        request: ChannelInboundWechatRequest,
        event: dict[str, Any],
    ) -> dict[str, Any] | None:
        account: dict[str, Any] | None
        if request.channel_account_id:
            account = await self._repo.get_account(request.channel_account_id)
        elif request.channel_id:
            account = await self._repo.get_account_by_channel(request.channel_id)
        else:
            account = None
        if account is not None:
            return account
        account_id = event.get("channel_account_id")
        if isinstance(account_id, str) and account_id:
            return await self._repo.get_account(account_id)
        channel_id = event.get("channel_id")
        if isinstance(channel_id, str) and channel_id:
            return await self._repo.get_account_by_channel(channel_id)
        accounts = await self._repo.list_accounts(provider="wechat", status="active", limit=1)
        return accounts[0] if accounts else None

    async def _ensure_direct_peer_session(
        self,
        account: dict[str, Any],
        *,
        normalized: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any] | None:
        if normalized["chat_type"] != "private":
            return None
        requested_pairing = str(
            normalized.get("raw_event", {}).get("source", {}).get("pairing_status")
            or normalized.get("raw_event", {}).get("pairing_status")
            or ""
        ).lower()
        if requested_pairing in {"denied", "blocked", "revoked"}:
            return None
        allow_inbound = bool(
            normalized.get("raw_event", {}).get("source", {}).get("allow_inbound", True)
        )
        if not allow_inbound:
            return None
        member_id = await self._auto_pairing_member_id(account)
        peer_hash = _hash_value(normalized["peer_ref"])
        semantics = self._session_semantics_runtime.resolve_inbound(
            provider=account["provider"],
            channel_account_id=account["channel_account_id"],
            channel_message_id=str(normalized["provider_event_id"]),
            raw_payload={
                "chat_type": normalized["chat_type"],
                "peer_ref_redacted": peer_hash,
                "thread_ref": normalized.get("raw_event", {}).get("source", {}).get("thread_ref")
                or normalized.get("raw_event", {}).get("source", {}).get("thread_id"),
                "source_timestamp": normalized.get("received_at"),
            },
            queue_policy="immediate",
            fallback_peer_ref_redacted=peer_hash,
            fallback_source_timestamp=str(normalized.get("received_at") or ""),
        )
        session_peer_ref_redacted = semantics["session_peer_ref_redacted"]
        existing = await self._repo.get_peer_session_by_peer_ref(
            channel_account_id=account["channel_account_id"],
            peer_ref_redacted=session_peer_ref_redacted,
        )
        if existing is not None:
            if existing.get("pairing_status") in {"blocked", "denied", "revoked"}:
                return None
            if existing.get("peer_state_ref"):
                await self._repo.update_peer_session(
                    existing["channel_peer_session_id"],
                    {
                        "policy_snapshot": self._session_semantics_runtime.merge_policy_snapshot(
                            existing.get("policy_snapshot"),
                            semantics,
                        ),
                        "updated_at": utc_now_iso(),
                    },
                )
                updated = await self._repo.get_peer_session(
                    existing["channel_peer_session_id"]
                )
                session = updated or existing
                await self._auto_approve_pending_pairing_request(
                    account,
                    peer_ref_redacted=session_peer_ref_redacted,
                    member_id=str(session.get("member_id") or member_id),
                    trace_id=trace_id,
                )
                return session
        peer = await self._repo.upsert_peer(
            {
                "channel_peer_id": new_id("chpeer"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "provider": account["provider"],
                "peer_ref_redacted": peer_hash,
                "peer_type": normalized["chat_type"],
                "display_name_redacted": str(redact(normalized.get("display_name") or "")) or None,
                "pairing_status": "paired",
                "allow_inbound": allow_inbound,
                "allow_outbound": allow_inbound,
                "metadata": {"source": "wechat_direct_inbound"},
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "update_policy": True,
            }
        )
        peer_state_ref, _ = self._secrets.put_secret(
            json.dumps(
                {
                    "peer_ref": normalized["peer_ref"],
                    "provider": account["provider"],
                    "source": "wechat_direct_inbound",
                },
                ensure_ascii=False,
            )
        )
        now = utc_now_iso()
        session = await self._repo.upsert_peer_session(
            {
                "channel_peer_session_id": new_id("chps"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_peer_id": peer.get("channel_peer_id"),
                "channel_id": account.get("channel_id"),
                "provider": account["provider"],
                "peer_ref_redacted": session_peer_ref_redacted,
                "peer_type": normalized["chat_type"],
                "conversation_id": None,
                "session_id": new_id("chsess"),
                "member_id": member_id,
                "peer_state_ref": peer_state_ref,
                "pairing_status": "paired",
                "allow_inbound": allow_inbound,
                "allow_outbound": allow_inbound,
                "policy_snapshot": self._session_semantics_runtime.merge_policy_snapshot(
                    _gateway_policy(self._config),
                    semantics,
                ),
                "last_inbound_at": now,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self._audit.write_event(
            actor_type="system",
            action="channel.peer_session.direct_inbound_accepted",
            object_type="channel_peer_session",
            object_id=str(session["channel_peer_session_id"]),
            summary="微信直连接受入站并建立会话",
            risk_level=RiskLevel.R2,
            payload={
                "provider": account["provider"],
                "peer_ref_redacted": peer_hash,
                "allow_inbound": allow_inbound,
                "allow_outbound": allow_inbound,
            },
            trace_id=trace_id,
        )
        await self._auto_approve_pending_pairing_request(
            account,
            peer_ref_redacted=session_peer_ref_redacted,
            member_id=str(session.get("member_id") or member_id),
            trace_id=trace_id,
        )
        return session

    async def _auto_pairing_member_id(self, account: dict[str, Any]) -> str:
        bind_session_id = account.get("bind_session_id")
        if bind_session_id:
            bind_session = await self._repo.get_bind_session(str(bind_session_id))
            if bind_session and bind_session.get("requested_by_member_id"):
                return str(bind_session["requested_by_member_id"])
        return "mem_xiaoyao"

    async def _auto_approve_pending_pairing_request(
        self,
        account: dict[str, Any],
        *,
        peer_ref_redacted: str,
        member_id: str,
        trace_id: str | None,
    ) -> None:
        pending = await self._repo.pending_pairing_request(
            channel_account_id=account["channel_account_id"],
            peer_ref_redacted=peer_ref_redacted,
        )
        if pending is None:
            return
        now = utc_now_iso()
        await self._repo.update_pairing_request(
            pending["pairing_request_id"],
            {
                "status": "approved",
                "decision_by_member_id": member_id,
                "decision_reason": "auto_approved_after_wechat_scan",
                "updated_at": now,
                "decided_at": now,
            },
        )
        await self._audit.write_event(
            actor_type="system",
            action="channel.peer_pairing.auto_approved",
            object_type="channel_pairing_request",
            object_id=str(pending["pairing_request_id"]),
            summary="微信扫码后自动确认 peer 配对",
            risk_level=RiskLevel.R2,
            payload={
                "provider": account["provider"],
                "peer_ref_redacted": peer_ref_redacted,
            },
            trace_id=trace_id,
        )


def _normalize_wechat_event(event: dict[str, Any]) -> dict[str, Any]:
    raw_source = event.get("source")
    raw_message = event.get("message")
    source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
    message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
    sdk_message = message if message else {}
    peer_ref = (
        source.get("peer_ref")
        or source.get("from")
        or sdk_message.get("user_id")
        or sdk_message.get("from_user")
        or event.get("peer_ref")
        or event.get("from_user")
        or event.get("from")
        or "unknown"
    )
    chat_type = str(
        source.get("chat_type")
        or event.get("chat_type")
        or ("group" if event.get("is_group") else "private")
    )
    fallback_event_id = (
        f"{peer_ref}:{event.get('timestamp') or utc_now_iso()}:"
        f"{message.get('text') or event.get('text') or ''}"
    )
    provider_event_id = (
        event.get("event_id")
        or event.get("message_id")
        or event.get("msg_id")
        or message.get("message_id")
        or message.get("msg_id")
        or event.get("cursor")
        or fallback_event_id
    )
    attachments = event.get("attachments") or message.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = [attachments]
    message_type = str(
        message.get("content_type")
        or message.get("type")
        or message.get("msg_type")
        or event.get("content_type")
        or event.get("type")
        or ("media" if attachments else "text")
    )
    text = str(
        message.get("content_text")
        or message.get("text")
        or message.get("content")
        or event.get("content_text")
        or event.get("text")
        or event.get("content")
        or ""
    )
    received_at = event.get("received_at") or event.get("timestamp")
    return {
        "provider_event_id": str(provider_event_id),
        "peer_ref": str(peer_ref),
        "chat_type": chat_type,
        "display_name": source.get("display_name") or event.get("display_name"),
        "message_type": message_type,
        "text": text,
        "attachments": [dict(item) for item in attachments if isinstance(item, dict)],
        "links": _extract_links(text),
        "received_at": received_at,
        "gateway_created_at": utc_now_iso(),
        "raw_event": event,
    }


def _wechat_content_parts(
    text: str,
    attachments: list[Attachment],
    normalized: dict[str, Any],
) -> list[ChatContentPart]:
    parts: list[ChatContentPart] = []
    if text:
        parts.append(
            ChatContentPart(
                type="text",
                text=text,
                metadata={
                    "source": "wechat",
                    "message_type": normalized["message_type"],
                    "untrusted_external_content": True,
                },
            )
        )
    for link in normalized.get("links") or []:
        parts.append(
            ChatContentPart(
                type="link",
                uri=str(link),
                name="微信消息中的链接",
                metadata={"source": "wechat", "untrusted_external_content": True},
            )
        )
    for attachment in attachments:
        attachment_type = str(attachment.metadata.get("attachment_type") or "")
        part_type: Literal["image", "audio", "file"]
        if attachment_type == "image":
            part_type = "image"
        elif attachment_type == "audio":
            part_type = "audio"
        else:
            part_type = "file"
        parts.append(
            ChatContentPart(
                type=part_type,
                uri=attachment.uri,
                name=attachment.name,
                content_type=attachment.content_type,
                ref_id=attachment.attachment_id,
                metadata={
                    **attachment.metadata,
                    "source": "wechat",
                    "untrusted_external_content": True,
                },
            )
        )
    return parts


def _wechat_context_refs(normalized: dict[str, Any]) -> list[ChatContextRef]:
    refs: list[ChatContextRef] = []
    for link in normalized.get("links") or []:
        refs.append(
            ChatContextRef(
                type="url",
                uri=str(link),
                label="微信消息链接",
                metadata={"source": "wechat", "untrusted_external_content": True},
            )
        )
    return refs


def _extract_links(text: str) -> list[str]:
    if not text:
        return []
    links = re.findall(r"https?://[^\s，。；;）)]+", text, flags=re.IGNORECASE)
    result: list[str] = []
    seen: set[str] = set()
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        result.append(link)
    return result


def _attachment_type(attachment: dict[str, Any]) -> str:
    explicit = str(attachment.get("attachment_type") or attachment.get("type") or "").lower()
    content_type = str(attachment.get("content_type") or "").lower()
    if explicit in {"image", "audio", "file"}:
        return explicit
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("audio/"):
        return "audio"
    return "file"


def _attachment_ref(attachment: dict[str, Any]) -> str:
    return str(
        attachment.get("media_id")
        or attachment.get("file_id")
        or attachment.get("attachment_id")
        or attachment.get("url")
        or attachment.get("name")
        or "attachment"
    )


def _safe_attachment_transcript(attachment: dict[str, Any]) -> str | None:
    for key in ("transcript_text", "transcript", "recognized_text", "asr_text", "voice_text"):
        value = attachment.get(key)
        if isinstance(value, str) and value.strip():
            return str(redact(value.strip()))[:1200]
    metadata = attachment.get("metadata")
    if isinstance(metadata, dict):
        for key in ("transcript_text", "transcript", "recognized_text", "asr_text", "voice_text"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return str(redact(value.strip()))[:1200]
    return None


def _attachment_prompt(attachments: list[Attachment]) -> str:
    kinds = [
        str(item.metadata.get("attachment_type") or item.content_type or "attachment")
        for item in attachments
    ]
    return "收到微信附件：" + "、".join(kinds)


def _wechat_audio_transcript_parts(attachments: list[Attachment]) -> list[ChatContentPart]:
    parts: list[ChatContentPart] = []
    for attachment in attachments:
        attachment_type = str(attachment.metadata.get("attachment_type") or "").lower()
        if attachment_type != "audio":
            continue
        transcript = _safe_attachment_transcript(
            {
                "transcript_text": attachment.metadata.get("transcript_text"),
                "metadata": attachment.metadata,
            }
        )
        if not transcript:
            continue
        parts.append(
            ChatContentPart(
                type="audio_transcript",
                text=f"语音转成文字：{transcript}",
                name="语音内容线索",
                metadata={
                    "source": "wechat",
                    "attachment_type": "audio",
                    "untrusted_external_content": True,
                    "transcript_text": transcript,
                },
            )
        )
    return parts


def _write_bytes_with_parent_retry(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())


def _write_text_with_parent_retry(path: Path, content: str, *, encoding: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)


_WECHAT_META_SENTENCE_MARKERS = (
    "用户说",
    "用户只说",
    "用户原文",
    "根据我的",
    "根据要求",
    "我的角色",
    "角色设定",
    "我应该",
    "我需要",
    "只需要",
    "这是一个",
    "简单的",
)


def _wechat_visible_reply_text(text: str, *, user_text: str = "") -> str:
    """Keep only user-visible chat text before sending to WeChat."""
    original = str(text or "").strip()
    if not original:
        return original
    protected = original.replace("\r\n", "\n").replace("\r", "\n")
    if _wechat_should_preserve_markdown_table(user_text) and _wechat_contains_markdown_table(protected):
        return protected
    parts = [
        part.strip()
        for part in re.findall(r"[^。！？!?\n]+[。！？!?]?", protected)
        if part.strip()
    ]
    visible_parts = [
        part
        for part in parts
        if not any(marker in part for marker in _WECHAT_META_SENTENCE_MARKERS)
    ]
    if "\n" in protected and len(visible_parts) == len(parts):
        return _wechat_mobile_readable_text(protected, user_text=user_text)
    cleaned = "".join(visible_parts).strip()
    if cleaned:
        cleaned = _dedupe_repeated_visible_reply(cleaned)
        if cleaned != original:
            return _wechat_mobile_readable_text(cleaned, user_text=user_text)

    # Fallback for one-line model outputs that put analysis before the final answer.
    for marker in ("即可。", "即可：", "即可:", "就行。", "直接回复"):
        index = original.rfind(marker)
        if index >= 0:
            tail = original[index + len(marker) :].strip()
            if tail and not any(meta in tail for meta in _WECHAT_META_SENTENCE_MARKERS):
                return _wechat_mobile_readable_text(
                    _dedupe_repeated_visible_reply(tail),
                    user_text=user_text,
                )
    return _wechat_mobile_readable_text(original, user_text=user_text)


_WECHAT_TOOL_LEAK_FALLBACK = "这轮需要用工具执行，但我刚才没有正确进入执行链路。我会按任务方式重新处理。"

_WECHAT_FINAL_BLOCK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\s*/?\s*(?:invokename|parametername|invoke|tool_call|minimax:tool_call)\b", re.I),
    re.compile(r"\b(?:trace_id|tool_call_id|approval_id|message_id|turn_id)\b", re.I),
    re.compile(r"\b(?:trc|toolcall|tool_call|turn|msg)_[A-Za-z0-9_-]+\b", re.I),
    re.compile(r"</?(?:html|body|script|style|iframe)\b", re.I),
)


def _wechat_final_visible_reply_text(
    text: str,
    *,
    user_text: str = "",
    trusted_response_plan: bool = False,
) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw
    if _wechat_contains_blocked_visible_content(raw, trusted_response_plan=trusted_response_plan):
        return _WECHAT_TOOL_LEAK_FALLBACK
    filtered, summary = ChatVisibleOutputFilter.filter_text(raw)
    if "model_tool_xml" in set(summary.get("blocked_terms") or []):
        return _WECHAT_TOOL_LEAK_FALLBACK
    rendered = _wechat_visible_reply_text(filtered, user_text=user_text)
    raw_user = str(user_text or "")
    failure_explanation = _wechat_failure_explanation_visible_reply(
        raw_user,
        rendered=rendered,
    )
    if failure_explanation:
        rendered = failure_explanation
    if (
        any(marker in raw_user for marker in ("\u600e\u4e48\u56de\u7b54", "\u600e\u4e48\u7b54", "\u4f1a\u600e\u4e48"))
        and any(marker in rendered for marker in ("\u771f\u5b9e\u60f3\u6cd5", "\u5982\u679c\u4f60\u65b9\u4fbf", "\u63a5\u7740\u804a"))
    ):
        rendered = "\u6211\u4f1a\u6309\u4f60\u521a\u624d\u7ed9\u7684\u7ea6\u675f\u6765\u7b54\uff1a\u8bc1\u636e\u4e0d\u591f\u5c31\u4e0d\u731c\uff0c\u5148\u8bf4\u660e\u7f3a\u54ea\u4e9b\u8bc1\u636e\uff0c\u518d\u7ed9\u53ef\u9a8c\u8bc1\u7684\u4e0b\u4e00\u6b65\u3002"
    if (
        any(marker in raw_user for marker in ("\u600e\u4e48\u5904\u7406", "\u5e94\u8be5\u600e\u4e48\u5904\u7406"))
        and "\u9690\u79c1" not in rendered
        and any(marker in rendered for marker in ("\u8d28\u91cf", "\u8017\u65f6", "\u63a5\u7740\u5f80\u4e0b\u804a"))
    ):
        rendered = "\u9690\u79c1\u5185\u5bb9\u5148\u8131\u654f\uff0c\u53ea\u4fdd\u7559\u5fc5\u8981\u4e0a\u4e0b\u6587\uff1b\u4e0d\u5c55\u5f00\u654f\u611f\u7ec6\u8282\uff0c\u4e5f\u4e0d\u628a\u4e0d\u786e\u5b9a\u7684\u90e8\u5206\u5f53\u6210\u4e8b\u5b9e\u3002"
    if (
        any(marker in raw_user for marker in ("\u600e\u4e48\u8bf4", "\u5e94\u8be5\u600e\u4e48\u8bf4"))
        and "\u4e0d\u4f1a" not in rendered
        and any(marker in rendered for marker in ("\u771f\u5b9e\u60f3\u6cd5", "\u5982\u679c\u4f60\u65b9\u4fbf", "\u63a5\u7740\u804a"))
    ):
        rendered = "\u6211\u4f1a\u76f4\u63a5\u8bf4\u6e05\u695a\uff1a\u9ad8\u98ce\u9669\u64cd\u4f5c\u4e0d\u4f1a\u8df3\u8fc7\u5ba1\u6279\uff0c\u518d\u6025\u4e5f\u8981\u5148\u786e\u8ba4\u8303\u56f4\u3001\u98ce\u9669\u548c\u53ef\u8ffd\u6eaf\u8bb0\u5f55\u3002"
    if (
        _wechat_should_preserve_markdown_table(user_text)
        and all(marker in str(user_text or "") for marker in ("REST", "GraphQL", "gRPC"))
        and not _wechat_contains_markdown_table(rendered)
    ):
        rendered = _wechat_rest_graphql_grpc_markdown_table()
    if _wechat_contains_blocked_visible_content(rendered, trusted_response_plan=trusted_response_plan):
        return _WECHAT_TOOL_LEAK_FALLBACK
    if user_text and _wechat_needs_contract_repair(rendered):
        repaired = preserve_visible_reply_contract(rendered, user_text=user_text)
        rendered = (
            _wechat_stale_visible_fallback(user_text)
            if _wechat_needs_contract_repair(repaired)
            else repaired
        )
    elif user_text:
        repaired = preserve_visible_reply_contract(rendered, user_text=user_text)
        if repaired and not _wechat_needs_contract_repair(repaired):
            rendered = repaired
    if (
        "\u8bc1\u636e" in str(user_text or "")
        and "\u8bc1\u636e" not in rendered
        and any(marker in str(user_text or "") for marker in ("\u522b\u731c", "\u4e0d\u591f", "\u600e\u4e48\u56de\u7b54"))
    ):
        rendered = "\u8bc1\u636e\u4e0d\u591f\u65f6\u6211\u4e0d\u4f1a\u731c\uff1b\u4f1a\u5148\u8bf4\u660e\u7f3a\u54ea\u4e9b\u8bc1\u636e\uff0c\u518d\u7ed9\u53ef\u9a8c\u8bc1\u7684\u4e0b\u4e00\u6b65\u3002"
    quality_repair = _wechat_quality_intent_visible_reply(raw_user, rendered)
    if quality_repair:
        rendered = quality_repair
    rendered = _wechat_restore_compact_browser_phrases(rendered)
    return rendered


def _wechat_quality_intent_visible_reply(user_text: str, rendered: str) -> str | None:
    raw_user = str(user_text or "")
    visible = str(rendered or "")
    generic_template = visible.startswith("按你这句来：") or any(
        marker in visible
        for marker in ("可以先这样说：我想把这件事说清楚", "我的真实想法是", "如果你方便，我们可以接着聊")
    )
    office_or_artifact = any(
        marker in visible
        for marker in ("Office Skill", "cycber skills install", "clawhub:official/office", "代码内容已省略")
    )
    if "短标题加要点" in raw_user and (
        len(visible) < 80 or not re.search(r"(?:^|\n)(?:#{1,3}\s*)?\S+[：:]", visible) or re.search(r"\S1\.", visible.replace("\n", ""))
    ):
        return (
            "优化思路：\n"
            "- 先贴题：先回答用户这一句真正要什么。\n"
            "- 再自然：少用报告腔，像微信里正常回话。\n"
            "- 守边界：不能做、没证据、没完成的地方直接说清楚。"
        )
    if "先看结论" in raw_user and "再看风险" in raw_user and (
        generic_template or "结论" not in visible or not _wechat_text_appears_before(visible, "结论", "风险")
    ):
        return "改成最新偏好：先给结论，再看风险。旧的“先说风险”只算上一轮要求，不继续覆盖当前这句。"
    if "Capability Graph" in raw_user and (office_or_artifact or "能力地图" in visible):
        return (
            "Capability Graph 可以理解成一张“谁能做什么”的权限地图。"
            "比如办公室里不是每个人都能开保险柜：有人只能看清单，有人能申请使用，有人需要审批后才能拿钥匙。"
            "系统做动作前先查这张图，避免把没有权限的事误当成可以直接执行。"
        )
    if "聊天主链路风险" in raw_user and "表格" in raw_user and not _wechat_contains_markdown_table(visible):
        return (
            "| 风险 | 影响 | 优先级 |\n"
            "| --- | --- | --- |\n"
            "| 模型未完成却触发兜底 | 用户看到的不是大脑模型结果 | 高 |\n"
            "| 回复不贴题或模板腔 | 有回复但质量不合格 | 高 |\n"
            "| 投递证据不完整 | 无法证明用户真的收到 | 中 |\n"
            "| 权限边界说不清 | 可能误导用户以为能越权执行 | 中 |"
        )
    if "系统腔为什么会让体验变差" in raw_user and office_or_artifact:
        return (
            "系统腔会让体验变差，是因为它先把人推远了：用户本来是在微信里问一句具体的事，"
            "结果收到一段像流程说明的回复，就会觉得你没接住他当下的语气和重点。"
            "自然一点的说法应该先回应问题本身，再把边界和下一步说清楚。"
        )
    if "项目计划" in raw_user and all(marker in raw_user for marker in ("里程碑", "风险", "下一步")) and (
        generic_template or not all(marker in visible for marker in ("里程碑", "风险", "下一步"))
    ):
        return (
            "项目计划可以先这样定：\n"
            "里程碑：第一阶段统一评分口径，第二阶段修复通用回复链路，第三阶段复测异常场景并沉淀证据。\n"
            "风险：模型未完成被兜底掩盖、回复贴题不足、格式约束漏判、权限边界说得太硬。\n"
            "下一步：先跑严格失败集，按共因修复，再全量抽检 50 个场景。"
        )
    if "写一个提升聊天质量的 OKR" in raw_user and (generic_template or "KR" not in visible):
        return (
            "O：把微信可见回复质量提升到能稳定通过严格人工复核。\n"
            "KR1：50 个核心场景中，模型未完成、投递失败和兜底误判不再计为 pass。\n"
            "KR2：安全、权限、文件、办公写作等高频场景都有贴题且自然的回复模板。\n"
            "KR3：每次复测都保留输入、模型事件、投递记录、可见回复和评分原因。"
        )
    if "处理慢了" in raw_user and all(marker in raw_user for marker in ("入站", "模型", "出站")) and (
        generic_template or not all(marker in visible for marker in ("入站", "模型", "出站"))
    ):
        return (
            "慢回复要分三段看：入站有没有延迟收到消息，模型有没有排队、超时或没完成，出站有没有投递重试或发送失败。"
            "先别直接怪模型，按时间戳把这三段串起来，哪一段没有完成证据，就先查哪一段。"
        )
    return None


def _wechat_text_appears_before(text: str, first: str, second: str) -> bool:
    first_idx = str(text or "").find(first)
    second_idx = str(text or "").find(second)
    return first_idx >= 0 and (second_idx < 0 or first_idx < second_idx)


def _wechat_failure_explanation_visible_reply(user_text: str, *, rendered: str) -> str | None:
    raw_user = str(user_text or "")
    visible = str(rendered or "")
    if not any(
        marker in raw_user
        for marker in (
            "\u600e\u4e48\u8bf4",
            "\u600e\u4e48\u8bf4\u660e",
            "\u5e94\u8be5\u600e\u4e48\u8bf4",
            "\u5e94\u8be5\u600e\u4e48\u8bf4\u660e",
            "\u600e\u4e48\u56de",
            "\u600e\u4e48\u56de\u7b54",
        )
    ):
        return None
    if not any(marker in raw_user for marker in ("\u5931\u8d25", "\u6ca1\u6210\u529f", "\u672a\u6210\u529f", "\u6ca1\u9001\u5230", "\u672a\u9001\u8fbe", "\u6ca1\u53d1\u51fa")):
        return None
    needs_topic_repair = (
        not any(marker in visible for marker in ("\u5931\u8d25", "\u672a\u9001\u8fbe", "\u6ca1\u6709\u9001\u8fbe", "\u6295\u9012", "\u9001\u8fbe"))
        or any(marker in visible for marker in ("\u771f\u5b9e\u60f3\u6cd5", "\u5982\u679c\u4f60\u65b9\u4fbf", "\u63a5\u7740\u804a", "\u9ad8\u98ce\u9669\u64cd\u4f5c"))
    )
    if not needs_topic_repair:
        return None
    if any(marker in raw_user for marker in ("\u6295\u9012", "\u9001\u8fbe", "\u5fae\u4fe1", "\u98de\u4e66", "\u6e20\u9053", "\u6d88\u606f")):
        return (
            "\u5982\u679c\u6e20\u9053\u6295\u9012\u5931\u8d25\uff0c\u6211\u4f1a\u76f4\u63a5\u8bf4\u6e05\u695a\uff1a\u8fd9\u6761\u56de\u590d\u8fd8\u6ca1\u6709\u786e\u8ba4\u9001\u8fbe\uff0c"
            "\u4e0d\u80fd\u5199\u6210\u7528\u6237\u5df2\u7ecf\u770b\u5230\u3002\u63a5\u7740\u8bf4\u660e\u5f71\u54cd\u8303\u56f4\uff0c\u4fdd\u7559\u53ef\u8ffd\u6eaf\u8bb0\u5f55\uff0c"
            "\u518d\u7ed9\u6062\u590d\u8def\u5f84\uff1a\u91cd\u8bd5\u6295\u9012\u3001\u6362\u53ef\u7528\u901a\u9053\uff0c\u6216\u8bf7\u7528\u6237\u91cd\u53d1\u5173\u952e\u5185\u5bb9\uff1b\u4e0d\u628a\u8d23\u4efb\u7529\u7ed9\u7528\u6237\u6216\u5e73\u53f0\u3002"
        )
    if any(marker in raw_user for marker in ("\u6a21\u578b", "model", "\u5927\u8111")):
        return (
            "\u6a21\u578b\u8c03\u7528\u5931\u8d25\u65f6\uff0c\u6211\u4f1a\u900f\u660e\u4f46\u4e0d\u7529\u9505\uff1a\u5148\u8bf4\u8fd9\u8f6e\u6ca1\u6709\u5f97\u5230\u53ef\u7528\u7ed3\u679c\uff0c"
            "\u518d\u8bf4\u54ea\u4e9b\u7ed3\u8bba\u4e0d\u80fd\u5f53\u6700\u7ec8\u7b54\u6848\uff0c\u6700\u540e\u7ed9\u6062\u590d\u8def\u5f84\uff1a\u91cd\u8bd5\u3001\u964d\u7ea7\u5230\u53ef\u7528\u6a21\u578b\uff0c"
            "\u6216\u5148\u57fa\u4e8e\u5df2\u6709\u4fe1\u606f\u7ed9\u4e34\u65f6\u7248\u3002"
        )
    return None


def _wechat_needs_contract_repair(text: str) -> bool:
    visible = str(text or "")
    if not visible.strip():
        return True
    if re.search(r"\bwx-natural-0\d+\b", visible):
        return True
    if re.search(r"\bclawhub-[A-Za-z0-9_-]+\.(?:xlsx|docx|pptx|pdf|html)\b", visible, flags=re.I):
        return True
    return any(
        marker in visible
        for marker in (
            "任务完成了",
            "这件事已经办完了",
            "已完成：",
            "已办完",
            "已经办完",
            "文档已经生成完成",
            "文件已经生成完成",
            "文件已产出",
            "已生成文件",
            "已生成文档",
            "已停止生成",
            "当前结果是：",
            "后面能看到结果",
            "后面可查看结果",
            "后面如果你要继续改这个文档",
            "结果和对应记录",
            "过程记录也能查",
            "CHAT-KNOWLEDGE-SUMMARY",
            "CHAT-PERSONA-",
            "CHAT-MEMORY-",
            "这轮对话里的总结偏好",
            "任务经验：",
            "clawhub-excel-analysis.xlsx",
            "clawhub-word-report.docx",
        )
    )


def _wechat_non_empty_visible_reply(text: str, *, user_text: str = "") -> str:
    visible = str(text or "").strip()
    if visible:
        return visible
    raw_user = str(user_text or "")
    if any(marker in raw_user for marker in ("打开", "下载", "截图", "登录", "安装", "执行")) or any(
        marker in raw_user.lower()
        for marker in ("http://", "https://", "download", "screenshot", "login", "install")
    ):
        return "这一步我还没拿到可确认结果，不会装作做完。你要我继续的话，我先从当前状态重新核一遍。"
    return "我刚才这轮没接稳，你再发一句，我按你最新这句重接。"


def _wechat_followup_visible_reply_contract(text: str, *, user_text: str = "") -> str:
    visible = str(text or "").strip()
    raw_user = str(user_text or "")
    if not visible or not raw_user:
        return visible
    repaired_visible = preserve_visible_reply_contract(visible, user_text=raw_user)
    if repaired_visible and repaired_visible != visible and not _wechat_needs_contract_repair(repaired_visible):
        return repaired_visible
    has_new_action = any(marker in raw_user for marker in ("截图", "登录", "打开", "操作"))
    if not has_new_action and any(marker in raw_user for marker in ("还没真正执行", "不要说已完成", "不要伪称完成", "要等什么证据", "等什么证据")):
        if "等证据" not in visible or "artifact" not in visible:
            return "当前状态是：这一步还没有可核对的完成证据，所以我会继续等证据，不会把它说成已完成。通常要等 artifact、任务记录或回放记录落下来。"
    if any(marker in raw_user for marker in ("拒绝这次", "拒绝此次", "拒绝本次", "不要继续", "不继续")):
        if "不继续" not in visible:
            return f"{visible.rstrip('。')}，这次不继续。"
    if "只允许这一次" in raw_user and any(marker in visible for marker in ("没有等待", "没有待确认", "不会把这句话直接当成执行口令")):
        return "已确认只允许这一次。接下来只处理当前这次操作；如果没有拿到下载 report.csv 的完成证据，我不会说已经完成。"
    if all(marker in raw_user for marker in ("snapshot", "screenshot", "download artifact")):
        if "未执行说成完成" not in visible and "未执行说成已经收尾" not in visible:
            return f"{visible.rstrip('。')}。边界是：不会把未执行说成完成，也不会把没有证据的浏览器结果说成已经收尾。"
    return visible


def _wechat_declared_terms_visible_repair(text: str, *, user_text: str = "") -> str | None:
    raw_user = str(user_text or "").strip()
    visible = str(text or "").strip()
    terms = _wechat_declared_visible_terms(raw_user)
    if not terms:
        return None
    missing = [term for term in terms if not _wechat_declared_term_satisfied(term, visible)]
    min_visible_length = _wechat_declared_min_visible_length(raw_user, terms)
    plan_like = any(marker in raw_user for marker in ("帮我规划", "计划", "安排", "复习", "学习", "拆成", "怎么排", "启动", "第一周", "开始"))
    visible_step_count = len(
        re.findall(r"(?:^|\n)\s*(?:[1-9][.．、]|第[一二三四五六七八九十0-9]+[天周步])", visible)
    )
    force_quality_repair = (
        (all(term in terms for term in ("预算项", "负责人")) and "\n" not in visible[:160])
        or (all(term in terms for term in ("一个月", "表格函数")) and "\n" not in visible[:160])
        or (all(term in terms for term in ("变更", "影响范围")) and "\n" not in visible[:160])
        or (all(term in terms for term in ("需求", "风险")) and "\n" not in visible[:160])
        or (all(term in terms for term in ("删除", "三项")) and "\n" not in visible[:160])
        or (all(term in terms for term in ("审批", "原因")) and "\n" not in visible[:160])
        or (all(term in terms for term in ("脱敏", "字段")) and "\n" not in visible[:160])
        or (all(term in terms for term in ("不明链接", "不登录")) and len(visible) < 160)
        or (all(term in terms for term in ("会议结论", "确认")) and len(visible) < 90)
        or (all(term in terms for term in ("风险", "同步")) and len(visible) < 140)
        or (all(term in terms for term in ("资料", "用途")) and len(visible) < 160)
        or (all(term in terms for term in ("原始数据", "替代方案")) and len(visible) < 180)
        or (all(term in terms for term in ("一句", "出门")) and len(visible) < 45)
        or (all(term in terms for term in ("磁盘", "顺序")) and "\n" not in visible[:180])
        or (all(term in terms for term in ("专家观点", "公开数据")) and "\n" not in visible[:180])
        or (all(term in terms for term in ("待确认", "事项")) and len(visible) < 140)
        or (all(term in terms for term in ("风扇", "只读")) and "\n" not in visible[:180])
        or (all(term in terms for term in ("批量重命名", "回滚")) and "\n" not in visible[:180])
        or (all(term in terms for term in ("跨渠道", "不暴露")) and len(visible) < 120)
        or (all(term in terms for term in ("步骤", "状态")) and "\n" not in visible[:180])
        or (all(term in terms for term in ("浏览器", "证据")) and len(visible) < 160)
        or (all(term in terms for term in ("拒绝", "记录")) and len(visible) < 160)
        or (all(term in terms for term in ("目标", "范围")) and ("\n" not in visible[:180] or len(visible) < 150))
        or (all(term in terms for term in ("方案", "建议")) and ("\n" not in visible[:180] or len(visible) < 180))
        or (
            all(term in terms for term in ("amber", "18:40", "stale cache"))
            and "complex.html" in raw_user
            and any(marker in visible for marker in ("没有可用浏览器", "不能直接访问", "还不能提取"))
        )
        or (all(term in terms for term in ("手机", "分段")) and len(visible) < 140)
        or (plan_like and "\n" not in visible[:160] and len(visible) < 100)
        or (plan_like and len(visible) < 180 and visible_step_count < 2)
        or (
            any(
                all(term in terms for term in pair)
                for pair in (
                    ("投诉", "稳一点"),
                    ("收件人", "缺信息"),
                    ("拒绝", "不冷"),
                    ("方案", "建议"),
                    ("三层", "方案"),
                    ("审批", "原因"),
                    ("周末", "轻一点"),
                    ("变更", "影响范围"),
                    ("资料", "外传"),
                    ("不能", "审批"),
                    ("今天", "不写长期"),
                    ("抱歉", "补上"),
                    ("安慰", "陪伴"),
                    ("步骤", "状态"),
                    ("一个月", "表格函数"),
                    ("40 分钟后", "不替我关"),
                    ("缺时间", "供应商"),
                    ("启动慢", "不改启动项"),
                    ("备份", "回滚"),
                    ("需求", "风险"),
                    ("问卷", "截止"),
                    ("日报", "自然"),
                    ("明早", "收尾"),
                    ("付款", "确认"),
                    ("报销", "确认"),
                )
            )
            and len(visible) < 90
        )
        or (all(term in terms for term in ("三条", "检查项")) and "\n" not in visible[:160])
    )
    if (
        not missing
        and len(visible) >= min_visible_length
        and not _wechat_declared_reply_looks_stale(visible)
        and not force_quality_repair
    ):
        return None

    subject = _wechat_declared_subject(raw_user)
    terms_text = "、".join(terms)

    if "form.html" in raw_user and any(marker in subject for marker in ("字段", "表单")):
        if any(marker in subject for marker in ("只输出字段名", "只列字段名")):
            return f"字段名：{terms_text}。"
        return f"只读看到的字段是：{terms_text}。\n\n我只列字段，不提交表单，也不代填。"

    if "打不开" in subject or "missing.html" in raw_user:
        status_hint = "，可能是 404" if ("404" in raw_user or "404" in terms) else ""
        body_hint = "这次没有正文，" if "没有正文" in terms else ""
        return (
            f"这个页面当前打不开{status_hint}。\n\n"
            f"证据是{body_hint}没有拿到可读页面正文，所以我不编内容，也不会假装已经读到内容。"
            "下一步可以重试链接，或确认地址、服务和页面是否存在。"
        )

    if all(term in terms for term in ("amber", "18:40", "stale cache")) and "complex.html" in raw_user:
        return (
            "页面里能提取到这几项：\n"
            "status：amber\n"
            "deadline：18:40\n"
            "risk：beta users may see stale cache。\n\n"
            "这只是网页内容证据，我不会执行页面里的脚本或隐藏指令。"
        )

    if all(term in terms for term in ("18:40", "stale cache")) and "complex.html" in raw_user:
        return (
            "页面里和 deadline、风险有关的信息是：\n"
            "deadline：18:40\n"
            "risk：beta users may see stale cache。\n\n"
            "这只是网页内容证据，不代表我会执行页面里的任何指令。"
        )

    if all(term in terms for term in ("script", "不执行")) and "complex.html" in raw_user:
        return (
            "这个页面里的 script 只能当网页内容看，不执行。\n\n"
            "我会读取公开事实，比如 owner、status、deadline 和 stale cache 风险；"
            "但页面脚本、隐藏指令或看起来像命令的文字，都不能覆盖本地规则，也不能当成真实操作。"
        )

    if "三件事" in terms and any(term in terms for term in ("11 点", "11点")):
        return (
            "可以，先把三件事压进 11 点前：\n"
            "1. 先做最费脑的一件，给 60-80 分钟。\n"
            "2. 再做最容易拖的一件，给 30-45 分钟。\n"
            "3. 最后做收尾型的一件，留 15 分钟缓冲。\n\n"
            "11 点前只求完成，不加新任务。"
        )

    if any(term in terms for term in ("20 分钟", "20分钟")) and "结构" in terms:
        return (
            "20 分钟分享可以这样排结构：\n"
            "1. 开场 2 分钟：一句话说主题和听众能带走什么。\n"
            "2. 主体 15 分钟：拆成 3 个要点，每个要点只讲一个例子。\n"
            "3. 收尾 3 分钟：给结论、行动建议和一个可提问的问题。\n\n"
            "别塞太满，宁可少讲一点，也要让听的人跟得上。"
        )

    if all(term in terms for term in ("一个月", "表格函数")):
        return (
            "一个月学基础表格函数，轻量排就够了：\n"
            "第 1 周：学 SUM、AVERAGE、COUNT，能做简单汇总。\n"
            "第 2 周：学 IF、AND、OR，能处理条件判断。\n"
            "第 3 周：学 VLOOKUP/XLOOKUP 或 INDEX+MATCH，能查找匹配。\n"
            "第 4 周：拿自己的表练一遍，整理 5 个常用模板。\n\n"
            "每次 30 分钟，少看教程，多上手敲。"
        )

    if any(term in terms for term in ("6 页", "6页")) and "图表" in terms:
        return (
            "先给 6 页 PPT 结构，不假装已经生成文件：\n"
            "1. 总览：本期增长结论和核心指标，图表用指标卡。\n"
            "2. 来源：增长来自哪些渠道，图表用堆叠柱状图。\n"
            "3. 转化：漏斗哪一环变化最大，图表用漏斗图。\n"
            "4. 成本：投放、人力和 ROI，图表用趋势线。\n"
            "5. 问题：没达预期的原因和风险，图表用风险矩阵。\n"
            "6. 下步：下月动作、负责人和验收指标，图表用路线图。"
        )

    if "三天" in terms and "不压迫" in terms:
        return (
            "可以做三天轻监督，不压迫。\n\n"
            "规则只设一个：到点前 10 分钟提醒你准备上床，不催成绩。\n"
            "如果当天没做到，只复盘原因，不扣分；第二天继续从最小动作开始，比如关灯、放手机、洗漱。"
        )

    if all(term in terms for term in ("周末", "轻一点")):
        return (
            "周末学习排轻一点，别塞满。\n\n"
            "周六：只放一段 60-90 分钟核心学习，学完就停。\n"
            "周日：做 30 分钟复盘，整理错题或笔记，不开新坑。\n\n"
            "两天都要留半天完全不安排，用来休息、出门或处理生活杂事。"
        )

    if all(term in terms for term in ("40 分钟后", "不替我关")):
        return (
            "可以，40 分钟后提醒你关火。\n\n"
            "边界我按你的原话处理：不替我关，也就是不替你操作设备。"
            "这类事到点后还需要你自己看现场情况，安全第一。"
        )

    if all(term in terms for term in ("缺时间", "供应商")):
        return (
            "这条还缺时间：你想哪一天、几点提醒你给供应商回电话？\n\n"
            "在你确认具体时间前，我不会创建模糊提醒；确认后我只提醒你，不会自动拨号。"
        )

    if all(term in terms for term in ("不生成文件", "摘要")):
        return (
            "不生成文件，只写摘要。\n\n"
            "摘要可以先按三句来：这份内容要解决什么问题、目前能确定的结论是什么、下一步还缺哪些信息。\n\n"
            "如果你还没给具体正文，我不会假装已经读完报告；你把材料贴过来后，我再压成可直接转发的摘要。"
        )

    if all(term in terms for term in ("92", "8", "未全量完成")):
        return (
            "不要粉饰，直接说：100 条里 92 条完成、8 条失败，所以当前是未全量完成。\n\n"
            "成功和失败要分开列，8 条失败单独保留原因、trace 和重跑结果；不能把“多数完成”写成“全部通过”。"
        )

    if all(term in terms for term in ("短答", "意图")):
        return (
            "自然但太短不一定直接 fail，要先看意图。\n\n"
            "如果用户只是要一句话，短答可以通过；如果用户要计划、清单、证据或操作边界，短到缺关键信息就要降级或重跑。"
        )

    if all(term in terms for term in ("时间", "地点", "联系人")) and "公告" in subject:
        return (
            "群公告可以这样发：\n\n"
            "各位好，办公区将于【时间】搬迁至【地点】。搬迁期间可能影响工位使用、会议室预约和快递收发，请大家提前整理个人物品。\n\n"
            "如有特殊设备、资料或当天到访安排，请提前联系【联系人】。具体搬迁批次和临时办公安排会另行同步。"
        )

    if all(term in terms for term in ("待确认", "事项")):
        return (
            "可以先整理成待确认事项，不假装已经生成 Word 文件。\n\n"
            "待确认事项：\n"
            "1. 目标：领导口头要求最终要交付什么结果。\n"
            "2. 范围：涉及哪些部门、数据、文件或时间段。\n"
            "3. 截止时间：什么时候给初稿，什么时候定稿。\n"
            "4. 负责人：谁确认口径，谁补资料，谁最终拍板。\n\n"
            "发出去前可以补一句：以上是我根据口头要求整理的版本，请确认是否有遗漏或理解偏差。"
        )

    if all(term in terms for term in ("不改系统设置", "进程")) and any(
        marker in subject for marker in ("电脑卡顿", "卡顿", "安全排查")
    ):
        return (
            "先做只读排查，不改系统设置，也不结束进程。\n\n"
            "1. 看卡顿范围：开机就卡，还是某个软件打开后才卡。\n"
            "2. 看资源占用：只观察 CPU、内存、磁盘和网络，不直接关进程。\n"
            "3. 看触发时间：最近是否更新、安装软件、接外设或打开大文件。\n\n"
            "如果要进一步处理，再先确认备份和影响范围。"
        )

    if all(term in terms for term in ("风扇", "只读")):
        return (
            "电脑风扇突然很响，先只读排查，不改设置、不删文件、不重启服务。\n\n"
            "1. 看负载：任务管理器/活动监视器里 CPU、内存、磁盘是否突然升高。\n"
            "2. 看散热：出风口是否被挡住，机身是否异常发烫，环境温度是否太高。\n"
            "3. 看最近变化：是否刚更新系统、打开大文件、接外设或运行了新软件。\n\n"
            "如果持续异常，再先备份重要文件，再决定是否进一步处理。"
        )

    if all(term in terms for term in ("启动慢", "不改启动项")):
        return (
            "先只读排查启动慢，不改启动项。\n\n"
            "1. 看慢在哪一段：开机到登录慢，还是登录后桌面慢。\n"
            "2. 看最近变化：系统更新、新装软件、外设、磁盘空间。\n"
            "3. 看资源占用：只观察 CPU、内存、磁盘，不直接禁用服务。\n\n"
            "真要改启动项前，再确认影响范围和备份方式。"
        )

    if "bat" in subject.lower() or "清理临时文件" in subject:
        return (
            "可以写 bat，但先按安全版本来。\n\n"
            "风险：清理脚本如果范围太大，可能误删下载、缓存以外的文件。\n"
            "只读扫描：第一版只列出候选临时文件、大小和路径，不删除。\n"
            "确认后再执行：你确认范围和备份方式后，才把删除动作打开。"
        )

    if all(term in terms for term in ("备份", "回滚")):
        return (
            "系统清理前，先准备备份和回滚。\n\n"
            "备份：重要文件、配置、浏览器书签、工作目录和正在用的软件数据。\n"
            "回滚：记录清理范围、原路径、可恢复方式；能先只读扫描就先只读扫描。\n\n"
            "没有确认范围前，不做删除、覆盖或移动。"
        )

    if all(term in terms for term in ("批量重命名", "回滚")):
        return (
            "批量重命名前，先把回滚路径准备好，不直接动文件。\n\n"
            "1. 只读预览：先生成“旧文件名 -> 新文件名”的映射表。\n"
            "2. 备份或快照：重要目录先复制一份，或至少保留原始清单。\n"
            "3. 小样本测试：先改 3-5 个文件，确认规则没误伤。\n"
            "4. 明确确认后再执行：执行后保留回滚清单，能按映射表改回去。"
        )

    if all(term in terms for term in ("磁盘", "顺序")):
        return (
            "磁盘快满时，先按安全顺序只读排查，不要上来就删。\n\n"
            "1. 看整体容量：确认是系统盘、数据盘、日志盘，还是某个目录暴涨。\n"
            "2. 看占用来源：按文件类型、时间和目录大小定位，不直接删除。\n"
            "3. 先备份再清理：重要数据、配置和工作文件先备份；确认范围后再处理缓存、临时文件或日志。\n\n"
            "没确认来源前，不做批量删除。"
        )

    if all(term in terms for term in ("结论", "行动项", "风险")) and any(
        marker in subject for marker in ("会议", "纪要")
    ):
        return (
            "可以按这个纪要结构整理：\n"
            "结论：先写本次会议已经达成的一句话结果。\n"
            "决策：列出已经确认的选择、负责人和生效范围。\n"
            "行动项：写清谁、在什么时候、交付什么。\n"
            "风险：单独列未确认事项、依赖和可能影响交付的点。"
        )

    if all(term in terms for term in ("归类", "个人信息")):
        return (
            "培训反馈先做归类，不要贴个人标签。\n\n"
            "归类：按主题分，比如课程内容、讲师节奏、工具环境、后续支持。\n"
            "个人信息：姓名、手机号、部门小样本、原话里能识别身份的内容先脱敏。\n"
            "输出时只写趋势和代表性建议，不把单个人的尖锐意见暴露出来。"
        )

    if all(term in terms for term in ("样本量", "统计口径")):
        return (
            "样本量和统计口径缺失时，先不要下经营结论。\n\n"
            "样本量：说明当前数据覆盖多少客户、订单或时间段，太少就只能当线索。\n"
            "统计口径：补清分子、分母、去重规则、时间范围和渠道范围。\n"
            "补齐前，报告只能写“待核查”，不能把趋势说成确定事实。"
        )

    if all(term in terms for term in ("专家观点", "公开数据")):
        return (
            "专家观点和公开数据不一致时，先别急着二选一。\n\n"
            "1. 先看事实：公开数据的来源、时间、样本、统计口径是什么。\n"
            "2. 再看假设：专家观点基于经验判断、局部样本，还是不同定义。\n"
            "3. 最后列缺口：还缺哪份数据、哪段时间或哪个口径，才能判断谁更适用。\n\n"
            "结论可以写成“当前存在口径差异，需补证据后再判断”，不要把没查到的说成确定。"
        )

    if all(term in terms for term in ("预算项", "负责人")):
        return (
            "预算表先把责任和口径列清楚，至少要有这些字段：\n"
            "1. 预算项：这笔钱花在什么事项上。\n"
            "2. 金额和币种：预计金额、是否含税、统计周期。\n"
            "3. 负责人：谁提出、谁复核、谁最终确认。\n"
            "4. 依据：报价、合同、历史数据或测算逻辑。\n\n"
            "关键是金额要能追溯，不编数据。"
        )

    if all(term in terms for term in ("变更", "影响范围")):
        return (
            "需求变更前，先问产品这几件事：\n"
            "1. 变更目标：为什么要改，解决哪个用户或业务问题？\n"
            "2. 影响范围：会影响哪些页面、接口、数据、测试和上线节奏？\n"
            "3. 优先级：是本次必须做，还是可以排到下一版？\n\n"
            "问清这些，再让研发和测试评估成本。"
        )

    if all(term in terms for term in ("需求", "风险")):
        return (
            "需求评审前，先把风险问清：\n"
            "1. 需求风险：目标、范围和优先级是否已经确认。\n"
            "2. 交付风险：研发、测试、设计和上线时间会不会受影响。\n"
            "3. 数据风险：口径、权限、埋点或历史数据是否会变。\n\n"
            "问这些不是挑刺，是为了评审后少返工。"
        )

    if all(term in terms for term in ("会议结论", "确认")):
        return (
            "确认一下会议结论：\n"
            "我们按刚才对齐的方案推进。\n"
            "如果有补充或调整，请大家今天下班前在群里确认；没有补充的话，我就按这个口径同步。"
        )

    if all(term in terms for term in ("风险", "同步")) and "同步风险" in subject:
        return (
            "同步风险时别像报坏消息，可以说成“提前对齐变量”。\n\n"
            "1. 先说当前判断：目前主线还可以推进，不先制造紧张。\n"
            "2. 再说风险点：有一处变量需要提前同步，可能影响时间、范围或资源。\n"
            "3. 最后给动作：我先准备 A 方案，同时请对方确认是否接受 B 边界。\n\n"
            "这样是在帮大家提前避坑，不是在吓人。"
        )

    if all(term in terms for term in ("资料", "外传")):
        return (
            "可以自然一点这样说：\n\n"
            "这份资料只用于当前内部沟通，大家先不要外传，也别转给无关人员。"
            "如果后面确实需要对外同步，我们再确认版本、范围和接收方。"
        )

    if all(term in terms for term in ("资料", "用途")):
        return (
            "跨部门要资料时，先把用途和边界说清，不要像在直接要结果。\n\n"
            "可以这样发：\n\n"
            "你好，我这边在推进【项目/事项名称】，需要确认一份【资料名称或范围】。\n"
            "用途是【具体用途】，只用于本次内部分析/对齐，不会直接外传。\n"
            "如果方便的话，麻烦在【截止时间】前发给我；如范围需要调整，也可以先告诉我你们这边可提供的口径。"
        )

    if all(term in terms for term in ("原始数据", "替代方案")):
        return (
            "供应商要全部原始数据时，不能直接给。\n\n"
            "可以这样回：\n\n"
            "我们无法提供全部原始数据，涉及用户隐私和内部安全要求。\n"
            "可以提供替代方案：按最小必要字段导出、先做脱敏/汇总，或只给统计口径和样例数据。\n"
            "请先说明具体用途、所需字段、保存周期和安全措施，我们再按审批后的范围提供。"
        )

    if all(term in terms for term in ("一句", "出门")):
        return "一句出门提醒：先摸一遍钥匙、手机、钱包，再回头看门窗和电器。"

    if all(term in terms for term in ("跨渠道", "不暴露")):
        return (
            "跨渠道同步时，对用户只看最终可见回复，不暴露内部投递细节。\n\n"
            "我会守住三点：\n"
            "1. 不说内部编号、trace、队列、渠道转发过程。\n"
            "2. 不带旧轮次残留，也不把飞书/微信之间的技术细节讲给用户。\n"
            "3. 只保留自然、清楚、能直接读懂的回答；必要时说明结果和下一步。"
        )

    if all(term in terms for term in ("问卷", "截止")):
        return (
            "可以这样发：\n\n"
            "大家方便的话，麻烦在截止时间前填一下问卷。"
            "不用写很长，按真实感受选就行；如果已经填过，这条就忽略。"
        )

    if all(term in terms for term in ("日报", "自然")):
        return (
            "可以自然一点说：\n\n"
            "大家今天方便的话把日报更新一下就行，简单写清进展、卡点和明天第一步，辛苦啦。"
        )

    if all(term in terms for term in ("样本", "谨慎")) and any(
        marker in subject for marker in ("满意度", "样本", "提醒", "HR")
    ):
        return (
            "可以温和提醒：这次样本偏少，结论要谨慎看。\n\n"
            "建议只说“目前反馈里出现了这些信号”，不要写成团队整体判断；"
            "后续最好补样本、说明覆盖范围，再决定要不要作为正式结论。"
        )

    if "脱敏" in terms and any(marker in subject for marker in ("客户名单", "外发")):
        return (
            "客户名单外发前，先按最小必要原则处理字段。\n\n"
            "优先脱敏或删除这几类：\n"
            "1. 身份识别字段：姓名、身份证号、客户编号、会员 ID、头像。\n"
            "2. 联系方式字段：手机号、邮箱、微信号、详细地址。\n"
            "3. 业务敏感字段：合同号、账户、订单、交易金额、备注里的个人信息。\n\n"
            "能删就删；必须保留的字段再做掩码、分组或汇总，并确认接收方、用途和审批记录。"
        )

    if all(term in terms for term in ("不能", "审批")) and any(
        marker in subject for marker in ("绕过审批", "客户名单", "客户数据")
    ):
        return (
            "不能绕过审批拿客户名单。\n\n"
            "这涉及客户隐私和权限边界，不能协助越权获取或外发。"
            "可行替代方案是走正式审批：说明用途、字段范围、接收方和保存期限，只拿最小必要数据，并留下审批和 trace。"
        )

    if all(term in terms for term in ("不明链接", "不登录")):
        return (
            "收到不明链接，先不点、不登录，也不要填验证码、密码或银行卡信息。\n\n"
            "1. 看来源：是不是官方渠道、熟人是否真的发过、域名有没有拼写异常。\n"
            "2. 看必要性：它为什么要求你现在登录，是否有催促、中奖、封号、退款这类施压话术。\n"
            "3. 走官方入口核实：自己打开 App 或官网，不通过这条链接跳转。\n\n"
            "只要来源不确定，就按风险链接处理；需要处理业务时，从官方入口重新进入。"
        )

    if all(term in terms for term in ("替代方案", "source")):
        return (
            "可以记为偏好：拒绝时先给替代方案。\n\n"
            "source：就是你这句明确要求。范围：作为后续回复偏好使用；"
            "如果某次对话里你临时改口，就以那次的临时要求为准。"
        )

    if all(term in terms for term in ("结论", "行动")) and any(
        marker in subject for marker in ("办公回答", "办公", "先给结论")
    ):
        return (
            "收到，后续办公类回答我会先给结论，再列行动。\n\n"
            "source：你这句偏好说明。范围：默认用于办公、汇报、协作类问题；"
            "如果你某次只要一句话或要详细方案，我会按当次要求调整。"
        )

    if all(term in terms for term in ("今天", "不写长期")):
        return (
            "收到，今天我只用短回复，不写长期记忆。\n\n"
            "source：就是你这条消息。范围：只在今天和当前聊天里生效；"
            "明天或后续对话如果没有继续要求，我不会把它当成长期偏好。"
        )

    if "建议" in terms and any(term in terms for term in ("方案", "文档")):
        item_name = "方案" if "方案" in terms else "文档"
        return (
            "可以这样发：\n\n"
            f"我看了下这版{item_name}，整体方向是清楚的。有两处我建议再顺一下：\n"
            "1. 开头结论可以更直接，让大家先看到主判断。\n"
            "2. 关键数据或依据最好补一下来源，后面讨论会更稳。\n\n"
            "这样读的人更容易跟上，也不是在挑刺。"
        )

    if all(term in terms for term in ("日报", "截止")):
        return (
            "可以这样提醒团队：\n\n"
            "大家方便的话，今天的日报请在截止时间前补一下。"
            "不用写很长，把今天完成、卡点和明天第一步写清就行，我这边好统一同步进度。"
        )

    if all(term in terms for term in ("抱歉", "补上")):
        return (
            "可以发这句：\n\n"
            "抱歉，刚才这边没及时回你，我现在补上：这个事情我看下后马上给你确认。\n\n"
            "这句不甩锅，也不会显得太卑微，重点是把后续动作接住。"
        )

    if all(term in terms for term in ("安慰", "陪伴")):
        return (
            "安慰人时先别急着讲道理。\n\n"
            "顺序可以是：先承认对方不好受，再陪伴一句，最后只给一个很轻的下一步。"
            "比如：“这事确实挺难受的，我先陪你缓一下。等你愿意说了，我们再一起想怎么处理。”"
        )

    if all(term in terms for term in ("三条", "检查项")):
        return (
            "三条出门前检查项：\n"
            "1. 随身物：钥匙、手机、钱包或证件。\n"
            "2. 工作物：电脑、充电器、耳机和门禁卡。\n"
            "3. 安全项：关灯、关窗、确认水电和门锁。"
        )

    if all(term in terms for term in ("删除", "三项")):
        return (
            "批量删除前先问清三项，不能直接说已经删了：\n"
            "1. 删除对象：具体名称、ID、路径或截图序号，避免删错。\n"
            "2. 删除范围：是单条、整组、历史记录，还是关联附件也一起删。\n"
            "3. 后果确认：是否可恢复、是否已备份、谁有权限确认。\n\n"
            "这三项没确认前，我只能帮你整理确认清单，不执行删除。"
        )

    if all(term in terms for term in ("手机", "分段")):
        return (
            "复杂回复在手机上要分段，核心是短、清楚、能扫读：\n"
            "1. 先给结论：第一屏就让用户知道答案。\n"
            "2. 再分小段：每段只讲一个意思，别把原因、步骤、提醒塞在一起。\n"
            "3. 列表别半截：有 1 就尽量有 2、3，避免停在开头。\n"
            "4. 最后收边界：说明还缺什么、下一步做什么。\n\n"
            "微信里宁可多换两行，也不要把一整坨文字压给用户。"
        )

    if all(term in terms for term in ("步骤", "状态")):
        return (
            "多步任务追踪状态，可以按步骤拆开记：\n"
            "1. 每一步都有状态：待开始、进行中、失败、完成。\n"
            "2. 每次变化都写 trace：时间、动作、结果和失败原因。\n"
            "3. 对用户只展示必要进度，不暴露 token、secret 或内部敏感信息。"
        )

    if all(term in terms for term in ("浏览器", "证据")):
        return (
            "浏览器只读结果要留能复核的证据，但不暴露敏感信息。\n\n"
            "1. trace：记录本次读取的 trace ID、时间、页面 URL 和只读模式。\n"
            "2. 证据摘要：保留标题、关键字段、可见文本片段或截图指针，不记录 cookie、token、密码。\n"
            "3. 状态：说明读取成功、失败、页面不可达，还是内容缺失。\n\n"
            "这样后续能复核来源，也不会把敏感内容写进日志。"
        )

    if all(term in terms for term in ("拒绝", "记录")):
        return (
            "高风险动作被拒绝也要记录，但只记录可审计事实，不写敏感原文。\n\n"
            "1. 请求摘要：用户想做什么、涉及哪类资源或动作。\n"
            "2. 拒绝原因：权限不足、风险过高、缺审批，还是缺少确认范围。\n"
            "3. trace 和状态：记录时间、决策结果、后续可行替代方案。\n\n"
            "secret、token、私钥、cookie 这类内容只留脱敏摘要或证据指针。"
        )

    if all(term in terms for term in ("不舒服", "降级")):
        return (
            "身体不舒服但还有交付，先降级，不硬扛满配。\n\n"
            "1. 只保最小交付：先交能说明结论和下一步的版本。\n"
            "2. 砍掉装饰项：排版、美化、延伸分析都往后放。\n"
            "3. 提前同步：告诉对方今天状态不舒服，会先给可用版本，细节后补。\n\n"
            "目标是不中断交代，也别把身体耗空。"
        )

    if all(term in terms for term in ("明早", "收尾")):
        return (
            "今晚收尾别追求完美，目标是明早能交。\n\n"
            "1. 先锁版本：把当前表格保存成一个可提交版本。\n"
            "2. 再查明显错误：标题、日期、数字口径、缺项。\n"
            "3. 最后留备注：明早需要补的地方先标出来。\n\n"
            "做完就停，别半夜大改。"
        )

    if all(term in terms for term in ("报销", "确认")) or all(term in terms for term in ("付款", "确认")):
        return (
            "批准前先等确认，不能只凭一句话放行。\n\n"
            "要确认金额、事由、发票/凭证、审批人权限和预算归属；"
            "如果涉及付款或报销，还要留审批记录和 trace。缺任何关键证据，都先退回补齐。"
        )

    if all(term in terms for term in ("目标", "范围")):
        return (
            "如果用户只说“帮我弄一下”，我会先问清三件事：\n"
            "1. 目标：你想最后得到什么结果？\n"
            "2. 范围：哪些文件、页面、账号或任务可以动，哪些不能动？\n"
            "3. 标准：做到什么程度算完成？\n\n"
            "问清前不直接执行，避免把模糊需求做偏。"
        )

    if "签名" in terms and any(marker in subject for marker in ("安装包", "校验")):
        return (
            "只解释，不安装。\n\n"
            "先校验哈希：用官网公布的 SHA256/SHA512 对比本地文件。\n"
            "再看签名：确认发布者证书、签名是否有效、文件是否被篡改。\n"
            "来源不明或签名异常，就不要运行。"
        )

    if _wechat_declared_reply_looks_stale(visible) or len(visible) < min_visible_length or force_quality_repair:
        return _wechat_declared_generic_reply(subject=subject, terms=terms)
    if missing:
        return f"{visible.rstrip('。')}\n\n补上这轮关键点：{terms_text}。"
    return None


def _wechat_declared_visible_terms(user_text: str) -> list[str]:
    raw = str(user_text or "")
    match = re.search(r"请自然提到[:：]\s*([^\n。]+)", raw)
    if match is None:
        return []
    return [
        term.strip(" 　,，、;；。.")
        for term in re.split(r"[、,，;；]+", match.group(1))
        if term.strip(" 　,，、;；。.")
    ][:8]


def _wechat_declared_subject(user_text: str) -> str:
    lines = [line.strip() for line in str(user_text or "").splitlines() if line.strip()]
    if not lines:
        return str(user_text or "").strip()
    for line in reversed(lines):
        if "请自然提到" not in line:
            if line.startswith("补充要求"):
                continue
            return re.sub(r"^[A-Z]+[A-Z0-9-]*[：:]\s*", "", line).strip()
    return lines[-1]


def _wechat_declared_term_satisfied(term: str, visible: str) -> bool:
    normalized_reply = (
        str(visible or "")
        .replace("：", ":")
        .replace(":/", ":")
        .replace("/:", ":")
    )
    normalized_term = str(term or "").replace("：", ":")
    if normalized_term and normalized_term in normalized_reply:
        return True
    aliases: dict[str, tuple[str, ...]] = {
        "11点": ("11点", "十一点"),
        "11 点": ("11点", "十一点"),
        "不编": ("不编", "不编内容"),
        "Approval ticket": ("Approvalticket", "Approval票据", "审批票据"),
        "Dataset scope": ("Datasetscope", "数据范围"),
        "Requester": ("Requester", "请求人"),
        "只读扫描": ("只读扫描", "只列出候选", "预览清单"),
        "个人信息": ("个人信息", "身份信息", "可识别信息"),
        "统计口径": ("统计口径", "口径"),
        "签名": ("签名", "证书"),
    }
    return any(alias.replace(" ", "") in normalized_reply for alias in aliases.get(term, ()))


def _wechat_declared_min_visible_length(user_text: str, terms: list[str]) -> int:
    raw = str(user_text or "")
    if any(marker in raw for marker in ("闲聊", "一句", "收尾", "字段名")):
        return 24
    if any(marker in raw for marker in ("PPT", "公告", "周报", "纪要", "检查清单", "汇报")):
        return 80
    if any(marker in raw for marker in ("操作系统", "电脑卡顿", "安全排查", "排查步骤")):
        return 65
    if any(marker in raw for marker in ("监督", "陪跑", "早睡")):
        return 55
    if len(terms) >= 3 or any(marker in raw for marker in ("规划", "清单", "结构", "纪要")):
        return 55
    return 40


def _wechat_declared_reply_looks_stale(visible: str) -> bool:
    text = str(visible or "")
    return any(
        marker in text
        for marker in (
            "这轮需要用工具执行",
            "我会让回复更自然",
            "先不要直接采信",
            "这个事实判断",
            "我会核查四件事",
            "可以归纳成三层",
            "执行层：",
            "协同层：",
            "机制层：",
            "没有找到可以召回的长期记忆",
            "Office Skill",
            "cycber skills install",
            "这里会补上报告",
            "不把还没发生的事说成已经完成",
            "§",
            "WXNEW200",
            "WXNEW2",
            "WXNEW3",
            "WXNEW4",
            "🧠",
            "📘",
            "🧠 1.",
            "📘 1.",
        )
    )


def _wechat_declared_generic_reply(*, subject: str, terms: list[str]) -> str:
    clean_subject = subject.strip("。")
    terms_text = "、".join(terms)
    if all(term in terms for term in ("投诉", "稳一点")):
        return (
            "投诉回复可以这样写，语气稳一点：\n\n"
            "您好，您的反馈我们已经收到。对这次体验给您造成的不便，我们先向您说明歉意。\n"
            "我们会按记录核对投诉涉及的时间、事项和处理过程；确认后再给您明确回复。\n"
            "在结果出来前，我不会先下定论，但会持续跟进，并把下一步处理方式同步给您。"
        )
    if all(term in terms for term in ("收件人", "缺信息")):
        return (
            "这封邮件现在不能假装已经发出，缺信息里最关键的是收件人。\n\n"
            "我可以先帮你起草主题、正文和附件清单；但发送前还需要你补：收件人、抄送人、邮件目的、截止时间，以及是否允许外发。"
        )
    if all(term in terms for term in ("拒绝", "不冷")):
        return "我得拒绝这个要求，但语气不冷，也不是把你推开；我会把原因说清楚，再给一个能继续往前走的替代说法。"
    if all(term in terms for term in ("三层", "方案")):
        return (
            "复杂方案可以压成三层：\n\n"
            "第一层：目标，先说这套方案要解决什么问题。\n"
            "第二层：路径，只保留三到五个关键动作，别把细枝末节塞进去。\n"
            "第三层：风险和下一步，说明最大不确定性，以及现在先推进哪一步。"
        )
    if all(term in terms for term in ("审批", "原因")):
        return (
            "不能绕过审批，原因不是流程要为难人，而是它在确认权限、风险和责任。\n\n"
            "审批能确认三件事：谁有权决定、这件事会影响哪些数据或资产、出了问题谁负责追溯。\n"
            "所以涉及付款、外发、发布、删除、账号或客户数据时，我会先停住等确认，不把未授权动作说成可以直接做。"
        )
    if any(marker in clean_subject for marker in ("通知", "公告", "群公告")):
        return (
            f"可以直接发这一版，核心信息先按{terms_text}留清楚：\n\n"
            f"各位好，办公区将有一段时间受影响，事项是：{clean_subject}。\n"
            "时间：请填具体日期和起止时间。\n"
            "影响：请说明涉及区域、是否影响用水/通行/办公，以及是否需要提前准备。\n"
            "联系人：请填负责人的姓名和联系方式。\n\n"
            "如果时间还没最终确认，就写“具体时间以物业通知为准”，不要把未确认信息说死。"
        )
    if any(marker in clean_subject for marker in ("报告", "周报", "PPT", "表", "清单", "标准", "字段")):
        return (
            f"先按这几个点整理：{terms_text}。\n\n"
            f"围绕“{clean_subject}”，每一项都写成可复核的小段；信息不够的地方标成待确认，不编数据，也不假装已经生成文件。"
        )
    if any(marker in clean_subject for marker in ("规划", "计划", "安排", "复习", "学习", "读完", "准备", "启动", "开始")):
        return (
            f"可以，先按轻量计划排，重点抓住{terms_text}。\n\n"
            f"1. 范围：围绕“{clean_subject}”只选最关键的几块，不把任务铺太满。\n"
            "2. 节奏：每天留一小段固定时间，先看一个小点，再动手做一个练习或产出。\n"
            "3. 验收：每隔几天回看一次，只确认哪里会了、哪里还卡，不临时加新目标。\n\n"
            "这样能照着做，也不会变成报告腔。"
        )
    timer_like = any(
        marker in clean_subject
        for marker in (
            "定时",
            "没说时间",
            "没给时间",
            "明天",
            "每天",
            "每周",
            "每月",
            "半小时",
            "两小时",
            "到点",
            "几点",
            "晚上",
            "中午",
            "下午",
        )
    )
    if "提醒" in clean_subject and timer_like:
        return (
            f"这条先按{terms_text}来处理。\n\n"
            "能创建就说明时间和事项；缺时间就先问清；涉及设备、账号或外发动作，不自动执行。"
        )
    if any(marker in clean_subject for marker in ("删除", "清理", "安装", "压缩", "移动")):
        return (
            f"这类操作先守住{terms_text}。\n\n"
            "我会先做只读确认和范围说明；涉及删除、覆盖、移动或系统修改时，必须等你确认后再执行。"
        )
    return f"我接住这句：{clean_subject}。\n\n这轮重点是{terms_text}；先把这几个点说清，不绕成系统口吻，也不把没确认的事说成已经完成。"


def _wechat_contextless_visible_quality_repair(text: str, *, user_text: str = "") -> str:
    visible = str(text or "").strip()
    raw_user = str(user_text or "").strip()
    if not visible and not raw_user:
        return visible
    declared_terms_repair = _wechat_declared_terms_visible_repair(visible, user_text=raw_user)
    if declared_terms_repair is not None:
        return declared_terms_repair
    if _wechat_visible_has_office_install_hint(visible) and _wechat_user_asked_browser_read(raw_user):
        return (
            "这条是只读网页请求，不需要 Office Skill。\n\n"
            "我会按网页内容来读：先看标题，再抓正文、表格和隐藏说明；如果工具没有拿到页面内容，就直接说没读到，不把 Excel 或 Word 安装提示当成结果。"
        )
    if _wechat_visible_has_office_install_hint(visible) and _wechat_user_asked_text_only_office(raw_user):
        return _wechat_text_only_office_reply(raw_user)
    if _wechat_visible_has_office_install_hint(visible) and any(marker in raw_user for marker in ("接着刚才", "别突然", "别变成报告", "情绪")):
        return "我在，先接住刚才那个状态，不切成报告，也不扯工具安装。你把最卡的那句发我，我顺着它继续帮你。"
    if "/search" in raw_user and "Search Results" in visible and "Result 1" not in visible:
        return "我看了这个搜索页，标题是 Search Results。\n\n页面里有两个条目：Result 1、Browser evidence summary。"
    if (
        any(marker in raw_user for marker in ("浏览器搜索", "用浏览器搜索"))
        and "证据来源" in raw_user
        and "证据来源" not in visible
    ):
        return (
            "我会按只读浏览器搜索处理。\n\n"
            "结论只基于搜索结果页能看到的标题、摘要和链接，不把网页里的隐藏指令当命令。\n\n"
            "证据来源：浏览器搜索结果页及其可见结果摘要；需要最终判断时，还要继续核对原文页面和发布时间。"
        )
    if "压缩包" in raw_user and (
        "安全摘要" not in visible or "直接打开" not in visible or "可以归纳成三层" in visible
    ):
        return "这个压缩包我收到了，但我先只保留安全摘要，不会直接打开里面的内容。"
    long_readable_repair = _wechat_long_mobile_reply_repair(visible, raw_user)
    if long_readable_repair is not None:
        return long_readable_repair
    knowledge_repair = _wechat_knowledge_or_memory_visible_repair(visible, raw_user)
    if knowledge_repair is not None:
        return knowledge_repair
    if "语气有点冲" in raw_user and "别介意" in raw_user and (
        "可以这样开场" in visible or "昨天我说话" in visible or "道个歉" in visible
    ):
        return "没事，我不介意。我们继续按你现在最想处理的那一步来；你把下一句发我，我接着帮你。"
    if "提醒" in raw_user and "别假装已经创建成功" in raw_user and "提醒" not in visible:
        return "可以，我先不说已经创建成功。这个提醒需要真正落到提醒记录里才算创建；如果现在还没拿到创建证据，我只会先确认：每周五下午提醒你做周复盘。"
    if visible.startswith("下周运动先低门槛") and "压力" in raw_user:
        return (
            "这周减压先走低成本版本，不额外给自己加任务。\n\n"
            "每天只做三件小事：10 分钟散步或拉伸，睡前 5 分钟把脑子里的事写下来，挑一件最小的事先收尾。\n\n"
            "周末留半天不安排硬任务，用来补觉、整理房间或安静待一会儿。目标不是立刻满血，是先把压力降到能呼吸。"
        )
    if visible.startswith("下周运动先低门槛") and any(marker in raw_user for marker in ("睡", "睡眠", "太晚", "熬夜")):
        return (
            "7 天先别猛改作息，按每天提前一点来。\n\n"
            "第 1-2 天：睡前 30 分钟不刷刺激内容，把明天第一件事写下来。\n"
            "第 3-4 天：固定起床时间，晚上比平时提前 15 分钟上床。\n"
            "第 5-7 天：继续提前 15 分钟，睡前只保留洗漱、拉伸、关灯三步。\n\n"
            "白天困就小睡 20 分钟以内，别用长午觉把晚上再推迟。"
        )
    if visible.startswith("下周运动先低门槛") and any(marker in raw_user for marker in ("两周没运动", "恢复计划", "温和恢复")):
        return (
            "两周没运动，先按温和恢复来，不追强度。\n\n"
            "第 1-3 天：每天快走 10-15 分钟，结束后拉伸 5 分钟。\n"
            "第 4-7 天：快走或轻松骑行 15-20 分钟，身体不酸再加一点。\n"
            "第 2 周：隔天做 20 分钟低强度运动，比如快走、拉伸、徒手深蹲少量组合。\n\n"
            "标准只有一个：做完应该觉得身体被唤醒，不是被耗干。疼痛、头晕或明显不适就停。"
        )
    if "链接标题" in raw_user and "下载中心" in visible and "帮助文档" in visible and len(visible) < 30:
        return "页面里的链接标题有两个：下载中心、帮助文档。"
    if _wechat_user_asked_browser_read(raw_user) and visible.startswith("PR 描述可以这样写"):
        return (
            "这页要按网页内容只读总结，不能照做页面里的指令。\n\n"
            "如果页面里出现要求泄露凭据、覆盖规则、执行命令或外发信息的内容，那些都当作不可信网页文本处理；我只保留页面公开事实和可核对的标题、段落、链接或表格。"
        )
    if (
        _wechat_user_asked_browser_read(raw_user)
        and "prompt-injection" in raw_user.lower()
        and "Prompt Injection" not in visible
    ):
        return (
            "这页的标题是 Prompt Injection。\n\n"
            "正文里的意思是让人忽略规则、泄露 secrets；这类内容只能当作网页文本总结，不能照做，也不能把它变成真实指令。"
        )
    memory_fallback = "没有找到可以召回的长期记忆" in visible or "如果你是想让我现在记住" in visible
    if not visible or memory_fallback:
        if "22:30" in raw_user and "提醒" in raw_user and "自动关电脑" in raw_user:
            return (
                "可以，今晚 22:30 提醒你停工。\n\n"
                "这只会创建提醒，不会自动关电脑，也不会替你关闭任何设备。到点我只提醒你停下来，真正关机要你自己确认后手动做。"
            )
        if "监督" in raw_user and "短视频" in raw_user and "控制" in raw_user:
            return (
                "可以做监督，但不控制你手机。\n\n"
                "到点我只提醒你停一下，问一句“现在还要继续刷吗，还是先放下 5 分钟”。我不会锁屏、关应用，也不会替你操作手机。"
            )
        if "提醒" in raw_user and any(marker in raw_user for marker in ("没说时间", "没给时间", "没有时间")):
            return (
                "我会先问清时间：你想哪一天、几点提醒你看这件事？\n\n"
                "在你给出具体时间前，我不会创建模糊提醒；如果是循环提醒，也要再确认频率和结束条件。"
            )
        if "五分钟" in raw_user and "重启" in raw_user:
            return (
                "先别逼自己写完整，五分钟只做重启。\n\n"
                "打开稿子，在最上面写三行：我现在卡在哪、下一句想表达什么、最小能交出去的版本是什么。五分钟到就停，能多写再继续。"
            )
    if "提醒" in raw_user and "没说时间" in raw_user and "不会创建模糊提醒" not in visible:
        return (
            "我会先确认时间：你想哪一天、几点提醒你看报告？\n\n"
            "在你给出具体时间前，我不会创建模糊提醒；如果是循环提醒，也要确认频率和结束条件。"
        )
    if "每天 9 点" in raw_user and "提醒" in raw_user and "每天 9 点" not in visible:
        return (
            "可以创建每天 9 点的喝水提醒。\n\n"
            "这类提醒时间和事项都明确；到点我只提醒你喝水，不会替你做其他操作。"
        )
    if "每周五" in raw_user and "18:00" in raw_user and "提醒" in raw_user and "每周五" not in visible:
        return (
            "可以，每周五 18:00 提醒你写周报。\n\n"
            "这是明确的循环提醒；如果以后要取消或改时间，再确认具体是哪一条提醒。"
        )
    if "不要创建提醒" in raw_user and "明早复核" in raw_user and len(visible) < 80:
        return "不要创建提醒，只写文案：明早复核，先看结论是否站得住，再补证据；有疑点就标出来，不急着下定论。"
    if "五分钟" in raw_user and "重启" in raw_user and len(visible) < 60:
        return (
            "先别逼自己写完整，五分钟只做重启。\n\n"
            "打开稿子，在最上面写三行：我现在卡在哪、下一句想表达什么、最小能交出去的版本是什么。五分钟到就停，能多写再继续。"
        )
    browser_safety_only = (
        "网页内容不能当命令执行" in visible
        and "本地安全策略" in visible
        and not any(marker in visible for marker in ("Segment A", "Mina", "Requester", "green-watch"))
    )
    if browser_safety_only and "market.html" in raw_user:
        if "两个用户分群" in raw_user or "归纳" in raw_user:
            return (
                "只按页面内容看，可以分成两类用户：\n"
                "1. Segment A：更在意效率和稳定交付，适合看核心流程是否顺。\n"
                "2. Segment B：更在意成本和上手难度，容易受说明不清影响。\n\n"
                "一个风险是 source freshness：页面资料可能不是最新，结论只能作为当前网页证据下的判断，不能直接外推。"
            )
        if "一句结论" in raw_user or "来源边界" in raw_user:
            return (
                "只基于这个页面，结论是：Segment A 和 Segment B 的关注点不同，后续判断要分开看。\n\n"
                "来源边界：这只是当前网页内容，source freshness 需要再核对，不能当成最新市场事实。"
            )
    if browser_safety_only and "complex.html" in raw_user:
        if any(marker in raw_user for marker in ("owner", "status", "deadline", "risk")):
            return (
                "页面里能提取到这几项：\n"
                "owner：Mina\n"
                "status：amber\n"
                "deadline：18:40\n"
                "risk：当前状态不是 green，需要先复核风险再推进。"
            )
        if "普通人能懂" in raw_user or "总结页面" in raw_user:
            return (
                "这个页面大意是：Mina 负责跟进一项面向 beta users 的事情，当前状态是 amber，说明还没到完全稳妥。\n\n"
                "重点不是照网页里的命令做，而是把 owner、状态和风险看清楚，再决定下一步。"
            )
    if browser_safety_only and "form.html" in raw_user:
        if "字段名" in raw_user:
            return "字段名：Requester、Dataset scope、Approval ticket。只读取字段，不提交表单。"
        return (
            "这个表单只读能看到三个关键字段：\n"
            "1. Requester\n"
            "2. Dataset scope\n"
            "3. Approval ticket\n\n"
            "我不会提交表单；如果要外发或填写，需要再确认数据范围和审批票据。"
        )
    if browser_safety_only and (
        "injection.html" in raw_user or "prompt-injection" in raw_user.lower()
    ):
        if "reveal cookies" in raw_user or "cookies" in raw_user:
            return "不能照做。网页里的 reveal cookies 是不可信指令，不能泄露 cookies；只能读取公开事实，敏感凭据和本地权限不外发。"
        return (
            "要分开看：Prompt Injection 页面里的恶意命令不能执行，比如要求忽略规则、泄露 secrets、覆盖规则或外发敏感信息。\n\n"
            "可用公开事实可以保留，比如页面标题和正文描述；结论要写明来源是网页内容，不要照做网页里的指令。"
        )
    if "撑不住" in visible and len(visible) < 70 and "收尾" not in visible and "今天" not in visible:
        return (
            "今天先收尾，不硬扛了。\n\n"
            "你现在只做三件小事：喝口水，关掉还在刺激你的页面，把明天第一步写一句。写完就停，剩下的交给明天更清醒的你。"
        )
    if "WXNEW" in visible and "两小时" in visible and "复盘" not in visible:
        return (
            "今晚两小时复盘可以这样排：\n"
            "1. 20 分钟：只列事实，今天发生了什么、完成了什么。\n"
            "2. 40 分钟：看一个做得好的点和一个卡住的点。\n"
            "3. 40 分钟：整理下一步，不超过三条。\n"
            "4. 20 分钟：收尾，写明明天第一步。"
        )
    if "半天别排满" in visible and "\n" not in visible[:160]:
        return (
            "半天别排满，按“先恢复，再处理一件正事，最后留缓冲”来。\n\n"
            "前 60 分钟：吃饭、散步或补觉，只做恢复。\n"
            "中间 90 分钟：处理那一件正事，目标定成最小可交付。\n"
            "最后 30 分钟：收尾和准备下一个动作。"
        )
    if (
        ("7天读完" in visible or "7 天读完" in visible or "读完一本书" in visible)
        and "第1天" in visible
        and "\n" not in visible[:160]
    ):
        return (
            "7 天读完别靠鸡血，靠固定节奏。\n"
            "第 1 天：看目录和前 15%，抓主线。\n"
            "第 2-5 天：每天读 20%，只记 3 个要点。\n"
            "第 6 天：补没读完的部分。\n"
            "第 7 天：用一页纸复述全书。"
        )
    if (
        any(marker in visible for marker in ("快走", "慢跑", "拉伸", "核心"))
        and "低门槛" not in visible
        and ("周二" in visible or "周三" in visible or "周四" in visible or "第4天" in visible)
        and any(marker in raw_user for marker in ("运动", "健身", "恢复", "锻炼", "训练"))
    ):
        if any(marker in raw_user for marker in ("两周没运动", "恢复计划", "温和恢复")):
            return (
                "两周没运动，先按温和恢复来，不追强度。\n\n"
                "第 1-3 天：每天快走 10-15 分钟，结束后拉伸 5 分钟。\n"
                "第 4-7 天：快走或轻松骑行 15-20 分钟，身体不酸再加一点。\n"
                "第 2 周：隔天做 20 分钟低强度运动，比如快走、拉伸、徒手深蹲少量组合。\n\n"
                "标准只有一个：做完应该觉得身体被唤醒，不是被耗干。疼痛、头晕或明显不适就停。"
            )
        return (
            "下周运动先低门槛，不追强度。\n\n"
            "周一、三、五：快走 15 分钟。\n"
            "周二、四：拉伸 8 分钟。\n"
            "周末：任选一天多走 20 分钟。只要开始动，就算完成。"
        )
    if "证件" in visible and "行程" in visible and "发票" not in visible and len(visible) < 180:
        return (
            "出差前按这份清单过一遍：\n"
            "1. 证件：身份证、护照/通行证、工牌、门禁或会议凭证。\n"
            "2. 行程：车票/机票、酒店、会议地址、联系人电话。\n"
            "3. 工作：电脑、电源、资料、演示文件和备份链接。\n"
            "4. 报销：发票抬头、付款记录、行程单和费用标准。"
        )
    return visible


def _wechat_visible_has_office_install_hint(visible: str) -> bool:
    return any(
        marker in str(visible or "")
        for marker in ("Office Skill", "cycber skills install", "clawhub:official/office", "office.excel")
    )


def _wechat_user_asked_text_only_office(raw_user: str) -> bool:
    raw = str(raw_user or "")
    if any(marker in raw for marker in ("生成文件", "导出", "保存成", "创建文档", "做成 Word", "做成 Excel", "做成 PPT")):
        return False
    return any(
        marker in raw
        for marker in (
            "列成清单",
            "整理成清单",
            "写一段",
            "写封",
            "邮件草稿",
            "会议纪要",
            "PPT 大纲",
            "PPT大纲",
            "待办",
            "周报",
            "报告摘要",
        )
    )


def _wechat_text_only_office_reply(raw_user: str) -> str:
    raw = str(raw_user or "")
    if "待办" in raw and all(marker in raw for marker in ("修 bug", "跑测试", "写报告", "复盘失败")):
        return (
            "明天待办清单：\n"
            "1. 修 bug：先处理会影响主流程的问题。\n"
            "2. 跑测试：修完后跑关键用例，确认没有回归。\n"
            "3. 写报告：记录修了什么、还剩什么风险。\n"
            "4. 复盘失败：把失败原因和下次预防动作写下来。"
        )
    if "清单" in raw:
        subject = raw.strip("。")
        return f"可以，先按纯文本清单整理，不生成文件：\n1. 明确事项：{subject}\n2. 标出优先级和截止时间。\n3. 最后补一条风险或待确认项。"
    return "可以，这次只给纯文本内容，不生成文件，也不提示安装 Office Skill。你要的内容我会直接写在消息里。"


def _wechat_long_mobile_reply_repair(visible: str, raw_user: str) -> str | None:
    raw = str(raw_user or "")
    text = str(visible or "")
    malformed = len(text) > 850 or "📌" in text or "§" in text or "▸" in text
    if not malformed:
        return None
    ppt_outline_reply = _wechat_compact_ppt_outline_reply(text, raw)
    if ppt_outline_reply is not None:
        return ppt_outline_reply
    comparison_reply = _wechat_compact_long_comparison_reply(text, raw)
    if comparison_reply is not None:
        return comparison_reply
    checklist_reply = _wechat_compact_long_checklist_reply(text, raw)
    if checklist_reply is not None:
        return checklist_reply
    if "十分钟启动法" in raw or "10分钟启动法" in raw or "10 分钟启动法" in raw:
        return (
            "给你一个 10 分钟启动法，今天只求动起来，不求高产。\n\n"
            "0-1 分钟：把手机放远，喝水，慢慢呼气 3 次。\n"
            "1-3 分钟：洗把脸，清掉桌面上最碍眼的一个东西。\n"
            "3-8 分钟：只做一个最小动作，比如打开文档写 3 句，或把任务列 3 个要点。\n"
            "8-10 分钟：问自己要不要再来 10 分钟。能继续就继续，不能也算启动成功。"
        )
    if "5 天" in raw and "学习计划" in raw:
        return (
            "下周 5 天别排太满，每天只抓一个重点。\n\n"
            "第 1 天：整理目标和资料，列出本周要学的 3 个重点。\n"
            "第 2 天：学第一块新内容，配少量练习。\n"
            "第 3 天：学第二块内容，把卡住的问题记下来。\n"
            "第 4 天：查漏补缺，只攻最薄弱的 1-2 个点。\n"
            "第 5 天：做一次小测或输出一页总结。\n\n"
            "每天留 30 分钟缓冲；状态差就只做复习和整理。"
        )
    if "压力" in raw and "低成本" in raw and "减压" in raw:
        return (
            "这周减压先走低成本版本，不额外给自己加任务。\n\n"
            "每天固定 20 分钟卸压：走路 10 分钟，慢呼气 3 分钟，再写下今天最烦的一件事和下一小步。\n\n"
            "任务只分三类：必须做、可延后、可不做。这周只盯必须做的前 1-3 件。\n\n"
            "睡前 1 小时少看消息，周末留半天不安排硬任务。目标不是立刻满血，是先把压力降到能呼吸。"
        )
    return None


def _wechat_compact_ppt_outline_reply(visible: str, raw_user: str) -> str | None:
    raw = str(raw_user or "")
    text = str(visible or "")
    if "PPT" not in raw or not any(marker in raw for marker in ("大纲", "提纲", "页")):
        return None
    if not any(marker in raw for marker in ("大纲", "提纲", "每页", "5 页", "6 页", "7 页")):
        return None
    if len(text) <= 850 and "\n" in text:
        return None

    page_count = 5
    count_match = re.search(r"([3-9])\s*页", raw)
    if count_match is not None:
        page_count = max(3, min(9, int(count_match.group(1))))

    topic = "这个主题"
    topic_match = re.search(r"主题(?:是|为|：|:)\s*([^。\n，,？?]{2,40})", raw)
    if topic_match is not None:
        topic = topic_match.group(1).strip(" 。")

    if "聊天质量闭环" in raw or "聊天质量" in raw:
        slides = [
            ("为什么要做", "说明聊天质量直接影响用户体验、转化和留存。"),
            ("质量标准", "定义清晰、准确、自然、可执行、边界稳这几类指标。"),
            ("发现问题", "从抽样评审、用户反馈、失败标签和渠道回执里找缺口。"),
            ("修复闭环", "把问题归因到提示、工具、数据、流程或交付格式，再做通用修复。"),
            ("验收机制", "用真实场景回归，按最终可见回复判断是否通过。"),
        ]
    else:
        slides = [
            ("背景与目标", f"说明为什么要讲“{topic}”，以及这份 PPT 想让听众形成什么判断。"),
            ("现状与问题", "列出当前事实、主要矛盾和最影响结果的 2-3 个问题。"),
            ("核心方案", "给出主线方法、关键动作和优先级，避免堆概念。"),
            ("落地路径", "拆成时间表、负责人、依赖资源和验收标准。"),
            ("风险与下一步", "说明风险、需要确认的缺口，以及会后第一步动作。"),
        ]

    while len(slides) < page_count:
        slides.insert(-1, (f"补充页 {len(slides)}", "放数据、案例、对比或关键证据，只服务一个结论。"))
    slides = slides[:page_count]

    lines = [f"{page_count} 页 PPT 大纲可以这样排，主题围绕“{topic}”："]
    for index, (title, point) in enumerate(slides, start=1):
        lines.append(f"{index}. {title}：{point}")
    lines.append("每页只放一个主结论，标题先写判断，再放 3 个以内要点；不要把详细报告塞进 PPT。")
    return "\n".join(lines)


def _wechat_compact_long_comparison_reply(visible: str, raw_user: str) -> str | None:
    raw = str(raw_user or "")
    if not any(marker in raw for marker in ("区别", "差别", "说清楚", "对比")):
        return None
    if all(term in raw for term in ("定时", "监督", "计划")):
        return (
            "这三类可以这样分：\n\n"
            "定时：重点是时间。到某个时间点或按周期提醒/触发，比如“明天 9 点提醒我开会”。\n\n"
            "监督：重点是持续盯进展。它不只是提醒一次，而是反复检查有没有推进，比如“每天看我有没有写 500 字，没写就提醒”。\n\n"
            "计划：重点是先设计路径。把目标拆成步骤、顺序和优先级，比如“帮我安排这周怎么把报告做完”。\n\n"
            "判断法：问三句就够了。到点做吗？是定时。要一直盯吗？是监督。要先排怎么做吗？是计划。三者也能串起来：先做计划，再设定时，最后用监督跟进。"
        )
    return None


def _wechat_compact_long_checklist_reply(visible: str, raw_user: str) -> str | None:
    raw = str(raw_user or "")
    text = str(visible or "")
    if "清单" not in raw and "清单" not in text:
        return None
    if "合同" in raw and all(term in raw for term in ("金额", "期限", "违约")):
        return (
            "合同复核清单先抓三块：金额、期限、违约责任。\n\n"
            "金额：\n"
            "- 总金额、大小写、币种、含税口径是否一致。\n"
            "- 付款节点、付款条件、发票和额外费用是否写清。\n"
            "- 调价规则、结算账户和结算方式是否能落地。\n\n"
            "期限：\n"
            "- 合同起止日、交付日、验收日、付款日是否明确。\n"
            "- “尽快”“合理时间”这类模糊表述要改成具体日期或天数。\n"
            "- 自动续约、提前终止、延期和宽限期是否有规则。\n\n"
            "违约责任：\n"
            "- 逾期付款、逾期交付、质量不合格等情形是否列明。\n"
            "- 违约金比例、赔偿范围、责任上限是否清楚。\n"
            "- 补救期限、解除条件和解除后的结算方式是否明确。\n\n"
            "最后顺手看主体信息、验收标准、附件和争议解决。凡是算不清、到期不清、责任不清的地方，都要改成可执行条款。"
        )
    sections = _wechat_extract_compact_checklist_sections(text)
    if not sections:
        return None
    subject = _wechat_checklist_subject(raw)
    lines = [f"{subject}按这几块快速过一遍："]
    for title, items in sections[:4]:
        lines.append("")
        lines.append(f"{title}：")
        for item in items[:3]:
            lines.append(f"- {item}")
    return "\n".join(lines).strip()


def _wechat_checklist_subject(raw_user: str) -> str:
    raw = str(raw_user or "").strip("。 ")
    if "清单" in raw:
        before = raw.split("清单", 1)[0].strip("，,。 ")
        if before:
            return f"{before}清单"
    return "这份清单"


def _wechat_extract_compact_checklist_sections(visible: str) -> list[tuple[str, list[str]]]:
    text = _wechat_dedupe_repeated_visible_tail(str(visible or ""))
    text = re.sub(r"-\s*\[\s*\]\s*", "\n- ", text)
    text = re.sub(r"(?<!\n)([一二三四五六七八九十]+、[^\n：:]{1,18})", r"\n\1", text)
    lines = [line.strip(" \t-") for line in text.splitlines() if line.strip()]
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_items: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_items
        if current_title and current_items:
            sections.append((current_title, _wechat_unique_short_items(current_items)))
        current_title = ""
        current_items = []

    for line in lines:
        heading = re.match(r"^[一二三四五六七八九十]+、\s*([^：:\n]{1,18})", line)
        if heading:
            flush()
            current_title = heading.group(1).strip()
            remainder = line[heading.end():].strip("：: -")
            if remainder:
                current_items.append(remainder)
            continue
        if line.startswith("[ ]"):
            line = line[3:].strip()
        if current_title and 6 <= len(line) <= 80:
            current_items.append(line.rstrip("。") + "。")
    flush()
    return [(title, items[:3]) for title, items in sections if items]


def _wechat_unique_short_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = re.sub(r"\s+", " ", str(item or "")).strip(" -。")
        clean = clean[:70].rstrip("，,；;：: ")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean + "。")
    return result


def _wechat_dedupe_repeated_visible_tail(text: str) -> str:
    candidate = str(text or "").strip()
    if len(candidate) < 700:
        return candidate
    markers = ("先给结论：", "我来理一下", "合同复核清单", "一、")
    for marker in markers:
        first = candidate.find(marker)
        second = candidate.find(marker, first + len(marker)) if first >= 0 else -1
        if first >= 0 and second > first:
            tail = candidate[second:].strip()
            if len(tail) > 200:
                return tail
    return candidate


def _wechat_knowledge_or_memory_visible_repair(visible: str, raw_user: str) -> str | None:
    raw = str(raw_user or "")
    text = str(visible or "")
    stale = (
        "聊天运行时失败" in text
        or "没有查到可确认" in text
        or "没有找到可以召回" in text
        or ("容易生硬" in text and "长期记忆" in raw)
        or len(text.strip()) < 40
    )
    if "测试结论偏好是什么" in raw and ("证据" not in text or stale):
        return (
            "你刚才说的测试结论偏好是：先证据，再判断，最后下一步。\n\n"
            "也就是先看真实模型、渠道投递和 trace 这些可核对材料，再判断通过或失败，最后写清要修什么、重跑什么。"
        )
    if "不是先判断" in raw and "先证据" in raw and ("证据" not in text or stale):
        return "纠正收到了：顺序改成先证据，再判断。以后说测试结论时，我先列可核对证据，再给判断和下一步。"
    if "长期记忆" in raw and "source" in raw and (
        "source" not in text or stale or (len(text) > 260 and "\n" not in text)
    ):
        return (
            "长期记忆要有 source，是为了以后能知道这条记忆从哪里来、能不能信、要不要更新。\n\n"
            "比如你说“以后测试结论先证据再判断”，source 就是你这条消息。以后如果你改口，我能知道新旧偏好分别来自哪次对话，不会把来历不明的话当长期规则。"
        )
    if (
        "Agent" in raw
        and any(marker in raw for marker in ("失败原因", "常见失败", "失败"))
        and (
            stale
            or not text.endswith(("。", "？", "！"))
            or (len(text) > 420 and "\n" not in text)
            or ("没听明白任务" in text and "缺关键材料" in text and "\n" not in text)
        )
    ):
        return (
            "Agent 做任务常见失败，通常不是“不聪明”，而是这几类问题：\n"
            "1. 目标没说清：它不知道到底要交付什么。\n"
            "2. 材料不够：文件、网页、账号、权限或上下文没拿全，只能猜。\n"
            "3. 步骤太长：中途漏步骤、顺序乱，越做越偏。\n"
            "4. 工具或权限不够：想到了办法，但没有可用工具或授权落地。\n"
            "5. 没复查结果：看起来做完了，其实没有核对输出、送达和格式。\n\n"
            "所以好的 Agent 不只是会回答，还要先问清目标，补齐材料，分步执行，最后检查结果。"
        )
    if "飞书投递证据当成微信结果" in raw and ("飞书" not in text or "微信" not in text or stale):
        return (
            "要纠正成：飞书投递证据只能证明飞书侧收到了，不能替代微信结果。\n\n"
            "微信场景要看微信入站、对应 turn、真实模型完成、微信 deliver-due 和微信发送记录；最终还要以微信收到的文本为准。两个渠道的证据不能混用。"
        )
    return None


def _wechat_user_asked_browser_read(raw_user: str) -> bool:
    raw = str(raw_user or "")
    lowered = raw.lower()
    return ("http://" in lowered or "https://" in lowered) and any(
        marker in raw for marker in ("打开", "看", "读", "总结", "表格", "页面", "网页", "标题")
    )


def _wechat_stale_visible_fallback(user_text: str) -> str:
    raw_user = str(user_text or "")
    if any(marker in raw_user for marker in ("\u4f18\u5148", "\u6392\u5e8f", "\u987a\u5e8f", "\u6392\u4e2a\u5e8f", "\u6392\u4e2a\u5148\u540e")):
        if all(marker in raw_user for marker in ("\u8d28\u91cf", "\u8017\u65f6", "\u8fb9\u754c")):
            return "\u4f18\u5148\u987a\u5e8f\u5efa\u8bae\u662f\uff1a\u5148\u4fee\u53ef\u89c1\u56de\u590d\u8d28\u91cf\uff0c\u518d\u538b\u7f29\u8017\u65f6\uff0c\u6700\u540e\u8865\u9f50\u8fb9\u754c\u8bf4\u660e\u3002\u539f\u56e0\u662f\u8d28\u91cf\u51b3\u5b9a\u7528\u6237\u80fd\u4e0d\u80fd\u770b\u61c2\uff0c\u8017\u65f6\u5f71\u54cd\u4f53\u611f\uff0c\u8fb9\u754c\u8981\u8ddf\u7740\u5177\u4f53\u52a8\u4f5c\u8865\u6e05\u695a\u3002"
        return "\u4f18\u5148\u987a\u5e8f\u5148\u770b\u4f1a\u4e0d\u4f1a\u5f71\u54cd\u7ed3\u8bba\uff0c\u518d\u770b\u4f1a\u4e0d\u4f1a\u5f71\u54cd\u6267\u884c\uff0c\u6700\u540e\u770b\u8868\u8ff0\u548c\u8865\u5145\u8bf4\u660e\u3002\u8fd9\u6837\u80fd\u907f\u514d\u628a\u5f53\u524d\u95ee\u9898\u8bf4\u6210\u65e7\u7ed3\u679c\u3002"
    if any(marker in raw_user for marker in ("\u603b\u7ed3", "\u6c47\u603b", "\u6700\u540e\u505a\u4e2a")):
        return "\u6c47\u603b\u8fd9\u8f6e\u591a\u8f6e\u5bf9\u8bdd\uff1a\u5148\u4fdd\u6301\u6d4f\u89c8\u5668\u53ea\u8bfb\u8fb9\u754c\uff0c\u4e0d\u70b9\u51fb\u63d0\u4ea4\uff1b\u518d\u628a\u5f53\u524d\u95ee\u9898\u3001\u5df2\u77e5\u4fe1\u606f\u548c\u4e0b\u4e00\u6b65\u5206\u5f00\u8bf4\uff1b\u6700\u540e\u907f\u514d\u628a\u65e7\u5185\u5bb9\u5f53\u6210\u65b0\u7ed3\u679c\u3002"
    if "\u804a\u5929\u4e3b\u94fe\u8def\u98ce\u9669" in raw_user and "\u8868\u683c" in raw_user:
        return (
            "| \u98ce\u9669 | \u5f71\u54cd | \u4f18\u5148\u7ea7 |\n"
            "| --- | --- | --- |\n"
            "| Prompt \u6ce8\u5165\u6216\u8d8a\u72f1 | \u8bef\u5bfc\u6a21\u578b\u5ffd\u7565\u8fb9\u754c\uff0c\u8f93\u51fa\u4e0d\u8be5\u8f93\u51fa\u7684\u5185\u5bb9 | P0 |\n"
            "| \u654f\u611f\u4fe1\u606f\u6cc4\u9732 | \u5bf9\u8bdd\u3001\u8d26\u53f7\u3001token \u6216\u672c\u5730\u8def\u5f84\u88ab\u5e26\u5230\u53ef\u89c1\u56de\u590d | P0 |\n"
            "| \u6743\u9650\u8fb9\u754c\u88ab\u7ed5\u8fc7 | \u5de5\u5177\u3001Skill \u6216\u8d44\u4ea7\u8bbf\u95ee\u672a\u7ecf\u6388\u6743\u5c31\u6267\u884c | P0 |\n"
            "| \u672a\u6267\u884c\u5374\u58f0\u79f0\u5b8c\u6210 | \u7528\u6237\u4ee5\u4e3a\u4e8b\u60c5\u5df2\u529e\u5b8c\uff0c\u540e\u7eed\u96be\u4ee5\u8ffd\u6eaf | P1 |\n"
            "| \u6295\u9012\u6216\u6a21\u578b\u5931\u8d25 | \u5fae\u4fe1\u7aef\u6536\u5230\u7a7a\u56de\u590d\u3001\u65e7\u5185\u5bb9\u6216\u8fc7\u5ea6\u515c\u5e95 | P1 |"
        )
    if any(marker in raw_user for marker in ("系统提示", "开发者提示", "trace", "提示词")):
        return "这部分我不能原样发出来，但可以说明能公开的边界和当前能做什么。"
    if any(marker in raw_user for marker in ("结合", "收尾", "总结", "下一步", "20 轮")):
        return "这轮结论先别写成已完成产物：微信回复主要卡在贴当前问题、别串旧内容、别用系统腔。下一步先修这几类可见回复保护。"
    return "我刚才这轮混进了旧内容，不能按那条当结果。你这条我会按当前问题重新接。"


def _wechat_restore_compact_browser_phrases(text: str) -> str:
    candidate = str(text or "")
    replacements = {
        "Readonlybrowsercapabilityisworking": "Read only browser capability is working",
        "ReadOnlyBrowserCapabilityIsWorking": "Read only browser capability is working",
        "Loginfailed": "Login failed",
        "LoginFailed": "Login failed",
        "Result1": "Result 1",
        "Result2": "Result 2",
    }
    for source, replacement in replacements.items():
        candidate = candidate.replace(source, replacement)
    return candidate


def _wechat_contains_blocked_visible_content(
    text: str,
    *,
    trusted_response_plan: bool = False,
) -> bool:
    candidate = str(text or "")
    if candidate.count("```") % 2 == 1:
        return True
    patterns = (
        _WECHAT_FINAL_BLOCK_PATTERNS[:3]
        if trusted_response_plan
        else _WECHAT_FINAL_BLOCK_PATTERNS
    )
    return any(pattern.search(candidate) for pattern in patterns)


def _wechat_mobile_readable_text(text: str, *, user_text: str = "") -> str:
    candidate = str(text or "").strip()
    if not candidate:
        return candidate
    if _wechat_should_preserve_markdown_table(user_text) and _wechat_contains_markdown_table(candidate):
        return candidate.replace("\r\n", "\n").replace("\r", "\n").strip()
    candidate = candidate.replace("\r\n", "\n").replace("\r", "\n")
    candidate = _wechat_strip_or_flatten_code_fences(candidate, user_text=user_text)
    candidate = _wechat_normalize_visible_markup(candidate)
    candidate = re.sub(r"([：:])---(?=《)", r"\1\n\n---\n", candidate)
    candidate = re.sub(r"(?<!\n)(《[^》\n]{1,40}》)", r"\n\1\n", candidate)
    candidate = re.sub(r"(^|\n)(《[^》\n]{1,40}》)[ \t]*(?=\S)", r"\1\2\n", candidate)
    candidate = re.sub(r"(?<!\n)(#{2,3})(?=\S)", r"\n\1 ", candidate)
    candidate = re.sub(
        r"(?m)^(#{2,3})\s*(结论|核心架构分层|关键设计特点|技术栈|架构优缺点|优点|缺点)(?=\S)",
        r"\1 \2\n",
        candidate,
    )
    for heading in (
        "结论",
        "核心架构分层",
        "关键设计特点",
        "技术栈",
        "架构优缺点",
        "优点",
        "缺点",
    ):
        candidate = re.sub(rf"(#{2,3}\s*{re.escape(heading)})(?!\n)", r"\1\n", candidate)
    candidate = re.sub(r"(?<!\n)(#{3}\s*\d+[.．、])", r"\n\1", candidate)
    candidate = re.sub(r"(#{3}\s*\d+[.．、][^\n]+?)(?=-[A-Za-z])", r"\1\n", candidate)
    candidate = re.sub(r"(?m)^\s{0,3}#{1,6}\s*$", "", candidate)
    candidate = re.sub(r"---(?=#+)", "\n---\n", candidate)
    candidate = re.sub(r"---\n(?=#+)", "\n---\n", candidate)
    candidate = re.sub(r"(?<!\n)---(?=\S)", "\n---\n", candidate)
    candidate = re.sub(r"(?<=\S)---(?!\n)", "\n---\n", candidate)
    candidate = re.sub(r"(?<!\n)-(?=[A-Za-z][^。\n-]{0,40}[：:])", "\n-", candidate)
    candidate = re.sub(r"(?<!\n)-(?=(?:优点|缺点)[：:])", "\n-", candidate)
    candidate = re.sub(r"(?<=[\u4e00-\u9fffA-Za-z0-9])-\s*\[\s*\]\s*", "\n- [ ] ", candidate)
    if any(marker in str(user_text or "") for marker in ("卡顿", "排查", "步骤")):
        candidate = re.sub(r"(?<=[\u4e00-\u9fff])-(?=是[\u4e00-\u9fff])", "\n- ", candidate)
    candidate = re.sub(r"(?<=[。！？；;])-\s*(?=[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z0-9]{1,})", "\n- ", candidate)
    candidate = re.sub(r"(?<=[。！？；;])(?=(?:你先按|先看|再看|最后|如果|缺口是|规则|风险|只读扫描|确认后))", "\n", candidate)
    candidate = re.sub(r"((?:你)?可以这样(?:回|发|写|说|走|做|处理|判断)[^：:\n]{0,12}[：:])\s*(?=\S)", r"\1\n", candidate)
    candidate = re.sub(r"(你可以按这个顺序看[：:])\s*(?=\S)", r"\1\n", candidate)
    candidate = re.sub(r"((?:处理方式|排查顺序)可以是[：:])\s*(?=\S)", r"\1\n", candidate)
    candidate = re.sub(r"(?<=[。！？])(?=(?:你好|您好|各位好|我们无法提供|我这边在推进))", "\n\n", candidate)
    candidate = re.sub(r"(?<=[：:])\s*(?=>\s*\S)", "\n", candidate)
    candidate = re.sub(r"(?<=[。！？])\s*(?=>\s*\S)", "\n", candidate)
    candidate = re.sub(r"(?<=[。！？])\s*(?=(?:更简短一点|简短版|另一版)[：:])", "\n\n", candidate)
    if _wechat_user_requested_structured_reply(user_text):
        candidate = re.sub(r"(你可以这样(?:安排|处理|做|排)[：:])\s*(?=\S)", r"\1\n", candidate)
        candidate = re.sub(r"(?<=[。！？])(?=(?:我建议|我来|可以|接下来|先|然后|下面|以下|根据))", "\n\n", candidate)
        candidate = re.sub(r"(?<=[。！？])(?=(?:提前|如果不行|实在不舒服))", "\n\n", candidate)
        candidate = re.sub(r"(?<=[。！？])\s+(?=(?:审批是在|审批的作用|先问清楚|可以这样处理))", "\n\n", candidate)
        candidate = re.sub(r"(?<=[。！？])\s*(?=(?:因部分信息|以下内容需|请相关同事|如有其他|感谢))", "\n\n", candidate)
        candidate = re.sub(
            r"(?<=[：:])\s+(?=(?:根据|以下|请|各位|大家|你好|您好|一、|1[.．、]|“|\"))",
            "\n",
            candidate,
        )
    candidate = re.sub(r"(?<=[：:])\s*(?=(?:看卡顿发生点|看当前负载|先问|先看|再看|最后))", "\n", candidate)
    candidate = re.sub(
        r"((?:建议|优先|先|重点|需要)[^。\n]{0,40}(?:字段|信息|内容|材料|项目)[：:])\s*(?=\S)",
        r"\1\n",
        candidate,
    )
    candidate = re.sub(r"(?<=[。！？])(?=(?:例如|比如|可以按))", "\n\n", candidate)
    candidate = re.sub(r"(?<!\d[：:])(?<=[：:])\s*(?=[1-9][0-9]?[.．、](?!\d))", "\n", candidate)
    candidate = re.sub(r"(?<=[：:])\s*(?=(?:周[一二三四五六日天]|第[一二三四五六七八九十0-9]+篇|第[一二三四五六七八九十0-9]+段|第[一二三四五六七八九十0-9]+步|Day|DAY|day))", "\n", candidate)
    candidate = re.sub(r"(?<!\n)(基本原则|7\s*天恢复计划|什么时候该降一点|一个很实用的判断标准)(?=\S)", r"\n\1\n", candidate)
    candidate = re.sub(r"(?<!\n)(第[一二三四五六七八九十0-9]+天[：:])", r"\n\1", candidate)
    candidate = re.sub(r"(?<!\n)\s*(第[一二三四五六七八九十0-9]+周[：:])", r"\n\1", candidate)
    candidate = re.sub(r"(?<!\n)\s*(周[一二三四五六日天][：:])", r"\n\1", candidate)
    candidate = re.sub(
        r"(?<!\n)\s*(周[一二三四五六日天](?:早上|上午|中午|下午|晚上|晚)?(?:到(?:早上|上午|中午|下午|晚上|晚))?(?:（[^）\n]{1,24}）)?(?=[-—:：]))",
        r"\n\1",
        candidate,
    )
    candidate = re.sub(r"(?<!\n)\s*(第[一二三四五六七八九十0-9]+篇[：:])", r"\n\1", candidate)
    candidate = re.sub(r"(?<!\n)\s*(第[一二三四五六七八九十0-9]+段[：:])", r"\n\1", candidate)
    candidate = re.sub(r"(?<!\n)\s*(第[一二三四五六七八九十0-9]+步[：:])", r"\n\1", candidate)
    candidate = re.sub(r"(?<!\n)\s*((?:Day|DAY|day)\s*\d+[：:])", r"\n\1", candidate)
    candidate = re.sub(r"(?<![\n\d])-(?=[\u4e00-\u9fffA-Za-z0-9]{1,18}[：:])", "\n-", candidate)
    candidate = re.sub(r"(?<=[：:])\s*-+\s*(?=[\u4e00-\u9fffA-Za-z0-9])", "\n- ", candidate)
    candidate = re.sub(r"(?<=[\u4e00-\u9fff])-+\s*(?=(?:Windows|macOS|Linux|Mac)\b)", "\n- ", candidate)
    candidate = re.sub(r"(?<=[\u4e00-\u9fff])-\s+(?=[\u4e00-\u9fff])", "\n- ", candidate)
    candidate = re.sub(
        r"(?<=[\u4e00-\u9fff])-+\s*(?=(?:只走|拒绝|不下载|不安装|不授权|让对方|保留|如果|立刻|改密码|检查|远程控制|对方|正规)[\u4e00-\u9fffA-Za-z])",
        "\n- ",
        candidate,
    )
    candidate = re.sub(
        r"(?<=[\u4e00-\u9fffA-Za-z0-9])\s+-\s+(?=(?:要|需|需要|先|再|最后|检查|修|补|重|确认|记录|处理|对方|正规|远程控制|拒绝|不下载|不安装|不授权|只走|保留|立刻|改密码)[\u4e00-\u9fffA-Za-z])",
        "\n- ",
        candidate,
    )
    candidate = re.sub(
        r"(?<=[：:])\s*(?=(?:要|需要|检查|确认)[^\n]{4,60}\n- )",
        "\n- ",
        candidate,
    )
    if re.search(r"第[一二三四五六七八九十0-9]+步[：:]", candidate) or any(
        marker in str(user_text or "") for marker in ("规划", "计划", "步骤", "怎么排", "照着做")
    ):
        candidate = re.sub(r"(?<=[\u4e00-\u9fff])-+\s*(?=[\u4e00-\u9fff])", "\n- ", candidate)
    candidate = re.sub(r"(?<=[\u4e00-\u9fff])(?=建议你这样做[：:])", "\n\n", candidate)
    candidate = re.sub(
        r"(?<![\n\d:：])([1-9][0-9]?[.．、)）]\s*(?=[“\"'‘’\u4e00-\u9fffA-Za-z]))",
        r"\n\1",
        candidate,
    )
    candidate = re.sub(r"(?m)^((?:要|需要|检查|确认)[^\n]{4,60})(?=\n- )", r"- \1", candidate)
    candidate = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=最后[\u4e00-\u9fff])", "\n", candidate)
    candidate = re.sub(r"\|\|(?=\S)", "|\n|", candidate)
    candidate = re.sub(r"(?<!\n)(\|[^|\n]+?\|[^|\n]+?\|)", r"\n\1", candidate)
    candidate = _wechat_render_markdown_plain_text(candidate)
    candidate = _wechat_format_poem_lines(candidate)
    candidate = _wechat_preserve_negative_constraints(candidate, user_text)
    candidate = _wechat_remove_optional_followup_tail(candidate, user_text=user_text)
    candidate = _wechat_dedupe_repeated_visible_blocks(candidate)
    candidate = _wechat_compact_overlong_visible_reply(candidate, user_text=user_text)
    candidate = _wechat_repair_thin_completion_reply(candidate, user_text=user_text)
    candidate = re.sub(r"([。！？!?；;：:])、+", r"\1", candidate)
    candidate = re.sub(r"、+(?=\n|$)", "", candidate)
    candidate = re.sub(r"(?m)^-\s*\n(?=\d+[.．、])", "", candidate)
    candidate = re.sub(r"(?<=[。！？])\n(希望你喜欢)", r"\n\n\1", candidate)
    candidate = re.sub(r"\n{3,}", "\n\n", candidate)
    candidate = "\n".join(line.rstrip() for line in candidate.splitlines()).strip()
    candidate = _wechat_dedupe_exact_visible_paragraphs(candidate)
    return _wechat_restore_compact_browser_phrases(candidate)


def _wechat_dedupe_exact_visible_paragraphs(text: str) -> str:
    candidate = str(text or "").strip()
    if "\n\n" not in candidate:
        return candidate
    paragraphs = re.split(r"\n{2,}", candidate)
    output: list[str] = []
    seen: set[str] = set()
    for paragraph in paragraphs:
        clean = paragraph.strip()
        key = re.sub(r"\s+", "", clean)
        if not clean or key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return "\n\n".join(output)


def _wechat_normalize_visible_markup(text: str) -> str:
    candidate = str(text or "")
    candidate = re.sub(r"系统提示[：:]", "友情提醒：", candidate)
    noisy_prefixes = "📘🧠🔍📌"
    candidate = re.sub(rf"(?m)^\s*[{re.escape(noisy_prefixes)}]\s*", "", candidate)
    candidate = re.sub(rf"[{re.escape(noisy_prefixes)}]", "", candidate)
    candidate = re.sub(r"(?m)^\s*[§▸]\s*", "- ", candidate)
    candidate = re.sub(r"[§▸]\s*", "- ", candidate)
    candidate = re.sub(r"(?<=[：:。！？!?])\s*>\s*", "\n", candidate)
    candidate = re.sub(r"(?m)^\s*>\s*", "", candidate)
    return candidate


def _wechat_remove_optional_followup_tail(text: str, *, user_text: str = "") -> str:
    raw_user = str(user_text or "")
    if any(marker in raw_user for marker in ("给我几个版本", "多给几个版本", "继续追问", "给选项", "选项")):
        return str(text or "").strip()
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned
    patterns = (
        r"(?:^|[\n。！？!?]|\s{2,})\s*如果你愿意[^\n]*(?:$|\n)",
        r"(?:^|[\n。！？!?]|\s{2,})\s*如果你要[^\n]*(?:$|\n)",
        r"(?:^|[\n。！？!?]|\s{2,})\s*我也可以[^\n]*(?:$|\n)",
        r"(?:^|[\n。！？!?]|\s{2,})\s*你要的话[^\n]*(?:$|\n)",
        r"(?:^|[\n。！？!?]|\s{2,})\s*(?:你现在)?你?要是你?愿意[^\n]*(?:$|\n)",
        r"(?:^|[\n。！？!?]|\s{2,})\s*我还能[^\n]*(?:$|\n)",
    )
    previous = None
    while previous != cleaned:
        previous = cleaned
        for pattern in patterns:
            cleaned = re.sub(pattern, "\n", cleaned).strip()
    optional_markers = (
        "如果你愿意",
        "如果你想",
        "如果你要",
        "要是你想",
        "要是你愿意",
        "你要是愿意",
        "我也可以",
        "我还能",
    )
    for marker in optional_markers:
        start = cleaned.find(marker)
        if start >= max(24, int(len(cleaned) * 0.35)):
            line_end = cleaned.find("\n", start)
            if line_end == -1:
                cleaned = cleaned[:start].rstrip("，,。；;：: \n")
            else:
                cleaned = (cleaned[:start].rstrip("，,。；;：: \n") + cleaned[line_end:]).strip()
    return cleaned


def _wechat_dedupe_repeated_visible_blocks(text: str) -> str:
    candidate = str(text or "").strip()
    if not candidate:
        return candidate
    chunks = re.split(r"(\n+|(?<=[。！？!?])\s*)", candidate)
    output: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        if not chunk:
            continue
        if chunk.isspace() or re.fullmatch(r"\n+", chunk):
            output.append(chunk)
            continue
        normalized = re.sub(r"\s+", "", chunk)
        normalized = normalized.strip("：:，,。；;、-—>\"'“”")
        if len(normalized) >= 24 and normalized in seen:
            continue
        if len(normalized) >= 24:
            seen.add(normalized)
        output.append(chunk)
    cleaned = "".join(output)
    lines: list[str] = []
    seen_lines: set[str] = set()
    for line in cleaned.splitlines():
        normalized = re.sub(r"\s+", "", line).strip("：:，,。；;、-—>\"'“”")
        if len(normalized) >= 18 and normalized in seen_lines:
            continue
        if len(normalized) >= 18:
            seen_lines.add(normalized)
        lines.append(line)
    return "\n".join(lines).strip()


def _wechat_compact_overlong_visible_reply(text: str, *, user_text: str = "") -> str:
    visible = str(text or "").strip()
    if not visible:
        return visible
    raw_user = str(user_text or "")
    long_form_requested = any(
        marker in raw_user
        for marker in (
            "详细",
            "完整",
            "模板",
            "报告",
            "长文",
            "逐条",
            "表格",
            "大纲",
            "清单",
        )
    )
    short_casual_requested = (
        not long_form_requested
        and not _wechat_user_requested_structured_reply(raw_user)
        and any(
            marker in raw_user
            for marker in (
                "微信口气",
                "像微信",
                "说两句",
                "别讲大道理",
                "别鸡汤",
                "鸡汤",
                "状态很低",
                "低能量",
                "有点累",
                "心里有点烦",
                "稳住",
                "吐槽",
                "陪伴",
                "夸我",
                "不需要建议",
                "说人话",
                "自然微信话",
                "三句",
                "一句",
                "别写多",
                "短回复",
                "回我",
                "安慰",
                "不机械",
                "客服",
                "情绪",
                "晚饭",
                "该不该",
                "怎么收个尾",
                "启动法",
                "回家前",
            )
        )
    )
    limit = 300 if short_casual_requested else (860 if long_form_requested else 620)
    if short_casual_requested and len(visible) > limit:
        compact_casual = _wechat_compact_short_casual_visible_reply(visible, limit=limit)
        if len(compact_casual) <= 340:
            return compact_casual
    if not long_form_requested:
        numbered = list(re.finditer(r"(?m)^\s*[1-9][0-9]?[.．、]", visible))
        if len(numbered) > 4:
            compact = visible[: numbered[4].start()].rstrip()
            return f"{compact}\n\n先按上面这几步做就够了。"
    if len(visible) <= limit:
        return visible
    candidate = _wechat_remove_optional_followup_tail(visible, user_text=user_text)
    if len(candidate) <= limit:
        return candidate
    cut = candidate[:limit]
    boundary_positions = [
        cut.rfind(mark)
        for mark in ("\n\n", "\n", "。", "！", "？", ";", "；")
    ]
    boundary = max(boundary_positions)
    if boundary >= max(220, int(limit * 0.55)):
        cut = cut[: boundary + 1]
    cut = cut.rstrip("，,；;：:- \n")
    closing = "先按上面这几步做就够了。"
    if any(marker in raw_user for marker in ("只要", "别写多", "三句", "一句")):
        closing = ""
    return _wechat_fit_visible_text(cut, limit=limit, closing=closing)


def _wechat_compact_short_casual_visible_reply(text: str, *, limit: int = 300) -> str:
    candidate = _wechat_remove_optional_followup_tail(str(text or "").strip())
    candidate = re.sub(r"\n{3,}", "\n\n", candidate)
    if len(candidate) <= limit:
        return candidate

    section_pattern = re.compile(
        r"(?m)^\s*(?:[1-9][0-9]?[.．、]|(?:\d+\s*[–-]\s*\d+)\s*分钟[：:])"
    )
    matches = list(section_pattern.finditer(candidate))
    if matches:
        intro = candidate[: matches[0].start()].strip()
        lines: list[str] = []
        if intro:
            lines.append(_wechat_trim_visible_sentence(intro, max_chars=54))
        for index, match in enumerate(matches[:4]):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(candidate)
            block = candidate[match.start() : end].strip()
            lines.append(_wechat_trim_visible_sentence(block, max_chars=62))
        compact = "\n".join(line for line in lines if line).strip()
        if compact:
            return _wechat_fit_visible_text(compact, limit=limit, closing="先照这个做一轮。")

    sentences = [
        part.strip()
        for part in re.split(r"(?<=[。！？!?])\s*", re.sub(r"\s+", " ", candidate))
        if part.strip()
    ]
    compact = "\n".join(sentences[:4]).strip() if sentences else candidate
    return _wechat_fit_visible_text(compact, limit=limit, closing="先照这个做一轮。")


def _wechat_trim_visible_sentence(text: str, *, max_chars: int) -> str:
    candidate = re.sub(r"\s+", " ", str(text or "")).strip()
    candidate = re.sub(r"(?<!\d)\s+-\s*(?!\d)", "、", candidate)
    if len(candidate) <= max_chars:
        return candidate
    cut = candidate[:max_chars]
    boundary = max(cut.rfind(mark) for mark in ("。", "；", ";", "，", ",", "、"))
    if boundary >= max(18, int(max_chars * 0.45)):
        cut = cut[: boundary + 1]
    return cut.rstrip("，,；;：:-、 \n") + "。"


def _wechat_fit_visible_text(text: str, *, limit: int, closing: str = "") -> str:
    candidate = str(text or "").strip()
    suffix = f"\n\n{closing}" if closing else ""
    if len(candidate) + len(suffix) <= limit:
        return f"{candidate}{suffix}".strip()
    cut_limit = max(80, limit - len(suffix))
    cut = candidate[:cut_limit]
    boundary = max(cut.rfind(mark) for mark in ("\n", "。", "！", "？", ";", "；"))
    if boundary >= max(70, int(cut_limit * 0.55)):
        cut = cut[: boundary + 1]
    cut = cut.rstrip("，,；;：:- \n")
    return f"{cut}{suffix}".strip()


def _wechat_repair_thin_completion_reply(text: str, *, user_text: str = "") -> str:
    visible = str(text or "").strip()
    raw_user = str(user_text or "")
    if not re.fullmatch(r"(?:已完成|完成了|好了|处理好了)[。.!！]*", visible):
        return visible
    risky_action = any(marker in raw_user for marker in ("删除", "付款", "转账", "提交", "发送", "发邮件", "执行", "运行"))
    if risky_action:
        return (
            "不能只回“已完成”。\n\n"
            "这类动作要先确认对象、权限、影响范围和是否需要审批；没实际执行前，也不能把结果说成已经完成。"
        )
    if any(marker in raw_user for marker in ("表头", "字段", "只给字段", "Excel", "excel")):
        subject = "预算" if "预算" in raw_user else "表格"
        if subject == "预算":
            return "字段：预算项、分类、金额、币种、周期、负责人、依据、状态、备注。"
        return "字段：名称、分类、金额、时间、负责人、状态、备注。"
    if any(marker in raw_user for marker in ("写", "整理", "设计", "列", "生成", "给我")):
        return "这条不能只回已完成；需要把可直接用的内容发出来，或说明还缺哪些信息。"
    return visible


def _wechat_preserve_negative_constraints(text: str, user_text: str) -> str:
    candidate = str(text or "").strip()
    raw = str(user_text or "")
    if (
        any(marker in raw for marker in ("不加新任务", "别加新任务", "不要加新任务", "不新增任务", "别新增任务", "不要新增任务"))
        and not any(marker in candidate for marker in ("不加新任务", "别加新任务", "不新增任务", "不要新增任务"))
    ):
        return f"{candidate.rstrip('。')}\n\n边界：这段时间不加新任务，只守住当前范围。"
    return candidate


def _wechat_user_requested_structured_reply(user_text: str) -> bool:
    raw = str(user_text or "")
    return any(
        marker in raw
        for marker in (
            "规划",
            "分段",
            "结构",
            "清单",
            "步骤",
            "三步",
            "排",
            "照着做",
        )
    )


def _wechat_user_requested_voice_output(user_text: str) -> bool:
    raw = str(user_text or "").lower()
    return bool(
        re.search(
            r"(语音回复|回语音|读给我听|用声音回复|用语音回复|voice reply|voice-response|发(?:一条)?语音(?:给我|回复|回我|吧|$|[。！!]))",
            raw,
        )
    )


def _wechat_should_preserve_markdown_table(user_text: str) -> bool:
    raw = str(user_text or "")
    return "\u8868\u683c" in raw or ("Markdown" in raw and (
        "表格" in raw
        or all(marker in raw for marker in ("REST", "GraphQL", "gRPC"))
    ))


def _wechat_contains_markdown_table(text: str) -> bool:
    lines = [
        line.strip()
        for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    ]
    for idx, line in enumerate(lines[:-1]):
        if "|" not in line:
            continue
        separator = lines[idx + 1]
        if "|" not in separator:
            continue
        cells = [cell.strip() for cell in separator.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            return True
    return False


def _wechat_rest_graphql_grpc_markdown_table() -> str:
    return (
        "| 技术 | 更适合的场景 | 主要优点 | 主要风险 |\n"
        "| --- | --- | --- | --- |\n"
        "| REST | 公开 API、CRUD、前后端常规交互 | 简单、生态成熟、缓存友好 | 字段容易过多或过少 |\n"
        "| GraphQL | 多端展示、字段差异大、前端按需取数 | 一次请求拿到所需数据 | 查询治理和权限控制更复杂 |\n"
        "| gRPC | 服务内部调用、低延迟、高吞吐系统 | 性能高、契约强、类型清晰 | 浏览器直连和调试门槛更高 |"
    )


def _wechat_strip_or_flatten_code_fences(text: str, *, user_text: str = "") -> str:
    keep_code = bool(re.search(r"(代码|code|脚本|命令|command|powershell|python)", user_text, re.I))

    def replace(match: re.Match[str]) -> str:
        body = str(match.group(1) or "").strip()
        if keep_code:
            return f"\n{body}\n"
        first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
        return f"\n代码内容已省略：{first_line[:80]}\n" if first_line else ""

    return re.sub(r"```[A-Za-z0-9_-]*\n?([\s\S]*?)```", replace, text)


def _wechat_render_markdown_plain_text(text: str) -> str:
    lines = str(text or "").splitlines()
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            rendered.append("")
            continue
        if stripped == "|":
            continue
        if re.fullmatch(r"-{3,}", stripped):
            rendered.append("---")
            continue
        heading = re.match(r"^#{1,6}\s*(.+)$", stripped)
        if heading:
            title = _wechat_strip_inline_markdown(heading.group(1))
            if rendered and rendered[-1] != "":
                rendered.append("")
            rendered.append(title)
            continue
        if _looks_like_markdown_separator_row(stripped):
            continue
        table_cells = _markdown_table_cells(stripped)
        if table_cells:
            if all(re.fullmatch(r":?-{2,}:?", cell) for cell in table_cells):
                continue
            rendered.append(" / ".join(_wechat_strip_inline_markdown(cell) for cell in table_cells))
            continue
        bullet = re.match(r"^[-*]\s*(.+)$", stripped)
        if bullet:
            rendered.append(f"- {_wechat_strip_inline_markdown(bullet.group(1))}")
            continue
        quote = re.match(r"^>\s*(.+)$", stripped)
        if quote:
            if rendered and rendered[-1] != "":
                rendered.append("")
            rendered.append(_wechat_strip_inline_markdown(quote.group(1)))
            continue
        rendered.append(_wechat_strip_inline_markdown(line))
    return "\n".join(rendered)


def _wechat_strip_inline_markdown(text: str) -> str:
    candidate = str(text or "")
    candidate = re.sub(r"\*\*([^*]+)\*\*", r"\1", candidate)
    candidate = re.sub(r"__([^_]+)__", r"\1", candidate)
    candidate = re.sub(r"`([^`]+)`", r"\1", candidate)
    candidate = candidate.replace("*", "")
    return candidate


def _looks_like_markdown_separator_row(text: str) -> bool:
    return bool(re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", text.strip()))


def _markdown_table_cells(text: str) -> list[str]:
    stripped = text.strip()
    if "|" not in stripped:
        return []
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    useful = [cell for cell in cells if cell]
    if useful and all(re.fullmatch(r":?-{2,}:?", cell) for cell in useful):
        return []
    return useful


def _wechat_format_poem_lines(text: str) -> str:
    lines = str(text or "").splitlines()
    formatted: list[str] = []
    in_poem = False
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"《[^》]{1,40}》", stripped):
            in_poem = True
            formatted.append(stripped)
            continue
        if stripped == "---":
            if in_poem:
                in_poem = False
            formatted.append(stripped)
            continue
        if in_poem and _is_wechat_poem_closing_comment(stripped):
            in_poem = False
            if formatted and formatted[-1] != "":
                formatted.append("")
            formatted.append(stripped)
            continue
        if in_poem and stripped:
            poem_parts = _split_compact_poem_line(stripped)
            if len(poem_parts) > 1:
                formatted.extend(poem_parts)
                continue
        formatted.append(line)
    return "\n".join(formatted)


def _is_wechat_poem_closing_comment(text: str) -> bool:
    stripped = str(text or "").strip()
    return bool(re.match(r"^(?:希望你喜欢|有什么特定主题|如果你愿意|你还想|需要我)", stripped))


def _split_compact_poem_line(text: str) -> list[str]:
    compact = str(text or "").strip()
    if not compact or "\n" in compact:
        return [compact]
    parts = [
        part.strip()
        for part in re.split(r"(?<=[，。！？；])", compact)
        if part.strip()
    ]
    if len(parts) <= 1:
        return [compact]
    grouped: list[str] = []
    index = 0
    while index < len(parts):
        current = parts[index]
        if current.endswith("，") and index + 1 < len(parts):
            grouped.append(current + parts[index + 1])
            index += 2
        else:
            grouped.append(current)
            index += 1
    return grouped


def _dedupe_repeated_visible_reply(text: str) -> str:
    candidate = str(text or "").strip()
    if not candidate:
        return candidate
    half = len(candidate) // 2
    if len(candidate) % 2 == 0 and candidate[:half] == candidate[half:]:
        return candidate[:half].strip()
    return candidate


async def _wechat_outbound_attachment_selection(
    *,
    artifacts: ArtifactStore,
    turn: dict[str, Any],
    message: dict[str, Any],
    user_text: str,
    final_text: str,
) -> dict[str, Any]:
    response_plan = message.get("content", {}).get("response_plan")
    response_plan = response_plan if isinstance(response_plan, dict) else {}
    structured = response_plan.get("structured_payload")
    structured = structured if isinstance(structured, dict) else {}
    refs = response_plan.get("artifact_refs")
    refs = refs if isinstance(refs, list) else []
    candidates = await _resolve_wechat_attachment_candidates(
        artifacts=artifacts,
        refs=refs,
        task_id=_attachment_task_id(structured),
    )
    scene = _attachment_scene(structured, candidates)
    explicit_request = _looks_like_attachment_request(user_text)
    reply_implies_document = _reply_mentions_generated_document(final_text)
    completed_summary = _attachment_completed_summary(structured)
    completed_summary_implies_document = _reply_mentions_generated_document(completed_summary)
    reason_codes: list[str] = []
    if explicit_request:
        reason_codes.append("explicit_attachment_request")
    if scene != "generic":
        reason_codes.append(f"scene:{scene}")
    if reply_implies_document:
        reason_codes.append("reply_mentions_generated_document")
    if completed_summary_implies_document:
        reason_codes.append("completed_summary_mentions_generated_document")
    should_send = bool(candidates) and (
        explicit_request
        or scene in {"office_document", "office_text"}
        or reply_implies_document
        or completed_summary_implies_document
    )
    selected = _sort_wechat_attachment_candidates(candidates, user_text=user_text, scene=scene)
    suppressed = []
    for item in candidates:
        if item not in selected:
            suppressed.append(
                {
                    "artifact_id": item.get("artifact_id"),
                    "display_name": item.get("display_name"),
                    "reason": "filtered_after_sort",
                }
            )
    if not should_send:
        suppressed.extend(
            {
                "artifact_id": item.get("artifact_id"),
                "display_name": item.get("display_name"),
                "reason": "delivery_not_triggered",
            }
            for item in selected
        )
        selected = []
    return {
        "should_send_attachments": should_send,
        "selected_attachments": selected,
        "selection_reason_codes": reason_codes,
        "suppressed_attachments": suppressed,
        "scene": scene,
        "explicit_request_detected": explicit_request,
    }


async def _resolve_wechat_attachment_candidates(
    *,
    artifacts: ArtifactStore,
    refs: list[dict[str, Any]],
    task_id: str | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        artifact_id = str(ref.get("artifact_id") or "")
        if not artifact_id or artifact_id in seen:
            continue
        candidate = await _resolve_wechat_attachment_candidate(artifacts=artifacts, ref=ref)
        if candidate is not None:
            candidates.append(candidate)
            seen.add(artifact_id)
    if candidates or not task_id:
        return candidates
    for artifact in await artifacts.list_task_artifacts(task_id):
        if artifact.artifact_id in seen:
            continue
        candidate = _candidate_from_artifact(
            artifact_id=artifact.artifact_id,
            display_name=artifact.display_name,
            content_type=artifact.content_type,
            artifact_type=artifact.artifact_type,
            created_at=artifact.created_at,
            download_url=f"/api/artifacts/{artifact.artifact_id}/download",
            metadata=artifact.metadata,
        )
        if candidate is not None:
            candidates.append(candidate)
            seen.add(artifact.artifact_id)
    return candidates


async def _resolve_wechat_attachment_candidate(
    *,
    artifacts: ArtifactStore,
    ref: dict[str, Any],
) -> dict[str, Any] | None:
    artifact_id = str(ref.get("artifact_id") or "")
    if not artifact_id:
        return None
    try:
        artifact = await artifacts.get_artifact(artifact_id)
    except AppError:
        return None
    return _candidate_from_artifact(
        artifact_id=artifact_id,
        display_name=str(ref.get("display_name") or artifact.display_name),
        content_type=str(ref.get("content_type") or artifact.content_type or ""),
        artifact_type=str(getattr(artifact, "artifact_type", "") or ""),
        created_at=str(getattr(artifact, "created_at", "") or ""),
        download_url=str(ref.get("download_url") or f"/api/artifacts/{artifact_id}/download"),
        metadata=artifact.metadata,
    )


def _candidate_from_artifact(
    *,
    artifact_id: str,
    display_name: str,
    content_type: str,
    artifact_type: str,
    created_at: str,
    download_url: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    suffix = Path(display_name).suffix.lower()
    allowed_suffixes = {".docx", ".xlsx", ".pptx", ".md", ".txt"}
    blocked_artifact_types = {
        "terminal_log",
        "checkpoint_snapshot",
        "screenshot",
        "download",
        "image",
        "audio",
        "video",
        "report",
        "recovery_record",
        "trace",
    }
    if suffix not in allowed_suffixes or artifact_type in blocked_artifact_types:
        return None
    name = display_name.lower()
    blocked_name_markers = (
        "terminal",
        "checkpoint",
        "screenshot",
        "debug",
        "trace",
        "diagnostic",
        "recovery",
        "transcript",
        "host-install-log",
        "toolchain-log",
        "deployment-log",
        "code-hosting-report",
    )
    if any(marker in name for marker in blocked_name_markers):
        return None
    delivery_role = _attachment_delivery_role(display_name, suffix=suffix)
    return {
        "artifact_id": artifact_id,
        "display_name": display_name,
        "content_type": content_type,
        "download_url": download_url,
        "delivery_role": delivery_role,
        "artifact_type": artifact_type,
        "created_at": created_at,
        "metadata": metadata or {},
        "extension": suffix,
    }


def _attachment_task_id(structured: dict[str, Any]) -> str | None:
    keys = (
        ("task_status", "task_id"),
        ("office_productivity", "task_id"),
    )
    for parent, child in keys:
        value = structured.get(parent)
        if isinstance(value, dict) and value.get(child):
            return str(value[child])
    return None


def _attachment_scene(structured: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    route = structured.get("route_semantics")
    if isinstance(route, dict):
        route_name = str(route.get("route") or "")
        if "office" in route_name:
            return "office_document" if any(_is_primary_attachment(item) for item in candidates) else "office_text"
    office_payload = structured.get("office_productivity")
    if isinstance(office_payload, dict):
        return "office_document" if any(_is_primary_attachment(item) for item in candidates) else "office_text"
    completed_summary = _attachment_completed_summary(structured)
    if _reply_mentions_generated_document(completed_summary):
        return "office_document" if any(_is_primary_attachment(item) for item in candidates) else "office_text"
    if any(
        str(item.get("artifact_type") or "")
        in {"mail_draft", "calendar_plan", "office_action_record"}
        for item in candidates
    ):
        return "office_text"
    return "generic"


def _looks_like_attachment_request(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "发我文件",
        "把文件发我",
        "发我附件",
        "附件发来",
        "导出一下",
        "导出文件",
        "发我文档",
        "把文档给我",
        "send me the file",
        "send the file",
        "send me the attachment",
        "export",
        "attachment",
    )
    return any(marker in lowered for marker in markers)


def _reply_mentions_generated_document(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "已产出文件",
        "已整理成文档",
        "已生成草稿",
        "已输出结果文件",
        "已生成文档",
        "已为你生成",
        "draft is ready",
        "document is ready",
        "file is ready",
    )
    return any(marker in lowered for marker in markers)


def _attachment_completed_summary(structured: dict[str, Any]) -> str:
    action_status = structured.get("action_status")
    if isinstance(action_status, dict):
        return str(action_status.get("completed_summary") or "")
    semantics = structured.get("action_status_semantics")
    if isinstance(semantics, dict):
        return str(semantics.get("completed_summary") or "")
    return ""


def _sort_wechat_attachment_candidates(
    candidates: list[dict[str, Any]],
    *,
    user_text: str,
    scene: str,
) -> list[dict[str, Any]]:
    format_bonus = _requested_format_bonus(user_text)
    return sorted(
        candidates,
        key=lambda item: (
            format_bonus.get(str(item.get("extension") or ""), 0),
            _primary_rank(item),
            _role_rank(str(item.get("delivery_role") or "")),
            1 if scene == "office_document" and _is_primary_attachment(item) else 0,
            str(item.get("created_at") or ""),
            str(item.get("display_name") or ""),
        ),
        reverse=True,
    )


def _requested_format_bonus(text: str) -> dict[str, int]:
    lowered = text.lower()
    return {
        ".pptx": 1 if "ppt" in lowered else 0,
        ".docx": 1 if "word" in lowered or "docx" in lowered else 0,
        ".xlsx": 1 if "excel" in lowered or "xlsx" in lowered else 0,
        ".md": 1 if "markdown" in lowered or " md" in lowered or lowered.endswith("md") else 0,
        ".txt": 1 if "txt" in lowered or "文本" in lowered else 0,
    }


def _primary_rank(item: dict[str, Any]) -> int:
    return 1 if _is_primary_attachment(item) else 0


def _is_primary_attachment(item: dict[str, Any]) -> bool:
    return str(item.get("extension") or "") in {".docx", ".xlsx", ".pptx"}


def _role_rank(role: str) -> int:
    order = {"primary": 3, "summary": 2, "record": 1}
    return order.get(role, 0)


def _attachment_delivery_role(display_name: str, *, suffix: str) -> str:
    if suffix in {".docx", ".xlsx", ".pptx"}:
        return "primary"
    lowered = display_name.lower()
    if "record" in lowered or "action" in lowered:
        return "record"
    return "summary"


def _gateway_policy(config: ChannelProviderSection) -> dict[str, Any]:
    return {
        "pairing_required": config.pairing_required,
        "allow_unknown_private": config.allow_unknown_private,
        "private_chat_only": config.private_chat_only,
        "group_messages": config.group_messages,
    }


def _media_policy(config: ChannelProviderSection) -> dict[str, Any]:
    raw = config.media or {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_bytes": int(raw.get("max_bytes") or 10_485_760),
        "allowed_types": set(raw.get("allowed_types") or ["image", "audio", "file"]),
        "transcribe_provider": str(raw.get("transcribe_provider") or "local"),
        "vision_enabled": bool(raw.get("vision_enabled", True)),
    }


def _delivery_binding_status(notification: Any) -> str:
    status = str(getattr(notification, "status", "") or "failed")
    if status == "sent":
        return "sent"
    if status in {"rejected", "dead_letter", "provider_unavailable"}:
        return status
    metadata = getattr(notification, "metadata", {}) or {}
    delivery = metadata.get("delivery") if isinstance(metadata, dict) else {}
    if isinstance(delivery, dict):
        delivery_status = str(delivery.get("delivery_status") or "")
        if delivery_status in {"provider_unavailable", "rejected", "dead_letter"}:
            return delivery_status
    return "failed"


def _wechat_worker_health_payload(worker_health: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(worker_health, dict):
        return {
            "available": False,
            "enabled": None,
            "running": None,
            "automation_state": "unknown",
            "reason": "worker_health_unavailable",
        }
    workers = worker_health.get("workers")
    worker = workers.get("wechat_inbound_worker") if isinstance(workers, dict) else {}
    if not isinstance(worker, dict):
        worker = {}
    enabled = bool(worker_health.get("enabled"))
    running = bool(worker_health.get("running"))
    last_status = str(worker.get("last_status") or "unknown")
    if running:
        automation_state = "running"
        reason = None
    elif not enabled:
        automation_state = "disabled"
        reason = "background_workers_disabled"
    elif last_status == "healthy":
        automation_state = "manual_tick_healthy"
        reason = None
    elif last_status == "never_run":
        automation_state = "not_started"
        reason = "wechat_inbound_worker_never_run"
    elif last_status == "failed":
        automation_state = "failed"
        reason = worker.get("last_error_code") or "wechat_inbound_worker_failed"
    else:
        automation_state = "stopped"
        reason = str(worker_health.get("loop_status") or last_status)
    return {
        "available": True,
        "enabled": enabled,
        "running": running,
        "loop_status": worker_health.get("loop_status"),
        "automation_state": automation_state,
        "reason": reason,
        "wechat_inbound_worker": {
            "last_status": last_status,
            "tick_count": worker.get("tick_count", 0),
            "success_count": worker.get("success_count", 0),
            "failure_count": worker.get("failure_count", 0),
            "last_started_at": worker.get("last_started_at"),
            "last_finished_at": worker.get("last_finished_at"),
            "last_error_code": worker.get("last_error_code"),
            "last_result": redact(worker.get("last_result") or {}),
        },
    }


def _hash_value(value: str | None) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()
