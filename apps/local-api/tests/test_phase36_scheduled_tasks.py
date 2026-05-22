from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from app.services.scheduled_tasks import ScheduleParser
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase36_schedule_parser_daily_interval_once_weekly() -> None:
    parser = ScheduleParser()
    now = datetime(2026, 4, 30, 1, 0, tzinfo=UTC)

    daily = parser.normalize({"type": "daily", "time": "09:30"}, now=now)
    interval = parser.normalize({"type": "interval", "every_seconds": 600}, now=now)
    once = parser.normalize({"type": "once", "run_at": "2026-04-30T09:00:00+00:00"}, now=now)
    weekly = parser.normalize(
        {"type": "weekly", "days": ["周五"], "time": "08:00"},
        now=now,
    )

    assert daily.schedule["timezone"] == "Asia/Shanghai"
    assert daily.next_run_at is not None
    assert interval.next_run_at == now + timedelta(seconds=600)
    assert once.next_run_at == datetime(2026, 4, 30, 9, 0, tzinfo=UTC)
    assert weekly.next_run_at is not None
    assert weekly.next_run_at > now


def test_phase36_crud_lifecycle_and_api(client: TestClient) -> None:
    created = _create_scheduled_task(client, goal="每天 09:00 帮我整理知识摘要")
    scheduled_task_id = created["scheduled_task_id"]

    listed = client.get("/api/scheduled-tasks").json()["items"]
    detail = client.get(f"/api/scheduled-tasks/{scheduled_task_id}").json()
    updated = client.patch(
        f"/api/scheduled-tasks/{scheduled_task_id}",
        json={"title": "每日摘要", "schedule": {"type": "daily", "time": "10:15"}},
    ).json()
    paused = client.post(
        f"/api/scheduled-tasks/{scheduled_task_id}/pause",
        json={"reason": "phase36 pause"},
    ).json()
    resumed = client.post(f"/api/scheduled-tasks/{scheduled_task_id}/resume").json()
    cancelled = client.post(
        f"/api/scheduled-tasks/{scheduled_task_id}/cancel",
        json={"reason": "phase36 cancel"},
    ).json()
    archived = client.post(
        f"/api/scheduled-tasks/{scheduled_task_id}/archive",
        json={"reason": "phase36 archive"},
    ).json()

    assert created["status"] == "active"
    assert any(item["scheduled_task_id"] == scheduled_task_id for item in listed)
    assert detail["schedule"]["type"] == "daily"
    assert updated["title"] == "每日摘要"
    assert paused["status"] == "paused"
    assert resumed["status"] == "active"
    assert cancelled["status"] == "cancelled"
    assert archived["status"] == "archived"


def test_phase36_manual_trigger_creates_run_task_and_replay_ref(client: TestClient) -> None:
    scheduled = _create_scheduled_task(client, goal="每天 09:00 帮我整理知识摘要")
    scheduled_task_id = scheduled["scheduled_task_id"]

    run = client.post(
        f"/api/scheduled-tasks/{scheduled_task_id}/trigger",
        json={"scheduled_for": "2026-04-30T00:00:00+00:00"},
    ).json()
    run_detail = client.get(f"/api/scheduled-runs/{run['run_id']}").json()
    runs = client.get(f"/api/scheduled-tasks/{scheduled_task_id}/runs").json()["items"]
    task = client.get(f"/api/tasks/{run['task_id']}").json()

    assert run["trigger_type"] == "manual"
    assert run["task_id"]
    assert run["task_replay_ref"]["href"].endswith(f"/api/tasks/{run['task_id']}/replay")
    assert run_detail["run_id"] == run["run_id"]
    assert any(item["run_id"] == run["run_id"] for item in runs)
    assert task["preflight"]["phase36"]["scheduled_run_id"] == run["run_id"]


