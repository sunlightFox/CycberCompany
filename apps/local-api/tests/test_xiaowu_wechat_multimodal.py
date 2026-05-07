from __future__ import annotations

import base64
import json
import time
import wave
from collections.abc import Callable
from io import BytesIO
from typing import Any, ClassVar, cast

import pytest
from app.services import chat as chat_module
from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from docx import Document
from fastapi.testclient import TestClient


def test_xiaowu_wechat_multimodal_flow_with_fake_connector_and_mocked_model(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, XiaowuWechatClient)
    _disable_chat_background_execution(client)
    brain_id = _create_local_brain(client)
    _bind_member_default_brain(client, "mem_xiaowu", brain_id)
    _bind_wechat_account(client, "Phase Xiaowu 微信")

    pairing_peer = "wxid-xiaowu-unpaired-secret"
    XiaowuWechatClient.events = [_text_event("evt-pair", pairing_peer, "申请配对")]
    pair_result = client.post("/api/channels/providers/wechat/poll-once")
    assert pair_result.status_code == 200, pair_result.text
    assert pair_result.json()["created_pairing_requests"] == 1

    pairing = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"][0]
    serialized_pairing = json.dumps(pairing, ensure_ascii=False)
    assert pairing["peer_ref_redacted"].startswith("sha256:")
    assert pairing_peer not in serialized_pairing

    approved = client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaowu"},
    )
    assert approved.status_code == 200, approved.text

    text_turn = _run_wechat_turn(
        client,
        peer_ref=pairing_peer,
        event_id="evt-text",
        message_text="小吴，帮我看一下这段消息",
        attachments=[],
        expected_reply="小吴在微信里收到啦，我们直接继续聊。",
    )
    image_turn = _run_wechat_turn(
        client,
        peer_ref=pairing_peer,
        event_id="evt-image",
        message_text="小吴，看看这张图片",
        attachments=[
            {
                "media_id": "image-secret-ref",
                "type": "image",
                "content_type": "image/png",
                "name": "截图.png",
            }
        ],
        expected_reply="我收到这张图了，现在还能看到的只是基础信息，细节我不会瞎猜。你要是告诉我重点，我就能接着帮你看。",
    )
    audio_turn = _run_wechat_turn(
        client,
        peer_ref=pairing_peer,
        event_id="evt-audio",
        message_text="小吴，听一下这段语音",
        attachments=[
            {
                "media_id": "audio-secret-ref",
                "type": "audio",
                "content_type": "audio/wav",
                "name": "voice.wav",
            }
        ],
        expected_reply="语音我收到了，不过现在还没拿到可用的转写文字。我先记着，等能转出来就能直接按内容接着聊。",
    )
    file_turn = _run_wechat_turn(
        client,
        peer_ref=pairing_peer,
        event_id="evt-file",
        message_text="小吴，帮我看这个文件",
        attachments=[
            {
                "media_id": "file-docx-secret-ref",
                "type": "file",
                "content_type": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                "name": "方案资料.docx",
            }
        ],
        expected_reply="这个文件我看到了，里面的内容我会按只读方式理解，不会乱动原文件。",
    )
    unsupported_turn = _run_wechat_turn(
        client,
        peer_ref=pairing_peer,
        event_id="evt-zip",
        message_text="小吴，再看一个压缩包",
        attachments=[
            {
                "media_id": "zip-secret-ref",
                "type": "file",
                "content_type": "application/zip",
                "name": "archive.zip",
            }
        ],
        expected_reply="这个压缩包我收到了，但我先只保留安全摘要，不会直接打开里面的内容。",
    )

    for item in [text_turn, image_turn, audio_turn, file_turn]:
        assert item["reply_text"] in item["sent_text"]
        assert item["turn_detail"]["status"] in {"completed", "failed"}
        assert item["queue"]["status"] in {"completed", "failed"}
        assert item["trace"]["trace_id"] == item["turn_response"]["trace_id"]
        assert item["secret_token"] not in json.dumps(item["envelope"], ensure_ascii=False)
        assert item["secret_token"] not in json.dumps(item["trace"], ensure_ascii=False)
        assert item["secret_token"] not in json.dumps(item["events"], ensure_ascii=False)
        assert item["secret_token"] not in json.dumps(item["attachments"], ensure_ascii=False)
        assert item["reply_text"] in item["sent_text"]

    assert {part["type"] for part in text_turn["envelope"]["content_parts"]} >= {"text"}
    assert {part["type"] for part in image_turn["envelope"]["content_parts"]} >= {
        "text",
        "image",
        "image_summary",
    }
    assert {part["type"] for part in audio_turn["envelope"]["content_parts"]} >= {
        "text",
        "audio",
        "audio_transcript",
    }
    assert {part["type"] for part in file_turn["envelope"]["content_parts"]} >= {
        "text",
        "file",
        "file_extract",
    }
    assert {part["type"] for part in unsupported_turn["envelope"]["content_parts"]} >= {
        "text",
        "file",
        "file_extract",
    }
    assert file_turn["envelope"]["normalized_summary"]["understanding_status"] == "understood"
    assert file_turn["envelope"]["normalized_summary"]["memory_candidate_count"] >= 1
    assert unsupported_turn["envelope"]["normalized_summary"]["understanding_status"] == "degraded"
    assert unsupported_turn["envelope"]["normalized_summary"]["memory_candidate_count"] == 0
    assert "图片内容线索" in image_turn["envelope"]["model_safe_text"]
    assert "语音内容线索" in audio_turn["envelope"]["model_safe_text"]
    assert "文件内容摘录" in file_turn["envelope"]["model_safe_text"]
    assert "这份资料说明先做图片识别，再做文件识别。" in file_turn["envelope"][
        "model_safe_text"
    ]

    image_attachment = image_turn["attachments"][0]
    audio_attachment = audio_turn["attachments"][0]
    file_attachment = file_turn["attachments"][0]
    unsupported_attachment = unsupported_turn["attachments"][0]
    assert image_attachment["attachment_type"] == "image"
    assert image_attachment["status"] == "ready"
    assert image_attachment["media_id"]
    assert image_attachment["blob_ref"].startswith("channel-attachment://wechat/")
    assert image_attachment["metadata"]["understanding_status"] == "degraded"
    assert audio_attachment["attachment_type"] == "audio"
    assert audio_attachment["status"] == "degraded"
    assert audio_attachment["media_id"]
    assert audio_attachment["metadata"]["transcription_status"] == "degraded"
    assert audio_attachment["metadata"]["understanding_status"] == "degraded"
    assert file_attachment["attachment_type"] == "file"
    assert file_attachment["status"] == "ready"
    assert file_attachment["media_id"] is None
    assert file_attachment["blob_ref"].startswith("channel-attachment://wechat/")
    assert file_attachment["metadata"]["understanding_status"] == "understood"
    assert file_attachment["metadata"]["memory_candidate_ids"]
    assert unsupported_attachment["attachment_type"] == "file"
    assert unsupported_attachment["metadata"]["understanding_status"] == "degraded"
    assert unsupported_attachment["metadata"]["memory_candidate_ids"] == []

    serialized_turns = json.dumps(
        [text_turn, image_turn, audio_turn, file_turn, unsupported_turn],
        ensure_ascii=False,
    )
    assert "image-secret-ref" not in serialized_turns
    assert "audio-secret-ref" not in serialized_turns
    assert "file-docx-secret-ref" not in serialized_turns
    assert "zip-secret-ref" not in serialized_turns

    file_memory = _find_memory_by_source(client, "multimodal_attachment")
    assert file_memory["summary_text"]
    source = client.get(f"/api/memory/{file_memory['memory_id']}/source").json()
    assert source["source"]["type"] == "multimodal_attachment"
    assert source["source"]["turn_id"] == file_turn["turn_response"]["turn_id"]
    assert source["source"]["message_id"] == file_turn["turn_response"]["user_message_id"]
    assert source["source"]["channel_attachment_id"] == file_attachment["channel_attachment_id"]
    assert source["trace_id"]
    assert source["source"]["artifact_id"] or source["source"]["media_id"]


