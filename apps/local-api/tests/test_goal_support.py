from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from fastapi.testclient import TestClient


def test_goal_support_api_plan_supervision_checkin_flow(client: TestClient) -> None:
    created = client.post(
        "/api/goals",
        json={
            "conversation_id": _conversation_id(client),
            "owner_member_id": "mem_xiaoyao",
            "description": "我要健身，给我制定一个健身计划。",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    goal = body["goal"]
    plan = body["active_plan"]

    assert goal["status"] == "awaiting_confirmation"
    assert goal["domain_label"] == "fitness"
    assert plan["status"] == "proposed"
    assert len(body["plan_items"]) >= 4

    confirmed = client.post(
        f"/api/goals/{goal['goal_id']}/plans/{plan['goal_plan_id']}/confirm",
        json={
            "start_supervision": True,
            "supervision": {"schedule": {"type": "daily", "time": "20:00"}},
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    detail = confirmed.json()
    policy = detail["supervision_policy"]
    assert detail["goal"]["status"] == "active"
    assert detail["active_plan"]["status"] == "confirmed"
    assert policy["status"] == "active"
    assert policy["scheduled_task_id"]

    scheduled = client.get(f"/api/scheduled-tasks/{policy['scheduled_task_id']}").json()
    assert scheduled["constraints"]["purpose"] == "goal_checkin"
    assert scheduled["constraints"]["goal_id"] == goal["goal_id"]

    checkin = client.post(
        f"/api/goals/{goal['goal_id']}/checkins",
        json={"prompt_text": "今天完成了吗？"},
    ).json()
    progress = client.post(
        f"/api/goals/{goal['goal_id']}/checkins/{checkin['checkin_id']}/reply",
        json={"reply_text": "做了一半，今天时间不太够。"},
    ).json()

    assert progress["partial_count"] == 1
    assert progress["progress_percent"] >= 5
    assert "健身" in progress["summary"]

    updated_detail = client.get(f"/api/goals/{goal['goal_id']}").json()
    statuses = {item["item_type"]: item["status"] for item in updated_detail["plan_items"]}
    assert statuses["planning"] == "completed"
    assert statuses["routine"] == "in_progress"
    assert statuses["checkin"] == "in_progress"


def test_goal_supervision_random_jitter_policy(client: TestClient) -> None:
    created = client.post(
        "/api/goals",
        json={
            "conversation_id": _conversation_id(client),
            "owner_member_id": "mem_xiaoyao",
            "description": "我要考证，给我制定一个备考计划。",
        },
    ).json()
    goal = created["goal"]
    plan = created["active_plan"]
    base_run = datetime(2099, 1, 1, 12, 0, tzinfo=UTC)

    confirmed = client.post(
        f"/api/goals/{goal['goal_id']}/plans/{plan['goal_plan_id']}/confirm",
        json={
            "start_supervision": True,
            "supervision": {
                "mode": "random_checkin",
                "random_jitter_minutes": 30,
                "schedule": {
                    "type": "once",
                    "run_at": base_run.isoformat(),
                    "timezone": "Asia/Shanghai",
                },
            },
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    policy = confirmed.json()["supervision_policy"]
    scheduled = client.get(f"/api/scheduled-tasks/{policy['scheduled_task_id']}").json()
    next_run_at = datetime.fromisoformat(scheduled["next_run_at"])

    assert policy["mode"] == "random_checkin"
    assert policy["frequency"]["random_jitter_minutes"] == 30
    assert scheduled["constraints"]["random_jitter_minutes"] == 30
    assert base_run <= next_run_at <= base_run + timedelta(minutes=30)


def test_goal_pause_resume_cancel_state_boundaries(client: TestClient) -> None:
    goal_id, policy = _create_active_goal_with_supervision(client)

    paused = client.post(f"/api/goals/{goal_id}/pause", json={"reason": "休息一天"})
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "paused"
    assert client.get(f"/api/scheduled-tasks/{policy['scheduled_task_id']}").json()["status"] == (
        "paused"
    )

    resumed = client.post(f"/api/goals/{goal_id}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "active"

    cancelled = client.post(f"/api/goals/{goal_id}/cancel", json={"reason": "目标变更"})
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/scheduled-tasks/{policy['scheduled_task_id']}").json()["status"] == (
        "cancelled"
    )

    rejected = client.post(f"/api/goals/{goal_id}/resume")
    assert rejected.status_code == 409


def test_goal_support_runtime_contracts(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}

    assert by_name["GoalSupportService"]["status"] == "implemented"
    assert by_name["GoalSupportService"]["details"]["domain_specific"] is False
    assert by_name["GoalSupervisionScheduler"]["details"]["random_jitter_minutes"] is True
    assert by_name["GoalProgressLoop"]["details"]["scenario_specific_templates"] is False


def test_goal_checkin_scheduled_trigger_does_not_create_normal_task(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    goal_id, policy = _create_active_goal_with_supervision(client)
    scheduled_task_id = policy["scheduled_task_id"]

    run = client.post(
        f"/api/scheduled-tasks/{scheduled_task_id}/trigger",
        json={"scheduled_for": "2026-05-25T12:00:00+00:00"},
    ).json()
    checkins = client.get(f"/api/goals/{goal_id}/checkins").json()["items"]
    messages = client.get("/api/notification/messages", params={"limit": 50}).json()["items"]

    assert run["status"] == "completed"
    assert run["task_id"] is None
    assert run["result"]["checkin_id"]
    assert any(item["scheduled_run_id"] == run["run_id"] for item in checkins)
    assert any(item.get("scheduled_run_id") == run["run_id"] for item in messages)

    due = _run_async(
        client,
        registry.scheduled_task_service.scan_due,
        now=datetime(2026, 5, 25, 12, 1, tzinfo=UTC),
    )
    assert all(item.task_id is None for item in due)


def test_goal_support_chat_flow(client: TestClient) -> None:
    conversation_id = _conversation_id(client)

    first = _chat_reply(
        client,
        conversation_id=conversation_id,
        session_id="goal-support-create",
        text="我要健身，给我制定一个健身计划。",
    )
    goals = client.get("/api/goals", params={"conversation_id": conversation_id}).json()["items"]
    goal = goals[0]

    assert "设成一个目标" in first
    assert goal["status"] == "awaiting_confirmation"

    second = _chat_reply(
        client,
        conversation_id=conversation_id,
        session_id="goal-support-confirm",
        text="可以，就按这个来，每天晚上8点提醒我。",
    )
    detail = client.get(f"/api/goals/{goal['goal_id']}").json()
    policy = detail["supervision_policy"]

    assert "开启监督" in second
    assert detail["goal"]["status"] == "active"
    assert policy["scheduled_task_id"]

    checkin = client.post(f"/api/goals/{goal['goal_id']}/checkins", json={}).json()
    third = _chat_reply(
        client,
        conversation_id=conversation_id,
        session_id="goal-support-reply",
        text="做了一半，今天没时间。",
    )
    progress = client.get(f"/api/goals/{goal['goal_id']}/progress").json()

    assert checkin["parsed_status"] == "pending"
    assert "已记录" in third
    assert progress["partial_count"] == 1
    assert progress["progress_percent"] >= 5


def _create_active_goal_with_supervision(client: TestClient) -> tuple[str, dict[str, Any]]:
    created = client.post(
        "/api/goals",
        json={
            "conversation_id": _conversation_id(client),
            "owner_member_id": "mem_xiaoyao",
            "description": "我要学英语，给我制定一个学习计划。",
        },
    ).json()
    goal = created["goal"]
    plan = created["active_plan"]
    confirmed = client.post(
        f"/api/goals/{goal['goal_id']}/plans/{plan['goal_plan_id']}/confirm",
        json={
            "start_supervision": True,
            "supervision": {
                "schedule": {
                    "type": "once",
                    "run_at": "2026-05-25T12:00:00+00:00",
                    "timezone": "Asia/Shanghai",
                }
            },
        },
    ).json()
    return goal["goal_id"], confirmed["supervision_policy"]


def _conversation_id(client: TestClient) -> str:
    return client.get("/api/chat/conversations").json()["items"][0]["conversation_id"]


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    async def runner() -> Any:
        return await func(*args, **kwargs)

    return cast(Any, client).portal.call(runner)


def _chat_reply(
    client: TestClient,
    *,
    conversation_id: str,
    text: str,
    session_id: str,
) -> str:
    created = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert created.status_code == 200, created.text
    stream = client.get(created.json()["stream_url"])
    return _reply_from_sse(stream.text)


def _reply_from_sse(raw: str) -> str:
    chunks: list[str] = []
    fallback = ""
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event.get("event") == "response.delta":
                chunks.append(str(event.get("payload", {}).get("text") or ""))
            if event.get("event") == "response.completed":
                response_plan = event.get("payload", {}).get("response_plan", {})
                fallback = str(
                    response_plan.get("plain_text") or response_plan.get("summary") or ""
                )
    return "".join(chunks).strip() or fallback.strip()
