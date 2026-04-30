from __future__ import annotations

import json

import httpx
import pytest
from cycber_cli.chat import send_message
from cycber_cli.http_client import ApiError, CycberApiClient
from cycber_cli.state import CliState


@pytest.mark.asyncio
async def test_phase32_cli_client_streams_chat_turn_with_mock_api() -> None:
    async with CycberApiClient("http://test", transport=_chat_transport(stream=True)) as client:
        state = CliState()
        result = await send_message(client, state, "你好", stream=True, include_diagnostics=True)

    assert result.created["turn_id"] == "turn_1"
    assert result.text == "你好，我在。"
    assert state.conversation_id == "conv_default_xiaoyao"
    assert state.member_id == "mem_xiaoyao"
    assert state.last_turn_id == "turn_1"
    assert "brain" in result.diagnostics


@pytest.mark.asyncio
async def test_phase32_cli_client_reads_persisted_events_without_stream() -> None:
    async with CycberApiClient("http://test", transport=_chat_transport(stream=False)) as client:
        state = CliState()
        result = await send_message(client, state, "你好", stream=False)

    assert result.text == "持久事件回复"


@pytest.mark.asyncio
async def test_phase32_cli_client_raises_project_api_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "TOOL_PERMISSION_DENIED"}})

    async with CycberApiClient("http://test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ApiError) as exc_info:
            await client.get_json("/api/tools/execute")

    assert exc_info.value.status_code == 403
    assert exc_info.value.payload["error"]["code"] == "TOOL_PERMISSION_DENIED"


def _chat_transport(*, stream: bool) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat/conversations":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "conversation_id": "conv_default_xiaoyao",
                            "primary_member_id": "mem_xiaoyao",
                        }
                    ]
                },
            )
        if request.url.path == "/api/chat/turn" and request.method == "POST":
            payload = json.loads(request.content.decode())
            assert payload["input"]["text"] == "你好"
            return httpx.Response(
                200,
                json={
                    "turn_id": "turn_1",
                    "conversation_id": "conv_default_xiaoyao",
                    "message_id": "msg_1",
                    "assistant_message_id": None,
                    "trace_id": "trc_1",
                    "status": "created",
                    "stream_url": "/api/chat/stream/turn_1",
                },
            )
        if request.url.path == "/api/chat/stream/turn_1" and stream:
            return httpx.Response(
                200,
                text=(
                    "event: response.delta\n"
                    "data: {\"payload\":{\"text\":\"你好，我在。\"}}\n\n"
                    "event: turn.completed\n"
                    "data: {\"payload\":{\"status\":\"completed\"}}\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )
        if request.url.path == "/api/chat/turns/turn_1/events":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "event_type": "response.delta",
                            "payload": {"text": "持久事件回复"},
                        }
                    ]
                },
            )
        if request.url.path.endswith("/brain-decision"):
            return httpx.Response(200, json={"intent": {"primary_intent": "casual_chat"}})
        if request.url.path.endswith("/semantic-review"):
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
        if request.url.path.endswith("/tone-policy"):
            return httpx.Response(200, json={"tone_mode": "default"})
        if request.url.path.endswith("/response-quality"):
            return httpx.Response(200, json={"quality_markers": {"no_leakage": True}})
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    return httpx.MockTransport(handler)
