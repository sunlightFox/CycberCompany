from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import anyio
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
        assert "duplicate_turn" in duplicate.json()["taxonomy"]
        assert "duplicate_inbound_suppressed" in duplicate.json()["failure_reason_codes"]

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

        async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
            request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
            captured.append(request)
            return await _insert_completed_turn(
                registry,
                request,
                assistant_text="飞书这边已经接通。",
                conversation_id=request.conversation_id or "conv_phase66_feishu",
            )

        registry.feishu_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
        fake.enqueue_event(_text_event("evt-feishu-paired", "oc_phase66", "ou_sender", "请回复"))
        routed = client.post("/api/channels/providers/feishu/poll-once")
        assert routed.status_code == 200, routed.text
        assert routed.json()["chat_turns_created"] == 1
        assert routed.json()["reliability_status"] == "ok"
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


def test_phase66_feishu_deliver_due_retries_briefly_when_turn_is_not_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEISHU_APP_ID", "cli_phase66_retry")
    monkeypatch.setenv("FEISHU_APP_SECRET", "phase66-retry-secret")
    with TestClient(create_app()) as client:
        registry = cast(Any, client.app).state.registry
        service = registry.feishu_gateway_service
        binding = {
            "channel_delivery_binding_id": "chdel_phase66_retry",
            "turn_id": "turn_phase66_retry",
            "channel_peer_session_id": "chps_phase66_retry",
            "status": "pending",
        }
        attempts = 0

        async def fake_list_delivery_bindings(**_: Any) -> list[dict[str, Any]]:
            return [binding]

        async def fake_deliver_binding(
            item: dict[str, Any],
            *,
            trace_id: str | None,
        ) -> bool | None:
            nonlocal attempts
            assert item["channel_delivery_binding_id"] == "chdel_phase66_retry"
            assert trace_id == "trc_phase66_retry"
            attempts += 1
            return True if attempts == 2 else None

        monkeypatch.setattr(service._repo, "list_delivery_bindings", fake_list_delivery_bindings)
        monkeypatch.setattr(service, "_deliver_binding", fake_deliver_binding)

        async def run_delivery() -> Any:
            return await service.deliver_due(trace_id="trc_phase66_retry")

        response = anyio.run(run_delivery)
        assert response.deliveries_sent == 1
        assert response.failures == 0
        assert attempts == 2


