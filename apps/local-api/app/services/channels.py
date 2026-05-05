from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from core_types import (
    AssetCategory,
    ChannelAccount,
    ChannelBindSession,
    ChannelEvent,
    ErrorCode,
    RiskLevel,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.asset_repo import AssetRepository
from app.db.repositories.channel_repo import ChannelRepository
from app.schemas.assets import AssetCreateRequest, CapabilityGrantCreateRequest
from app.schemas.channels import (
    ChannelBindFinalizeResponse,
    ChannelBindStartRequest,
    ChannelBindStartResponse,
    ChannelBindStatusResponse,
    FeishuBindCallbackResponse,
    ChannelInboundWechatRequest,
    ChannelInboundWechatResponse,
    ChannelProviderHealthResponse,
    ChannelRevokeResponse,
)
from app.schemas.notifications import (
    InboundMessageCreateRequest,
    NotificationChannelCreateRequest,
)
from app.services.asset import AssetService
from app.services.audit import AuditEventService
from app.services.capability import CapabilityGraphService
from app.services.channel_connectors import (
    ChannelConnectorRegistry,
    ChannelSendResult,
    ProviderUnavailable,
)
from app.services.notifications import NotificationGatewayService
from app.services.secrets import SecretStore

DEFAULT_BIND_POLICY = {
    "allow_inbound": True,
    "allow_outbound": True,
    "private_chat_only": True,
    "default_peer_policy": "deny_until_paired",
}


class ChannelBindingService:
    def __init__(
        self,
        *,
        repo: ChannelRepository,
        asset_repo: AssetRepository,
        asset_service: AssetService,
        capability: CapabilityGraphService,
        notifications: NotificationGatewayService,
        connectors: ChannelConnectorRegistry,
        secret_store: SecretStore,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._asset_repo = asset_repo
        self._assets = asset_service
        self._capability = capability
        self._notifications = notifications
        self._connectors = connectors
        self._secrets = secret_store
        self._trace = trace_service
        self._audit = audit_service

    async def start_bind(
        self,
        request: ChannelBindStartRequest,
        *,
        trace_id: str | None = None,
    ) -> ChannelBindStartResponse:
        bind_session_id = new_id("chbind")
        now = utc_now_iso()
        policy = {**DEFAULT_BIND_POLICY, **request.policy}
        try:
            challenge = await self._connectors.get(request.provider).start_bind(
                bind_session_id=bind_session_id,
                display_name_hint=request.display_name_hint,
            )
            status = challenge.status
            expires_at = challenge.expires_at
            failure_reason = None
            provider_status = challenge.provider_status
            qr_payload_ref = _redacted_ref(challenge.qr_payload or bind_session_id)
            qr = {
                "format": challenge.qr_format,
                "data": challenge.qr_payload,
                "artifact_id": None,
            }
        except ProviderUnavailable as exc:
            status = "failed"
            expires_at = (utc_now() + timedelta(minutes=10)).isoformat()
            failure_reason = "provider_unavailable"
            provider_status = {"error": str(redact(str(exc)))}
            qr_payload_ref = None
            qr = {}
        data = {
            "bind_session_id": bind_session_id,
            "organization_id": "org_default",
            "provider": request.provider,
            "requested_by_member_id": request.requested_by_member_id,
            "display_name_hint": request.display_name_hint,
            "status": status,
            "qr_format": qr.get("format"),
            "qr_payload_ref": qr_payload_ref,
            "qr_artifact_id": None,
            "expires_at": expires_at,
            "risk_level": RiskLevel.R3.value,
            "policy_snapshot": redact(policy),
            "provider_status": redact(provider_status),
            "failure_reason": failure_reason,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_bind_session(data)
        await self._audit.write_event(
            actor_type="member",
            actor_id=request.requested_by_member_id,
            action="channel.bind.started",
            object_type="channel_bind_session",
            object_id=bind_session_id,
            summary="微信渠道绑定会话已创建",
            risk_level=RiskLevel.R3,
            payload={"provider": request.provider, "status": status},
            trace_id=trace_id,
        )
        row = await self._require_bind_session(bind_session_id)
        return ChannelBindStartResponse(**row, qr=redact(qr), poll_after_ms=1500)

    async def get_bind_status(
        self,
        bind_session_id: str,
        *,
        trace_id: str | None = None,
    ) -> ChannelBindStatusResponse:
        row = await self._require_bind_session(bind_session_id)
        if _is_expired(row.get("expires_at")) and row["status"] not in {"bound", "revoked"}:
            await self._repo.update_bind_session(
                bind_session_id,
                {
                    "status": "expired",
                    "failure_reason": "bind_session_expired",
                    "updated_at": utc_now_iso(),
                },
            )
            row = await self._require_bind_session(bind_session_id)
        if row["status"] in {"qr_ready", "pending", "scanned"}:
            try:
                provider_status = await self._connectors.get(row["provider"]).poll_bind(
                    bind_session_id
                )
                updates: dict[str, Any] = {
                    "provider_status": provider_status.provider_state,
                    "updated_at": utc_now_iso(),
                }
                next_status = str(provider_status.status)
                if next_status == "logged_in":
                    next_status = "confirmed"
                if provider_status.status in {"confirmed", "bound", "logged_in"}:
                    updates.update(
                        {
                            "status": "confirmed",
                            "confirmed_at": provider_status.confirmed_at or utc_now_iso(),
                            "provider_account_ref_redacted": _redacted_ref(
                                provider_status.provider_account_ref or bind_session_id
                            ),
                        }
                    )
                elif provider_status.status in {"expired", "failed"}:
                    updates.update(
                        {
                            "status": provider_status.status,
                            "failure_reason": provider_status.failure_reason,
                        }
                    )
                elif next_status in {"qr_ready", "pending", "scanned"}:
                    updates["status"] = next_status
                await self._repo.update_bind_session(bind_session_id, updates)
                row = await self._require_bind_session(bind_session_id)
            except ProviderUnavailable:
                await self._repo.update_bind_session(
                    bind_session_id,
                    {
                        "status": "failed",
                        "failure_reason": "provider_unavailable",
                        "updated_at": utc_now_iso(),
                    },
                )
                row = await self._require_bind_session(bind_session_id)
        events = await self._repo.list_bind_session_events(bind_session_id)
        qr = {
            "format": row.get("qr_format"),
            "data": row.get("qr_payload_ref"),
            "artifact_id": row.get("qr_artifact_id"),
        }
        del trace_id
        return ChannelBindStatusResponse(**row, events=events, qr=redact(qr), poll_after_ms=1500)

    async def finalize_bind(
        self,
        bind_session_id: str,
        *,
        trace_id: str | None = None,
    ) -> ChannelBindFinalizeResponse:
        row = await self._require_bind_session(bind_session_id)
        if _is_expired(row.get("expires_at")) and row["status"] not in {"bound", "revoked"}:
            await self._repo.update_bind_session(
                bind_session_id,
                {
                    "status": "expired",
                    "failure_reason": "bind_session_expired",
                    "updated_at": utc_now_iso(),
                },
            )
            row = await self._require_bind_session(bind_session_id)
        if row.get("bound_asset_id") and row.get("bound_channel_id"):
            asset_row = await self._asset_repo.get_asset(row["bound_asset_id"])
            channel = await self._notifications.get_channel(row["bound_channel_id"])
            account = await self._account_for_channel(row["bound_channel_id"])
            return ChannelBindFinalizeResponse(
                bind_session=ChannelBindSession(**row),
                asset=redact(asset_row or {}),
                channel=channel.model_dump(mode="json"),
                account=account,
            )
        if row["status"] not in {"confirmed", "qr_ready", "pending", "scanned"}:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "绑定会话状态不可 finalize",
                status_code=409,
                details={"status": row["status"]},
            )
        try:
            bound = await self._connectors.get(row["provider"]).finalize_bind(bind_session_id)
        except ProviderUnavailable as exc:
            await self._repo.update_bind_session(
                bind_session_id,
                {
                    "status": "failed",
                    "failure_reason": "provider_unavailable",
                    "updated_at": utc_now_iso(),
                },
            )
            raise AppError(
                ErrorCode.MCP_UNAVAILABLE,
                "微信 connector 不可用",
                status_code=503,
                details={"reason": str(redact(str(exc)))},
            ) from exc
        provider_state_ref, storage_uri = self._secrets.put_secret(
            json.dumps(redact(bound.provider_state), ensure_ascii=False, default=str)
        )
        now = utc_now_iso()
        await self._asset_repo.upsert_secret_ref(
            secret_ref=provider_state_ref,
            organization_id="org_default",
            kind="channel_provider_state",
            label=f"微信渠道状态：{bound.display_name}",
            storage_uri=storage_uri,
            secret_type="wechat_provider_state",
            provider=row["provider"],
            metadata={"bind_session_id": bind_session_id},
            now=now,
        )
        asset = await self._assets.create_asset(
            AssetCreateRequest(
                asset_type=AssetCategory.ACCOUNT,
                display_name=f"微信：{bound.display_name}",
                provider=row["provider"],
                sensitivity="high",
                config={
                    "platform": "wechat",
                    "username": bound.display_name,
                    "auth_type": "channel_provider_state",
                    "provider": row["provider"],
                    "channel_kind": "direct_message",
                },
                owner_scope_type="member",
                owner_scope_id=row["requested_by_member_id"],
                visibility="private",
                risk_level=RiskLevel.R3,
                summary_text=f"{bound.display_name} 微信通讯渠道",
                capabilities=bound.capabilities,
                policy={"channel_binding": True, **row["policy_snapshot"]},
                metadata={
                    "asset_subtype": "communication_channel",
                    "bind_session_id": bind_session_id,
                    "provider_state_ref": provider_state_ref,
                },
            ),
            trace_id=trace_id,
        )
        await self._asset_repo.update_asset(
            asset.asset_id,
            {"secret_ref": provider_state_ref, "updated_at": now},
        )
        await self._grant_channel_actions(asset.asset_id, row["requested_by_member_id"], trace_id)
        channel = await self._notifications.create_channel(
            NotificationChannelCreateRequest(
                provider=row["provider"],
                display_name=f"微信：{bound.display_name}",
                channel_type="direct_message",
                sensitivity="high",
                policy=row["policy_snapshot"],
                provider_config={
                    "channel_account_ref": _redacted_ref(bound.provider_account_ref),
                    "provider_state_ref": provider_state_ref,
                },
                created_by_member_id=row["requested_by_member_id"],
                asset_id=asset.asset_id,
                create_asset=False,
            ),
            trace_id=trace_id,
        )
        account_data = {
            "channel_account_id": new_id("chacc"),
            "organization_id": "org_default",
            "asset_id": asset.asset_id,
            "channel_id": channel.channel_id,
            "bind_session_id": bind_session_id,
            "provider": row["provider"],
            "account_ref_redacted": _redacted_ref(bound.provider_account_ref),
            "display_name": bound.display_name,
            "status": "active",
            "capabilities": bound.capabilities,
            "provider_state_ref": provider_state_ref,
            "policy": row["policy_snapshot"],
            "last_seen_at": now,
            "last_verified_at": now,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_account(account_data)
        await self._repo.update_bind_session(
            bind_session_id,
            {
                "status": "bound",
                "bound_asset_id": asset.asset_id,
                "bound_channel_id": channel.channel_id,
                "provider_account_ref_redacted": _redacted_ref(bound.provider_account_ref),
                "provider_state_ref": provider_state_ref,
                "provider_status": {"finalized": True},
                "updated_at": now,
            },
        )
        await self._audit.write_event(
            actor_type="system",
            action="channel.bind.completed",
            object_type="channel_bind_session",
            object_id=bind_session_id,
            summary="微信渠道绑定完成",
            risk_level=RiskLevel.R3,
            payload={"asset_id": asset.asset_id, "channel_id": channel.channel_id},
            trace_id=trace_id,
        )
        updated = await self._require_bind_session(bind_session_id)
        return ChannelBindFinalizeResponse(
            bind_session=ChannelBindSession(**updated),
            asset=asset.model_dump(mode="json"),
            channel=channel.model_dump(mode="json"),
            account=ChannelAccount(**account_data),
        )

    async def cancel_bind(
        self,
        bind_session_id: str,
        *,
        trace_id: str | None = None,
    ) -> ChannelBindSession:
        row = await self._require_bind_session(bind_session_id)
        if row["status"] in {"bound", "revoked"}:
            raise AppError(ErrorCode.TASK_STATE_INVALID, "绑定会话不可取消", status_code=409)
        await self._repo.update_bind_session(
            bind_session_id,
            {"status": "cancelled", "updated_at": utc_now_iso()},
        )
        await self._audit.write_event(
            actor_type="system",
            action="channel.bind.cancelled",
            object_type="channel_bind_session",
            object_id=bind_session_id,
            summary="微信渠道绑定已取消",
            risk_level=RiskLevel.R2,
            payload={"status": row["status"]},
            trace_id=trace_id,
        )
        return ChannelBindSession(**(await self._require_bind_session(bind_session_id)))

    async def confirm_feishu_bind_callback(
        self,
        *,
        bind_session_id: str,
        code: str | None = None,
        tenant_key: str | None = None,
        open_id: str | None = None,
        trace_id: str | None = None,
    ) -> FeishuBindCallbackResponse:
        row = await self._require_bind_session(bind_session_id)
        connector = self._connectors.get("feishu")
        recorder = getattr(connector, "record_bind_callback", None)
        callback_state: dict[str, Any] = {}
        if callable(recorder):
            callback_state = recorder(
                bind_session_id=bind_session_id,
                code=code,
                tenant_key=tenant_key,
                open_id=open_id,
            )
        now = utc_now_iso()
        updates: dict[str, Any] = {
            "status": "confirmed",
            "confirmed_at": now,
            "provider_status": {
                "callback_received": True,
                "transport_mode": "websocket",
                "bind_mode": "qr_oauth_callback",
                "callback_keys": [
                    key
                    for key, value in (
                        ("code", code),
                        ("tenant_key", tenant_key),
                        ("open_id", open_id),
                    )
                    if value is not None
                ],
            },
            "updated_at": now,
        }
        if callback_state:
            if callback_state.get("provider_account_ref"):
                updates["provider_account_ref_redacted"] = _redacted_ref(
                    str(callback_state["provider_account_ref"])
                )
            if callback_state.get("status") == "confirmed":
                updates["confirmed_at"] = callback_state.get("confirmed_at") or now
            updates["provider_status"].update(
                {
                    key: value
                    for key, value in callback_state.items()
                    if key not in {"provider_account_ref", "status"}
                }
            )
        await self._repo.update_bind_session(bind_session_id, updates)
        row = await self._require_bind_session(bind_session_id)
        await self._audit.write_event(
            actor_type="system",
            action="channel.bind.callback.confirmed",
            object_type="channel_bind_session",
            object_id=bind_session_id,
            summary="飞书扫码回调已确认",
            risk_level=RiskLevel.R2,
            payload={
                "provider": row["provider"],
                "confirmed_at": row.get("confirmed_at"),
            },
            trace_id=trace_id,
        )
        return FeishuBindCallbackResponse(
            status=row["status"],
            bind_session_id=bind_session_id,
            provider="feishu",
            provider_account_ref_redacted=row.get("provider_account_ref_redacted"),
            confirmed_at=row.get("confirmed_at"),
            diagnostic={
                "bind_mode": "qr_oauth_callback",
                "redirect_target": "finalize_bind_session",
            },
        )

    async def list_accounts(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
    ) -> list[ChannelAccount]:
        return [
            ChannelAccount(**row)
            for row in await self._repo.list_accounts(provider=provider, status=status)
        ]

    async def revoke_channel(
        self,
        channel_id: str,
        *,
        trace_id: str | None = None,
    ) -> ChannelRevokeResponse:
        channel = await self._notifications.update_channel_status(
            channel_id,
            "revoked",
            trace_id=trace_id,
        )
        if channel.asset_id:
            await self._assets.set_status(channel.asset_id, "archived", trace_id=trace_id)
        account = await self._repo.get_account_by_channel(channel_id)
        if account:
            await self._connectors.get(channel.provider).revoke(account.get("provider_state_ref"))
            await self._repo.update_account(
                account["channel_account_id"],
                {"status": "revoked", "updated_at": utc_now_iso()},
            )
            if account.get("bind_session_id"):
                await self._repo.update_bind_session(
                    account["bind_session_id"],
                    {"status": "revoked", "updated_at": utc_now_iso()},
                )
        return ChannelRevokeResponse(
            channel_id=channel_id,
            asset_id=channel.asset_id,
            status="revoked",
        )

    async def receive_wechat_inbound(
        self,
        request: ChannelInboundWechatRequest,
        *,
        trace_id: str | None = None,
    ) -> ChannelInboundWechatResponse:
        if request.channel_account_id:
            account = await self._repo.get_account(request.channel_account_id)
        elif request.channel_id:
            account = await self._repo.get_account_by_channel(request.channel_id)
        else:
            accounts = await self._repo.list_accounts(
                provider=request.provider,
                status="active",
                limit=1,
            )
            account = accounts[0] if accounts else None
        if not account:
            raise AppError(ErrorCode.NOT_FOUND, "微信渠道账号不存在", status_code=404)
        source = request.source or {}
        message = request.message or {}
        chat_type = str(source.get("chat_type") or "private")
        peer_ref = str(source.get("peer_ref") or source.get("from") or "unknown")
        peer_hash = _redacted_ref(peer_ref)
        requested_pairing = str(source.get("pairing_status") or "").lower()
        policy_allows_inbound = bool(source.get("allow_inbound", True))
        peer_allows_inbound = (
            chat_type == "private"
            and policy_allows_inbound
            and requested_pairing not in {"unpaired", "denied", "blocked"}
        )
        now = utc_now_iso()
        peer = await self._repo.upsert_peer(
            {
                "channel_peer_id": new_id("chpeer"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "provider": account["provider"],
                "peer_ref_redacted": peer_hash,
                "peer_type": chat_type,
                "display_name_redacted": str(redact(source.get("display_name") or "")) or None,
                "pairing_status": "paired" if peer_allows_inbound else "rejected_or_ignored",
                "allow_inbound": peer_allows_inbound,
                "allow_outbound": False,
                "metadata": {"source": "wechat_inbound"},
                "created_at": now,
                "updated_at": now,
                "update_policy": not peer_allows_inbound,
            }
        )
        content = str(message.get("content_text") or message.get("text") or "")
        provider_event_ref = _redacted_ref(request.provider_event_id or content or now)
        inserted = await self._repo.insert_event_offset(
            {
                "offset_id": new_id("choff"),
                "organization_id": account["organization_id"],
                "channel_account_id": account["channel_account_id"],
                "provider": account["provider"],
                "provider_event_id_redacted": provider_event_ref,
                "channel_event_id": None,
                "status": "processing",
                "received_at": request.received_at or now,
                "created_at": now,
                "updated_at": now,
            }
        )
        if not inserted:
            existing_event = await self._repo.get_event_by_offset(
                channel_account_id=account["channel_account_id"],
                provider_event_id_redacted=provider_event_ref,
            )
            if existing_event is None:
                raise AppError(
                    ErrorCode.NOT_FOUND,
                    "微信入站事件重复但原事件不存在",
                    status_code=409,
                )
            return ChannelInboundWechatResponse(
                event=ChannelEvent(**existing_event),
                notification_inbound=None,
                status="duplicate",
            )
        event_data = {
            "channel_event_id": new_id("chevt"),
            "organization_id": account["organization_id"],
            "provider": account["provider"],
            "channel_account_id": account["channel_account_id"],
            "channel_id": account.get("channel_id"),
            "event_type": "wechat.inbound",
            "provider_event_id_redacted": provider_event_ref,
            "payload_redacted": {
                "provider": request.provider,
                "channel_account_id": request.channel_account_id,
                "channel_id": request.channel_id,
                "provider_event_id_redacted": provider_event_ref,
                "source": {
                    "chat_type": chat_type,
                    "peer_ref_redacted": peer_hash,
                    "display_name_redacted": str(redact(source.get("display_name") or "")),
                },
                "message": {
                    "content_type": message.get("content_type") or "text",
                    "content_text": redact(content),
                },
                "raw_event_key_count": len(request.raw_event),
            },
            "normalized_event": {
                "provider": account["provider"],
                "chat_type": chat_type,
                "peer_ref_redacted": peer_hash,
                "content_type": message.get("content_type") or "text",
                "content_text": redact(content),
                "untrusted_external_content": True,
            },
            "status": "received" if peer.get("allow_inbound") else "rejected_or_ignored",
            "trace_id": trace_id,
            "received_at": request.received_at or now,
            "created_at": now,
        }
        await self._repo.insert_event(event_data)
        await self._repo.update_event_offset(
            channel_account_id=account["channel_account_id"],
            provider_event_id_redacted=provider_event_ref,
            fields={
                "channel_event_id": event_data["channel_event_id"],
                "status": event_data["status"],
                "updated_at": now,
            },
        )
        notification_payload = None
        if event_data["status"] == "received" and account.get("channel_id"):
            inbound = await self._notifications.receive_inbound(
                InboundMessageCreateRequest(
                    channel_id=account["channel_id"],
                    sender_ref=peer_hash,
                    provider_message_id=provider_event_ref,
                    received_at=request.received_at,
                    content=content,
                ),
                trace_id=trace_id,
            )
            notification_payload = inbound.model_dump(mode="json")
        return ChannelInboundWechatResponse(
            event=ChannelEvent(**event_data),
            notification_inbound=notification_payload,
            status=event_data["status"],
        )

    async def provider_health(self, provider: str) -> ChannelProviderHealthResponse:
        try:
            health = await self._connectors.get(provider).health()
            return ChannelProviderHealthResponse(**health.__dict__)
        except ProviderUnavailable:
            return ChannelProviderHealthResponse(
                provider=provider,
                enabled=False,
                reachable=False,
                login_state="provider_unavailable",
                last_error_code="provider_unavailable",
            )

    def connector_registry(self) -> ChannelConnectorRegistry:
        return self._connectors

    async def send_channel_text(
        self,
        *,
        provider: str,
        provider_state_ref: str | None,
        recipient: str,
        text: str,
    ) -> ChannelSendResult:
        provider_state = self._load_provider_state(provider_state_ref)
        return await self._connectors.get(provider).send_text(
            provider_state_ref=provider_state_ref,
            provider_state=provider_state,
            recipient=recipient,
            text=text,
        )

    async def send_channel_audio(
        self,
        *,
        provider: str,
        provider_state_ref: str | None,
        recipient: str,
        audio_bytes: bytes,
        content_type: str | None,
        filename: str | None,
    ) -> ChannelSendResult:
        provider_state = self._load_provider_state(provider_state_ref)
        return await self._connectors.get(provider).send_audio(
            provider_state_ref=provider_state_ref,
            provider_state=provider_state,
            recipient=recipient,
            audio_bytes=audio_bytes,
            content_type=content_type,
            filename=filename,
        )

    async def _grant_channel_actions(
        self,
        asset_id: str,
        member_id: str,
        trace_id: str | None,
    ) -> None:
        for action in ("message_send", "message_receive", "approval.reply", "channel.revoke"):
            await self._capability.create_grant(
                CapabilityGrantCreateRequest(
                    subject_type="member",
                    subject_id=member_id,
                    object_type="asset",
                    object_id=asset_id,
                    action=action,
                    effect="allow",
                    risk_level=RiskLevel.R3,
                    source_type="channel_binding",
                    source_id=asset_id,
                ),
                trace_id=trace_id,
            )

    async def _require_bind_session(self, bind_session_id: str) -> dict[str, Any]:
        row = await self._repo.get_bind_session(bind_session_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "绑定会话不存在", status_code=404)
        return row

    async def _account_for_channel(self, channel_id: str) -> ChannelAccount:
        account = await self._repo.get_account_by_channel(channel_id)
        if account is None:
            raise AppError(ErrorCode.NOT_FOUND, "渠道账号不存在", status_code=404)
        return ChannelAccount(**account)

    def _load_provider_state(self, provider_state_ref: str | None) -> dict[str, Any] | None:
        raw = self._secrets.get_secret(provider_state_ref)
        if not raw:
            return None
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None


def _redacted_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_expired(value: Any) -> bool:
    if not value:
        return False
    try:
        text = str(value).replace("Z", "+00:00")
        expires_at = datetime.fromisoformat(text)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= utc_now()
