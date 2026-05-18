from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase103_smoke_release_report_exposes_task_closure_gate_summary(
    client: TestClient,
) -> None:
    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]

    report = client.get(f"/api/release-gates/{gate_id}/report")
    assert report.status_code == 200, report.text

    summary = report.json()["summary"]["phase103_task_closure_gate"]
    assert summary["suite_id"] == "suite_phase103_task_closure_gate"
    assert "per_domain_scorecard" in summary
    assert "extension_ecosystem" in summary["per_domain_scorecard"]
    assert "overall_metrics" in summary
