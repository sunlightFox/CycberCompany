from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class ChatTurnFacade:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def create_turn(self, request: Any, *, retry_of_turn_id: str | None = None) -> Any:
        return await self._service._create_turn_impl(
            request,
            retry_of_turn_id=retry_of_turn_id,
        )

    async def stream_turn_events(self, turn_id: str) -> AsyncIterator[Any]:
        async for event in self._service._stream_turn_events_impl(turn_id):
            yield event

    async def run_turn(self, turn_id: str) -> None:
        await self._service._run_turn_impl(turn_id)

    async def recover_incomplete_turns(self) -> int:
        return await self._service._recover_incomplete_turns_impl()

    async def cancel_turn(self, turn_id: str) -> Any:
        return await self._service._cancel_turn_impl(turn_id)

    async def retry_turn(self, turn_id: str) -> Any:
        return await self._service._retry_turn_impl(turn_id)
