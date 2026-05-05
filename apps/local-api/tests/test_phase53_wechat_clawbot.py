from __future__ import annotations

import json
from typing import Any, ClassVar, cast

from fastapi.testclient import TestClient


def test_phase53_real_wechat_provider_uses_clawbot_sdk_contract(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, FakeWechatClient)

    started = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": "真实微信"},
    )
    assert started.status_code == 200, started.text
    start_payload = started.json()
    assert start_payload["status"] == "qr_ready"
    assert start_payload["qr"]["data"] == "QR_IMAGE_VISIBLE"
    assert "RAW_QR_TICKET_TOKEN" not in json.dumps(start_payload, ensure_ascii=False)

    status = client.get(f"/api/channels/bind-sessions/{start_payload['bind_session_id']}")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "confirmed"

    finalized = client.post(
        f"/api/channels/bind-sessions/{start_payload['bind_session_id']}/finalize"
    )
    assert finalized.status_code == 200, finalized.text
    payload = finalized.json()
    assert payload["account"]["provider"] == "wechat"
    assert payload["account"]["account_ref_redacted"].startswith("sha256:")
    assert "wxid-real-secret" not in json.dumps(payload, ensure_ascii=False)
    assert FakeWechatClient.create_kwargs
    assert FakeWechatClient.wait_qrcodes == ["RAW_QR_TICKET_TOKEN"]


def test_phase53_wechat_send_text_records_attempt_and_never_fakes_sent(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, FakeWechatClient)
    bound = _bind_real_wechat(client)

    sent = client.post(
        "/api/notification/messages",
        json={
            "channel_id": bound["channel_id"],
            "message_type": "task_completed",
            "recipient": "wxid-recipient-secret",
            "subject": "完成",
            "body": "报告已经完成。",
        },
    )
    assert sent.status_code == 200, sent.text
    sent_payload = sent.json()
    assert sent_payload["status"] == "sent"
    assert sent_payload["provider_message_id"].startswith("wechat:sha256:")
    attempts = client.get(
        f"/api/notification/messages/{sent_payload['notification_id']}/attempts"
    )
    assert attempts.status_code == 200, attempts.text
    assert attempts.json()["items"][0]["status"] == "sent"
    assert FakeWechatClient.send_calls == [
        {
            "account_id": "wxid-real-secret",
            "user_id": "wxid-recipient-secret",
            "text": "报告已经完成。",
        }
    ]
    assert "msg-secret-id" not in json.dumps(sent_payload, ensure_ascii=False)

    _install_fake_wechat(client, FailingSendWechatClient)
    bound = _bind_real_wechat(client)
    failed = client.post(
        "/api/notification/messages",
        json={
            "channel_id": bound["channel_id"],
            "message_type": "task_failed",
            "recipient": "wxid-recipient-secret",
            "subject": "失败",
            "body": "发送失败验证。",
        },
    )
    assert failed.status_code == 200, failed.text
    failed_payload = failed.json()
    assert failed_payload["status"] == "failed"
    assert "send-secret" not in json.dumps(failed_payload, ensure_ascii=False)
    failed_attempts = client.get(
        f"/api/notification/messages/{failed_payload['notification_id']}/attempts"
    ).json()["items"]
    assert failed_attempts[0]["status"] == "retryable_failure"
    assert failed_attempts[0]["error_code"] == "provider_send_failed"
    assert "send-secret" not in json.dumps(failed_attempts, ensure_ascii=False)


def test_phase53_wechat_provider_unavailable_and_abnormal_status_do_not_create_assets(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.config.channels.providers["wechat"].enabled = False
    disabled = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": "禁用微信"},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["status"] == "failed"
    assert disabled.json()["failure_reason"] == "provider_unavailable"
    assert disabled.json()["qr"] == {}
    assert client.get("/api/channels/accounts", params={"provider": "wechat"}).json()[
        "items"
    ] == []

    _install_fake_wechat(client, UnavailableWechatClient)
    unavailable = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": "不可用微信"},
    )
    assert unavailable.status_code == 200, unavailable.text
    assert unavailable.json()["status"] == "failed"
    assert unavailable.json()["failure_reason"] == "provider_unavailable"

    _install_fake_wechat(client, WaitingWechatClient)
    started = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": "等待微信"},
    ).json()
    finalize = client.post(f"/api/channels/bind-sessions/{started['bind_session_id']}/finalize")
    assert finalize.status_code == 503, finalize.text
    assert finalize.json()["error"]["code"] == "MCP_UNAVAILABLE"
    assert client.get("/api/channels/accounts", params={"provider": "wechat"}).json()[
        "items"
    ] == []


