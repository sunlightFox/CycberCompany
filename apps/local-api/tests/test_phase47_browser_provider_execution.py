from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs

import pytest
from app.services.browser_executor import (
    BrowserExecutionRequest,
    BrowserExecutionResult,
    BrowserExecutor,
)
from core_types import RiskLevel
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_phase47_browser_executor_dom_artifacts_and_revoke(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _TestSite() as site:
        profile = _create_profile(client, allowed_domains=["127.0.0.1"])
        asset = _create_browser_session_asset(client)
        session = _create_session(client, profile["browser_profile_id"], asset["asset_id"])
        for action in ["read", "interact", "capture", "download"]:
            _grant_asset(client, asset["asset_id"], action, RiskLevel.R2)
        handle = _query_handle(client, asset["asset_id"], "interact")
        download_handle = _query_handle(client, asset["asset_id"], "download")
        capture_handle = _query_handle(client, asset["asset_id"], "capture")
        task = _create_task(client)

        filled = client.post(
            "/api/tools/execute",
            json={
                "task_id": task["task_id"],
                "tool_name": "browser.fill",
                "args": {
                    "url": site.url("/form"),
                    "selector": "#message",
                    "value": "phase47 hello",
                    "session_handle_id": handle["handle_id"],
                },
            },
        )
        clicked = client.post(
            "/api/tools/execute",
            json={
                "task_id": task["task_id"],
                "tool_name": "browser.click",
                "args": {
                    "url": site.url("/form"),
                    "selector": "#go",
                    "session_handle_id": handle["handle_id"],
                },
            },
        )
        submit_payload = {
            "task_id": task["task_id"],
            "tool_name": "browser.submit",
            "args": {
                "url": site.url("/form"),
                "selector": "#phase47-form",
                "session_handle_id": handle["handle_id"],
            },
        }
        pending_submit = client.post("/api/tools/execute", json=submit_payload).json()
        client.post(
            f"/api/approvals/{pending_submit['approval']['approval_id']}/approve",
            json={"reason": "phase47 submit"},
        )
        submitted = client.post(
            "/api/tools/execute",
            json={**submit_payload, "approval_id": pending_submit["approval"]["approval_id"]},
        )
        screenshot_payload = {
            "task_id": task["task_id"],
            "tool_name": "browser.screenshot",
            "args": {"url": site.url("/form"), "session_handle_id": capture_handle["handle_id"]},
        }
        pending_screenshot = client.post("/api/tools/execute", json=screenshot_payload).json()
        client.post(
            f"/api/approvals/{pending_screenshot['approval']['approval_id']}/approve",
            json={"reason": "phase47 screenshot"},
        )
        screenshot = client.post(
            "/api/tools/execute",
            json={
                **screenshot_payload,
                "approval_id": pending_screenshot["approval"]["approval_id"],
            },
        )
        download_payload = {
            "task_id": task["task_id"],
            "tool_name": "browser.download",
            "args": {
                "url": site.url("/download.csv"),
                "display_name": "phase47.csv",
                "session_handle_id": download_handle["handle_id"],
            },
        }
        pending_download = client.post("/api/tools/execute", json=download_payload).json()
        client.post(
            f"/api/approvals/{pending_download['approval']['approval_id']}/approve",
            json={"reason": "phase47 download"},
        )
        download = client.post(
            "/api/tools/execute",
            json={**download_payload, "approval_id": pending_download["approval"]["approval_id"]},
        )
        revoked = client.post(
            f"/api/browser/profiles/{profile['browser_profile_id']}/revoke",
            json={"reason": "phase47 revoke"},
        )
        blocked_after_revoke = client.post(
            "/api/tools/execute",
            json={
                "task_id": task["task_id"],
                "tool_name": "browser.snapshot",
                "args": {
                    "url": site.url("/form"),
                    "session_handle_id": handle["handle_id"],
                },
            },
        )

    assert session["browser_session_id"]
    assert filled.status_code == 200, filled.text
    filled_result = filled.json()["result"]
    assert filled_result["action_status"] == "completed"
    assert filled_result["interaction"]["dom_interaction_executed"] is True
    assert filled_result["backend"] in {"http_fallback", "playwright"}
    assert clicked.status_code == 200, clicked.text
    assert clicked.json()["result"]["interaction"]["navigated"] is True
    assert submitted.status_code == 200, submitted.text
    submitted_result = submitted.json()["result"]
    assert submitted_result["action_status"] == "completed"
    assert "phase47 hello" in submitted_result["snapshot"]
    assert screenshot.status_code == 200, screenshot.text
    assert screenshot.json()["result"]["artifact"]["artifact_type"] == "screenshot"
    assert download.status_code == 200, download.text
    assert download.json()["result"]["download"]["artifact_type"] == "download"
    assert download.json()["result"]["browser_evidence"]["download_artifact_id"]
    assert revoked.status_code == 200, revoked.text
    assert blocked_after_revoke.status_code in {400, 403}
    assert blocked_after_revoke.json()["error"]["code"] == "ASSET_HANDLE_INVALID"
    assert _payload_leakage_count(
        {
            "filled": filled_result,
            "submitted": submitted_result,
            "screenshot": screenshot.json(),
            "download": download.json(),
            "blocked": blocked_after_revoke.json(),
        }
    ) == 0


@pytest.mark.asyncio
async def test_phase47_browser_executor_auto_fallback_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "auto")
    executor = BrowserExecutor()
    fake_playwright = _UnavailablePlaywright()
    cast(Any, executor)._playwright = fake_playwright

    with _TestSite() as site:
        first = await executor.execute(
            BrowserExecutionRequest(action="snapshot", url=site.url("/form"))
        )
        second = await executor.execute(
            BrowserExecutionRequest(action="snapshot", url=site.url("/form"))
        )

    assert first.backend == "http_fallback"
    assert first.fallback_chain == ["playwright_unavailable", "http_fallback"]
    assert first.degraded_reason == "phase47_playwright_missing"
    assert second.backend == "http_fallback"
    assert second.fallback_chain == ["playwright_skipped", "http_fallback"]
    assert second.degraded_reason == "phase47_playwright_missing"
    assert fake_playwright.calls == 1


