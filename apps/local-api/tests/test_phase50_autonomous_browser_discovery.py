from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from core_types import RiskLevel
from fastapi.testclient import TestClient


def test_phase50_autonomous_browser_discovery_prepares_draft_then_publishes_after_approval(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _DiscoverySite() as site:
        _create_target(client, platform_key="phase50_auto", start_url=site.url("/"))
        _create_account_and_grant(client, provider_key="phase50_auto")
        plan = _create_plan(client, platform_key="phase50_auto")

        pending = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={},
        )
        assert pending.status_code == 200, pending.text
        payload = pending.json()

        assert payload["discovery"]["status"] == "awaiting_approval"
        assert payload["discovery"]["failure_reason"] is None
        assert payload["adapter"]["status"] == "test_only"
        assert payload["adapter"]["metadata"]["source"] == "autonomous_discovery"
        assert payload["adapter"]["metadata"]["candidate_adapter"] is True
        assert any(item["step_name"] == "fill_body" for item in payload["steps"])
        assert any(
            item["step_name"] == "submit_publish" and item["status"] == "awaiting_approval"
            for item in payload["steps"]
        )
        assert site.submissions == []

        approved = client.post(
            f"/api/approvals/{plan['approval_id']}/approve",
            json={"reason": "phase50 autonomous submit"},
        )
        assert approved.status_code == 200, approved.text
        executed = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/resume-after-human",
            json={"approval_id": plan["approval_id"]},
        )
        assert executed.status_code == 200, executed.text
        executed_payload = executed.json()

    assert executed_payload["plan"]["status"] == "completed"
    assert executed_payload["execution"]["status"] == "completed"
    assert site.submissions == [
        {"title": "Phase50 autonomous", "body": plan["content_summary"]}
    ]
    adapters = client.get(
        "/api/external-platform/adapters",
        params={"platform_key": "phase50_auto", "status": "test_only"},
    ).json()["items"]
    assert len(adapters) == 1
    assert adapters[0]["metadata"]["success_count"] == 1
    assert _payload_leakage_count({"pending": payload, "executed": executed_payload}) == 0