def test_phase53_wechat_health_reports_disabled_and_enabled_sdk(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    registry.config.channels.providers["wechat"].enabled = False
    disabled = client.get("/api/channels/providers/wechat/health")
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert disabled.json()["login_state"] == "disabled"

    _install_fake_wechat(client, FakeWechatClient)
    enabled = client.get("/api/channels/providers/wechat/health")
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert enabled.json()["reachable"] is True
    assert enabled.json()["login_state"] in {"sdk_available", "logged_in"}

    _bind_real_wechat(client)
    connected = client.get("/api/channels/providers/wechat/health")
    assert connected.status_code == 200, connected.text
    assert connected.json()["login_state"] == "logged_in"
    assert connected.json()["details"]["connection_state"] == "connected"


class FakeWechatClient:
    create_kwargs: ClassVar[list[dict[str, Any]]] = []
    wait_qrcodes: ClassVar[list[str]] = []
    send_calls: ClassVar[list[dict[str, str]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.create_kwargs = []
        cls.wait_qrcodes = []
        cls.send_calls = []

    @classmethod
    def create(cls, **kwargs: Any) -> FakeWechatClient:
        cls.create_kwargs.append(dict(kwargs))
        return cls()

    async def start_login(self) -> dict[str, Any]:
        return {
            "status": "qr_ready",
            "qrcode": "RAW_QR_TICKET_TOKEN",
            "qrcode_image_content": "QR_IMAGE_VISIBLE",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }

    async def wait_for_login(
        self,
        qrcode: str,
        timeout: float | int | None = None,
    ) -> dict[str, Any]:
        del timeout
        self.__class__.wait_qrcodes.append(qrcode)
        return {
            "status": "confirmed",
            "account_id": "wxid-real-secret",
            "display_name": "真实微信",
        }

    async def send_text(self, *, account_id: str, user_id: str, text: str) -> dict[str, Any]:
        self.__class__.send_calls.append(
            {"account_id": account_id, "user_id": user_id, "text": text}
        )
        return {"message_id": "msg-secret-id"}


class FailingSendWechatClient(FakeWechatClient):
    async def send_text(self, *, account_id: str, user_id: str, text: str) -> dict[str, Any]:
        del account_id, user_id, text
        raise RuntimeError("provider failed token=send-secret")


class WaitingWechatClient(FakeWechatClient):
    async def wait_for_login(
        self,
        qrcode: str,
        timeout: float | int | None = None,
    ) -> dict[str, Any]:
        del qrcode, timeout
        return {"status": "waiting"}


class UnavailableWechatClient(FakeWechatClient):
    @classmethod
    def create(cls, **kwargs: Any) -> FakeWechatClient:
        del kwargs
        from app.services.channel_connectors import ProviderUnavailable

        raise ProviderUnavailable("sdk unavailable token=unavailable-secret")


def _install_fake_wechat(client: TestClient, factory: type[FakeWechatClient]) -> None:
    factory.reset()
    registry = cast(Any, client.app).state.registry
    registry.config.channels.providers["wechat"].enabled = True
    connector = registry.channel_binding_service.connector_registry().get("wechat")
    cast(Any, connector).set_client_factory(factory)


def _bind_real_wechat(client: TestClient) -> dict[str, str]:
    started = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": "真实微信"},
    )
    assert started.status_code == 200, started.text
    finalized = client.post(
        f"/api/channels/bind-sessions/{started.json()['bind_session_id']}/finalize"
    )
    assert finalized.status_code == 200, finalized.text
    payload = finalized.json()
    return {
        "channel_id": payload["channel"]["channel_id"],
        "channel_account_id": payload["account"]["channel_account_id"],
    }
