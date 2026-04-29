from __future__ import annotations

from fastapi.testclient import TestClient


def test_phase19_runtime_contracts_and_suite(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_module = {item["name"]: item for item in contracts}
    gaps = client.get("/api/system/design-gaps").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]

    assert by_module["ModelPlanner"]["status"] == "implemented"
    assert by_module["ModelPlanner"]["details"]["model_assist"] is False
    assert by_module["PlanVerifier"]["status"] == "implemented"
    assert by_module["PolicyPruner"]["status"] == "implemented"
    assert by_module["AgentNextActionSelector"]["status"] == "implemented"
    assert by_module["ToolFailureRecoveryPlanner"]["status"] == "implemented"
    assert any(item["gap_id"] == "gap_model_planner_assist_disabled" for item in gaps)

    suite = next(
        item for item in suites if item["suite_id"] == "suite_phase19_model_planner_agent"
    )
    assert suite["category"] == "model_planner_agent"
    assert suite["required"] is True


def test_phase19_agent_task_records_candidate_verification_and_next_actions(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase19 planner evidence",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 8, "max_tool_calls": 5},
        },
    ).json()

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    candidates = client.get(
        f"/api/tasks/{task['task_id']}/model-plan-candidates"
    ).json()["items"]
    verifications = client.get(
        f"/api/tasks/{task['task_id']}/plan-verification-results"
    ).json()["items"]
    next_actions = client.get(
        f"/api/tasks/{task['task_id']}/agent-next-actions"
    ).json()["items"]

    assert task["mode"] == "agent"
    assert candidates
    assert candidates[0]["model_assist"]["enabled"] is False
    assert verifications
    assert verifications[0]["schema_valid"] is True
    assert next_actions
    assert any(item["next_action_type"] in {"act", "stop_success"} for item in next_actions)
    assert replay["model_plan_candidates"]
    assert replay["plan_verification_results"]
    assert replay["agent_next_action_decisions"]


def test_phase19_workflow_stays_workflow_without_agent_overuse(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "整理后端封版清单", "mode_hint": "workflow", "auto_start": True},
    ).json()

    assert task["mode"] == "workflow"
    assert task["status"] == "completed"
    assert client.get(f"/api/tasks/{task['task_id']}/agent-loop").json()["items"] == []
    assert client.get(f"/api/tasks/{task['task_id']}/model-plan-candidates").json()[
        "items"
    ] == []


def test_phase19_dangerous_terminal_candidate_is_pruned_before_runtime(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "用终端命令清理目录",
            "mode_hint": "workflow",
            "constraints": {"command": "rm -rf C:\\Users\\Administrator\\Desktop"},
            "auto_start": True,
        },
    ).json()

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    prunes = client.get(f"/api/tasks/{task['task_id']}/plan-policy-prunes").json()["items"]

    assert task["status"] == "completed"
    assert any(item["prune_type"] == "remove_dangerous_shell_command" for item in prunes)
    assert all(
        step["input"].get("tool_name") != "terminal.run" for step in replay["steps"]
    )
    assert not replay["tool_calls"]


def test_phase19_sensitive_path_and_secret_payload_are_pruned_and_redacted(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "删除系统敏感路径",
            "mode_hint": "workflow",
            "constraints": {"path": "C:\\Users\\Administrator\\.ssh\\id_rsa"},
            "auto_start": True,
        },
    ).json()

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    candidates = client.get(
        f"/api/tasks/{task['task_id']}/model-plan-candidates"
    ).json()["items"]
    verifications = client.get(
        f"/api/tasks/{task['task_id']}/plan-verification-results"
    ).json()["items"]
    prunes = client.get(f"/api/tasks/{task['task_id']}/plan-policy-prunes").json()["items"]
    serialized_candidate = str(candidates)

    assert task["status"] == "completed"
    assert any(item["prune_type"] == "remove_sensitive_payload" for item in prunes)
    assert verifications[0]["no_direct_secret"] is False
    assert "Administrator" not in serialized_candidate
    assert "id_rsa" not in serialized_candidate
    assert not replay["tool_calls"]


def test_phase19_high_risk_plan_records_approval_checkpoint(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "删除临时文件",
            "mode_hint": "workflow",
            "constraints": {"path": "outputs/target.txt"},
            "auto_start": False,
        },
    ).json()

    plan = client.get(f"/api/tasks/{task['task_id']}/plan").json()["plan"]
    prunes = client.get(f"/api/tasks/{task['task_id']}/plan-policy-prunes").json()["items"]

    assert plan["approval_strategy"]["required_before_execution"] is True
    assert any(item["prune_type"] == "insert_approval_checkpoint" for item in prunes)


def test_phase19_unavailable_skill_mcp_become_capability_candidates(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "用 Skill 和 MCP 做规划",
            "mode_hint": "workflow",
            "constraints": {"skill_id": "skill_missing", "mcp_tool_name": "missing.tool"},
            "auto_start": True,
        },
    ).json()
    candidates = client.get(
        f"/api/tasks/{task['task_id']}/planner-capability-candidates"
    ).json()["items"]
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert candidates
    assert any(item["policy_status"] == "unavailable" for item in candidates)
    assert not replay["skill_runs"]
    assert not replay["mcp_calls"]


def test_phase19_budget_stop_creates_next_action_and_recovery_plan(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase19 budget recovery",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 1, "max_tool_calls": 5},
        },
    ).json()

    next_actions = client.get(
        f"/api/tasks/{task['task_id']}/agent-next-actions"
    ).json()["items"]
    recovery = client.get(
        f"/api/tasks/{task['task_id']}/failure-recovery-plans"
    ).json()["items"]

    assert task["status"] == "paused"
    assert any(item["next_action_type"] == "stop_budget" for item in next_actions)
    assert any(item["failure_type"] == "budget_exhausted" for item in recovery)
    assert all(item["bypass_controls"] is False for item in recovery)


def test_phase19_eval_and_release_report_include_summary(client: TestClient) -> None:
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase19_model_planner_agent"},
    ).json()
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert run["status"] == "passed"
    assert run["total_cases"] == 11
    assert completed["status"] == "ready_for_release"
    phase19 = report["summary"]["phase19"]
    assert report["decision"] == "go"
    assert phase19["registered_cases"] == 11
    assert phase19["failed_results"] == 0
    assert phase19["contracts"]["ModelPlanner"] == 1
    assert phase19["model_assist_enabled"] is False
