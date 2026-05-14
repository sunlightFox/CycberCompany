from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_phase37_profile_session_lifecycle_and_api(client: TestClient) -> None:
    profile = _create_profile(client)
    profile_id = profile["browser_profile_id"]

    paused = client.post(
        f"/api/browser/profiles/{profile_id}/pause",
        json={"reason": "phase37 pause"},
    ).json()
    activated = client.post(f"/api/browser/profiles/{profile_id}/activate").json()
    events = client.get(f"/api/browser/profiles/{profile_id}/events").json()["items"]

    migration_contract = assert_phase_migration_contract(client, "phase37")
    assert migration_contract["required_migration"] == "025_browser_sessions.sql"
    assert profile["status"] == "active"
    assert paused["status"] == "paused"
    assert activated["status"] == "active"
    assert {item["event_type"] for item in events}.issuperset(
        {
            "browser_profile.created",
            "browser_profile.paused",
            "browser_profile.activated",
        }
    )


def test_phase37_asset_broker_session_handle_is_redacted_and_revoked(
    client: TestClient,
) -> None:
    profile = _create_profile(client)
    asset = _create_browser_session_asset(client)
    session = _create_session(client, profile["browser_profile_id"], asset["asset_id"])
    _grant(client, asset["asset_id"], "read")

    query = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": ["read"],
            "keywords": ["phase37"],
        },
    )
    assert query.status_code == 200, query.text
    handle = query.json()["handles"][0]
    resolved = client.post(
        f"/api/assets/handles/{handle['handle_id']}/resolve-for-tool",
        json={"action": "read", "tool_name": "browser.snapshot", "task_id": None},
    )
    revoke = client.post(
        f"/api/browser/profiles/{profile['browser_profile_id']}/revoke",
        json={"reason": "phase37 revoke"},
    ).json()
    invalid = client.post(
        f"/api/assets/handles/{handle['handle_id']}/validate",
        json={"subject_type": "member", "subject_id": "mem_xiaoyao", "action": "read"},
    )

    resolved_json = resolved.json()
    serialized = json.dumps({"query": query.json(), "resolved": resolved_json}, ensure_ascii=False)
    assert session["asset_id"] == asset["asset_id"]
    assert "phase37-cookie-value" not in serialized
    assert (
        resolved_json["resource"]["config"]["browser_profile_id"]
        == profile["browser_profile_id"]
    )
    assert (
        resolved_json["resource"]["config"]["browser_session_id"]
        == session["browser_session_id"]
    )
    assert revoke["status"] == "revoked"
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "ASSET_HANDLE_INVALID"


