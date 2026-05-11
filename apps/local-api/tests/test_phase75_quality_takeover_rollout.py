from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from response_composer import ResponseComposer

from app.services.chat import _presence_rollout_state
from app.services.context_budget import ContextBudgetService
from app.services.context_visibility import ContextVisibilityService


def test_phase75_context_budget_keeps_latest_history_and_reports_trim() -> None:
    service = ContextBudgetService()
    messages = [
        {
            "author_type": "user",
            "content_text": "第一条很长的历史内容 会被预算裁掉",
            "created_at": "2026-05-10T00:00:00Z",
        },
        {
            "author_type": "assistant",
            "content_text": "第二条历史内容 也比较长",
            "created_at": "2026-05-10T00:00:01Z",
        },
        {
            "author_type": "user",
            "content_text": "第三条要尽量保留",
            "created_at": "2026-05-10T00:00:02Z",
        },
        {
            "author_type": "assistant",
            "content_text": "第四条最近消息一定保留",
            "created_at": "2026-05-10T00:00:03Z",
        },
    ]

    selected, summary = service.select_recent_messages(
        messages,
        token_budget=7,
        estimate_tokens=lambda text: len(str(text).split()),
    )

    assert summary["current_message_priority_preserved"] is True
    assert summary["selected_count"] >= 2
    assert "current_message_first" in summary["reason_codes"]
    assert selected[-1]["content_text"] == "第四条最近消息一定保留"
    assert selected[-2]["content_text"] == "第三条要尽量保留"


def test_phase75_context_visibility_prefers_same_session_and_marks_untrusted_defaults() -> None:
    service = ContextVisibilityService()
    messages = [
        {
            "author_type": "assistant",
            "content_text": "old-session",
            "created_at": "2026-05-10T00:00:00Z",
            "session_id": "session-old",
        },
        {
            "author_type": "user",
            "content_text": "same-session",
            "created_at": "2026-05-10T00:00:01Z",
            "session_id": "session-now",
        },
    ]
    user_message = {"content": {"session_id": "session-now"}}

    selected, summary = service.filter_recent_messages(messages, user_message=user_message)

    assert len(selected) == 1
    assert selected[0]["content_text"] == "same-session"
    assert summary["same_session_only"] is True
    assert "cross_session_history_filtered" in summary["reason_codes"]
    assert "tool_result_verbatim" in summary["untrusted_defaults"]


def test_phase75_rollout_marks_low_risk_chat_as_soft_control() -> None:
    state = _presence_rollout_state(
        understanding={"conversation_mode": "deep_talk"},
        response_policy={"opening_style": "judgment_first"},
        action_dialogue={"action_status": "no_action"},
        user_text="对比聊天质量方案的差异。",
    )

    assert state["advisory_mode"] == "soft_control"
    assert state["quality_takeover_scope"] == "low_risk_chat"
    assert state["fallback_reason_codes"] == []


def test_phase75_rollout_guards_action_semantics_for_strict_format() -> None:
    state = _presence_rollout_state(
        understanding={"conversation_mode": "task_request"},
        response_policy={"boundary_mode": "explicit_honest"},
        action_dialogue={"action_status": "waiting_for_approval"},
        user_text="只输出 JSON，确认后再执行。",
    )

    assert state["advisory_mode"] == "advisory"
    assert state["quality_takeover_scope"] == "none"
    assert "strict_format_guard" in state["fallback_reason_codes"]
    assert "action_semantics_guarded" in state["fallback_reason_codes"]


def test_phase75_response_policy_only_applies_action_semantics_when_scope_allows() -> None:
    composer = ResponseComposer()

    low_risk = composer.response_plan_for_status(
        summary="当前状态：需要确认。",
        response_policy={"quality_takeover_scope": "low_risk_chat"},
        action_dialogue={"action_status": "waiting_for_approval"},
    )
    action_semantics = composer.response_plan_for_status(
        summary="当前状态：需要确认。",
        response_policy={"quality_takeover_scope": "action_semantics"},
        action_dialogue={"action_status": "pending_approval"},
    )

    assert "先等你点头" not in low_risk.plain_text
    assert "先等你点头" in action_semantics.plain_text


def test_phase75_turn_payload_contains_shadow_and_presence_runtime_without_confusion(
    client: TestClient,
) -> None:
    created = _create_turn(
        client,
        "phase75-rollout-low-risk",
        "对比闲聊、任务、工具三种回复风格的差异。",
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    plan = completed["payload"]["response_plan"]
    payload = plan["structured_payload"]
    presence_runtime = payload["presence_runtime"]

    assert "chat_quality_shadow" in payload
    assert presence_runtime["advisory_mode"] == "soft_control"
    assert presence_runtime["quality_takeover_scope"] == "low_risk_chat"
    assert presence_runtime["heuristic_governance"]["soft_heuristics_do_not_terminate_mainline"] is True
    assert presence_runtime["heuristic_inventory"]
    assert presence_runtime["context_budget"]["current_message_priority_preserved"] is True
    assert "tool_result_verbatim" in presence_runtime["context_visibility"]["untrusted_defaults"]
    assert payload["chat_quality_shadow"]["advisory_only"] is True


def test_phase75_boundary_turn_stays_advisory_with_fallback_reasons(
    client: TestClient,
) -> None:
    created = _create_turn(
        client,
        "phase75-boundary",
        "你是真人吗？有没有隐藏账号能直接帮我登录？",
    )

    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    presence_runtime = completed["payload"]["response_plan"]["structured_payload"]["presence_runtime"]

    assert presence_runtime["quality_takeover_scope"] == "none"
    assert presence_runtime["advisory_mode"] == "advisory"
    assert "boundary_scene_excluded" in presence_runtime["fallback_reason_codes"]


def _create_turn(
    client: TestClient,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/chat/turn",
        json={
            "member_id": "mem_xiaoyao",
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
