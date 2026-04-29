from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from brain.adapters import CancelToken
from core_types import ChatEvent


@dataclass(slots=True)
class StoredTurnEvent:
    sequence: int
    event: ChatEvent


class TurnEventStore:
    def __init__(self) -> None:
        self._events: dict[str, list[StoredTurnEvent]] = {}
        self._tokens: dict[str, CancelToken] = {}
        self._conditions: dict[str, asyncio.Condition] = {}
        self._completed: set[str] = set()

    def token_for(self, turn_id: str) -> CancelToken:
        token = self._tokens.get(turn_id)
        if token is None:
            token = CancelToken()
            self._tokens[turn_id] = token
        return token

    async def append(self, turn_id: str, sequence: int, event: ChatEvent) -> None:
        events = self._events.setdefault(turn_id, [])
        events.append(StoredTurnEvent(sequence=sequence, event=event))
        condition = self._conditions.setdefault(turn_id, asyncio.Condition())
        async with condition:
            condition.notify_all()

    async def mark_completed(self, turn_id: str) -> None:
        self._completed.add(turn_id)
        condition = self._conditions.setdefault(turn_id, asyncio.Condition())
        async with condition:
            condition.notify_all()

    def cancel(self, turn_id: str) -> None:
        self.token_for(turn_id).cancel()

    def get_events(self, turn_id: str) -> list[StoredTurnEvent]:
        return list(self._events.get(turn_id, []))

    async def subscribe(self, turn_id: str, *, after_sequence: int = 0) -> AsyncIterator[ChatEvent]:
        while True:
            events = self._events.get(turn_id, [])
            for stored in events:
                if stored.sequence > after_sequence:
                    after_sequence = stored.sequence
                    yield stored.event
            if turn_id in self._completed:
                return
            condition = self._conditions.setdefault(turn_id, asyncio.Condition())
            async with condition:
                await condition.wait()
