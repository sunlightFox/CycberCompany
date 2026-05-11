from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from app.services.terminal_lane import (
    TERMINAL_LANE_BACKGROUND,
    TERMINAL_LANE_BROWSER_ASSIST,
    TERMINAL_LANE_MAIN,
    TERMINAL_LANE_READONLY,
    TERMINAL_LANE_RECOVERY,
    TERMINAL_LANES,
)


@dataclass
class _LaneState:
    lane: str
    active_count: int = 0
    queued_count: int = 0
    running_tool_call_ids: list[str] = field(default_factory=list)
    oldest_started_at: str | None = None


class TerminalQueueService:
    def __init__(self) -> None:
        self._shared_foreground = asyncio.Semaphore(1)
        self._controllers: dict[str, asyncio.Semaphore] = {
            TERMINAL_LANE_MAIN: self._shared_foreground,
            TERMINAL_LANE_READONLY: self._shared_foreground,
            TERMINAL_LANE_BROWSER_ASSIST: asyncio.Semaphore(1),
            TERMINAL_LANE_BACKGROUND: asyncio.Semaphore(2),
            TERMINAL_LANE_RECOVERY: asyncio.Semaphore(1),
        }
        self._states = {lane: _LaneState(lane=lane) for lane in TERMINAL_LANES}
        self._guard = asyncio.Lock()

    async def enqueue(
        self,
        lane: str,
        work: Callable[[], Awaitable[Any]],
        *,
        tool_call_id: str,
        timeout_seconds: int | None = None,
    ) -> Any:
        del timeout_seconds
        controller = self._controllers[lane]
        async with self._guard:
            self._states[lane].queued_count += 1
        await controller.acquire()
        async with self._guard:
            state = self._states[lane]
            state.queued_count = max(0, state.queued_count - 1)
            state.active_count += 1
            state.running_tool_call_ids.append(tool_call_id)
            if state.oldest_started_at is None:
                state.oldest_started_at = datetime.now(UTC).isoformat()
        try:
            return await work()
        finally:
            controller.release()
            async with self._guard:
                state = self._states[lane]
                state.active_count = max(0, state.active_count - 1)
                state.running_tool_call_ids = [
                    item for item in state.running_tool_call_ids if item != tool_call_id
                ]
                if state.active_count == 0:
                    state.oldest_started_at = None

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "lane": state.lane,
                "active_count": state.active_count,
                "queued_count": state.queued_count,
                "running_tool_call_ids": list(state.running_tool_call_ids),
                "oldest_started_at": state.oldest_started_at,
            }
            for state in self._states.values()
        ]

    def reset_lane(self, lane: str) -> int:
        state = self._states[lane]
        if state.active_count > 0:
            return 0
        state.queued_count = 0
        state.running_tool_call_ids = []
        state.oldest_started_at = None
        return 1

    def release_expired(self) -> int:
        released = 0
        for state in self._states.values():
            if state.active_count == 0 and state.oldest_started_at is not None:
                state.oldest_started_at = None
                released += 1
        return released
