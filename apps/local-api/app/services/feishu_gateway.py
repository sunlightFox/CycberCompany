from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core_types import (
    ChatIngressMetadata,
    ChatInput,
    ChatTurnRequest,
    ClientContext,
    ErrorCode,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.config import ChannelProviderSection
from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.channel_repo import ChannelRepository
from app.db.repositories.chat_repo import ChatRepository
from app.schemas.channels import (
    FeishuGatewayHealthResponse,
    FeishuGatewayPollResponse,
)
from app.schemas.notifications import NotificationMessageCreateRequest
from app.services.audit import AuditEventService
from app.services.channel_connectors import ChannelConnectorRegistry
from app.services.chat import ChatService
from app.services.notifications import NotificationGatewayService
from app.services.secrets import SecretStore


@dataclass
class FeishuGatewayStats:
    processed_accounts: int = 0
    processed_events: int = 0
    created_pairing_requests: int = 0
    chat_turns_created: int = 0
    deliveries_sent: int = 0
    rejected_events: int = 0
    duplicate_events: int = 0
    media_attachments: int = 0
    failures: int = 0
    operations_recorded: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    def response(self) -> FeishuGatewayPollResponse:
        return FeishuGatewayPollResponse(
            status="healthy" if self.failures == 0 else "degraded",
            **self.__dict__,
        )


class FeishuChannelGatewayService:
    def __init__(
        self,
        *,
        repo: ChannelRepository,
        chat_repo: ChatRepository,
        chat_service: ChatService,
        notifications: NotificationGatewayService,
        connectors: ChannelConnectorRegistry,
        secret_store: SecretStore,
        data_dir: Path,
        trace_service: TraceService,
        audit_service: AuditEventService,
        config: ChannelProviderSection,
    ) -> None:
        self._repo = repo
        self._chat_repo = chat_repo
        self._chat = chat_service
        self._notifications = notifications
        self._connectors = connectors
        self._secrets = secret_store
        self._blob_dir = data_dir / "channel-attachments" / "feishu"
        self._trace = trace_service
        self._audit = audit_service
        self._config = config
        self._last_poll_result: dict[str, Any] = {}
        self._channel_ingress_runtime: Any | None = None

    def set_channel_ingress_runtime(self, runtime: Any) -> None:
        self._channel_ingress_runtime = runtime

    async def poll_once(
        self,
        *,
        trace_id: str | None = None,
        limit: int | None = None,
    ) -> FeishuGatewayPollResponse:
        stats = FeishuGatewayStats()
        if not self._config.enabled or not self._config.poll_enabled:
            stats.details = {
                "reason": "feishu_gateway_disabled",
                "enabled": self._config.enabled,
                "poll_enabled": self._config.poll_enabled,
                "transport_mode": "websocket",
            }
            self._last_poll_result = stats.response().model_dump(mode="json")
            return stats.response()
        accounts = await self._repo.list_accounts(provider="feishu", status="active", limit=50)
        batch_limit = int(limit or self._config.poll_batch_size or 20)
        connector = self._connectors.get("feishu")
        for account in accounts:
            stats.processed_accounts += 1
            provider_state = self._load_provider_state(account.get("provider_state_ref"))
            await self._upsert_connection(account, provider_state, trace_id=trace_id)
            try:
                events = await connector.poll_events(provider_state=provider_state, limit=batch_limit)
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

    async def receive_event(
        self,
        *,
        event: dict[str, Any],
        channel_account_id: str | None = None,
        trace_id: str | None = None,
    ) -> FeishuGatewayPollResponse:
        stats = FeishuGatewayStats()
        account = await self._resolve_account(channel_account_id)
        if account is None:
            raise AppError(ErrorCode.NOT_FOUND, "飞书渠道账号不存在", status_code=404)
        await self._handle_event(
            account,
            provider_state=self._load_provider_state(account.get("provider_state_ref")),
            event=event,
            stats=stats,
            trace_id=trace_id,
        )
        self._last_poll_result = stats.response().model_dump(mode="json")
        return stats.response()

    async def deliver_due(
        self,
        *,
        trace_id: str | None = None,
        limit: int = 20,
    ) -> FeishuGatewayPollResponse:
        stats = FeishuGatewayStats()
        pending = await self._repo.list_delivery_bindings(
            provider="feishu",
            status="pending",
            limit=limit,
        )
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

    async def gateway_health(
        self,
        *,
        worker_health: dict[str, Any] | None = None,
    ) -> FeishuGatewayHealthResponse:
        provider_health = await self._provider_health()
        accounts = await self._repo.list_accounts(provider="feishu", status="active", limit=100)
        connections = await self._repo.list_feishu_connections(limit=100)
        return FeishuGatewayHealthResponse(
            enabled=self._config.enabled,
            poll_enabled=self._config.poll_enabled,
            service_available=provider_health.reachable,
            connected=any(item.get("connection_state") == "connected" for item in connections),
            status=provider_health.login_state,
            login_state=provider_health.login_state,
            connection_state=(
                "connected"
                if any(item.get("connection_state") == "connected" for item in connections)
                else "configured"
                if accounts
                else "disconnected"
            ),
            transport_mode="websocket",
            active_accounts=len(accounts),
            pending_pairing_requests=await self._repo.count_pending_pairing_requests(provider="feishu"),
            pending_deliveries=await self._repo.count_delivery_bindings(provider="feishu", status="pending"),
            connections=connections,
            last_poll_result=self._last_poll_result,
            worker_health=worker_health or {},
            provider_health=provider_health,
        )

    async def message_operation(
        self,
        *,
        channel_account_id: str,
        operation: str,
        message_id: str | None = None,
        emoji_type: str | None = None,
        container_id: str | None = None,
        container_id_type: str = "chat",
        page_size: int = 20,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        account = await self._repo.get_account(channel_account_id)
        if account is None or account.get("provider") != "feishu":
            raise AppError(ErrorCode.NOT_FOUND, "飞书渠道账号不存在", status_code=404)
        connector = self._connectors.get("feishu")
        provider_state = self._load_provider_state(account.get("provider_state_ref"))
        if operation == "recall":
            if not message_id:
                raise AppError(ErrorCode.VALIDATION_ERROR, "message_id required", status_code=422)
            result = await connector.recall_message(provider_state=provider_state, message_id=message_id)  # type: ignore[attr-defined]
        elif operation == "read":
            if not message_id:
                raise AppError(ErrorCode.VALIDATION_ERROR, "message_id required", status_code=422)
            result = await connector.mark_message_read(provider_state=provider_state, message_id=message_id)  # type: ignore[attr-defined]
        elif operation == "reaction":
            if not message_id or not emoji_type:
                raise AppError(ErrorCode.VALIDATION_ERROR, "message_id and emoji_type required", status_code=422)
            result = await connector.add_reaction(provider_state=provider_state, message_id=message_id, emoji_type=emoji_type)  # type: ignore[attr-defined]
        elif operation == "history":
            if not container_id:
                raise AppError(ErrorCode.VALIDATION_ERROR, "container_id required", status_code=422)
            result = await connector.history(
                provider_state=provider_state,
                container_id=container_id,
                container_id_type=container_id_type,
                page_size=page_size,
            )  # type: ignore[attr-defined]
        else:
            raise AppError(ErrorCode.VALIDATION_ERROR, "unsupported feishu operation", status_code=422)
        now = utc_now_iso()
        await self._repo.insert_feishu_message_operation(
            {
                "feishu_operation_id": new_id("fsop"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_id": account.get("channel_id"),
                "provider_message_id_redacted": _hash_value(message_id) if message_id else None,
                "operation": operation,
                "request_summary": {
                    "message_id_redacted": _hash_value(message_id) if message_id else None,
                    "emoji_type": emoji_type,
                    "container_id_redacted": _hash_value(container_id) if container_id else None,
                    "container_id_type": container_id_type,
                    "page_size": page_size,
                },
                "response_summary": result.response_summary,
                "status": result.status,
                "error_code": result.error_code,
                "error_summary": result.error_summary,
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        return {
            "status": result.status,
            "provider_message_id": result.provider_message_id,
            "response_summary": result.response_summary,
            "error_code": result.error_code,
            "error_summary": result.error_summary,
        }

    async def _handle_event(
        self,
        account: dict[str, Any],
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        stats: FeishuGatewayStats,
        trace_id: str | None,
    ) -> None:
        del provider_state
        normalized = _normalize_feishu_event(event)
        provider_event_ref = _hash_value(normalized["provider_event_id"])
        peer_hash = _hash_value(normalized["peer_ref"])
        now = utc_now_iso()
        inserted = await self._repo.insert_event_offset(
            {
                "offset_id": new_id("choff"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "provider": "feishu",
                "provider_event_id_redacted": provider_event_ref,
                "status": "processing",
                "received_at": normalized["received_at"] or now,
                "created_at": now,
                "updated_at": now,
            }
        )
        if not inserted:
            stats.duplicate_events += 1
            return
        peer = await self._repo.upsert_peer(
            {
                "channel_peer_id": new_id("chpeer"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "provider": "feishu",
                "peer_ref_redacted": peer_hash,
                "peer_type": normalized["chat_type"],
                "display_name_redacted": str(redact(normalized.get("display_name") or "")) or None,
                "pairing_status": "seen",
                "allow_inbound": False,
                "allow_outbound": False,
                "metadata": {"source": "feishu_gateway", "chat_id_redacted": peer_hash},
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
        elif session is None and self._config.allow_unknown_private:
            session = await self._auto_pair_session(account, peer=peer, normalized=normalized, peer_hash=peer_hash)
        elif not session or not session.get("allow_inbound"):
            status = "pairing_required"
            await self._create_pairing_request(
                account,
                peer=peer,
                normalized=normalized,
                peer_hash=peer_hash,
                stats=stats,
                trace_id=trace_id,
            )
            stats.rejected_events += 1
        channel_event_id = new_id("chevt")
        event_data = {
            "channel_event_id": channel_event_id,
            "organization_id": account["organization_id"],
            "provider": "feishu",
            "channel_account_id": account["channel_account_id"],
            "channel_id": account.get("channel_id"),
            "event_type": f"feishu.{normalized['event_type']}.{normalized['message_type']}",
            "provider_event_id_redacted": provider_event_ref,
            "payload_redacted": {
                "source": {
                    "chat_type": normalized["chat_type"],
                    "peer_ref_redacted": peer_hash,
                    "sender_id_redacted": _hash_value(normalized["sender_id"]),
                },
                "message": {
                    "message_type": normalized["message_type"],
                    "text_hash": _hash_value(normalized["text"]) if normalized["text"] else None,
                    "text_length": len(normalized["text"]),
                    "attachment_count": len(normalized["attachments"]),
                    "mentions": normalized["mentions"],
                    "operation": normalized.get("operation"),
                },
            },
            "normalized_event": {
                "provider": "feishu",
                "chat_type": normalized["chat_type"],
                "peer_ref_redacted": peer_hash,
                "sender_id_redacted": _hash_value(normalized["sender_id"]),
                "content_type": normalized["message_type"],
                "content_hash": _hash_value(normalized["text"]) if normalized["text"] else None,
                "content_length": len(normalized["text"]),
                "trusted_channel": status == "received",
                "untrusted_external_content": status != "received",
                "provider_received_at": normalized["received_at"],
                "message_id_redacted": _hash_value(normalized["message_id"]) if normalized["message_id"] else None,
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
        await self._repo.insert_feishu_event_record(
            {
                "feishu_event_record_id": new_id("fsevt"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_event_id": channel_event_id,
                "provider_event_id_redacted": provider_event_ref,
                "event_type": normalized["event_type"],
                "message_type": normalized["message_type"],
                "chat_id_redacted": peer_hash,
                "sender_id_redacted": _hash_value(normalized["sender_id"]),
                "message_id_redacted": _hash_value(normalized["message_id"]) if normalized["message_id"] else None,
                "payload_redacted": event_data["payload_redacted"],
                "normalized_event": event_data["normalized_event"],
                "status": status,
                "trace_id": trace_id,
                "received_at": normalized["received_at"] or now,
                "created_at": now,
                "updated_at": now,
            }
        )
        stats.processed_events += 1
        if status != "received" or session is None:
            return
        await self._route_to_chat(
            account,
            session=session,
            channel_event_id=channel_event_id,
            normalized=normalized,
            stats=stats,
            trace_id=trace_id,
        )

    async def _route_to_chat(
        self,
        account: dict[str, Any],
        *,
        session: dict[str, Any],
        channel_event_id: str,
        normalized: dict[str, Any],
        stats: FeishuGatewayStats,
        trace_id: str | None,
    ) -> None:
        text = normalized["text"].strip()
        if not text:
            text = f"收到一条飞书{normalized['message_type']}消息。"
        span_id = None
        if trace_id:
            span_id = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.CHAT_INGRESS,
                name="feishu route to chat",
                input_data={
                    "channel_event_id": channel_event_id,
                    "message_type": normalized["message_type"],
                    "text_length": len(text),
                    "attachment_count": len(normalized["attachments"]),
                },
            )
        try:
            raw_payload = {
                "provider": "feishu",
                "channel_event_id": channel_event_id,
                "channel_account_id": account["channel_account_id"],
                "channel_peer_session_id": session["channel_peer_session_id"],
                "message_type": normalized["message_type"],
                "message_id_redacted": _hash_value(normalized["message_id"]) if normalized["message_id"] else None,
                "mentions": normalized["mentions"],
                "attachment_count": len(normalized["attachments"]),
            }
            if self._channel_ingress_runtime is not None and not normalized["attachments"]:
                response = await self._channel_ingress_runtime.submit_channel_turn(
                    provider="feishu",
                    session=session,
                    channel_message_id=normalized["provider_event_id"],
                    text=text,
                    raw_payload=raw_payload,
                    ui_mode="feishu_chat",
                )
            else:
                response = await self._chat.create_turn(
                    ChatTurnRequest(
                        session_id=session["session_id"],
                        conversation_id=session.get("conversation_id"),
                        member_id=session["member_id"],
                        input=ChatInput(type="text", text=text),
                        ingress_metadata=ChatIngressMetadata(
                            channel="feishu",
                            channel_message_id=normalized["provider_event_id"],
                            queue_policy="immediate",
                            raw_payload=raw_payload,
                        ),
                        client_context=ClientContext(
                            timezone="Asia/Shanghai",
                            locale="zh-CN",
                            ui_mode="feishu_chat",
                        ),
                    )
                )
        except Exception as exc:
            if span_id:
                await self._trace.end_span(
                    span_id,
                    status=TraceSpanStatus.FAILED,
                    output_data={"error": str(redact(str(exc)))},
                )
            raise
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={
                    "turn_id": response.turn_id,
                    "conversation_id": response.conversation_id,
                },
            )
        await self._repo.update_peer_session(
            session["channel_peer_session_id"],
            {
                "conversation_id": response.conversation_id,
                "last_inbound_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            },
        )
        now = utc_now_iso()
        await self._repo.insert_delivery_binding(
            {
                "channel_delivery_binding_id": new_id("chdel"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_peer_session_id": session["channel_peer_session_id"],
                "channel_event_id": channel_event_id,
                "turn_id": response.turn_id,
                "provider": "feishu",
                "status": "pending",
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        stats.chat_turns_created += 1

    async def _deliver_binding(self, binding: dict[str, Any], *, trace_id: str | None) -> bool | None:
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
        if not session or not session.get("channel_id"):
            return None
        recipient = self._peer_ref_from_session(session)
        if not recipient:
            await self._repo.update_delivery_binding(
                binding["channel_delivery_binding_id"],
                {"status": "failed", "failure_reason": "peer_state_missing", "updated_at": utc_now_iso()},
            )
            return False
        notification = await self._notifications.create_message(
            NotificationMessageCreateRequest(
                channel_id=session["channel_id"],
                message_type="feishu_chat_reply",
                recipient=recipient,
                subject="飞书回复",
                body=str(message["content_text"]),
                metadata={
                    "channel_delivery_binding_id": binding["channel_delivery_binding_id"],
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "voice_reply": dict(message.get("voice_metadata") or {}),
                },
            ),
            trace_id=trace_id,
        )
        status = "sent" if notification.status == "sent" else "failed" if notification.status == "failed" else "pending"
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
                "attempts": int(binding.get("attempts") or 0) + 1,
                "failure_reason": notification.failure_reason if status == "failed" else None,
                "updated_at": utc_now_iso(),
                "sent_at": utc_now_iso() if status == "sent" else None,
            },
        )
        return status == "sent"

    async def _create_pairing_request(
        self,
        account: dict[str, Any],
        *,
        peer: dict[str, Any],
        normalized: dict[str, Any],
        peer_hash: str,
        stats: FeishuGatewayStats,
        trace_id: str | None,
    ) -> None:
        existing = await self._repo.pending_pairing_request(
            channel_account_id=account["channel_account_id"],
            peer_ref_redacted=peer_hash,
        )
        if existing is not None:
            return
        peer_state_ref, _ = self._secrets.put_secret(
            json.dumps({"peer_ref": normalized["peer_ref"], "provider": "feishu"}, ensure_ascii=False)
        )
        now = utc_now_iso()
        await self._repo.insert_pairing_request(
            {
                "pairing_request_id": new_id("chpair"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_peer_id": peer.get("channel_peer_id"),
                "provider": "feishu",
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

    async def _auto_pair_session(
        self,
        account: dict[str, Any],
        *,
        peer: dict[str, Any],
        normalized: dict[str, Any],
        peer_hash: str,
    ) -> dict[str, Any]:
        peer_state_ref, _ = self._secrets.put_secret(
            json.dumps({"peer_ref": normalized["peer_ref"], "provider": "feishu"}, ensure_ascii=False)
        )
        now = utc_now_iso()
        return await self._repo.upsert_peer_session(
            {
                "channel_peer_session_id": new_id("chps"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_peer_id": peer.get("channel_peer_id"),
                "channel_id": account.get("channel_id"),
                "provider": "feishu",
                "peer_ref_redacted": peer_hash,
                "peer_type": normalized["chat_type"],
                "session_id": new_id("chsess"),
                "member_id": "mem_xiaoyao",
                "peer_state_ref": peer_state_ref,
                "pairing_status": "paired",
                "allow_inbound": True,
                "allow_outbound": True,
                "policy_snapshot": _gateway_policy(self._config),
                "created_at": now,
                "updated_at": now,
            }
        )

    async def _upsert_connection(
        self,
        account: dict[str, Any],
        provider_state: dict[str, Any] | None,
        *,
        trace_id: str | None,
    ) -> None:
        state = provider_state or {}
        now = utc_now_iso()
        await self._repo.upsert_feishu_connection(
            {
                "feishu_connection_id": new_id("fsconn"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "channel_id": account.get("channel_id"),
                "app_id_redacted": _hash_value(str(state.get("app_id") or account.get("account_ref_redacted") or "")),
                "tenant_key_redacted": _hash_value(str(state.get("tenant_key"))) if state.get("tenant_key") else None,
                "bot_open_id_redacted": _hash_value(str(state.get("bot_open_id"))) if state.get("bot_open_id") else None,
                "transport_mode": "websocket",
                "status": "configured",
                "connection_state": "connected" if self._config.poll_enabled else "configured",
                "permission_snapshot": {"source": "provider_config", "credentials": "redacted"},
                "capability_snapshot": {"capabilities": account.get("capabilities") or []},
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )

    async def _resolve_account(self, channel_account_id: str | None) -> dict[str, Any] | None:
        if channel_account_id:
            return await self._repo.get_account(channel_account_id)
        accounts = await self._repo.list_accounts(provider="feishu", status="active", limit=1)
        return accounts[0] if accounts else None

    async def _provider_health(self):
        health = await self._connectors.get("feishu").health()
        from app.schemas.channels import ChannelProviderHealthResponse

        return ChannelProviderHealthResponse(**health.__dict__)

    def _load_provider_state(self, provider_state_ref: str | None) -> dict[str, Any] | None:
        raw = self._secrets.get_secret(provider_state_ref)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    def _peer_ref_from_session(self, session: dict[str, Any]) -> str | None:
        raw = self._secrets.get_secret(session.get("peer_state_ref"))
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return str(data.get("peer_ref") or "") or None


def _normalize_feishu_event(raw: dict[str, Any]) -> dict[str, Any]:
    event = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    header = raw.get("header") if isinstance(raw.get("header"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else event.get("message_event", {})
    if not isinstance(message, dict):
        message = {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    chat_id = str(message.get("chat_id") or event.get("chat_id") or "unknown_chat")
    sender_ref = str(
        sender_id.get("open_id")
        or sender_id.get("user_id")
        or sender.get("open_id")
        or event.get("sender_id")
        or "unknown_sender"
    )
    content_raw = message.get("content")
    content = _parse_feishu_content(content_raw)
    message_type = str(message.get("message_type") or event.get("message_type") or raw.get("message_type") or "text")
    attachments = _feishu_attachments(message_type, content, message)
    return {
        "raw_event": raw,
        "event_type": str(header.get("event_type") or event.get("event_type") or raw.get("type") or "message"),
        "provider_event_id": str(header.get("event_id") or raw.get("event_id") or message.get("message_id") or new_id("fsevt")),
        "message_id": str(message.get("message_id") or raw.get("message_id") or ""),
        "peer_ref": chat_id,
        "sender_id": sender_ref,
        "chat_type": _feishu_chat_type(str(message.get("chat_type") or event.get("chat_type") or "")),
        "display_name": str(sender.get("sender_type") or sender_ref),
        "message_type": message_type,
        "text": _feishu_text_from_content(message_type, content),
        "attachments": attachments,
        "mentions": _feishu_mentions(message),
        "operation": _feishu_operation(raw),
        "received_at": str(header.get("create_time") or raw.get("received_at") or utc_now_iso()),
    }


def _parse_feishu_content(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {"text": value}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {}


def _feishu_text_from_content(message_type: str, content: dict[str, Any]) -> str:
    if message_type == "text":
        return str(content.get("text") or "")
    if message_type == "post":
        return _flatten_feishu_post(content)
    if "text" in content:
        return str(content.get("text") or "")
    if "title" in content:
        return str(content.get("title") or "")
    return ""


def _flatten_feishu_post(content: dict[str, Any]) -> str:
    post = content.get("post") if isinstance(content.get("post"), dict) else content
    texts: list[str] = []
    for lang_value in post.values():
        if not isinstance(lang_value, dict):
            continue
        for row in lang_value.get("content") or []:
            if isinstance(row, list):
                for item in row:
                    if isinstance(item, dict) and item.get("tag") == "text":
                        texts.append(str(item.get("text") or ""))
    return "".join(texts)


def _feishu_attachments(message_type: str, content: dict[str, Any], message: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = {**content, **message}
    keys = ("image_key", "file_key", "audio_key", "media_id", "file_id")
    if not any(candidates.get(key) for key in keys):
        return []
    return [
        {
            "type": message_type,
            "file_key": candidates.get("file_key") or candidates.get("image_key") or candidates.get("audio_key"),
            "media_id": candidates.get("media_id") or candidates.get("file_id"),
            "name": candidates.get("file_name") or candidates.get("name") or f"feishu-{message_type}",
            "content_type": candidates.get("content_type") or "application/octet-stream",
            "size_bytes": candidates.get("size"),
        }
    ]


def _feishu_mentions(message: dict[str, Any]) -> list[dict[str, Any]]:
    mentions = message.get("mentions")
    if not isinstance(mentions, list):
        return []
    return [redact(item) for item in mentions if isinstance(item, dict)]


def _feishu_chat_type(value: str) -> str:
    lowered = value.lower()
    if lowered in {"p2p", "private", "user"}:
        return "private"
    if lowered in {"group", "chat"}:
        return "group"
    return "private" if not lowered else lowered


def _feishu_operation(raw: dict[str, Any]) -> str | None:
    event = raw.get("event") if isinstance(raw.get("event"), dict) else raw
    value = str(event.get("event_type") or raw.get("type") or "").lower()
    if "withdraw" in value or "recall" in value or "delete" in value:
        return "recall"
    if "read" in value:
        return "read"
    if "reaction" in value or "emoji" in value:
        return "reaction"
    return None


def _gateway_policy(config: ChannelProviderSection) -> dict[str, Any]:
    return {
        "allow_inbound": True,
        "allow_outbound": True,
        "private_chat_only": config.private_chat_only,
        "group_messages": config.group_messages,
        "pairing_required": config.pairing_required,
        "provider": "feishu",
    }


def _hash_value(value: str | None) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()
