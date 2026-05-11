from __future__ import annotations

import json
from typing import Any, cast

from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase68_release_summary_and_diagnostic_are_wired(client: TestClient) -> None:
    suites = client.get("/api/evals/suites").json()["items"]
    registry = cast(Any, client.app).state.registry

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    phase68 = report["summary"]["phase68"]

    migration_contract = assert_phase_migration_contract(client, "phase68")
    assert migration_contract["current_at_least_required"] is True
    assert "suite_phase68_chat_quality_gate_rebuild" in {item["suite_id"] for item in suites}
    assert phase68["suite_id"] == "suite_phase68_chat_quality_gate_rebuild"
    assert phase68["batch_id"] == "CHAT-QUALITY-GATE-20260503"
    assert len(phase68["quality_batch"]["runners"]) == 3
    assert "prompt_version_coverage" in phase68
    assert phase68["prompt_version_coverage"]["voice_policy_v4_coverage"] == 1.0
    assert phase68["prompt_version_coverage"]["prompt_assembly_v4_coverage"] == 1.0
    assert phase68["prompt_version_coverage"]["coverage_source"] == "phase68_contract_test_fallback"
    assert "gate_status_counts" in phase68
    assert "shadow_policy" in phase68
    assert "continuation_usage" in phase68
    assert phase68["check_script_wiring"]["release_profile_runs_all_batches"] is True
    assert phase68["check_script_wiring"]["prompt_residual_gate_wired"] is True
    assert phase68["check_script_wiring"]["visible_leakage_gate_wired"] is True
    assert phase68["runtime_old_prompt_residual_hits"] == []
    assert "phase89_false_interception_governance" in report["summary"]
    assert any(item["source_type"] == "phase68_chat_quality_gate_rebuild" for item in evidence)
    assert "phase68" in diagnostic
    assert "phase68_chat_quality_gate_rebuild" in diagnostic
