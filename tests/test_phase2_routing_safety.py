from __future__ import annotations

import httpx
import pytest
from brain import BrainRouter, BrainRouteRequest
from brain.adapters import CancelToken, ModelAdapterError, ModelChatRequest, OpenAICompatibleClient
from core_types import ErrorCode
from safety_service import SafetyService


def test_safety_privacy_classifier_blocks_cloud_for_sensitive_text() -> None:
    result = SafetyService().classify_chat_input(
        "token=abc123 and C:\\Users\\alice\\secret.txt"
    )

    assert result.privacy_level == "high"
    assert result.allow_cloud is False
    assert {"token", "local_path"}.issubset(set(result.sensitivity_hits))
    assert "abc123" not in result.redacted_text
    assert "alice" not in result.redacted_text


@pytest.mark.asyncio
async def test_model_router_prefers_local_and_blocks_cloud_for_high_privacy() -> None:
    decision = await BrainRouter().route(
        BrainRouteRequest(
            text="解释一下 Context Gateway",
            default_brain_id="brn_cloud",
            privacy_level="high",
            available_brains=[
                _brain("brn_cloud", is_local=False, allow_cloud=True),
                _brain("brn_local", is_local=True),
            ],
            model_routing_config={"routing": {"privacy": {"high": {"allow_cloud": False}}}},
        )
    )

    assert decision.model_route is not None
    assert decision.model_route.primary_brain_id == "brn_local"
    assert decision.model_route.privacy_policy == "local_only"


@pytest.mark.asyncio
async def test_model_router_returns_none_when_only_cloud_is_available_for_high_privacy() -> None:
    decision = await BrainRouter().route(
        BrainRouteRequest(
            text="api_key=placeholder-secret",
            privacy_level="high",
            available_brains=[_brain("brn_cloud", is_local=False, allow_cloud=True)],
            model_routing_config={"routing": {"privacy": {"high": {"allow_cloud": False}}}},
        )
    )

    assert decision.model_route is None
    assert decision.rejected_candidates[0]["reason"] == "privacy_high"


@pytest.mark.asyncio
async def test_model_router_reports_rejected_candidates() -> None:
    decision = await BrainRouter().route(
        BrainRouteRequest(
            text="解释一下 Context Gateway",
            default_brain_id="brn_default",
            privacy_level="medium",
            available_brains=[
                _brain("brn_default", is_local=True),
                {**_brain("brn_tiny", is_local=True), "context_window": 1024},
                {**_brain("brn_bad", is_local=True), "status": "unhealthy"},
            ],
            model_routing_config={"routing": {"reserved_output_tokens": 1024}},
        )
    )

    assert decision.model_route is not None
    rejected = {
        item["brain_id"]: item["reason"]
        for item in decision.model_route.rejected_candidates
    }
    assert rejected["brn_tiny"] == "context_too_small"
    assert rejected["brn_bad"] == "unhealthy"


@pytest.mark.asyncio
async def test_openai_adapter_streaming_success_and_completion() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        body = (
            'data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
            'data: {"choices":[{"finish_reason":"stop"}],"usage":{"total_tokens":3}}\n\n'
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    client = OpenAICompatibleClient(
        "https://example.test/v1",
        "test-key",
        transport=httpx.MockTransport(handler),
    )
    events = [
        event
        async for event in client.stream_chat(_request(), CancelToken())
    ]

    assert [event.event for event in events] == ["started", "delta", "delta", "completed"]
    assert "".join(event.text for event in events if event.event == "delta") == "你好"


@pytest.mark.asyncio
async def test_openai_adapter_streaming_prefers_text_when_usage_is_in_chunk() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"content":"O"}}],"usage":{"total_tokens":1}}\n\n'
            'data: {"choices":[{"delta":{"content":"K"}}],"usage":{"total_tokens":2}}\n\n'
            'data: {"choices":[{"finish_reason":"stop"}],"usage":{"total_tokens":3}}\n\n'
        )
        return httpx.Response(200, content=body.encode("utf-8"))

    client = OpenAICompatibleClient(
        "https://example.test/v1",
        transport=httpx.MockTransport(handler),
    )
    events = [
        event
        async for event in client.stream_chat(_request(), CancelToken())
    ]

    assert [event.event for event in events] == ["started", "delta", "delta", "completed"]
    assert "".join(event.text for event in events if event.event == "delta") == "OK"


@pytest.mark.asyncio
async def test_openai_adapter_stream_interruption_is_normalized() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
        )

    client = OpenAICompatibleClient(
        "https://example.test/v1",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelAdapterError) as exc_info:
        async for _event in client.stream_chat(_request(retry_count=0), CancelToken()):
            pass

    assert exc_info.value.code == ErrorCode.MODEL_STREAM_INTERRUPTED


def _brain(brain_id: str, *, is_local: bool, allow_cloud: bool = False) -> dict:
    return {
        "brain_id": brain_id,
        "display_name": brain_id,
        "provider": "openai_compatible",
        "endpoint": "http://127.0.0.1:11434",
        "model_name": "test-model",
        "is_local": is_local,
        "context_window": 4096,
        "supports_tools": False,
        "supports_vision": False,
        "allow_fallback": True,
        "allow_cloud": allow_cloud,
        "status": "healthy",
    }


def _request(*, retry_count: int = 1) -> ModelChatRequest:
    return ModelChatRequest(
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.3,
        max_output_tokens=64,
        top_p=0.9,
        timeout_seconds=10,
        stream=True,
        trace_id="trc_test",
        turn_id="turn_test",
        route_id="route_test",
        privacy_level="low",
        first_token_timeout_seconds=1,
        retry_count=retry_count,
    )
