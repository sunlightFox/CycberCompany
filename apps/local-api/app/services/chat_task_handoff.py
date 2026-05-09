from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class ChatTaskHandoff:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def execute_turn(self, turn: dict[str, Any], events: list[dict[str, Any]]) -> AsyncIterator[Any]:
        async for event in self._service._execute_turn_impl(turn, events):
            yield event
