from __future__ import annotations

from typing import Any


class TaskWorkflowRuntime:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def run_task(self, task_id: str, *, trace_id: str | None) -> None:
        await self._engine._run_task_impl(task_id, trace_id=trace_id)

    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "task_workflow_runtime",
            "approval_wait_supported": True,
            "step_state_machine": True,
        }