def test_phase50_autonomous_candidate_adapter_is_reused_on_second_publish(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _DiscoverySite() as site:
        _create_target(client, platform_key="phase50_auto_reuse", start_url=site.url("/"))
        _create_account_and_grant(client, provider_key="phase50_auto_reuse")
        first_plan = _create_plan(client, platform_key="phase50_auto_reuse")
        first_pending = client.post(
            f"/api/external-platform/action-plans/{first_plan['plan_id']}/execute-adapter"
        ).json()
        adapter_id = first_pending["adapter"]["adapter_id"]
        client.post(
            f"/api/approvals/{first_plan['approval_id']}/approve",
            json={"reason": "phase50 first autonomous submit"},
        )
        client.post(
            f"/api/external-platform/action-plans/{first_plan['plan_id']}/resume-after-human",
            json={"approval_id": first_plan["approval_id"]},
        )

        second_plan = _create_plan(client, platform_key="phase50_auto_reuse")
        second_pending = client.post(
            f"/api/external-platform/action-plans/{second_plan['plan_id']}/execute-adapter"
        )
        assert second_pending.status_code == 200, second_pending.text
        second_payload = second_pending.json()

    assert second_payload["adapter"]["adapter_id"] == adapter_id
    assert second_payload["discovery"] is None
    assert second_payload["execution"]["status"] == "awaiting_approval"
    assert len(site.submissions) == 1


def test_phase50_autonomous_discovery_challenge_and_missing_form_fail_closed(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _DiscoverySite(challenge=True) as site:
        _create_target(client, platform_key="phase50_auto_challenge", start_url=site.url("/"))
        _create_account_and_grant(client, provider_key="phase50_auto_challenge")
        plan = _create_plan(client, platform_key="phase50_auto_challenge")
        challenged = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={},
        )
        assert challenged.status_code == 200, challenged.text
        challenge_payload = challenged.json()
    assert challenge_payload["execution"] is None
    assert challenge_payload["discovery"]["status"] == "challenge_detected"
    assert challenge_payload["plan"]["status"] == "failed"
    assert site.submissions == []

    with _DiscoverySite(no_form=True) as drift_site:
        _create_target(client, platform_key="phase50_auto_no_form", start_url=drift_site.url("/"))
        _create_account_and_grant(client, provider_key="phase50_auto_no_form")
        drift_plan = _create_plan(client, platform_key="phase50_auto_no_form")
        failed = client.post(
            f"/api/external-platform/action-plans/{drift_plan['plan_id']}/execute-adapter",
            json={},
        )
        assert failed.status_code == 200, failed.text
        failed_payload = failed.json()
    assert failed_payload["execution"] is None
    assert failed_payload["discovery"]["status"] == "failed"
    assert failed_payload["discovery"]["failure_reason"] == "publish_form_not_found"
    assert failed_payload["next_step"] == "provide_publish_url_or_configure_adapter"
    assert drift_site.submissions == []


def test_phase50_autonomous_discovery_does_not_guess_account_or_platform(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _DiscoverySite() as site:
        _create_target(client, platform_key="phase50_auto_multi", start_url=site.url("/"))
        _create_account_and_grant(client, provider_key="phase50_auto_multi", display_name="A")
        _create_account_and_grant(client, provider_key="phase50_auto_multi", display_name="B")
        intent = _resolve(client, "帮我在 phase50_auto_multi 发布文章，内容：Phase50 autonomous")
        created = client.post(
            "/api/external-platform/action-plans",
            json={"intent_id": intent["intent_id"], "execution_mode": "browser"},
        )
        assert created.status_code == 200, created.text
        plan = created.json()["plan"]
        assert plan["status"] == "awaiting_clarification"

        not_executed = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={},
        )
        assert not_executed.status_code == 200, not_executed.text
        assert not_executed.json()["discovery"] is None
        assert not_executed.json()["next_step"] == "awaiting_clarification"
        assert site.submissions == []

    missing_platform = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": "帮我发布文章，内容：Phase50 autonomous", "member_id": "mem_xiaoyao"},
    )
    assert missing_platform.status_code == 200, missing_platform.text
    assert missing_platform.json()["intent"]["status"] == "clarification_needed"
    missing_plan = client.post(
        "/api/external-platform/action-plans",
        json={
            "intent_id": missing_platform.json()["intent"]["intent_id"],
            "execution_mode": "browser",
        },
    ).json()["plan"]
    assert missing_plan["status"] == "awaiting_intent_clarification"
    no_guess = client.post(
        f"/api/external-platform/action-plans/{missing_plan['plan_id']}/execute-adapter",
        json={},
    )
    assert no_guess.status_code == 200, no_guess.text
    assert no_guess.json()["discovery"] is None
    assert no_guess.json()["next_step"] == "awaiting_intent_clarification"


def test_phase50_autonomous_discover_adapter_debug_endpoint(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _DiscoverySite() as site:
        _create_target(client, platform_key="phase50_auto_debug", start_url=site.url("/"))
        _create_account_and_grant(client, provider_key="phase50_auto_debug")
        plan = _create_plan(client, platform_key="phase50_auto_debug")
        discovered = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/discover-adapter"
        )
        assert discovered.status_code == 200, discovered.text
        payload = discovered.json()

    assert payload["discovery"]["status"] == "draft_prepared"
    assert payload["adapter"]["status"] == "test_only"
    assert payload["next_step"] == "execute_adapter"


