from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.release import _phase116_blocks_release


def test_phase116_maturity_dashboard_endpoint_and_readiness_share_contract(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness")
    assert readiness.status_code == 200, readiness.text
    phase116 = readiness.json()["phase_readiness"]["phase116_maturity_dashboard_unification"]

    assert phase116["details"]["phase116_contract_version"] == "phase116.maturity_dashboard.v1"
    assert len(phase116["details"]["dimensions"]) == 5
    assert phase116["details"]["release_readiness"]["status"] in {
        "ready",
        "go_with_findings",
        "no_go",
    }

    dashboard = client.get("/api/system/maturity-dashboard")
    assert dashboard.status_code == 200, dashboard.text
    payload = dashboard.json()

    assert payload["contract_version"] == "phase116.maturity_dashboard.v1"
    assert payload["status"] == phase116["status"]
    assert len(payload["dimensions"]) == 5
    assert {item["key"] for item in payload["dimensions"]} == {
        "stability",
        "routing",
        "delivery",
        "extension",
        "quality",
    }
    assert payload["priority_queue"] == phase116["details"]["priority_queue"]
    assert payload["upstream_contracts"]["phase114_mainline_observability_closure"] == (
        "phase114.mainline_observability.v1"
    )


def test_phase116_release_summary_uses_same_contract_and_blocker_surface(
    client: TestClient,
) -> None:
    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    report = client.get(f"/api/release-gates/{gate_id}/report")
    assert report.status_code == 200, report.text
    summary = report.json()["summary"]

    dashboard = client.get("/api/system/maturity-dashboard").json()
    phase116 = summary["phase116_maturity_dashboard_unification"]

    assert phase116["contract_version"] == dashboard["contract_version"]
    assert phase116["release_readiness"] == dashboard["release_readiness"]
    assert phase116["top_blockers"] == dashboard["top_blockers"]
    assert phase116["priority_queue"] == dashboard["priority_queue"]


def test_phase116_quality_contract_drift_surfaces_shared_reason_code(
    client: TestClient,
    monkeypatch,
) -> None:
    registry = client.app.state.registry
    release_service = registry.release_gate_service
    readiness_service = registry.chat_mainline_readiness_service
    payload = {
        "run_id": "phase116-drift",
        "status": "passed",
        "profile": "smoke",
        "check_contract_version": "phase105.gate_signal_plane.v1",
        "duration_seconds": 5.0,
        "completed_at": "2026-05-18T00:00:00Z",
        "signal_suites": [],
        "commands": [],
        "command_matrix": {"smoke": ".\\scripts\\check.ps1 -Profile smoke"},
        "slow_test_report": {"source": "pytest --durations=20", "lines": []},
        "maturity_dashboard_summary": {
            "phase116_contract_version": "phase116.maturity_dashboard.v1",
            "dashboard_status": "partial",
            "top_blockers": [],
            "priority_queue_preview": [],
            "release_readiness": {"status": "go_with_findings", "p0_blocker_count": 0},
        },
    }
    monkeypatch.setattr(
        readiness_service,
        "_latest_root_check_report",
        lambda profile=None: payload if profile in {None, "smoke"} else {},
    )
    monkeypatch.setattr(
        release_service,
        "_latest_check_report",
        lambda profile=None: payload if profile in {None, "smoke"} else {},
    )

    dashboard = client.get("/api/system/maturity-dashboard").json()
    quality_dimension = next(item for item in dashboard["dimensions"] if item["key"] == "quality")
    quality_codes = {item["blocker_code"] for item in quality_dimension["blockers"]}
    assert "phase105_latest_smoke_signal_suite_drift" in quality_codes

    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    report = client.get(f"/api/release-gates/{gate_id}/report").json()
    phase116 = report["summary"]["phase116_maturity_dashboard_unification"]
    top_codes = {item["blocker_code"] for item in phase116["top_blockers"]}
    assert "phase105_latest_smoke_signal_suite_drift" in top_codes


def test_phase116_release_blocking_helper_only_blocks_p0_or_contract_drift() -> None:
    assert _phase116_blocks_release(
        {
            "priority_queue": [
                {"blocker_code": "phase113_latest_smoke_report_not_passed", "severity": "P1"}
            ],
            "release_readiness": {"blocking_contract_drifts": []},
        }
    ) is False
    assert _phase116_blocks_release(
        {
            "priority_queue": [{"blocker_code": "routing_path_not_stable", "severity": "P0"}],
            "release_readiness": {"blocking_contract_drifts": []},
        }
    ) is True
    assert _phase116_blocks_release(
        {
            "priority_queue": [],
            "release_readiness": {
                "blocking_contract_drifts": ["phase105_latest_smoke_contract_drift"]
            },
        }
    ) is True
