from __future__ import annotations

import anyio
from fastapi.testclient import TestClient

from core_types import RiskLevel

from app.services.browser_policy import (
    browser_action_policy,
    browser_backend_capabilities,
    browser_session_preflight,
)


def test_phase50_browser_action_policy_matrix_core_actions() -> None:
    open_policy = browser_action_policy("browser.open")
    submit_policy = browser_action_policy("browser.submit")
    download_policy = browser_action_policy("browser.download")
    upload_policy = browser_action_policy("browser.upload")

    assert open_policy.category == "browser_read"
    assert open_policy.default_risk_level == RiskLevel.R2
    assert submit_policy.category == "browser_submit"
    assert submit_policy.default_risk_level == RiskLevel.R5
    assert set(submit_policy.required_controls) == {"approval", "strong_approval"}
    assert download_policy.category == "browser_download"
    assert download_policy.workflow_bypass_allowed is True
    assert upload_policy.category == "browser_upload"
    assert "file_upload" in upload_policy.backend_capabilities


def test_phase50_browser_backend_capability_matrix() -> None:
    fallback = browser_backend_capabilities("http_fallback")
    local_cdp = browser_backend_capabilities("local_cdp")

    assert fallback["dom_snapshot"] is True
    assert fallback["file_upload"] is False
    assert fallback["persistent_identity"] is False
    assert local_cdp["challenge_recovery"] is True
    assert local_cdp["persistent_identity"] is True


def test_phase50_browser_session_preflight_matrix() -> None:
    active = browser_session_preflight(
        session_status="active",
        health_status="ready",
        login_state="authenticated",
        execution_backend="local_cdp",
        identity_binding_status="bound",
        login_capture_mode="manual_handoff",
    )
    login_required = browser_session_preflight(
        session_status="degraded",
        health_status="login_required",
        login_state="login_required",
        execution_backend="playwright",
        identity_binding_status="bound",
        login_capture_mode="manual_handoff",
    )

    assert active["session_state"] == "active"
    assert active["login_reuse_allowed"] is True
    assert login_required["session_state"] == "login_required"
    assert login_required["recovery_allowed"] is True
    assert login_required["login_reuse_allowed"] is False


def test_phase50_boundary_policy_snapshot_uses_browser_policy_matrix(
    client: TestClient,
) -> None:
    registry = client.app.state.registry
    task = client.post(
        "/api/tasks",
        json={"goal": "phase50 browser policy boundary", "auto_start": False},
    ).json()

    async def _decide() -> object:
        return await registry.execution_boundary_service.decide_tool_action(
            organization_id="org_default",
            tool_name="browser.download",
            source="builtin",
            requested_risk_level=RiskLevel.R3,
            args={"url": "https://example.com/file.csv"},
            task_id=task["task_id"],
            member_id="mem_xiaoyao",
            tool_call_id=None,
            trace_id=None,
        )

    decision = anyio.run(_decide)
    snapshot = decision.policy_snapshot["browser_action_policy"]
    expected = browser_action_policy("browser.download").as_dict()

    assert decision.action_category == expected["category"]
    assert snapshot["action"] == expected["action"]
    assert snapshot["risk_level"] == expected["risk_level"]
    assert set(decision.required_controls) >= set(expected["required_controls"])
