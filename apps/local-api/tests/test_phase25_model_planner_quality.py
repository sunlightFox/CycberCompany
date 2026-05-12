from __future__ import annotations

import json
from typing import Any, cast

from fastapi.testclient import TestClient


def test_phase25_contracts_gap_and_required_suite(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    by_module = {item["name"]: item for item in contracts}
    suite = next(
        item for item in suites if item["suite_id"] == "suite_phase25_model_planner_quality"
    )

    assert by_module["ModelPlanner"]["status"] == "implemented"
    assert by_module["ModelPlanner"]["details"]["model_assist_mode"] == "auto"
    assert by_module["ModelPlanner"]["details"]["candidate_only"] is True
    assert by_module["ModelPlanCandidateGenerator"]["status"] == "implemented"
    assert by_module["PlanQualityScorer"]["status"] == "implemented"
    assert by_module["ObservationAwareReplanner"]["status"] == "implemented"
    assert by_module["ModelAssistedRecoveryPlanner"]["status"] == "implemented"
    assert by_module["SkillMCPCandidateRanker"]["status"] == "implemented"
    assert any(
        item["gap_id"] == "gap_model_planner_assist_disabled"
        and item["status"] == "accepted_risk"
        for item in gaps
    )
    assert suite["required"] is True
    assert suite["category"] == "model_planner_quality"


def test_phase25_no_model_fallback_keeps_agent_task_running(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase25 fallback planner evidence",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 8, "max_tool_calls": 5},
        },
    ).json()

    candidates = client.get(
        f"/api/tasks/{task['task_id']}/model-plan-candidates"
    ).json()["items"]
    plan = client.get(f"/api/tasks/{task['task_id']}/plan").json()["plan"]
    planner_decisions = client.get(
        f"/api/tasks/{task['task_id']}/planner-decisions"
    ).json()["items"]

    selected = next(item for item in candidates if item["status"] == "selected")
    assert task["mode"] == "agent"
    assert task["status"] in {"completed", "paused"}
    assert selected["source"] == "deterministic_rule_surrogate"
    assert selected["model_assist"]["fallback_used"] is True
    assert selected["model_assist"]["quality_score"]["total_score"] > 0
    assert plan["preflight"]["phase25"]["fallback_used"] is True
    assert planner_decisions[0]["model_hint"]["phase25"]["candidate_only"] is True


def test_phase25_fake_model_candidate_is_verified_scored_and_selected(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.task_engine.set_model_planner_adapter(
        _FakeModelPlannerAdapter(_valid_model_candidate("research phase25 model quality"))
    )

    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase25 model quality",
            "mode_hint": "agent",
            "auto_start": False,
        },
    ).json()

    candidates = client.get(
        f"/api/tasks/{task['task_id']}/model-plan-candidates"
    ).json()["items"]
    verifications = client.get(
        f"/api/tasks/{task['task_id']}/plan-verification-results"
    ).json()["items"]
    plan = client.get(f"/api/tasks/{task['task_id']}/plan").json()["plan"]
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    selected = next(item for item in candidates if item["status"] == "selected")
    assert any(item["source"] == "model_assist" for item in candidates)
    assert selected["source"] == "model_assist"
    assert selected["model_assist"]["attempted"] is True
    assert selected["model_assist"]["quality_score"]["selected"] is True
    assert all(item["schema_valid"] for item in verifications)
    assert plan["preflight"]["phase25"]["selected_candidate_source"] == "model_assist"
    assert replay["model_plan_candidates"]
    assert replay["planner_decisions"][0]["model_hint"]["phase25"]["model_assist_attempted"] is True


