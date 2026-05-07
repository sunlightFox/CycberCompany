from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from core_types.common import ApiModel


class ConversationUnderstandingRequest(ApiModel):
    turn_id: str
    conversation_id: str
    member_id: str
    user_text: str
    message_type: str = "text"
    channel_profile: str | None = None
    delivery_mode: str | None = None
    sender_label: str | None = None
    has_multimodal_parts: bool = False
    has_pending_action: bool = False
    has_running_task: bool = False
    latest_summary: str | None = None
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    user_profile: dict[str, Any] = Field(default_factory=dict)
    continuity_summary: str | None = None
    trace_id: str | None = None


class ConversationUnderstanding(ApiModel):
    conversation_mode: Literal[
        "casual",
        "deep_talk",
        "question",
        "task_request",
        "confirmation",
        "boundary",
        "memory_update",
        "memory_correction",
        "clarification",
    ] = "question"
    user_goal: str = ""
    relationship_expectation: Literal[
        "companionship",
        "advice",
        "execution",
        "confirmation",
        "explanation",
    ] = "explanation"
    current_turn_priority: Literal[
        "reply_first",
        "clarify_first",
        "act_first",
        "block_first",
        "repair_first",
    ] = "reply_first"
    emotional_state: Literal[
        "neutral",
        "warm",
        "anxious",
        "frustrated",
        "urgent",
        "playful",
        "sad",
        "angry",
    ] = "neutral"
    latest_instruction_override: bool = False
    must_not_do: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    reason_codes: list[str] = Field(default_factory=list)
    interaction_posture_candidates: list[str] = Field(default_factory=list)
    repair_needed: bool = False


class PresenceStateRequest(ApiModel):
    turn_id: str
    conversation_id: str
    member_id: str
    user_text: str
    understanding: ConversationUnderstanding
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    working_state: dict[str, Any] = Field(default_factory=dict)
    memory_candidates: list[dict[str, Any]] = Field(default_factory=list)
    user_profile: dict[str, Any] = Field(default_factory=dict)
    latest_continuity: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None


class PresenceState(ApiModel):
    identity_state: dict[str, Any] = Field(default_factory=dict)
    relationship_state: dict[str, Any] = Field(default_factory=dict)
    conversation_state: dict[str, Any] = Field(default_factory=dict)
    action_state: dict[str, Any] = Field(default_factory=dict)
    memory_state: dict[str, Any] = Field(default_factory=dict)
    session_state: dict[str, Any] = Field(default_factory=dict)
    interaction_posture: Literal[
        "take_over",
        "steady",
        "clarify_minimally",
        "boundary_but_helpful",
        "repair_previous_miss",
        "result_delivery",
    ] = "steady"
    reason_codes: list[str] = Field(default_factory=list)


class ResponsePolicyRequest(ApiModel):
    understanding: ConversationUnderstanding
    presence_state: PresenceState
    response_plan: dict[str, Any] = Field(default_factory=dict)
    privacy_level: str | None = None


class ResponsePolicyDecision(ApiModel):
    opening_style: str = "natural_direct"
    depth_mode: str = "light"
    followthrough_mode: str = "standalone"
    boundary_mode: str = "none"
    progress_mode: str = "answer_directly"
    memory_reference_mode: str = "do_not_force"
    structure_mode: str = "adaptive"
    tone_guardrails: list[str] = Field(default_factory=list)
    continuation_expectation: str = "optional"
    visible_failure_strategy: str = "partial_honest"
    reason_codes: list[str] = Field(default_factory=list)


class ActionDialogueFacts(ApiModel):
    action_label: str = ""
    target: str = ""
    detail_status: str = ""
    failure_reason: str = ""
    evidence_summary: str = ""
    reply_options: list[str] = Field(default_factory=list)
    route_semantics: dict[str, Any] = Field(default_factory=dict)
    natural_interaction: dict[str, Any] = Field(default_factory=dict)
    task_status: dict[str, Any] = Field(default_factory=dict)
    approval_pending: bool = False
    tool_created: bool = False
    task_created: bool = False


class ActionDialogueDecision(ApiModel):
    action_status: str = "no_action"
    narration_style: str = "answer_directly"
    natural_transition: str = "none"
    should_explain_pending: bool = False
    should_claim_completion: bool = False
    blocked_by_approval: bool = False
    visible_failure_strategy: str = "partial_honest"
    related_capabilities: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
