from __future__ import annotations

from typing import Any


class TaskAgentRuntime:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    async def run_agent_loop(self, task_id: str, *, trace_id: str | None) -> None:
        await self._engine._run_agent_loop_impl(task_id, trace_id=trace_id)

    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "task_agent_runtime",
            "observation_loop": True,
            "bounded_retry": True,
        }