def test_phase36_due_scanner_is_idempotent_and_pause_blocks(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    scheduled = _create_scheduled_task(
        client,
        goal="每隔 10 分钟 帮我整理知识摘要",
        schedule={"type": "interval", "every_seconds": 600},
    )
    scheduled_task_id = scheduled["scheduled_task_id"]
    due_at = "2026-04-30T00:00:00+00:00"

    _run_async(
        client,
        registry.scheduled_tasks.update_task,
        scheduled_task_id,
        {"next_run_at": due_at, "updated_at": due_at},
    )
    first_runs = _run_async(
        client,
        registry.scheduled_task_service.scan_due,
        now=datetime(2026, 4, 30, 0, 1, tzinfo=UTC),
    )
    _run_async(
        client,
        registry.scheduled_tasks.update_task,
        scheduled_task_id,
        {"next_run_at": due_at, "updated_at": due_at},
    )
    second_runs = _run_async(
        client,
        registry.scheduled_task_service.scan_due,
        now=datetime(2026, 4, 30, 0, 1, tzinfo=UTC),
    )
    client.post(f"/api/scheduled-tasks/{scheduled_task_id}/pause", json={})
    _run_async(
        client,
        registry.scheduled_tasks.update_task,
        scheduled_task_id,
        {"next_run_at": "2026-04-30T00:02:00+00:00", "updated_at": due_at},
    )
    paused_runs = _run_async(
        client,
        registry.scheduled_task_service.scan_due,
        now=datetime(2026, 4, 30, 0, 3, tzinfo=UTC),
    )
    persisted_runs = client.get(f"/api/scheduled-tasks/{scheduled_task_id}/runs").json()["items"]

    assert len(first_runs) == 1
    assert len(second_runs) == 1
    assert first_runs[0].run_id == second_runs[0].run_id
    assert paused_runs == []
    assert len({item["run_id"] for item in persisted_runs}) == 1


def test_phase36_unattended_high_risk_pauses_before_execution(client: TestClient) -> None:
    scheduled = _create_scheduled_task(client, goal="每天 09:00 帮我删除 outputs/target.txt")
    run = client.post(
        f"/api/scheduled-tasks/{scheduled['scheduled_task_id']}/trigger",
        json={"scheduled_for": "2026-04-30T00:00:00+00:00"},
    ).json()
    task = client.get(f"/api/tasks/{run['task_id']}").json()

    assert run["status"] == "waiting_policy"
    assert run["policy_decision"]["auto_start"] is False
    assert "unattended_high_risk_requires_fresh_approval" in run["policy_decision"]["reason_codes"]
    assert task["status"] in {"planned", "waiting_approval", "paused"}
    assert task["preflight"]["phase36"]["background_execution"]["session_approval_reuse"] is False


def test_phase36_unattended_payment_wording_pauses_before_execution(client: TestClient) -> None:
    scheduled = _create_scheduled_task(client, goal="明天下午 3 点提醒我给供应商付款 5000 元")
    run = client.post(
        f"/api/scheduled-tasks/{scheduled['scheduled_task_id']}/trigger",
        json={"scheduled_for": "2026-04-30T00:00:00+00:00"},
    ).json()

    assert run["status"] == "waiting_policy"
    assert run["policy_decision"]["risk_level"] == "R4"
    assert run["policy_decision"]["auto_start"] is False


def test_phase36_chat_scheduled_task_visible_reply_has_no_internal_leaks(client: TestClient) -> None:
    conversation_id = client.get("/api/chat/conversations").json()["items"][0]["conversation_id"]
    created = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": "phase36-scheduled-visible",
            "input": {
                "type": "text",
                "text": "明天下午 3 点提醒我给供应商付款 5000 元。",
            },
        },
    )
    assert created.status_code == 200, created.text

    stream = client.get(created.json()["stream_url"])
    detail = client.get(f"/api/chat/turns/{created.json()['turn_id']}").json()
    reply = _reply_from_sse(stream.text)
    tasks = client.get("/api/scheduled-tasks").json()["items"]

    assert detail["intent"] == "scheduled_task_request"
    assert any("供应商付款 5000 元" in str(item.get("goal") or "") for item in tasks)
    assert reply == "好，明天下午 3 点提醒你给供应商付款 5000 元。到点我会先提醒你确认，这个我只提醒，不会自动付款。"
    for forbidden in [
        "调度方式",
        "下一次执行时间",
        "后台流程",
        "高风险动作",
        "格式约束作答",
        "trace_id",
        "task_id",
        "Asia/Shanghai",
    ]:
        assert forbidden not in reply
    assert re.search(r"\d{4}-\d{2}-\d{2}T", reply) is None


