from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(slots=True)
class CancelToken:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass(slots=True)
class ModelChatRequest:
    model: str
    messages: list[dict[str, str]]
    temperature: float
    max_output_tokens: int
    top_p: float
    timeout_seconds: int
    stream: bool
    trace_id: str
    turn_id: str
    route_id: str
    privacy_level: str
    first_token_timeout_seconds: int = 30
    retry_count: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelStreamEvent:
    event: Literal["started", "delta", "usage_delta", "completed", "failed", "cancelled"]
    text: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelChatResult:
    text: str
    usage: dict[str, Any]
    finish_reason: str
    metadata: dict[str, Any] = field(default_factory=dict)
