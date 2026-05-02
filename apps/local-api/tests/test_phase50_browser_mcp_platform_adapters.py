from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs

from core_types import RiskLevel
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase50_migration_adapter_registry_and_secret_manifest_deny(
    client: TestClient,
) -> None:
    contract = assert_phase_migration_contract(client, "phase50")
    assert contract["required_migration"] == "032_external_platform_adapters.sql"

    adapter = _register_browser_adapter(
        client,
        platform_key="phase50_registry",
        start_url="http://127.0.0.1:1/publish",
    )
    repeated = _register_browser_adapter(
        client,
        platform_key="phase50_registry",
        start_url="http://127.0.0.1:1/publish",
    )
    assert repeated["adapter"]["adapter_id"] == adapter["adapter"]["adapter_id"]

    listed = client.get(
        "/api/external-platform/adapters",
        params={"platform_key": "phase50_registry"},
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1

    validation = client.post(
        f"/api/external-platform/adapters/{adapter['adapter']['adapter_id']}/validate"
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True

    denied = client.post(
        "/api/external-platform/adapters",
        json={
            "platform_key": "phase50_registry",
            "adapter_type": "browser",
            "action_type": "publish_content",
            "display_name": "secret adapter",
            "allowed_domains": ["127.0.0.1"],
            "manifest": {
                "start_url": "http://127.0.0.1:1/publish",
                "selectors": {"submit": "#form"},
                "token": "phase50-raw-token",
            },
        },
    )
    assert denied.status_code == 422, denied.text
    assert denied.json()["error"]["details"]["issues"][0]["code"] == "inline_secret_key_denied"
    assert _payload_leakage_count({"adapter": adapter, "denied": denied.json()}) == 0


def test_phase50_browser_adapter_compile_approval_execute_and_verify(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _TestSite() as site:
        plan = _create_publish_plan(
            client,
            platform_key="phase50_browser",
            execution_mode="browser",
        )
        adapter = _register_browser_adapter(
            client,
            platform_key="phase50_browser",
            start_url=site.url("/publish"),
        )["adapter"]

        compiled = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/compile",
            json={"adapter_id": adapter["adapter_id"]},
        )
        assert compiled.status_code == 200, compiled.text
        compiled_payload = compiled.json()
        submit_steps = [
            item for item in compiled_payload["steps"] if item["step_name"] == "submit_publish"
        ]
        assert len(submit_steps) == 1
        assert submit_steps[0]["requires_approval"] is True
        assert submit_steps[0]["approval_id"] == plan["approval_id"]
        assert submit_steps[0]["risk_level"] == "R5"

        pending = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={"adapter_id": adapter["adapter_id"]},
        )
        assert pending.status_code == 200, pending.text
        pending_payload = pending.json()
        assert pending_payload["execution"]["status"] == "awaiting_approval"
        assert any(item["status"] == "awaiting_approval" for item in pending_payload["steps"])
        assert site.submissions == []

        approved = client.post(
            f"/api/approvals/{plan['approval_id']}/approve",
            json={"reason": "phase50 browser submit"},
        )
        assert approved.status_code == 200, approved.text

        executed = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/resume-after-human",
            json={"adapter_id": adapter["adapter_id"], "approval_id": plan["approval_id"]},
        )
        assert executed.status_code == 200, executed.text
        payload = executed.json()

    assert payload["plan"]["status"] == "completed"
    assert payload["execution"]["status"] == "completed"
    assert site.submissions == [{"title": "Phase50", "body": plan["content_summary"]}]
    assert any(item["step_name"] == "verify_result" for item in payload["steps"])
    assert payload["execution"]["evidence"]["verification_evidence_present"] is True
    assert _payload_leakage_count(payload) == 0


def test_phase50_browser_adapter_challenge_and_drift_fail_closed(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _TestSite(challenge=True) as site:
        plan = _create_publish_plan(
            client,
            platform_key="phase50_challenge",
            execution_mode="browser",
        )
        adapter = _register_browser_adapter(
            client,
            platform_key="phase50_challenge",
            start_url=site.url("/publish"),
            challenge_texts=["captcha"],
        )["adapter"]
        challenged = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={"adapter_id": adapter["adapter_id"]},
        )
        assert challenged.status_code == 200, challenged.text
        payload = challenged.json()
    assert payload["execution"]["status"] == "challenge_detected"
    assert payload["drift_events"][0]["drift_type"] == "challenge_detected"
    assert payload["plan"]["failure_reason"] == "adapter_challenge_detected"

    with _TestSite(no_form=True) as drift_site:
        drift_plan = _create_publish_plan(
            client,
            platform_key="phase50_drift",
            execution_mode="browser",
        )
        drift_adapter = _register_browser_adapter(
            client,
            platform_key="phase50_drift",
            start_url=drift_site.url("/publish"),
        )["adapter"]
        approve = client.post(
            f"/api/approvals/{drift_plan['approval_id']}/approve",
            json={"reason": "phase50 drift submit"},
        )
        assert approve.status_code == 200, approve.text
        drifted = client.post(
            f"/api/external-platform/action-plans/{drift_plan['plan_id']}/execute-adapter",
            json={
                "adapter_id": drift_adapter["adapter_id"],
                "approval_id": drift_plan["approval_id"],
            },
        )
        assert drifted.status_code == 200, drifted.text
        drift_payload = drifted.json()
    assert drift_payload["execution"]["status"] == "drift_detected"
    assert drift_payload["drift_events"][0]["drift_type"] == "selector_or_page_drift"
    assert drift_payload["plan"]["status"] == "failed"
    assert _payload_leakage_count({"challenge": payload, "drift": drift_payload}) == 0


