from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.schemas.tasks import ToolExecuteRequest, ToolExecuteResponse


def test_phase83_before_tool_call_can_block_and_audit(client: TestClient) -> None:
    registry = client.app.state.registry

    async def _blocker(_hook_input: dict[str, Any]) -> dict[str, Any]:
        return {"status": "blocked", "reason_code": "test_blocked", "blocked": True}

    registry.chat_hook_runtime.register_hook(
        stage="before_tool_call",
        name="test.before_tool_call.blocker",
        handler=_blocker,
    )

    with pytest.raises(AppError) as exc:
        _run_async(
            registry.tool_runtime.execute(
                ToolExecuteRequest(
                    member_id="mem_xiaoyao",
                    tool_name="browser.search",
                    args={"query": "phase83"},
                ),
            )
        )
    assert str(exc.value.code) == "TOOL_PERMISSION_DENIED"
    audits = _run_async(registry.audit_service.list_events())
    assert any(item.action == "chat.hook.before_tool_call" for item in audits.items)


def test_phase83_before_tool_call_rewrite_reaches_dispatcher(client: TestClient) -> None:
    registry = client.app.state.registry
    captured: dict[str, Any] = {}

    async def _rewrite(_hook_input: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "rewritten",
            "reason_code": "rewrite_args",
            "rewritten_payload": {"args": {"query": "rewritten"}},
        }

    async def _fake_dispatch(request: Any, *, trace_id: str | None = None) -> ToolExecuteResponse:
        captured["args"] = request.args
        return ToolExecuteResponse(
            tool_call={
                "tool_call_id": "toolcall_phase83",
                "organization_id": "org_default",
                "task_id": None,
                "step_id": None,
                "tool_name": request.tool_name,
                "source": "builtin",
                "status": "completed",
                "idempotency_key": request.idempotency_key,
                "args_redacted": request.args,
                "result_redacted": {},
                "handle_ids": [],
                "artifact_ids": [],
                "capability_decision_id": None,
                "safety_decision_id": None,
                "safety_decision": {},
                "policy_snapshot": {},
                "resolved_asset_refs": [],
                "risk_level": "R1",
                "approval_id": None,
                "timeout_seconds": None,
                "error_code": None,
                "error_summary": None,
                "trace_id": trace_id,
                "created_at": "2026-05-10T00:00:00Z",
                "updated_at": "2026-05-10T00:00:00Z",
            },
            result={"ok": True},
        )

    registry.chat_hook_runtime.register_hook(
        stage="before_tool_call",
        name="test.before_tool_call.rewrite",
        handler=_rewrite,
    )
    registry.tool_runtime._dispatcher.execute = _fake_dispatch

    _run_async(
        registry.tool_runtime.execute(
                ToolExecuteRequest(
                    member_id="mem_xiaoyao",
                    tool_name="browser.search",
                    args={"query": "original"},
                ),
            )
        )
    assert captured["args"]["query"] == "rewritten"


def test_phase83_after_tool_call_sanitizes_raw_output(client: TestClient) -> None:
    registry = client.app.state.registry

    async def _fake_dispatch(request: Any, *, trace_id: str | None = None) -> ToolExecuteResponse:
        return ToolExecuteResponse(
            tool_call={
                "tool_call_id": "toolcall_phase83_sanitize",
                "organization_id": "org_default",
                "task_id": None,
                "step_id": None,
                "tool_name": request.tool_name,
                "source": "builtin",
                "status": "completed",
                "idempotency_key": request.idempotency_key,
                "args_redacted": request.args,
                "result_redacted": {},
                "handle_ids": [],
                "artifact_ids": [],
                "capability_decision_id": None,
                "safety_decision_id": None,
                "safety_decision": {},
                "policy_snapshot": {},
                "resolved_asset_refs": [],
                "risk_level": "R1",
                "approval_id": None,
                "timeout_seconds": None,
                "error_code": None,
                "error_summary": None,
                "trace_id": trace_id,
                "created_at": "2026-05-10T00:00:00Z",
                "updated_at": "2026-05-10T00:00:00Z",
            },
            result={"stdout": "token=secret-value\n" + ("A" * 2000)},
        )

    registry.tool_runtime._dispatcher.execute = _fake_dispatch
    response = _run_async(
        registry.tool_runtime.execute(
                ToolExecuteRequest(
                    member_id="mem_xiaoyao",
                    tool_name="terminal.readonly",
                    args={"command": "echo hello"},
                ),
            )
        )
    assert "secret-value" not in response.result["stdout"]
    assert "[REDACTED" in response.result["stdout"]


def test_phase83_before_finalize_and_before_memory_write_contracts(client: TestClient) -> None:
    registry = client.app.state.registry
    finalize = _run_async(
        registry.chat_hook_runtime.run_before_finalize(
            {
                "trace_id": None,
                "conversation_id": "conv_default",
                "turn_id": None,
                "member_id": "mem_xiaoyao",
                "session_id": "sess_phase83",
                "channel": "local",
                "payload": {
                    "plain_text": "trace_id=trc_phase83 approval_id=apr_123 tool_call_id=toolcall_456",
                    "summary": "trace_id=trc_phase83",
                    "response_plan": {
                        "plain_text": "trace_id=trc_phase83",
                        "summary": "trace_id=trc_phase83",
                    },
                },
            }
        )
    )
    assert finalize["status"] == "rewritten"
    assert "trace_id" not in str(finalize["rewritten_payload"]["plain_text"]).lower()

    memory = _run_async(
        registry.chat_hook_runtime.run_before_memory_write(
            {
                "trace_id": None,
                "conversation_id": None,
                "turn_id": None,
                "member_id": "mem_xiaoyao",
                "session_id": None,
                "channel": "local",
                "payload": {"source": {"type": "conversation_turn"}},
            }
        )
    )
    assert memory["blocked"] is True
    assert memory["reason_code"] == "memory_source_minimum_fields_missing"


def test_phase83_advisory_hook_failure_downgrades_with_diagnostic(client: TestClient) -> None:
    registry = client.app.state.registry

    async def _broken(_hook_input: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    registry.chat_hook_runtime.register_hook(
        stage="after_context_build",
        name="test.after_context_build.broken",
        handler=_broken,
    )
    result = _run_async(
        registry.chat_hook_runtime.run_after_context_build(
            {
                "trace_id": None,
                "conversation_id": "conv_default",
                "turn_id": None,
                "member_id": "mem_xiaoyao",
                "session_id": "sess_phase83_advisory",
                "channel": "local",
                "payload": {"context_packet_id": "ctx_123"},
            }
        )
    )
    assert result["blocked"] is False
    assert result["status"] == "failed"
    assert result["trace_annotations"]["fail_closed_applied"] is True


def _run_async(value: Any) -> Any:
    return asyncio.run(value)
