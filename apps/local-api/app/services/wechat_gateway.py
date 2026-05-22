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
            await self._deliver_binding(binding, trace_id=trace_id)
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
        selection = await _wechat_outbound_attachment_selection(
            artifacts=self._artifacts,
            turn=turn,
            message=message,
            user_text=str(user_message.get("content_text") or "") if user_message else "",
            final_text=self._stream_bridge.final_plain_text(message),
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
        notification = await self._notifications.create_message(
            # Final visible text is always derived from the bridge, not ad hoc gateway copy.
            NotificationMessageCreateRequest(
                channel_id=session["channel_id"],
                message_type="wechat_chat_reply",
                recipient=recipient,
                subject="微信回复",
                body=self._stream_bridge.final_plain_text(message),
                metadata={
                    "channel_delivery_binding_id": binding["channel_delivery_binding_id"],
                    "turn_id": turn_id,
                    "message_id": message_id,
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
                "attempts": int(latest.get("attempts") or 0) + 1,
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
