from __future__ import annotations

from typing import Any

from core_types import ErrorCode, RiskLevel, TaskBudget, TaskMode, TaskStatus, TraceSpanStatus, TraceSpanType
from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso


class TaskAgentRuntime:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def run_agent_loop(self, task_id: str, *, trace_id: str | None) -> None:
        task = await self._engine._get_task(task_id)
        await self._engine._ensure_repo_workspace_baseline(task_id, trace_id=trace_id)
        existing_iterations = await self._engine._repo.list_agent_loop_iterations(task_id)
        span_id = await self._engine._start_span(
            trace_id,
            TraceSpanType.TASK_RUN,
            "run authoritative agent loop",
            input_data={"task_id": task_id, "mode": TaskMode.AGENT.value},
        )
        loop_steps = max((int(item.get("loop_index") or 0) for item in existing_iterations), default=0)
        tool_calls = 0
        stop_reason = "completed"
        pause_reason = None
        try:
            await self._engine._event(
                task_id,
                "agent.loop_started",
                {
                    "task_id": task_id,
                    "mode": TaskMode.AGENT.value,
                    "runtime": "task_agent_runtime",
                    "authoritative": True,
                },
                trace_id=trace_id,
            )
            await self._engine._event(
                task_id,
                "agent.observe",
                {
                    "goal": task["goal"],
                    "budget": task.get("budget", {}),
                    "resource_handles": task.get("resource_handle_ids", []),
                    "runtime": "task_agent_runtime",
                },
                trace_id=trace_id,
            )
            steps = await self._engine._repo.list_steps(task_id)
            budget = TaskBudget(**task.get("budget", {}))
            for step in steps:
                fresh = await self._engine._get_task(task_id)
                if fresh["status"] == TaskStatus.CANCELLED.value:
                    stop_reason = "cancelled"
                    break
                if step["status"] in {"completed", "failed"}:
                    continue
                if loop_steps >= budget.max_loop_steps or tool_calls >= budget.max_tool_calls:
                    stop_reason = "budget_exhausted"
                    pause_reason = "budget_exhausted"
                    break
                loop_steps += 1
                observe_span = await self._engine._start_span(
                    trace_id,
                    TraceSpanType.AGENT_OBSERVE,
                    "agent observe",
                    input_data={"loop_index": loop_steps, "step_key": step["step_key"]},
                )
                observation = await self._engine._create_observation(
                    task=fresh,
                    step=step,
                    source_type="task_state",
                    source_ref={"step_id": step["step_id"], "step_key": step["step_key"]},
                    summary=f"准备执行步骤：{step['title']}",
                    payload={"goal": fresh["goal"], "step_input": step.get("input", {})},
                    trace_id=trace_id,
                )
                await self._engine._end_span(
                    observe_span,
                    output_data={
                        "observation_id": observation["observation_id"],
                        "summary": observation["summary"],
                    },
                )
                plan_span = await self._engine._start_span(
                    trace_id,
                    TraceSpanType.AGENT_PLAN,
                    "agent plan",
                    input_data={"loop_index": loop_steps, "step_type": step["step_type"]},
                )
                await self._engine._event(
                    task_id,
                    "agent.plan",
                    {
                        "loop_index": loop_steps,
                        "next_step_key": step["step_key"],
                        "step_type": step["step_type"],
                        "runtime": "task_agent_runtime",
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                await self._engine._end_span(
                    plan_span,
                    output_data={"selected_action": step["step_key"], "reason": "next_pending_step"},
                )
                act_span = await self._engine._start_span(
                    trace_id,
                    TraceSpanType.AGENT_ACT,
                    "agent act",
                    input_data={"loop_index": loop_steps, "step_key": step["step_key"]},
                )
                await self._engine._event(
                    task_id,
                    "agent.act",
                    {"loop_index": loop_steps, "step_key": step["step_key"]},
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                if step["step_type"] in {"tool_call", "mcp_call", "skill_run"}:
                    tool_calls += 1
                await self._engine._run_step(fresh, step, trace_id=trace_id)
                after_step = await self._engine._repo.get_step(step["step_id"]) or step
                await self._engine._end_span(
                    act_span,
                    output_data={"status": after_step["status"], "tool_call_id": after_step.get("tool_call_id")},
                )
                result_observation = await self._engine._create_observation(
                    task=fresh,
                    step=after_step,
                    source_type=after_step["step_type"],
                    source_ref={
                        "step_id": after_step["step_id"],
                        "step_key": after_step["step_key"],
                        "tool_call_id": after_step.get("tool_call_id"),
                    },
                    summary=_observation_summary_for_step(after_step),
                    payload=after_step.get("output", {}),
                    trace_id=trace_id,
                )
                current = await self._engine._get_task(task_id)
                evaluate_span = await self._engine._start_span(
                    trace_id,
                    TraceSpanType.AGENT_EVALUATE,
                    "agent evaluate",
                    input_data={"loop_index": loop_steps, "step_status": after_step["status"]},
                )
                await self._engine._event(
                    task_id,
                    "agent.evaluate",
                    {
                        "loop_index": loop_steps,
                        "task_status": current["status"],
                        "step_key": step["step_key"],
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                iteration_stop_reason = None
                iteration_pause_reason = None
                if current["status"] == TaskStatus.WAITING_APPROVAL.value:
                    stop_reason = "approval_waiting"
                    pause_reason = "approval_waiting"
                    iteration_stop_reason = stop_reason
                    iteration_pause_reason = pause_reason
                elif current["status"] in {TaskStatus.FAILED.value, TaskStatus.PAUSED.value}:
                    stop_reason = (
                        "boundary_blocked"
                        if after_step.get("error_code") in {
                            ErrorCode.SAFETY_BLOCKED.value,
                            ErrorCode.TOOL_PERMISSION_DENIED.value,
                        }
                        else "recovery_exhausted"
                    )
                    iteration_stop_reason = stop_reason
                    if current["status"] == TaskStatus.PAUSED.value:
                        pause_reason = stop_reason
                        iteration_pause_reason = pause_reason
                await self._engine._end_span(
                    evaluate_span,
                    output_data={
                        "task_status": current["status"],
                        "pause_reason": iteration_pause_reason,
                        "stop_reason": iteration_stop_reason,
                    },
                )
                next_pending_step_key = _next_pending_step_key(
                    await self._engine._repo.list_steps(task_id)
                )
                budget_snapshot = {
                    "loop_steps": loop_steps,
                    "max_loop_steps": budget.max_loop_steps,
                    "tool_calls": tool_calls,
                    "max_tool_calls": budget.max_tool_calls,
                }
                iteration_id = new_id("agit")
                plan_delta_suggestion = self._engine._replanner.suggest(
                    task=current,
                    step=after_step,
                    loop_index=loop_steps,
                    task_status=current["status"],
                    step_status=after_step["status"],
                    next_step_key=next_pending_step_key,
                    stop_reason=iteration_stop_reason,
                    budget_snapshot=budget_snapshot,
                    trace_id=trace_id,
                )
                next_action = self._engine._next_action_selector.select(
                    task=task,
                    step=after_step,
                    iteration_id=iteration_id,
                    loop_index=loop_steps,
                    task_status=current["status"],
                    step_status=after_step["status"],
                    next_step_key=next_pending_step_key,
                    stop_reason=iteration_stop_reason,
                    budget_snapshot=budget_snapshot,
                    trace_id=trace_id,
                    plan_delta_suggestion=plan_delta_suggestion,
                )
                next_action_payload = next_action.model_dump(mode="json")
                next_action_payload["next_action_type"] = _phase96_action_type(
                    next_action.next_action_type,
                    iteration_pause_reason,
                    iteration_stop_reason,
                )
                next_action_payload["stop_reason"] = iteration_stop_reason
                await self._engine._repo.insert_agent_loop_iteration(
                    {
                        "iteration_id": iteration_id,
                        "organization_id": task["organization_id"],
                        "task_id": task_id,
                        "loop_index": loop_steps,
                        "observation_id": result_observation["observation_id"],
                        "observation_summary": result_observation["summary"],
                        "plan_delta": {
                            **next_action.plan_delta,
                            "plan_delta_type": _phase96_plan_delta_type(next_action.next_action_type),
                        },
                        "selected_action": {
                            "step_id": after_step["step_id"],
                            "step_key": after_step["step_key"],
                            "step_type": after_step["step_type"],
                            "next_action_type": next_action_payload["next_action_type"],
                        },
                        "tool_call_refs": _tool_call_refs(after_step),
                        "safety_decision_refs": await self._engine._safety_refs_for_step(after_step),
                        "evaluation_result": {
                            "task_status": current["status"],
                            "step_status": after_step["status"],
                            "pause_reason": iteration_pause_reason,
                            "stop_reason": iteration_stop_reason,
                            "recoverable": current["status"]
                            in {TaskStatus.PAUSED.value, TaskStatus.WAITING_APPROVAL.value},
                            "reason_codes": list(next_action.reason_codes),
                        },
                        "next_step_key": next_pending_step_key,
                        "stop_reason": iteration_stop_reason,
                        "budget_snapshot": budget_snapshot,
                        "status": "completed",
                        "trace_id": trace_id,
                        "started_at": utc_now_iso(),
                        "completed_at": utc_now_iso(),
                    }
                )
                await self._engine._repo.insert_agent_next_action_decision(next_action_payload)
                if next_action_payload["next_action_type"] == "revise_plan":
                    await self._engine._event(
                        task_id,
                        "agent.revise",
                        {
                            "loop_index": loop_steps,
                            "decision_id": next_action.decision_id,
                            "reason_codes": next_action.reason_codes,
                            "plan_delta_suggestion_id": plan_delta_suggestion.suggestion_id,
                        },
                        step_id=step["step_id"],
                        trace_id=trace_id,
                    )
                await self._engine._event(
                    task_id,
                    "agent.next_action_selected",
                    {
                        "loop_index": loop_steps,
                        "decision_id": next_action.decision_id,
                        "next_action_type": next_action_payload["next_action_type"],
                        "reason_codes": next_action.reason_codes,
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                if after_step["status"] == "failed" or iteration_stop_reason in {
                    "recovery_exhausted",
                    "boundary_blocked",
                    "approval_waiting",
                }:
                    recovery_reason = iteration_stop_reason or after_step.get("error_code") or "failed"
                    await self._engine._create_tool_failure_recovery_plan(
                        task=current,
                        step=after_step,
                        failure_reason=recovery_reason,
                        trace_id=trace_id,
                    )
                await self._engine._event(
                    task_id,
                    "agent.iteration_completed",
                    {
                        "loop_index": loop_steps,
                        "observation_id": result_observation["observation_id"],
                        "pause_reason": iteration_pause_reason,
                        "stop_reason": iteration_stop_reason,
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                if current["status"] == TaskStatus.WAITING_APPROVAL.value:
                    break
                if current["status"] in {TaskStatus.FAILED.value, TaskStatus.PAUSED.value}:
                    break
            final_task = await self._engine._get_task(task_id)
            if final_task["status"] == TaskStatus.FAILED.value:
                await self._engine._safe_reflect(task_id, trace_id=trace_id)
            if stop_reason == "budget_exhausted":
                next_pending_step_key = _next_pending_step_key(await self._engine._repo.list_steps(task_id))
                budget_observation = await self._engine._create_observation(
                    task=await self._engine._get_task(task_id),
                    step=None,
                    source_type="agent_budget",
                    source_ref={"task_id": task_id, "reason": stop_reason},
                    summary="Agent loop 因预算限制暂停，已保留下一步建议。",
                    payload={
                        "reason": stop_reason,
                        "loop_steps": loop_steps,
                        "max_loop_steps": budget.max_loop_steps,
                        "tool_calls": tool_calls,
                        "max_tool_calls": budget.max_tool_calls,
                        "next_step_key": next_pending_step_key,
                    },
                    trace_id=trace_id,
                )
                budget_snapshot = {
                    "loop_steps": loop_steps,
                    "max_loop_steps": budget.max_loop_steps,
                    "tool_calls": tool_calls,
                    "max_tool_calls": budget.max_tool_calls,
                }
                iteration_id = new_id("agit")
                plan_delta_suggestion = self._engine._replanner.suggest(
                    task=task,
                    step=None,
                    loop_index=loop_steps + 1,
                    task_status=TaskStatus.PAUSED.value,
                    step_status=None,
                    next_step_key=next_pending_step_key,
                    stop_reason=stop_reason,
                    budget_snapshot=budget_snapshot,
                    trace_id=trace_id,
                )
                next_action = self._engine._next_action_selector.select(
                    task=task,
                    step=None,
                    iteration_id=iteration_id,
                    loop_index=loop_steps + 1,
                    task_status=TaskStatus.PAUSED.value,
                    step_status=None,
                    next_step_key=next_pending_step_key,
                    stop_reason=stop_reason,
                    budget_snapshot=budget_snapshot,
                    trace_id=trace_id,
                    plan_delta_suggestion=plan_delta_suggestion,
                )
                next_action_payload = next_action.model_dump(mode="json")
                next_action_payload["next_action_type"] = "stop_budget"
                next_action_payload["stop_reason"] = stop_reason
                await self._engine._repo.insert_agent_loop_iteration(
                    {
                        "iteration_id": iteration_id,
                        "organization_id": task["organization_id"],
                        "task_id": task_id,
                        "loop_index": loop_steps + 1,
                        "observation_id": budget_observation["observation_id"],
                        "observation_summary": budget_observation["summary"],
                        "plan_delta": {
                            **next_action.plan_delta,
                            "plan_delta_type": "budget_pause",
                        },
                        "selected_action": {"next_action_type": "stop_budget"},
                        "tool_call_refs": [],
                        "safety_decision_refs": [],
                        "evaluation_result": {
                            "task_status": TaskStatus.PAUSED.value,
                            "pause_reason": "budget_exhausted",
                            "stop_reason": stop_reason,
                            "recoverable": True,
                            "reason_codes": list(next_action.reason_codes),
                        },
                        "next_step_key": next_pending_step_key,
                        "stop_reason": stop_reason,
                        "budget_snapshot": budget_snapshot,
                        "status": "stopped",
                        "trace_id": trace_id,
                        "started_at": utc_now_iso(),
                        "completed_at": utc_now_iso(),
                    }
                )
                await self._engine._repo.insert_agent_next_action_decision(next_action_payload)
                await self._engine._create_tool_failure_recovery_plan(
                    task=await self._engine._get_task(task_id),
                    step=None,
                    failure_reason=stop_reason,
                    trace_id=trace_id,
                )
                await self._engine._create_retry_plan(
                    await self._engine._get_task(task_id),
                    reason=stop_reason,
                    suggested_actions=["增加 loop budget 后继续执行", "缩小任务范围后重试"],
                    trace_id=trace_id,
                )
                await self._engine._transition_task(
                    task_id,
                    TaskStatus.PAUSED.value,
                    trace_id=trace_id,
                    extra={"failure_reason": stop_reason},
                )
            elif stop_reason in {"recovery_exhausted", "boundary_blocked"}:
                await self._engine._transition_task(
                    task_id,
                    TaskStatus.FAILED.value,
                    trace_id=trace_id,
                    extra={"failure_reason": stop_reason},
                )
            final_task = await self._engine._get_task(task_id)
            if final_task["status"] == TaskStatus.RUNNING.value:
                await self._engine._complete_task(
                    task_id,
                    {"summary": "任务已完成。", "stop_reason": "goal_satisfied"},
                    trace_id=trace_id,
                )
                stop_reason = "goal_satisfied"
            await self._engine._event(
                task_id,
                "agent.stop",
                {
                    "stop_reason": stop_reason,
                    "pause_reason": pause_reason,
                    "loop_steps": loop_steps,
                    "tool_calls": tool_calls,
                },
                trace_id=trace_id,
            )
            await self._engine._event(
                task_id,
                "agent.stopped",
                {
                    "stop_reason": stop_reason,
                    "pause_reason": pause_reason,
                    "loop_steps": loop_steps,
                    "tool_calls": tool_calls,
                    "runtime": "task_agent_runtime",
                },
                trace_id=trace_id,
            )
            await self._engine._safe_reflect(task_id, trace_id=trace_id)
            final_status = (
                "waiting_approval"
                if pause_reason == "approval_waiting"
                else "paused"
                if pause_reason
                else "completed"
            )
            await self._engine._end_span(
                span_id,
                output_data={"status": final_status, "pause_reason": pause_reason, "stop_reason": stop_reason},
            )
        except Exception as exc:
            await self._engine._repo.update_task(
                task_id,
                {
                    "status": TaskStatus.FAILED.value,
                    "failure_reason": str(redact(str(exc))),
                    "updated_at": utc_now_iso(),
                },
            )
            await self._engine._event(
                task_id,
                "agent.failed",
                {
                    "error_code": getattr(exc, "code", ErrorCode.TASK_STEP_FAILED),
                    "message": str(redact(str(exc))),
                    "stop_reason": "recovery_exhausted",
                },
                trace_id=trace_id,
            )
            await self._engine._audit.write_event(
                actor_type="system",
                action="agent.failed",
                object_type="task",
                object_id=task_id,
                summary="Agent 任务执行失败",
                risk_level=RiskLevel.R2,
                payload={"error": str(redact(str(exc)))},
                trace_id=trace_id,
            )
            await self._engine._safe_reflect(task_id, trace_id=trace_id)
            await self._engine._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={
                    "error_code": getattr(exc, "code", ErrorCode.TASK_STEP_FAILED),
                    "stop_reason": "recovery_exhausted",
                },
            )
            if not isinstance(exc, AppError):
                raise

    async def resume_after_pause(
        self,
        task_id: str,
        *,
        pause_reason: str | None,
        trace_id: str | None,
    ) -> None:
        await self._engine._event(
            task_id,
            "agent.resume",
            {
                "task_id": task_id,
                "pause_reason": pause_reason,
                "runtime": "task_agent_runtime",
            },
            trace_id=trace_id,
        )
        await self.run_agent_loop(task_id, trace_id=trace_id)

    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "task_agent_runtime",
            "authoritative": True,
            "observation_loop": True,
            "bounded_retry": True,
            "execution_model": "observe_decide_act_evaluate_replan_stop",
            "public_entrypoints": ["run_agent_loop", "resume_after_pause"],
        }


def _next_pending_step_key(steps: list[dict[str, Any]]) -> str | None:
    for step in steps:
        if step.get("status") not in {"completed", "failed"}:
            return str(step.get("step_key"))
    return None


def _tool_call_refs(step: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if step.get("tool_call_id"):
        refs.append(
            {
                "type": "tool_call",
                "tool_call_id": step["tool_call_id"],
                "step_id": step.get("step_id"),
            }
        )
    output = step.get("output") or {}
    if not isinstance(output, dict):
        return refs
    skill_run = output.get("skill_run")
    if isinstance(skill_run, dict) and skill_run.get("skill_run_id"):
        refs.append(
            {
                "type": "skill_run",
                "skill_run_id": skill_run["skill_run_id"],
                "step_id": step.get("step_id"),
            }
        )
    mcp_call = output.get("mcp_call")
    if isinstance(mcp_call, dict) and mcp_call.get("mcp_call_id"):
        refs.append(
            {
                "type": "mcp_call",
                "mcp_call_id": mcp_call["mcp_call_id"],
                "step_id": step.get("step_id"),
            }
        )
    return refs


def _observation_summary_for_step(step: dict[str, Any]) -> str:
    title = str(step.get("title") or step.get("step_key") or "step")
    status = str(step.get("status") or "unknown")
    if status == "completed":
        return f"{title} 已完成。"
    if status == "waiting_approval":
        return f"{title} 正在等待审批。"
    if status == "failed":
        error = step.get("error_summary") or step.get("error_code") or "unknown_error"
        return f"{title} 失败：{redact(str(error))}"
    return f"{title} 状态：{status}。"


def _phase96_action_type(
    raw_action_type: str,
    pause_reason: str | None,
    stop_reason: str | None,
) -> str:
    if pause_reason == "approval_waiting":
        return "pause_for_approval"
    if pause_reason == "budget_exhausted":
        return "pause_for_budget"
    if stop_reason == "goal_satisfied":
        return "stop_completed"
    if stop_reason in {"recovery_exhausted", "boundary_blocked", "cancelled"}:
        return "stop_failed"
    if raw_action_type in {"stop_budget", "pause_for_budget", "pause"}:
        return "pause_for_budget"
    if raw_action_type in {"continue", "continue_step"}:
        return "continue_step"
    return raw_action_type


def _phase96_plan_delta_type(raw_action_type: str) -> str:
    if raw_action_type == "revise_plan":
        return "replan"
    if raw_action_type in {"stop_budget", "pause_for_budget", "pause"}:
        return "budget_pause"
    return "step_continuation"
