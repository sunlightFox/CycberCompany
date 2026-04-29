from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_phase12_runtime_contracts_eval_and_prompt_cleanup(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    source = Path("apps/local-api/app/services/chat.py").read_text(encoding="utf-8")

    assert by_name["ChatExperienceService"]["status"] == "implemented"
    assert by_name["ResponseComposer"]["status"] == "implemented"
    assert "suite_phase12_chat_experience" in {item["suite_id"] for item in suites}
    assert "第二阶段不能" not in source
    assert "phase two" not in source.lower()


def test_phase12_clarification_short_circuits_ambiguous_high_risk_action(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase12-clarify",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "帮我删除那个文件"},
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    context_ready = next(event for event in events if event["event"] == "context.ready")
    completed = next(event for event in events if event["event"] == "response.completed")
    decision = completed["payload"]["response_plan"]["structured_payload"][
        "clarification_decision"
    ]
    fetched = client.get(f"/api/chat/turns/{turn['turn_id']}/clarification").json()

    assert "task.created" not in {event["event"] for event in events}
    assert "selection_reason" in context_ready["payload"]
    assert context_ready["payload"]["route_profile"] == "tool_or_task"
    assert decision["needs_clarification"] is True
    assert 1 <= len(decision["questions"]) <= 3
    assert fetched["reason"] == "filesystem_scope_missing"
    assert "已删除" not in completed["payload"]["response_plan"]["plain_text"]


def test_phase12_scoped_tool_request_enters_task_and_plan_only_request_stays_direct(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    scoped = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase12-scoped-task",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "请删除 data/tmp.txt"},
        },
    ).json()
    scoped_events = _parse_sse(client.get(scoped["stream_url"]).text)
    scoped_clarification = client.get(
        f"/api/chat/turns/{scoped['turn_id']}/clarification"
    ).json()

    plan_only = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase12-plan-only",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "只生成方案，不要执行：如何删除那个文件"},
        },
    ).json()
    plan_events = _parse_sse(client.get(plan_only["stream_url"]).text)
    plan_detail = client.get(f"/api/chat/turns/{plan_only['turn_id']}").json()

    assert scoped_clarification["needs_clarification"] is False
    assert "task.created" in {event["event"] for event in scoped_events}
    assert "task.created" not in {event["event"] for event in plan_events}
    assert plan_detail["experience"]["route_profile"] in {"simple_qa", "privacy_sensitive"}


def test_phase12_working_state_supports_continue_and_context_reason(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    first = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase12-state-1",
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
        f"/api/chat/conversations/{conversation['conversation_id']}/working-state"
    ).json()

    second = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase12-state-2",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "继续优化刚才方案"},
        },
    ).json()
    client.get(second["stream_url"])
    second_detail = client.get(f"/api/chat/turns/{second['turn_id']}").json()
    updated_state = client.get(
        f"/api/chat/conversations/{conversation['conversation_id']}/working-state"
    ).json()

    assert "聊天体验" in state["active_topic"]
    assert state["known_constraints"]
    assert updated_state["active_topic"] == state["active_topic"]
    assert "working_state" in second_detail["experience"]["context_selection_reason"]


def test_phase12_pending_confirmation_clears_when_user_answers(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    first = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase12-pending-1",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "帮我整理文件夹"},
        },
    ).json()
    client.get(first["stream_url"])
    pending = client.get(
        f"/api/chat/conversations/{conversation['conversation_id']}/working-state"
    ).json()

    second = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase12-pending-2",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "只生成方案，不要执行"},
        },
    ).json()
    client.get(second["stream_url"])
    cleared = client.get(
        f"/api/chat/conversations/{conversation['conversation_id']}/working-state"
    ).json()

    assert pending["pending_confirmation"]["questions"]
    assert cleared["pending_confirmation"] == {}
    assert cleared["open_questions"] == []


def test_phase12_failure_and_retry_are_recoverable(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    failed = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase12-retry",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "你好，简单聊两句"},
        },
    ).json()
    failed_events = _parse_sse(client.get(failed["stream_url"]).text)
    failed_detail = client.get(f"/api/chat/turns/{failed['turn_id']}").json()
    retry = client.post(f"/api/chat/turns/{failed['turn_id']}/retry").json()
    client.get(retry["stream_url"])
    retry_detail = client.get(f"/api/chat/turns/{retry['turn_id']}").json()

    assert failed_events[-1]["event"] == "turn.failed"
    assert failed_events[-1]["payload"]["response_plan"]["structured_payload"]["recoverable"]
    assert failed_detail["experience"]["recoverable"] is True
    assert failed_detail["experience"]["suggested_next_actions"]
    assert f"retry_of_turn_id:{failed['turn_id']}" in retry_detail["experience"][
        "context_selection_reason"
    ]


def test_phase12_release_report_contains_chat_experience_summary(client: TestClient) -> None:
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert completed["blocker_count"] == 0
    assert report["decision"] == "go"
    assert report["summary"]["phase12"]["working_state_table"] is True
    assert report["summary"]["phase12"]["clarification_table"] is True


def _parse_sse(raw: str) -> list[dict]:
    events: list[dict] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
