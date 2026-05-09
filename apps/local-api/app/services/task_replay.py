from __future__ import annotations

from typing import Any


class TaskReplayFacade:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def replay(self, task_id: str, *, trace_id: str | None = None) -> Any:
        return await self._engine._replay_impl(task_id, trace_id=trace_id)
