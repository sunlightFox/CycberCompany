from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from brain.adapters import CancelToken, ModelAdapterError, ModelChatRequest, ModelChatResult
from brain.adapters.types import ModelStreamEvent
from core_types import ErrorCode, RiskLevel

from app.services.brain import BrainService


class _FakeBrainRepo:
    def __init__(self, brain: dict[str, Any]) -> None:
        self.brain = dict(brain)
        self.updates: list[dict[str, Any]] = []

    async def get_brain(self, brain_id: str) -> dict[str, Any] | None:
        if brain_id != self.brain["brain_id"]:
            return None
        return dict(self.brain)

    async def update_brain(self, brain_id: str, fields: dict[str, Any]) -> None:
        assert brain_id == self.brain["brain_id"]
        self.updates.append(dict(fields))
        self.brain.update(fields)


class _FakeSecretStore:
    def get_secret(self, secret_ref: str | None) -> str | None:
        del secret_ref
        return "secret"


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def write_event(self, **payload: Any) -> None:
        self.events.append(payload)


class _HealthyGateway:
    async def complete_chat(
        self,
        brain: dict[str, Any],
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> ModelChatResult:
        del brain, request, cancel_token
        return ModelChatResult(text="pong", usage={"output_tokens": 1}, finish_reason="stop")

    async def stream_chat(
        self,
        brain: dict[str, Any],
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> AsyncIterator[ModelStreamEvent]:
        del brain, request, cancel_token
        yield ModelStreamEvent(event="started")
        yield ModelStreamEvent(event="delta", text="pong")
        yield ModelStreamEvent(event="completed", finish_reason="stop")


class _NonStreamProtocolErrorGateway:
    async def complete_chat(
        self,
        brain: dict[str, Any],
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> ModelChatResult:
        del brain, request, cancel_token
        raise ModelAdapterError(
            ErrorCode.MODEL_PROTOCOL_ERROR,
            "模型非流式连接提前断开",
        )

    async def stream_chat(
        self,
        brain: dict[str, Any],
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> AsyncIterator[ModelStreamEvent]:
        del brain, request, cancel_token
        if False:
            yield ModelStreamEvent(event="started")


class _StreamProtocolErrorGateway(_HealthyGateway):
    async def stream_chat(
        self,
        brain: dict[str, Any],
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> AsyncIterator[ModelStreamEvent]:
        del brain, request, cancel_token
        raise ModelAdapterError(
            ErrorCode.MODEL_PROTOCOL_ERROR,
            "模型流式连接提前断开",
        )
        if False:
            yield ModelStreamEvent(event="started")


class _AutoFallbackGateway(_HealthyGateway):
    async def complete_chat(
        self,
        brain: dict[str, Any],
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> ModelChatResult:
        if brain.get("protocol_family") != "responses":
            raise ModelAdapterError(
                ErrorCode.MODEL_PROTOCOL_ERROR,
                "chat.completions 不可用",
            )
        return await super().complete_chat(brain, request, cancel_token)


def _brain(*, supports_stream: bool = True) -> dict[str, Any]:
    return {
        "brain_id": "brn_test",
        "display_name": "Test Brain",
        "endpoint": "http://127.0.0.1:9000/v1",
        "model_name": "gpt-test",
        "protocol_family": "chat_completions",
        "request_format": "chat_completions",
        "response_format": "openai_chat",
        "supports_stream": supports_stream,
        "streaming_supported": supports_stream,
    }


async def _reachable(endpoint: str) -> bool:
    del endpoint
    return True


@pytest.mark.anyio
async def test_verify_brain_persists_staged_capabilities_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeBrainRepo(_brain())
    audit = _FakeAudit()
    service = BrainService(repo=repo, secret_store=_FakeSecretStore(), audit=audit)
    service._gateway = _HealthyGateway()
    monkeypatch.setattr("app.services.brain._probe_tcp_reachability", _reachable)

    result = await service.verify_brain("brn_test", trace_id="trc_verify")

    assert result.status == "healthy"
    assert result.error_code is None
    assert result.verify_capabilities["configured_protocol_family"] == "chat_completions"
    assert result.verify_capabilities["protocol_family"] == "chat_completions"
    assert result.verify_capabilities["request_format"] == "chat_completions"
    assert result.verify_capabilities["response_format"] == "openai_chat"
    assert result.verify_capabilities["candidate_protocol_families"] == ["chat_completions"]
    assert result.verify_capabilities["selected_protocol_family"] == "chat_completions"
    assert result.verify_capabilities["supports_stream"] is True
    assert result.verify_capabilities["tcp_reachable"] is True
    assert result.verify_capabilities["endpoint_reachable"] is True
    assert result.verify_capabilities["auth_valid"] is True
    assert result.verify_capabilities["non_stream_valid"] is True
    assert result.verify_capabilities["stream_valid"] is True
    assert result.verify_capabilities["error_stage"] is None
    assert repo.updates[-1]["status"] == "healthy"
    assert repo.updates[-1]["verify_capabilities"]["stream_valid"] is True
    assert audit.events[-1]["action"] == "brain.verify"
    assert audit.events[-1]["risk_level"] == RiskLevel.R1


@pytest.mark.anyio
async def test_verify_brain_marks_non_stream_protocol_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeBrainRepo(_brain())
    service = BrainService(repo=repo, secret_store=_FakeSecretStore(), audit=_FakeAudit())
    service._gateway = _NonStreamProtocolErrorGateway()
    monkeypatch.setattr("app.services.brain._probe_tcp_reachability", _reachable)

    result = await service.verify_brain("brn_test")

    assert result.status == "unhealthy"
    assert result.error_code == ErrorCode.MODEL_PROTOCOL_ERROR.value
    assert result.message == "模型非流式连接提前断开"
    assert result.verify_capabilities["tcp_reachable"] is True
    assert result.verify_capabilities["selected_protocol_family"] == "chat_completions"
    assert result.verify_capabilities["non_stream_valid"] is False
    assert result.verify_capabilities["stream_valid"] is False
    assert result.verify_capabilities["error_stage"] == "non_stream_protocol"
    assert repo.updates[-1]["last_error_code"] == ErrorCode.MODEL_PROTOCOL_ERROR.value


@pytest.mark.anyio
async def test_verify_brain_marks_stream_protocol_stage_after_non_stream_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeBrainRepo(_brain())
    service = BrainService(repo=repo, secret_store=_FakeSecretStore(), audit=_FakeAudit())
    service._gateway = _StreamProtocolErrorGateway()
    monkeypatch.setattr("app.services.brain._probe_tcp_reachability", _reachable)

    result = await service.verify_brain("brn_test")

    assert result.status == "unhealthy"
    assert result.error_code == ErrorCode.MODEL_PROTOCOL_ERROR.value
    assert result.message == "模型流式连接提前断开"
    assert result.verify_capabilities["endpoint_reachable"] is True
    assert result.verify_capabilities["auth_valid"] is True
    assert result.verify_capabilities["non_stream_valid"] is True
    assert result.verify_capabilities["stream_valid"] is False
    assert result.verify_capabilities["error_stage"] == "stream_protocol"


@pytest.mark.anyio
async def test_verify_brain_auto_protocol_can_fallback_to_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _FakeBrainRepo(
        {
            **_brain(),
            "protocol_family": "auto",
            "request_format": "chat_completions",
            "response_format": "auto",
            "privacy_policy": {"codex_wire_api": "responses"},
        }
    )
    service = BrainService(repo=repo, secret_store=_FakeSecretStore(), audit=_FakeAudit())
    service._gateway = _AutoFallbackGateway()
    monkeypatch.setattr("app.services.brain._probe_tcp_reachability", _reachable)

    result = await service.verify_brain("brn_test")

    assert result.status == "healthy"
    assert result.verify_capabilities["configured_protocol_family"] == "auto"
    assert result.verify_capabilities["candidate_protocol_families"] == [
        "responses",
        "chat_completions",
    ]
    assert result.verify_capabilities["selected_protocol_family"] == "responses"
    assert result.verify_capabilities["protocol_family"] == "responses"
    assert result.verify_capabilities["request_format"] == "responses"
