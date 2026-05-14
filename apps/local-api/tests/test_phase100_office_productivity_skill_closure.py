from __future__ import annotations

import importlib.util

import pytest
from fastapi.testclient import TestClient

OFFICE_DEPS_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ["docx", "openpyxl", "pptx"]
)


def test_phase100_productivity_mail_send_replay_exposes_domain_evidence(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/tasks",
        json={
            "goal": "Send the signed contract summary to the customer",
            "domain": "productivity",
            "domain_request": {
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

    assert created["status"] == "waiting_approval"
    assert created["result"]["domain"] == "productivity"
    assert created["result"]["request_type"] == "mail"
    assert created["result"]["status"] == "waiting_input"
    assert (
        created["result"]["provider_capability_profile"]["provider_ref"]
        == "local.office_suite"
    )

    replay = client.get(f"/api/tasks/{created['task_id']}/replay").json()
    assert replay["domain"] == "productivity"
    assert replay["domain_request"]["request_type"] == "mail"
    assert replay["domain_evidence"]["approval_state"]["status"] == "required"
    assert replay["agent_loop"]["domain"] == "productivity"
    assert replay["agent_loop"]["iterations"]
    assert (
        replay["agent_loop"]["iterations"][-1]["evidence"]["artifact_evidence"]["evidence_type"]
        == "mail"
    )
    assert replay["agent_loop"]["iterations"][-1]["approval"]["status"] == "required"

    approved = client.post(
        f"/api/approvals/{created['current_approval_id']}/approve",
        json={"reason": "phase100"},
    ).json()
    assert approved["status"] == "completed"

    final_detail = client.get(f"/api/tasks/{created['task_id']}").json()
    assert final_detail["result"]["status"] == "completed"
    assert final_detail["result"]["approval_state"]["status"] == "approved"


@pytest.mark.skipif(
    not OFFICE_DEPS_AVAILABLE,
    reason="office python dependencies are not installed",
)
@pytest.mark.parametrize(
    ("package_ref", "tool_name", "goal", "office_request", "typed_key"),
    [
        (
            "official/office/word-report",
            "office.word.generate",
            "Generate a project update document",
            {
                "request_type": "document",
                "operation": "generate",
                "title": "Project update",
                "summary": "Summarize the current status and next steps.",
                "content": "Current status and next steps.",
                "metadata": {
                    "sections": [
                        {"title": "Status"},
                        {"title": "Next Steps"},
                    ]
                },
            },
            "document_change_set",
        ),
        (
            "official/office/excel-analysis-workbook",
            "office.excel.generate",
            "Generate a spreadsheet summary",
            {
                "request_type": "spreadsheet",
                "operation": "generate",
                "title": "Sales summary",
                "summary": "Summarize the latest sales structure.",
                "content": "Monthly sales summary.",
                "metadata": {
                    "sheets": [
                        {"name": "Summary"},
                        {"name": "Trends"},
                    ]
                },
            },
            "sheet_update_summary",
        ),
        (
            "official/office/ppt-briefing",
            "office.ppt.generate",
            "Generate a deck summary",
            {
                "request_type": "deck",
                "operation": "generate",
                "title": "Board briefing",
                "summary": "Summarize the board briefing structure.",
                "content": "Board briefing outline.",
                "metadata": {
                    "slides": [
                        {"title": "Context"},
                        {"title": "Decision"},
                    ]
                },
            },
            "deck_outline",
        ),
    ],
)
def test_phase100_productivity_skill_closure_keeps_generic_result_layer(
    client: TestClient,
    package_ref: str,
    tool_name: str,
    goal: str,
    office_request: dict[str, object],
    typed_key: str,
) -> None:
    install = client.post(
        "/api/skills/install",
        json={"source_type": "repository_ref", "source_uri": f"clawhub:{package_ref}"},
    ).json()
    bundle_id = install["bundle"]["bundle_id"]
    skill_id = install["skills"][0]["skill_id"]
    assert client.post(
        f"/api/plugins/{bundle_id}/enable",
        json={"actor_member_id": "mem_xiaoyao"},
    ).status_code == 200
    assert client.post(
        f"/api/skills/{skill_id}/grants",
        json={"allowed_tools": [tool_name]},
    ).status_code == 200

    created = client.post(
        "/api/tasks",
        json={
            "goal": goal,
            "domain": "productivity",
            "domain_request": office_request,
            "office_request": office_request,
            "auto_start": True,
        },
    ).json()

    assert created["status"] == "completed"
    assert created["result"]["domain"] == "productivity"
    assert created["result"]["deliverable"]["deliverable_type"] == office_request["request_type"]
    assert created["result"]["office_productivity"]["typed_output"][typed_key]
    replay = client.get(f"/api/tasks/{created['task_id']}/replay").json()
    assert replay["domain_evidence"]["deliverable"]["deliverable_type"] == office_request["request_type"]
    assert replay["domain_evidence"]["artifact_evidence"]["evidence_type"] == office_request["request_type"]


def test_phase100_productivity_provider_unavailable_returns_failed_domain_result(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/tasks",
        json={
            "goal": "Draft a customer follow-up email",
            "domain": "productivity",
            "domain_request": {
                "request_type": "mail",
                "operation": "draft",
                "provider_ref": "remote.unavailable_suite",
                "title": "Follow-up",
                "summary": "Draft a follow-up.",
                "content": "Thanks again for the meeting.",
                "recipients": ["customer@example.com"],
            },
            "auto_start": True,
        },
    ).json()

    detail = client.get(f"/api/tasks/{created['task_id']}").json()
    assert detail["status"] == "failed"
    assert detail["result"]["domain"] == "productivity"
    assert detail["result"]["status"] == "failed"
    assert (
        detail["result"]["provider_capability_profile"]["provider_ref"]
        == "remote.unavailable_suite"
    )