def test_phase36_chat_relative_seconds_reminder_creates_near_once_task(client: TestClient) -> None:
    conversation_id = client.get("/api/chat/conversations").json()["items"][0]["conversation_id"]
    before_ids = {
        item["scheduled_task_id"]
        for item in client.get("/api/scheduled-tasks", params={"limit": 200}).json()["items"]
    }
    created = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": "phase36-relative-seconds-visible",
            "input": {"type": "text", "text": "再过 20 秒提醒我喝水。"},
        },
    )
    assert created.status_code == 200, created.text

    stream = client.get(created.json()["stream_url"])
    reply = _reply_from_sse(stream.text)
    tasks = client.get("/api/scheduled-tasks", params={"limit": 200}).json()["items"]
    scheduled = next(item for item in tasks if item["scheduled_task_id"] not in before_ids)

    assert scheduled["schedule"]["type"] == "once"
    assert scheduled["schedule"]["timezone"] == "Asia/Shanghai"
    assert "秒后提醒你喝水" in reply
    assert "明天" not in reply
    assert "next_run_at" not in reply


def test_phase36_upload_id_masking_reminder_is_normal_reminder(client: TestClient) -> None:
    conversation_id = client.get("/api/chat/conversations").json()["items"][0]["conversation_id"]
    before_ids = {
        item["scheduled_task_id"]
        for item in client.get("/api/scheduled-tasks", params={"limit": 200}).json()["items"]
    }
    created = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": "phase36-id-mask-normal-reminder",
            "input": {
                "type": "text",
                "text": "明天下午 3:15 提醒我上传证件照片前先打码。",
            },
        },
    )
    assert created.status_code == 200, created.text

    stream = client.get(created.json()["stream_url"])
    reply = _reply_from_sse(stream.text)
    tasks = client.get("/api/scheduled-tasks", params={"limit": 200}).json()["items"]
    scheduled = next(item for item in tasks if item["scheduled_task_id"] not in before_ids)
    run = client.post(
        f"/api/scheduled-tasks/{scheduled['scheduled_task_id']}/trigger",
        json={"scheduled_for": "2026-05-22T00:00:00+00:00"},
    ).json()

    assert scheduled["schedule"]["type"] == "once"
    assert "T15:15" in scheduled["schedule"]["run_at"]
    assert reply == "好，明天下午 3:15 提醒你上传证件照片前先打码。到点我会直接叫你。"
    assert "高风险" not in reply
    assert "不会直接" not in reply
    assert run["status"] == "completed"
    assert run["policy_decision"]["risk_level"] == "R2"
    assert run["policy_decision"]["auto_start"] is True


def test_phase36_due_notification_uses_natural_reminder_copy(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    scheduled = _create_scheduled_task(
        client,
        goal="再过 20 秒提醒我喝水",
        schedule={"type": "once", "run_at": "2026-05-22T00:00:00+00:00"},
    )
    _run_async(
        client,
        registry.scheduled_tasks.update_task,
        scheduled["scheduled_task_id"],
        {
            "next_run_at": "2026-05-22T00:00:00+00:00",
            "updated_at": "2026-05-22T00:00:00+00:00",
        },
    )

    runs = _run_async(
        client,
        registry.scheduled_task_service.scan_due,
        now=datetime(2026, 5, 22, 0, 0, 30, tzinfo=UTC),
    )
    tick = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "notification_retry_worker"},
    )
    messages = client.get("/api/notification/messages", params={"limit": 50}).json()["items"]
    related = [
        item
        for item in messages
        if item.get("scheduled_task_id") == scheduled["scheduled_task_id"]
    ]

    assert len(runs) == 1
    assert runs[0].status == "completed"
    assert tick.status_code == 200, tick.text
    assert related
    assert related[0]["status"] == "sent"
    assert related[0]["body_redacted"] == "到点了，提醒你喝水。"
    assert "定时任务状态" not in related[0]["body_redacted"]
    assert "completed" not in related[0]["body_redacted"]


