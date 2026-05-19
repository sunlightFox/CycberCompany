from __future__ import annotations

import json
from typing import Any, cast

import anyio
from fastapi.testclient import TestClient


def test_phase13_runtime_contracts_design_gap_and_eval_suite(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    by_name = {item["name"]: item for item in contracts}

    assert by_name["BrainDecisionService"]["status"] == "implemented"
    assert by_name["BrainRouter"]["status"] == "degraded"
    assert by_name["BrainRouter"]["details"]["compatibility_facade"] is True
    assert any(item["gap_id"] == "gap_brain_decision_model_assist" for item in gaps)
    assert "suite_phase13_brain_decision" in {item["suite_id"] for item in suites}


def test_phase13_decision_preview_classifies_intent_mode_and_context(
    client: TestClient,
) -> None:
    before = _brain_decision_count(client)
    casual = _preview(client, "你好")
    question = _preview(client, "这是什么？")
    memory = _preview(client, "记得我之前说过什么偏好吗")
    plan_only = _preview(client, "只给方案，不要执行：如何删除那个文件")
    destructive = _preview(client, "帮我删除那个文件")
    low_signal = _preview(client, "？？？")
    private = _preview(client, "我的 api_key 是 sk-secret，帮我解释一下", privacy_level="high")
    skill = _preview(client, "用技能帮我写一个草稿")
    mcp = _preview(client, "调用 MCP 做一下")
    desktop_files = _preview(client, "我桌面有哪些文件")
    advice_tradeoff = _preview(client, "在测试速度、覆盖率、真实模型成本之间做取舍")
    forget_preference = _preview(client, "请忘记本批次临时测试回复偏好")

    assert casual["intent"]["primary_intent"] == "casual_chat"
    assert casual["mode"]["mode"] == "direct"
    assert casual["context"]["include_memory"] is False
    assert casual["intent"]["model_hint"]["enabled"] is False

    assert question["intent"]["primary_intent"] == "simple_question"
    assert question["mode"]["mode"] == "direct"

    assert memory["intent"]["primary_intent"] == "memory_query"
    assert memory["mode"]["mode"] == "direct_with_memory"
    assert memory["context"]["include_memory"] is True
    assert "memory_query_enabled" in memory["context"]["selection_reason"]

    assert plan_only["intent"]["primary_intent"] == "simple_question"
    assert plan_only["mode"]["mode"] == "direct"
    assert plan_only["clarification"]["needs_clarification"] is False

    assert destructive["mode"]["mode"] == "ask_clarification"
    assert destructive["clarification"]["reason"] == "filesystem_scope_missing"
    assert 1 <= len(destructive["clarification"]["questions"]) <= 3

    assert low_signal["intent"]["primary_intent"] == "unknown"
    assert low_signal["mode"]["mode"] == "ask_clarification"
    assert low_signal["clarification"]["reason"] == "low_intent_confidence"

    assert "secret_or_sensitive" in private["intent"]["risk_signals"]
    assert private["mode"]["requires_approval_before_execute"] is False

    assert skill["intent"]["primary_intent"] == "skill_request"
    assert skill["intent"]["needs_task"] is False
    assert skill["mode"]["submode"] == "capability_boundary"
    assert skill["capability_snapshot"]["skill"]["enabled_count"] == 0

    assert mcp["intent"]["primary_intent"] == "mcp_request"
    assert mcp["intent"]["needs_task"] is False
    assert mcp["mode"]["submode"] == "capability_boundary"
    assert mcp["capability_snapshot"]["mcp_runtime"]["ready_server_count"] == 0
    assert desktop_files["intent"]["primary_intent"] == "system_filesystem_read"
    assert desktop_files["intent"]["needs_tool"] is True
    assert desktop_files["intent"]["needs_task"] is False
    assert desktop_files["mode"]["mode"] == "direct"
    assert desktop_files["clarification"]["needs_clarification"] is False
    assert "filesystem_scope_required" not in desktop_files["intent"]["risk_signals"]
    assert advice_tradeoff["clarification"]["needs_clarification"] is False
    assert "目标文件或范围是什么？" not in json.dumps(advice_tradeoff, ensure_ascii=False)
    assert forget_preference["clarification"]["needs_clarification"] is False
    assert "目标文件或范围是什么？" not in json.dumps(forget_preference, ensure_ascii=False)
    assert _brain_decision_count(client) == before


def test_phase13_continuation_uses_working_state_without_memory_query(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    first = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase13-continuation-seed",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "我们要优化聊天体验方案，先定三条原则，不要追问简单问题",
            },
        },
    ).json()
    client.get(first["stream_url"])

    decision = _preview(client, "继续优化刚才方案", conversation["conversation_id"])

    assert decision["intent"]["primary_intent"] == "complex_dialogue"
    assert decision["mode"]["mode"] == "direct"
    assert decision["context"]["include_conversation_state"] is True
    assert decision["context"]["include_memory"] is False
    assert "working_state_continuation" in decision["context"]["selection_reason"]