def test_phase50_autonomous_release_suite_and_contracts(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    for name in [
        "AutonomousBrowserDiscovery",
        "DiscoveryCandidateAdapterLearning",
        "DiscoveryApprovalBeforeSubmit",
    ]:
        assert by_name[name]["status"] == "implemented"

    suites = client.get("/api/evals/suites").json()["items"]
    assert "suite_phase50_autonomous_browser_discovery" in {
        item["suite_id"] for item in suites
    }
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase50_autonomous_browser_discovery"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 10


def _create_target(
    client: TestClient,
    *,
    platform_key: str,
    start_url: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/targets",
        json={
            "platform_key": platform_key,
            "display_name": platform_key,
            "aliases": [platform_key],
            "supported_actions": ["publish_content"],
            "required_asset_types": ["account"],
            "execution_modes": ["browser"],
            "risk_defaults": {"publish_content": "R4"},
            "metadata": {
                "autonomous_browser_discovery": {"start_url": start_url},
                "real_external_platform_integration": False,
            },
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_account_and_grant(
    client: TestClient,
    *,
    provider_key: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    account = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": display_name or f"{provider_key} account",
            "provider": provider_key,
            "sensitivity": "high",
            "config": {
                "platform": provider_key,
                "username": display_name or provider_key,
                "auth_type": "token",
            },
            "secret_value": "token=phase50-autonomous-secret",
            "owner_scope_type": "member",
            "owner_scope_id": "mem_xiaoyao",
            "visibility": "private",
            "risk_level": "R4",
            "summary_text": f"{provider_key} account",
            "capabilities": ["publish_content", "publish_post", "interact"],
            "metadata": {"platform": provider_key},
        },
    )
    assert account.status_code == 200, account.text
    asset = dict(account.json())
    grant = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset["asset_id"],
            "action": "publish_content",
            "effect": "allow",
            "risk_level": RiskLevel.R4.value,
            "source_type": "phase50_autonomous_test",
            "source_id": asset["asset_id"],
        },
    )
    assert grant.status_code == 200, grant.text
    return asset


def _resolve(client: TestClient, text: str) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": text, "member_id": "mem_xiaoyao"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["intent"])


def _create_plan(client: TestClient, *, platform_key: str) -> dict[str, Any]:
    intent = _resolve(client, f"帮我在 {platform_key} 发布文章，内容：Phase50 autonomous")
    created = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent["intent_id"], "execution_mode": "browser"},
    )
    assert created.status_code == 200, created.text
    plan = dict(created.json()["plan"])
    assert plan["task_id"]
    assert plan["approval_id"]
    assert plan["status"] == "awaiting_approval"
    return plan


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "phase50-autonomous-secret",
        "token=phase50",
        "cookie=phase50",
        "password=phase50",
        "private_key=phase50",
        "mnemonic=phase50",
    ]
    return sum(1 for item in forbidden if item in serialized)


class _DiscoverySite:
    def __init__(self, *, challenge: bool = False, no_form: bool = False) -> None:
        self.challenge = challenge
        self.no_form = no_form
        self.submissions: list[dict[str, str]] = []

    def __enter__(self) -> _DiscoverySite:
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


def _handler_for(site: _DiscoverySite) -> type[BaseHTTPRequestHandler]:
    class _Phase50AutonomousHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if site.challenge:
                self._send_html(
                    "<html><title>Challenge</title><body>captcha required</body></html>"
                )
                return
            if self.path.startswith("/publish"):
                if site.no_form:
                    self._send_html(
                        "<html><title>Composer</title><body>layout changed</body></html>"
                    )
                    return
                self._send_html(
                    """
                    <html>
                      <head><title>Composer</title></head>
                      <body>
                        <form id="publish-form" method="post" action="/published">
                          <input id="title" name="title" placeholder="标题" value="">
                          <textarea id="body" name="body" placeholder="正文"></textarea>
                          <button id="submit" type="submit">发布</button>
                        </form>
                      </body>
                    </html>
                    """
                )
                return
            self._send_html(
                """
                <html>
                  <head><title>Home</title></head>
                  <body><a id="write-entry" href="/publish">写文章</a></body>
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
                "<html><title>Published</title><body>published post_id=phase50-auto</body></html>"
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

    return _Phase50AutonomousHandler
