from __future__ import annotations

import json
from typing import Any, cast

from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase39_manual_checkpoint_api_and_path_boundary(client: TestClient) -> None:
    task_id = _create_task(client)
    _write_file(client, task_id, "outputs/manual.txt", "before")

    created = client.post(
        f"/api/tasks/{task_id}/checkpoints",
        json={"paths": ["outputs/manual.txt"], "reason": "phase39 manual checkpoint"},
    )
    assert created.status_code == 200, created.text
    checkpoint = created.json()
    assert checkpoint["status"] == "ready"
    assert checkpoint["items"][0]["exists_before"] is True
    assert checkpoint["items"][0]["target_uri"].startswith(f"artifact://{task_id}/")

    escape = client.post(
        f"/api/tasks/{task_id}/checkpoints",
        json={"paths": ["../outside.txt"]},
    )
    assert escape.status_code == 403

    _overwrite_file(client, task_id, "outputs/manual.txt", "after")
    rolled_back = client.post(
        f"/api/checkpoints/{checkpoint['checkpoint_id']}/rollback",
        json={"requested_by": "user_local_owner", "reason": "phase39 restore"},
    )
    assert rolled_back.status_code == 200, rolled_back.text
    assert rolled_back.json()["event"]["status"] == "completed"
    assert _read_file(client, task_id, "outputs/manual.txt") == "before"

    history = client.get(f"/api/tasks/{task_id}/rollback-events").json()["items"]
    assert history and history[0]["checkpoint_id"] == checkpoint["checkpoint_id"]


def test_phase39_file_delete_and_move_auto_checkpoint_rollback(client: TestClient) -> None:
    task_id = _create_task(client)
    _write_file(client, task_id, "outputs/delete-me.txt", "keep me")

    delete_result = _delete_file(client, task_id, "outputs/delete-me.txt")
    delete_checkpoint_id = delete_result["checkpoint_id"]
    assert delete_result["rollback_available"] is True
    assert _read_file_status(client, task_id, "outputs/delete-me.txt") == 404

    restored = client.post(
        f"/api/checkpoints/{delete_checkpoint_id}/rollback",
        json={"requested_by": "user_local_owner", "reason": "phase39 delete restore"},
    )
    assert restored.status_code == 200, restored.text
    assert _read_file(client, task_id, "outputs/delete-me.txt") == "keep me"

    _write_file(client, task_id, "outputs/source.txt", "move me")
    move_result = _move_file(client, task_id, "outputs/source.txt", "outputs/dest.txt")
    move_checkpoint_id = move_result["checkpoint_id"]
    assert _read_file(client, task_id, "outputs/dest.txt") == "move me"
    assert _read_file_status(client, task_id, "outputs/source.txt") == 404

    moved_back = client.post(
        f"/api/checkpoints/{move_checkpoint_id}/rollback",
        json={"requested_by": "user_local_owner", "reason": "phase39 move restore"},
    )
    assert moved_back.status_code == 200, moved_back.text
    assert _read_file(client, task_id, "outputs/source.txt") == "move me"
    assert _read_file_status(client, task_id, "outputs/dest.txt") == 404


def test_phase39_overwrite_checkpoint_conflict_and_replay(client: TestClient) -> None:
    task_id = _create_task(client)
    _write_file(client, task_id, "outputs/conflict.txt", "v1")

    first_overwrite = _overwrite_file(client, task_id, "outputs/conflict.txt", "v2")
    checkpoint_id = first_overwrite["checkpoint_id"]
    assert checkpoint_id
    assert first_overwrite["rollback_available"] is True

    _overwrite_file(client, task_id, "outputs/conflict.txt", "v3")
    conflict = client.post(
        f"/api/checkpoints/{checkpoint_id}/rollback",
        json={"requested_by": "user_local_owner", "reason": "phase39 conflict"},
    )
    assert conflict.status_code == 200, conflict.text
    payload = conflict.json()
    assert payload["event"]["status"] == "completed_with_conflicts"
    assert payload["event"]["conflict_items"] == 1
    assert _read_file(client, task_id, "outputs/conflict.txt") == "v3"

    replay = client.get(f"/api/tasks/{task_id}/replay")
    assert replay.status_code == 200, replay.text
    replay_payload = replay.json()
    assert replay_payload["checkpoints"]
    assert replay_payload["rollback_events"]
    assert _payload_leakage_count(replay_payload) == 0


