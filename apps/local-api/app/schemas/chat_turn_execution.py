from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from core_types import ContextPacket

StageDisposition = Literal["continue", "complete", "fail"]


@dataclass
class TurnExecutionPlan:
    turn_id: str
    conversation_id: str | None = None
    member_id: str | None = None
    intent: str | None = None
    mode: str | None = None
    route: str | None = None
    context_policy: dict[str, Any] = field(default_factory=dict)
    persona_policy: dict[str, Any] = field(default_factory=dict)
    capability_intent: dict[str, Any] = field(default_factory=dict)
    model_policy: dict[str, Any] = field(default_factory=dict)
    attachment_requirements: dict[str, Any] = field(default_factory=dict)
    output_requirements: dict[str, Any] = field(default_factory=dict)
    delivery_requirements: dict[str, Any] = field(default_factory=dict)
    evidence_requirements: dict[str, Any] = field(default_factory=dict)
    approval_requirements: dict[str, Any] = field(default_factory=dict)
    completion_semantics: dict[str, Any] = field(default_factory=dict)
    response_contract: dict[str, Any] = field(default_factory=dict)
    trace_metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "member_id": self.member_id,
            "intent": self.intent,
            "mode": self.mode,
            "route": self.route,
            "context_policy": dict(self.context_policy),
            "persona_policy": dict(self.persona_policy),
            "capability_intent": dict(self.capability_intent),
            "model_policy": dict(self.model_policy),
            "attachment_requirements": dict(self.attachment_requirements),
            "output_requirements": dict(self.output_requirements),
            "delivery_requirements": dict(self.delivery_requirements),
            "evidence_requirements": dict(self.evidence_requirements),
            "approval_requirements": dict(self.approval_requirements),
            "completion_semantics": dict(self.completion_semantics),
            "response_contract": dict(self.response_contract),
            "trace_metadata": dict(self.trace_metadata),
        }


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
    execution_plan: TurnExecutionPlan | None = None
    user_text: str = ""
    session_id: str | None = None
    privacy: Any | None = None
    brain_decision: Any | None = None
    context_packet: ContextPacket | None = None
    context_runtime: dict[str, Any] = field(default_factory=dict)
    route_decision: Any | None = None
    presence_runtime: dict[str, Any] = field(default_factory=dict)
    chat_quality_shadow: dict[str, Any] = field(default_factory=dict)
    queue_item: dict[str, Any] | None = None
    envelope: dict[str, Any] | None = None
