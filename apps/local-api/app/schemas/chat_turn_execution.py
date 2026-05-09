from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core_types import ContextPacket

StageDisposition = Literal["continue", "complete", "fail"]


@dataclass
class ChatTurnTerminalOutcome:
    status: str
    code: str | None = None
    message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatTurnStageResult:
    disposition: StageDisposition
    outcome: ChatTurnTerminalOutcome | None = None

    @classmethod
    def continue_(cls) -> ChatTurnStageResult:
        return cls(disposition="continue")

    @classmethod
    def complete(
        cls,
        *,
        status: str = "completed",
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChatTurnStageResult:
        return cls(
            disposition="complete",
            outcome=ChatTurnTerminalOutcome(
                status=status,
                message=message,
                metadata=dict(metadata or {}),
            ),
        )

    @classmethod
    def fail(
        cls,
        *,
        status: str = "failed",
        code: str | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ChatTurnStageResult:
        return cls(
            disposition="fail",
            outcome=ChatTurnTerminalOutcome(
                status=status,
                code=code,
                message=message,
                metadata=dict(metadata or {}),
            ),
        )


@dataclass
class ChatTurnExecutionContext:
    turn: dict[str, Any]
    events: list[dict[str, Any]]
    root_span_id: str | None = None
    user_text: str = ""
    session_id: str | None = None
    privacy: Any | None = None
    brain_decision: Any | None = None
    context_packet: ContextPacket | None = None
    route_decision: Any | None = None
    presence_runtime: dict[str, Any] = field(default_factory=dict)
    chat_quality_shadow: dict[str, Any] = field(default_factory=dict)
    queue_item: dict[str, Any] | None = None
    envelope: dict[str, Any] | None = None

