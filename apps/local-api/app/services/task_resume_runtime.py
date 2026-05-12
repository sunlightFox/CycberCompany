from __future__ import annotations

from core_types import ErrorCode, TaskMode, TaskStatus

from app.core.errors import AppError
from app.core.time import utc_now_iso
from typing import Any


class TaskResumeRuntime:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def retry_task(self, task_id: str, *, trace_id: str | None = None) -> Any:
        task = await self._engine._get_task(task_id)
        if task["status"] not in {TaskStatus.FAILED.value, TaskStatus.PAUSED.value}:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "只有 failed/paused 任务可以重试",
                status_code=409,
            )
        reset_count = await self._engine._reset_recoverable_failed_steps(task, trace_id=trace_id)
        if reset_count == 0:
            if not (
                task["mode"] == TaskMode.AGENT.value
                and task["status"] == TaskStatus.PAUSED.value
            ):
                raise AppError(
                    ErrorCode.TASK_RETRY_EXHAUSTED,
                    "没有可自动重试的失败步骤",
                    status_code=409,
                    details={"task_id": task_id},
                )
        await self._engine._repo.update_task(
            task_id,
            {
                "status": TaskStatus.RUNNING.value,
                "failure_reason": None,
                "updated_at": utc_now_iso(),
            },
        )
        await self._engine._mark_run_job(task_id, "running")
        if task["mode"] == TaskMode.AGENT.value:
            await self._engine._agent_runtime.resume_after_pause(
                task_id,
                pause_reason=str(task.get("failure_reason") or ""),
                trace_id=trace_id,
            )
        else:
            await self._engine._run_task(task_id, trace_id=trace_id)
        await self._engine._sync_run_job_to_task(task_id)
        return await self._engine.detail(task_id)

    async def handle_approval_resolved(
        self,
        approval_id: str,
        *,
        trace_id: str | None = None,
    ) -> Any:
        approval = await self._engine._repo.get_approval(approval_id)
        if approval is None:
            raise AppError(ErrorCode.NOT_FOUND, "审批不存在", status_code=404)
        task_id = approval["task_id"]
        task = await self._engine._get_task(task_id)
        if approval["status"] == "denied":
            if task["status"] in {
                TaskStatus.COMPLETED.value,
                TaskStatus.CANCELLED.value,
                TaskStatus.FAILED.value,
            }:
                return await self._engine.detail(task_id)
            if approval.get("step_id"):
                await self._engine._repo.update_step(
                    approval["step_id"],
                    {
                        "status": "failed",
                        "error_code": ErrorCode.APPROVAL_DENIED.value,
                        "error_summary": "用户拒绝审批",
                        "updated_at": utc_now_iso(),
                    },
                )
            await self._engine._transition_task(
                task_id,
                TaskStatus.PAUSED.value,
                trace_id=trace_id,
                extra={"failure_reason": "approval_denied"},
            )
            return await self._engine.detail(task_id)
        if approval["status"] in {"approved", "edited"}:
            step = (
                await self._engine._repo.get_step(approval["step_id"])
                if approval.get("step_id")
                else None
            )
            if (
                task.get("current_approval_id") != approval_id
                and task["status"] != TaskStatus.WAITING_APPROVAL.value
                and (
                    step is None
                    or step["status"] in {"completed", "running", "failed", "cancelled"}
                )
            ):
                return await self._engine.detail(task_id)
            await self._engine._event(
                task_id,
                "approval.resume.started",
                {"approval_id": approval_id, "step_id": approval.get("step_id")},
                step_id=approval.get("step_id"),
                trace_id=trace_id,
            )
            if approval.get("step_id"):
                if step is not None and step["status"] not in {"completed", "running"}:
                    next_fields = {
                        "status": "pending",
                        "updated_at": utc_now_iso(),
                    }
                    if approval.get("edited_payload"):
                        next_fields["input"] = self._engine._merge_edited_step_input(
                            step["input"],
                            approval["edited_payload"],
                        )
                    await self._engine._repo.update_step(approval["step_id"], next_fields)
            await self._engine._mark_run_job(task_id, "running")
            await self._engine._transition_task(
                task_id,
                TaskStatus.RUNNING.value,
                trace_id=trace_id,
                extra={"current_approval_id": None},
            )
            if task["mode"] == TaskMode.AGENT.value:
                await self._engine._agent_runtime.resume_after_pause(
                    task_id,
                    pause_reason="approval_waiting",
                    trace_id=trace_id,
                )
            else:
                await self._engine._run_task(task_id, trace_id=trace_id)
            await self._engine._sync_run_job_to_task(task_id)
            await self._engine._event(
                task_id,
                "approval.resume.completed",
                {"approval_id": approval_id, "task_id": task_id},
                step_id=approval.get("step_id"),
                trace_id=trace_id,
            )
            return await self._engine.detail(task_id)
        return await self._engine.detail(task_id)

    async def recover_stale_jobs(self) -> None:
        for job in await self._engine._repo.list_recoverable_jobs():
            task = await self._engine._repo.get_task(job["task_id"])
            if task is not None and task["status"] == TaskStatus.RUNNING.value:
                await self._engine._repo.update_task(
                    task["task_id"],
                    {
                        "status": TaskStatus.FAILED.value,
                        "failure_reason": "服务重启后运行中的任务已关闭",
                        "updated_at": utc_now_iso(),
                    },
                )
                await self._engine._repo.update_job_by_idempotency(
                    job["idempotency_key"],
                    {
                        "status": "failed",
                        "error_code": ErrorCode.TASK_STEP_FAILED.value,
                        "error_summary": "服务重启后运行中的任务已关闭",
                        "locked_by": None,
                        "locked_at": None,
                        "updated_at": utc_now_iso(),
                    },
                )

    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "task_resume_runtime",
            "approval_resume": True,
            "retry_supported": True,
            "stale_recovery": True,
            "public_entrypoints": [
                "retry_task",
                "handle_approval_resolved",
                "recover_stale_jobs",
            ],
        }