def test_xiaowu_wechat_audio_transcript_feeds_model_as_natural_text(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_wechat(client, XiaowuWechatClient)
    _disable_chat_background_execution(client)
    brain_id = _create_local_brain(client)
    _bind_member_default_brain(client, "mem_xiaowu", brain_id)
    _bind_wechat_account(client, "Phase Xiaowu 微信")

    captured_messages: list[list[dict[str, str]]] = []

    async def fake_stream_chat(
        self: Any,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ):
        del self, cancel_token
        captured_messages.append(request.messages)
        last_user = request.messages[-1]["content"]
        if len(captured_messages) == 1:
            assert "语音转成文字：今天先把图片识别和文件识别串起来，回复口吻自然一点" in last_user
            assert "provider" not in last_user.lower()
            assert "degraded" not in last_user.lower()
        text = (
            "我听懂了，你这段语音是在说先把图片识别和文件识别串起来，"
            "再把回复口吻调自然一点。重点是先看懂内容，再像微信聊天一样顺着内容回，"
            "不要只报状态。这样用户发来图、文件和语音时，小吴可以直接接住内容，"
            "用清楚又像人话的方式回应。"
        )
        yield ModelStreamEvent(event="started")
        yield ModelStreamEvent(event="delta", text=text)
        yield ModelStreamEvent(event="completed", usage={"output_tokens": len(text)})

    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", fake_stream_chat)

    pairing_peer = "wxid-xiaowu-transcript-secret"
    XiaowuWechatClient.events = [_text_event("evt-audio-pair", pairing_peer, "申请配对")]
    client.post("/api/channels/providers/wechat/poll-once")
    pairing = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"][0]
    client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaowu"},
    )

    XiaowuWechatClient.events = [
        {
            "event_id": "evt-audio-transcript",
            "source": {
                "peer_ref": pairing_peer,
                "chat_type": "private",
                "display_name": "外部联系人",
            },
            "message": {
                "content_type": "audio",
                "text": "小吴，听一下这段语音",
                "attachments": [
                    {
                        "media_id": "audio-transcript-secret-ref",
                        "type": "audio",
                        "content_type": "audio/wav",
                        "name": "voice.wav",
                        "transcript_text": "今天先把图片识别和文件识别串起来，回复口吻自然一点",
                    }
                ],
            },
        }
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1

    binding = client.get(
        "/api/channels/delivery-bindings",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"][0]
    turn_id = binding["turn_id"]

    registry = cast(Any, client.app).state.registry
    portal = client.portal
    assert portal is not None

    async def _run_turn() -> None:
        await registry.chat_service.run_turn(turn_id)

    portal.call(_run_turn)
    client.post("/api/channels/providers/wechat/deliver-due")
    _wait_for(
        lambda: client.get(
            "/api/channels/delivery-bindings",
            params={"provider": "wechat", "turn_id": turn_id},
        ).json()["items"][0]["status"]
        == "sent",
        timeout=5.0,
    )

    stream = client.get(f"/api/chat/stream/{turn_id}")
    assert stream.status_code == 200, stream.text
    events = _parse_sse(stream.text)
    envelope = client.get(f"/api/chat/turns/{turn_id}/envelope").json()
    assert "语音转成文字：今天先把图片识别和文件识别串起来，回复口吻自然一点" in envelope[
        "model_safe_text"
    ]
    assert captured_messages, json.dumps(events, ensure_ascii=False)
    sent_text = XiaowuWechatClient.send_calls[-1]["text"]
    assert "我听懂了" in sent_text
    assert "provider" not in sent_text.lower()
    assert "degraded" not in sent_text.lower()


def test_xiaowu_wechat_audio_reply_is_naturalized_through_continuation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_wechat(client, XiaowuWechatClient)
    _disable_chat_background_execution(client)
    brain_id = _create_local_brain(client)
    _bind_member_default_brain(client, "mem_xiaowu", brain_id)
    _bind_wechat_account(client, "Phase Xiaowu 微信")

    captured_messages: list[list[dict[str, str]]] = []

    async def fake_stream_chat(
        self: Any,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ):
        del self, cancel_token
        captured_messages.append(request.messages)
        if len(captured_messages) == 1:
            text = "收到语音，我来继续处理。"
        else:
            text = (
                "我听到你说想先把图片识别和文件识别串起来，重点其实是两步："
                "先把用户发来的内容转成可读文字，再让小吴顺着内容自然回复，"
                "不要像系统状态。这样下一轮用户发来图、文件或语音时，"
                "小吴能先讲听懂了什么，再给一个好理解的下一步。"
            )
        yield ModelStreamEvent(event="started")
        yield ModelStreamEvent(event="delta", text=text)
        yield ModelStreamEvent(event="completed", usage={"output_tokens": len(text)})

    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", fake_stream_chat)

    pairing_peer = "wxid-xiaowu-audio-natural-secret"
    XiaowuWechatClient.events = [_text_event("evt-audio-pair-n2", pairing_peer, "申请配对")]
    client.post("/api/channels/providers/wechat/poll-once")
    pairing = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"][0]
    client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaowu"},
    )

    XiaowuWechatClient.events = [
        {
            "event_id": "evt-audio-natural",
            "source": {
                "peer_ref": pairing_peer,
                "chat_type": "private",
                "display_name": "外部联系人",
            },
            "message": {
                "content_type": "audio",
                "text": "小吴，听一下这段语音，顺着内容自然一点回我。",
                "attachments": [
                    {
                        "media_id": "audio-transcript-secret-ref",
                        "type": "audio",
                        "content_type": "audio/wav",
                        "name": "voice.wav",
                        "transcript_text": "今天先把图片识别和文件识别串起来，回复口吻自然一点",
                    }
                ],
            },
        }
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1

    binding = client.get(
        "/api/channels/delivery-bindings",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"][0]
    turn_id = binding["turn_id"]

    registry = cast(Any, client.app).state.registry
    portal = client.portal
    assert portal is not None

    async def _run_turn() -> None:
        await registry.chat_service.run_turn(turn_id)

    portal.call(_run_turn)
    client.post("/api/channels/providers/wechat/deliver-due")
    _wait_for(
        lambda: client.get(
            "/api/channels/delivery-bindings",
            params={"provider": "wechat", "turn_id": turn_id},
        ).json()["items"][0]["status"]
        == "sent",
        timeout=5.0,
    )

    stream = client.get(f"/api/chat/stream/{turn_id}")
    assert stream.status_code == 200, stream.text
    events = _parse_sse(stream.text)
    response_completed = next(event for event in events if event["event"] == "response.completed")
    structured_payload = response_completed["payload"]["response_plan"]["structured_payload"]
    sent_text = XiaowuWechatClient.send_calls[-1]["text"]
    envelope = client.get(f"/api/chat/turns/{turn_id}/envelope").json()

    assert len(captured_messages) == 1
    assert "语音转成文字：今天先把图片识别和文件识别串起来，回复口吻自然一点" in envelope[
        "model_safe_text"
    ]
    assert "语音转成文字：今天先把图片识别和文件识别串起来，回复口吻自然一点" in (
        captured_messages[0][-1]["content"]
    )
    assert sent_text == "收到语音，我来继续处理。"
    assert "continuation" not in structured_payload


def test_xiaowu_wechat_collect_and_fail_closed_paths(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, XiaowuWechatClient)
    _disable_chat_background_execution(client)
    brain_id = _create_local_brain(client)
    _bind_member_default_brain(client, "mem_xiaowu", brain_id)
    _bind_wechat_account(client, "Phase Xiaowu 微信")

    revoked_peer = "wxid-xiaowu-revoked-secret"
    XiaowuWechatClient.events = [_text_event("evt-revoke-pair", revoked_peer, "申请配对")]
    client.post("/api/channels/providers/wechat/poll-once")
    pairing = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"][0]
    approved = client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaowu"},
    ).json()
    peer_session_id = approved["peer_session"]["channel_peer_session_id"]
    revoked = client.post(
        f"/api/channels/peers/{peer_session_id}/revoke",
        json={"member_id": "mem_xiaowu"},
    )
    assert revoked.status_code == 200, revoked.text

    XiaowuWechatClient.events = [
        _text_event("evt-revoked", revoked_peer, "撤销后不应进入聊天")
    ]
    blocked = client.post("/api/channels/providers/wechat/poll-once")
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["chat_turns_created"] == 0

    collect = _create_turn(
        client,
        session_id="xiaowu-collect-session",
        conversation_id=None,
        payload={
            "input": {"type": "text", "text": "小吴，先记第一段"},
            "ingress_metadata": {
                "channel": "wechat",
                "channel_message_id": "collect-1",
                "queue_policy": "collect",
                "debounce_ms": 200,
            },
        },
    )
    second = _create_turn(
        client,
        session_id="xiaowu-collect-session",
        conversation_id=collect["conversation_id"],
        payload={
            "input": {"type": "text", "text": "小吴，再补第二段"},
            "ingress_metadata": {
                "channel": "wechat",
                "channel_message_id": "collect-2",
                "queue_policy": "collect",
                "debounce_ms": 200,
            },
        },
    )
    collect_envelope = client.get(f"/api/chat/turns/{collect['turn_id']}/envelope").json()
    collect_queue = client.get(f"/api/chat/turns/{collect['turn_id']}/queue").json()["item"]
    collect_conversation = client.get(
        f"/api/chat/conversations/{collect['conversation_id']}"
    ).json()
    user_messages = [
        message
        for message in collect_conversation["messages"]
        if message["turn_id"] == collect["turn_id"] and message["author_type"] == "user"
    ]

    assert second["turn_id"] == collect["turn_id"]
    assert second["queue_status"] == "superseded"
    assert collect_envelope["normalized_summary"]["debounce_collected"] is True
    assert collect_envelope["normalized_summary"]["collected_message_count"] == 2
    assert "小吴，先记第一段" in collect_envelope["model_safe_text"]
    assert "小吴，再补第二段" in collect_envelope["model_safe_text"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"]["normalized_summary"]["collected_message_count"] == 2
    assert collect_queue["queue_policy"] == "collect"
    assert collect_queue["status"] == "queued"


def _run_wechat_turn(
    client: TestClient,
    *,
    peer_ref: str,
    event_id: str,
    message_text: str,
    attachments: list[dict[str, Any]],
    expected_reply: str,
) -> dict[str, Any]:
    previous_send_count = len(XiaowuWechatClient.send_calls)
    XiaowuWechatClient.events = [
        {
            "event_id": event_id,
            "source": {
                "peer_ref": peer_ref,
                "chat_type": "private",
                "display_name": "外部联系人",
            },
            "message": {
                "content_type": attachments[0]["type"] if attachments else "text",
                "text": message_text,
                "attachments": attachments,
            },
        }
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1

    binding = client.get(
        "/api/channels/delivery-bindings",
        params={"provider": "wechat", "status": "pending"},
    ).json()["items"][0]
    turn_id = binding["turn_id"]
    assert turn_id

    registry = cast(Any, client.app).state.registry
    portal = client.portal
    assert portal is not None

    async def _finish_turn() -> None:
        await _complete_turn(registry, turn_id, assistant_text=expected_reply)

    portal.call(_finish_turn)
    client.post("/api/channels/providers/wechat/deliver-due")
    stream = client.get(f"/api/chat/stream/{turn_id}")
    assert stream.status_code == 200, stream.text
    _wait_for(
        lambda: client.get(
            "/api/channels/delivery-bindings",
            params={"provider": "wechat", "turn_id": turn_id},
        ).json()["items"][0]["status"]
        == "sent",
        timeout=5.0,
    )
    delivery = client.get(
        "/api/channels/delivery-bindings",
        params={"provider": "wechat", "turn_id": turn_id},
    ).json()["items"][0]
    assert delivery["status"] == "sent", delivery
    assert len(XiaowuWechatClient.send_calls) > previous_send_count
    assert XiaowuWechatClient.send_calls[-1]["text"] == expected_reply

    turn_response = client.get(f"/api/chat/turns/{turn_id}").json()
    envelope = client.get(f"/api/chat/turns/{turn_id}/envelope").json()
    queue = client.get(f"/api/chat/turns/{turn_id}/queue").json()["item"]
    events = client.get(f"/api/chat/turns/{turn_id}/events").json()["items"]
    attachments_result = client.get(
        "/api/channels/attachments",
        params={"channel_event_id": binding["channel_event_id"]},
    ).json()["items"]
    trace = client.get(f"/api/traces/{turn_response['trace_id']}").json()

    return {
        "turn_response": turn_response,
        "turn_detail": turn_response,
        "envelope": envelope,
        "queue": queue,
        "events": events,
        "attachments": attachments_result,
        "trace": trace,
        "sent_text": XiaowuWechatClient.send_calls[-1]["text"],
        "reply_text": expected_reply,
        "secret_token": "secret-ref",
    }


def _find_memory_by_source(client: TestClient, source_type: str) -> dict[str, Any]:
    items = client.get("/api/memory", params={"member_id": "mem_xiaowu"}).json()["items"]
    for item in items:
        if item.get("source", {}).get("type") == source_type:
            return item
    raise AssertionError(f"memory source type {source_type} not found")


async def _complete_turn(
    registry: Any,
    turn_id: str,
    *,
    assistant_text: str,
) -> None:
    turn = await registry.chat.get_turn(turn_id)
    assert turn is not None
    assistant_message_id = f"msg_assistant_{turn_id.replace('-', '_')}"
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
    await registry.chat.update_queue_item(
        turn_id,
        status="completed",
        updated_at=now,
        completed_at=now,
    )


def _install_fake_wechat(client: TestClient, factory: type[XiaowuWechatClient]) -> None:
    factory.reset()
    registry = cast(Any, client.app).state.registry
    registry.config.channels.providers["wechat"].enabled = True
    registry.config.channels.providers["wechat"].poll_enabled = True
    connector = registry.channel_binding_service.connector_registry().get("wechat")
    cast(Any, connector).set_client_factory(factory)


def _disable_chat_background_execution(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    registry.chat_service._execution.schedule = lambda *args, **kwargs: None


def _bind_wechat_account(client: TestClient, display_name_hint: str) -> None:
    started = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": display_name_hint},
    )
    assert started.status_code == 200, started.text
    finalized = client.post(
        f"/api/channels/bind-sessions/{started.json()['bind_session_id']}/finalize"
    )
    assert finalized.status_code == 200, finalized.text


def _bind_member_default_brain(client: TestClient, member_id: str, brain_id: str) -> None:
    updated = client.patch(
        f"/api/members/{member_id}/default-brain",
        json={"brain_id": brain_id},
    )
    assert updated.status_code == 200, updated.text


def _create_local_brain(client: TestClient) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": "Xiaowu wechat brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "xiaowu-wechat-test-model",
            "is_local": True,
            "context_window": 4096,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["brain_id"])


def _create_turn(
    client: TestClient,
    *,
    session_id: str,
    conversation_id: str | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "session_id": session_id,
        "member_id": "mem_xiaowu",
        **payload,
    }
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    response = client.post("/api/chat/turn", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _reply_from_events(events: list[dict[str, Any]]) -> str:
    return "".join(
        str(event.get("payload", {}).get("text", ""))
        for event in events
        if event.get("event") == "response.delta"
    )


def _wait_for(
    condition: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 0.05,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(interval)
    raise AssertionError("condition was not met before timeout")


def _text_event(event_id: str, peer_ref: str, text: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "source": {"peer_ref": peer_ref, "chat_type": "private", "display_name": "外部联系人"},
        "message": {"content_type": "text", "text": text},
    }


class XiaowuWechatClient:
    events: ClassVar[list[dict[str, Any]]] = []
    send_calls: ClassVar[list[dict[str, str]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.events = []
        cls.send_calls = []

    @classmethod
    def create(cls, **kwargs: Any) -> XiaowuWechatClient:
        del kwargs
        return cls()

    async def start_login(self) -> dict[str, Any]:
        return {
            "status": "qr_ready",
            "qrcode": "QR_RAW_XIAOWU",
            "qrcode_image_content": "QR_IMAGE_XIAOWU",
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
            "account_id": "wxid-xiaowu-account-secret",
            "display_name": "小吴微信",
        }

    async def poll_events(self, account_id: str) -> Any:
        assert account_id == "wxid-xiaowu-account-secret"
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
        self.__class__.send_calls.append(
            {
                "account_id": account_id,
                "user_id": user_id,
                "text": f"audio:{len(audio_bytes)}",
            }
        )
        return {"message_id": f"audio-{len(self.__class__.send_calls)}-secret"}

    async def download_media(self, *, account_id: str, media_id: str) -> bytes:
        assert account_id == "wxid-xiaowu-account-secret"
        if media_id == "image-secret-ref":
            return _png_1x1()
        if media_id in {"audio-secret-ref", "audio-transcript-secret-ref"}:
            return _wav_1sec()
        if media_id == "file-docx-secret-ref":
            return _docx_bytes("文件正文：这份资料说明先做图片识别，再做文件识别。")
        if media_id == "zip-secret-ref":
            return b"PK\x03\x04xiaowu-zip"
        raise RuntimeError("missing media")


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _png_1x1() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO6n4i0AAAAASUVORK5CYII="
    )


def _wav_1sec() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        frames = b"\x00\x00" * 8000
        wav_file.writeframes(frames)
    return buffer.getvalue()