def test_phase37_browser_snapshot_writes_evidence_and_replay(
    client: TestClient,
) -> None:
    with _TestSite() as site:
        profile = _create_profile(client, allowed_domains=["127.0.0.1"])
        asset = _create_browser_session_asset(client)
        _create_session(client, profile["browser_profile_id"], asset["asset_id"])
        _grant(client, asset["asset_id"], "read")
        handle = _query_handle(client, asset["asset_id"], "read")
        task = _create_task(client)

        response = client.post(
            "/api/tools/execute",
            json={
                "task_id": task["task_id"],
                "tool_name": "browser.snapshot",
                "args": {
                    "url": f"{site.url('/page?token=phase37-url-secret')}",
                    "session_handle_id": handle["handle_id"],
                },
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        evidence = client.get(
            f"/api/browser/evidence/{result['browser_evidence_id']}"
        ).json()
        task_evidence = client.get(
            f"/api/tasks/{task['task_id']}/browser-evidence"
        ).json()["items"]
        replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    serialized = json.dumps(
        {"result": result, "evidence": evidence, "task_evidence": task_evidence, "replay": replay},
        ensure_ascii=False,
    )
    assert result["browser_evidence_id"]
    assert result["session_state"] == "active"
    assert result["backend_capabilities"]["dom_snapshot"] is True
    assert result["verification_evidence"]["present"] is True
    assert result["browser_execution_summary"]["preflight_outcome"] == "ready"
    assert evidence["browser_session_id"]
    assert evidence["untrusted_external_content"] is True
    assert evidence["redaction_summary"]["cookie_redacted"] is True
    assert evidence["safety_decision"]["reason_codes"]
    assert task_evidence[0]["browser_evidence_id"] == evidence["browser_evidence_id"]
    assert replay["browser_evidence"][0]["browser_evidence_id"] == evidence["browser_evidence_id"]
    assert "phase37-url-secret" not in serialized
    assert "cookie" not in serialized.lower() or "[REDACTED" in serialized


def test_phase37_download_artifact_and_url_safety_blocks(client: TestClient) -> None:
    with _TestSite() as site:
        profile = _create_profile(client, allowed_domains=["127.0.0.1"])
        asset = _create_browser_session_asset(client)
        _create_session(client, profile["browser_profile_id"], asset["asset_id"])
        _grant(client, asset["asset_id"], "download")
        handle = _query_handle(client, asset["asset_id"], "download")
        task = _create_task(client)
        payload = {
            "task_id": task["task_id"],
            "tool_name": "browser.download",
            "args": {
                "url": site.url("/download.csv"),
                "display_name": "phase37.csv",
                "session_handle_id": handle["handle_id"],
            },
        }
        pending = client.post("/api/tools/execute", json=payload).json()
        approval_id = pending["approval"]["approval_id"]
        client.post(f"/api/approvals/{approval_id}/approve", json={"reason": "phase37"})
        downloaded = client.post(
            "/api/tools/execute",
            json={**payload, "approval_id": approval_id},
        )

    blocked_urls = [
        "file:///C:/Users/Administrator/Desktop/secret.txt",
        "javascript:alert(1)",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/admin",
    ]
    blocked = [
        client.post(
            "/api/tools/execute",
            json={"tool_name": "browser.open", "args": {"url": url}},
        )
        for url in blocked_urls
    ]
    result = downloaded.json()["result"]
    artifact = result["download"]
    evidence = result["browser_evidence"]

    assert downloaded.status_code == 200, downloaded.text
    assert result["session_state"] == "active"
    assert result["backend_capabilities"]["file_download"] is True
    assert result["browser_execution_summary"]["step_outcome_counts"]["completed"] == 1
    assert artifact["artifact_type"] == "download"
    assert "quarantine" in artifact["uri"]
    assert evidence["download_artifact_id"] == artifact["artifact_id"]
    assert evidence["network_summary"]["request_count"] == 1
    assert all(item.status_code == 403 for item in blocked)
    assert all(item.json()["error"]["code"] == "TOOL_PERMISSION_DENIED" for item in blocked)


def test_phase37_release_contracts_summary_diagnostic_and_leakage(
    client: TestClient,
) -> None:
    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_module = {item["name"]: item for item in contracts}
    registry = cast(Any, client.app).state.registry

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    phase37 = report["summary"]["phase37"]

    assert "suite_phase37_browser_sessions" in {item["suite_id"] for item in suites}
    for module in [
        "BrowserProfileService",
        "BrowserSessionAssetBroker",
        "BrowserURLSafetyPolicy",
        "BrowserEvidenceBundle",
        "BrowserSessionHandleRedaction",
        "BrowserReplayEvidence",
    ]:
        assert by_module[module]["status"] == "implemented"
    assert completed["status"] == "ready_for_release"
    assert phase37["suite_id"] == "suite_phase37_browser_sessions"
    assert phase37["registered_cases"] == 10
    assert phase37["tables"]["browser_profiles"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase37"]["registered"] is True
    assert any(item["source_type"] == "phase37_browser_sessions" for item in evidence)
    assert "phase37" in diagnostic
    assert "phase37_browser_sessions" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_profile(
    client: TestClient,
    *,
    allowed_domains: list[str] | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/browser/profiles",
        json={
            "display_name": "Phase37 profile",
            "profile_type": "task_isolated",
            "sensitivity": "medium",
            "allowed_domains": allowed_domains or ["example.com"],
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
            "display_name": "Phase37 browser session",
            "provider": "browser_session",
            "sensitivity": "high",
            "secret_value": "phase37-cookie-value",
            "config": {
                "platform": "phase37",
                "username": "browser-user",
                "auth_type": "cookie_session",
                "login_domain": "127.0.0.1",
            },
            "summary_text": "Phase37 browser session asset",
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
            "login_domain": "127.0.0.1",
            "auth_type": "cookie_session",
            "sensitivity": "high",
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
            "keywords": ["phase37"],
        },
    )
    assert response.status_code == 200, response.text
    handles = [
        item for item in response.json()["handles"] if item["asset_id"] == asset_id
    ]
    assert handles
    return handles[0]


def _create_task(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/tasks",
        json={"goal": "phase37 browser evidence", "mode_hint": "workflow", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _payload_leakage_count(payload: Any) -> int:
    text = json.dumps(payload, ensure_ascii=False).lower()
    needles = [
        "phase37-cookie-value",
        "phase37-url-secret",
        "sk-phase37",
        "private_key=phase37",
        "mnemonic=phase37",
        "c:\\users\\administrator\\phase37",
    ]
    return sum(1 for needle in needles if needle in text)


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
            body = b"name,value\nphase37,ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = (
            b"<html><head><title>Phase37 Page</title></head>"
            b"<body>phase37 page without secrets</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return
