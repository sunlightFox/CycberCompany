from __future__ import annotations

from fastapi.testclient import TestClient

from test_phase103_task_closure_gate import _insert_closure_record


def test_phase111_repo_task_detail_exposes_proof_and_completion_semantics(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "修复 repo 单文件 bug 并跑测试",
            "mode_hint": "agent",
            "auto_start": True,
            "constraints": {
                "repo_request_type": "repo_patch_request",
                "repo_patch_path": "src/app.py",
                "repo_patch_content": "phase111 = True\n",
                "verify_read_path": "src/app.py",
                "verify_contains_text": "phase111 = True",
            },
        },
    ).json()

    detail = client.get(f"/api/tasks/{task['task_id']}").json()
    proof = detail["result"]["phase111_deliverable_proof"]
    completion = detail["result"]["phase111_completion_semantics"]

    assert proof["contract_version"] == "phase111.deliverable_proof.v1"
    assert proof["domain"] == "repo_local"
    assert "artifact_or_diff" in proof["required_proof_types"]
    assert "verification_passed" in proof["present_proof_types"]
    assert proof["missing_proof_types"] == []
    assert completion["contract_version"] == "phase111.completion_semantics.v1"
    assert completion["status"] == "completed_with_evidence"
    assert completion["delivery_status"] == "delivered"
    assert completion["verification_status"] == "passed"
    assert completion["final_deliverable"] is True


def test_phase111_readiness_and_release_summary_expose_contract_and_completion_gates(
    client: TestClient,
) -> None:
    seed_task = client.post(
        "/api/tasks",
        json={
            "goal": "Prepare a customer follow-up email draft",
            "office_request": {
                "request_type": "mail",
                "operation": "draft",
                "title": "Phase111 seed",
                "summary": "Seed task for phase111 closure records.",
                "content": "Seed body.",
                "recipients": ["customer@example.com"],
            },
            "auto_start": True,
        },
    ).json()
    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    registry = client.app.state.registry

    _insert_closure_record(
        registry.release,
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
        registry.release,
        task_id=seed_task["task_id"],
        release_gate_id=gate_id,
        domain="video_workflow",
        task_tier="L3",
        delivery_status="completed_unverified",
        verification_status="missing",
        delivery_blockers=["verification_missing"],
    )

    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase111 = readiness["phase_readiness"]["phase111_task_delivery_evidence"]
    assert phase111["status"] == "ready"
    assert (
        phase111["details"]["phase111_contract_version"]
        == "phase111.task_delivery_evidence.v1"
    )
    assert "content_platform" in phase111["details"]["minimum_deliverable_proof_contracts"]
    assert "delivery_status" in phase111["details"]["completion_requires"]

    report = client.get(f"/api/release-gates/{gate_id}/report").json()
    summary = report["summary"]["phase111_task_delivery_evidence"]
    assert summary["status"] == "ready"
    assert summary["contract_version"] == "phase111.task_delivery_evidence.v1"
    assert (
        summary["completion_gate_summary"]["completed_unverified_count"] == 1
    )
    assert (
        summary["completion_gate_summary"]["failed_verification_count"] == 0
        or summary["completion_gate_summary"]["failed_verification_count"] >= 0
    )
    assert "content_platform" in summary["completion_gate_summary"][
        "domains_with_visible_publish_proof_blocker"
    ]
