from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase100_mail_draft_task_returns_typed_office_result(client: TestClient) -> None:
    created = client.post(
        "/api/tasks",
        json={
            "goal": "Prepare a customer follow-up email draft",
            "office_request": {
                "request_type": "mail",
                "operation": "draft",
                "title": "Q4 renewal follow-up",
                "summary": "Share the proposal update and ask for a review slot.",
                "content": "Thanks for the call. Attached is the proposal delta and next-step summary.",
                "recipients": ["customer@example.com"],
            },
            "auto_start": True,
        },
    ).json()

    detail = client.get(f"/api/tasks/{created['task_id']}").json()

    assert created["status"] == "completed"
    assert detail["mode"] == "agent"
    assert detail["preflight"]["office_productivity"]["enabled"] is True
    assert detail["result"]["deliverable"]["deliverable_type"] == "mail"
    assert detail["result"]["approval_state"]["status"] == "not_required"
    assert detail["result"]["office_productivity"]["typed_output"]["mail_draft"]["subject"] == (
        "Q4 renewal follow-up"
    )
    assert detail["artifact_count"] >= 1


def test_phase100_mail_send_task_waits_for_approval_and_resumes(client: TestClient) -> None:
    created = client.post(
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

    waiting = client.get(f"/api/tasks/{created['task_id']}").json()
    approved = client.post(
        f"/api/approvals/{created['current_approval_id']}/approve",
        json={"reason": "phase100"},
    ).json()
    final_detail = client.get(f"/api/tasks/{created['task_id']}").json()

    assert created["status"] == "waiting_approval"
    assert waiting["result"]["approval_state"]["status"] == "required"
    assert waiting["result"]["deliverable"]["metadata"]["task_status"] == "waiting_approval"
    assert approved["status"] == "completed"
    assert final_detail["result"]["approval_state"]["status"] == "approved"
    assert final_detail["result"]["deliverable"]["deliverable_type"] == "mail"
    assert (
        final_detail["result"]["office_productivity"]["typed_output"]["mail_draft"]["operation"]
        == "send"
    )


def test_phase100_calendar_plan_task_emits_unified_office_evidence(client: TestClient) -> None:
    created = client.post(
        "/api/tasks",
        json={
            "goal": "Plan the monthly operating review meeting",
            "office_request": {
                "request_type": "calendar",
                "operation": "plan",
                "title": "Monthly operating review",
                "summary": "Prepare a meeting plan for the monthly operating review.",
                "attendees": ["ops@example.com", "finance@example.com"],
                "scheduled_time": "2025-08-15T16:00:00Z",
            },
            "auto_start": True,
        },
    ).json()

    detail = client.get(f"/api/tasks/{created['task_id']}").json()

    assert created["status"] == "completed"
    assert detail["result"]["deliverable"]["deliverable_type"] == "calendar"
    assert detail["result"]["artifact_evidence"]["evidence_type"] == "calendar"
    assert detail["result"]["office_productivity"]["typed_output"]["calendar_action"][
        "scheduled_time"
    ] == "2025-08-15T16:00:00Z"
