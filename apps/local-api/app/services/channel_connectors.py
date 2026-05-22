from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import os
import struct
import tempfile
import time
import wave
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlencode

from trace_service import redact

from app.core.config import ChannelProviderSection
from app.core.time import utc_now, utc_now_iso


@dataclass(frozen=True)
class ChannelBindChallenge:
    provider_session_id: str
    status: str
    qr_format: str | None
    qr_payload: str | None
    expires_at: str
    provider_status: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelProviderStatus:
    status: str
    provider_account_ref: str | None = None
    display_name: str | None = None
    provider_state: dict[str, Any] = field(default_factory=dict)
    failure_reason: str | None = None
    confirmed_at: str | None = None


@dataclass(frozen=True)
class ChannelBoundAccount:
    provider_account_ref: str
    display_name: str
    provider_state: dict[str, Any]
    capabilities: list[str]


@dataclass(frozen=True)
class ChannelSendResult:
    status: str
    provider_message_id: str | None = None
    response_summary: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True)
class ChannelHealth:
    provider: str
    enabled: bool
    reachable: bool
    login_state: str
    version: str | None = None
    last_error_code: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class ChannelConnector(Protocol):
    provider: str

    async def start_bind(
        self,
        *,
        bind_session_id: str,
        display_name_hint: str,
    ) -> ChannelBindChallenge:
        ...

    async def poll_bind(self, bind_session_id: str) -> ChannelProviderStatus:
        ...

    async def finalize_bind(self, bind_session_id: str) -> ChannelBoundAccount:
        ...

    async def revoke(self, provider_state_ref: str | None) -> ChannelSendResult:
        ...

    async def send_text(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        text: str,
    ) -> ChannelSendResult:
        ...

    async def send_audio(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        audio_bytes: bytes,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        ...

    async def send_file(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        local_path: Path,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        ...

    async def health(self) -> ChannelHealth:
        ...

    async def poll_events(
        self,
        *,
        provider_state: dict[str, Any] | None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        ...

    async def download_media(
        self,
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        attachment: dict[str, Any],
    ) -> bytes:
        ...


class ProviderUnavailable(RuntimeError):
    pass


class WechatMockConnector:
    provider = "wechat_mock"

    def __init__(self, config: ChannelProviderSection) -> None:
        self._config = config
        self._sessions: dict[str, ChannelProviderStatus] = {}
        self._poll_counts: dict[str, int] = {}

    async def start_bind(
        self,
        *,
        bind_session_id: str,
        display_name_hint: str,
    ) -> ChannelBindChallenge:
        expires_at = (utc_now() + timedelta(minutes=10)).isoformat()
        self._sessions[bind_session_id] = ChannelProviderStatus(
            status="confirmed",
            provider_account_ref=f"mock:{bind_session_id}",
            display_name=display_name_hint or "微信测试账号",
            provider_state={
                "account_ref_redacted": _hash_value(f"mock:{bind_session_id}"),
                "provider": self.provider,
            },
            confirmed_at=utc_now_iso(),
        )
        self._poll_counts[bind_session_id] = 0
        return ChannelBindChallenge(
            provider_session_id=bind_session_id,
            status="qr_ready",
            qr_format="text",
            qr_payload=f"mock-wechat-qr:{bind_session_id}",
            expires_at=expires_at,
            provider_status={"mock": True, "next_status": "confirmed"},
        )

    async def poll_bind(self, bind_session_id: str) -> ChannelProviderStatus:
        status = self._sessions.get(bind_session_id)
        if status is None:
            return ChannelProviderStatus(
                status="failed",
                failure_reason="mock_session_not_found",
            )
        count = self._poll_counts.get(bind_session_id, 0)
        self._poll_counts[bind_session_id] = count + 1
        if count == 0:
            return ChannelProviderStatus(
                status="scanned",
                provider_account_ref=status.provider_account_ref,
                display_name=status.display_name,
                provider_state={"mock": True, "scan_status": "scanned"},
            )
        return status or ChannelProviderStatus(
            status="failed",
            failure_reason="mock_session_not_found",
        )

    async def finalize_bind(self, bind_session_id: str) -> ChannelBoundAccount:
        status = self._sessions.get(bind_session_id)
        if status is None:
            status = await self.poll_bind(bind_session_id)
        if status.status != "confirmed" or not status.provider_account_ref:
            raise ProviderUnavailable(status.failure_reason or "mock bind is not confirmed")
        return ChannelBoundAccount(
            provider_account_ref=status.provider_account_ref,
            display_name=status.display_name or "微信测试账号",
            provider_state={
                **status.provider_state,
                "account_id": status.provider_account_ref,
                "account_ref_redacted": _hash_value(str(status.provider_account_ref)),
            },
            capabilities=[
                "message_channel",
                "notification.inbound",
                "notification.outbound",
                "approval.reply",
            ],
        )

    async def revoke(self, provider_state_ref: str | None) -> ChannelSendResult:
        del provider_state_ref
        return ChannelSendResult(status="sent", response_summary={"mock_revoked": True})

    async def send_text(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        text: str,
    ) -> ChannelSendResult:
        del provider_state_ref, provider_state, text
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"wechat_mock:{_hash_value(recipient)[:16]}",
            response_summary={"mock": True, "recipient_hash": _hash_value(recipient)},
        )

    async def send_audio(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        audio_bytes: bytes,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        del provider_state_ref, provider_state
        digest = hashlib.sha256(audio_bytes).hexdigest()
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"wechat_mock_audio:{digest[:16]}",
            response_summary={
                "mock": True,
                "recipient_hash": _hash_value(recipient),
                "content_type": content_type,
                "filename": filename,
                "audio_size_bytes": len(audio_bytes),
            },
        )

    async def send_file(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        local_path: Path,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        del provider_state_ref, provider_state, local_path
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"wechat_mock_file:{_hash_value(recipient)[:16]}",
            response_summary={
                "mock": True,
                "recipient_hash": _hash_value(recipient),
                "content_type": content_type,
                "filename": filename,
                "delivery_kind": "file",
            },
        )

    async def health(self) -> ChannelHealth:
        return ChannelHealth(
            provider=self.provider,
            enabled=self._config.enabled,
            reachable=True,
            login_state="mock_ready",
            version="mock",
            details={"test_only": True},
        )

    async def poll_events(
        self,
        *,
        provider_state: dict[str, Any] | None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        del provider_state, limit
        return []

    async def download_media(
        self,
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        attachment: dict[str, Any],
    ) -> bytes:
        del provider_state, event
        content = attachment.get("content_bytes")
        if isinstance(content, bytes):
            return content
        text = str(attachment.get("content_text") or "")
        return text.encode("utf-8")


class FeishuMockConnector:
    provider = "feishu_mock"

    def __init__(self, config: ChannelProviderSection) -> None:
        self._config = config
        self._events: list[dict[str, Any]] = []

    def enqueue_event(self, event: dict[str, Any]) -> None:
        self._events.append(event)

    def record_bind_callback(
        self,
        *,
        bind_session_id: str,
        code: str | None = None,
        tenant_key: str | None = None,
        open_id: str | None = None,
    ) -> dict[str, Any]:
        del code
        return {
            "bind_session_id": bind_session_id,
            "status": "confirmed",
            "provider_account_ref": "feishu_mock:bot",
            "display_name": "飞书测试账号",
            "tenant_key_redacted": _hash_value(tenant_key or "mock_tenant"),
            "operator_open_id_redacted": _hash_value(open_id or "mock_open_id"),
            "confirmed_at": utc_now_iso(),
        }

    async def start_bind(
        self,
        *,
        bind_session_id: str,
        display_name_hint: str,
    ) -> ChannelBindChallenge:
        return ChannelBindChallenge(
            provider_session_id=bind_session_id,
            status="qr_ready",
            qr_format="url",
            qr_payload=f"feishu-mock-bind://scan?state={bind_session_id}",
            expires_at=(utc_now() + timedelta(minutes=10)).isoformat(),
            provider_status={
                "mock": True,
                "display_name_hint": display_name_hint or "飞书测试账号",
                "transport_mode": "websocket",
                "bind_mode": "qr_callback",
            },
        )

    async def poll_bind(self, bind_session_id: str) -> ChannelProviderStatus:
        del bind_session_id
        return ChannelProviderStatus(
            status="confirmed",
            provider_account_ref="feishu_mock:bot",
            display_name="飞书测试账号",
            provider_state={
                "mock": True,
                "app_id": "cli_mock",
                "account_id": "feishu_mock:bot",
                "transport_mode": "websocket",
                "capabilities": _feishu_capabilities(),
            },
            confirmed_at=utc_now_iso(),
        )

    async def finalize_bind(self, bind_session_id: str) -> ChannelBoundAccount:
        status = await self.poll_bind(bind_session_id)
        return ChannelBoundAccount(
            provider_account_ref=str(status.provider_account_ref),
            display_name=status.display_name or "飞书测试账号",
            provider_state=status.provider_state,
            capabilities=_feishu_capabilities(),
        )

    async def revoke(self, provider_state_ref: str | None) -> ChannelSendResult:
        del provider_state_ref
        return ChannelSendResult(status="sent", response_summary={"mock_revoked": True})

    async def send_text(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        text: str,
    ) -> ChannelSendResult:
        del provider_state_ref, provider_state
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"feishu_mock_text:{_hash_value(recipient)[:16]}",
            response_summary={"mock": True, "recipient_hash": _hash_value(recipient), "text": text},
        )

    async def send_audio(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        audio_bytes: bytes,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        del provider_state_ref, provider_state
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"feishu_mock_audio:{_hash_value(recipient)[:16]}",
            response_summary={
                "mock": True,
                "recipient_hash": _hash_value(recipient),
                "audio_size_bytes": len(audio_bytes),
                "content_type": content_type,
                "filename": filename,
            },
        )

    async def send_file(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        local_path: Path,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        del provider_state_ref, provider_state, local_path
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"feishu_mock_file:{_hash_value(recipient)[:16]}",
            response_summary={
                "mock": True,
                "recipient_hash": _hash_value(recipient),
                "content_type": content_type,
                "filename": filename,
                "delivery_kind": "file",
            },
        )

    async def send_card(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        card_json: dict[str, Any],
    ) -> ChannelSendResult:
        del provider_state_ref, provider_state, card_json
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"feishu_mock_card:{_hash_value(recipient)[:16]}",
            response_summary={"mock": True, "recipient_hash": _hash_value(recipient), "card": True},
        )

    async def recall_message(
        self,
        *,
        provider_state: dict[str, Any] | None,
        message_id: str,
    ) -> ChannelSendResult:
        del provider_state
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"feishu_mock:{_hash_value(message_id)[:16]}",
            response_summary={"mock": True, "operation": "recall"},
        )

    async def mark_message_read(
        self,
        *,
        provider_state: dict[str, Any] | None,
        message_id: str,
    ) -> ChannelSendResult:
        del provider_state
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"feishu_mock:{_hash_value(message_id)[:16]}",
            response_summary={"mock": True, "operation": "read"},
        )

    async def add_reaction(
        self,
        *,
        provider_state: dict[str, Any] | None,
        message_id: str,
        emoji_type: str,
    ) -> ChannelSendResult:
        del provider_state
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"feishu_mock:{_hash_value(message_id)[:16]}",
            response_summary={"mock": True, "operation": "reaction", "emoji_type": emoji_type},
        )

    async def history(
        self,
        *,
        provider_state: dict[str, Any] | None,
        container_id: str,
        container_id_type: str,
        page_size: int = 20,
    ) -> ChannelSendResult:
        del provider_state
        return ChannelSendResult(
            status="sent",
            response_summary={
                "mock": True,
                "operation": "history",
                "container_id_ref": _hash_value(container_id),
                "container_id_type": container_id_type,
                "page_size": page_size,
            },
        )

    async def poll_events(
        self,
        *,
        provider_state: dict[str, Any] | None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        del provider_state
        events, self._events = self._events[:limit], self._events[limit:]
        return events

    async def download_media(
        self,
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        attachment: dict[str, Any],
    ) -> bytes:
        del provider_state, event
        content = attachment.get("content_bytes")
        if isinstance(content, bytes):
            return content
        return str(attachment.get("content_text") or "").encode("utf-8")

    async def health(self) -> ChannelHealth:
        return ChannelHealth(
            provider=self.provider,
            enabled=self._config.enabled,
            reachable=True,
            login_state="mock_ready",
            version="mock",
            details={"test_only": True, "transport_mode": "websocket"},
        )


class FeishuOpenPlatformConnector:
    provider = "feishu"
    _base_url = "https://open.feishu.cn"

    def __init__(self, config: ChannelProviderSection, *, state_dir: Path) -> None:
        self._config = config
        self._state_dir = state_dir
        self._last_error_code: str | None = None
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    async def start_bind(
        self,
        *,
        bind_session_id: str,
        display_name_hint: str,
    ) -> ChannelBindChallenge:
        if not self._config.enabled:
            self._last_error_code = "provider_unavailable"
            raise ProviderUnavailable("feishu provider is disabled")
        creds = self._configured_credentials()
        app_id = str(creds.get("app_id") or "")
        status = "qr_ready" if app_id and creds.get("app_secret") else "waiting"
        auth_url = self._build_bind_authorize_url(bind_session_id, creds) if app_id else None
        self._write_bind_state(
            bind_session_id,
            {
                "bind_session_id": bind_session_id,
                "status": status,
                "app_id_redacted": _hash_value(app_id) if app_id else None,
                "display_name_hint": display_name_hint or "飞书机器人",
                "authorization_url_ref": _hash_value(auth_url) if auth_url else None,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            },
        )
        return ChannelBindChallenge(
            provider_session_id=bind_session_id,
            status=status,
            qr_format="url" if auth_url else None,
            qr_payload=auth_url,
            expires_at=(utc_now() + timedelta(minutes=15)).isoformat(),
            provider_status={
                "provider": "feishu",
                "display_name_hint": display_name_hint or "飞书机器人",
                "transport_mode": "websocket",
                "bind_mode": "qr_oauth_callback",
                "credential_status": "configured" if status == "qr_ready" else "missing",
                "redirect_uri_redacted": _hash_value(str(creds.get("redirect_uri") or "")),
                "required_env": [
                    "FEISHU_APP_ID",
                    "FEISHU_APP_SECRET",
                    "FEISHU_VERIFICATION_TOKEN",
                    "FEISHU_ENCRYPT_KEY",
                    "FEISHU_REDIRECT_URI",
                ],
            },
        )

    async def poll_bind(self, bind_session_id: str) -> ChannelProviderStatus:
        creds = self._configured_credentials()
        app_id = str(creds.get("app_id") or "")
        if not app_id or not creds.get("app_secret"):
            return ChannelProviderStatus(
                status="waiting",
                failure_reason="feishu_credentials_missing",
                provider_state={"transport_mode": "websocket"},
            )
        bind_state = self._read_bind_state(bind_session_id)
        if bind_state and bind_state.get("status") != "confirmed":
            return ChannelProviderStatus(
                status=str(bind_state.get("status") or "qr_ready"),
                provider_account_ref=f"feishu:{app_id}",
                display_name=str(self._config.media.get("display_name") or "飞书机器人"),
                provider_state={
                    "transport_mode": "websocket",
                    "bind_mode": "qr_oauth_callback",
                    "app_id_redacted": _hash_value(app_id),
                },
            )
        callback = bind_state or {}
        return ChannelProviderStatus(
            status="confirmed",
            provider_account_ref=f"feishu:{app_id}",
            display_name=str(self._config.media.get("display_name") or "飞书机器人"),
            provider_state={
                **creds,
                "account_id": f"feishu:{app_id}",
                "account_ref_redacted": _hash_value(f"feishu:{app_id}"),
                "transport_mode": "websocket",
                "bind_mode": "qr_oauth_callback",
                "bind_session_id": bind_session_id,
                "tenant_key_redacted": callback.get("tenant_key_redacted"),
                "operator_open_id_redacted": callback.get("operator_open_id_redacted"),
                "capabilities": _feishu_capabilities(),
            },
            confirmed_at=str(callback.get("confirmed_at") or utc_now_iso()),
        )

    def record_bind_callback(
        self,
        *,
        bind_session_id: str,
        code: str | None = None,
        tenant_key: str | None = None,
        open_id: str | None = None,
    ) -> dict[str, Any]:
        creds = self._configured_credentials()
        app_id = str(creds.get("app_id") or "")
        if not app_id:
            raise ProviderUnavailable("feishu app_id is required for bind callback")
        now = utc_now_iso()
        state = {
            **(self._read_bind_state(bind_session_id) or {}),
            "bind_session_id": bind_session_id,
            "status": "confirmed",
            "provider_account_ref": f"feishu:{app_id}",
            "display_name": str(self._config.media.get("display_name") or "飞书机器人"),
            "app_id_redacted": _hash_value(app_id),
            "oauth_code_redacted": _hash_value(code) if code else None,
            "tenant_key_redacted": _hash_value(tenant_key) if tenant_key else None,
            "operator_open_id_redacted": _hash_value(open_id) if open_id else None,
            "confirmed_at": now,
            "updated_at": now,
        }
        self._write_bind_state(bind_session_id, state)
        return state

    async def finalize_bind(self, bind_session_id: str) -> ChannelBoundAccount:
        status = await self.poll_bind(bind_session_id)
        if status.status != "confirmed" or not status.provider_account_ref:
            raise ProviderUnavailable(status.failure_reason or "feishu bind is not confirmed")
        return ChannelBoundAccount(
            provider_account_ref=status.provider_account_ref,
            display_name=status.display_name or "飞书机器人",
            provider_state=status.provider_state,
            capabilities=_feishu_capabilities(),
        )

    async def revoke(self, provider_state_ref: str | None) -> ChannelSendResult:
        del provider_state_ref
        return ChannelSendResult(
            status="sent",
            response_summary={"revoked_locally": True, "provider": "feishu"},
        )

    async def send_text(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        text: str,
    ) -> ChannelSendResult:
        del provider_state_ref
        return await self._send_message(
            provider_state=provider_state,
            recipient=recipient,
            msg_type="text",
            content={"text": text},
            delivery_kind="text",
        )

    async def send_audio(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        audio_bytes: bytes,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        del provider_state_ref
        suffix = Path(filename or "voice.mp3").suffix or ".mp3"
        with tempfile.NamedTemporaryFile(prefix="feishu-audio-", suffix=suffix, delete=False) as fp:
            temp_path = Path(fp.name)
            fp.write(audio_bytes)
        try:
            uploaded = await self._upload_file(
                provider_state=provider_state,
                file_path=temp_path,
                file_name=filename or temp_path.name,
                file_type=_feishu_file_type(content_type, filename),
            )
            if not uploaded.get("file_key"):
                return ChannelSendResult(
                    status="retryable_failure",
                    error_code="provider_media_upload_failed",
                    error_summary="feishu file upload did not return file_key",
                    response_summary={"retryable": True, "delivery_kind": "audio"},
                )
            return await self._send_message(
                provider_state=provider_state,
                recipient=recipient,
                msg_type="file",
                content={"file_key": uploaded["file_key"]},
                delivery_kind="audio",
                extra_summary={
                    "content_type": content_type,
                    "filename": filename,
                    "audio_size_bytes": len(audio_bytes),
                    "delivery_format": "file",
                    "file_key_ref": _hash_value(str(uploaded["file_key"])),
                },
            )
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def send_file(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        local_path: Path,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        del provider_state_ref
        file_name = filename or local_path.name
        uploaded = await self._upload_file(
            provider_state=provider_state,
            file_path=local_path,
            file_name=file_name,
            file_type=_feishu_file_type(content_type, file_name),
        )
        if not uploaded.get("file_key"):
            return ChannelSendResult(
                status="retryable_failure",
                error_code="provider_media_upload_failed",
                error_summary="feishu file upload did not return file_key",
                response_summary={"retryable": True, "delivery_kind": "file"},
            )
        return await self._send_message(
            provider_state=provider_state,
            recipient=recipient,
            msg_type="file",
            content={"file_key": uploaded["file_key"]},
            delivery_kind="file",
            extra_summary={
                "content_type": content_type,
                "filename": file_name,
                "delivery_format": "file",
                "file_key_ref": _hash_value(str(uploaded["file_key"])),
            },
        )

    async def send_card(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        card_json: dict[str, Any],
    ) -> ChannelSendResult:
        del provider_state_ref
        return await self._send_message(
            provider_state=provider_state,
            recipient=recipient,
            msg_type="interactive",
            content=card_json,
            delivery_kind="card",
        )

    async def recall_message(
        self,
        *,
        provider_state: dict[str, Any] | None,
        message_id: str,
    ) -> ChannelSendResult:
        return await self._request_message_operation(
            provider_state=provider_state,
            method="DELETE",
            path=f"/open-apis/im/v1/messages/{message_id}",
            operation="recall",
            provider_message_id=message_id,
        )

    async def mark_message_read(
        self,
        *,
        provider_state: dict[str, Any] | None,
        message_id: str,
    ) -> ChannelSendResult:
        return await self._request_message_operation(
            provider_state=provider_state,
            method="POST",
            path=f"/open-apis/im/v1/messages/{message_id}/read_users",
            operation="read",
            provider_message_id=message_id,
        )

    async def add_reaction(
        self,
        *,
        provider_state: dict[str, Any] | None,
        message_id: str,
        emoji_type: str,
    ) -> ChannelSendResult:
        return await self._request_message_operation(
            provider_state=provider_state,
            method="POST",
            path=f"/open-apis/im/v1/messages/{message_id}/reactions",
            operation="reaction",
            provider_message_id=message_id,
            json_body={"reaction_type": {"emoji_type": emoji_type}},
        )

    async def history(
        self,
        *,
        provider_state: dict[str, Any] | None,
        container_id: str,
        container_id_type: str,
        page_size: int = 20,
    ) -> ChannelSendResult:
        query = (
            f"container_id_type={container_id_type}"
            f"&container_id={container_id}&page_size={max(1, min(page_size, 50))}"
        )
        return await self._request_message_operation(
            provider_state=provider_state,
            method="GET",
            path=f"/open-apis/im/v1/messages?{query}",
            operation="history",
            provider_message_id=None,
        )

    async def poll_events(
        self,
        *,
        provider_state: dict[str, Any] | None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        del provider_state
        queue_path = self._state_dir / "event-queue.jsonl"
        if not queue_path.exists():
            return []
        try:
            lines = queue_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        events: list[dict[str, Any]] = []
        rest: list[str] = []
        for line in lines:
            if len(events) >= limit:
                rest.append(line)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        try:
            queue_path.write_text("\n".join(rest) + ("\n" if rest else ""), encoding="utf-8")
        except OSError:
            pass
        return events

    async def download_media(
        self,
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        attachment: dict[str, Any],
    ) -> bytes:
        file_key = str(attachment.get("file_key") or attachment.get("media_id") or "")
        if not file_key:
            inline = attachment.get("content_bytes")
            if isinstance(inline, bytes):
                return inline
            return str(attachment.get("content_text") or "").encode("utf-8")
        message_id = str(attachment.get("message_id") or _message_id_from_event(event) or "")
        token = await self._tenant_access_token(provider_state)
        import httpx

        async with httpx.AsyncClient(timeout=float(self._config.timeout_seconds or 10.0)) as client:
            if message_id:
                response = await client.get(
                    f"{self._base_url}/open-apis/im/v1/messages/{message_id}/resources/{file_key}",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"type": attachment.get("type") or attachment.get("file_type") or "file"},
                )
            else:
                response = await client.get(
                    f"{self._base_url}/open-apis/im/v1/messages/{file_key}/resources",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"type": attachment.get("type") or attachment.get("file_type") or "file"},
                )
            if response.status_code == 404:
                return b""
            response.raise_for_status()
            return response.content or b""

    async def health(self) -> ChannelHealth:
        if not self._config.enabled:
            return ChannelHealth(
                provider=self.provider,
                enabled=False,
                reachable=False,
                login_state="disabled",
                last_error_code="provider_unavailable",
            )
        creds = self._configured_credentials()
        configured = bool(creds.get("app_id") and creds.get("app_secret"))
        return ChannelHealth(
            provider=self.provider,
            enabled=True,
            reachable=configured,
            login_state="configured" if configured else "missing_credentials",
            version="openapi-v1",
            last_error_code=self._last_error_code,
            details={
                "transport_mode": "websocket",
                "state_dir": str(self._state_dir),
                "app_id_redacted": _hash_value(str(creds.get("app_id") or "")) if creds.get("app_id") else None,
                "capabilities": _feishu_capabilities(),
            },
        )

    def _configured_credentials(self) -> dict[str, Any]:
        media = self._config.media or {}
        return {
            "app_id": os.getenv("FEISHU_APP_ID") or media.get("app_id"),
            "app_secret": os.getenv("FEISHU_APP_SECRET") or media.get("app_secret"),
            "verification_token": os.getenv("FEISHU_VERIFICATION_TOKEN")
            or media.get("verification_token"),
            "encrypt_key": os.getenv("FEISHU_ENCRYPT_KEY") or media.get("encrypt_key"),
            "receive_id_type": media.get("receive_id_type") or "chat_id",
            "redirect_uri": os.getenv("FEISHU_REDIRECT_URI")
            or media.get("redirect_uri")
            or "http://127.0.0.1:8765/api/channels/inbound/feishu/bind-callback",
            "scope": media.get("scope") or "",
        }

    def _build_bind_authorize_url(self, bind_session_id: str, creds: dict[str, Any]) -> str:
        query = {
            "app_id": str(creds.get("app_id") or ""),
            "redirect_uri": str(creds.get("redirect_uri") or ""),
            "state": bind_session_id,
            "response_type": "code",
        }
        scope = str(creds.get("scope") or "")
        if scope:
            query["scope"] = scope
        return f"{self._base_url}/open-apis/authen/v1/index?{urlencode(query)}"

    def _read_bind_state(self, bind_session_id: str) -> dict[str, Any] | None:
        path = self._bind_state_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        item = data.get(bind_session_id)
        return item if isinstance(item, dict) else None

    def _write_bind_state(self, bind_session_id: str, state: dict[str, Any]) -> None:
        path = self._bind_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data[bind_session_id] = redact(state)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _bind_state_path(self) -> Path:
        return self._state_dir / "bind-sessions.json"

    async def _tenant_access_token(self, provider_state: dict[str, Any] | None) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at - 60:
            return self._access_token
        state = {**self._configured_credentials(), **(provider_state or {})}
        app_id = str(state.get("app_id") or "")
        app_secret = str(state.get("app_secret") or "")
        if not app_id or not app_secret:
            raise ProviderUnavailable("feishu app_id/app_secret are required")
        import httpx

        async with httpx.AsyncClient(timeout=float(self._config.timeout_seconds or 10.0)) as client:
            response = await client.post(
                f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            payload = response.json()
        if int(payload.get("code") or 0) != 0:
            self._last_error_code = str(payload.get("code") or "token_failed")
            raise ProviderUnavailable(str(redact(payload.get("msg") or "feishu token failed")))
        token = str(payload.get("tenant_access_token") or "")
        if not token:
            raise ProviderUnavailable("feishu tenant_access_token missing")
        self._access_token = token
        self._access_token_expires_at = now + int(payload.get("expire") or 7200)
        return token

    async def _send_message(
        self,
        *,
        provider_state: dict[str, Any] | None,
        recipient: str,
        msg_type: str,
        content: dict[str, Any],
        delivery_kind: str,
        extra_summary: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        token = await self._tenant_access_token(provider_state)
        state = {**self._configured_credentials(), **(provider_state or {})}
        receive_id_type = str(state.get("receive_id_type") or _feishu_receive_id_type(recipient))
        import httpx

        async with httpx.AsyncClient(timeout=float(self._config.timeout_seconds or 10.0)) as client:
            response = await client.post(
                f"{self._base_url}/open-apis/im/v1/messages",
                params={"receive_id_type": receive_id_type},
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": recipient,
                    "msg_type": msg_type,
                    "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
                },
            )
            payload = response.json()
        code = int(payload.get("code") or 0)
        if code != 0:
            self._last_error_code = str(code)
            return ChannelSendResult(
                status="retryable_failure" if _feishu_retryable(code) else "rejected",
                error_code="provider_send_failed",
                error_summary=str(redact(payload.get("msg") or f"feishu code {code}")),
                response_summary={
                    "retryable": _feishu_retryable(code),
                    "delivery_kind": delivery_kind,
                    "provider_code": code,
                },
            )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        message_id = str(data.get("message_id") or data.get("message_id_v2") or "")
        return ChannelSendResult(
            status="sent",
            provider_message_id=f"feishu:{_hash_value(message_id or recipient)[:24]}",
            response_summary={
                "sdk": "feishu-openapi",
                "delivery_kind": delivery_kind,
                "delivery_format": msg_type,
                "recipient_hash": _hash_value(recipient),
                "provider_message_ref": _hash_value(message_id) if message_id else None,
                **(extra_summary or {}),
            },
        )

    async def _upload_file(
        self,
        *,
        provider_state: dict[str, Any] | None,
        file_path: Path,
        file_name: str,
        file_type: str,
    ) -> dict[str, Any]:
        token = await self._tenant_access_token(provider_state)
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        import httpx

        async with httpx.AsyncClient(timeout=float(self._config.timeout_seconds or 10.0)) as client:
            with file_path.open("rb") as fp:
                response = await client.post(
                    f"{self._base_url}/open-apis/im/v1/files",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"file_type": file_type, "file_name": file_name},
                    files={"file": (file_name, fp, mime_type)},
                )
            payload = response.json()
        if int(payload.get("code") or 0) != 0:
            raise ProviderUnavailable(str(redact(payload.get("msg") or "feishu upload failed")))
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        return data

    async def _request_message_operation(
        self,
        *,
        provider_state: dict[str, Any] | None,
        method: str,
        path: str,
        operation: str,
        provider_message_id: str | None,
        json_body: dict[str, Any] | None = None,
    ) -> ChannelSendResult:
        token = await self._tenant_access_token(provider_state)
        import httpx

        async with httpx.AsyncClient(timeout=float(self._config.timeout_seconds or 10.0)) as client:
            response = await client.request(
                method,
                f"{self._base_url}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=json_body,
            )
            payload = response.json()
        code = int(payload.get("code") or 0)
        return ChannelSendResult(
            status="sent" if code == 0 else "retryable_failure" if _feishu_retryable(code) else "rejected",
            provider_message_id=(
                f"feishu:{_hash_value(provider_message_id)[:24]}"
                if provider_message_id
                else None
            ),
            error_code=None if code == 0 else "provider_operation_failed",
            error_summary=None if code == 0 else str(redact(payload.get("msg") or code)),
            response_summary={
                "operation": operation,
                "provider_code": code,
                "retryable": _feishu_retryable(code),
                "provider_message_ref": _hash_value(provider_message_id)
                if provider_message_id
                else None,
                "response_keys": sorted(payload.keys())[:20],
            },
        )


class WechatClawbotConnector:
    provider = "wechat"

    def __init__(self, config: ChannelProviderSection, *, state_dir: Path) -> None:
        self._config = config
        self._state_dir = state_dir
        self._sessions: dict[str, dict[str, Any]] = {}
        self._client_factory: Any | None = None
        self._last_error_code: str | None = None

    def set_client_factory(self, factory: Any) -> None:
        self._client_factory = factory

    async def start_bind(
        self,
        *,
        bind_session_id: str,
        display_name_hint: str,
    ) -> ChannelBindChallenge:
        del display_name_hint
        if not self._config.enabled:
            self._last_error_code = "provider_unavailable"
            raise ProviderUnavailable("wechat provider is disabled")
        client = await self._create_client(bind_session_id)
        start_result = await _maybe_await(client.start_login())
        raw_qrcode = _pick_attr(start_result, "qrcode", "qr_code", "qr_payload")
        qr_payload = _pick_attr(
            start_result,
            "qrcode_image_content",
            "qr_image_content",
            "qr_url",
            "url",
        )
        expires_at = _pick_attr(start_result, "expires_at") or (
            utc_now() + timedelta(minutes=10)
        ).isoformat()
        status = _pick_attr(start_result, "status") or "qr_ready"
        self._sessions[bind_session_id] = {
            "client": client,
            "qrcode": raw_qrcode,
        }
        return ChannelBindChallenge(
            provider_session_id=bind_session_id,
            status=str(status),
            qr_format="text",
            qr_payload=str(qr_payload or ""),
            expires_at=str(expires_at),
            provider_status=redact({"sdk": "wechat-clawbot-sdk", "status": status}),
        )

    async def poll_bind(self, bind_session_id: str) -> ChannelProviderStatus:
        session = self._sessions.get(bind_session_id)
        if session is None:
            if not self._config.enabled:
                return ChannelProviderStatus(
                    status="failed",
                    failure_reason="provider_unavailable",
                )
            client = await self._create_client(bind_session_id)
            session = {"client": client, "qrcode": None}
            self._sessions[bind_session_id] = session
        client = session["client"]
        qrcode = session.get("qrcode")
        try:
            login_result = await _maybe_await(client.wait_for_login(qrcode, timeout=0))
        except TypeError:
            if qrcode is not None:
                login_result = await _maybe_await(client.wait_for_login(qrcode))
            else:
                login_result = await _maybe_await(client.wait_for_login())
        status = _pick_attr(login_result, "status") or "confirmed"
        account_ref = _pick_attr(
            login_result,
            "account_id",
            "user_id",
            "wxid",
            "uin",
            "account_ref",
        )
        display_name = _pick_attr(login_result, "display_name", "nickname", "name") or "微信账号"
        if account_ref:
            session["account_id"] = str(account_ref)
            session["display_name"] = str(display_name)
            session["login_status"] = str(status)
        return ChannelProviderStatus(
            status=str(status),
            provider_account_ref=str(account_ref or bind_session_id),
            display_name=str(display_name),
            provider_state=redact(
                {
                    "account_ref_redacted": _hash_value(str(account_ref or bind_session_id)),
                    "display_name": str(display_name),
                    "login_status": str(status),
                }
            ),
            confirmed_at=(
                utc_now_iso()
                if str(status) in {"confirmed", "bound", "logged_in"}
                else None
            ),
        )

    async def finalize_bind(self, bind_session_id: str) -> ChannelBoundAccount:
        session = self._sessions.get(bind_session_id)
        if session is not None and session.get("account_id"):
            provider_account_ref = str(session["account_id"])
            display_name = str(session.get("display_name") or "微信账号")
            status = ChannelProviderStatus(
                status="confirmed",
                provider_account_ref=provider_account_ref,
                display_name=display_name,
                provider_state={
                    "account_ref_redacted": _hash_value(provider_account_ref),
                    "display_name": display_name,
                    "login_status": str(session.get("login_status") or "confirmed"),
                },
                confirmed_at=utc_now_iso(),
            )
        else:
            status = await self.poll_bind(bind_session_id)
        if status.status not in {"confirmed", "bound", "logged_in"}:
            raise ProviderUnavailable(
                status.failure_reason or f"wechat bind status={status.status}"
            )
        return ChannelBoundAccount(
            provider_account_ref=status.provider_account_ref or bind_session_id,
            display_name=status.display_name or "微信账号",
            provider_state={
                **status.provider_state,
                "account_id": status.provider_account_ref or bind_session_id,
                "account_ref_redacted": _hash_value(
                    status.provider_account_ref or bind_session_id
                ),
                "bind_session_id": bind_session_id,
                "provider": self.provider,
                "state_dir": str(self._state_dir),
            },
            capabilities=[
                "message_channel",
                "notification.inbound",
                "notification.outbound",
                "approval.reply",
            ],
        )

    async def revoke(self, provider_state_ref: str | None) -> ChannelSendResult:
        del provider_state_ref
        return ChannelSendResult(status="sent", response_summary={"revoked": True})

    async def send_text(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        text: str,
    ) -> ChannelSendResult:
        if not self._config.enabled:
            return ChannelSendResult(
                status="provider_unavailable",
                error_code="provider_unavailable",
                error_summary="wechat provider is disabled",
                response_summary={"retryable": True},
            )
        try:
            client = await self._create_client(provider_state_ref or "default")
            account_id = _account_id_from_state(provider_state)
            if not account_id:
                return ChannelSendResult(
                    status="rejected",
                    error_code="provider_state_missing_account",
                    error_summary="wechat provider state does not include account_id",
                    response_summary={"retryable": False},
                )
            result = await _maybe_await(
                client.send_text(account_id=account_id, user_id=recipient, text=text)
            )
            message_id = _pick_attr(result, "message_id", "msg_id", "id")
            provider_message_ref = _hash_value(str(message_id or recipient))
            return ChannelSendResult(
                status="sent",
                provider_message_id=f"wechat:{provider_message_ref[:24]}",
                response_summary={
                    "sdk": "wechat-clawbot-sdk",
                    "provider_message_ref": provider_message_ref,
                },
            )
        except Exception as exc:
            self._last_error_code = exc.__class__.__name__
            return ChannelSendResult(
                status="retryable_failure",
                error_code="provider_send_failed",
                error_summary=str(redact(str(exc))),
                response_summary={"retryable": True},
            )

    async def send_file(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        local_path: Path,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        if not self._config.enabled:
            return ChannelSendResult(
                status="provider_unavailable",
                error_code="provider_unavailable",
                error_summary="wechat provider is disabled",
                response_summary={"retryable": True},
            )
        try:
            client = await self._create_client(provider_state_ref or "default")
            account_id = _account_id_from_state(provider_state)
            if not account_id:
                return ChannelSendResult(
                    status="rejected",
                    error_code="provider_state_missing_account",
                    error_summary="wechat provider state does not include account_id",
                    response_summary={"retryable": False},
                )
            send_file = getattr(client, "send_file", None)
            if send_file is None:
                return ChannelSendResult(
                    status="rejected",
                    error_code="message_rejected",
                    error_summary="wechat sdk does not support file outbound",
                    response_summary={"retryable": False, "reason": "file_outbound_unsupported"},
                )
            mime_type = (
                content_type
                or mimetypes.guess_type(filename or local_path.name)[0]
                or "application/octet-stream"
            )
            kwargs = {
                "account_id": account_id,
                "user_id": recipient,
                "local_path": local_path,
                "filename": filename or local_path.name,
                "mime_type": mime_type,
                "text": None,
            }
            context_token = _wechat_context_token_for_recipient(
                state_dir=self._state_dir,
                account_id=account_id,
                recipient=recipient,
            )
            try:
                result = await _maybe_await(send_file(**kwargs, context_token=context_token))
            except TypeError:
                result = await _maybe_await(send_file(**kwargs))
            message_id = _pick_attr(result, "message_id", "msg_id", "id")
            provider_message_ref = _hash_value(str(message_id or local_path.name or recipient))
            return ChannelSendResult(
                status="sent",
                provider_message_id=f"wechat_file:{provider_message_ref[:24]}",
                response_summary={
                    "sdk": "wechat-clawbot-sdk",
                    "provider_message_ref": provider_message_ref,
                    "content_type": mime_type,
                    "filename": kwargs["filename"],
                    "delivery_kind": "file",
                    "provider_raw_response": _wechat_send_response_summary(result),
                    "delivery_confirmation": (
                        "unconfirmed"
                        if _wechat_send_response_unconfirmed(result)
                        else "provider_acknowledged"
                    ),
                },
            )
        except Exception as exc:
            self._last_error_code = exc.__class__.__name__
            return ChannelSendResult(
                status="retryable_failure",
                error_code="provider_send_failed",
                error_summary=str(redact(str(exc))),
                response_summary={"retryable": True, "delivery_kind": "file"},
            )

    async def send_audio(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        audio_bytes: bytes,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> ChannelSendResult:
        if not self._config.enabled:
            return ChannelSendResult(
                status="provider_unavailable",
                error_code="provider_unavailable",
                error_summary="wechat provider is disabled",
                response_summary={"retryable": True},
            )
        try:
            client = await self._create_client(provider_state_ref or "default")
            account_id = _account_id_from_state(provider_state)
            if not account_id:
                return ChannelSendResult(
                    status="rejected",
                    error_code="provider_state_missing_account",
                    error_summary="wechat provider state does not include account_id",
                    response_summary={"retryable": False},
                )
            context_token = _wechat_context_token_for_recipient(
                state_dir=self._state_dir,
                account_id=account_id,
                recipient=recipient,
            )
            result = None
            sent = False
            delivery_format = "audio"
            voice_bubble_summary: dict[str, Any] = {}
            result, sent, voice_bubble_meta = await _send_audio_as_voice_message(
                client=client,
                account_id=account_id,
                recipient=recipient,
                audio_bytes=audio_bytes,
                content_type=content_type,
                filename=filename,
                context_token=context_token,
            )
            if not sent:
                voice_bubble_summary = {
                    "voice_bubble_delivery_confirmation": "not_sent",
                    "voice_bubble_failure_reason": voice_bubble_meta.get("failure_reason")
                    or "native_voice_bubble_unavailable_or_failed",
                }
            if sent:
                delivery_format = "voice_bubble"
                voice_bubble_summary = {
                    "voice_bubble_content_type": "audio/silk",
                    "voice_bubble_sample_rate": 24000,
                    "voice_bubble_bits_per_sample": 16,
                    "voice_bubble_playtime_ms": voice_bubble_meta.get("playtime_ms"),
                    "voice_bubble_delivery_confirmation": (
                        "unconfirmed"
                        if _wechat_send_response_unconfirmed(result)
                        else "provider_acknowledged"
                    ),
                    "voice_bubble_raw_response": _wechat_send_response_summary(result),
                }
                if _wechat_send_response_unconfirmed(result):
                    fallback_result, fallback_sent = await _send_audio_as_media_file(
                        client=client,
                        account_id=account_id,
                        recipient=recipient,
                        audio_bytes=audio_bytes,
                        content_type=content_type,
                        filename=filename,
                        context_token=context_token,
                    )
                    if fallback_sent:
                        result = fallback_result or result
                        delivery_format = "voice_bubble+media_file_fallback"
            method_names = ("send_audio", "send_voice")
            kwargs_variants = [
                {
                    "account_id": account_id,
                    "user_id": recipient,
                    "audio_bytes": audio_bytes,
                    "content_type": content_type,
                    "filename": filename,
                    "context_token": context_token,
                },
                {
                    "account_id": account_id,
                    "user_id": recipient,
                    "audio": audio_bytes,
                    "content_type": content_type,
                    "filename": filename,
                    "context_token": context_token,
                },
                {
                    "account_id": account_id,
                    "user_id": recipient,
                    "audio_bytes": audio_bytes,
                    "content_type": content_type,
                    "filename": filename,
                },
                {
                    "account_id": account_id,
                    "user_id": recipient,
                    "audio": audio_bytes,
                    "content_type": content_type,
                    "filename": filename,
                },
            ]
            if not sent:
                for method_name in method_names:
                    method = getattr(client, method_name, None)
                    if method is None:
                        continue
                    for kwargs in kwargs_variants:
                        try:
                            result = await _maybe_await(method(**kwargs))
                            sent = True
                            break
                        except TypeError:
                            continue
                    if sent:
                        break
            if not sent:
                result, sent = await _send_audio_as_media_file(
                    client=client,
                    account_id=account_id,
                    recipient=recipient,
                    audio_bytes=audio_bytes,
                    content_type=content_type,
                    filename=filename,
                    context_token=context_token,
                )
                if sent:
                    delivery_format = "media_file"
            if not sent:
                return ChannelSendResult(
                    status="rejected",
                    error_code="message_rejected",
                    error_summary="wechat sdk does not support audio outbound",
                    response_summary={"retryable": False, "reason": "audio_outbound_unsupported"},
                )
            message_id = _pick_attr(result, "message_id", "msg_id", "id")
            provider_message_ref = _hash_value(
                str(message_id or hashlib.sha256(audio_bytes).hexdigest())
            )
            return ChannelSendResult(
                status="sent",
                provider_message_id=f"wechat_audio:{provider_message_ref[:24]}",
                response_summary={
                    "sdk": "wechat-clawbot-sdk",
                    "provider_message_ref": provider_message_ref,
                    "content_type": content_type,
                    "filename": filename,
                    "audio_size_bytes": len(audio_bytes),
                    "delivery_kind": "audio",
                    "delivery_format": delivery_format,
                    "provider_raw_response": _wechat_send_response_summary(result),
                    "delivery_confirmation": (
                        "unconfirmed"
                        if _wechat_send_response_unconfirmed(result)
                        else "provider_acknowledged"
                    ),
                    **voice_bubble_summary,
                },
            )
        except Exception as exc:
            self._last_error_code = exc.__class__.__name__
            return ChannelSendResult(
                status="retryable_failure",
                error_code="provider_send_failed",
                error_summary=str(redact(str(exc))),
                response_summary={"retryable": True, "delivery_kind": "audio"},
            )

    async def poll_events(
        self,
        *,
        provider_state: dict[str, Any] | None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not self._config.enabled:
            raise ProviderUnavailable("wechat provider is disabled")
        account_id = _account_id_from_state(provider_state)
        if not account_id:
            raise ProviderUnavailable("wechat provider state does not include account_id")
        client = await self._create_client(account_id)
        events: list[dict[str, Any]] = []
        iterator = client.poll_events(account_id).__aiter__()
        poll_budget_seconds = self._poll_events_budget_seconds()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + poll_budget_seconds
        while len(events) < limit:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except TimeoutError:
                aclose = getattr(iterator, "aclose", None)
                if aclose is not None:
                    try:
                        await _maybe_await(aclose())
                    except Exception:
                        pass
                break
            except StopAsyncIteration:
                break
            events.append(_object_to_dict(event))
        return events

    def _poll_events_budget_seconds(self) -> float:
        provider_timeout = max(0.5, float(self._config.timeout_seconds or 10.0))
        poll_interval = max(0.5, float(self._config.poll_interval_seconds or 5.0))
        return max(0.5, min(provider_timeout, poll_interval, 2.0))

    async def download_media(
        self,
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        attachment: dict[str, Any],
    ) -> bytes:
        if not self._config.enabled:
            raise ProviderUnavailable("wechat provider is disabled")
        account_id = _account_id_from_state(provider_state)
        if not account_id:
            raise ProviderUnavailable("wechat provider state does not include account_id")
        inline = attachment.get("content_bytes")
        if isinstance(inline, bytes):
            return inline
        client = await self._create_client(account_id)
        ref = (
            attachment.get("media_id")
            or attachment.get("file_id")
            or attachment.get("attachment_id")
            or event.get("media_id")
            or event.get("file_id")
        )
        if ref is None:
            raise ProviderUnavailable("wechat media attachment reference is missing")
        for method_name in ("download_media", "download_file", "get_media"):
            method = getattr(client, method_name, None)
            if method is None:
                continue
            try:
                result = await _maybe_await(
                    method(account_id=account_id, media_id=ref)
                )
            except TypeError:
                result = await _maybe_await(method(ref))
            if isinstance(result, bytes):
                return result
            if isinstance(result, str):
                return result.encode("utf-8")
            data = _pick_attr(result, "content", "bytes", "data")
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                return data.encode("utf-8")
        raise ProviderUnavailable("wechat sdk does not expose media download")

    async def health(self) -> ChannelHealth:
        if not self._config.enabled:
            return ChannelHealth(
                provider=self.provider,
                enabled=False,
                reachable=False,
                login_state="disabled",
                last_error_code="provider_unavailable",
            )
        try:
            await self._load_client_class()
            session_health = self._session_health_details()
            if session_health["login_state"] != "sdk_available":
                return ChannelHealth(
                    provider=self.provider,
                    enabled=True,
                    reachable=True,
                    login_state=session_health["login_state"],
                    version=self._config.min_version,
                    last_error_code=self._last_error_code,
                    details={
                        "state_dir": str(self._state_dir),
                        **session_health["details"],
                    },
                )
            return ChannelHealth(
                provider=self.provider,
                enabled=True,
                reachable=True,
                login_state="sdk_available",
                version=self._config.min_version,
                last_error_code=self._last_error_code,
                details={"state_dir": str(self._state_dir)},
            )
        except Exception as exc:
            self._last_error_code = exc.__class__.__name__
            return ChannelHealth(
                provider=self.provider,
                enabled=True,
                reachable=False,
                login_state="sdk_unavailable",
                version=None,
                last_error_code=self._last_error_code,
                details={"reason": str(redact(str(exc)))},
            )

    async def _create_client(self, session_key: str) -> Any:
        del session_key
        client_class = await self._load_client_class()
        try:
            return await _maybe_await(client_class.create(state_dir=str(self._state_dir)))
        except TypeError:
            return await _maybe_await(client_class.create())

    async def _load_client_class(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory
        try:
            from wechat_clawbot_sdk import AsyncWeChatBotClient  # type: ignore
        except Exception as exc:
            raise ProviderUnavailable("wechat-clawbot-sdk is not installed") from exc
        return AsyncWeChatBotClient

    def _session_health_details(self) -> dict[str, Any]:
        confirmed_sessions = [
            session
            for session in self._sessions.values()
            if session.get("account_id")
            or str(session.get("login_status") or "").lower()
            in {"confirmed", "bound", "logged_in", "connected"}
        ]
        state_accounts = _wechat_state_account_refs(self._state_dir)
        if confirmed_sessions or state_accounts:
            return {
                "login_state": "logged_in",
                "details": {
                    "connection_state": "connected",
                    "confirmed_session_count": len(confirmed_sessions),
                    "persisted_account_count": len(state_accounts),
                    "account_refs_redacted": [
                        _hash_value(account_ref) for account_ref in state_accounts[:10]
                    ],
                },
            }
        return {
            "login_state": "sdk_available",
            "details": {
                "connection_state": "sdk_available",
                "confirmed_session_count": 0,
                "persisted_account_count": 0,
            },
        }


class ChannelConnectorRegistry:
    def __init__(self, connectors: list[ChannelConnector]) -> None:
        self._connectors = {connector.provider: connector for connector in connectors}

    def get(self, provider: str) -> ChannelConnector:
        connector = self._connectors.get(provider)
        if connector is None:
            raise ProviderUnavailable(f"{provider} provider is unavailable")
        return connector


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


async def _send_audio_as_voice_message(
    *,
    client: Any,
    account_id: str,
    recipient: str,
    audio_bytes: bytes,
    content_type: str | None,
    filename: str | None,
    context_token: str | None,
) -> tuple[Any, bool, dict[str, Any]]:
    if not context_token:
        return None, False, {"failure_reason": "missing_context_token"}
    api_client = getattr(client, "_api_client", None)
    message_service = getattr(client, "_message_service", None)
    cdn_base_url = getattr(message_service, "_cdn_base_url", None)
    if api_client is None or not cdn_base_url:
        return None, False, {"failure_reason": "sdk_internals_unavailable"}
    try:
        from wechat_clawbot_sdk.api.protocol import (
            MessageItemType,
            MessageState,
            MessageType,
            UploadMediaType,
        )
        from wechat_clawbot_sdk.media.transfer import (
            encode_hex_aes_key_for_message,
            prepare_upload,
        )
        from wechat_clawbot_sdk.messaging import generate_client_id
    except Exception:
        return None, False, {"failure_reason": "sdk_protocol_unavailable"}
    try:
        voice_audio = _encode_wechat_voice_silk(audio_bytes)
    except Exception as exc:
        return None, False, {"failure_reason": exc.__class__.__name__}
    with tempfile.NamedTemporaryFile(
        prefix="wechat-voice-bubble-",
        suffix=".silk",
        delete=False,
    ) as fp:
        temp_path = Path(fp.name)
        fp.write(voice_audio["audio_bytes"])
    try:
        session = await _maybe_await(client.get_account_session(account_id))
        uploaded = await prepare_upload(
            file_path=temp_path,
            to_user_id=recipient,
            media_type=int(UploadMediaType.VOICE),
            api_client=api_client,
            session=session,
            cdn_base_url=cdn_base_url,
        )
        payload = {
            "msg": {
                "from_user_id": "",
                "to_user_id": recipient,
                "client_id": generate_client_id(),
                "message_type": int(MessageType.BOT),
                "message_state": int(MessageState.FINISH),
                "item_list": [
                    {
                        "type": int(MessageItemType.VOICE),
                        "voice_item": {
                            "media": {
                                "encrypt_query_param": uploaded.download_encrypted_query_param,
                                "aes_key": encode_hex_aes_key_for_message(uploaded.aeskey_hex),
                                "encrypt_type": 1,
                            },
                            "encode_type": 6,
                            "bits_per_sample": voice_audio["bits_per_sample"],
                            "sample_rate": voice_audio["sample_rate"],
                            "playtime": voice_audio["playtime_ms"],
                        },
                    }
                ],
                "context_token": context_token,
            }
        }
        result = await _maybe_await(api_client.send_message(session, payload))
        return result, True, voice_audio
    except Exception as exc:
        voice_audio["failure_reason"] = exc.__class__.__name__
        return None, False, voice_audio
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _encode_wechat_voice_silk(audio_bytes: bytes) -> dict[str, Any]:
    try:
        import miniaudio  # type: ignore
        import pysilk  # type: ignore
    except Exception as exc:
        raise RuntimeError("wechat voice bubble encoder dependencies are not installed") from exc
    decoded = miniaudio.decode(
        audio_bytes,
        output_format=miniaudio.SampleFormat.SIGNED16,
        nchannels=1,
        sample_rate=24000,
    )
    samples = decoded.samples
    if not samples:
        raise RuntimeError("decoded audio is empty")
    pcm_bytes = struct.pack(f"<{len(samples)}h", *samples)
    output = io.BytesIO()
    pysilk.encode(io.BytesIO(pcm_bytes), output, 24000, 24000, tencent=True)
    silk_bytes = output.getvalue()
    if not silk_bytes:
        raise RuntimeError("encoded silk audio is empty")
    frame_count = int(getattr(decoded, "num_frames", 0) or len(samples))
    playtime_ms = int(round((frame_count / 24000) * 1000))
    return {
        "audio_bytes": silk_bytes,
        "content_type": "audio/silk",
        "filename": "voice.silk",
        "size_bytes": len(silk_bytes),
        "sample_rate": 24000,
        "bits_per_sample": 16,
        "playtime_ms": max(1, playtime_ms),
    }


async def _send_audio_as_media_file(
    *,
    client: Any,
    account_id: str,
    recipient: str,
    audio_bytes: bytes,
    content_type: str | None,
    filename: str | None,
    context_token: str | None,
) -> tuple[Any, bool]:
    send_file = getattr(client, "send_file", None)
    send_media = getattr(client, "send_media", None)
    build_media = getattr(client, "_build_media_payload", None)
    if send_file is None and (send_media is None or build_media is None):
        return None, False
    suffix = _audio_filename_suffix(filename, content_type)
    with tempfile.NamedTemporaryFile(prefix="wechat-voice-", suffix=suffix, delete=False) as fp:
        temp_path = Path(fp.name)
        fp.write(audio_bytes)
    try:
        if send_file is not None:
            kwargs = {
                "account_id": account_id,
                "user_id": recipient,
                "local_path": temp_path,
                "filename": filename or temp_path.name,
                "mime_type": content_type or "audio/wav",
                "text": None,
            }
            if context_token:
                try:
                    result = await _maybe_await(
                        send_file(**kwargs, context_token=context_token)
                    )
                    return result, True
                except TypeError:
                    pass
            result = await _maybe_await(send_file(**kwargs))
            return result, True
        media = build_media(
            local_path=temp_path,
            remote_url=None,
            filename=filename or temp_path.name,
            mime_type=content_type or "audio/wav",
            fallback_mime_type=content_type or "audio/wav",
        )
        result = await _maybe_await(
            send_media(
                account_id=account_id,
                user_id=recipient,
                context_token=context_token,
                media=media,
                text=None,
            )
        )
        return result, True
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _audio_filename_suffix(filename: str | None, content_type: str | None) -> str:
    suffix = Path(filename or "").suffix
    if suffix:
        return suffix[:16]
    lowered = (content_type or "").lower()
    if "mpeg" in lowered or "mp3" in lowered:
        return ".mp3"
    if "mp4" in lowered or "m4a" in lowered:
        return ".m4a"
    return ".wav"


def _voice_encode_type(content_type: str | None, filename_suffix: str | None = None) -> int:
    lowered = (content_type or "").lower()
    suffix = (filename_suffix or "").lower()
    if "silk" in lowered or suffix == ".silk":
        return 6
    if "mpeg" in lowered or "mp3" in lowered or suffix == ".mp3":
        return 7
    if "ogg" in lowered or suffix == ".ogg":
        return 8
    return 0


def _voice_audio_metadata(audio_bytes: bytes) -> dict[str, int]:
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            sample_rate = int(wav_file.getframerate() or 16000)
            bits_per_sample = int((wav_file.getsampwidth() or 2) * 8)
            frame_count = int(wav_file.getnframes() or 0)
            playtime_ms = int(round((frame_count / sample_rate) * 1000)) if sample_rate else 0
            return {
                "sample_rate": sample_rate,
                "bits_per_sample": bits_per_sample,
                "playtime_ms": max(1, playtime_ms),
            }
    except Exception:
        return {
            "sample_rate": 16000,
            "bits_per_sample": 16,
            "playtime_ms": max(1, len(audio_bytes) // 32),
        }


def _wechat_context_token_for_recipient(
    *,
    state_dir: Path,
    account_id: str,
    recipient: str,
) -> str | None:
    path = state_dir / "accounts" / f"{account_id}.context-tokens.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        token = data.get(recipient)
        if isinstance(token, str) and token:
            return token
        string_values = [value for value in data.values() if isinstance(value, str) and value]
        if len(string_values) == 1:
            return string_values[0]
    return None


def _wechat_send_response_unconfirmed(result: Any) -> bool:
    if result is None:
        return True
    if isinstance(result, dict):
        ret = result.get("ret")
        errcode = result.get("errcode")
        if isinstance(errcode, int) and errcode != 0:
            return True
        if isinstance(ret, int) and ret < 0:
            return True
        return not any(
            key in result and result.get(key) is not None
            for key in ("message_id", "msg_id", "id", "ret", "errcode")
        )
    ret = _pick_attr(result, "ret")
    errcode = _pick_attr(result, "errcode")
    if isinstance(errcode, int) and errcode != 0:
        return True
    if isinstance(ret, int) and ret < 0:
        return True
    return _pick_attr(result, "message_id", "msg_id", "id", "ret", "errcode") is None


def _wechat_send_response_summary(result: Any) -> dict[str, Any]:
    if result is None:
        return {"empty_response": True}
    data = _object_to_dict(result)
    if not data:
        return {"empty_response": True}
    allowed_keys = {
        "ret",
        "errcode",
        "errmsg",
        "message_id",
        "msg_id",
        "id",
        "status",
        "code",
        "message",
    }
    summary = {key: redact(value) for key, value in data.items() if key in allowed_keys}
    if summary:
        return summary
    return {"response_keys": sorted(data.keys())[:20]}


def _wechat_state_account_refs(state_dir: Path) -> list[str]:
    accounts_dir = state_dir / "accounts"
    if not accounts_dir.exists():
        return []
    account_refs: list[str] = []
    for path in sorted(accounts_dir.glob("*.json")):
        name = path.name
        if name.endswith(".context-tokens.json") or name.endswith(".sync.json"):
            continue
        account_ref = path.stem
        if account_ref:
            account_refs.append(account_ref)
    return account_refs


def _pick_attr(value: Any, *names: str) -> Any:
    if isinstance(value, dict):
        for name in names:
            if name in value:
                return value[name]
        return None
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _object_to_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return {str(key): _jsonable(item) for key, item in asdict(value).items()}
    if hasattr(value, "__dict__"):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    return {"value": str(value)}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {str(key): _jsonable(item) for key, item in asdict(value).items()}
    if hasattr(value, "__dict__"):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    return str(value)


def _feishu_capabilities() -> list[str]:
    return [
        "message_channel",
        "notification.inbound",
        "notification.outbound",
        "message.text",
        "message.image",
        "message.file",
        "message.audio",
        "message.card",
        "message.recall",
        "message.reaction",
        "message.read",
        "message.history",
        "message.media_upload",
        "message.media_download",
    ]


def _feishu_file_type(content_type: str | None, filename: str | None) -> str:
    lowered = (content_type or "").lower()
    suffix = Path(filename or "").suffix.lower()
    if "image" in lowered or suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image"
    if "audio" in lowered or suffix in {".mp3", ".m4a", ".wav", ".flac", ".silk", ".ogg"}:
        return "audio"
    if "video" in lowered or suffix in {".mp4", ".mov", ".mkv", ".webm"}:
        return "video"
    return "file"


def _feishu_receive_id_type(recipient: str) -> str:
    if recipient.startswith("oc_") or recipient.startswith("chat_"):
        return "chat_id"
    if recipient.startswith("ou_"):
        return "open_id"
    if recipient.startswith("on_"):
        return "union_id"
    return "chat_id"


def _message_id_from_event(event: dict[str, Any]) -> str | None:
    raw_event = event.get("event") if isinstance(event.get("event"), dict) else event
    message = raw_event.get("message") if isinstance(raw_event.get("message"), dict) else {}
    if not message and isinstance(raw_event.get("raw_event"), dict):
        nested = raw_event["raw_event"].get("event")
        if isinstance(nested, dict) and isinstance(nested.get("message"), dict):
            message = nested["message"]
    value = message.get("message_id") or raw_event.get("message_id") or event.get("message_id")
    return str(value) if value else None


def _feishu_retryable(code: int) -> bool:
    return code in {1, 2, 10002, 10003, 19001, 19006, 230020, 230024, 230025, 230028}


def _hash_value(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _account_id_from_state(provider_state: dict[str, Any] | None) -> str | None:
    if not provider_state:
        return None
    for key in ("account_id", "provider_account_ref", "user_id", "wxid", "uin"):
        value = provider_state.get(key)
        if value:
            return str(value)
    return None
