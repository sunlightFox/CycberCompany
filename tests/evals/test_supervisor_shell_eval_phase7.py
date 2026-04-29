from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.eval


def test_eval_phase7_supervisor_and_shell_invariants(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        simple = client.post(
            "/api/tasks",
            json={"goal": "写一份简短报告", "auto_start": True},
        ).json()
        complex_task = client.post(
            "/api/tasks",
            json={"goal": "请产品和技术共同评审一个复杂方案", "auto_start": True},
        ).json()
        replay = client.get(
            f"/api/tasks/{complex_task['task_id']}/collaboration-replay"
        ).json()
        before = {
            item["member_id"]: item["display_name"]
            for item in client.get("/api/members").json()["items"]
        }
        client.post("/api/shells/switch/preview", json={"shell_id": "company"})
        client.post("/api/shells/switch", json={"shell_id": "company"})
        after = {
            item["member_id"]: item["display_name"]
            for item in client.get("/api/members").json()["items"]
        }
        replay_text = json.dumps(replay, ensure_ascii=False)

    metrics = {
        "simple_not_supervisor": 1.0 if simple["mode"] != "supervisor" else 0.0,
        "complex_supervisor": 1.0 if complex_task["mode"] == "supervisor" else 0.0,
        "participant_selection": 1.0 if len(replay["participants"]) >= 2 else 0.0,
        "context_boundary": 1.0
        if all(
            "other_members_private_memory" in item["context_scope"]["excluded_context"]
            for item in replay["participants"]
        )
        else 0.0,
        "host_source_refs": 1.0 if replay["host_decisions"][0]["source_refs"] else 0.0,
        "shell_invariant": 1.0 if before == after else 0.0,
        "secret_isolation": 1.0 if "plain-secret" not in replay_text else 0.0,
    }

    assert all(value == 1.0 for value in metrics.values())
