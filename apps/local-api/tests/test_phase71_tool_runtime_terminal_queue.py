from __future__ import annotations

import anyio
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient


def test_phase71_tool_runtime_diagnostic_exposes_terminal_queue_and_lanes(
    client: TestClient,
) -> None:
    response = client.get("/api/system/tool-runtime")
    assert response.status_code == 200, response.text
    terminal = response.json()["terminal"]

    assert terminal["runtime"] == "tool_terminal_runtime"
    assert terminal["maturity"] == "runtime_native"
    assert terminal["execution_mode"] == "queued_sandboxed_sync"
    assert terminal["queue_enabled"] is True
    assert terminal["lane_model"] == "in_memory_lanes_v1"
    assert terminal["snapshot_supported"] is True
    assert terminal["reset_supported"] is True
    assert set(terminal["lanes"]) == {
        "main",
        "readonly",
        "browser_assist",
        "background",
        "recovery",
    }
    assert isinstance(terminal["queue_snapshot"], list)
    assert {item["lane"] for item in terminal["queue_snapshot"]} == set(terminal["lanes"])


def test_phase71_terminal_run_returns_unified_result_fields_for_readonly_and_approved_mutation(
    client: TestClient,
) -> None:
    readonly_task = _create_task(client, "phase71 readonly")
    readonly_result = _execute_terminal_via_api(
        client,
        task_id=readonly_task["task_id"],
        command="echo phase71-readonly",
    )["result"]

    _assert_terminal_result_shape(readonly_result)
    assert readonly_result["status"] == "completed"
    assert readonly_result["execution_semantics"]["lane"] == "readonly"
    assert readonly_result["execution_semantics"]["command_class"] == "readonly"
    assert readonly_result["approval_state"]["status"] in {"not_required", "resolved"}
    assert "phase71-readonly" in readonly_result["output_preview"]

    mutation_task = _create_task(client, "phase71 approved")
    mutation_result = _execute_terminal_via_api(
        client,
        task_id=mutation_task["task_id"],
        command="python -c \"print('phase71-approved')\"",
    )["result"]

    _assert_terminal_result_shape(mutation_result)
    assert mutation_result["status"] == "completed"
    assert mutation_result["execution_semantics"]["lane"] == "readonly"
    assert mutation_result["approval_state"]["status"] == "resolved"
    assert "phase71-approved" in mutation_result["output_preview"]


