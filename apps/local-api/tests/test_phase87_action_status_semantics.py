from __future__ import annotations

from core_types import ResponsePlan
from fastapi.testclient import TestClient

from app.services.chat_response import ChatResponseCoordinator


def test_phase87_finalize_promotes_canonical_action_status_and_evidence() -> None:
    coordinator = ChatResponseCoordinator()
    plan = ResponsePlan(
        plain_text="已拿到网页结果。",
        structured_payload={
            "tool_result_context": {
                "status": "completed",
                "tool_name": "browser.snapshot",
                "tool_call_id": "call_phase87_browser",
                "evidence_refs": [{"browser_evidence_id": "bev_phase87"}],
                "approval_state": {"status": "not_required", "approval_id": None},
            }
        },
    )

    finalized = coordinator.finalize_plan(plan, "已拿到网页结果。")

    assert finalized.structured_payload["action_status_semantics"]["status"] == "completed_with_evidence"
    assert finalized.structured_payload["tool_result_context"]["status"] == "completed_with_evidence"
    assert finalized.tool_status_semantics["status"] == "completed_with_evidence"


def test_phase87_finalize_downgrades_completion_without_evidence() -> None:
    coordinator = ChatResponseCoordinator()
    plan = ResponsePlan(
        plain_text="这一步已经完成。",
        structured_payload={
            "task_status_semantics": {
                "status": "completed",
                "task_id": "task_phase87_no_evidence",
            }
        },
    )

    finalized = coordinator.finalize_plan(plan, "这一步已经完成。")

    assert finalized.structured_payload["action_status_semantics"]["status"] in {"planned", "executing"}
    assert finalized.task_status_semantics["status"] in {"planned", "executing"}


def test_phase87_readiness_reports_action_state_semantics_ready(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase87 = readiness["phase_readiness"]["phase87_action_state_semantics"]

    assert phase87["status"] == "ready"
    assert phase87["details"]["canonical_action_state_owner"] == "action_status_semantics"
    assert phase87["details"]["completed_evidence_gate"] == "completed_with_evidence"
