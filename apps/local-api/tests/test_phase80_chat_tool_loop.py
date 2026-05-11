from __future__ import annotations

import json
from typing import Any

import anyio
from core_types import ErrorCode
from fastapi.testclient import TestClient

from app.core.errors import AppError


def test_phase80_direct_answer_does_not_enter_tool_loop(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[str] = []
    original_execute = client.app.state.registry.tool_runtime.execute

    async def _tracked_execute(request: Any, trace_id: str | None = None) -> Any:
        calls.append(str(request.tool_name))
        return await original_execute(request, trace_id=trace_id)

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", _tracked_execute)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase80-direct-answer",
        "解释一下单轮工具闭环是什么，但这次不要执行任何工具。",
    )

    events = _parse_sse(client.get(created["stream_url"]).text)

    assert not calls
    assert "tool.completed" not in {event["event"] for event in events}


def test_phase80_browser_readonly_success_adds_single_turn_tool_payload(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request: Any, trace_id: str | None = None) -> Any:
        del trace_id
        assert request.tool_name == "browser.snapshot"
        return type(
            "ToolResponse",
            (),
            {
                "result": {
                    "title": "Phase80 页面",
                    "url": "https://example.test/phase80",
                    "http_status": 200,
                    "browser_evidence_id": "bev_phase80",
                    "content_preview": "<html><body><h1>Phase80 页面</h1><p>单轮工具闭环成功。</p></body></html>",
                    "browser_page_state": {
                        "status": "observed",
                        "page_title": "Phase80 页面",
                        "evidence_refs": [
                            {
                                "type": "browser_evidence",
                                "action": "snapshot",
                                "id": "bev_phase80",
                            }
                        ],
                    },
                },
                "tool_call": type(
                    "ToolCall",
                    (),
                    {
                        "tool_call_id": "call_phase80_browser",
                        "risk_level": type("Risk", (), {"value": "R2"})(),
                        "status": "completed",
                    },
                )(),
                "approval": None,
                "artifacts": [],
            },
        )()

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase80-browser-success",
        "帮我看一下这个网页讲了什么：https://example.test/phase80",
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    payload = completed["payload"]["response_plan"]["structured_payload"]

    assert "tool.completed" in {event["event"] for event in events}
    assert payload["route_semantics"]["tool_loop"] is True
    assert payload["route_semantics"]["tool_name"] == "browser.snapshot"
    assert payload["tool_result_context"]["status"] == "completed_with_evidence"
    assert payload["action_status_semantics"]["status"] == "completed_with_evidence"
    assert payload["tool_result_context"]["trusted_level"] == "untrusted_external_content"
    assert payload["tool_result_context"]["evidence_refs"][0]["browser_evidence_id"] == "bev_phase80"
    assert payload["browser_read_page"]["title"] == "Phase80 页面"


def test_phase80_terminal_readonly_approval_pending_stays_honest(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request: Any, trace_id: str | None = None) -> Any:
        del trace_id
        assert request.tool_name == "terminal.run"
        return type(
            "ToolResponse",
            (),
            {
                "result": {
                    "approval_state": {
                        "status": "required",
                        "approval_id": "apr_phase80_terminal",
                    },
                    "execution_semantics": {
                        "lane": "readonly",
                        "command_class": "readonly",
                    },
                    "retryable": False,
                },
                "tool_call": type(
                    "ToolCall",
                    (),
                    {
                        "tool_call_id": "call_phase80_terminal_pending",
                        "risk_level": type("Risk", (), {"value": "R4"})(),
                        "status": "approval_required",
                    },
                )(),
                "approval": type(
                    "Approval",
                    (),
                    {
                        "approval_id": "apr_phase80_terminal",
                        "summary": "需要确认后才能执行",
                        "model_dump": lambda self, mode="json": {
                            "approval_id": "apr_phase80_terminal",
                            "status": "required",
                            "summary": "需要确认后才能执行",
                        },
                    },
                )(),
                "artifacts": [],
            },
        )()

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase80-terminal-approval",
        '执行命令: "dir"',
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    plan = completed["payload"]["response_plan"]
    payload = plan["structured_payload"]
    final_text = plan["plain_text"]

    assert "tool.completed" not in {event["event"] for event in events}
    assert any(event["event"] == "task.created" for event in events)
    assert payload["tool_result_context"]["status"] == "waiting_for_approval"
    assert payload["action_status_semantics"]["status"] == "waiting_for_approval"
    assert payload["tool_result_context"]["approval_state"]["status"] == "required"
    assert payload["route_semantics"]["tool_loop"] is True
    assert payload["approval_prompt"]["approval_id"] == "apr_phase80_terminal"
    assert "还没有执行" in final_text or "等待确认" in final_text
    assert _assistant_message(client, conversation_id, created["turn_id"])["content_text"] == final_text


