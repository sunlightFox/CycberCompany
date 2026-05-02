from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, cast

from core_types import ChatTurnResponse
from fastapi.testclient import TestClient


def test_phase54_wechat_gateway_pairing_idempotency_and_reply_once(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)

    GatewayWechatClient.events = [
        _text_event("evt-unknown", "wxid-phase54-peer-secret", "你好")
    ]
    first = client.post("/api/channels/providers/wechat/poll-once")
    assert first.status_code == 200, first.text
    assert first.json()["created_pairing_requests"] == 1
    assert first.json()["chat_turns_created"] == 0
    assert GatewayWechatClient.send_calls[-1]["user_id"] == "wxid-phase54-peer-secret"

    duplicate = client.post("/api/channels/providers/wechat/poll-once")
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["duplicate_events"] == 1

    pairings = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    )
    assert pairings.status_code == 200, pairings.text
    pairing = pairings.json()["items"][0]
    serialized_pairing = json.dumps(pairing, ensure_ascii=False)
    assert pairing["peer_ref_redacted"].startswith("sha256:")
    assert "wxid-phase54-peer-secret" not in serialized_pairing

    approved = client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaoyao", "reason": "phase54"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["peer_session"]["pairing_status"] == "paired"

    registry = cast(Any, client.app).state.registry
    registry.wechat_gateway_service._blob_dir.mkdir(parents=True, exist_ok=True)

    captured: list[Any] = []

    async def fake_create_turn(request: Any, **_: Any) -> ChatTurnResponse:
        captured.append(request)
        return await _insert_completed_turn(
            registry,
            request,
            assistant_text="能收到，我们可以直接像聊天一样继续。",
            conversation_id=request.conversation_id or "conv_phase54_wechat",
        )

    registry.wechat_gateway_service._chat.create_turn = fake_create_turn
    GatewayWechatClient.events = [
        _text_event("evt-paired", "wxid-phase54-peer-secret", "你能收到吗")
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1
    assert captured[-1].input.text == "你能收到吗"
    assert captured[-1].client_context.ui_mode == "wechat_chat"
    assert "不可信" not in captured[-1].input.text
    assert "审批" not in captured[-1].input.text
    assert "外部微信消息" not in captured[-1].input.text
    _run_async(
        client,
        _wait_until,
        lambda: [
            item
            for item in GatewayWechatClient.send_calls
            if item["text"] == "能收到，我们可以直接像聊天一样继续。"
        ],
        timeout=3.0,
    )
    again = client.post("/api/channels/providers/wechat/deliver-due")
    assert again.status_code == 200, again.text
    assert again.json()["deliveries_sent"] == 0
    reply_calls = [
        item
        for item in GatewayWechatClient.send_calls
        if item["text"] == "能收到，我们可以直接像聊天一样继续。"
    ]
    assert len(reply_calls) == 1
    assert reply_calls[0]["user_id"] == "wxid-phase54-peer-secret"
    GatewayWechatClient.events = [
        _text_event("evt-paired-2", "wxid-phase54-peer-secret", "继续聊")
    ]
    second = client.post("/api/channels/providers/wechat/poll-once")
    assert second.status_code == 200, second.text
    assert second.json()["chat_turns_created"] == 1
    assert captured[-1].conversation_id == "conv_phase54_wechat"
    assert captured[-1].input.text == "继续聊"
    assert "wxid-phase54-peer-secret" not in json.dumps(
        client.get("/api/channels/peers").json(),
        ensure_ascii=False,
    )


def test_phase54_wechat_gateway_fail_closed_and_media_degraded(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)

    GatewayWechatClient.events = [
        _text_event("evt-group", "room-phase54-secret", "群消息", chat_type="group"),
        _text_event("evt-blocked", "wxid-blocked-secret", "未配对消息"),
    ]
    result = client.post("/api/channels/providers/wechat/poll-once")
    assert result.status_code == 200, result.text
    assert result.json()["rejected_events"] >= 2
    assert result.json()["chat_turns_created"] == 0

    pairing = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"][0]
    approved = client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaoyao"},
    ).json()
    peer_id = approved["peer_session"]["channel_peer_session_id"]
    revoked = client.post(
        f"/api/channels/peers/{peer_id}/revoke",
        json={"member_id": "mem_xiaoyao"},
    )
    assert revoked.status_code == 200, revoked.text

    GatewayWechatClient.events = [
        _text_event("evt-revoked", "wxid-blocked-secret", "撤销后不应进入")
    ]
    blocked = client.post("/api/channels/providers/wechat/poll-once")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["chat_turns_created"] == 0

    _pair_peer(client, "wxid-media-secret")
    captured: list[Any] = []

    async def fake_create_turn(request: Any, **_: Any) -> ChatTurnResponse:
        captured.append(request)
        return await _insert_completed_turn(cast(Any, client.app).state.registry, request)

    cast(Any, client.app).state.registry.wechat_gateway_service._chat.create_turn = fake_create_turn
    GatewayWechatClient.events = [
        {
            "event_id": "evt-image",
            "source": {"peer_ref": "wxid-media-secret", "chat_type": "private"},
            "message": {
                "content_type": "image",
                "text": "",
                "attachments": [
                    {
                        "media_id": "image-secret-ref",
                        "type": "image",
                        "content_type": "image/png",
                        "name": "secret-image.png",
                    }
                ],
            },
        },
        {
            "event_id": "evt-audio",
            "source": {"peer_ref": "wxid-media-secret", "chat_type": "private"},
            "message": {
                "content_type": "audio",
                "attachments": [
                    {
                        "media_id": "audio-secret-ref",
                        "type": "audio",
                        "content_type": "audio/wav",
                        "name": "secret-audio.wav",
                    }
                ],
            },
        },
    ]
    media = client.post("/api/channels/providers/wechat/poll-once")
    assert media.status_code == 200, media.text
    assert media.json()["media_attachments"] == 2, media.text
    attachments = client.get("/api/channels/attachments")
    assert attachments.status_code == 200, attachments.text
    payload = attachments.json()["items"]
    ready_media = {
        item["attachment_type"]: item
        for item in payload
        if item["attachment_type"] in {"image", "audio"} and item["blob_ref"]
    }
    assert set(ready_media) == {"image", "audio"}
    assert ready_media["image"]["status"] == "ready"
    assert ready_media["image"]["blob_ref"].startswith("channel-attachment://wechat/")
    assert ready_media["image"]["media_id"]
    assert ready_media["audio"]["status"] == "degraded"
    assert ready_media["audio"]["blob_ref"].startswith("channel-attachment://wechat/")
    assert ready_media["audio"]["media_id"]
    assert ready_media["audio"]["metadata"]["transcription_status"] == "degraded"
    assert all(item["provider_attachment_ref_redacted"].startswith("sha256:") for item in payload)
    assert "image-secret-ref" not in json.dumps(payload, ensure_ascii=False)
    assert captured
    assert "不可信" not in captured[-1].input.text
    assert captured[-1].client_context.ui_mode == "wechat_chat"


