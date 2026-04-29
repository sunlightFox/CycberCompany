from __future__ import annotations

import json
from typing import Any, cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase8_release_gate_runs_and_collects_evidence(client: TestClient) -> None:
    created = client.post("/api/release-gates", json={}).json()
    gate_id = created["release_gate_id"]

    completed = client.post(f"/api/release-gates/{gate_id}/run").json()
    evidence = client.get(f"/api/release-gates/{gate_id}/evidence").json()["items"]
    findings = client.get(f"/api/release-gates/{gate_id}/findings").json()["items"]
    report = client.get(f"/api/release-gates/{gate_id}/report").json()

    evidence_types = {item["evidence_type"] for item in evidence}
    assert completed["status"] in {"ready_for_release", "blocked"}
    assert completed["blocker_count"] == 0
    assert findings == []
    assert {
        "eval_run",
        "security_audit_run",
        "trace_integrity_run",
        "audit_integrity_run",
        "replay_integrity_run",
        "permission_boundary_run",
        "backup_restore_run",
        "benchmark_run",
        "diagnostic_bundle",
        "release_report",
    }.issubset(evidence_types)
    assert report["decision"] == "go"
    assert report["checksum"].startswith("sha256:")


def test_phase8_eval_security_backup_restore_and_diagnostic_api(
    client: TestClient,
) -> None:
    suites = client.get("/api/evals/suites").json()["items"]
    eval_run = client.post("/api/evals/runs", json={}).json()
    security_run = client.post("/api/security/audit-runs", json={}).json()
    backup = client.post("/api/backup/jobs", json={}).json()
    restore = client.post(
        "/api/restore/jobs",
        json={"backup_job_id": backup["backup_job_id"]},
    ).json()
    benchmark = client.post("/api/benchmarks/runs", json={}).json()
    diagnostic = client.post("/api/diagnostics/bundles", json={}).json()
    full_health = client.get("/api/health/full").json()

    assert len(suites) >= 8
    assert eval_run["status"] == "passed"
    assert security_run["status"] == "passed"
    assert backup["status"] == "completed"
    assert backup["checksum"].startswith("sha256:")
    assert restore["status"] == "completed"
    assert restore["checksum_verified"] is True
    assert benchmark["status"] == "passed"
    assert diagnostic["status"] == "completed"
    assert diagnostic["checksum"].startswith("sha256:")
    assert full_health["release_gate_readiness"]["eval_suites"] >= 8


def test_phase8_secret_leakage_blocks_release_gate(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry
    anyio.run(
        _insert_leaky_audit_payload,
        registry,
        "api_key=sk-thisShouldBlockRelease123456",
    )

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    findings = client.get(f"/api/release-gates/{gate['release_gate_id']}/findings").json()
    finding_text = json.dumps(findings, ensure_ascii=False)

    assert completed["status"] == "blocked"
    assert completed["blocker_count"] >= 1
    assert "secret_leakage" in finding_text
    assert "sk-thisShouldBlockRelease123456" not in finding_text


async def _insert_leaky_audit_payload(registry: Any, secret: str) -> None:
    await registry.db.execute(
        """
        INSERT INTO audit_events (
          audit_id, actor_type, actor_id, action, object_type, object_id, risk_level,
          summary, payload_redacted_json, trace_id, created_at
        ) VALUES (
          'aud_leak', 'system', NULL, 'test.leak', 'test', 'obj_leak', 'R1',
          'leak fixture', ?, NULL, '2026-01-01T00:00:00+00:00'
        )
        """,
        (json.dumps({"value": secret}),),
    )
