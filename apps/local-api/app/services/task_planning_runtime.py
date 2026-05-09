from __future__ import annotations

from typing import Any


class TaskPlanningRuntime:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def create_task(self, request: Any, *, trace_id: str | None = None) -> Any:
        return await self._engine._create_task_impl(request, trace_id=trace_id)

    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "task_planning_runtime",
            "planner_candidate_only": True,
            "tool_execution_via_tool_runtime": True,
        }
