from __future__ import annotations

import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import anyio
from core_types import EvidenceType
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_phase29_suite_contracts_profiles_and_no_new_migration(
    client: TestClient,
) -> None:
    pyproject = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    markers = {
        item.split(":", 1)[0]
        for item in pyproject["tool"]["pytest"]["ini_options"]["markers"]
    }
    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    by_module = {item["name"]: item for item in contracts}
    check_script = (ROOT_DIR / "scripts/check.ps1").read_text(encoding="utf-8")

    migration_contract = assert_phase_migration_contract(client, "phase29")
    assert migration_contract["current_at_least_required"] is True
    assert {"slow", "release", "security", "eval"}.issubset(markers)
    assert "suite_phase29_release_scale_verification" in {
        item["suite_id"] for item in suites
    }
    assert by_module["ReleaseGate"]["status"] == "implemented_with_release_grade_evidence"
    assert by_module["CIVerificationMatrix"]["status"] == "implemented"
    assert by_module["LongRunExperienceEval"]["status"] == "implemented"
    assert by_module["PerformanceResourceBenchmark"]["status"] == "implemented"
    assert by_module["AcceptedRiskLifecycle"]["status"] == "implemented"
    assert "-Profile full" in check_script
    assert "-Profile release" in check_script
    assert any(
        item["gap_id"] == "gap_phase29_external_ci_provider_not_configured"
        and item["expires_at"]
        and item["promotion_rule"]
        for item in gaps
    )


def test_phase29_release_report_diagnostic_and_phase23_aggregation(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic_payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    phase29 = report["summary"]["phase29"]

    assert completed["status"] == "ready_for_release"
    assert report["decision"] == "go"
    assert phase29["registered_cases"] == 12
    assert phase29["failed_results"] == 0
    assert phase29["ci_profile_status"]["profiles_ready"] is True
    assert phase29["long_eval_status"]["continuity_score"] >= 0.98
    assert phase29["accepted_risk_lifecycle"]["blocking_count"] == 0
    assert phase29["release_grade_inputs"]["zero_tolerance_failures"] == 0
    assert phase29["performance_status"]["status"] in {"passed", "degraded"}
    assert phase29["migration_backup_restore_status"]["status"] == "passed"
    assert report["summary"]["phase23"]["capability_scores"]["phase29"]["registered"] is True
    assert any(item["source_type"] == "phase29_release_scale_verification" for item in evidence)
    assert "phase29" in diagnostic_payload
    assert diagnostic_payload["phase29"]["diagnostic_drilldown"]["phase17_28_coverage"]
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic_payload}) == 0


def test_phase29_accepted_risk_expiry_promotes_to_blocker(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    expired_at = (datetime.now(UTC) - timedelta(days=220)).isoformat()
    anyio.run(
        registry.db.execute,
        """
        INSERT INTO design_gaps (
          gap_id, module_name, current_behavior, design_gap, blocker_level,
          fix_phase, acceptance_tests_json, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gap_phase29_expired_test",
            "Phase29RiskTest",
            "expired risk test",
            "risk is intentionally expired for blocker promotion",
            "none",
            "phase29_test_owner",
            '["risk has a mitigation but is expired"]',
            "accepted_risk",
            expired_at,
            expired_at,
        ),
    )

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    risks = report["summary"]["phase29"]["accepted_risk_lifecycle"]["items"]

    assert completed["status"] == "blocked"
    assert report["decision"] == "no_go"
    assert any(
        item["risk_id"] == "gap_phase29_expired_test" and item["status"] == "expired"
        for item in risks
    )
    assert report["summary"]["phase29"]["accepted_risk_lifecycle"]["blocking_count"] >= 1
    assert report["findings_summary"]["blocker_count"] >= 1


def test_phase29_performance_warning_degrades_and_severe_blocks(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    service = registry.release_gate_service
    gate = client.post("/api/release-gates", json={}).json()
    gate_id = gate["release_gate_id"]

    anyio.run(_add_benchmark_evidence, service, gate_id, "phase29_warn", 3000, "passed")
    degraded = anyio.run(service._phase29_report_summary, gate_id)
    assert degraded["performance_status"]["status"] == "degraded"
    assert degraded["performance_status"]["blocking_count"] == 0

    anyio.run(_add_benchmark_evidence, service, gate_id, "phase29_block", 12000, "failed")
    blocked = anyio.run(service._phase29_report_summary, gate_id)
    assert blocked["performance_status"]["status"] == "failed"
    assert blocked["performance_status"]["blocking_count"] >= 1


def _payload_leakage_count(payload: dict[str, Any]) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "secret=",
        "token=",
        "cookie=",
        "private_key",
        "mnemonic",
        "c:\\users\\administrator",
    ]
    return sum(1 for marker in forbidden if marker in serialized)


async def _add_benchmark_evidence(
    service: Any,
    gate_id: str,
    source_id: str,
    db_smoke_ms: int,
    status: str,
) -> None:
    await service._add_evidence(
        gate_id,
        EvidenceType.BENCHMARK_RUN,
        source_type="benchmark_run",
        source_id=source_id,
        summary={"metrics": {"db_smoke_ms": db_smoke_ms}, "resources": {}},
        status=status,
    )