def test_phase117_feishu_adjacent_file_events_are_coalesced_and_understood(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEISHU_APP_ID", "cli_phase117")
    monkeypatch.setenv("FEISHU_APP_SECRET", "phase117-secret")
    with TestClient(create_app()) as client:
        _bind_feishu(client)
        fake = _install_fake_feishu(client)

        fake.enqueue_event(_text_event("evt-feishu-pair-117", "oc_phase117", "ou_sender", "你好"))
        first = client.post("/api/channels/providers/feishu/poll-once")
        assert first.status_code == 200, first.text
        pairing = client.get(
            "/api/channels/pairing-requests",
            params={"provider": "feishu", "status": "pending"},
        ).json()["items"][0]
        approved = client.post(
            f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
            json={"member_id": "mem_xiaoyao", "reason": "phase117"},
        )
        assert approved.status_code == 200, approved.text

        fake.register_blob("file_phase117_a", "青藤计划 12800 Beta供应商 6月15日 陈澈".encode("utf-8"))
        fake.register_blob("file_phase117_b", "补充附件：青藤计划需要合并归纳。".encode("utf-8"))
        registry = cast(Any, client.app).state.registry
        captured: list[Any] = []

        async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
            request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
            captured.append(request)
            return await _insert_completed_turn(
                registry,
                request,
                assistant_text="已读取两个附件并合并归纳。",
                conversation_id=request.conversation_id or "conv_phase117_feishu",
            )

        registry.feishu_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
        fake.enqueue_event(
            _file_event(
                "evt-feishu-file-117-a",
                "oc_phase117",
                "ou_sender",
                "请合并总结两个附件",
                file_key="file_phase117_a",
                file_name="a.txt",
                content_type="text/plain",
            )
        )
        fake.enqueue_event(
            _file_event(
                "evt-feishu-file-117-b",
                "oc_phase117",
                "ou_sender",
                "补充附件",
                file_key="file_phase117_b",
                file_name="b.txt",
                content_type="text/plain",
            )
        )

        routed = client.post("/api/channels/providers/feishu/poll-once")
        assert routed.status_code == 200, routed.text
        assert routed.json()["chat_turns_created"] == 1
        assert routed.json()["media_attachments"] == 2
        assert len(captured) == 1
        request = captured[-1]
        assert len(request.attachments) == 2
        assert request.ingress_metadata.channel_message_id.startswith("coalesced:")
        assert request.ingress_metadata.raw_payload["attachment_count"] == 2
        assert request.ingress_metadata.raw_payload["multimodal_understanding"][
            "understood_attachment_count"
        ] == 2
        safe_text = "\n".join(part.text or "" for part in request.input.content_parts)
        assert "青藤计划" in safe_text
        assert "12800" in safe_text
        assert "Beta供应商" in safe_text


def test_phase117_feishu_pdf_understanding_keeps_canonical_attachment_terms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEISHU_APP_ID", "cli_phase117_pdf")
    monkeypatch.setenv("FEISHU_APP_SECRET", "phase117-pdf-secret")
    with TestClient(create_app()) as client:
        _bind_feishu(client)
        fake = _install_fake_feishu(client)

        fake.enqueue_event(_text_event("evt-feishu-pair-117-pdf", "oc_phase117_pdf", "ou_sender", "你好"))
        first = client.post("/api/channels/providers/feishu/poll-once")
        assert first.status_code == 200, first.text
        pairing = client.get(
            "/api/channels/pairing-requests",
            params={"provider": "feishu", "status": "pending"},
        ).json()["items"][0]
        approved = client.post(
            f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
            json={"member_id": "mem_xiaoyao", "reason": "phase117-pdf"},
        )
        assert approved.status_code == 200, approved.text

        fake.register_blob("file_phase117_pdf", _minimal_pdf_bytes())
        registry = cast(Any, client.app).state.registry
        captured: list[Any] = []

        async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
            request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
            captured.append(request)
            return await _insert_completed_turn(
                registry,
                request,
                assistant_text="已读取附件并总结。",
                conversation_id=request.conversation_id or "conv_phase117_feishu_pdf",
            )

        registry.feishu_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
        fake.enqueue_event(
            _file_event(
                "evt-feishu-file-117-pdf",
                "oc_phase117_pdf",
                "ou_sender",
                "请阅读附件并总结成三点，必须只基于附件。",
                file_key="file_phase117_pdf",
                file_name="qingting-plan.pdf",
                content_type="application/pdf",
            )
        )

        routed = client.post("/api/channels/providers/feishu/poll-once")
        assert routed.status_code == 200, routed.text
        assert routed.json()["chat_turns_created"] == 1
        assert routed.json()["media_attachments"] == 1
        request = captured[-1]
        safe_text = "\n".join(part.text or "" for part in request.input.content_parts)
        assert "标准事实" in safe_text
        assert "青藤计划" in safe_text
        assert "12800" in safe_text
        assert "Beta供应商" in safe_text
        assert "6月15日" in safe_text
        assert "陈澈" in safe_text


class _FeishuTestConnector(FeishuMockConnector):
    provider = "feishu"

    def __init__(self) -> None:
        super().__init__(ChannelProviderSection(enabled=True, poll_enabled=True))
        self.sent_text: list[dict[str, Any]] = []
        self._blobs: dict[str, bytes] = {}

    def register_blob(self, key: str, content: bytes) -> None:
        self._blobs[key] = content

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

    async def download_media(
        self,
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        attachment: dict[str, Any],
    ) -> bytes:
        key = str(attachment.get("file_key") or attachment.get("media_id") or "")
        if key in self._blobs:
            return self._blobs[key]
        return await super().download_media(
            provider_state=provider_state,
            event=event,
            attachment=attachment,
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


def _file_event(
    event_id: str,
    chat_id: str,
    sender_id: str,
    text: str,
    *,
    file_key: str,
    file_name: str,
    content_type: str,
) -> dict[str, Any]:
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
                "message_type": "file",
                "content": json.dumps(
                    {
                        "text": text,
                        "file_key": file_key,
                        "file_name": file_name,
                        "content_type": content_type,
                    },
                    ensure_ascii=False,
                ),
            },
        },
    }


def _minimal_pdf_bytes() -> bytes:
    stream = (
        "BT /F1 13 Tf 72 730 Td (Qingteng Plan) Tj "
        "0 -22 Td (Budget 12800 CNY) Tj "
        "0 -22 Td (Risk Beta supplier delay) Tj "
        "0 -22 Td (Deadline June 15) Tj "
        "0 -22 Td (Owner Chen Che) Tj ET"
    )
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
        f"5 0 obj << /Length {len(stream.encode('ascii'))} >> stream\n{stream}\nendstream endobj\n".encode(
            "ascii"
        ),
    ]
    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for obj in objects:
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(obj)
    xref_offset = sum(len(chunk) for chunk in chunks)
    xref = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    xref.extend(f"{offset:010d} 00000 n \n".encode("ascii") for offset in offsets[1:])
    chunks.extend(xref)
    chunks.append(b"trailer << /Size 6 /Root 1 0 R >>\n")
    chunks.append(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))
    return b"".join(chunks)


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