def test_phase71_terminal_timeout_releases_lane_and_reset_lane_keeps_runtime_usable(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    task = _create_task(client, "phase71 timeout release")
    timed_out = _execute_terminal_via_api(
        client,
        task_id=task["task_id"],
        command="python -c \"import time; time.sleep(2)\"",
        timeout_seconds=1,
        expect_status=504,
    )
    assert timed_out["error"]["code"] == "TOOL_TIMEOUT"

    snapshot = registry.tool_runtime._terminal_runtime.snapshot()["queue_snapshot"]
    readonly_lane = next(item for item in snapshot if item["lane"] == "readonly")
    assert readonly_lane["active_count"] == 0
    assert readonly_lane["queued_count"] == 0

    reset_count = anyio.run(registry.tool_runtime._terminal_runtime.reset_lane, "readonly")
    assert reset_count in {0, 1}

    follow_up_task = _create_task(client, "phase71 after reset")
    follow_up = _execute_terminal_via_api(
        client,
        task_id=follow_up_task["task_id"],
        command="echo phase71-after-reset",
    )
    assert "phase71-after-reset" in follow_up["result"]["output_preview"]


def test_phase71_builtin_runtime_terminal_branch_delegates_to_terminal_runtime(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    captured: dict[str, Any] = {}

    async def _fake_execute(
        request: Any,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> Any:
        from app.services.tools import ToolRunOutcome

        captured["tool_name"] = request.tool_name
        captured["tool_call_id"] = tool_call_id
        captured["organization_id"] = organization_id
        captured["trace_id"] = trace_id
        return ToolRunOutcome(
            result={
                "status": "completed",
                "execution_semantics": {
                    "lane": "readonly",
                    "command_class": "readonly",
                    "queue_mode": "serialized",
                    "sync_execution": True,
                },
                "evidence_refs": {"log_artifact_id": None, "dlp_report_id": None},
                "approval_state": {"status": "not_required", "approval_id": None},
                "sandbox_profile": None,
                "backend_status": None,
                "degraded_reason": None,
                "resource_usage": {},
                "cleanup": {},
                "retryable": False,
            },
            artifacts=[],
        )

    registry.tool_runtime._terminal_runtime.execute = _fake_execute

    async def _call() -> Any:
        from app.schemas.tasks import ToolExecuteRequest

        return await registry.tool_runtime._builtin_runtime.execute(
            ToolExecuteRequest(
                task_id="task_phase71_delegate",
                tool_name="terminal.run",
                args={"command": "echo delegated"},
            ),
            tool_call_id="call_phase71_delegate",
            organization_id="org_phase71_delegate",
            trace_id="trace_phase71_delegate",
        )

    outcome = anyio.run(_call)
    assert captured["tool_name"] == "terminal.run"
    assert captured["tool_call_id"] == "call_phase71_delegate"
    assert captured["organization_id"] == "org_phase71_delegate"
    assert captured["trace_id"] == "trace_phase71_delegate"
    assert outcome.result["execution_semantics"]["lane"] == "readonly"


def test_phase71_terminal_read_log_also_uses_unified_result_fields(client: TestClient) -> None:
    task = _create_task(client, "phase71 read log")
    executed = _execute_terminal_via_api(
        client,
        task_id=task["task_id"],
        command="echo phase71-log-read",
    )
    log_artifact_id = executed["result"]["log_artifact_id"]
    read_log = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.read_log",
            "args": {"artifact_id": log_artifact_id},
        },
    )
    assert read_log.status_code == 200, read_log.text
    result = read_log.json()["result"]

    _assert_terminal_result_shape(result)
    assert result["status"] == "completed"
    assert result["reason_code"] == "terminal_log_available"
    assert result["execution_semantics"]["lane"] == "readonly"
    assert result["execution_semantics"]["command_class"] == "log_read"
    assert result["evidence_refs"]["log_artifact_id"] == log_artifact_id
    assert "phase71-log-read" in result["content_preview"]


def test_phase71_tools_py_terminal_methods_are_thin_delegators() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "tools.py"
    ).read_text(encoding="utf-8")

    execute_start = source.index("async def _execute_terminal_tool(")
    execute_end = source.index("async def _update_terminal_policy_snapshot(")
    execute_block = source[execute_start:execute_end]
    assert "return await self._terminal_runtime.execute(" in execute_block
    assert "run_terminal_command(" not in execute_block
    assert "scan_output(" not in execute_block
    assert "write_text(" not in execute_block

    read_start = source.index("async def _read_terminal_log(")
    read_end = source.index("async def _execute_deployment_tool(")
    read_block = source[read_start:read_end]
    assert "return await self._terminal_runtime.read_log(request)" in read_block
    assert "list_artifacts(request.task_id)" not in read_block
    assert "read_preview(" not in read_block


def _assert_terminal_result_shape(result: dict[str, Any]) -> None:
    assert "status" in result
    assert "execution_semantics" in result
    assert "evidence_refs" in result
    assert "approval_state" in result
    assert "sandbox_profile" in result
    assert "backend_status" in result
    assert "degraded_reason" in result
    assert "resource_usage" in result
    assert "cleanup" in result
    assert "retryable" in result


def _create_task(client: TestClient, goal: str) -> dict[str, Any]:
    response = client.post(
        "/api/tasks",
        json={"goal": goal, "mode_hint": "workflow", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _execute_terminal_via_api(
    client: TestClient,
    *,
    task_id: str,
    command: str,
    timeout_seconds: int | None = None,
    expect_status: int = 200,
) -> dict[str, Any]:
    payload = {
        "task_id": task_id,
        "tool_name": "terminal.run",
        "args": {
            "command": command,
            **({"timeout_seconds": timeout_seconds} if timeout_seconds is not None else {}),
        },
    }
    first = client.post("/api/tools/execute", json=payload)
    assert first.status_code == 200, first.text
    first_body = first.json()
    if first_body.get("approval"):
        approval_id = first_body["approval"]["approval_id"]
        approved = client.post(
            f"/api/approvals/{approval_id}/approve",
            json={"reason": "phase71 terminal runtime"},
        )
        assert approved.status_code == 200, approved.text
        second = client.post(
            "/api/tools/execute",
            json={**payload, "approval_id": approval_id},
        )
        assert second.status_code == expect_status, second.text
        return second.json()
    assert first.status_code == expect_status, first.text
    return first_body
