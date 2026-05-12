from __future__ import annotations

from typing import Any, cast

import anyio
from fastapi.testclient import TestClient


def test_phase96_agent_runtime_is_authoritative_and_budget_pause_is_replayable(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase96 authoritative agent loop",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 1, "max_tool_calls": 5},
        },
    ).json()

    topology = client.get("/api/system/runtime-topology").json()["items"]
    task_runtime = next(item for item in topology if item["name"] == "task")
    loop_response = client.get(f"/api/tasks/{task['task_id']}/agent-loop").json()
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    next_actions = client.get(f"/api/tasks/{task['task_id']}/agent-next-actions").json()["items"]

    assert task["status"] == "paused"
    assert task_runtime["status"] == "runtime_native"
    assert task_runtime["details"]["agent"]["authoritative"] is True
    assert task_runtime["details"]["agent_runtime_authority"] == "task_agent_runtime"
    assert loop_response["runtime"] == "task_agent_runtime"
    assert loop_response["authoritative"] is True
    assert loop_response["pause_reason"] == "budget_exhausted"
    assert loop_response["items"]
    assert "iteration" in loop_response["items"][0]
    assert "evaluation" in loop_response["items"][0]
    assert replay["agent_loop"]["runtime"] == "task_agent_runtime"
    assert replay["agent_loop"]["pause_reason"] == "budget_exhausted"
    assert replay["agent_loop_evidence"]["authoritative"] is True
    assert any(item["next_action_type"] == "pause_for_budget" for item in next_actions)


def test_phase96_retry_on_paused_agent_resumes_via_agent_runtime(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase96 retry resume",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 1, "max_tool_calls": 5},
        },
    ).json()

    anyio.run(
        registry.tasks.update_task,
        task["task_id"],
        {
            "budget": {
                "max_steps": 20,
                "max_loop_steps": 8,
                "max_tool_calls": 5,
                "max_runtime_seconds": 1800,
                "max_model_calls": 20,
                "max_total_cost": 0.0,
                "max_artifact_bytes": 10_000_000,
            }
        },
    )
    retried = client.post(f"/api/tasks/{task['task_id']}/retry")
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    event_types = [event["event_type"] for event in replay["events"]]

    assert retried.status_code == 200, retried.text
    assert "agent.resume" in event_types
    assert replay["agent_loop"]["runtime"] == "task_agent_runtime"
    assert replay["agent_loop"]["latest_next_action"] is not None
