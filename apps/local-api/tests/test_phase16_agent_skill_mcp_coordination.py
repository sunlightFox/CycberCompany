from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase16_runtime_contracts_and_eval_suite(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_module = {item["name"]: item for item in contracts}

    assert by_module["TaskPlannerService"]["status"] == "implemented"
    assert by_module["AgentLoopRunner"]["status"] == "implemented"
    assert by_module["TaskObservationService"]["status"] == "implemented"
    assert by_module["TaskReflectionService"]["status"] == "implemented"
    assert by_module["ModelPlanner"]["status"] == "implemented"
    assert by_module["ModelPlanner"]["details"]["model_assist"] is False

    gaps = client.get("/api/system/design-gaps").json()["items"]
    assert any(item["gap_id"] == "gap_model_planner_assist_disabled" for item in gaps)

    suites = client.get("/api/evals/suites").json()["items"]
    assert any(
        item["suite_id"] == "suite_phase16_agent_skill_mcp_coordination" for item in suites
    )


def test_phase16_workflow_task_records_planner_without_agent_loop(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "整理一个后端验收清单", "mode_hint": "workflow", "auto_start": True},
    ).json()

    assert task["mode"] == "workflow"
    assert task["status"] == "completed"

    decisions = client.get(f"/api/tasks/{task['task_id']}/planner-decisions").json()["items"]
    loops = client.get(f"/api/tasks/{task['task_id']}/agent-loop").json()["items"]
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert decisions
    assert decisions[0]["planner_type"] == "workflow_template_planner"
    assert decisions[0]["selected_mode"] == "workflow"
    assert decisions[0]["model_hint"]["enabled"] is False
    assert loops == []
    assert replay["planner_decisions"]


def test_phase16_agent_budget_stop_creates_observation_and_retry_plan(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase16 budget stop evidence",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 1, "max_tool_calls": 5},
        },
    ).json()

    assert task["mode"] == "agent"
    assert task["status"] == "paused"
    assert task["failure_reason"] == "budget_exhausted"

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    assert replay["planner_decisions"]
    assert replay["agent_loop_iterations"]
    assert replay["observations"]
    assert replay["retry_plans"]
    assert replay["reflection_candidates"]
    assert replay["retry_plans"][0]["reason"] == "budget_exhausted"
    assert any(
        iteration["stop_reason"] == "budget_exhausted"
        for iteration in replay["agent_loop_iterations"]
    )
    assert any(
        observation["source_type"] == "agent_budget" for observation in replay["observations"]
    )
    assert replay["final_result"]["stop_reason"] == "budget_exhausted"


def test_phase16_agent_replay_contains_loop_observations_and_reflection(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase16 backend evidence",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 8, "max_tool_calls": 5},
        },
    ).json()

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    event_types = {event["event_type"] for event in replay["events"]}

    assert task["status"] == "completed"
    assert "agent.loop_started" in event_types
    assert "agent.iteration_completed" in event_types
    assert "agent.stopped" in event_types
    assert replay["agent_loop_iterations"]
    assert replay["observations"]
    assert any(
        item["candidate_type"] in {"memory_candidate", "skill_candidate"}
        for item in replay["reflection_candidates"]
    )
    assert replay["final_result"]["stop_reason"] == "completed"


def test_phase16_direct_task_mode_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/tasks",
        json={"goal": "只聊聊，不创建任务", "mode_hint": "direct", "auto_start": False},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TASK_PLAN_FAILED"


def test_phase16_unavailable_mcp_is_planner_boundary_not_fake_call(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "调用 MCP 做一下",
            "mode_hint": "workflow",
            "constraints": {"mcp_tool_name": "missing.tool"},
            "auto_start": True,
        },
    ).json()
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert task["status"] == "completed"
    assert not replay["mcp_calls"]
    decision = replay["planner_decisions"][0]
    plan = client.get(f"/api/tasks/{task['task_id']}/plan").json()["plan"]
    assert "mcp_tool_unavailable_removed_from_plan" in decision["reason_codes"]
    assert (
        "mcp_no_ready_server" in decision["reason_codes"]
        or "mcp_tool_not_active_or_not_found" in decision["reason_codes"]
    )
    assert plan["preflight"]["blocked_actions"][0]["type"] == "mcp_call"
    assert plan["preflight"]["blocked_actions"][0]["execution_created"] is False


def test_phase16_unavailable_skill_is_removed_before_execution(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "用指定 Skill 生成后端计划",
            "mode_hint": "workflow",
            "constraints": {"skill_id": "skill_missing"},
            "auto_start": True,
        },
    ).json()
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    plan = client.get(f"/api/tasks/{task['task_id']}/plan").json()["plan"]

    assert task["status"] == "completed"
    assert not replay["skill_runs"]
    assert all(step["step_type"] != "skill_run" for step in replay["steps"])
    decision = replay["planner_decisions"][0]
    assert "skill_unavailable_removed_from_plan" in decision["reason_codes"]
    assert plan["preflight"]["blocked_actions"][0]["type"] == "skill_run"
    assert plan["preflight"]["blocked_actions"][0]["execution_created"] is False