def test_phase25_invalid_json_and_timeout_model_outputs_fallback(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.task_engine.set_model_planner_adapter(_FakeModelPlannerAdapter("{not-json"))
    invalid_task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase25 invalid model output",
            "mode_hint": "agent",
            "auto_start": False,
        },
    ).json()

    registry.task_engine.set_model_planner_adapter(_TimeoutModelPlannerAdapter())
    timeout_task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase25 timeout model output",
            "mode_hint": "agent",
            "auto_start": False,
        },
    ).json()

    invalid_candidates = client.get(
        f"/api/tasks/{invalid_task['task_id']}/model-plan-candidates"
    ).json()["items"]
    timeout_candidates = client.get(
        f"/api/tasks/{timeout_task['task_id']}/model-plan-candidates"
    ).json()["items"]

    assert any(
        item["source"] == "model_assist_failed"
        and item["model_assist"]["fallback_reason"] == "schema_invalid"
        for item in invalid_candidates
    )
    assert any(
        item["source"] == "model_assist_failed"
        and item["model_assist"]["fallback_reason"] == "model_timeout"
        for item in timeout_candidates
    )
    assert any(
        item["status"] == "selected" and item["source"] != "model_assist"
        for item in invalid_candidates
    )
    assert any(
        item["status"] == "selected" and item["source"] != "model_assist"
        for item in timeout_candidates
    )


def test_phase25_dangerous_model_candidate_is_pruned_before_execution(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.task_engine.set_model_planner_adapter(
        _FakeModelPlannerAdapter(
            {
                "recommended_mode": "agent",
                "steps": [
                    {
                        "step_key": "terminal_run",
                        "step_type": "tool_call",
                        "title": "危险终端命令",
                        "risk_level": "R5",
                        "input": {
                            "tool_name": "terminal.run",
                            "args": {
                                "command": "rm -rf C:\\Users\\Administrator\\Desktop",
                            },
                        },
                    },
                    {
                        "step_key": "compose_report",
                        "step_type": "compose",
                        "title": "生成报告",
                        "risk_level": "R1",
                        "input": {},
                    },
                ],
                "confidence": 0.9,
                "reasoning_summary": "candidate must be pruned before runtime",
            }
        )
    )

    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase25 dangerous shell prune",
            "mode_hint": "agent",
            "auto_start": True,
        },
    ).json()
    prunes = client.get(f"/api/tasks/{task['task_id']}/plan-policy-prunes").json()["items"]
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    serialized = json.dumps(replay, ensure_ascii=False)

    assert any(item["prune_type"] == "remove_dangerous_shell_command" for item in prunes)
    assert all(step["input"].get("tool_name") != "terminal.run" for step in replay["steps"])
    assert "Administrator" not in serialized
    assert "rm -rf" not in serialized


def test_phase25_high_risk_workflow_keeps_approval_checkpoint(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "删除 phase25 临时文件",
            "mode_hint": "workflow",
            "constraints": {"path": "outputs/phase25-target.txt"},
            "auto_start": False,
        },
    ).json()

    plan = client.get(f"/api/tasks/{task['task_id']}/plan").json()["plan"]
    prunes = client.get(f"/api/tasks/{task['task_id']}/plan-policy-prunes").json()["items"]

    assert task["mode"] == "workflow"
    assert plan["approval_strategy"]["required_before_execution"] is True
    assert any(item["prune_type"] == "insert_approval_checkpoint" for item in prunes)


