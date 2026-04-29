from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from core_types import ApiModel, ChatEvent, ChatEventType, ChatTurnRequest

PLACEHOLDER_RESPONSE = "模型还没有配置好。我已经记下这轮输入；配置大脑后，我就能正式处理。"


class PlaceholderTurn(ApiModel):
    assistant_text: str
    events: list[ChatEvent]


class ChatRuntime:
    async def run_placeholder(self, request: ChatTurnRequest, turn_id: str) -> PlaceholderTurn:
        return PlaceholderTurn(
            assistant_text=PLACEHOLDER_RESPONSE,
            events=self.placeholder_events(turn_id),
        )

    def placeholder_events(self, turn_id: str) -> list[ChatEvent]:
        timestamp = utc_now()
        return [
            ChatEvent(event=ChatEventType.TURN_STARTED, turn_id=turn_id, timestamp=timestamp),
            ChatEvent(event=ChatEventType.CONTEXT_STARTED, turn_id=turn_id, timestamp=timestamp),
            ChatEvent(
                event=ChatEventType.CONTEXT_READY,
                turn_id=turn_id,
                timestamp=timestamp,
                payload={"memories": [], "capabilities": [], "resource_handles": []},
            ),
            ChatEvent(
                event=ChatEventType.MODEL_PLACEHOLDER,
                turn_id=turn_id,
                timestamp=timestamp,
                payload={"reason": "brain_not_configured"},
            ),
            ChatEvent(
                event=ChatEventType.RESPONSE_DELTA,
                turn_id=turn_id,
                timestamp=timestamp,
                payload={"text": PLACEHOLDER_RESPONSE},
            ),
            ChatEvent(event=ChatEventType.RESPONSE_COMPLETED, turn_id=turn_id, timestamp=timestamp),
            ChatEvent(event=ChatEventType.TURN_COMPLETED, turn_id=turn_id, timestamp=timestamp),
        ]

    def event(
        self,
        event: ChatEventType,
        *,
        turn_id: str,
        trace_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ChatEvent:
        return ChatEvent(
            event=event,
            turn_id=turn_id,
            trace_id=trace_id,
            timestamp=utc_now(),
            payload=payload or {},
        )

    def failed_events(self, turn_id: str, reason: str) -> list[ChatEvent]:
        timestamp = utc_now()
        return [
            ChatEvent(
                event=ChatEventType.TURN_FAILED,
                turn_id=turn_id,
                timestamp=timestamp,
                payload={"reason": reason},
            )
        ]


def utc_now() -> datetime:
    return datetime.now(UTC)
