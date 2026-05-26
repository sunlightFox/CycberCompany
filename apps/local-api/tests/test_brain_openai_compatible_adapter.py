from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from brain.adapters import CancelToken, ModelChatRequest, OpenAICompatibleClient
from brain.adapters.errors import map_http_status
from core_types import ErrorCode


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


def test_openai_compatible_maps_429_to_model_unavailable() -> None:
    error = map_http_status(429, "qpm limit exceeded")

    assert error.code == ErrorCode.MODEL_UNAVAILABLE
    assert "qpm limit exceeded" in error.message


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

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> _MockResponse:
        del headers
        if url.endswith("/chat/completions"):
            return _MockResponse(
                json_data={
                    "id": "resp_chat",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "pong-from-content",
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
                    'data: {"choices":[{"delta":{"content":"visible-pong"}}]}',
                    'data: {"choices":[{"finish_reason":"stop"}],"usage":{"completion_tokens":1}}',
                ],
                content_type="text/event-stream",
            )
        return _MockResponse(
            lines=[
                'data: {"type":"response.output_text.delta","delta":"pong"}',
                (
                    'data: {"type":"response.completed","status":"completed",'
                    '"usage":{"output_tokens":1}}'
                ),
            ],
            content_type="text/event-stream",
        )


class _FallbackAsyncClient(_MockAsyncClient):
    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> _MockResponse:
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
                (
                    'data: {"type":"response.completed","status":"completed",'
                    '"usage":{"output_tokens":1}}'
                ),
            ],
            content_type="text/event-stream",
        )


class _PayloadCaptureAsyncClient(_MockAsyncClient):
    payloads: list[dict[str, Any]] = []

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> _MockResponse:
        self.payloads.append(dict(json))
        return await super().post(url, headers=headers, json=json)


@pytest.mark.anyio
async def test_openai_compatible_chat_completion_ignores_reasoning_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("brain.adapters.openai_compatible.httpx.AsyncClient", _MockAsyncClient)
    client = OpenAICompatibleClient(
        "https://example.com/v1",
        "secret",
        protocol_family="chat_completions",
    )

    result = await client.complete_chat(_request(stream=False), CancelToken())

    assert result.text == "pong-from-content"
    assert result.finish_reason == "stop"


@pytest.mark.anyio
async def test_openai_compatible_chat_stream_ignores_reasoning_content(
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

    assert not any(event.event == "delta" and event.text == "pong" for event in events)
    assert any(event.event == "delta" and event.text == "visible-pong" for event in events)
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
async def test_openai_compatible_responses_payload_includes_codex_reasoning_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _PayloadCaptureAsyncClient.payloads = []
    monkeypatch.setattr(
        "brain.adapters.openai_compatible.httpx.AsyncClient",
        _PayloadCaptureAsyncClient,
    )
    client = OpenAICompatibleClient(
        "https://example.com/v1",
        "secret",
        protocol_family="responses",
        reasoning_effort="medium",
        text_verbosity="medium",
    )

    await client.complete_chat(_request(stream=False), CancelToken())

    payload = _PayloadCaptureAsyncClient.payloads[-1]
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["text"] == {"verbosity": "medium"}


@pytest.mark.anyio
async def test_openai_compatible_responses_payload_marks_assistant_history_as_output_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _PayloadCaptureAsyncClient.payloads = []
    monkeypatch.setattr(
        "brain.adapters.openai_compatible.httpx.AsyncClient",
        _PayloadCaptureAsyncClient,
    )
    client = OpenAICompatibleClient(
        "https://example.com/v1",
        "secret",
        protocol_family="responses",
    )
    request = _request(stream=False)
    request.messages = [
        {"role": "system", "content": "answer briefly"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "previous answer"},
        {"role": "user", "content": "follow up"},
    ]

    await client.complete_chat(request, CancelToken())

    payload = _PayloadCaptureAsyncClient.payloads[-1]
    content_types = [item["content"][0]["type"] for item in payload["input"]]
    assert content_types == ["input_text", "input_text", "output_text", "input_text"]


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
