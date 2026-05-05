from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import anyio
from app.core.errors import AppError
from app.services.turn_recovery import (
    MAX_ATTEMPTS_PER_ERROR,
    MAX_ATTEMPTS_PER_TURN,
    RECOVERY_ACTIONS,
    TurnRecoveryService,
)
from core_types import ChatEventType, ErrorCode, TaskMode, TaskStatus, TraceSpanType
from fastapi.testclient import TestClient


def test_phase60_recovery_actions_are_fixed_whitelist() -> None:
    assert RECOVERY_ACTIONS == {
        "retry_failed_step",
        "retry_task_from_recovery_plan",
        "rebuild_minimal_context",
        "fallback_model_route",
        "ask_user_for_missing_input",
        "request_approval",
        "stop_unrecoverable",
    }
    assert MAX_ATTEMPTS_PER_TURN == 3
    assert MAX_ATTEMPTS_PER_ERROR == 2


def test_phase60_task_retry_resets_failed_step_and_reexecutes(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    task = client.post(
        "/api/tasks",
        json={
            "goal": "搜索 phase60 retry failed step",
            "mode_hint": "workflow",
            "auto_start": True,
        },
    ).json()
    task_id = task["task_id"]
    replay = client.get(f"/api/tasks/{task_id}/replay").json()
    failed_step = next(step for step in replay["steps"] if step["step_key"] == "knowledge_search")
    original_idempotency_key = failed_step["idempotency_key"]

    anyio.run(
        registry.tasks.update_step,
        failed_step["step_id"],
        {
            "status": "failed",
            "error_code": ErrorCode.TOOL_EXECUTION_FAILED.value,
            "error_summary": "first failure token=phase60-secret",
        },
    )
    anyio.run(
        registry.tasks.update_task,
        task_id,
        {
            "status": "failed",
            "failure_reason": "tool failed token=phase60-secret",
        },
    )

    retried = client.post(f"/api/tasks/{task_id}/retry")
    retry_replay = client.get(f"/api/tasks/{task_id}/replay").json()
    retried_step = next(
        step for step in retry_replay["steps"] if step["step_id"] == failed_step["step_id"]
    )
    events = [event["event_type"] for event in retry_replay["events"]]
    serialized = json.dumps(retry_replay, ensure_ascii=False)

    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "completed"
    assert retried_step["status"] == "completed"
    assert retried_step["retry_count"] == 1
    assert retried_step["idempotency_key"] != original_idempotency_key
    assert "task.step.retry" in events
    assert "task.completed" in events
    assert "phase60-secret" not in serialized


def test_phase60_chat_same_turn_recovers_failed_tool_and_records_trace(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    original_execute = registry.tool_runtime.execute
    calls = {"count": 0}

    async def flaky_execute(request: Any, *, trace_id: str | None = None) -> Any:
        if request.tool_name == "knowledge.search" and calls["count"] == 0:
            calls["count"] += 1
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "temporary tool failure token=phase60-secret C:\\Users\\Administrator\\secret.txt",
                status_code=500,
            )
        return await original_execute(request, trace_id=trace_id)

    registry.tool_runtime.execute = flaky_execute
    try:
        conversation_id = _conversation_id(client)
        created = _create_turn(
            client,
            conversation_id,
            "phase60-recover-success",
            "请调研 phase60 恢复链路并输出任务报告",
        )
        events = _parse_sse(client.get(created["stream_url"]).text)
    finally:
        registry.tool_runtime.execute = original_execute

    event_names = [event["event"] for event in events]
    completed = next(event for event in events if event["event"] == "response.completed")
    recovery_payload = completed["payload"]["response_plan"]["structured_payload"]["recovery"]
    recovery = client.get(f"/api/chat/turns/{created['turn_id']}/recovery").json()["items"]
    trace = client.get(f"/api/traces/{created['trace_id']}").json()
    serialized = json.dumps(
        {"events": events, "recovery": recovery, "trace": trace},
        ensure_ascii=False,
    )

    assert "turn.recovery_started" in event_names
    assert "turn.recovery_diagnosed" in event_names
    assert "turn.recovery_action" in event_names
    assert "turn.recovery_completed" in event_names
    assert "turn.failed" not in event_names
    assert "task.completed" in event_names
    assert recovery_payload["status"] == "recovered"
    assert recovery_payload["attempt_count"] == 1
    assert recovery_payload["actions_taken"] == ["retry_failed_step"]
    assert recovery[0]["status"] == "recovered"
    assert any(span["span_type"] == TraceSpanType.TURN_RECOVERY.value for span in trace["spans"])
    assert "phase60-secret" not in serialized
    assert "C:\\Users\\Administrator" not in serialized


def test_phase60_chat_recovery_exhaustion_fails_after_budget(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    original_execute = registry.tool_runtime.execute

    async def failing_execute(request: Any, *, trace_id: str | None = None) -> Any:
        if request.tool_name == "knowledge.search":
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "persistent tool failure token=phase60-secret",
                status_code=500,
            )
        return await original_execute(request, trace_id=trace_id)

    registry.tool_runtime.execute = failing_execute
    try:
        conversation_id = _conversation_id(client)
        created = _create_turn(
            client,
            conversation_id,
            "phase60-recover-exhausted",
            "请调研 phase60 持续失败并输出任务报告",
        )
        events = _parse_sse(client.get(created["stream_url"]).text)
    finally:
        registry.tool_runtime.execute = original_execute

    event_names = [event["event"] for event in events]
    failed = next(event for event in events if event["event"] == "turn.failed")
    recovery_payload = failed["payload"]["response_plan"]["structured_payload"]["recovery"]
    recovery = client.get(f"/api/chat/turns/{created['turn_id']}/recovery").json()["items"]
    detail = client.get(f"/api/chat/turns/{created['turn_id']}").json()
    serialized = json.dumps({"events": events, "recovery": recovery}, ensure_ascii=False)

    assert event_names.count("turn.recovery_started") == MAX_ATTEMPTS_PER_ERROR
    assert event_names[-1] == "turn.failed"
    assert "task.failed" in event_names
    assert recovery_payload["status"] == "exhausted"
    assert recovery_payload["attempt_count"] == MAX_ATTEMPTS_PER_ERROR
    assert recovery_payload["root_cause"]
    assert len(recovery) == MAX_ATTEMPTS_PER_ERROR
    assert {item["status"] for item in recovery} == {"failed"}
    assert detail["status"] == "failed"
    assert detail["error_code"] == ErrorCode.TASK_STEP_FAILED.value
    assert "phase60-secret" not in serialized


def test_phase60_high_risk_or_approval_failure_is_not_auto_retried(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase60-approval",
        "请处理终端任务 phase60，需要命令执行",
    )
    events = _parse_sse(client.get(created["stream_url"]).text)
    event_names = [event["event"] for event in events]
    completed = next(event for event in events if event["event"] == "response.completed")
    recovery_payload = completed["payload"]["response_plan"]["structured_payload"]["recovery"]
    recovery = client.get(f"/api/chat/turns/{created['turn_id']}/recovery").json()["items"]

    assert "approval.required" in event_names
    assert "turn.recovery_action" not in event_names
    assert recovery_payload["status"] == "waiting_approval"
    assert recovery_payload["next_action"] == "request_approval"
    assert recovery == []


def test_phase60_permission_block_stops_without_retry(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    task = _task(status=TaskStatus.FAILED, failure_reason="permission denied by capability graph")
    service = TurnRecoveryService(
        chat_repo=registry.chat,
        task_engine=registry.task_engine,
        trace_service=registry.trace_service,
        composer=registry.chat_service._composer,
    )
    async def run_recovery() -> Any:
        return await service.recover_task_for_turn(
            turn={"turn_id": "turn_phase60_perm", "trace_id": "trc_phase60_perm"},
            task=task,
            root_span_id=None,
        )

    result = anyio.run(run_recovery)

    assert result.recovery_payload["status"] == "unrecoverable"
    assert result.recovery_payload["failure_type"] == "permission_denied"
    assert result.recovery_payload["next_action"] == "ask_user_for_missing_input"
    event_types = [event.event_type for event in result.events]
    assert ChatEventType.TURN_RECOVERY_DIAGNOSED in event_types
    assert ChatEventType.TURN_RECOVERY_ACTION not in event_types
    assert event_types[-1] == ChatEventType.TURN_RECOVERY_COMPLETED


def _conversation_id(client: TestClient) -> str:
    return client.get("/api/chat/conversations").json()["items"][0]["conversation_id"]


def _create_turn(
    client: TestClient,
    conversation_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/chat/turn",
        json={
            "session_id": session_id,
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": text},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _task(
    *,
    status: TaskStatus,
    failure_reason: str | None = "task failed",
    risk_level: str = "R1",
) -> Any:
    return SimpleNamespace(
        task_id="tsk_phase60",
        organization_id="org_default",
        title="Phase60 task",
        goal="phase60",
        mode=TaskMode.WORKFLOW,
        status=status,
        risk_level=risk_level,
        current_approval_id=None,
        failure_reason=failure_reason,
        result={},
        plan=SimpleNamespace(
            steps=[
                {
                    "step_key": "low_risk",
                    "risk_level": risk_level,
                }
            ]
        ),
    )