def test_phase25_fixed_workflow_is_not_upgraded_to_agent_by_model(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.task_engine.set_model_planner_adapter(
        _FakeModelPlannerAdapter({"recommended_mode": "agent", "steps": []})
    )

    task = client.post(
        "/api/tasks",
        json={
            "goal": "整理 phase25 封版清单",
            "mode_hint": "workflow",
            "auto_start": True,
        },
    ).json()

    assert task["mode"] == "workflow"
    assert task["status"] == "completed"
    assert client.get(f"/api/tasks/{task['task_id']}/model-plan-candidates").json()[
        "items"
    ] == []
    assert client.get(f"/api/tasks/{task['task_id']}/agent-loop").json()["items"] == []


def test_phase25_agent_loop_budget_stop_records_replan_and_recovery(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase25 budget replanning",
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
    stop_budget = next(
        item for item in next_actions if item["next_action_type"] == "pause_for_budget"
    )
    assert stop_budget["plan_delta"]["trigger_reason"] == "budget_near_limit"
    assert stop_budget["plan_delta"]["model_assist"]["fallback"] == "rule_observation_replanner"
    assert any(item["failure_type"] == "budget_exhausted" for item in recovery)
    assert all(item["bypass_controls"] is False for item in recovery)


def test_phase25_skill_mcp_unavailable_are_ranked_but_not_executed(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "用 Skill 和 MCP 做 phase25 规划",
            "mode_hint": "workflow",
            "constraints": {"skill_id": "skill_missing", "mcp_tool_name": "missing.tool"},
            "auto_start": True,
        },
    ).json()
    capability_candidates = client.get(
        f"/api/tasks/{task['task_id']}/planner-capability-candidates"
    ).json()["items"]
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert capability_candidates
    assert any(
        item["policy_status"] == "unavailable"
        and "phase25_policy_preview_rejected" in item["reason_codes"]
        for item in capability_candidates
    )
    assert not replay["skill_runs"]
    assert not replay["mcp_calls"]


def test_phase25_release_report_and_phase23_aggregation(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase25 release evidence",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_loop_steps": 1, "max_tool_calls": 5},
        },
    ).json()
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    phase25 = report["summary"]["phase25"]
    phase23 = report["summary"]["phase23"]

    assert task["task_id"]
    assert completed["status"] == "ready_for_release"
    assert report["decision"] == "go"
    assert phase25["suite_id"] == "suite_phase25_model_planner_quality"
    assert phase25["registered_cases"] == 10
    assert phase25["candidate_count"] >= 1
    assert phase25["quality_score_summary"]["scored_candidates"] >= 1
    assert phase25["replan_count"] >= 1
    assert phase25["recovery_count"] >= 1
    assert phase25["leakage_count"] == 0
    assert phase23["capability_scores"]["phase25"]["registered"] is True
    assert "phase25-secret-value" not in json.dumps(report, ensure_ascii=False)


class _FakeModelPlannerAdapter:
    name = "fake_model_planner"

    def __init__(self, payload: dict[str, Any] | str) -> None:
        self._payload = payload

    async def generate(self, request: Any) -> dict[str, Any] | str:
        del request
        return {
            "candidate": json.dumps(self._payload, ensure_ascii=False)
            if isinstance(self._payload, dict)
            else self._payload,
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "finish_reason": "stop",
            "brain": {
                "brain_id": "brain_fake_local",
                "provider": "local_fake",
                "model_name": "fake-planner",
                "is_local": True,
            },
        }


class _TimeoutModelPlannerAdapter:
    name = "timeout_model_planner"

    async def generate(self, request: Any) -> dict[str, Any] | str:
        del request
        raise TimeoutError


def _valid_model_candidate(goal: str) -> dict[str, Any]:
    return {
        "recommended_mode": "agent",
        "steps": [
            {
                "step_key": "knowledge_search",
                "step_type": "tool_call",
                "title": f"检索 {goal}",
                "risk_level": "R1",
                "input": {
                    "tool_name": "knowledge.search",
                    "args": {"query": goal, "limit": 5},
                },
            },
            {
                "step_key": "compose_report",
                "step_type": "compose",
                "title": f"生成 {goal} 报告",
                "risk_level": "R1",
                "input": {},
            },
        ],
        "success_criteria": ["任务产生可回放结果"],
        "assumptions": ["使用只读检索和报告输出"],
        "missing_information": ["asset_scope_if_required_by_runtime"],
        "required_capabilities": ["tool:knowledge.search"],
        "confidence": 0.92,
        "reasoning_summary": "fake local planner candidate for deterministic tests",
    }
