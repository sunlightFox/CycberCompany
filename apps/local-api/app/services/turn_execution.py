from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

TurnRunner = Callable[[str], Awaitable[None]]


class TurnExecutionManager:
    def __init__(self, runner: TurnRunner) -> None:
        self._runner = runner
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(self, turn_id: str, *, delay_seconds: float = 0.0) -> None:
        task = self._tasks.get(turn_id)
        if task is not None and not task.done():
            return
        if delay_seconds > 0:
            self._tasks[turn_id] = asyncio.create_task(
                self._run_after(turn_id, delay_seconds)
            )
            return
        self._tasks[turn_id] = asyncio.create_task(self._run(turn_id))

    def is_running(self, turn_id: str) -> bool:
        task = self._tasks.get(turn_id)
        return task is not None and not task.done()

    async def close(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if not tasks:
            self._tasks.clear()
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _run(self, turn_id: str) -> None:
        try:
            await self._runner(turn_id)
        finally:
            self._tasks.pop(turn_id, None)

    async def _run_after(self, turn_id: str, delay_seconds: float) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await self._runner(turn_id)
        finally:
            self._tasks.pop(turn_id, None)
