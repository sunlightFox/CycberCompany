from __future__ import annotations

from typing import Any

from pydantic import Field

from core_types.common import ApiModel


class ConversationUnderstandingShadow(ApiModel):
    version: str = "chat_quality_shadow.v1"
    primary_scene: str = "casual_chat"
    expected_tone: str = "natural"
    continues_previous_turn: bool = False
    latest_instruction_override: bool = False
    constraint_tightening: bool = False
    action_request: bool = False
    tool_followup: bool = False
    memory_related: bool = False
    depth_signal: str = "light"
    quality_dimensions: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class ChatDialogueStateShadow(ApiModel):
    version: str = "chat_quality_shadow.v1"
    active_topic: str | None = None
    user_goal: str | None = None
    open_loops: list[str] = Field(default_factory=list)
    pending_confirmation: dict[str, Any] = Field(default_factory=dict)
    turn_continuity: str = "standalone"
    topic_shift_confidence: float = 0.0
    source_dialogue_state_present: bool = False
    quality_dimensions: list[str] = Field(default_factory=list)


class ResponsePolicyShadow(ApiModel):
    version: str = "chat_quality_shadow.v1"
    opening_style: str = "natural_direct"
    depth_mode: str = "light"
    followthrough_mode: str = "standalone"
    boundary_mode: str = "none"
    tool_narration_mode: str = "answer_directly"
    memory_reference_mode: str = "do_not_force"
    continuation_expectation: str = "optional"
    quality_dimensions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class ActionDialogueMappingShadow(ApiModel):
    version: str = "chat_quality_shadow.v1"
    action_status: str = "no_action"
    narration_style: str = "answer_directly"
    should_explain_pending: bool = False
    should_claim_completion: bool = False
    natural_transition: str = "none"
    blocked_by_approval: bool = False
    related_capabilities: list[str] = Field(default_factory=list)
    quality_dimensions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class ChatQualityShadowEvaluation(ApiModel):
    version: str = "chat_quality_shadow.v1"
    quality_tags: list[str] = Field(default_factory=list)
    quality_score_hint: float = 1.0
    risk_notes: list[str] = Field(default_factory=list)


class ShadowPolicyAdvisoryGate(ApiModel):
    eligible_for_policy_advisory: bool = False
    eligibility_reason: str = "not_evaluated"
    eligibility_tags: list[str] = Field(default_factory=list)
    eligible_scene: str | None = None


class ShadowPolicyComparison(ApiModel):
    comparison_enabled: bool = False
    baseline_policy: dict[str, Any] = Field(default_factory=dict)
    advisory_policy: dict[str, Any] | None = None
    policy_diffs: list[str] = Field(default_factory=list)
    advisory_summary: str | None = None
    safe_to_promote_hint: bool = False
