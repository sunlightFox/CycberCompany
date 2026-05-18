from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import anyio
from fastapi.testclient import TestClient

from app.services.gate_signal_plane import (
    gate_signal_plane_contract_version,
    smoke_signal_suite_paths,
    smoke_signal_suite_summary,
)
from app.services.release import _phase29_command_matrix


def test_phase105_readiness_exposes_gate_signal_plane_and_smoke_backbone(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness")
    assert readiness.status_code == 200, readiness.text
    phase105 = readiness.json()["phase_readiness"]["phase105_gate_signal_plane_governance"]

    assert phase105["status"] == "ready"
    assert phase105["details"]["phase105_contract_version"] == "phase105.gate_signal_plane.v1"
    assert (
        phase105["details"]["check_contract_version"]
        == gate_signal_plane_contract_version()
    )
    assert "phase104_check_script_recovery" in phase105["details"]["smoke_signal_phase_keys"]
    assert "phase104_check_report_contract" in phase105["details"]["smoke_signal_phase_keys"]
    assert phase105["details"]["smoke_regression_command"] == ".\\scripts\\check.ps1 -Profile smoke"
    assert "latest_smoke_report_blockers" in phase105["details"]


def test_phase105_release_summary_uses_latest_smoke_check_report_contract(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    release_service = registry.release_gate_service
    data_dir = Path(release_service._config.storage.data_dir)
    report_dir = data_dir / "check-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = smoke_signal_suite_summary()
    payload = {
        "run_id": "99999999T999999Z",
        "status": "passed",
        "profile": "smoke",
        "check_contract_version": gate_signal_plane_contract_version(),
        "duration_seconds": 42.0,
        "completed_at": "2026-05-17T00:00:00Z",
        "signal_suites": summary["signal_suites"],
        "commands": [
            {
                "name": "pytest_smoke",
                "status": "passed",
                "exit_code": 0,
                "duration_seconds": 42.0,
                "log_path": str(report_dir / "pytest_smoke.log"),
            }
        ],
        "command_matrix": _phase29_command_matrix(),
        "slow_test_report": {"source": "pytest --durations=20", "lines": []},
    }
    report_path = report_dir / "check-99999999T999999Z.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    signal_summary = anyio.run(release_service.chat_mainline_signal_summary)

    assert signal_summary["phase105_gate_signal_plane_governance_status"] == "ready"
    assert signal_summary["phase105_contract_version"] == "phase105.gate_signal_plane.v1"
    assert signal_summary["phase105_latest_smoke_report_present"] is True
    assert signal_summary["phase105_latest_smoke_report_status"] == "passed"
    assert signal_summary["phase105_latest_smoke_contract_match"] is True
    assert signal_summary["phase105_latest_smoke_signal_suite_match"] is True
    assert signal_summary["phase105_missing_signal_paths"] == []
    assert signal_summary["phase105_drift_signal_paths"] == []
    assert signal_summary["phase105_latest_smoke_report_blockers"] == []


def test_phase105_shared_signal_manifest_drives_script_report_and_python() -> None:
    root_dir = Path(__file__).resolve().parents[3]
    check_script = (root_dir / "scripts" / "check.ps1").read_text(encoding="utf-8")
    smoke_paths = smoke_signal_suite_paths()
    command_matrix = _phase29_command_matrix()

    assert smoke_paths
    assert "config\\gate_signal_plane.json" in check_script
    for path in smoke_paths:
        assert path in command_matrix["smoke_backend"]
    assert "Get-GateSignalProfile" in check_script
    assert "signal_suites" in check_script
