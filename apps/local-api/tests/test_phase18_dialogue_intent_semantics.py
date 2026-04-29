from __future__ import annotations

import json
from typing import Any, cast

import anyio
from fastapi.testclient import TestClient


def test_phase18_contracts_design_gap_and_eval_suite(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    suites_first = client.get("/api/evals/suites").json()["items"]
    suites_second = client.get("/api/evals/suites").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    suite_ids = [item["suite_id"] for item in suites_second]

    assert by_name["DialogueStateService"]["status"] == "implemented"
    assert by_name["SemanticIntentAnalyzer"]["status"] == "implemented"
    assert by_name["LowConfidenceDecisionReviewer"]["status"] == "implemented"
    assert by_name["ModelAssistedVerifier"]["status"] == "implemented_with_fallback"
    assert any(
        item["gap_id"] == "gap_phase18_model_assisted_verifier_disabled"
        and item["status"] == "accepted_risk"
        for item in gaps
    )
    assert "suite_phase18_dialogue_intent_semantics" in set(suite_ids)
    assert suite_ids.count("suite_phase18_dialogue_intent_semantics") == 1
    assert len(suites_first) == len(suites_second)


def test_phase18_decision_preview_has_no_semantic_persistence_side_effect(
    client: TestClient,
) -> None:
    before = _table_counts(
        client,
        "dialogue_states",
        "semantic_intent_candidates",
        "low_confidence_decision_reviews",
    )
    preview = _preview(client, "你好，帮我记住我喜欢短回复，顺便删除那个文件")
    after = _table_counts(
        client,
        "dialogue_states",
        "semantic_intent_candidates",
        "low_confidence_decision_reviews",
    )
    semantic = preview["semantic_intent_candidates"][0]

    assert preview["intent"]["primary_intent"] == "task_request"
    assert "memory_update" in semantic["memory_intents"]
    assert "tool_or_task_request" in semantic["tool_intents"]
    assert "destructive_action" in semantic["risk_intents"]
    assert "casual_vs_action_request" in semantic["conflicts"]
    assert preview["low_confidence_review"]["fallback_used"] is True
    assert preview["low_confidence_review"]["model_assist_enabled"] is True
    assert preview["semantic_review"]["fallback_used"] is True
    assert before == after


def test_phase18_dialogue_state_tracks_continuation_and_context_reason(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    first = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase18-dialogue-state-1",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "我们要优化聊天体验方案，先定三条原则，不要追问简单问题",
            },
        },
    ).json()
    client.get(first["stream_url"])
    state = client.get(
        f"/api/chat/conversations/{conversation['conversation_id']}/dialogue-state"
    ).json()

    second = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase18-dialogue-state-2",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "继续优化刚才方案"},
        },
    ).json()
    client.get(second["stream_url"])
    decision = client.get(f"/api/chat/turns/{second['turn_id']}/brain-decision").json()
    semantic = client.get(f"/api/chat/turns/{second['turn_id']}/semantic-intents").json()

    assert "聊天体验" in state["active_topic"]
    assert state["known_constraints"]
    assert state["topic_shift"] is False
    assert decision["intent"]["primary_intent"] == "complex_dialogue"
    assert "dialogue_state_goal_continuation" in decision["context"]["selection_reason"]
    assert "working_state_continuation" in decision["context"]["selection_reason"]
    assert semantic["items"][0]["conversation_intents"] == ["continue_previous_topic"]


def test_phase18_context_conflict_and_high_risk_create_review_and_clarification(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    seed = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase18-conflict-seed",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "我们先做知识库检索方案，决定采用本地向量"},
        },
    ).json()
    client.get(seed["stream_url"])

    conflict = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase18-conflict-turn",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "不是这个，改成只做后端，并帮我转账"},
        },
    ).json()
    events = _parse_sse(client.get(conflict["stream_url"]).text)
    decision = client.get(f"/api/chat/turns/{conflict['turn_id']}/brain-decision").json()
    semantic = client.get(f"/api/chat/turns/{conflict['turn_id']}/semantic-intents").json()
    review = client.get(
        f"/api/chat/turns/{conflict['turn_id']}/low-confidence-review"
    ).json()
    completed = next(event for event in events if event["event"] == "response.completed")
    clarification = completed["payload"]["response_plan"]["structured_payload"][
        "clarification_decision"
    ]

    assert "task.created" not in {event["event"] for event in events}
    assert decision["mode"]["mode"] == "ask_clarification"
    assert "context_conflict_detected" in decision["context"]["selection_reason"]
    assert "old_goal_vs_new_goal" in semantic["items"][0]["conflicts"]
    assert "high_risk_financial_or_signature" in semantic["items"][0]["risk_intents"]
    assert review["fallback_used"] is True
    assert "context_conflict" in review["trigger_reasons"]
    assert clarification["clarification_type"] in {"conflicting_context", "missing_destination"}
    assert 1 <= len(clarification["questions"]) <= 3


