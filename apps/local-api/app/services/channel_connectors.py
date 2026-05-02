from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol

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
                status="failed",
                error_code="provider_unavailable",
                error_summary="wechat provider is disabled",
            )
        try:
            client = await self._create_client(provider_state_ref or "default")
            account_id = _account_id_from_state(provider_state)
            if not account_id:
                return ChannelSendResult(
                    status="failed",
                    error_code="provider_state_missing_account",
                    error_summary="wechat provider state does not include account_id",
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
                status="failed",
                error_code="provider_send_failed",
                error_summary=str(redact(str(exc))),
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
        per_tick_timeout = max(1.0, float(self._config.timeout_seconds or 10.0))
        while len(events) < limit:
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=per_tick_timeout)
            except TimeoutError:
                break
            except StopAsyncIteration:
                break
            events.append(_object_to_dict(event))
        return events

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
