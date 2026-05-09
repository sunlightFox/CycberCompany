from __future__ import annotations

from typing import Any


class TaskWorkflowRunner:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def run_task(self, task_id: str, *, trace_id: str | None) -> None:
        await self._engine._run_task_impl(task_id, trace_id=trace_id)
