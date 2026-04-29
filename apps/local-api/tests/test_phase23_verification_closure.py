from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_phase23_markers_contracts_and_required_suites(client: TestClient) -> None:
    pyproject = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    markers = {
        item.split(":", 1)[0]
        for item in pyproject["tool"]["pytest"]["ini_options"]["markers"]
    }
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    by_module = {item["name"]: item for item in contracts}

    assert {
        "unit",
        "api",
        "integration",
        "eval",
        "slow",
        "release",
        "security",
        "chat_main_chain",
    }.issubset(markers)
    assert by_module["VerificationClosure"]["status"] == "implemented"
    assert by_module["TestMatrix"]["status"] == "implemented"
    assert by_module["EvalEvidenceAggregator"]["status"] == "implemented"
    assert by_module["AcceptedRiskRegistry"]["status"] == "implemented"
    assert {
        "suite_phase17_chat_main_chain",
        "suite_phase18_dialogue_intent_semantics",
        "suite_phase19_model_planner_agent",
        "suite_phase20_memory_knowledge_quality",
        "suite_phase21_execution_boundary",
        "suite_phase22_persona_heart_experience",
        "suite_phase24_model_semantic_verifier",
        "suite_phase25_model_planner_quality",
        "suite_phase26_embedding_retrieval_quality",
        "suite_phase27_os_sandbox",
        "suite_phase28_mcp_runtime_isolation",
        "suite_phase29_release_scale_verification",
    }.issubset({item["suite_id"] for item in suites})


def test_phase23_release_report_and_diagnostic_close_verification_loop(
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
    diagnostic = client.get(f"/api/diagnostics/bundles/{diagnostic_id}").json()
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic_payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    phase23 = report["summary"]["phase23"]

    assert completed["status"] == "ready_for_release"
    assert report["decision"] == "go"
    assert report["summary"]["go_no_go_reason"].startswith("go:")
    assert phase23["eval_status"]["registered_suites"] >= 12
    assert phase23["eval_status"]["failed_cases"] == 0
    assert phase23["secret_leakage_status"]["hit_count"] == 0
    assert phase23["trace_integrity_status"]["status"] == "passed"
    assert {"ruff", "mypy", "pytest"}.issubset(phase23["tooling_status"])
    assert phase23["test_status"]["target_seconds"] == 900
    assert phase23["capability_scores"]["phase22"]["registered"] is True
    assert phase23["capability_scores"]["phase26"]["registered"] is True
    assert phase23["capability_scores"]["phase27"]["registered"] is True
    assert phase23["capability_scores"]["phase28"]["registered"] is True
    assert phase23["capability_scores"]["phase29"]["registered"] is True
    assert any(item["source_type"] == "phase23_verification_closure" for item in evidence)
    assert diagnostic["status"] == "completed"
    assert "phase23" in diagnostic_payload
    assert diagnostic_payload["phase23"]["eval_status"]["registered_suites"] >= 12
    assert "phase26" in diagnostic_payload
    assert "phase27" in diagnostic_payload
    assert "phase28" in diagnostic_payload
    assert "phase29" in diagnostic_payload
    assert diagnostic_payload["phase23"]["accepted_risks"]
    assert "phase23-secret-value" not in json.dumps(
        {"report": report, "diagnostic": diagnostic_payload},
        ensure_ascii=False,
    )


def test_phase23_accepted_risk_registry_covers_known_degraded_paths(
    client: TestClient,
) -> None:
    client.get("/api/system/runtime-contracts")
    gate = client.post("/api/release-gates", json={}).json()
    client.post(f"/api/release-gates/{gate['release_gate_id']}/run")
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    risks = report["summary"]["phase23"]["accepted_risks"]
    modules = {item["module"] for item in risks}
    risk_ids = {item["risk_id"] for item in risks}

    assert {
        "TerminalRunner",
        "MCPConnectionManager",
        "EmbeddingProviderResolver",
        "PersonaHeartLongitudinalEval",
        "VerificationClosure",
        "CIVerificationMatrix",
    }.issubset(modules)
    assert "gap_phase23_local_verification_not_ci" in risk_ids
    assert "gap_phase29_external_ci_provider_not_configured" in risk_ids
    assert all(item["why_accepted"] and item["owner_phase"] for item in risks)