def test_phase13_preference_application_request_stays_out_of_memory_query(
    client: TestClient,
) -> None:
    decision = _preview(
        client,
        "结合我们前面 20 轮的测试，按先风险后结论的偏好，给我一个收尾结论和一个下一步。",
    )

    assert decision["intent"]["primary_intent"] != "memory_query"
    assert decision["mode"]["mode"] == "direct"
    assert decision["context"]["include_memory"] is False


def test_phase13_chat_events_and_turn_decision_are_persisted(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase13-memory-context",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "记得我之前说过什么偏好吗"},
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    context_ready = next(event for event in events if event["event"] == "context.ready")
    intent = next(event for event in events if event["event"] == "intent.detected")
    mode = next(event for event in events if event["event"] == "mode.selected")
    decision = client.get(f"/api/chat/turns/{turn['turn_id']}/brain-decision").json()
    detail = client.get(f"/api/chat/turns/{turn['turn_id']}").json()

    assert context_ready["payload"]["decision_id"] == decision["brain_decision_id"]
    assert context_ready["payload"]["context_decision"]["include_memory"] is True
    assert intent["payload"]["decision_id"] == decision["brain_decision_id"]
    assert intent["payload"]["intent"] == "memory_query"
    assert mode["payload"]["mode"] == "direct_with_memory"
    assert mode["payload"]["decision_id"] == decision["brain_decision_id"]
    assert detail["brain_decision_id"] == decision["brain_decision_id"]


def test_phase13_risk_decision_clarifies_before_task_and_scoped_action_creates_task(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    ambiguous = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase13-ambiguous-delete",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "帮我删除那个文件"},
        },
    ).json()
    ambiguous_events = _parse_sse(client.get(ambiguous["stream_url"]).text)
    ambiguous_decision = client.get(
        f"/api/chat/turns/{ambiguous['turn_id']}/brain-decision"
    ).json()

    scoped = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase13-scoped-delete",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "请删除 data/tmp.txt"},
        },
    ).json()
    scoped_events = _parse_sse(client.get(scoped["stream_url"]).text)
    scoped_decision = client.get(f"/api/chat/turns/{scoped['turn_id']}/brain-decision").json()

    assert "task.created" not in {event["event"] for event in ambiguous_events}
    assert ambiguous_decision["mode"]["mode"] == "ask_clarification"
    assert ambiguous_decision["clarification"]["reason"] == "filesystem_scope_missing"
    assert "task.created" in {event["event"] for event in scoped_events}
    assert scoped_decision["intent"]["primary_intent"] == "task_request"
    assert scoped_decision["mode"]["mode"] == "workflow"


def test_phase13_unavailable_mcp_stays_at_tool_boundary_without_task(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase13-mcp-boundary",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "调用 MCP 做一下"},
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    decision = client.get(f"/api/chat/turns/{turn['turn_id']}/brain-decision").json()
    completed = next(event for event in events if event["event"] == "response.completed")

    assert "task.created" not in {event["event"] for event in events}
    assert decision["mode"]["submode"] == "capability_boundary"
    assert decision["capability_snapshot"]["mcp_runtime"]["available"] is False
    assert completed["payload"]["response_plan"]["style"] == "tool_boundary"
    assert "没有执行任何外部动作" in completed["payload"]["response_plan"]["safety_notice"]


def test_phase13_release_report_contains_brain_decision_summary(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase13-report-decision",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "调用 MCP 做一下"},
        },
    ).json()
    client.get(turn["stream_url"])
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert completed["blocker_count"] == 0
    assert report["decision"] == "go"
    assert report["summary"]["phase13"]["brain_decision_table"] is True
    assert report["summary"]["phase13"]["brain_decision_contract"] == 1
    assert report["summary"]["phase13"]["decision_logs"] >= 1
    assert report["summary"]["phase13"]["turn_decision_logs"] >= 1
    assert "capability_boundary_decisions" in report["summary"]["phase13"]
    assert "working_state_continuations" in report["summary"]["phase13"]


def _preview(
    client: TestClient,
    text: str,
    conversation_id: str | None = None,
    *,
    privacy_level: str = "medium",
) -> dict:
    response = client.post(
        "/api/brain/decision-preview",
        json={
            "text": text,
            "member_id": "mem_xiaoyao",
            "conversation_id": conversation_id,
            "privacy_level": privacy_level,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _brain_decision_count(client: TestClient) -> int:
    registry = cast(Any, client.app).state.registry
    row = anyio.run(
        registry.db.fetch_one,
        "SELECT COUNT(*) AS count FROM brain_decision_logs",
    )
    return int(row["count"])


def _parse_sse(raw: str) -> list[dict]:
    events: list[dict] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
