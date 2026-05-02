from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    ChatEvent,
    ChatTurnRecoveryAttempt,
    ChatTurnRequest,
    ChatTurnResponse,
    DialogueState,
    LowConfidenceDecisionReview,
    ResponseQualityEvaluation,
    SemanticIntentCandidate,
    SemanticReviewResult,
    TonePolicyResolution,
)
from pydantic import Field


class MessageItem(ApiModel):
    message_id: str
    conversation_id: str
    turn_id: str | None = None
    author_type: str
    author_id: str | None = None
    content_type: str
    content_text: str | None = None
    content: dict[str, Any]
    trace_id: str | None = None
    created_at: str


class ConversationListItem(ApiModel):
    conversation_id: str
    organization_id: str
    title: str | None = None
    conversation_type: str
    primary_member_id: str | None = None
    participants: list[dict[str, Any]]
    status: str
    created_at: str
    updated_at: str


class ConversationDetail(ConversationListItem):
    messages: list[MessageItem]


class ConversationListResponse(ApiModel):
    items: list[ConversationListItem]


class ChatEventStreamResponse(ApiModel):
    items: list[ChatEvent]


class ChatTurnDetail(ApiModel):
    turn_id: str
    conversation_id: str
    member_id: str
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    trace_id: str
    status: str
    intent: str | None = None
    mode: str | None = None
    privacy_level: str | None = None
    route: dict[str, Any]
    usage: dict[str, Any]
    experience: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    retry_of_turn_id: str | None = None
    brain_decision_id: str | None = None
    cancel_requested: bool
    created_at: str
    updated_at: str
    ended_at: str | None = None


class ChatPersistedEvent(ApiModel):
    event_id: str
    turn_id: str
    sequence: int
    event_type: str
    trace_id: str | None = None
    payload: dict[str, Any]
    created_at: str


class ChatPersistedEventsResponse(ApiModel):
    items: list[ChatPersistedEvent]


class ChatTurnRecoveryAttemptListResponse(ApiModel):
    items: list[ChatTurnRecoveryAttempt] = Field(default_factory=list)


class ConversationWorkingStateResponse(ApiModel):
    conversation_id: str
    organization_id: str
    active_topic: str | None = None
    user_goal: str | None = None
    known_constraints: list[str] = Field(default_factory=list)
    decisions_made: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    candidate_actions: list[str] = Field(default_factory=list)
    referenced_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    last_response_summary: str | None = None
    pending_confirmation: dict[str, Any] = Field(default_factory=dict)
    source_turn_id: str | None = None
    confidence: float
    status: str
    created_at: str
    updated_at: str


class ChatClarificationDecisionResponse(ApiModel):
    clarification_id: str | None = None
    turn_id: str
    conversation_id: str
    needs_clarification: bool
    reason: str
    clarification_type: str = "none"
    blocking_level: str
    questions: list[str] = Field(default_factory=list)
    can_answer_partially: bool = False
    trace_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class DialogueStateResponse(DialogueState):
    pass


class SemanticIntentCandidatesResponse(ApiModel):
    items: list[SemanticIntentCandidate] = Field(default_factory=list)


class LowConfidenceDecisionReviewResponse(LowConfidenceDecisionReview):
    pass


class SemanticReviewResponse(SemanticReviewResult):
    pass


class SemanticReviewEventsResponse(ApiModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class TonePolicyResolutionResponse(TonePolicyResolution):
    pass


class ResponseQualityEvaluationResponse(ResponseQualityEvaluation):
    pass


__all__ = [
    "ChatClarificationDecisionResponse",
    "ChatEvent",
    "ChatEventStreamResponse",
    "ChatPersistedEvent",
    "ChatPersistedEventsResponse",
    "ChatTurnRecoveryAttempt",
    "ChatTurnRecoveryAttemptListResponse",
    "ChatTurnDetail",
    "ChatTurnRequest",
    "ChatTurnResponse",
    "ConversationDetail",
    "DialogueStateResponse",
    "LowConfidenceDecisionReviewResponse",
    "ResponseQualityEvaluationResponse",
    "SemanticReviewEventsResponse",
    "SemanticReviewResponse",
    "ConversationListResponse",
    "ConversationListItem",
    "ConversationWorkingStateResponse",
    "MessageItem",
    "SemanticIntentCandidatesResponse",
    "TonePolicyResolutionResponse",
]
