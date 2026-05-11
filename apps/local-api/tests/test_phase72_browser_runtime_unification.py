from __future__ import annotations

import json
from typing import Any, cast

import anyio
import pytest
from app.services.browser_executor import BrowserExecutionResult
from fastapi.testclient import TestClient


def test_phase72_runtime_topology_and_tool_runtime_expose_browser_runtime_layers(
    client: TestClient,
) -> None:
    topology = client.get("/api/system/runtime-topology")
    assert topology.status_code == 200, topology.text
    items = {item["name"]: item for item in topology.json()["items"]}

    browser_workflow = items["browser_workflow"]
    assert browser_workflow["runtime"] == "browser_workflow_runtime"
    assert browser_workflow["status"] == "runtime_native"
    assert "browser_session_runtime" in browser_workflow["dependencies"]
    assert "browser_page_state_runtime" in browser_workflow["dependencies"]
    assert "browser_replay_store" in browser_workflow["dependencies"]

    tool_runtime = client.get("/api/system/tool-runtime")
    assert tool_runtime.status_code == 200, tool_runtime.text
    browser = tool_runtime.json()["browser"]
    assert browser["session_runtime"]["runtime"] == "browser_session_runtime"
    assert browser["page_state_runtime"]["runtime"] == "browser_page_state_runtime"
    assert browser["replay_store"]["runtime"] == "browser_replay_store"
    assert "challenge_detected" in browser["page_state_runtime"]["status_model"]


def test_phase72_browser_snapshot_writes_unified_page_state_and_replay(
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
            title="Phase72 Unified Page",
            http_status=200,
            snapshot=(
                "<html><body><h1>Unified Page</h1>"
                "<form><input name='q' /></form><button>提交</button></body></html>"
            ),
            content_preview=(
                "<html><body><h1>Unified Page</h1>"
                "<form><input name='q' /></form><button>提交</button></body></html>"
            ),
            recoverable=False,
            selector=request.selector,
        )

    registry = cast(Any, client.app).state.registry
    monkeypatch.setattr(registry.tool_runtime._browser_executor, "execute", fake_execute)

    intent = client.post(
        "/api/browser-workflows/intents/resolve",
        json={
            "text": "打开 https://example.com/unified 并观察页面状态",
            "member_id": "mem_xiaoyao",
            "target_url": "https://example.com/unified",
            "action_type": "read_page",
        },
    )
    assert intent.status_code == 200, intent.text
    plan_created = client.post(
        "/api/browser-workflows/plans",
        json={"intent_id": intent.json()["intent"]["intent_id"]},
    )
    assert plan_created.status_code == 200, plan_created.text
    plan = plan_created.json()["plan"]

    snapshot = client.post(
        "/api/tools/execute",
        json={
            "task_id": plan["task_id"],
            "tool_name": "browser.snapshot",
            "args": {"url": "https://example.com/unified"},
        },
    )
    assert snapshot.status_code == 200, snapshot.text
    result = snapshot.json()["result"]
    page_state = result["browser_page_state"]
    assert page_state["status"] == "actionable"
    assert page_state["current_url"] == "https://example.com/unified"
    assert page_state["evidence_refs"]
    assert result["browser_evidence_id"]

    replay = client.get(f"/api/browser-workflows/plans/{plan['plan_id']}/replay")
    assert replay.status_code == 200, replay.text
    replay_payload = replay.json()
    assert replay_payload["redaction_summary"]["replay_store"] == "browser_replay_store"
    assert replay_payload["redaction_summary"]["page_state_count"] >= 1


def test_phase72_chat_browser_read_uses_shared_page_state_and_honest_login_semantics(
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
            evidence_summary="login page observed",
            title="Phase72 Login",
            http_status=200,
            snapshot=(
                "<html><body><h1>请登录</h1><form>"
                "<input name='username' /><input type='password' name='password' />"
                "</form></body></html>"
            ),
            content_preview=(
                "<html><body><h1>请登录</h1><form>"
                "<input name='username' /><input type='password' name='password' />"
                "</form></body></html>"
            ),
            recoverable=False,
            selector=request.selector,
        )

    registry = cast(Any, client.app).state.registry
    monkeypatch.setattr(registry.tool_runtime._browser_executor, "execute", fake_execute)

    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase72-browser-read-login",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "帮我看这个登录页讲什么 https://example.com/login",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )
    completed = next(event for event in events if event["event"] == "response.completed")
    payload = completed["payload"]["response_plan"]["structured_payload"]["browser_read_page"]

    assert "登录或鉴权入口" in reply
    assert payload["page_state"]["status"] == "login_required"
    assert payload["evidence_refs"]
    assert payload["page_state"]["evidence_refs"]


def test_phase72_browser_session_runtime_reuses_latest_page_state_for_same_task(
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
            evidence_summary=f"{request.action} on {request.url}",
            title="Phase72 Reuse",
            http_status=200,
            snapshot="<html><body><button id='go'>Go</button></body></html>",
            content_preview="<html><body><button id='go'>Go</button></body></html>",
            recoverable=False,
            selector=request.selector,
        )

    registry = cast(Any, client.app).state.registry
    monkeypatch.setattr(registry.tool_runtime._browser_executor, "execute", fake_execute)
    task = client.post("/api/tasks", json={"goal": "Phase72 browser reuse", "auto_start": False}).json()

    opened = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "browser.open",
            "args": {"url": "https://example.com/reused"},
        },
    )
    assert opened.status_code == 200, opened.text

    session_context: dict[str, Any] = {}
    resolved_url = _run_async(
        client,
        registry.browser_session_runtime.resolve_page_url(
            task_id=task["task_id"],
            args={},
            action="click",
            session_context=session_context,
        ),
    )
    assert resolved_url == "https://example.com/reused"
    assert session_context["current_url"] == "https://example.com/reused"


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                data = json.loads(current.get("data", "{}"))
                events.append(
                    {
                        "event": data.get("event") or current.get("event"),
                        "payload": data.get("payload", {}),
                    }
                )
                current = {}
            continue
        if line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current["data"] = f"{current.get('data', '')}{line.split(':', 1)[1].strip()}"
    return events


def _run_async(client: TestClient, awaitable: Any) -> Any:
    portal = getattr(client, "portal", None)
    if portal is not None:

        async def portal_runner() -> Any:
            return await awaitable

        return portal.call(portal_runner)

    async def runner() -> Any:
        return await awaitable

    return anyio.run(runner)
