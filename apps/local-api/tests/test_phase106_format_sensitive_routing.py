from __future__ import annotations

import json
from typing import Any

from app.services import chat as chat_module
from app.services.brain_route_decider import intent_decision
from app.services.channel_stream_bridge import ChannelStreamBridge
from app.services.chat_intent_router import ChatIntentRouter
from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from fastapi.testclient import TestClient


async def _phase106_stream_chat(
    self: Any,
    request: ModelChatRequest,
    cancel_token: CancelToken,
):
    del self, cancel_token
    prompt = str(request.messages[-1]["content"])
    lowered = prompt.lower()
    if "skill" in lowered and "mcp" in lowered and "json" in lowered:
        reply = '{"skill":"productized capability bundle","mcp":"tool/service protocol bridge"}'
    elif "word" in lowered and "excel" in lowered and "powerpoint" in lowered:
        reply = (
            "| Tool | Best fit |\n"
            "| --- | --- |\n"
            "| Word | Long-form writing and reports |\n"
            "| Excel | Data analysis and tabular calculation |\n"
            "| PowerPoint | Presentation decks and briefings |"
        )
    elif "code only" in lowered and "python" in lowered:
        reply = 'def phase106_answer() -> str:\n    return "format-stable"'
    elif "plain text only" in lowered and "routing isolation" in lowered:
        reply = "Routing isolation stays on the main chat chain."
    else:
        reply = "phase106"
    yield ModelStreamEvent(event="started")
    yield ModelStreamEvent(event="delta", text=reply)
    yield ModelStreamEvent(event="completed", usage={"output_tokens": len(reply)})


def test_phase106_format_sensitive_skill_mcp_request_stays_on_chat_chain() -> None:
    decision = ChatIntentRouter().decide("只输出 JSON，解释 Skill 和 MCP 的区别。")

    assert decision.route_type == "default"
    assert decision.reason_code == "fallback_to_existing_chat_chain"


def test_phase106_format_sensitive_office_comparison_does_not_hard_route_office() -> None:
    decision = ChatIntentRouter().decide(
        "用表格比较 Word、Excel 和 PowerPoint 各自适合什么场景。"
    )

    assert decision.route_type == "default"
    assert decision.office_request is None


def test_phase106_format_sensitive_skill_mcp_request_stays_direct_in_brain_intent() -> None:
    decision = intent_decision(
        "Only output JSON to explain the difference between Skill and MCP.",
        "low",
        capability_snapshot={},
    )

    assert decision.primary_intent == "simple_question"
    assert "format_sensitive_skill_mcp_explanation" in decision.reason_codes
    assert decision.execution_policy == "no_task"


def test_phase106_format_sensitive_requests_preserve_json_and_table_without_route_pollution(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", _phase106_stream_chat)
    brain_id = _create_local_brain(client)
    client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    conversation_id = _conversation_id(client)

    created_json = _create_turn(
        client,
        conversation_id,
        "phase106-json",
        "Only output JSON to explain the difference between Skill and MCP.",
    )
    json_events = _parse_sse(client.get(created_json["stream_url"]).text)
    json_completed = next(item for item in json_events if item["event"] == "response.completed")
    json_plan = json_completed["payload"]["response_plan"]

    assert json_plan["plain_text"].startswith("{")
    assert json_plan["plain_text"].endswith("}")
    assert "productized capability bundle" in json_plan["plain_text"]
    assert json_plan["structured_payload"]["response_quality_guard"]["strict_format_preserved"] is True

    created_table = _create_turn(
        client,
        conversation_id,
        "phase106-table",
        "Use a table to compare when Word, Excel, and PowerPoint fit best.",
    )
    table_events = _parse_sse(client.get(created_table["stream_url"]).text)
    table_completed = next(item for item in table_events if item["event"] == "response.completed")
    table_plan = table_completed["payload"]["response_plan"]
    plain_text = table_plan["plain_text"]

    assert plain_text.startswith("| Tool |")
    assert "| Word |" in plain_text
    assert "| Excel |" in plain_text
    assert "| PowerPoint |" in plain_text
    assert table_plan["structured_payload"]["response_quality_guard"]["strict_format_preserved"] is True


def test_phase106_response_plan_and_channel_bridge_keep_code_and_plain_text_consistent(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", _phase106_stream_chat)
    brain_id = _create_local_brain(client)
    client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    conversation_id = _conversation_id(client)

    created_code = _create_turn(
        client,
        conversation_id,
        "phase106-code",
        "Code only in Python to show a tiny routing isolation example.",
    )
    code_events = _parse_sse(client.get(created_code["stream_url"]).text)
    code_completed = next(item for item in code_events if item["event"] == "response.completed")
    code_plan = code_completed["payload"]["response_plan"]
    code_text = code_plan["plain_text"]

    assert code_text.startswith("def phase106_answer()")
    assert 'return "format-stable"' in code_text
    assert code_plan["structured_payload"]["response_quality_guard"]["strict_format_preserved"] is True

    created_plain = _create_turn(
        client,
        conversation_id,
        "phase106-plain",
        "Plain text only: explain routing isolation in one sentence.",
    )
    plain_events = _parse_sse(client.get(created_plain["stream_url"]).text)
    plain_completed = next(item for item in plain_events if item["event"] == "response.completed")
    plain_plan = plain_completed["payload"]["response_plan"]
    plain_text = plain_plan["plain_text"]

    assert plain_text == "Routing isolation stays on the main chat chain."
    assert plain_plan["structured_payload"]["response_quality_guard"]["strict_format_preserved"] is True

    assistant_message = _assistant_message(client, conversation_id, created_plain["turn_id"])
    delivery = ChannelStreamBridge().deliver_chat_events(assistant_message)

    assert assistant_message["content"]["response_plan"]["plain_text"] == plain_text
    assert assistant_message["content_text"] == plain_text
    assert delivery["plain_text"] == plain_text
    assert delivery["final_text_source"] == "response_plan_plain_text"
    assert delivery["fallback_used"] is False


def _create_local_brain(client: TestClient) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": "Phase106 local brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "phase106-test-model",
            "is_local": True,
            "context_window": 4096,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["brain_id"])


def _conversation_id(client: TestClient) -> str:
    response = client.get("/api/chat/conversations")
    assert response.status_code == 200, response.text
    return str(response.json()["items"][0]["conversation_id"])


def _create_turn(
    client: TestClient,
    conversation_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/chat/turn",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": conversation_id,
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _assistant_message(
    client: TestClient,
    conversation_id: str,
    turn_id: str,
) -> dict[str, Any]:
    detail = client.get(f"/api/chat/conversations/{conversation_id}").json()
    assistant_messages = [
        message
        for message in detail["messages"]
        if message.get("turn_id") == turn_id and message.get("author_type") == "assistant"
    ]
    assert assistant_messages
    return assistant_messages[-1]
