from __future__ import annotations

import json
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase46_worker_health_and_manual_tick_api(client: TestClient) -> None:
    health = client.get("/api/system/background-workers/health")
    assert health.status_code == 200, health.text
    payload = health.json()

    assert payload["component"] == "BackgroundWorkerService"
    assert payload["enabled"] is False
    assert payload["running"] is False
    assert payload["timeout_seconds"] >= 1
    assert payload["loop_status"] == "disabled"
    assert payload["degraded"] is False
    assert set(payload["workers"]) == {
        "scheduled_due_worker",
        "notification_retry_worker",
        "checkpoint_cleanup_worker",
        "stale_recovery_worker",
    }

    tick = client.post("/api/system/background-workers/tick")
    assert tick.status_code == 200, tick.text
    tick_payload = tick.json()
    assert tick_payload["status"] == "completed"
    assert tick_payload["worker_count"] == 4
    assert all(item["status"] == "healthy" for item in tick_payload["results"].values())
    assert _payload_leakage_count(tick_payload) == 0


def test_phase46_worker_failure_is_isolated_and_redacted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = cast(Any, client.app).state.registry

    async def fail_scan_due(*args: object, **kwargs: object) -> list[Any]:
        raise RuntimeError("token=phase46-secret should be redacted")

    monkeypatch.setattr(registry.scheduled_task_service, "scan_due", fail_scan_due)
    tick = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "scheduled_due_worker"},
    )
    health = client.get("/api/system/background-workers/health").json()
    worker = health["workers"]["scheduled_due_worker"]

    assert tick.status_code == 200, tick.text
    assert tick.json()["status"] == "completed"
    assert tick.json()["results"]["scheduled_due_worker"]["status"] == "failed"
    assert tick.json()["results"]["scheduled_due_worker"]["error_code"] == "RuntimeError"
    assert health["degraded"] is True
    assert worker["last_status"] == "failed"
    assert worker["consecutive_failure_count"] == 1
    assert worker["last_duration_ms"] >= 0
    assert _payload_leakage_count({"tick": tick.json(), "health": health}) == 0


def test_phase46_scheduled_due_worker_manual_tick_is_idempotent(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    scheduled = _create_scheduled_task(
        client,
        goal="每隔 10 分钟 帮我整理 Phase46 worker 摘要",
        schedule={"type": "interval", "every_seconds": 600},
    )
    scheduled_task_id = scheduled["scheduled_task_id"]
    due_at = "2026-05-01T00:00:00+00:00"

    _run_async(
        client,
        registry.scheduled_tasks.update_task,
        scheduled_task_id,
        {"next_run_at": due_at, "updated_at": due_at},
    )
    first = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "scheduled_due_worker"},
    ).json()
    _run_async(
        client,
        registry.scheduled_tasks.update_task,
        scheduled_task_id,
        {"next_run_at": due_at, "updated_at": due_at},
    )
    second = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "scheduled_due_worker"},
    ).json()
    runs = client.get(f"/api/scheduled-tasks/{scheduled_task_id}/runs").json()["items"]

    assert first["results"]["scheduled_due_worker"]["due_runs"] == 1
    assert second["results"]["scheduled_due_worker"]["due_runs"] == 1
    assert len({item["run_id"] for item in runs}) == 1
    assert runs[0]["trigger_type"] == "due"
    assert runs[0]["task_id"]


def test_phase46_notification_retry_worker_uses_bounded_backoff(client: TestClient) -> None:
    channel = _create_channel(client, provider="webhook")
    queued = client.post(
        "/api/notification/messages",
        json={
            "channel_id": channel["channel_id"],
            "message_type": "system_degraded",
            "recipient": "user_local_owner",
            "subject": "Phase46 retry",
            "body": "worker retry should degrade without leaking token=phase46-secret",
            "send_immediately": False,
        },
    )
    assert queued.status_code == 200, queued.text
    queued_payload = queued.json()
    assert queued_payload["status"] == "blocked"

    safe_message = client.post(
        "/api/notification/messages",
        json={
            "channel_id": channel["channel_id"],
            "message_type": "system_degraded",
            "recipient": "user_local_owner",
            "subject": "Phase46 retry",
            "body": "worker retry should degrade through the disabled webhook provider",
            "send_immediately": False,
        },
    )
    assert safe_message.status_code == 200, safe_message.text
    notification_id = safe_message.json()["notification_id"]

    tick = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "notification_retry_worker"},
    )
    assert tick.status_code == 200, tick.text
    retried = client.get("/api/notification/messages", params={"status": "failed"}).json()[
        "items"
    ]
    message = next(item for item in retried if item["notification_id"] == notification_id)
    attempts = client.get(
        f"/api/notification/messages/{notification_id}/attempts"
    ).json()["items"]

    assert tick.json()["results"]["notification_retry_worker"]["processed_messages"] >= 1
    assert message["retry_count"] == 1
    assert message["next_retry_at"]
    assert attempts[0]["error_code"] == "provider_disabled"
    assert (
        _payload_leakage_count({"tick": tick.json(), "message": message, "attempts": attempts})
        == 0
    )

    inactive_channel = _create_channel(client, provider="local_mock")
    patched = client.patch(
        f"/api/notification/channels/{inactive_channel['channel_id']}",
        json={"status": "paused"},
    )
    assert patched.status_code == 200, patched.text
    queued_inactive = client.post(
        "/api/notification/messages",
        json={
            "channel_id": inactive_channel["channel_id"],
            "message_type": "system_degraded",
            "recipient": "user_local_owner",
            "subject": "Phase46 inactive retry",
            "body": "inactive channel should increment retry metadata",
            "send_immediately": False,
        },
    )
    assert queued_inactive.status_code == 200, queued_inactive.text
    inactive_id = queued_inactive.json()["notification_id"]
    inactive_tick = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "notification_retry_worker"},
    )
    assert inactive_tick.status_code == 200, inactive_tick.text
    inactive_message = next(
        item
        for item in client.get("/api/notification/messages", params={"status": "failed"}).json()[
            "items"
        ]
        if item["notification_id"] == inactive_id
    )
    assert inactive_message["retry_count"] == 1
    assert inactive_message["next_retry_at"]
    assert inactive_message["failure_reason"] == "channel_paused"