def test_phase47_external_platform_provider_registry_and_execution_modes(
    client: TestClient,
) -> None:
    providers = client.get("/api/external-platform/providers")
    assert providers.status_code == 200, providers.text
    provider_payload = providers.json()
    provider_keys = {item["provider_key"] for item in provider_payload["items"]}
    assert {"fake_provider", "browser"}.issubset(provider_keys)
    assert all(
        item["metadata"].get("secret_material_visible") is not True
        for item in provider_payload["items"]
    )

    account = _create_account(client, display_name="Phase47 account")
    _grant_asset(client, account["asset_id"], "publish_content", RiskLevel.R4)
    intent = _resolve(
        client,
        "请在某平台发布文章，内容：Phase47 provider registry 验收。",
    )["intent"]

    fake_plan = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent["intent_id"], "execution_mode": "fake_provider"},
    ).json()
    client.post(
        f"/api/approvals/{fake_plan['plan']['approval_id']}/approve",
        json={"reason": "phase47 fake provider"},
    )
    fake_executed = client.post(
        f"/api/external-platform/action-plans/{fake_plan['plan']['plan_id']}/execute"
    )

    browser_intent = _resolve(
        client,
        "请在某平台发布文章，内容：Phase47 browser provider boundary。",
    )["intent"]
    browser_plan = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": browser_intent["intent_id"], "execution_mode": "browser"},
    ).json()
    client.post(
        f"/api/approvals/{browser_plan['plan']['approval_id']}/approve",
        json={"reason": "phase47 browser provider"},
    )
    browser_executed = client.post(
        f"/api/external-platform/action-plans/{browser_plan['plan']['plan_id']}/execute"
    )
    unknown_intent = _resolve(
        client,
        "请在某平台发布文章，内容：Phase47 unknown provider fail closed。",
    )["intent"]
    unknown_plan = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": unknown_intent["intent_id"], "execution_mode": "fake_provider"},
    ).json()
    client.post(
        f"/api/approvals/{unknown_plan['plan']['approval_id']}/approve",
        json={"reason": "phase47 unknown provider"},
    )
    unknown_executed = client.post(
        f"/api/external-platform/action-plans/{unknown_plan['plan']['plan_id']}/execute",
        json={"executor": "missing_provider"},
    )
    source_text = (
        ROOT_DIR / "apps/local-api/app/services/external_platform_actions.py"
    ).read_text(encoding="utf-8")

    assert fake_executed.status_code == 200, fake_executed.text
    fake_payload = fake_executed.json()
    assert fake_payload["plan"]["status"] == "completed"
    assert fake_payload["plan"]["evidence"]["provider_result"]["provider_module"] == (
        "FakeExternalPlatformProvider"
    )
    assert fake_payload["plan"]["evidence"]["provider_registry"]["provider_key"] == "fake_provider"
    assert browser_executed.status_code == 200, browser_executed.text
    browser_payload = browser_executed.json()
    assert browser_payload["plan"]["status"] == "failed"
    assert browser_payload["plan"]["failure_reason"] == "browser_provider_not_configured"
    assert browser_payload["executions"][0]["executor"] == "browser"
    assert unknown_executed.status_code == 200, unknown_executed.text
    unknown_payload = unknown_executed.json()
    assert unknown_payload["plan"]["status"] == "failed"
    assert unknown_payload["plan"]["failure_reason"] == "provider_not_registered"
    assert unknown_payload["executions"][0]["executor"] == "missing_provider"
    assert unknown_payload["executions"][0]["error_code"] == (
        "EXTERNAL_PLATFORM_PROVIDER_NOT_REGISTERED"
    )
    assert "async def _run_fake_provider" not in source_text
    assert _payload_leakage_count(
        {
            "providers": provider_payload,
            "fake": fake_payload,
            "browser": browser_payload,
            "unknown": unknown_payload,
        }
    ) == 0


