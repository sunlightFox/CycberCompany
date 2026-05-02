from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase52_workspace_path_boundary_and_deployment_lifecycle(client: TestClient) -> None:
    rejected = client.post(
        "/api/project-workspaces",
        json={"source_uri": "file:///C:/Users/Administrator/Desktop/app"},
    )
    assert rejected.status_code == 403

    created = client.post(
        "/api/project-deployments",
        json={
            "source_uri": "fixture://node-static",
            "constraints": {"preferred_port": 5188},
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "waiting_approval"
    assert body["workspace"]["root_uri"].startswith("workspace://projects/")
    approval_id = body["plan"]["approval_strategy"]["approval_id"]
    assert approval_id
    assert body["plan"]["backend_selection"]["selected_backend"] in {
        "container",
        "wsl",
        "local_workspace",
    }
    if body["plan"]["backend_type"] == "local_workspace":
        assert body["plan"]["degraded_isolation"] is True

    run_without_approval = client.post(
        f"/api/project-deployments/{body['deployment_id']}/run",
        json={},
    )
    assert run_without_approval.status_code == 200
    assert run_without_approval.json()["status"] == "waiting_approval"

    fake_approval = client.post(
        f"/api/project-deployments/{body['deployment_id']}/run",
        json={"approval_id": "manual-confirmation"},
    )
    assert fake_approval.status_code == 409

    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"reason": "phase52 deployment dry-run"},
    )
    assert approved.status_code == 200, approved.text

    run = client.post(
        f"/api/project-deployments/{body['deployment_id']}/run",
        json={"approval_id": approval_id},
    )
    assert run.status_code == 200, run.text
    deployed = run.json()
    assert deployed["status"] == "healthy"
    assert deployed["endpoint"]["url"] == "http://127.0.0.1:5188"
    assert deployed["managed_process"]["status"] == "running"
    assert deployed["port_lease"]["status"] == "active"

    logs = client.get(f"/api/project-deployments/{body['deployment_id']}/logs")
    assert logs.status_code == 200, logs.text
    assert logs.json()["status"] == "completed"
    assert "health_check=passed" in logs.json()["content_preview"]

    stopped = client.post(f"/api/project-deployments/{body['deployment_id']}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"
    assert stopped.json()["port_lease"]["status"] == "released"


def test_phase52_toolchain_and_project_tools_are_bound_to_task(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "Phase52 project tool task", "auto_start": False},
    ).json()
    no_task = client.post(
        "/api/tools/execute",
        json={"tool_name": "runtime.ensure", "args": {"runtime_name": "node"}},
    )
    assert no_task.status_code in {403, 409, 422}

    ensured = client.post(
        "/api/toolchains/ensure",
        json={"runtime_name": "node", "version": "lts", "task_id": task["task_id"]},
    )
    assert ensured.status_code == 200, ensured.text
    assert ensured.json()["install_mode"] == "portable"
    assert ensured.json()["policy_snapshot"]["modifies_global_path"] is False

    tool = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "project.run",
            "args": {"preferred_port": 5199},
        },
    )
    assert tool.status_code == 200, tool.text
    assert tool.json()["approval"]["status"] == "pending"
    assert tool.json()["tool_call"]["status"] == "approval_required"