def test_phase80_terminal_readonly_failure_does_not_claim_completion(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request: Any, trace_id: str | None = None) -> Any:
        del request, trace_id
        raise AppError(
            ErrorCode.TOOL_TIMEOUT,
            "终端执行超时",
            status_code=504,
            details={"reason": "timeout while executing terminal command"},
        )

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase80-terminal-timeout",
        '执行命令: "dir"',
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    payload = completed["payload"]["response_plan"]["structured_payload"]
    final_text = completed["payload"]["response_plan"]["plain_text"]

    assert "tool.completed" not in {event["event"] for event in events}
    assert payload["terminal_route"]["status"] == "failed_with_reason"
    assert (
        "没有执行" in final_text
        or "没执行" in final_text
        or "没有拿到结果" in final_text
    )
    assert "已经处理好了" not in final_text


def test_phase80_readiness_reports_tool_loop_contracts(client: TestClient) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness")
    assert readiness.status_code == 200, readiness.text
    phase80 = readiness.json()["phase_readiness"]["phase80_tool_loop"]

    assert phase80["status"] in {"ready", "partial"}
    assert "apps/local-api/tests/test_phase80_chat_tool_loop.py" in phase80["source_of_truth"]
    assert phase80["next_owner_module"].endswith("/chat_direct_routes_runtime.py")


def test_phase80_chat_tool_loop_propagates_turn_context_into_hook_ledger(
    client: TestClient,
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_dispatch(request: Any, trace_id: str | None = None) -> Any:
        captured["turn_id"] = request.turn_id
        captured["conversation_id"] = request.conversation_id
        captured["session_id"] = request.session_id
        captured["channel"] = request.channel
        return type(
            "ToolResponse",
            (),
            {
                "result": {
                    "title": "Phase80 Hook Ledger",
                    "url": "https://example.test/phase80-hook",
                    "http_status": 200,
                    "browser_evidence_id": "bev_phase80_hook",
                    "content_preview": "<html><body><h1>Phase80 Hook Ledger</h1></body></html>",
                    "browser_page_state": {
                        "status": "observed",
                        "page_title": "Phase80 Hook Ledger",
                        "evidence_refs": [
                            {
                                "type": "browser_evidence",
                                "action": "snapshot",
                                "id": "bev_phase80_hook",
                            }
                        ],
                    },
                },
                "tool_call": type(
                    "ToolCall",
                    (),
                    {
                        "tool_call_id": "call_phase80_hook_ledger",
                        "risk_level": type("Risk", (), {"value": "R2"})(),
                        "status": "completed",
                    },
                )(),
                "approval": None,
                "artifacts": [],
            },
        )()

    monkeypatch.setattr(client.app.state.registry.tool_runtime._dispatcher, "execute", fake_dispatch)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase80-hook-ledger",
        "帮我看一下这个网页讲了什么：https://example.test/phase80-hook",
    )
    client.get(created["stream_url"])

    assert captured["turn_id"] == created["turn_id"]
    assert captured["conversation_id"] == conversation_id
    assert captured["session_id"] == "phase80-hook-ledger"
    assert captured["channel"] == "local"

    registry = client.app.state.registry
    run_ledgers = anyio.run(registry.chat.list_run_ledgers, created["turn_id"])
    event_types = {item["event_type"] for item in run_ledgers}
    assert "hook.before_tool_call" in event_types
    assert "hook.after_tool_call" in event_types


def _create_turn(
    client: TestClient,
    conversation_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _conversation_id(client: TestClient) -> str:
    return str(client.get("/api/chat/conversations").json()["items"][0]["conversation_id"])


def _assistant_message(
    client: TestClient,
    conversation_id: str,
    turn_id: str,
) -> dict[str, Any]:
    detail = client.get(f"/api/chat/conversations/{conversation_id}").json()
    messages = detail["messages"]
    assistant_messages = [
        message
        for message in messages
        if message.get("turn_id") == turn_id and message.get("author_type") == "assistant"
    ]
    assert assistant_messages
    return assistant_messages[-1]


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