def test_phase47_release_contracts_eval_report_and_diagnostic(client: TestClient) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase47")
    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    registry = cast(Any, client.app).state.registry

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase47_browser_provider_execution"},
    )
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))

    assert migration_contract["required_migration"] == "031_media_runtime.sql"
    assert "suite_phase47_browser_provider_execution" in {item["suite_id"] for item in suites}
    for module in [
        "BrowserExecutor",
        "PlaywrightBrowserExecutor",
        "BrowserContextLifecycle",
        "BrowserDomInteractionEvidence",
        "BrowserStorageStateRedaction",
        "ExternalPlatformProviderRegistry",
        "FakeExternalPlatformProviderModule",
        "ExternalPlatformExecutionModeRouter",
    ]:
        assert by_name[module]["status"] in {"implemented", "implemented_with_fallback"}
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 10
    assert completed["status"] == "ready_for_release"
    phase47 = report["summary"]["phase47"]
    assert phase47["suite_id"] == "suite_phase47_browser_provider_execution"
    assert phase47["registered_cases"] == 10
    assert phase47["provider_registry"]["fake_provider_registered"] is True
    assert phase47["browser_executor"]["fallback_supported"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase47"]["registered"] is True
    assert any(item["source_type"] == "phase47_browser_provider_execution" for item in evidence)
    assert "phase47_browser_provider_execution" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_profile(
    client: TestClient,
    *,
    allowed_domains: list[str],
) -> dict[str, Any]:
    response = client.post(
        "/api/browser/profiles",
        json={
            "display_name": "Phase47 profile",
            "profile_type": "task_isolated",
            "sensitivity": "medium",
            "allowed_domains": allowed_domains,
            "policy": {"download_quarantine": True},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_browser_session_asset(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": "Phase47 browser session",
            "provider": "browser_session",
            "sensitivity": "high",
            "secret_value": "phase47-cookie-value",
            "config": {
                "platform": "phase47",
                "username": "browser-user",
                "auth_type": "cookie_session",
                "login_domain": "127.0.0.1",
            },
            "summary_text": "Phase47 browser session asset",
            "capabilities": ["read", "download", "interact", "capture"],
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_session(client: TestClient, profile_id: str, asset_id: str) -> dict[str, Any]:
    response = client.post(
        f"/api/browser/profiles/{profile_id}/sessions",
        json={
            "asset_id": asset_id,
            "login_domain": "127.0.0.1",
            "auth_type": "cookie_session",
            "sensitivity": "high",
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_account(client: TestClient, *, display_name: str) -> dict[str, Any]:
    response = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": display_name,
            "provider": "fake_platform",
            "sensitivity": "high",
            "config": {
                "platform": "fake_platform",
                "username": display_name,
                "auth_type": "token",
            },
            "secret_value": "token=phase47-secret-token",
            "owner_scope_type": "member",
            "owner_scope_id": "mem_xiaoyao",
            "visibility": "private",
            "risk_level": "R4",
            "summary_text": f"{display_name} external platform account",
            "capabilities": ["login", "publish_content", "publish_post"],
            "metadata": {"platform": "fake_platform", "label": display_name},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _grant_asset(
    client: TestClient,
    asset_id: str,
    action: str,
    risk: RiskLevel,
) -> dict[str, Any]:
    response = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": action,
            "effect": "allow",
            "risk_level": risk.value,
            "source_type": "phase47_test",
            "source_id": asset_id,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _query_handle(client: TestClient, asset_id: str, action: str) -> dict[str, Any]:
    response = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": [action],
            "keywords": ["phase47"],
        },
    )
    assert response.status_code == 200, response.text
    handles = [item for item in response.json()["handles"] if item["asset_id"] == asset_id]
    assert handles
    return dict(handles[0])


def _create_task(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/tasks",
        json={"goal": "phase47 browser execution", "mode_hint": "workflow", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _resolve(client: TestClient, text: str) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": text, "member_id": "mem_xiaoyao"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "phase47-cookie-value",
        "phase47-secret-token",
        "token=phase47",
        "cookie=phase47",
        "private_key=phase47",
        "mnemonic=phase47",
        "c:\\users\\administrator\\phase47",
    ]
    return sum(1 for item in forbidden if item in serialized)


class _UnavailablePlaywright:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: BrowserExecutionRequest) -> BrowserExecutionResult:
        self.calls += 1
        return BrowserExecutionResult(
            action=request.action,
            url=request.url,
            action_status="degraded",
            backend="playwright",
            backend_status="unavailable",
            evidence_summary="phase47 fake unavailable",
            recoverable=True,
            fallback_chain=["playwright_unavailable"],
            degraded_reason="phase47_playwright_missing",
        )


class _TestSite:
    def __enter__(self) -> _TestSite:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._server.server_port}{path}"


class _TestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/download.csv"):
            body = b"name,value\nphase47,ok\n"
            self._send(200, body, "text/csv")
            return
        if self.path.startswith("/clicked"):
            self._send_html("<html><title>Clicked</title><body>phase47 clicked</body></html>")
            return
        self._send_html(
            """
            <html>
              <head><title>Phase47 Form</title></head>
              <body>
                <a id="go" href="/clicked">go</a>
                <form id="phase47-form" method="post" action="/submitted">
                  <input id="message" name="message" value="">
                  <button id="submit" type="submit">submit</button>
                </form>
              </body>
            </html>
            """
        )

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length).decode("utf-8")
        data = parse_qs(body)
        message = data.get("message", [""])[0]
        self._send_html(
            f"<html><title>Submitted</title><body>submitted:{message}</body></html>"
        )

    def _send_html(self, html: str) -> None:
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return
