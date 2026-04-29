from __future__ import annotations

from pathlib import Path

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.eval, pytest.mark.release, pytest.mark.slow]


def test_eval_phase8_release_gate_security_backup_and_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        gate = client.post("/api/release-gates", json={}).json()
        run = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
        evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()
        report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
        diagnostic = client.post("/api/diagnostics/bundles", json={}).json()

    metrics = {
        "gate_not_blocked": 1.0 if run["blocker_count"] == 0 else 0.0,
        "evidence_complete": 1.0 if len(evidence["items"]) >= 9 else 0.0,
        "go_report": 1.0 if report["decision"] == "go" else 0.0,
        "diagnostic_redacted": 1.0
        if diagnostic["status"] == "completed" and diagnostic["checksum"].startswith("sha256:")
        else 0.0,
    }
    assert all(value == 1.0 for value in metrics.values())