def test_phase54_wechat_worker_tick_receives_and_delivers_natural_reply(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-worker-secret")
    registry = cast(Any, client.app).state.registry
    captured: list[Any] = []

    async def fake_create_turn(request: Any, **_: Any) -> ChatTurnResponse:
        captured.append(request)
        return await _insert_completed_turn(
            registry,
            request,
            assistant_text="在的，这条微信我收到了。",
            conversation_id=request.conversation_id or "conv_phase54_worker",
        )

    registry.wechat_gateway_service._chat.create_turn = fake_create_turn
    GatewayWechatClient.events = [
        _text_event("evt-worker-natural", "wxid-worker-secret", "在吗")
    ]
    tick = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "wechat_inbound_worker"},
    )
    assert tick.status_code == 200, tick.text
    result = tick.json()["results"]["wechat_inbound_worker"]
    assert result["status"] == "healthy"
    assert result["inbound"]["chat_turns_created"] == 1
    assert result["outbound"]["deliveries_sent"] == 1
    assert captured[-1].input.text == "在吗"
    assert GatewayWechatClient.send_calls[-1]["text"] == "在的，这条微信我收到了。"
    serialized = json.dumps(tick.json(), ensure_ascii=False)
    assert "wxid-worker-secret" not in serialized
    assert "QR_RAW_PHASE54" not in serialized


