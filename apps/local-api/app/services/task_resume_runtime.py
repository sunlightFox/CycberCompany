from __future__ import annotations

from typing import Any


class TaskResumeRuntime:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def retry_task(self, task_id: str, *, trace_id: str | None = None) -> Any:
        return await self._engine._retry_task_impl(task_id, trace_id=trace_id)

    async def handle_approval_resolved(
        self,
        approval_id: str,
        *,
        trace_id: str | None = None,
    ) -> Any:
        return await self._engine._handle_approval_resolved_impl(
            approval_id,
            trace_id=trace_id,
        )

    async def recover_stale_jobs(self) -> None:
        await self._engine._recover_stale_jobs_impl()

    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "task_resume_runtime",
            "approval_resume": True,
            "retry_supported": True,
            "stale_recovery": True,
        }
