from __future__ import annotations

import json
from typing import Any, cast

import pytest
from app.services.browser_executor import BrowserExecutionResult
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase55_session_health_restore_and_page_state_are_redacted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(request: Any) -> BrowserExecutionResult:
        return BrowserExecutionResult(
            action=request.action,
            url=request.url,
            action_status="completed",
            backend="fake",
            backend_status="available",
            evidence_summary=f"{request.action} ran on {request.url}",
            title="Phase55 Page",
            http_status=200,
            snapshot="<html><body><button id='go'>Go</button></body></html>",
            content_preview="<html><body><button id='go'>Go</button></body></html>",
            network_summary={"request_count": 1, "events": [{"url": request.url}]},
            console_summary={"error_count": 0, "events": [{"text": "token=phase55-console"}]},
            recoverable=False,
            selector=request.selector,
        )

    registry = cast(Any, client.app).state.registry
    monkeypatch.setattr(registry.tool_runtime._browser_executor, "execute", fake_execute)

    profile = _create_profile(client)
    asset = _create_browser_session_asset(client)
    session = _create_session(client, profile["browser_profile_id"], asset["asset_id"])
    _grant(client, asset["asset_id"], "read")
    handle = _query_handle(client, asset["asset_id"], "read")

    health = client.post(
        f"/api/browser/sessions/{session['browser_session_id']}/health-check",
        json={
            "probe_type": "manual",
            "observed_status": "healthy",
            "provider_status": "available",
            "failure_reason": None,
            "recovery_hint": None,
            "evidence": {
                "set-cookie": "phase55-cookie-value",
                "localStorage": {"token": "phase55-storage-token"},
            },
        },
    )
    assert health.status_code == 200, health.text
    health_payload = health.json()
    assert health_payload["probe"]["health_status"] == "healthy"
    assert health_payload["browser_session"]["login_state"] == "authenticated"

    task = _create_task(client)
    opened = client.post(
        "/api/tools/execute",
        json={
                "task_id": task["task_id"],
                "tool_name": "browser.open",
                "args": {
                    "url": "https://example.com/login",
                    "session_handle_id": handle["handle_id"],
                },
            },
        )
    assert opened.status_code == 200, opened.text
    result = opened.json()["result"]
    page_state_id = result["browser_page_state"]["page_state_id"]

    page_states = client.get(
        f"/api/browser/sessions/{session['browser_session_id']}/page-states"
    ).json()["items"]
    restore = client.post(
        f"/api/browser/sessions/{session['browser_session_id']}/restore-context",
            json={
                "task_id": task["task_id"],
                "current_url": "https://example.com/login?token=phase55-url-token",
                "requested_action": "browser.open",
            },
    )

    serialized = json.dumps(
        {"health": health_payload, "result": result, "page_states": page_states, "restore": restore.json()},
        ensure_ascii=False,
    )
    assert page_states[0]["page_state_id"] == page_state_id
    assert page_states[0]["browser_evidence_id"] == result["browser_evidence_id"]
    assert page_states[0]["dom_summary"]["has_snapshot"] is True
    assert page_states[0]["redaction_summary"]["cookie_redacted"] is True
    assert restore.status_code == 200, restore.text
    assert restore.json()["context"]["recoverable"] is False
    assert "phase55-cookie-value" not in serialized
    assert "phase55-storage-token" not in serialized
    assert "phase55-url-token" not in serialized
    assert "phase55-console" not in serialized


