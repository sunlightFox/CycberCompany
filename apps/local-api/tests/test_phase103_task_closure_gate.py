from __future__ import annotations

import json
from typing import Any, cast

import anyio
from fastapi.testclient import TestClient

from app.core.time import utc_now_iso


def test_phase103_report_and_diagnostic_include_domain_scorecards_and_trends(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    release_service = registry.release_gate_service
    repo = registry.release

    seed_task = client.post(
        "/api/tasks",
        json={
            "goal": "Prepare a customer follow-up email draft",
            "office_request": {
                "request_type": "mail",
                "operation": "draft",
                "title": "Phase103 seed",
                "summary": "Seed task for phase103 closure records.",
                "content": "Seed body.",
                "recipients": ["customer@example.com"],
            },
            "auto_start": True,
        },
    ).json()
    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]

    _insert_closure_record(
        repo,
        task_id=seed_task["task_id"],
        release_gate_id=gate_id,
        domain="repo_local",
        task_tier="L2",
        delivery_status="delivered_after_recovery",
        verification_status="passed",
        final_deliverable=True,
        error_recovered=True,
        recovery_summary={"repair_attempted": True, "repair_outcome": "resolved"},
    )
    _insert_closure_record(
        repo,
        task_id=seed_task["task_id"],
        release_gate_id=gate_id,
        domain="code_hosting",
        task_tier="L3",
        delivery_status="delivered",
        verification_status="passed",
        final_deliverable=True,
        once_success=True,
    )
    _insert_closure_record(
        repo,
        task_id=seed_task["task_id"],
        release_gate_id=gate_id,
        domain="content_platform",
        task_tier="L3",
        delivery_status="waiting_handoff",
        verification_status="failed",
        human_handoff=True,
        delivery_blockers=["visible_publish_proof_missing"],
    )
    _insert_closure_record(
        repo,
        task_id=seed_task["task_id"],
        release_gate_id=gate_id,
        domain="office_productivity",
        task_tier="L2",
        delivery_status="waiting_approval",
        verification_status="not_required",
        approval_interruption=True,
        delivery_blockers=["pending_approval"],
    )
    _insert_closure_record(
        repo,
        task_id=seed_task["task_id"],
        release_gate_id=gate_id,
        domain="video_workflow",
        task_tier="L3",
        delivery_status="completed_unverified",
        verification_status="missing",
        delivery_blockers=["verification_missing"],
    )

    first = anyio.run(release_service.generate_report, gate_id)
    _insert_closure_record(
        repo,
        task_id=seed_task["task_id"],
        release_gate_id=gate_id,
        domain="repo_local",
        task_tier="L2",
        delivery_status="delivered",
        verification_status="passed",
        final_deliverable=True,
        once_success=True,
    )
    second = anyio.run(release_service.generate_report, gate_id)
    summary = second.summary["phase103_task_closure_gate"]

    assert first.summary["phase103_task_closure_gate"]["suite_id"] == "suite_phase103_task_closure_gate"
    assert second.decision.value == "no_go"
    assert set(summary["per_domain_scorecard"]) == {
        "repo_local",
        "code_hosting",
        "content_platform",
        "office_productivity",
        "extension_ecosystem",
        "video_workflow",
    }
    assert summary["per_domain_scorecard"]["repo_local"]["recovery_success_rate"] == 1.0
    assert summary["per_domain_scorecard"]["office_productivity"]["delivery_status_counts"][
        "waiting_approval"
    ] == 1
    assert any(
        item["domain"] == "video_workflow" and item["metric"] == "verification_gate"
        for item in summary["blocking_reasons"]
    )
    assert summary["trend_summary"]["snapshots"]
    assert "phase103_task_closure_gate" in second.summary

    bundle = anyio.run(
        lambda: release_service.create_diagnostic_bundle(scope={"release_gate_id": gate_id})
    )
    bundle_name = bundle.output_uri.removeprefix("diagnostic://")
    payload = json.loads((release_service._diagnostic_dir / bundle_name).read_text(encoding="utf-8"))
    assert payload["phase103_task_closure_gate"]["per_domain_scorecard"]["repo_local"]
    assert payload["phase103_task_closure_gate"]["trend_summary"]["drift"]


def test_phase103_run_gate_emits_evidence_and_report_summary(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "修复 repo 单文件 bug 并跑测试",
            "mode_hint": "agent",
            "auto_start": True,
            "constraints": {
                "repo_request_type": "repo_patch_request",
                "repo_patch_path": "src/app.py",
                "repo_patch_content": "fixed = True\n",
                "verify_read_path": "src/app.py",
                "verify_contains_text": "fixed = True",
            },
        },
    ).json()
    assert task["status"] == "completed"

    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    run = client.post(f"/api/release-gates/{gate_id}/run")
    assert run.status_code == 200, run.text

    report = client.get(f"/api/release-gates/{gate_id}/report").json()
    evidence = client.get(f"/api/release-gates/{gate_id}/evidence").json()["items"]

    assert report["summary"]["phase103_task_closure_gate"]["suite_id"] == "suite_phase103_task_closure_gate"
    assert any(item["source_type"] == "phase103_task_closure_gate" for item in evidence)


def _insert_closure_record(
    repo: Any,
    *,
    task_id: str,
    release_gate_id: str,
    domain: str,
    task_tier: str,
    delivery_status: str,
    verification_status: str,
    final_deliverable: bool = False,
    once_success: bool = False,
    human_handoff: bool = False,
    approval_interruption: bool = False,
    error_recovered: bool = False,
    delivery_blockers: list[str] | None = None,
    recovery_summary: dict[str, Any] | None = None,
) -> None:
    anyio.run(
        repo.insert_task_closure_record,
        {
            "closure_record_id": f"closure_{domain}_{delivery_status}_{utc_now_iso()}",
            "organization_id": "org_default",
            "task_id": task_id,
            "release_gate_id": release_gate_id,
            "source_eval_run_id": None,
            "domain": domain,
            "task_tier": task_tier,
            "delivery_status": delivery_status,
            "delivery_blockers": delivery_blockers or [],
            "handoff_reason": "human_resume_required" if human_handoff else None,
            "approval_interruption": approval_interruption,
            "recovery_summary": recovery_summary or {},
            "verification_status": verification_status,
            "once_success": once_success,
            "final_deliverable": final_deliverable,
            "human_handoff": human_handoff,
            "error_recovered": error_recovered,
            "round_count": 1,
            "tool_call_count": 1,
            "replan_count": 0,
            "stop_reason": delivery_status,
            "untrusted_observation_triggered": False,
            "residual_risk_present": False,
            "created_at": utc_now_iso(),
        },
    )
