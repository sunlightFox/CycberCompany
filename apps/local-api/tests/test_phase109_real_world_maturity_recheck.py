from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase109_readiness_quantifies_real_world_stability_gaps(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase109 = readiness["phase_readiness"]["phase109_real_world_maturity_recheck"]

    assert phase109["status"] == "partial"
    assert (
        phase109["details"]["phase109_contract_version"]
        == "phase109.real_world_maturity_recheck.v1"
    )
    assert phase109["details"]["maturity_grade"] == "partial"
    assert "real_world_evidence_p0_gaps_present" in phase109["blocking_reasons"]
    assert "channel_long_run_no_turn_present" in phase109["blocking_reasons"]
    assert phase109["details"]["long_run_evidence_present"] is True
    diagnostics = phase109["details"]["no_turn_diagnostics"]
    assert diagnostics["evidence_no_turn_group_counts"]["routing"] >= 2
    assert diagnostics["top_evidence_no_turn_groups"][0]["name"] == "routing"
    assert diagnostics["likely_primary_causes"][0]["cause_code"] == "routing_path_not_stable"
    assert diagnostics["likely_primary_causes"][0]["classification"] == "evidence_gap"
    assert diagnostics["remediation_queue"][0]["cause_code"] == "routing_path_not_stable"
    assert diagnostics["remediation_queue"][0]["priority"] == "p0"
    bundles = {
        item["bundle_id"]: item for item in phase109["details"]["evidence_bundles"]
    }
    assert bundles["wechat_50_smoke"]["summary_present"] is True
    assert bundles["wechat_50_smoke"]["p0_gap_count"] >= 1
    assert bundles["wechat_50_smoke"]["no_turn_count"] >= 1
    assert bundles["wechat_real_smoke"]["summary_present"] is True
    assert bundles["wechat_real_smoke"]["p0_gap_count"] >= 1
    assert bundles["wechat_real_smoke"]["no_turn_count"] >= 1


def test_phase109_release_summary_exposes_maturity_grade_and_evidence(
    client: TestClient,
) -> None:
    created = client.post("/api/release-gates", json={})
    assert created.status_code == 200, created.text
    report = client.get(f"/api/release-gates/{created.json()['release_gate_id']}/report").json()

    phase109 = report["summary"]["phase109_real_world_maturity_recheck"]
    assert phase109["status"] == "partial"
    assert phase109["contract_version"] == "phase109.real_world_maturity_recheck.v1"
    assert phase109["maturity_grade"] == "partial"
    assert phase109["long_run_evidence_present"] is True
    assert phase109["blocking_gap_quantification"]["total_p0_gap_count"] >= 1
    diagnostics = phase109["no_turn_diagnostics"]
    assert diagnostics["evidence_no_turn_group_counts"]["routing"] >= 2
    assert diagnostics["top_evidence_no_turn_groups"][0]["name"] == "routing"
    assert diagnostics["likely_primary_causes"][0]["cause_code"] == "routing_path_not_stable"
    assert diagnostics["remediation_queue"][0]["classification"] == "evidence_gap"
    bundles = {item["bundle_id"]: item for item in phase109["evidence_bundles"]}
    assert "wechat_50_smoke" in bundles
    assert "wechat_real_smoke" in bundles
