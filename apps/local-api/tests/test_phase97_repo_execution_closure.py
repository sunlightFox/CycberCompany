from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from app.services.chat_intent_router import ChatIntentRouter


def test_phase97_repo_patch_task_requires_and_records_verification(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "修复 repo 单文件 bug 并跑测试",
            "mode_hint": "agent",
            "auto_start": True,
            "constraints": {
                "repo_request_type": "repo_patch_request",
                "repo_patch_path": "src/app.py",
                "repo_patch_content": "fixed = True\n",
                "verify_read_path": "src/app.py",
                "verify_contains_text": "fixed = True",
            },
        },
    ).json()
    task = _drain_task_approvals(client, task)

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert task["status"] == "completed"
    assert task["result"]["deliverable"] is True
    assert task["result"]["files_changed"] == ["src/app.py"]
    assert task["result"]["verification_summary"]["ran"] is True
    assert task["result"]["verification_summary"]["passed"] is True
    assert task["result"]["not_run_checks"] == []
    assert replay["final_result"]["deliverable"] is True
    assert replay["workflow_evidence"]["repo_execution"]["enabled"] is True


def test_phase97_repo_fix_after_failure_repairs_once_and_resolves(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "修复 repo 失败测试并自动再修一次",
            "mode_hint": "agent",
            "auto_start": True,
            "constraints": {
                "repo_request_type": "repo_fix_after_failure",
                "repo_patch_path": "src/app.py",
                "repo_patch_content": "broken = True\n",
                "verify_read_path": "src/app.py",
                "verify_contains_text": "fixed = True",
                "repair_patch_path": "src/app.py",
                "repair_patch_content": "fixed = True\n",
            },
        },
    ).json()
    task = _drain_task_approvals(client, task)

    assert task["status"] == "completed"
    assert task["result"]["deliverable"] is True
    assert task["result"]["repair_attempted"] is True
    assert task["result"]["repair_outcome"] == "resolved"
    assert task["result"]["verification_summary"]["passed"] is True


def test_phase97_repo_fix_after_failure_stops_after_one_failed_repair(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "修复 repo 失败测试但局部修复依旧失败",
            "mode_hint": "agent",
            "auto_start": True,
            "constraints": {
                "repo_request_type": "repo_fix_after_failure",
                "repo_patch_path": "src/app.py",
                "repo_patch_content": "broken = True\n",
                "verify_read_path": "src/app.py",
                "verify_contains_text": "fixed = True",
                "repair_patch_path": "src/app.py",
                "repair_patch_content": "still_broken = True\n",
            },
        },
    ).json()
    task = _drain_task_approvals(client, task)

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert task["status"] == "failed"
    assert task["result"]["deliverable"] is False
    assert task["result"]["repair_attempted"] is True
    assert task["result"]["repair_outcome"] == "failed"
    assert task["result"]["residual_risk"]
    assert replay["final_result"]["verification_summary"]["status"] == "failed"


def test_phase97_dirty_workspace_is_tracked_without_polluting_changed_files(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "修改 repo 文件并兼容已有脏工作区",
            "mode_hint": "agent",
            "auto_start": False,
            "constraints": {
                "repo_request_type": "repo_patch_request",
                "repo_patch_path": "src/app.py",
                "repo_patch_content": "fixed = True\n",
                "verify_read_path": "src/app.py",
                "verify_contains_text": "fixed = True",
            },
        },
    ).json()

    registry = cast(Any, client.app).state.registry
    workspace = registry.artifact_store.task_dir(task["task_id"])
    (workspace / "notes").mkdir(parents=True, exist_ok=True)
    (workspace / "notes" / "preexisting.txt").write_text("user draft\n", encoding="utf-8")

    started = client.post(f"/api/tasks/{task['task_id']}/start").json()
    started = _drain_task_approvals(client, started)

    assert started["status"] == "completed"
    assert started["result"]["workspace_dirty_preexisting"] == ["notes/preexisting.txt"]
    assert started["result"]["files_changed"] == ["src/app.py"]


def test_phase97_repo_test_request_can_be_deliverable_without_patch(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "在 repo 里跑 targeted pytest",
            "mode_hint": "agent",
            "auto_start": True,
            "constraints": {
                "repo_request_type": "repo_test_request",
                "verify_command": "python -c \"print('ok')\"",
            },
        },
    ).json()
    task = _drain_task_approvals(client, task)

    assert task["status"] == "completed"
    assert task["result"]["files_changed"] == []
    assert task["result"]["verification_summary"]["ran"] is True
    assert task["result"]["verification_summary"]["passed"] is True
    assert task["result"]["deliverable"] is True


def test_phase97_repo_host_mutation_command_is_not_counted_as_deliverable(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "在 repo 任务里执行高风险主机变更命令",
            "mode_hint": "agent",
            "auto_start": True,
            "constraints": {
                "repo_request_type": "repo_test_request",
                "verify_command": "Remove-Item -Recurse C:\\Windows\\Temp",
            },
        },
    ).json()
    task = _drain_task_approvals(client, task)

    assert task["status"] == "failed"
    assert task["result"]["deliverable"] is False
    assert task["result"]["verification_summary"]["status"] in {"failed", "not_run"}
    assert task["result"]["residual_risk"]


def test_phase97_chat_router_exposes_stable_repo_route_types() -> None:
    router = ChatIntentRouter()

    assert router.decide("帮我读一下这个代码仓").route_type == "repo_readonly_request"
    assert router.decide("帮我改代码并跑 pytest").route_type == "repo_test_request"
    assert router.decide("帮我做一个 repo refactor").route_type == "repo_refactor_request"


def _drain_task_approvals(client: TestClient, task: dict[str, Any]) -> dict[str, Any]:
    current = task
    while current["status"] == "waiting_approval":
        approval_id = current.get("current_approval_id")
        assert approval_id
        current = client.post(f"/api/approvals/{approval_id}/approve", json={}).json()
    return current
