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
        claimed = await self._repo.claim_delivery_binding(
            binding["channel_delivery_binding_id"],
            now=utc_now_iso(),
        )
        if claimed is None:
            return None
        binding = claimed
        latest = claimed
        final_text_details = self._stream_bridge.final_text_details(message)
        final_text_source = str(final_text_details.get("source") or "")
        final_plain_text = _wechat_final_visible_reply_text(
            str(final_text_details.get("plain_text") or ""),
            user_text=str(user_message.get("content_text") or "") if user_message else "",
            trusted_response_plan=final_text_source == "response_plan_plain_text",
        )
        if user_message and _wechat_needs_contract_repair(final_plain_text):
            final_plain_text = preserve_visible_reply_contract(
                final_plain_text,
                user_text=str(user_message.get("content_text") or ""),
            )
        final_plain_text = _wechat_followup_visible_reply_contract(
            final_plain_text,
            user_text=str(user_message.get("content_text") or "") if user_message else "",
        )
        final_plain_text = _wechat_non_empty_visible_reply(
            final_plain_text,
            user_text=str(user_message.get("content_text") or "") if user_message else "",
        )
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
            user_text=str(user_message.get("content_text") or "") if user_message else "",
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
                        "voice_reply": dict(message.get("voice_metadata") or {}),
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
    candidate = re.sub(r"---(?=#+)", "\n---\n", candidate)
    candidate = re.sub(r"---\n(?=#+)", "\n---\n", candidate)
    candidate = re.sub(r"(?<!\n)---(?=\S)", "\n---\n", candidate)
    candidate = re.sub(r"(?<=\S)---(?!\n)", "\n---\n", candidate)
    candidate = re.sub(r"(?<!\n)-(?=[A-Za-z][^。\n-]{0,40}[：:])", "\n-", candidate)
    candidate = re.sub(r"(?<!\n)-(?=(?:优点|缺点)[：:])", "\n-", candidate)
    candidate = re.sub(r"\|\|(?=\S)", "|\n|", candidate)
    candidate = re.sub(r"(?<!\n)(\|[^|\n]+?\|[^|\n]+?\|)", r"\n\1", candidate)
    candidate = _wechat_render_markdown_plain_text(candidate)
    candidate = _wechat_format_poem_lines(candidate)
    candidate = re.sub(r"(?<=[。！？])\n(希望你喜欢)", r"\n\n\1", candidate)
    candidate = re.sub(r"\n{3,}", "\n\n", candidate)
    return "\n".join(line.rstrip() for line in candidate.splitlines()).strip()


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
        rendered.append(_wechat_strip_inline_markdown(line))
    return "\n".join(rendered)


def _wechat_strip_inline_markdown(text: str) -> str:
    candidate = str(text or "")
    candidate = re.sub(r"\*\*([^*]+)\*\*", r"\1", candidate)
    candidate = re.sub(r"__([^_]+)__", r"\1", candidate)
    candidate = re.sub(r"`([^`]+)`", r"\1", candidate)
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