def test_phase55_session_health_fail_closed_for_reuse_and_expiry(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_execute(request: Any) -> BrowserExecutionResult:
        return BrowserExecutionResult(
            action=request.action,
            url=request.url,
            action_status="completed",
            backend="fake",
            backend_status="available",
            evidence_summary="ok",
            snapshot="<html><body>ok</body></html>",
            recoverable=False,
        )

    registry = cast(Any, client.app).state.registry
    monkeypatch.setattr(registry.tool_runtime._browser_executor, "execute", fake_execute)

    profile = _create_profile(client)
    asset = _create_browser_session_asset(client)
    session = _create_session(client, profile["browser_profile_id"], asset["asset_id"])
    _grant(client, asset["asset_id"], "read")
    handle = _query_handle(client, asset["asset_id"], "read")

    login_required = client.post(
        f"/api/browser/sessions/{session['browser_session_id']}/health-check",
        json={"probe_type": "manual", "observed_status": "login_required"},
    )
    assert login_required.status_code == 200, login_required.text
    blocked = client.post(
        "/api/tools/execute",
        json={
            "task_id": _create_task(client)["task_id"],
            "tool_name": "browser.open",
            "args": {
                "url": "https://example.com",
                "session_handle_id": handle["handle_id"],
            },
        },
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "LOGIN_REQUIRED"

    expired_profile = _create_profile(client)
    expired_asset = _create_browser_session_asset(client)
    expired_session = _create_session(
        client, expired_profile["browser_profile_id"], expired_asset["asset_id"]
    )
    expired = client.post(
        f"/api/browser/sessions/{expired_session['browser_session_id']}/health-check",
        json={"probe_type": "manual", "observed_status": "session_expired"},
    )
    assert expired.status_code == 200, expired.text
    expired_blocked = client.post(
        "/api/tools/execute",
        json={
            "task_id": _create_task(client)["task_id"],
            "tool_name": "browser.open",
            "args": {
                "url": "https://example.com",
                "browser_session_id": expired_session["browser_session_id"],
                "member_id": "mem_xiaoyao",
            },
        },
    )
    assert expired_blocked.status_code == 409
    assert expired_blocked.json()["error"]["code"] == "SESSION_EXPIRED"


def test_phase55_release_contracts_and_eval_suite(client: TestClient) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase55")
    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    diagnostic = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()

    assert migration_contract["required_migration"] == "040_browser_session_persistence_deepening.sql"
    assert "suite_phase55_browser_session_persistence" in {item["suite_id"] for item in suites}
    assert by_name["BrowserSessionHealthProbe"]["status"] == "implemented"
    assert by_name["BrowserPageStateReplay"]["status"] == "implemented"
    assert completed["status"] == "ready_for_release"
    assert report["summary"]["phase55_browser_session_persistence"]["suite_id"] == (
        "suite_phase55_browser_session_persistence"
    )
    assert report["summary"]["phase23"]["capability_scores"]["phase55"]["registered"] is True
    assert any(
        item["source_type"] == "phase55_browser_session_persistence"
        for item in diagnostic["items"]
    )


def _create_profile(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/browser/profiles",
        json={
            "display_name": "Phase55 profile",
            "profile_type": "task_isolated",
            "sensitivity": "medium",
            "allowed_domains": ["example.com"],
            "policy": {"download_quarantine": True},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_browser_session_asset(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": "Phase55 browser session",
            "provider": "browser_session",
            "sensitivity": "high",
            "secret_value": "phase55-cookie-value",
            "config": {
                "platform": "phase55",
                "username": "browser-user",
                "auth_type": "cookie_session",
                "login_domain": "example.com",
            },
            "summary_text": "Phase55 browser session asset",
            "capabilities": ["read", "download", "interact", "capture"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_session(client: TestClient, profile_id: str, asset_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/browser/profiles/{profile_id}/sessions",
        json={
            "asset_id": asset_id,
            "login_domain": "example.com",
            "auth_type": "cookie_session",
            "sensitivity": "high",
            "reuse_policy": {"cross_task_reuse": True},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _grant(client: TestClient, asset_id: str, action: str) -> None:
    response = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": action,
            "effect": "allow",
        },
    )
    assert response.status_code == 200, response.text


def _query_handle(client: TestClient, asset_id: str, action: str) -> dict[str, Any]:
    response = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": [action],
            "keywords": ["phase55"],
        },
    )
    assert response.status_code == 200, response.text
    handles = [item for item in response.json()["handles"] if item["asset_id"] == asset_id]
    assert handles
    return handles[0]


def _create_task(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/tasks",
        json={"goal": "phase55 browser evidence", "mode_hint": "workflow", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return response.json()
