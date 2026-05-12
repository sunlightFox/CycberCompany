from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase96_replay_agentloop_contract_is_iteration_first(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase96 replay contract",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 8, "max_tool_calls": 5},
        },
    ).json()

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    loop_response = client.get(f"/api/tasks/{task['task_id']}/agent-loop").json()
    observations = client.get(f"/api/tasks/{task['task_id']}/observations").json()
    next_actions = client.get(f"/api/tasks/{task['task_id']}/agent-next-actions").json()

    assert replay["agent_loop"]["runtime"] == "task_agent_runtime"
    assert replay["agent_loop"]["authoritative"] is True
    assert replay["workflow_evidence"]["mode"] == "agent"
    assert "iteration_count" in replay["agent_loop_evidence"]
    assert "retry_plan_count" in replay["recovery_evidence"]
    assert "handoff_count" in replay["handoff_evidence"]
    assert replay["agent_loop"]["iterations"]
    frame = replay["agent_loop"]["iterations"][0]
    assert "iteration" in frame
    assert "selected_action" in frame
    assert "evaluation" in frame
    assert loop_response["items"][0]["iteration"]["loop_index"] == frame["iteration"]["loop_index"]
    assert observations["runtime"] == "task_agent_runtime"
    assert observations["task_id"] == task["task_id"]
    assert next_actions["runtime"] == "task_agent_runtime"
    assert next_actions["task_id"] == task["task_id"]