def test_phase50_mcp_adapter_uses_registered_tool_and_approval(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.mcp_service.set_transport_factory(lambda _server: Phase50MCPTransport())
    _create_mcp_server(client)
    plan = _create_publish_plan(client, platform_key="phase50_mcp", execution_mode="mcp_adapter")
    adapter = _register_mcp_adapter(client, platform_key="phase50_mcp")["adapter"]

    compiled = client.post(
        f"/api/external-platform/action-plans/{plan['plan_id']}/compile",
        json={"adapter_id": adapter["adapter_id"]},
    )
    assert compiled.status_code == 200, compiled.text
    assert any(item["executor"] == "mcp" for item in compiled.json()["steps"])

    pending = client.post(
        f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
        json={"adapter_id": adapter["adapter_id"]},
    )
    assert pending.status_code == 200, pending.text
    assert pending.json()["execution"]["status"] == "awaiting_approval"

    approved = client.post(
        f"/api/approvals/{plan['approval_id']}/approve",
        json={"reason": "phase50 mcp submit"},
    )
    assert approved.status_code == 200, approved.text
    executed = client.post(
        f"/api/external-platform/action-plans/{plan['plan_id']}/resume-after-human",
        json={"adapter_id": adapter["adapter_id"], "approval_id": plan["approval_id"]},
    )
    assert executed.status_code == 200, executed.text
    payload = executed.json()

    assert payload["plan"]["status"] == "completed"
    assert payload["execution"]["status"] == "completed"
    assert any(
        item["mcp_call_id"]
        for item in payload["steps"]
        if item["step_name"] == "submit_publish"
    )
    assert _payload_leakage_count(payload) == 0


def test_phase50_release_contracts_eval_report_and_diagnostic(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    for name in [
        "ExternalPlatformAdapterRegistry",
        "BrowserPlatformAdapterCompiler",
        "MCPPlatformAdapterCompiler",
        "AdapterApprovalBinding",
        "AdapterChallengeFailClosed",
        "AdapterDriftDetection",
        "AdapterExecutionReplayEvidence",
    ]:
        assert by_name[name]["status"] == "implemented"

    suites = client.get("/api/evals/suites").json()["items"]
    assert "suite_phase50_browser_mcp_platform_adapters" in {
        item["suite_id"] for item in suites
    }
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase50_browser_mcp_platform_adapters"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 9

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))

    assert completed["status"] == "ready_for_release"
    phase50 = report["summary"]["phase50"]
    assert phase50["suite_id"] == "suite_phase50_browser_mcp_platform_adapters"
    assert phase50["registered_cases"] == 9
    assert phase50["migration_contract"]["required_migration"] == (
        "032_external_platform_adapters.sql"
    )
    assert report["summary"]["phase23"]["capability_scores"]["phase50"]["registered"] is True
    assert any(
        item["source_type"] == "phase50_browser_mcp_platform_adapters" for item in evidence
    )
    assert "phase50_browser_mcp_platform_adapters" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_publish_plan(
    client: TestClient,
    *,
    platform_key: str,
    execution_mode: str,
) -> dict[str, Any]:
    _create_platform_target(client, platform_key=platform_key, execution_modes=[execution_mode])
    account = _create_account(
        client,
        display_name=f"{platform_key} account",
        provider_key=platform_key,
    )
    _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)
    intent = client.post(
        "/api/external-platform/intents/resolve",
        json={
            "text": f"帮我在 {platform_key} 发布文章，内容：Phase50",
            "member_id": "mem_xiaoyao",
        },
    )
    assert intent.status_code == 200, intent.text
    created = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent.json()["intent"]["intent_id"], "execution_mode": execution_mode},
    )
    assert created.status_code == 200, created.text
    plan = dict(created.json()["plan"])
    assert plan["task_id"]
    assert plan["approval_id"]
    assert plan["status"] == "awaiting_approval"
    return plan