def test_phase18_goal_change_does_not_trigger_memory_retrieval_unless_explicit(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    seed = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase18-goal-change-seed",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "我们先做知识库检索方案，决定采用本地向量"},
        },
    ).json()
    client.get(seed["stream_url"])

    generic_change = _preview(client, "不是这个，改成只做后端", conversation["conversation_id"])
    explicit_memory = _preview(
        client,
        "纠正记忆：我不是喜欢长回复，改成短回复",
        conversation["conversation_id"],
    )

    assert generic_change["intent"]["primary_intent"] != "memory_correction"
    assert generic_change["intent"]["needs_memory"] is False
    assert generic_change["context"]["include_memory"] is False
    assert "memory_query_enabled" not in generic_change["context"]["selection_reason"]
    assert "context_conflict_detected" in generic_change["context"]["selection_reason"]

    assert explicit_memory["intent"]["primary_intent"] == "memory_correction"
    assert explicit_memory["mode"]["mode"] == "direct_with_memory"
    assert explicit_memory["context"]["include_memory"] is True


def test_phase18_casual_after_dialogue_state_keeps_context_small(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    seed = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase18-casual-context-seed",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "我们要优化聊天体验方案，先定三条原则"},
        },
    ).json()
    client.get(seed["stream_url"])

    casual = _preview(client, "你好", conversation["conversation_id"])

    assert casual["intent"]["primary_intent"] == "casual_chat"
    assert casual["context"]["include_conversation_state"] is False
    assert casual["context"]["include_session_summary"] is False
    assert casual["context"]["include_memory"] is False
    assert "dialogue_state_goal_continuation" not in casual["context"]["selection_reason"]


def test_phase18_capability_unavailable_uses_boundary_and_review(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase18-mcp-skill-boundary",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "调用 MCP 和技能做一下"},
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    decision = client.get(f"/api/chat/turns/{turn['turn_id']}/brain-decision").json()
    review = client.get(f"/api/chat/turns/{turn['turn_id']}/low-confidence-review").json()

    assert "task.created" not in {event["event"] for event in events}
    assert decision["mode"]["submode"] == "capability_boundary"
    assert "capability_unavailable" in decision["context"]["selection_reason"]
    assert "capability_unavailable" in review["trigger_reasons"]
    assert review["status"] == "model_assist_fallback"


def test_phase18_release_report_contains_semantic_summary(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase18-report",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "？？？"},
        },
    ).json()
    client.get(turn["stream_url"])
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert completed["blocker_count"] == 0
    assert report["decision"] == "go"
    phase18 = report["summary"]["phase18"]
    assert phase18["suite_id"] == "suite_phase18_dialogue_intent_semantics"
    assert phase18["registered_cases"] >= 10
    assert phase18["dialogue_states"] >= 1
    assert phase18["semantic_candidates"] >= 1
    assert phase18["low_confidence_reviews"] >= 1
    assert phase18["model_assist_gap"] == 1


def _preview(
    client: TestClient,
    text: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/brain/decision-preview",
        json={
            "text": text,
            "member_id": "mem_xiaoyao",
            "conversation_id": conversation_id,
            "privacy_level": "medium",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _table_counts(client: TestClient, *tables: str) -> dict[str, int]:
    registry = cast(Any, client.app).state.registry
    result: dict[str, int] = {}
    for table in tables:
        row = anyio.run(registry.db.fetch_one, f"SELECT COUNT(*) AS count FROM {table}")
        result[table] = int(row["count"])
    return result


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