def test_phase39_approval_summary_declares_rollback_availability(
    client: TestClient,
) -> None:
    task_id = _create_task(client)
    _write_file(client, task_id, "outputs/approval.txt", "approval")

    approval_response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.delete",
            "args": {"path": "outputs/approval.txt"},
        },
    )
    assert approval_response.status_code == 200, approval_response.text
    approval = approval_response.json()["approval"]
    assert approval["status"] == "pending"
    assert "checkpoint" in approval["summary"].lower()
    rollback = approval["payload_redacted"]["rollback_availability"]
    assert rollback["rollback_available"] is True
    assert rollback["scope"] == "task_artifacts"


def test_phase39_release_contracts_suite_report_and_diagnostic(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase39")
    assert migration_contract["required_migration"] == "027_task_checkpoints.sql"

    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    contract_names = {item["name"] for item in contracts}
    assert {
        "TaskCheckpointService",
        "WorkspaceSnapshotPolicy",
        "FileMutationCheckpoint",
        "RollbackService",
        "CheckpointReplayEvidence",
        "RollbackApprovalEvidence",
    }.issubset(contract_names)

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase39_task_checkpoints"},
    )
    assert run.status_code == 200, run.text
    run_payload = run.json()
    assert run_payload["status"] == "passed"
    assert run_payload["total_cases"] == 10

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))

    phase39 = report["summary"]["phase39"]
    assert completed["status"] == "ready_for_release"
    assert phase39["suite_id"] == "suite_phase39_task_checkpoints"
    assert phase39["registered_cases"] == 10
    assert phase39["tables"]["task_checkpoints"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase39"]["registered"] is True
    assert "phase39" in diagnostic
    assert "phase39_task_checkpoints" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_task(client: TestClient) -> str:
    response = client.post(
        "/api/tasks",
        json={"goal": "phase39 checkpoint task", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["task_id"])


def _write_file(client: TestClient, task_id: str, path: str, content: str) -> dict[str, Any]:
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
    return dict(response.json()["result"])


def _overwrite_file(client: TestClient, task_id: str, path: str, content: str) -> dict[str, Any]:
    approval_id = _request_approval(
        client,
        task_id,
        "file.write",
        {"path": path, "content": content, "overwrite": True},
    )
    _approve(client, approval_id)
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.write",
            "approval_id": approval_id,
            "args": {"path": path, "content": content, "overwrite": True},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["result"])


def _delete_file(client: TestClient, task_id: str, path: str) -> dict[str, Any]:
    approval_id = _request_approval(client, task_id, "file.delete", {"path": path})
    _approve(client, approval_id)
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.delete",
            "approval_id": approval_id,
            "args": {"path": path},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["result"])


def _move_file(
    client: TestClient,
    task_id: str,
    source: str,
    destination: str,
) -> dict[str, Any]:
    approval_id = _request_approval(
        client,
        task_id,
        "file.move",
        {"path": source, "destination": destination},
    )
    _approve(client, approval_id)
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.move",
            "approval_id": approval_id,
            "args": {"path": source, "destination": destination},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["result"])


def _request_approval(
    client: TestClient,
    task_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> str:
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": tool_name,
            "args": args,
        },
    )
    assert response.status_code == 200, response.text
    approval = response.json()["approval"]
    assert approval["status"] == "pending"
    return str(approval["approval_id"])


def _approve(client: TestClient, approval_id: str) -> None:
    async def runner() -> None:
        await cast(Any, client.app).state.registry.approval_service.approve(
            approval_id,
            actor_type="user",
            actor_id="user_local_owner",
            reason="phase39 test approval",
            trace_id=None,
        )

    cast(Any, client).portal.call(runner)


def _read_file(client: TestClient, task_id: str, path: str) -> str:
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.read",
            "args": {"path": path},
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["result"]["content"])


def _read_file_status(client: TestClient, task_id: str, path: str) -> int:
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.read",
            "args": {"path": path},
        },
    )
    return response.status_code


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "sk-phase39-secret",
        "token=phase39",
        "cookie=phase39",
        "private_key=phase39",
        "mnemonic=phase39",
        "c:\\users\\administrator\\phase39",
    ]
    return sum(1 for item in forbidden if item in serialized)
