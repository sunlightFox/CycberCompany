from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from app.core.config import ChannelProviderSection, load_app_config
from app.main import create_app
from app.services.channel_connectors import FeishuMockConnector
from core_types import ChatTurnResponse
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_phase66_feishu_config_and_health_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    config = load_app_config(ROOT_DIR)

    assert "feishu" in config.channels.providers
    assert config.channels.providers["feishu"].media["transport_mode"] == "websocket"
    assert "history" in config.channels.providers["feishu"].media["capabilities"]

    with TestClient(create_app()) as client:
        health = client.get("/api/channels/providers/feishu/health")
        assert health.status_code == 200, health.text
        payload = health.json()
        assert payload["provider"] == "feishu"
        assert payload["details"]["transport_mode"] == "websocket"
        assert payload["details"]["capabilities"]

        gateway = client.get("/api/channels/providers/feishu/gateway-health")
        assert gateway.status_code == 200, gateway.text
        assert gateway.json()["transport_mode"] == "websocket"


def test_phase66_feishu_inbound_pairing_chat_delivery_and_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEISHU_APP_ID", "cli_phase66")
    monkeypatch.setenv("FEISHU_APP_SECRET", "phase66-secret")
    with TestClient(create_app()) as client:
        binding = _bind_feishu(client)
        fake = _install_fake_feishu(client)

        fake.enqueue_event(_text_event("evt-feishu-unknown", "oc_phase66", "ou_sender", "你好"))
        first = client.post("/api/channels/providers/feishu/poll-once")
        assert first.status_code == 200, first.text
        assert first.json()["processed_events"] == 1
        assert first.json()["created_pairing_requests"] == 1
        assert first.json()["chat_turns_created"] == 0

        fake.enqueue_event(_text_event("evt-feishu-unknown", "oc_phase66", "ou_sender", "你好"))
        duplicate = client.post("/api/channels/providers/feishu/poll-once")
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["duplicate_events"] == 1

        pairings = client.get(
            "/api/channels/pairing-requests",
            params={"provider": "feishu", "status": "pending"},
        )
        assert pairings.status_code == 200, pairings.text
        pairing = pairings.json()["items"][0]
        assert "oc_phase66" not in json.dumps(pairing, ensure_ascii=False)

        approved = client.post(
            f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
            json={"member_id": "mem_xiaoyao", "reason": "phase66"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["peer_session"]["provider"] == "feishu"
        assert approved.json()["peer_session"]["pairing_status"] == "paired"

        registry = cast(Any, client.app).state.registry
        captured: list[Any] = []

        async def fake_create_turn(request: Any, **_: Any) -> ChatTurnResponse:
            captured.append(request)
            return await _insert_completed_turn(
                registry,
                request,
                assistant_text="飞书这边已经接通。",
                conversation_id=request.conversation_id or "conv_phase66_feishu",
            )

        registry.feishu_gateway_service._chat.create_turn = fake_create_turn
        fake.enqueue_event(_text_event("evt-feishu-paired", "oc_phase66", "ou_sender", "请回复"))
        routed = client.post("/api/channels/providers/feishu/poll-once")
        assert routed.status_code == 200, routed.text
        assert routed.json()["chat_turns_created"] == 1
        assert captured[-1].input.text == "请回复"
        assert captured[-1].client_context.ui_mode == "feishu_chat"

        delivered = client.post("/api/channels/providers/feishu/deliver-due")
        assert delivered.status_code == 200, delivered.text
        assert delivered.json()["deliveries_sent"] == 1
        assert fake.sent_text[-1]["recipient"] == "oc_phase66"
        assert fake.sent_text[-1]["text"] == "飞书这边已经接通。"

        recall = client.post(
            "/api/channels/providers/feishu/operation",
            params={"operation": "recall"},
            json={
                "channel_account_id": binding["channel_account_id"],
                "message_id": "om_phase66_secret",
            },
        )
        assert recall.status_code == 200, recall.text
        assert recall.json()["status"] == "sent"

        reaction = client.post(
            "/api/channels/providers/feishu/operation",
            params={"operation": "reaction"},
            json={
                "channel_account_id": binding["channel_account_id"],
                "message_id": "om_phase66_secret",
                "emoji_type": "OK",
            },
        )
        assert reaction.status_code == 200, reaction.text
        assert reaction.json()["response_summary"]["operation"] == "reaction"

        history = client.post(
            "/api/channels/providers/feishu/operation",
            params={"operation": "history"},
            json={
                "channel_account_id": binding["channel_account_id"],
                "container_id": "oc_phase66",
                "container_id_type": "chat",
                "page_size": 10,
            },
        )
        assert history.status_code == 200, history.text
        assert history.json()["response_summary"]["operation"] == "history"

        async def list_operations() -> list[dict[str, Any]]:
            return await registry.channels.list_feishu_message_operations(
                channel_account_id=binding["channel_account_id"],
                limit=10,
            )

        operations = cast(Any, client).portal.call(list_operations)
        assert {item["operation"] for item in operations} >= {"recall", "reaction", "history"}
        assert "phase66-secret" not in json.dumps(
            client.get("/api/channels/providers/feishu/gateway-health").json()
        )


class _FeishuTestConnector(FeishuMockConnector):
    provider = "feishu"

    def __init__(self) -> None:
        super().__init__(ChannelProviderSection(enabled=True, poll_enabled=True))
        self.sent_text: list[dict[str, Any]] = []

    async def send_text(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        text: str,
    ):
        self.sent_text.append({"recipient": recipient, "text": text})
        return await super().send_text(
            provider_state_ref=provider_state_ref,
            provider_state=provider_state,
            recipient=recipient,
            text=text,
        )


def _bind_feishu(client: TestClient) -> dict[str, Any]:
    started = client.post(
        "/api/channels/bind-sessions",
        json={
            "provider": "feishu",
            "requested_by_member_id": "mem_xiaoyao",
            "display_name_hint": "飞书机器人",
        },
    )
    assert started.status_code == 200, started.text
    started_payload = started.json()
    assert started_payload["status"] == "qr_ready"
    assert started_payload["qr"]["format"] == "url"
    assert started_payload["qr"]["data"].startswith(
        "https://open.feishu.cn/open-apis/authen/v1/index?"
    )
    assert "state=" in started_payload["qr"]["data"]
    callback = client.get(
        "/api/channels/inbound/feishu/bind-callback",
        params={
            "state": started_payload["bind_session_id"],
            "code": "phase66-oauth-code",
            "tenant_key": "tenant_phase66_secret",
            "open_id": "ou_phase66_secret",
        },
    )
    assert callback.status_code == 200, callback.text
    assert callback.json()["status"] == "confirmed"
    status = client.get(f"/api/channels/bind-sessions/{started_payload['bind_session_id']}")
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "confirmed"
    finalized = client.post(f"/api/channels/bind-sessions/{started_payload['bind_session_id']}/finalize")
    assert finalized.status_code == 200, finalized.text
    serialized = json.dumps(finalized.json(), ensure_ascii=False)
    assert "phase66-oauth-code" not in serialized
    assert "tenant_phase66_secret" not in serialized
    assert "ou_phase66_secret" not in serialized
    return finalized.json()["account"]


def _install_fake_feishu(client: TestClient) -> _FeishuTestConnector:
    registry = cast(Any, client.app).state.registry
    fake = _FeishuTestConnector()
    registry.channel_binding_service.connector_registry()._connectors["feishu"] = fake
    return fake


def _text_event(event_id: str, chat_id: str, sender_id: str, text: str) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "create_time": "2026-05-04T00:00:00+08:00",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": sender_id},
                "sender_type": "user",
            },
            "message": {
                "message_id": f"om_{event_id}",
                "chat_id": chat_id,
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
        },
    }


