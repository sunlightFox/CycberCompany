from typing import Any

import anyio
from fastapi.testclient import TestClient

from app.core.time import utc_now_iso


def test_phase114_readiness_and_observability_endpoint_share_contract(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness")
    assert readiness.status_code == 200, readiness.text
    phase114 = readiness.json()["phase_readiness"]["phase114_mainline_observability_closure"]

    assert phase114["details"]["phase114_contract_version"] == "phase114.mainline_observability.v1"
    assert set(phase114["details"]["mainline_rates"]) == {
        "turn_created_rate",
        "turn_queued_rate",
        "turn_completed_rate",
        "approval_resolution_rate",
        "final_deliverable_rate",
    }
    assert "by_channel" in phase114["details"]["segmented_views"]
    assert "by_domain" in phase114["details"]["segmented_views"]
    assert "by_runtime_path" in phase114["details"]["segmented_views"]

    observability = client.get("/api/system/chat-mainline-observability")
    assert observability.status_code == 200, observability.text
    payload = observability.json()

    assert payload["contract_version"] == "phase114.mainline_observability.v1"
    assert payload["status"] == phase114["status"]
    assert payload["mainline_rates"] == phase114["details"]["mainline_rates"]
    assert payload["segmented_views"] == phase114["details"]["segmented_views"]
    assert payload["replay_alignment"]["routing_replay_fields_present"] is True

    dashboard = client.get("/api/system/maturity-dashboard")
    assert dashboard.status_code == 200, dashboard.text
    dashboard_payload = dashboard.json()
    routing_dimension = next(
        item for item in dashboard_payload["dimensions"] if item["key"] == "routing"
    )
    assert dashboard_payload["upstream_contracts"]["phase114_mainline_observability_closure"] == (
        "phase114.mainline_observability.v1"
    )
    assert routing_dimension["upstream_phase_keys"] == ["phase110_channel_routing_stability"]


def test_phase114_zero_sample_rates_return_null_with_zero_sample_size(
    client: TestClient,
) -> None:
    payload = client.get("/api/system/chat-mainline-observability").json()

    for item in payload["mainline_rates"].values():
        if item["sample_size"] == 0:
            assert item["rate"] is None
            assert item["denominator"] == 0


def test_phase114_release_summary_reuses_shared_metrics_and_tracks_approval_resolution(
    client: TestClient,
) -> None:
    created = client.post("/api/release-gates", json={})
    assert created.status_code == 200, created.text
    release_gate_id = created.json()["release_gate_id"]
    seeded_task = client.post(
        "/api/tasks",
        json={
            "goal": "Prepare a repository patch summary",
            "office_request": {
                "request_type": "mail",
                "operation": "draft",
                "title": "Patch summary",
                "summary": "Summarize the latest repository patch.",
                "content": "Patch summary content.",
                "recipients": ["review@example.com"],
            },
            "auto_start": True,
        },
    ).json()

    registry = client.app.state.registry
    _insert_closure_record(
        registry.release_gate_service._repo,
        task_id=seeded_task["task_id"],
        release_gate_id=release_gate_id,
        domain="repo_local",
        task_tier="t1",
        delivery_status="delivered",
        verification_status="passed",
        final_deliverable=True,
        once_success=True,
    )

    waiting = client.post(
        "/api/tasks",
        json={
            "goal": "Send the signed contract summary to the customer",
            "office_request": {
                "request_type": "mail",
                "operation": "send",
                "title": "Signed contract summary",
                "summary": "Send the approved contract recap to the customer.",
                "content": "Please find the signed contract recap and next implementation milestone.",
                "recipients": ["customer@example.com"],
            },
            "auto_start": True,
        },
    ).json()
    assert waiting["status"] == "waiting_approval"
    approved = client.post(
        f"/api/approvals/{waiting['current_approval_id']}/approve",
        json={"reason": "phase114"},
    )
    assert approved.status_code == 200, approved.text

    observability = client.get("/api/system/chat-mainline-observability").json()
    approval_rate = observability["mainline_rates"]["approval_resolution_rate"]
    assert approval_rate["sample_size"] >= 1
    assert approval_rate["rate"] == 1.0

    report = client.get(f"/api/release-gates/{release_gate_id}/report").json()
    phase103 = report["summary"]["phase103_task_closure_gate"]
    phase114 = report["summary"]["phase114_mainline_observability_closure"]

    assert phase114["contract_version"] == "phase114.mainline_observability.v1"
    assert phase114["mainline_rates"]["approval_resolution_rate"]["rate"] == 1.0
    assert (
        phase114["mainline_rates"]["final_deliverable_rate"]["rate"]
        == phase103["overall_metrics"]["final_deliverable_rate"]
    )
    assert phase114["replay_alignment"]["routing_replay_fields_present"] is True


def _insert_closure_record(
    repo: Any,
    *,
    task_id: str,
    release_gate_id: str,
    domain: str,
    task_tier: str,
    delivery_status: str,
    verification_status: str,
    final_deliverable: bool,
    once_success: bool,
) -> None:
    anyio.run(
        repo.insert_task_closure_record,
        {
            "closure_record_id": f"closure_{domain}_{utc_now_iso()}",
            "organization_id": "org_default",
            "task_id": task_id,
            "release_gate_id": release_gate_id,
            "source_eval_run_id": None,
            "domain": domain,
            "task_tier": task_tier,
            "delivery_status": delivery_status,
            "delivery_blockers": [],
            "handoff_reason": None,
            "approval_interruption": False,
            "recovery_summary": {},
            "verification_status": verification_status,
            "once_success": once_success,
            "final_deliverable": final_deliverable,
            "human_handoff": False,
            "error_recovered": False,
            "round_count": 1,
            "tool_call_count": 1,
            "replan_count": 0,
            "stop_reason": delivery_status,
            "untrusted_observation_triggered": False,
            "residual_risk_present": False,
            "created_at": utc_now_iso(),
        },
    )
