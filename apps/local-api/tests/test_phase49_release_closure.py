from __future__ import annotations

import json
from typing import Any, cast

from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase49_release_closure_suite_contracts_report_and_diagnostic(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase49")
    assert migration_contract["required_migration"] == "031_media_runtime.sql"
    assert migration_contract["required_tables"] == []

    suites_once = client.get("/api/evals/suites").json()["items"]
    suites_twice = client.get("/api/evals/suites").json()["items"]
    phase49_suites = [
        item for item in suites_twice if item["suite_id"] == "suite_phase49_release_closure"
    ]
    assert len(phase49_suites) == 1
    assert len(suites_once) == len(suites_twice)

    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    for module in [
        "RealModelReleaseClosure",
        "ReleaseClosureEvidenceMatrix",
        "CompositeBackendE2EReplay",
        "ProductionCaseIdDependencyScan",
        "ReleaseLeakageScanMatrix",
        "AcceptedRiskClosureRegistry",
        "BackendSealingReport",
    ]:
        assert by_name[module]["status"] == "implemented"

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase49_release_closure"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 10

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))

    phase49 = report["summary"]["phase49"]
    coverage = phase49["phase35_48_coverage"]
    assert completed["status"] == "ready_for_release"
    assert phase49["suite_id"] == "suite_phase49_release_closure"
    assert phase49["registered_cases"] == 10
    assert phase49["full_pass"] is True
    assert coverage["all_required_readable"] is True
    assert "phase44" in coverage["phases"]
    assert coverage["phases"]["phase48"]["suite_id"] == "suite_phase48_governance_closure"
    assert phase49["quality_runner"]["matrix_ready"] is True
    assert phase49["real_model_smoke"]["matrix_ready"] is True
    assert phase49["composite_e2e"]["matrix_ready"] is True
    assert phase49["production_case_id_scan"]["hit_count"] == 0
    assert phase49["leakage_scan"]["leakage_count"] == 0
    assert phase49["accepted_risk_closure"]["blocking_count"] == 0
    assert phase49["accepted_risk_closure"]["total"] >= 1
    assert phase49["backend_sealing_report"]["ready"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase49"]["registered"] is True
    assert any(item["source_type"] == "phase49_release_closure" for item in evidence)
    assert "phase49" in diagnostic
    assert "phase49_release_closure" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def test_phase49_design_gaps_include_closure_fields(client: TestClient) -> None:
    gaps = client.get("/api/system/design-gaps").json()["items"]
    phase49_gap = next(
        item
        for item in gaps
        if item["gap_id"] == "gap_phase49_real_model_release_environment_dependent"
    )
    assert phase49_gap["status"] == "accepted_risk"
    assert phase49_gap["fix_phase"] == "future_hosted_real_model_release_ci"
    assert phase49_gap["acceptance_tests"]
    assert _payload_leakage_count(phase49_gap) == 0


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "sk-phase49-secret",
        "token=phase49",
        "cookie=phase49",
        "private_key=phase49",
        "mnemonic=phase49",
        "c:\\users\\administrator\\phase49",
    ]
    return sum(1 for item in forbidden if item in serialized)