async def _insert_completed_turn(
    registry: Any,
    request: Any,
    *,
    assistant_text: str,
    conversation_id: str,
) -> ChatTurnResponse:
    turn_id = "turn_phase66_feishu"
    user_message_id = "msg_phase66_user"
    assistant_message_id = "msg_phase66_assistant"
    now = "2026-05-04T00:00:00+00:00"
    existing = await registry.chat.get_conversation(conversation_id)
    if existing is None:
        await registry.chat.create_conversation(
            conversation_id=conversation_id,
            organization_id="org_default",
            title="飞书消息",
            primary_member_id=request.member_id,
            participants=[
                {"type": "user", "id": "user_local_owner"},
                {"type": "member", "id": request.member_id},
            ],
            created_at=now,
        )
    await registry.chat.insert_message(
        message_id=user_message_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        author_type="user",
        author_id="user_local_owner",
        content_type="text",
        content_text=request.input.text,
        content={"text": request.input.text},
        trace_id="trc_phase66_feishu",
        created_at=now,
    )
    await registry.chat.insert_message(
        message_id=assistant_message_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        author_type="assistant",
        author_id=request.member_id,
        content_type="text",
        content_text=assistant_text,
        content={"text": assistant_text},
        trace_id="trc_phase66_feishu",
        created_at=now,
    )
    await registry.chat.insert_turn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        member_id=request.member_id,
        user_message_id=user_message_id,
        trace_id="trc_phase66_feishu",
        status="created",
        retry_of_turn_id=None,
        created_at=now,
    )
    await registry.chat.update_turn(
        turn_id,
        assistant_message_id=assistant_message_id,
        status="completed",
        updated_at=now,
        ended_at=now,
    )
    return ChatTurnResponse(
        turn_id=turn_id,
        conversation_id=conversation_id,
        message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        trace_id="trc_phase66_feishu",
        status="completed",
    )
