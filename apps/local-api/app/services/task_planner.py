from __future__ import annotations

from typing import Any


class TaskPlannerFacade:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def create_task(self, request: Any, *, trace_id: str | None = None) -> Any:
        return await self._engine._create_task_impl(request, trace_id=trace_id)