def test_phase36_consecutive_failures_dead_letter(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = cast(Any, client.app).state.registry
    scheduled = _create_scheduled_task(
        client,
        goal="每天 09:00 帮我整理失败测试",
        execution_policy={"attendance": "unattended"},
    )
    scheduled_task_id = scheduled["scheduled_task_id"]
    client.patch(
        f"/api/scheduled-tasks/{scheduled_task_id}",
        json={"max_consecutive_failures": 2},
    )

    async def fail_create_task(*args: object, **kwargs: object) -> object:
        raise RuntimeError("phase36 simulated task creation failure")

    monkeypatch.setattr(registry.task_engine, "create_task", fail_create_task)
    for scheduled_for in [
        datetime(2026, 4, 30, tzinfo=UTC),
        datetime(2026, 5, 1, tzinfo=UTC),
    ]:
        with pytest.raises(RuntimeError, match="phase36 simulated"):
            _run_async(
                client,
                registry.scheduled_task_service.trigger,
                scheduled_task_id,
                scheduled_for=scheduled_for,
            )
    detail = client.get(f"/api/scheduled-tasks/{scheduled_task_id}").json()
    runs = client.get(f"/api/scheduled-tasks/{scheduled_task_id}/runs").json()["items"]

    assert detail["status"] == "dead_letter"
    assert detail["consecutive_failure_count"] == 2
    assert len(runs) == 2
    assert {item["status"] for item in runs} == {"failed"}


def test_phase36_suite_contracts_release_summary_and_diagnostic(client: TestClient) -> None:
    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_module = {item["name"]: item for item in contracts}
    registry = cast(Any, client.app).state.registry

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    phase36 = report["summary"]["phase36"]

    migration_contract = assert_phase_migration_contract(client, "phase36")
    assert migration_contract["required_migration"] == "024_scheduled_tasks.sql"
    assert "suite_phase36_scheduled_background_tasks" in {item["suite_id"] for item in suites}
    for module in [
        "ScheduledTaskService",
        "ScheduleParser",
        "ScheduledDueScanner",
        "BackgroundExecutionPolicy",
        "ScheduledTaskRunHistory",
    ]:
        assert by_module[module]["status"] == "implemented"
    assert completed["status"] == "ready_for_release"
    assert phase36["suite_id"] == "suite_phase36_scheduled_background_tasks"
    assert phase36["registered_cases"] == 9
    assert phase36["tables"]["scheduled_tasks"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase36"]["registered"] is True
    assert any(item["source_type"] == "phase36_scheduled_background_tasks" for item in evidence)
    assert "phase36" in diagnostic
    assert "phase36_scheduled_background_tasks" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_scheduled_task(
    client: TestClient,
    *,
    goal: str,
    schedule: dict[str, Any] | None = None,
    execution_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/scheduled-tasks",
        json={
            "owner_member_id": "mem_xiaoyao",
            "goal": goal,
            "schedule": schedule or {"type": "daily", "time": "09:00"},
            "execution_policy": execution_policy or {"attendance": "unattended"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    async def runner() -> Any:
        return await func(*args, **kwargs)

    return cast(Any, client).portal.call(runner)


def _reply_from_sse(raw: str) -> str:
    chunks: list[str] = []
    fallback = ""
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("event") == "response.delta":
                chunks.append(str(event.get("payload", {}).get("text") or ""))
            if event.get("event") == "response.completed":
                response_plan = event.get("payload", {}).get("response_plan", {})
                fallback = str(response_plan.get("plain_text") or response_plan.get("summary") or "")
    return "".join(chunks).strip() or fallback.strip()


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "sk-phase36",
        "secret-phase36",
        "token=phase36",
        "cookie=phase36",
        "private_key=phase36",
        "mnemonic=phase36",
        "c:\\users\\administrator\\phase36",
    ]
    return sum(1 for item in forbidden if item in serialized)
