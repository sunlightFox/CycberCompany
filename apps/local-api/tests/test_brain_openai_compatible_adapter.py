from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from brain.adapters import CancelToken, ModelChatRequest, OpenAICompatibleClient
from brain.adapters.types import ModelStreamEvent


def _request(*, stream: bool) -> ModelChatRequest:
    return ModelChatRequest(
        model="test-model",
        messages=[{"role": "user", "content": "ping"}],
        temperature=0,
        max_output_tokens=8,
        top_p=1,
        timeout_seconds=15,
        stream=stream,
        trace_id="trc_test",
        turn_id="turn_test",
        route_id="route_test",
        privacy_level="low",
        retry_count=0,
    )


class _MockResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any | None = None,
        lines: list[str] | None = None,
        content_type: str = "application/json",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self._lines = list(lines or [])
        self.headers = {"content-type": content_type}
        self.text = (
            json.dumps(json_data, ensure_ascii=False)
            if json_data is not None
            else "\n".join(self._lines)
        )

    async def __aenter__(self) -> _MockResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def json(self) -> Any:
        return self._json_data

    async def aread(self) -> bytes:
        return self.text.encode("utf-8")

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _MockAsyncClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def __aenter__(self) -> _MockAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _MockResponse:
        del headers
        if url.endswith("/chat/completions"):
            return _MockResponse(
                json_data={
                    "id": "resp_chat",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "reasoning_content": "pong-from-reasoning",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                }
            )
        return _MockResponse(
            json_data={
                "id": "resp_responses",
                "status": "completed",
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "pong-from-responses"},
                        ]
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 4},
            }
        )

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> _MockResponse:
        del method, headers, json
        if url.endswith("/chat/completions"):
            return _MockResponse(
                lines=[
                    'data: {"choices":[{"delta":{"reasoning_content":"pong"}}]}',
                    'data: {"choices":[{"finish_reason":"stop"}],"usage":{"completion_tokens":1}}',
                ],
                content_type="text/event-stream",
            )
        return _MockResponse(
            lines=[
                'data: {"type":"response.output_text.delta","delta":"pong"}',
                'data: {"type":"response.completed","status":"completed","usage":{"output_tokens":1}}',
            ],
            content_type="text/event-stream",
        )


class _FallbackAsyncClient(_MockAsyncClient):
    async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _MockResponse:
        del headers, json
        return _MockResponse(
            json_data={
                "id": "resp_chat_empty",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            }
        )

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> _MockResponse:
        del method, url, headers, json
        return _MockResponse(
            lines=[
                "event: response.created",
                'data: {"type":"response.created","response":{"id":"resp_fallback"}}',
                "",
                "event: response.output_text.delta",
                'data: {"type":"response.output_text.delta","delta":"pong"}',
                "",
                "event: response.completed",
                'data: {"type":"response.completed","status":"completed","usage":{"output_tokens":1}}',
            ],
            content_type="text/event-stream",
        )


@pytest.mark.anyio
async def test_openai_compatible_chat_completion_accepts_reasoning_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("brain.adapters.openai_compatible.httpx.AsyncClient", _MockAsyncClient)
    client = OpenAICompatibleClient(
        "https://example.com/v1",
        "secret",
        protocol_family="chat_completions",
    )

    result = await client.complete_chat(_request(stream=False), CancelToken())

    assert result.text == "pong-from-reasoning"
    assert result.finish_reason == "stop"


@pytest.mark.anyio
async def test_openai_compatible_chat_stream_accepts_reasoning_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("brain.adapters.openai_compatible.httpx.AsyncClient", _MockAsyncClient)
    client = OpenAICompatibleClient(
        "https://example.com/v1",
        "secret",
        protocol_family="chat_completions",
    )

    events = [
        event
        async for event in client.stream_chat(_request(stream=True), CancelToken())
    ]

    assert any(event.event == "delta" and event.text == "pong" for event in events)
    assert any(event.event == "completed" for event in events)


@pytest.mark.anyio
async def test_openai_compatible_responses_modes_are_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("brain.adapters.openai_compatible.httpx.AsyncClient", _MockAsyncClient)
    client = OpenAICompatibleClient(
        "https://example.com/v1",
        "secret",
        protocol_family="responses",
    )

    result = await client.complete_chat(_request(stream=False), CancelToken())
    events = [
        event
        async for event in client.stream_chat(_request(stream=True), CancelToken())
    ]

    assert result.text == "pong-from-responses"
    assert any(event.event == "delta" and event.text == "pong" for event in events)
    assert any(event.event == "completed" for event in events)


@pytest.mark.anyio
async def test_openai_compatible_chat_completion_falls_back_to_stream_for_codex_proxy_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("brain.adapters.openai_compatible.httpx.AsyncClient", _FallbackAsyncClient)
    client = OpenAICompatibleClient(
        "https://example.com/v1",
        "secret",
        protocol_family="chat_completions",
    )

    result = await client.complete_chat(_request(stream=False), CancelToken())

    assert result.text == "pong"
    assert result.metadata["fallback"] == "stream_completion"
