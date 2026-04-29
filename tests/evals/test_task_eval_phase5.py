from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.eval


def test_eval_task_phase5_control_replay_and_safety(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        task = client.post(
            "/api/tasks",
            json={
                "goal": "执行终端命令",
                "constraints": {"command": "echo eval-phase5"},
                "auto_start": True,
            },
        ).json()
        approval_id = task["current_approval_id"]
        completed = client.post(
            f"/api/approvals/{approval_id}/approve",
            json={"reason": "eval approval"},
        ).json()
        replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
        blocked = client.post(
            "/api/tools/execute",
            json={
                "task_id": task["task_id"],
                "tool_name": "terminal.run",
                "args": {"command": "Remove-Item -Recurse secrets"},
                "approval_id": approval_id,
            },
        )
        audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert task["status"] == "waiting_approval"
    assert completed["status"] == "completed"
    assert replay["steps"]
    assert replay["tool_calls"]
    assert replay["approvals"][0]["status"] == "approved"
    assert any(artifact["checksum"].startswith("sha256:") for artifact in replay["artifacts"])
    assert blocked.status_code in {403, 409}
    assert "eval-phase5" in audit_text or "terminal.run" in audit_text