def test_phase46_checkpoint_cleanup_and_stale_recovery_workers(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    task_id = _create_task(client)
    _write_file(client, task_id, "outputs/phase46.txt", "checkpoint worker")
    checkpoint = client.post(
        f"/api/tasks/{task_id}/checkpoints",
        json={"paths": ["outputs/phase46.txt"], "reason": "phase46 cleanup"},
    ).json()
    checkpoint_id = checkpoint["checkpoint_id"]

    _run_async(
        client,
        registry.checkpoints.update_checkpoint,
        checkpoint_id,
        {
            "status": "ready",
            "expires_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
    cleanup = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "checkpoint_cleanup_worker"},
    ).json()
    expired = _run_async(client, registry.checkpoints.get_checkpoint, checkpoint_id)

    scheduled = _create_scheduled_task(client, goal="每天执行 stale run 恢复测试")
    stale_run_id = "schrun_phase46_stale"
    _run_async(
        client,
        registry.scheduled_tasks.insert_run,
        {
            "run_id": stale_run_id,
            "scheduled_task_id": scheduled["scheduled_task_id"],
            "organization_id": "org_default",
            "trace_id": None,
            "trigger_type": "due",
            "idempotency_key": "phase46-stale-run",
            "scheduled_for": "2026-01-01T00:00:00+00:00",
            "started_at": "2026-01-01T00:00:00+00:00",
            "status": "running",
            "policy_decision": {},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
    stale = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "stale_recovery_worker"},
    ).json()
    recovered = client.get(f"/api/scheduled-runs/{stale_run_id}").json()

    assert cleanup["results"]["checkpoint_cleanup_worker"]["expired_checkpoints"] >= 1
    assert expired["status"] == "expired"
    assert recovered["status"] == "failed"
    assert recovered["failure_reason"] == "worker_recovered_stale_scheduled_run"
    assert stale["results"]["stale_recovery_worker"]["scheduled_runs_recovered"] >= 1


def test_phase46_suite_contracts_release_summary_and_diagnostic(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase46")
    assert migration_contract["required_migration"] == "031_media_runtime.sql"

    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    assert "suite_phase46_background_workers" in {item["suite_id"] for item in suites}
    for module in [
        "WorkerSupervisor",
        "BackgroundWorkerService",
        "ScheduledDueWorker",
        "NotificationRetryWorker",
        "CheckpointCleanupWorker",
        "StaleRecoveryWorker",
        "WorkerHealthDiagnostics",
    ]:
        assert by_name[module]["status"] == "implemented"

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase46_background_workers"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 9

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    phase46 = report["summary"]["phase46"]

    assert completed["status"] == "ready_for_release"
    assert phase46["suite_id"] == "suite_phase46_background_workers"
    assert phase46["registered_cases"] == 9
    assert phase46["worker_health_contract"]["deterministic_manual_tick"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase46"]["registered"] is True
    assert any(item["source_type"] == "phase46_background_workers" for item in evidence)
    assert "phase46" in diagnostic
    assert "phase46_background_workers" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_scheduled_task(
    client: TestClient,
    *,
    goal: str,
    schedule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/scheduled-tasks",
        json={
            "owner_member_id": "mem_xiaoyao",
            "goal": goal,
            "schedule": schedule or {"type": "daily", "time": "09:00"},
            "execution_policy": {"attendance": "unattended"},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_channel(client: TestClient, *, provider: str) -> dict[str, Any]:
    response = client.post(
        "/api/notification/channels",
        json={
            "provider": provider,
            "display_name": f"phase46 {provider}",
            "channel_type": provider,
            "sensitivity": "medium",
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_task(client: TestClient) -> str:
    response = client.post(
        "/api/tasks",
        json={"goal": "phase46 checkpoint cleanup task", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["task_id"])


def _write_file(client: TestClient, task_id: str, path: str, content: str) -> None:
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.write",
            "args": {"path": path, "content": content},
        },
    )
    assert response.status_code == 200, response.text


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    async def runner() -> Any:
        return await func(*args, **kwargs)

    return cast(Any, client).portal.call(runner)


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "token=phase46-secret",
        "sk-phase46",
        "secret-phase46",
        "cookie=phase46",
        "private_key=phase46",
        "mnemonic=phase46",
        "c:\\users\\administrator\\phase46",
    ]
    return sum(1 for item in forbidden if item in serialized)
