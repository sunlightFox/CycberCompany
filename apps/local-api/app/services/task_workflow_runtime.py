from __future__ import annotations

from core_types import ErrorCode, RiskLevel, TaskBudget, TaskMode, TaskStatus, TraceSpanStatus, TraceSpanType
from trace_service import redact

from app.core.errors import AppError
from app.core.time import utc_now_iso
from typing import Any


class TaskWorkflowRuntime:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def start_task(self, task_id: str, *, trace_id: str | None = None) -> Any:
        task = await self._engine._get_task(task_id)
        if task["status"] == TaskStatus.WAITING_APPROVAL.value:
            return await self._engine.detail(task_id)
        if task["status"] != TaskStatus.RUNNING.value:
            self._engine._ensure_task_transition(task["status"], TaskStatus.RUNNING.value)
            await self._engine._transition_task(task_id, TaskStatus.RUNNING.value, trace_id=trace_id)
        await self._engine._mark_run_job(task_id, "running")
        await self.run_task(task_id, trace_id=trace_id)
        await self._engine._sync_run_job_to_task(task_id)
        return await self._engine.detail(task_id)

    async def resume_task(self, task_id: str, *, trace_id: str | None = None) -> Any:
        task = await self._engine._get_task(task_id)
        self._engine._ensure_task_transition(task["status"], TaskStatus.RUNNING.value)
        await self._engine._transition_task(task_id, TaskStatus.RUNNING.value, trace_id=trace_id)
        await self._engine._mark_run_job(task_id, "running")
        await self.run_task(task_id, trace_id=trace_id)
        await self._engine._sync_run_job_to_task(task_id)
        return await self._engine.detail(task_id)

    async def run_task(self, task_id: str, *, trace_id: str | None) -> None:
        task = await self._engine._get_task(task_id)
        if task["mode"] == TaskMode.SUPERVISOR.value:
            await self._run_supervisor_task(task_id, trace_id=trace_id)
            return
        if task["mode"] == TaskMode.AGENT.value:
            await self._engine._run_agent_loop(task_id, trace_id=trace_id)
            return
        span_id = await self._engine._start_span(
            trace_id,
            TraceSpanType.TASK_RUN,
            "run task",
            input_data={"task_id": task_id},
        )
        try:
            await self._engine._event(task_id, "task.started", {"task_id": task_id}, trace_id=trace_id)
            steps = await self._engine._repo.list_steps(task_id)
            budget = TaskBudget(**task.get("budget", {}))
            if len(steps) > budget.max_steps:
                raise AppError(ErrorCode.TASK_BUDGET_EXCEEDED, "任务步骤超出预算", status_code=409)
            for step in steps:
                fresh = await self._engine._get_task(task_id)
                if fresh["status"] == TaskStatus.CANCELLED.value:
                    return
                if step["status"] == "completed":
                    continue
                if step["status"] == "failed":
                    await self._engine._create_tool_failure_recovery_plan(
                        task=fresh,
                        step=step,
                        failure_reason=str(step.get("error_code") or "step_failed"),
                        trace_id=trace_id,
                    )
                    await self._engine._create_retry_plan(
                        fresh,
                        reason=str(step.get("error_code") or "step_failed"),
                        suggested_actions=["自动恢复会重试可恢复步骤", "必要时请缩小任务范围"],
                        trace_id=trace_id,
                    )
                    await self._engine._repo.update_task(
                        task_id,
                        {
                            "status": TaskStatus.FAILED.value,
                            "failure_reason": str(
                                step.get("error_summary")
                                or step.get("error_code")
                                or "step_failed"
                            ),
                            "updated_at": utc_now_iso(),
                        },
                    )
                    await self._engine._safe_reflect(task_id, trace_id=trace_id)
                    await self._engine._end_span(span_id, output_data={"status": "failed"})
                    return
                await self._engine._run_step(fresh, step, trace_id=trace_id)
                current = await self._engine._get_task(task_id)
                if current["status"] == TaskStatus.WAITING_APPROVAL.value:
                    await self._engine._end_span(span_id, output_data={"status": "waiting_approval"})
                    return
                if current["status"] in {TaskStatus.FAILED.value, TaskStatus.PAUSED.value}:
                    failed_step = await self._engine._latest_failed_step(task_id)
                    if failed_step is not None:
                        await self._engine._create_tool_failure_recovery_plan(
                            task=current,
                            step=failed_step,
                            failure_reason=str(
                                failed_step.get("error_code")
                                or current.get("failure_reason")
                                or "step_failed"
                            ),
                            trace_id=trace_id,
                        )
                        await self._engine._create_retry_plan(
                            current,
                            reason=str(
                                failed_step.get("error_code")
                                or current.get("failure_reason")
                                or "step_failed"
                            ),
                            suggested_actions=["自动恢复会重试可恢复步骤", "必要时请缩小任务范围"],
                            trace_id=trace_id,
                        )
                    await self._engine._end_span(span_id, output_data={"status": current["status"]})
                    return
            await self._engine._complete_task(task_id, {"summary": "任务已完成。"}, trace_id=trace_id)
            await self._engine._end_span(span_id, output_data={"status": "completed"})
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
                "task.failed",
                {
                    "error_code": getattr(exc, "code", ErrorCode.TASK_STEP_FAILED),
                    "message": str(redact(str(exc))),
                },
                trace_id=trace_id,
            )
            await self._engine._audit.write_event(
                actor_type="system",
                action="task.failed",
                object_type="task",
                object_id=task_id,
                summary="任务执行失败",
                risk_level=RiskLevel.R2,
                payload={"error": str(redact(str(exc)))},
                trace_id=trace_id,
            )
            failed_step = await self._engine._latest_failed_step(task_id)
            failed_task = await self._engine._get_task(task_id)
            if failed_step is not None:
                failure_reason = str(
                    failed_step.get("error_code")
                    or failed_task.get("failure_reason")
                    or getattr(exc, "code", ErrorCode.TASK_STEP_FAILED.value)
                )
                await self._engine._create_tool_failure_recovery_plan(
                    task=failed_task,
                    step=failed_step,
                    failure_reason=failure_reason,
                    trace_id=trace_id,
                )
                await self._engine._create_retry_plan(
                    failed_task,
                    reason=failure_reason,
                    suggested_actions=["自动恢复会重试可恢复步骤", "必要时请缩小任务范围"],
                    trace_id=trace_id,
                )
            await self._engine._safe_reflect(task_id, trace_id=trace_id)
            await self._engine._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.TASK_STEP_FAILED)},
            )
            if isinstance(exc, AppError):
                return
            raise

    async def _run_supervisor_task(self, task_id: str, *, trace_id: str | None) -> None:
        if self._engine._supervisor is None:
            raise AppError(
                ErrorCode.SUPERVISOR_PLAN_FAILED,
                "Supervisor Service 未初始化",
                status_code=500,
            )
        try:
            result = await self._engine._supervisor.start(task_id, trace_id=trace_id)
            await self._engine._complete_task(task_id, result, trace_id=trace_id)
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
                "task.failed",
                {
                    "error_code": getattr(exc, "code", ErrorCode.SUPERVISOR_PLAN_FAILED),
                    "message": str(redact(str(exc))),
                },
                trace_id=trace_id,
            )
            await self._engine._audit.write_event(
                actor_type="system",
                action="supervisor.failed",
                object_type="task",
                object_id=task_id,
                summary="Supervisor 协作执行失败",
                risk_level=RiskLevel.R2,
                payload={"error": str(redact(str(exc)))},
                trace_id=trace_id,
            )
            await self._engine._safe_reflect(task_id, trace_id=trace_id)
            if not isinstance(exc, AppError):
                raise

    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "task_workflow_runtime",
            "approval_wait_supported": True,
            "step_state_machine": True,
            "public_entrypoints": ["start_task", "resume_task", "run_task"],
        }