def _create_platform_target(
    client: TestClient,
    *,
    platform_key: str,
    execution_modes: list[str],
) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/targets",
        json={
            "platform_key": platform_key,
            "display_name": platform_key,
            "aliases": [platform_key],
            "supported_actions": ["publish_content"],
            "required_asset_types": ["account"],
            "execution_modes": execution_modes,
            "risk_defaults": {"publish_content": "R4"},
            "metadata": {"phase50_test_target": True, "real_external_platform_integration": False},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_account(
    client: TestClient,
    *,
    display_name: str,
    provider_key: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": display_name,
            "provider": provider_key,
            "sensitivity": "high",
            "config": {"platform": provider_key, "username": display_name, "auth_type": "token"},
            "secret_value": "token=phase50-secret-token",
            "owner_scope_type": "member",
            "owner_scope_id": "mem_xiaoyao",
            "visibility": "private",
            "risk_level": "R4",
            "summary_text": f"{display_name} account",
            "capabilities": ["publish_content", "publish_post", "interact"],
            "metadata": {"platform": provider_key},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _grant(
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
            "source_type": "phase50_test",
            "source_id": asset_id,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _register_browser_adapter(
    client: TestClient,
    *,
    platform_key: str,
    start_url: str,
    challenge_texts: list[str] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/adapters",
        json={
            "platform_key": platform_key,
            "adapter_type": "browser",
            "action_type": "publish_content",
            "display_name": "Phase50 browser adapter",
            "status": "active",
            "allowed_domains": ["127.0.0.1"],
            "manifest": {
                "start_url": start_url,
                "allowed_domains": ["127.0.0.1"],
                "publish_flow": {
                    "start_url": start_url,
                    "default_title": "Phase50",
                    "selectors": {
                        "title": "#title",
                        "body": "#body",
                        "form": "#phase50-form",
                        "submit": "#phase50-form",
                    },
                    "verify": {"success_text": "published", "expected_url": start_url},
                },
                "challenge_detection": {"any_text": challenge_texts or []},
            },
            "version": "1.0.0",
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _register_mcp_adapter(client: TestClient, *, platform_key: str) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/adapters",
        json={
            "platform_key": platform_key,
            "adapter_type": "mcp",
            "action_type": "publish_content",
            "display_name": "Phase50 MCP adapter",
            "status": "active",
            "manifest": {
                "tool_map": {
                    "prepare": "mcp.phase50.prepare",
                    "submit": "mcp.phase50.submit",
                    "verify": "mcp.phase50.verify",
                },
                "mock_mcp": True,
            },
            "version": "1.0.0",
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_mcp_server(client: TestClient) -> None:
    created = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "phase50",
            "display_name": "Phase50 Mock MCP",
            "transport": "stdio",
            "command": "fake-mcp",
            "args": [],
            "env_refs": [],
        },
    )
    assert created.status_code == 200, created.text
    assert client.post("/api/mcp/servers/phase50/enable").status_code == 200
    synced = client.post("/api/mcp/servers/phase50/sync")
    assert synced.status_code == 200, synced.text
    tools = client.get("/api/mcp/servers/phase50/tools").json()["items"]
    assert {item["registry_tool_name"] for item in tools}.issuperset(
        {"mcp.phase50.prepare", "mcp.phase50.submit", "mcp.phase50.verify"}
    )


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "phase50-secret-token",
        "phase50-raw-token",
        "token=phase50",
        "cookie=phase50",
        "password=phase50",
        "private_key=phase50",
        "mnemonic=phase50",
        "c:\\users\\administrator\\phase50",
    ]
    return sum(1 for item in forbidden if item in serialized)


class Phase50MCPTransport:
    async def start(self) -> None:
        return None

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        return None

    async def close(self) -> None:
        return None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "phase50", "version": "0.1.0"},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": name,
                        "description": f"Phase50 {name}",
                        "inputSchema": {"type": "object", "required": ["text"]},
                        "annotations": {"readOnlyHint": True},
                    }
                    for name in ["prepare", "submit", "verify"]
                ]
            }
        if method == "resources/list":
            return {"resources": []}
        if method == "prompts/list":
            return {"prompts": []}
        if method == "tools/call":
            name = (params or {}).get("name")
            arguments = (params or {}).get("arguments", {})
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"{name}:ok post_id=phase50-post content={arguments.get('text')}",
                    }
                ]
            }
        raise AssertionError(f"unexpected MCP method: {method}")


class _TestSite:
    def __init__(self, *, challenge: bool = False, no_form: bool = False) -> None:
        self.challenge = challenge
        self.no_form = no_form
        self.submissions: list[dict[str, str]] = []

    def __enter__(self) -> _TestSite:
        handler = _handler_for(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._server.server_port}{path}"


def _handler_for(site: _TestSite) -> type[BaseHTTPRequestHandler]:
    class _Phase50Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if site.challenge:
                self._send_html(
                    "<html><title>Challenge</title><body>captcha required</body></html>"
                )
                return
            if site.no_form:
                self._send_html("<html><title>No form</title><body>layout changed</body></html>")
                return
            self._send_html(
                """
                <html>
                  <head><title>Phase50 Publish</title></head>
                  <body>
                    <form id="phase50-form" method="post" action="/published">
                      <input id="title" name="title" value="">
                      <textarea id="body" name="body"></textarea>
                      <button id="submit" type="submit">publish</button>
                    </form>
                  </body>
                </html>
                """
            )

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            site.submissions.append(
                {
                    "title": data.get("title", [""])[0],
                    "body": data.get("body", [""])[0],
                }
            )
            self._send_html(
                "<html><title>Published</title><body>published post_id=phase50-post</body></html>"
            )

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return _Phase50Handler