def test_phase54_wechat_immediate_delivery_after_turn_completion(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-immediate-secret")
    registry = cast(Any, client.app).state.registry
    captured: list[Any] = []

    async def fake_create_turn(request: Any, **_: Any) -> ChatTurnResponse:
        captured.append(request)
        return await _insert_running_turn(
            registry,
            request,
            conversation_id=request.conversation_id or "conv_phase54_immediate",
        )

    registry.wechat_gateway_service._chat.create_turn = fake_create_turn
    GatewayWechatClient.events = [
        _text_event("evt-immediate-natural", "wxid-immediate-secret", "快一点回复")
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1
    assert captured[-1].input.text == "快一点回复"

    bindings = client.get("/api/channels/delivery-bindings", params={"status": "pending"})
    binding = bindings.json()["items"][0]
    _run_async(
        client,
        _complete_turn,
        registry,
        binding["turn_id"],
        assistant_text="我已经尽快回你了。",
    )
    _run_async(
        client,
        _wait_until,
        lambda: [
            item for item in GatewayWechatClient.send_calls if item["text"] == "我已经尽快回你了。"
        ],
        timeout=3.0,
    )

    assert GatewayWechatClient.send_calls[-1]["text"] == "我已经尽快回你了。"
    assert GatewayWechatClient.send_calls[-1]["user_id"] == "wxid-immediate-secret"
    again = client.post("/api/channels/providers/wechat/deliver-due")
    assert again.status_code == 200, again.text
    assert again.json()["deliveries_sent"] == 0
    health = client.get("/api/channels/providers/wechat/gateway-health")
    assert health.status_code == 200, health.text
    immediate = health.json()["immediate_delivery"]
    assert immediate["watchers_started"] >= 1
    assert immediate["watchers_delivered"] >= 1
    assert immediate["last_delivery_latency_ms"] is not None
    assert "wxid-immediate-secret" not in json.dumps(health.json(), ensure_ascii=False)


def test_phase54_wechat_paired_private_host_filesystem_list_uses_real_chat_chain(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-host-fs-secret")
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    (desktop / "wechat-alpha.txt").write_text("content must not leak", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))

    GatewayWechatClient.events = [
        _text_event("evt-host-fs-list", "wxid-host-fs-secret", "我桌面有哪些文件")
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1
    _run_async(
        client,
        _wait_until,
        lambda: [
            item for item in GatewayWechatClient.send_calls if "wechat-alpha.txt" in item["text"]
        ],
        timeout=5.0,
    )
    reply = GatewayWechatClient.send_calls[-1]["text"]
    assert "wechat-alpha.txt" in reply
    assert "目标文件或范围" not in reply
    assert "备份" not in reply
    assert "content must not leak" not in reply


def test_phase54_wechat_paired_private_webpage_read_uses_real_chat_chain(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-browser-read-secret")

    with _WechatTestSite() as site:
        GatewayWechatClient.events = [
            _text_event(
                "evt-browser-read",
                "wxid-browser-read-secret",
                f"帮我看一下这网站有什么内容，{site.url('/page')}",
            )
        ]
        routed = client.post("/api/channels/providers/wechat/poll-once")
        assert routed.status_code == 200, routed.text
        assert routed.json()["chat_turns_created"] == 1
        _run_async(
            client,
            _wait_until,
            lambda: [
                item
                for item in GatewayWechatClient.send_calls
                if "微信网页只读测试" in item["text"]
            ],
            timeout=5.0,
        )

    reply = GatewayWechatClient.send_calls[-1]["text"]
    assert "微信网页只读测试" in reply
    assert "微信渠道也能进入只读网页链路" in reply
    assert "我无法访问外部链接" not in reply
    assert "复制链接在浏览器打开" not in reply


def test_phase54_wechat_immediate_delivery_failure_is_auditable(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, FailingGatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-failing-send-secret")
    registry = cast(Any, client.app).state.registry

    async def fake_create_turn(request: Any, **_: Any) -> ChatTurnResponse:
        return await _insert_running_turn(
            registry,
            request,
            conversation_id=request.conversation_id or "conv_phase54_send_failure",
        )

    registry.wechat_gateway_service._chat.create_turn = fake_create_turn
    FailingGatewayWechatClient.events = [
        _text_event("evt-failing-send", "wxid-failing-send-secret", "这条会发送失败")
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    binding = client.get("/api/channels/delivery-bindings", params={"status": "pending"}).json()[
        "items"
    ][0]
    _run_async(
        client,
        _complete_turn,
        registry,
        binding["turn_id"],
        assistant_text="这条回复会触发失败记录。",
    )
    _run_async(
        client,
        _wait_until,
        lambda: _binding_has_status(
            registry,
            binding["channel_delivery_binding_id"],
            "failed",
        ),
        timeout=3.0,
    )

    failed = client.get("/api/channels/delivery-bindings", params={"status": "failed"}).json()[
        "items"
    ][0]
    assert failed["attempts"] == 1
    assert "provider failed" in failed["failure_reason"]
    assert "send-secret" not in json.dumps(failed, ensure_ascii=False)


class GatewayWechatClient:
    events: ClassVar[list[dict[str, Any]]] = []
    send_calls: ClassVar[list[dict[str, str]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.events = []
        cls.send_calls = []

    @classmethod
    def create(cls, **kwargs: Any) -> GatewayWechatClient:
        del kwargs
        return cls()

    async def start_login(self) -> dict[str, Any]:
        return {
            "status": "qr_ready",
            "qrcode": "QR_RAW_PHASE54",
            "qrcode_image_content": "QR_PHASE54",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }

    async def wait_for_login(
        self,
        qrcode: str,
        timeout: float | int | None = None,
    ) -> dict[str, Any]:
        del qrcode, timeout
        return {
            "status": "confirmed",
            "account_id": "wxid-phase54-account-secret",
            "display_name": "Phase54 微信",
        }

    async def poll_events(self, account_id: str) -> Any:
        assert account_id == "wxid-phase54-account-secret"
        for event in list(self.__class__.events):
            yield event

    async def send_text(self, *, account_id: str, user_id: str, text: str) -> dict[str, Any]:
        self.__class__.send_calls.append(
            {"account_id": account_id, "user_id": user_id, "text": text}
        )
        return {"message_id": f"msg-{len(self.__class__.send_calls)}-secret"}

    async def download_media(self, *, account_id: str, media_id: str) -> bytes:
        assert account_id == "wxid-phase54-account-secret"
        if media_id == "image-secret-ref":
            return b"\x89PNG\r\nphase54"
        if media_id == "audio-secret-ref":
            return b"RIFFphase54"
        raise RuntimeError("missing media")


class FailingGatewayWechatClient(GatewayWechatClient):
    async def send_text(self, *, account_id: str, user_id: str, text: str) -> dict[str, Any]:
        del account_id, user_id, text
        raise RuntimeError("provider failed token=send-secret")


class _WechatTestSite:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _WechatTestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _WechatTestSite:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        address = self._server.server_address
        host = address[0]
        port = address[1]
        assert isinstance(host, str)
        assert isinstance(port, int)
        return f"http://{host}:{port}{path}"


class _WechatTestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = (
            "<html><head><title>微信网页只读测试</title></head>"
            "<body><h1>微信网页只读测试</h1>"
            "<p>微信渠道也能进入只读网页链路。</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


def _install_fake_wechat(client: TestClient, factory: type[GatewayWechatClient]) -> None:
    factory.reset()
    registry = cast(Any, client.app).state.registry
    registry.config.channels.providers["wechat"].enabled = True
    registry.config.channels.providers["wechat"].poll_enabled = True
    connector = registry.channel_binding_service.connector_registry().get("wechat")
    cast(Any, connector).set_client_factory(factory)


def _bind_real_wechat(client: TestClient) -> dict[str, str]:
    started = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": "Phase54 微信"},
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


def _text_event(
    event_id: str,
    peer_ref: str,
    text: str,
    *,
    chat_type: str = "private",
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "source": {"peer_ref": peer_ref, "chat_type": chat_type, "display_name": "外部联系人"},
        "message": {"content_type": "text", "text": text},
    }


def _sha256_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_test_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _pair_peer(client: TestClient, peer_ref: str) -> None:
    registry = cast(Any, client.app).state.registry
    connector = registry.channel_binding_service.connector_registry().get("wechat")
    client_factory = cast(Any, connector)._client_factory or GatewayWechatClient
    client_factory.events = [_text_event(f"evt-pair-{peer_ref}", peer_ref, "申请配对")]
    response = client.post("/api/channels/providers/wechat/poll-once")
    assert response.status_code == 200, response.text
    pairings = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"]
    peer_hash = _sha256_ref(peer_ref)
    pairing = next(item for item in pairings if item["peer_ref_redacted"] == peer_hash)
    approved = client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaoyao"},
    )
    assert approved.status_code == 200, approved.text


def _run_async(client: TestClient, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    portal = client.portal
    if portal is not None:
        async def runner() -> Any:
            return await func(*args, **kwargs)

        return portal.call(runner)
    raise RuntimeError("TestClient portal is not available")


async def _insert_completed_turn(
    registry: Any,
    request: Any,
    *,
    assistant_text: str = "收到，我会从微信接着聊。",
    conversation_id: str | None = None,
) -> ChatTurnResponse:
    turn_id = _new_test_id("turn")
    user_message_id = _new_test_id("msg_user")
    assistant_message_id = _new_test_id("msg_assistant")
    conversation_id = conversation_id or request.conversation_id or _new_test_id("conv_media")
    now = "2026-05-02T00:00:00+00:00"
    existing = await registry.chat.get_conversation(conversation_id)
    if existing is None:
        await registry.chat.create_conversation(
            conversation_id=conversation_id,
            organization_id="org_default",
            title="微信媒体",
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
        trace_id="trc_phase54_media",
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
        trace_id="trc_phase54_media",
        created_at=now,
    )
    await registry.chat.insert_turn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        member_id=request.member_id,
        user_message_id=user_message_id,
        trace_id="trc_phase54_media",
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
        trace_id="trc_phase54_media",
        status="completed",
    )


async def _insert_running_turn(
    registry: Any,
    request: Any,
    *,
    conversation_id: str | None = None,
) -> ChatTurnResponse:
    turn_id = _new_test_id("turn")
    user_message_id = _new_test_id("msg_user")
    conversation_id = conversation_id or request.conversation_id or _new_test_id("conv_running")
    now = "2026-05-02T00:00:00+00:00"
    existing = await registry.chat.get_conversation(conversation_id)
    if existing is None:
        await registry.chat.create_conversation(
            conversation_id=conversation_id,
            organization_id="org_default",
            title="微信即时投递",
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
        trace_id="trc_phase54_immediate",
        created_at=now,
    )
    await registry.chat.insert_turn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        member_id=request.member_id,
        user_message_id=user_message_id,
        trace_id="trc_phase54_immediate",
        status="running",
        retry_of_turn_id=None,
        created_at=now,
    )
    return ChatTurnResponse(
        turn_id=turn_id,
        conversation_id=conversation_id,
        message_id=user_message_id,
        assistant_message_id=None,
        trace_id="trc_phase54_immediate",
        status="running",
    )


async def _complete_turn(
    registry: Any,
    turn_id: str,
    *,
    assistant_text: str,
) -> None:
    turn = await registry.chat.get_turn(turn_id)
    assert turn is not None
    assistant_message_id = _new_test_id("msg_assistant")
    now = "2026-05-02T00:00:01+00:00"
    await registry.chat.insert_message(
        message_id=assistant_message_id,
        conversation_id=turn["conversation_id"],
        turn_id=turn_id,
        author_type="assistant",
        author_id=turn["member_id"],
        content_type="text",
        content_text=assistant_text,
        content={"text": assistant_text},
        trace_id=turn["trace_id"],
        created_at=now,
    )
    await registry.chat.update_turn(
        turn_id,
        assistant_message_id=assistant_message_id,
        status="completed",
        updated_at=now,
        ended_at=now,
    )


async def _wait_until(
    condition: Callable[[], Any],
    *,
    timeout: float,
    interval: float = 0.05,
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = condition()
        if hasattr(result, "__await__"):
            result = await result
        if result:
            return result
        await asyncio.sleep(interval)
    raise AssertionError("condition was not met before timeout")


async def _binding_has_status(registry: Any, binding_id: str, status: str) -> bool:
    binding = await registry.channels.get_delivery_binding(binding_id)
    return bool(binding and binding.get("status") == status)
