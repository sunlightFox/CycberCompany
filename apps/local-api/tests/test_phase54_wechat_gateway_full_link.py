from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import struct
import threading
import time
import uuid
import wave
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest
from app.core.config import ChannelProviderSection
from app.services.channel_connectors import (
    WechatClawbotConnector,
    _encode_wechat_voice_silk,
    _send_audio_as_voice_message,
)
from app.services.wechat_gateway import _wechat_outbound_attachment_selection
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

    async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        captured.append(request)
        return await _insert_completed_turn(
            registry,
            request,
            assistant_text="能收到，我们可以直接像聊天一样继续。",
            conversation_id=request.conversation_id or "conv_phase54_wechat",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
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


def test_phase54_wechat_voice_encoder_outputs_tencent_silk() -> None:
    silk = _encode_wechat_voice_silk(_test_wav_bytes())

    assert silk["audio_bytes"].startswith(b"\x02#!SILK_V3")
    assert silk["content_type"] == "audio/silk"
    assert silk["sample_rate"] == 24000
    assert silk["bits_per_sample"] == 16
    assert silk["playtime_ms"] >= 100
    assert silk["size_bytes"] == len(silk["audio_bytes"])


def test_phase54_wechat_voice_bubble_uses_silk_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_payloads: list[dict[str, Any]] = []
    uploaded_paths: list[Path] = []

    async def fake_prepare_upload(**kwargs: Any) -> Any:
        uploaded_paths.append(Path(kwargs["file_path"]))
        api_client.upload_media_type = int(kwargs["media_type"])
        return SimpleNamespace(
            download_encrypted_query_param="enc-query",
            aeskey_hex="00" * 16,
        )

    monkeypatch.setattr(
        "wechat_clawbot_sdk.media.transfer.prepare_upload",
        fake_prepare_upload,
    )
    api_client = _FakeVoiceApiClient(sent_payloads, result={"ret": 0})
    fake_client = _FakeNativeVoiceWechatClient(api_client=api_client)

    result, sent, voice_meta = asyncio.run(
        _send_audio_as_voice_message(
            client=fake_client,
            account_id="wxid-account",
            recipient="wxid-peer",
            audio_bytes=_test_wav_bytes(),
            content_type="audio/wav",
            filename="voice.wav",
            context_token="ctx-token",
        )
    )

    assert sent is True
    assert result == {"ret": 0}
    assert voice_meta["playtime_ms"] >= 100
    assert uploaded_paths and uploaded_paths[0].suffix == ".silk"
    payload = sent_payloads[-1]
    item = payload["msg"]["item_list"][0]
    from wechat_clawbot_sdk.api.protocol import MessageItemType, UploadMediaType

    assert item["type"] == int(MessageItemType.VOICE)
    assert item["voice_item"]["encode_type"] == 6
    assert item["voice_item"]["sample_rate"] == 24000
    assert item["voice_item"]["bits_per_sample"] == 16
    assert item["voice_item"]["playtime"] >= 100
    assert payload["msg"]["context_token"] == "ctx-token"
    assert fake_prepare_upload
    assert api_client.upload_media_type == int(UploadMediaType.VOICE)


def test_phase54_wechat_voice_bubble_unconfirmed_falls_back_to_media_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_prepare_upload(**kwargs: Any) -> Any:
        return SimpleNamespace(
            download_encrypted_query_param="enc-query",
            aeskey_hex="00" * 16,
        )

    monkeypatch.setattr(
        "wechat_clawbot_sdk.media.transfer.prepare_upload",
        fake_prepare_upload,
    )
    account_id = "wxid-account"
    recipient = "wxid-peer"
    accounts_dir = tmp_path / "accounts"
    accounts_dir.mkdir()
    (accounts_dir / f"{account_id}.context-tokens.json").write_text(
        json.dumps({recipient: "ctx-token"}),
        encoding="utf-8",
    )
    api_client = _FakeVoiceApiClient([], result={})
    fake_client = _FakeNativeVoiceWechatClient(api_client=api_client)
    connector = WechatClawbotConnector(
        ChannelProviderSection(enabled=True),
        state_dir=tmp_path,
    )
    connector.set_client_factory(_SingleWechatClientFactory(fake_client))

    result = asyncio.run(
        connector.send_audio(
            provider_state_ref="default",
            provider_state={"account_id": account_id},
            recipient=recipient,
            audio_bytes=_test_wav_bytes(),
            content_type="audio/wav",
            filename="voice.wav",
        )
    )

    assert result.status == "sent"
    assert result.response_summary["delivery_format"] == "voice_bubble+media_file_fallback"
    assert result.response_summary["voice_bubble_delivery_confirmation"] == "unconfirmed"
    assert result.response_summary["voice_bubble_content_type"] == "audio/silk"
    assert result.response_summary["voice_bubble_playtime_ms"] > 0
    assert result.response_summary["provider_raw_response"]["message_id"] == "fallback-message"
    assert fake_client.file_calls[-1]["filename"] == "voice.wav"


def test_phase54_xiaoyao_wechat_voice_reply_delivers_audio_message(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-xiaoyao-voice-secret")
    registry = cast(Any, client.app).state.registry
    text_send_count = len(GatewayWechatClient.send_calls)

    async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        return await _insert_completed_voice_turn(
            registry,
            request,
            assistant_text="可以，我用小耀自己的声音回复你。",
            conversation_id=request.conversation_id or "conv_phase54_xiaoyao_voice",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
    GatewayWechatClient.events = [
        _text_event(
            "evt-xiaoyao-voice",
            "wxid-xiaoyao-voice-secret",
            "小耀，请用声音回复我。",
        )
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1
    _run_async(
        client,
        _wait_until,
        lambda: GatewayWechatClient.audio_calls,
        timeout=3.0,
    )

    assert len(GatewayWechatClient.send_calls) == text_send_count
    audio_call = GatewayWechatClient.audio_calls[-1]
    assert audio_call["user_id"] == "wxid-xiaoyao-voice-secret"
    assert audio_call["account_id"] == "wxid-phase54-account-secret"
    assert audio_call["content_type"] == "audio/mpeg"
    assert audio_call["filename"] == "voice.mp3"
    assert audio_call["audio_size_bytes"] > 0


def test_phase54_wechat_gateway_routes_with_ingress_metadata_envelope_and_latency_trace(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-rich-secret")
    registry = cast(Any, client.app).state.registry

    async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        assert request.input.text == "看下这个链接 https://example.com/a?token=secret"
        assert request.input.type == "multi_part"
        assert request.input.content_parts
        assert request.input.content_parts[0].type == "link"
        assert request.context_refs[0].type == "url"
        assert request.ingress_metadata.channel == "wechat"
        assert request.ingress_metadata.channel_message_id == "evt-rich-link"
        assert request.ingress_metadata.raw_payload["channel_event_id"].startswith("chevt_")
        serialized = json.dumps(
            request.ingress_metadata.model_dump(mode="json"),
            ensure_ascii=False,
        )
        assert "wxid-rich-secret" not in serialized
        return await _insert_completed_turn(
            registry,
            request,
            assistant_text="我看到了这个链接，会按只读方式处理。",
            conversation_id=request.conversation_id or "conv_phase54_rich",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
    GatewayWechatClient.events = [
        _text_event(
            "evt-rich-link",
            "wxid-rich-secret",
            "看下这个链接 https://example.com/a?token=secret",
        )
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1
    events = client.get("/api/channels/events", params={"provider": "wechat", "limit": 5})
    assert events.status_code == 200, events.text
    event_payload = events.json()["items"][0]
    assert event_payload["channel_event_id"].startswith("chevt_")
    assert event_payload["provider_event_id_redacted"].startswith("sha256:")
    assert event_payload["payload_redacted"]["message"]["text_length"] == len(
        "看下这个链接 https://example.com/a?token=secret"
    )
    assert event_payload["normalized_event"]["latency_markers"]["t2_channel_event_created_at"]
    serialized_events = json.dumps(events.json(), ensure_ascii=False)
    assert "wxid-rich-secret" not in serialized_events
    assert "token=secret" not in serialized_events
    trace_id = routed.headers.get("x-trace-id")
    assert trace_id
    trace = client.get(f"/api/traces/{trace_id}")
    assert trace.status_code == 200, trace.text
    spans = trace.json()["spans"]
    ingress_spans = [item for item in spans if item["span_type"] == "chat.ingress"]
    assert any(item["name"] == "wechat route to chat" for item in ingress_spans)
    assert "secret" not in json.dumps(ingress_spans, ensure_ascii=False)


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

    async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
        request = cast(Any, client.app).state.registry.channel_ingress_runtime._router.route(
            **kwargs
        ).to_turn_request()
        captured.append(request)
        return await _insert_completed_turn(cast(Any, client.app).state.registry, request)

    cast(Any, client.app).state.registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
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


def test_phase54_wechat_direct_inbound_routes_to_chat_turn_and_delivers_reply(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    binding = _bind_real_wechat(client)
    registry = cast(Any, client.app).state.registry

    async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        return await _insert_completed_turn(
            registry,
            request,
            assistant_text="直接入站后已经接上聊天链路。",
            conversation_id=request.conversation_id or "conv_phase54_direct_inbound",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
    response = client.post(
        "/api/channels/inbound/wechat",
        json={
            "provider": "wechat",
            "channel_account_id": binding["channel_account_id"],
            "provider_event_id": "evt-phase54-direct-inbound",
            "source": {
                "chat_type": "private",
                "peer_ref": "wxid-direct-inbound-secret",
                "display_name": "外部联系人",
            },
            "message": {
                "content_type": "text",
                "content_text": "phase54 direct inbound chat",
            },
            "raw_event": {
                "event_id": "evt-phase54-direct-inbound",
                "source": {
                    "peer_ref": "wxid-direct-inbound-secret",
                    "chat_type": "private",
                },
                "message": {
                    "content_type": "text",
                    "content_text": "phase54 direct inbound chat",
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "received"
    assert payload["notification_inbound"]["binding_status"] == "no_pending_action"
    assert payload["turn_id"]
    assert payload["delivery_binding_id"]
    assert payload["chat_turns_created"] == 1
    assert payload["diagnostic"]["chat_route"]["status"] == "routed"
    assert payload["diagnostic"]["chat_route"]["reliability_status"] == "ok"
    assert payload["diagnostic"]["chat_route"]["correlation"]["turn_id"] == payload["turn_id"]
    assert payload["diagnostic"]["chat_route"]["delivery_binding"]["binding_visible"] is True
    _run_async(
        client,
        _wait_until,
        lambda: [
            item
            for item in GatewayWechatClient.send_calls
            if item["text"] == "直接入站后已经接上聊天链路。"
        ],
        timeout=3.0,
    )
    delivery = client.get(
        "/api/channels/delivery-bindings",
        params={"turn_id": payload["turn_id"], "provider": "wechat"},
    )
    assert delivery.status_code == 200, delivery.text
    assert delivery.json()["items"][0]["status"] == "sent"
    assert "wxid-direct-inbound-secret" not in json.dumps(payload, ensure_ascii=False)


def test_phase54_wechat_worker_tick_receives_and_delivers_natural_reply(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-worker-secret")
    registry = cast(Any, client.app).state.registry
    captured: list[Any] = []

    async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        captured.append(request)
        return await _insert_completed_turn(
            registry,
            request,
            assistant_text="在的，这条微信我收到了。",
            conversation_id=request.conversation_id or "conv_phase54_worker",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
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

    async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        captured.append(request)
        return await _insert_running_turn(
            registry,
            request,
            conversation_id=request.conversation_id or "conv_phase54_immediate",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
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
    assert health.json()["connected"] is True
    assert health.json()["status"] == "connected"
    assert health.json()["login_state"] in {"logged_in", "mock_ready"}
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

    async def fake_submit_channel_turn(**kwargs: Any) -> ChatTurnResponse:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        return await _insert_running_turn(
            registry,
            request,
            conversation_id=request.conversation_id or "conv_phase54_send_failure",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = fake_submit_channel_turn
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


def test_phase54_wechat_notification_gateway_exposes_provider_unavailable_failure(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    bound = _bind_real_wechat(client)
    registry = cast(Any, client.app).state.registry
    registry.config.channels.providers["wechat"].enabled = False

    response = client.post(
        "/api/notification/messages",
        json={
            "channel_id": bound["channel_id"],
            "message_type": "system_degraded",
            "recipient": "wxid-provider-unavailable-secret",
            "subject": "Provider unavailable",
            "body": "wechat provider disabled should not be marked sent",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["metadata"]["delivery"]["delivery_status"] == "provider_unavailable"
    assert payload["failure_reason"].startswith("provider_unavailable")
    attempts = client.get(
        f"/api/notification/messages/{payload['notification_id']}/attempts"
    ).json()["items"]
    assert attempts[0]["status"] == "provider_unavailable"
    assert "provider-unavailable-secret" not in json.dumps(attempts, ensure_ascii=False)


def test_phase54_wechat_notification_gateway_exposes_retryable_and_rejected_failures(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, FailingGatewayWechatClient)
    bound = _bind_real_wechat(client)
    retryable = client.post(
        "/api/notification/messages",
        json={
            "channel_id": bound["channel_id"],
            "message_type": "system_degraded",
            "recipient": "wxid-retryable-secret",
            "subject": "Retryable",
            "body": "provider should fail with retryable semantics",
        },
    )
    assert retryable.status_code == 200, retryable.text
    retryable_payload = retryable.json()
    assert retryable_payload["status"] == "failed"
    assert retryable_payload["metadata"]["delivery"]["delivery_status"] == "retryable_failure"
    assert retryable_payload["metadata"]["delivery"]["retryable"] is True
    assert retryable_payload["failure_reason"].startswith("provider_send_failed")
    retryable_attempts = client.get(
        f"/api/notification/messages/{retryable_payload['notification_id']}/attempts"
    ).json()["items"]
    assert retryable_attempts[0]["status"] == "retryable_failure"

    registry = cast(Any, client.app).state.registry
    connector = registry.channel_binding_service.connector_registry().get("wechat")
    cast(Any, connector).set_client_factory(GatewayWechatClient)
    rejected_channel = client.post(
        "/api/notification/channels",
        json={
            "provider": "wechat",
            "display_name": "Phase54 rejected",
            "channel_type": "direct_message",
            "sensitivity": "high",
            "provider_config": {},
        },
    )
    assert rejected_channel.status_code == 200, rejected_channel.text
    rejected = client.post(
        "/api/notification/messages",
        json={
            "channel_id": rejected_channel.json()["channel_id"],
            "message_type": "system_degraded",
            "recipient": "wxid-rejected-secret",
            "subject": "Rejected",
            "body": "missing provider state should not be marked sent",
        },
    )
    assert rejected.status_code == 200, rejected.text
    rejected_payload = rejected.json()
    assert rejected_payload["status"] == "rejected"
    assert rejected_payload["metadata"]["delivery"]["delivery_status"] == "rejected"
    assert rejected_payload["metadata"]["delivery"]["retryable"] is False
    rejected_attempts = client.get(
        f"/api/notification/messages/{rejected_payload['notification_id']}/attempts"
    ).json()["items"]
    assert rejected_attempts[0]["status"] == "rejected"
    assert "wxid-rejected-secret" not in json.dumps(rejected_attempts, ensure_ascii=False)


class GatewayWechatClient:
    events: ClassVar[list[dict[str, Any]]] = []
    send_calls: ClassVar[list[dict[str, str]]] = []
    audio_calls: ClassVar[list[dict[str, Any]]] = []
    file_calls: ClassVar[list[dict[str, Any]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.events = []
        cls.send_calls = []
        cls.audio_calls = []
        cls.file_calls = []

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

    async def send_audio(
        self,
        *,
        account_id: str,
        user_id: str,
        audio_bytes: bytes,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        self.__class__.audio_calls.append(
            {
                "account_id": account_id,
                "user_id": user_id,
                "content_type": content_type,
                "filename": filename,
                "audio_size_bytes": len(audio_bytes),
            }
        )
        return {"message_id": f"audio-{len(self.__class__.audio_calls)}-secret"}

    async def send_file(self, **kwargs: Any) -> dict[str, Any]:
        self.__class__.file_calls.append(dict(kwargs))
        return {"message_id": f"file-{len(self.__class__.file_calls)}-secret"}

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

    async def send_audio(
        self,
        *,
        account_id: str,
        user_id: str,
        audio_bytes: bytes,
        content_type: str | None = None,
        filename: str | None = None,
    ) -> dict[str, Any]:
        del account_id, user_id, audio_bytes, content_type, filename
        raise RuntimeError("provider failed token=audio-secret")

    async def send_file(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise RuntimeError("provider failed token=file-secret")


class AttachmentFailingGatewayWechatClient(GatewayWechatClient):
    async def send_file(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise RuntimeError("provider failed token=attachment-secret")


class _FakeVoiceApiClient:
    def __init__(self, sent_payloads: list[dict[str, Any]], *, result: dict[str, Any]) -> None:
        self.sent_payloads = sent_payloads
        self.result = result
        self.upload_media_type: int | None = None

    async def send_message(self, session: Any, payload: dict[str, Any]) -> dict[str, Any]:
        del session
        self.sent_payloads.append(payload)
        return dict(self.result)


class _FakeNativeVoiceWechatClient:
    def __init__(self, *, api_client: _FakeVoiceApiClient) -> None:
        self._api_client = api_client
        self._message_service = SimpleNamespace(_cdn_base_url="https://cdn.example.test")
        self.file_calls: list[dict[str, Any]] = []

    async def get_account_session(self, account_id: str) -> dict[str, str]:
        return {"account_id": account_id}

    async def send_file(self, **kwargs: Any) -> dict[str, str]:
        self.file_calls.append(dict(kwargs))
        return {"message_id": "fallback-message"}


class _SingleWechatClientFactory:
    def __init__(self, client: _FakeNativeVoiceWechatClient) -> None:
        self.client = client

    async def create(self, **kwargs: Any) -> _FakeNativeVoiceWechatClient:
        del kwargs
        return self.client


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


def test_phase54_wechat_outbound_attachment_selection_prefers_primary_office_outputs(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    task = client.post("/api/tasks", json={"goal": "phase54 attachment selection"}).json()
    task_id = task["task_id"]
    artifact_store = registry.artifact_store
    word_artifact = _run_async(
        client,
        lambda: artifact_store.write_bytes(
            task_id=task_id,
            organization_id="org_default",
            display_name="weekly-brief.docx",
            content=b"phase54-docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            artifact_type="office_document",
        ),
    )
    markdown_artifact = _run_async(
        client,
        lambda: artifact_store.write_text(
            task_id=task_id,
            organization_id="org_default",
            display_name="weekly-brief.md",
            content="# phase54",
            artifact_type="text",
        ),
    )
    _run_async(
        client,
        lambda: artifact_store.write_text(
            task_id=task_id,
            organization_id="org_default",
            display_name="terminal.log",
            content="internal",
            artifact_type="terminal_log",
        ),
    )

    selection = _run_async(
        client,
        lambda: _wechat_outbound_attachment_selection(
            artifacts=artifact_store,
            turn={},
            message={
                "content": {
                    "response_plan": {
                        "artifact_refs": [
                            {
                                "artifact_id": word_artifact.artifact_id,
                                "display_name": word_artifact.display_name,
                                "content_type": word_artifact.content_type,
                            },
                            {
                                "artifact_id": markdown_artifact.artifact_id,
                                "display_name": markdown_artifact.display_name,
                                "content_type": markdown_artifact.content_type,
                            },
                        ],
                        "structured_payload": {"office_productivity": {"task_id": task_id}},
                    }
                }
            },
            user_text="把文件发我，我要 Word 版本",
            final_text="已为你生成文档。",
        ),
    )

    assert selection["explicit_request_detected"] is True
    assert selection["scene"] == "office_document"
    assert [item["display_name"] for item in selection["selected_attachments"]] == [
        "weekly-brief.docx",
        "weekly-brief.md",
    ]


def test_phase54_wechat_notification_provider_sends_text_then_attachments(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    bound = _bind_real_wechat(client)
    registry = cast(Any, client.app).state.registry
    task = client.post("/api/tasks", json={"goal": "phase54 notification attachments"}).json()
    artifact_store = registry.artifact_store
    task_id = task["task_id"]
    word_artifact = _run_async(
        client,
        lambda: artifact_store.write_bytes(
            task_id=task_id,
            organization_id="org_default",
            display_name="reply.docx",
            content=b"phase54-docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            artifact_type="office_document",
        ),
    )
    note_artifact = _run_async(
        client,
        lambda: artifact_store.write_text(
            task_id=task_id,
            organization_id="org_default",
            display_name="reply.md",
            content="# reply",
            artifact_type="text",
        ),
    )

    response = client.post(
        "/api/notification/messages",
        json={
            "channel_id": bound["channel_id"],
            "message_type": "wechat_chat_reply",
            "recipient": "wxid-attachment-secret",
            "subject": "attachment",
            "body": "文件在这。",
            "metadata": {
                "attachments": [
                    {
                        "artifact_id": word_artifact.artifact_id,
                        "display_name": word_artifact.display_name,
                        "content_type": word_artifact.content_type,
                    },
                    {
                        "artifact_id": note_artifact.artifact_id,
                        "display_name": note_artifact.display_name,
                        "content_type": note_artifact.content_type,
                    },
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "sent"
    assert GatewayWechatClient.send_calls[-1]["text"] == "文件在这。"
    assert [item["filename"] for item in GatewayWechatClient.file_calls[-2:]] == [
        "reply.docx",
        "reply.md",
    ]
    attempts = client.get(
        f"/api/notification/messages/{payload['notification_id']}/attempts"
    ).json()["items"]
    assert attempts[0]["response_summary"]["attachment_delivery_status"] == "sent"
    assert len(attempts[0]["response_summary"]["attachment_results"]) == 2


def test_phase54_wechat_notification_provider_keeps_text_sent_when_attachments_fail(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, AttachmentFailingGatewayWechatClient)
    bound = _bind_real_wechat(client)
    registry = cast(Any, client.app).state.registry
    task = client.post("/api/tasks", json={"goal": "phase54 attachment degrade"}).json()
    artifact_store = registry.artifact_store
    task_id = task["task_id"]
    artifact = _run_async(
        client,
        lambda: artifact_store.write_text(
            task_id=task_id,
            organization_id="org_default",
            display_name="reply.md",
            content="# reply",
            artifact_type="text",
        ),
    )

    response = client.post(
        "/api/notification/messages",
        json={
            "channel_id": bound["channel_id"],
            "message_type": "wechat_chat_reply",
            "recipient": "wxid-attachment-fail-secret",
            "subject": "attachment",
            "body": "先把文字发出去。",
            "metadata": {
                "attachments": [
                    {
                        "artifact_id": artifact.artifact_id,
                        "display_name": artifact.display_name,
                        "content_type": artifact.content_type,
                    }
                ]
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "sent"
    attempts = client.get(
        f"/api/notification/messages/{payload['notification_id']}/attempts"
    ).json()["items"]
    assert attempts[0]["response_summary"]["attachment_delivery_status"] == "failed_all"
    assert attempts[0]["response_summary"]["attachment_results"][0]["error_code"] == (
        "provider_send_failed"
    )


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


def _test_wav_bytes(*, duration_ms: int = 180, sample_rate: int = 24000) -> bytes:
    frame_count = int(sample_rate * duration_ms / 1000)
    frames = bytearray()
    for index in range(frame_count):
        sample = int(0.24 * 32767 * math.sin(2 * math.pi * 440 * index / sample_rate))
        frames.extend(struct.pack("<h", sample))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(frames))
    return output.getvalue()


def _pair_peer(client: TestClient, peer_ref: str) -> None:
    registry = cast(Any, client.app).state.registry
    connector = registry.channel_binding_service.connector_registry().get("wechat")
    client_factory = cast(Any, connector)._client_factory or GatewayWechatClient
    client_factory.events = [_text_event(f"evt-pair-{peer_ref}", peer_ref, "申请配对")]
    response = client.post("/api/channels/providers/wechat/poll-once")
    assert response.status_code == 200, response.text
    peer_hash = _sha256_ref(peer_ref)
    pairing = None
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and pairing is None:
        pairings = client.get(
            "/api/channels/pairing-requests",
            params={"provider": "wechat", "status": "pending"},
        ).json()["items"]
        pairing = next(
            (item for item in pairings if item["peer_ref_redacted"] == peer_hash),
            None,
        )
        if pairing is None:
            time.sleep(0.05)
    assert pairing is not None
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
    assistant_content: dict[str, Any] | None = None,
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
        content=assistant_content or {"text": assistant_text},
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


async def _insert_completed_voice_turn(
    registry: Any,
    request: Any,
    *,
    assistant_text: str,
    conversation_id: str | None = None,
) -> ChatTurnResponse:
    turn_id = _new_test_id("turn")
    user_message_id = _new_test_id("msg_user")
    assistant_message_id = _new_test_id("msg_assistant")
    conversation_id = conversation_id or request.conversation_id or _new_test_id("conv_voice")
    now = "2026-05-02T00:00:00+00:00"
    trace_id = await registry.trace_service.start_trace()
    existing = await registry.chat.get_conversation(conversation_id)
    if existing is None:
        await registry.chat.create_conversation(
            conversation_id=conversation_id,
            organization_id="org_default",
            title="微信语音",
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
        trace_id=trace_id,
        created_at=now,
    )
    await registry.chat.insert_turn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        member_id=request.member_id,
        user_message_id=user_message_id,
        trace_id=trace_id,
        status="created",
        retry_of_turn_id=None,
        created_at=now,
    )
    voice_result = await registry.voice_service.render_voice_reply(
        turn={
            "organization_id": "org_default",
            "member_id": request.member_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
        },
        user_text=request.input.text,
        assistant_text=assistant_text,
        response_plan={"structured_payload": {"voice_reply": {"requested": True}}},
        persona={"default_mode": "warm", "style_principles": ["小耀自己的声音"]},
        heart={"preferred_pace": "natural"},
        risk_level="R1",
        trace_id=trace_id,
        message_id=assistant_message_id,
    )
    voice_reply = voice_result.voice_reply
    await registry.chat.insert_message(
        message_id=assistant_message_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        author_type="assistant",
        author_id=request.member_id,
        content_type="audio",
        content_text=assistant_text,
        content={"type": "audio", "text": assistant_text, "voice_reply": voice_reply},
        trace_id=trace_id,
        voice_profile_id=voice_reply["voice_profile_id"],
        voice_render_job_id=voice_result.render_job["render_job_id"],
        audio_uri=voice_reply["audio_uri"],
        audio_content_type=voice_reply["audio_content_type"],
        voice_metadata=voice_reply,
        created_at=now,
    )
    await registry.voice_service.attach_message(
        render_job_id=voice_result.render_job["render_job_id"],
        message_id=assistant_message_id,
        trace_id=trace_id,
    )
    await registry.chat.update_turn(
        turn_id,
        assistant_message_id=assistant_message_id,
        status="completed",
        updated_at=now,
        ended_at=now,
    )
    await registry.trace_service.end_trace(trace_id)
    return ChatTurnResponse(
        turn_id=turn_id,
        conversation_id=conversation_id,
        message_id=user_message_id,
        assistant_message_id=assistant_message_id,
        trace_id=trace_id,
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
