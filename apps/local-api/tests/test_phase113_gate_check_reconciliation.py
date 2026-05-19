from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import anyio
from fastapi.testclient import TestClient

from app.services.gate_signal_plane import (
    gate_signal_plane_contract_version,
    smoke_signal_suite_summary,
)


def test_phase113_release_summary_marks_smoke_suite_drift_with_shared_reason_codes(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    release_service = registry.release_gate_service
    data_dir = Path(release_service._config.storage.data_dir)
    report_dir = data_dir / "check-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = smoke_signal_suite_summary()
    drifted_suites = list(summary["signal_suites"][:-1])
    payload = {
        "run_id": "99999999T999998Z",
        "status": "passed",
        "profile": "smoke",
        "check_contract_version": gate_signal_plane_contract_version(),
        "duration_seconds": 12.0,
        "completed_at": "2026-05-17T00:00:00Z",
        "signal_suites": drifted_suites,
        "commands": [
            {
                "name": "pytest_smoke",
                "status": "passed",
                "exit_code": 0,
                "duration_seconds": 12.0,
                "log_path": str(report_dir / "pytest_smoke.log"),
            }
        ],
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
    report_path = report_dir / "check-99999999T999998Z.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    signal_summary = anyio.run(release_service.chat_mainline_signal_summary)

    assert signal_summary["phase105_latest_smoke_report_present"] is True
    assert signal_summary["phase105_latest_smoke_contract_match"] is True
    assert signal_summary["phase105_latest_smoke_signal_suite_match"] is False
    assert signal_summary["phase105_latest_smoke_report_blockers"] == [
        "phase105_latest_smoke_signal_suite_drift"
    ]
    assert payload["maturity_dashboard_summary"]["phase116_contract_version"] == (
        "phase116.maturity_dashboard.v1"
    )
