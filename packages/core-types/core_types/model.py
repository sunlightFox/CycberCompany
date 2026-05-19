from __future__ import annotations

from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import ModelRouteName


class ModelRoute(ApiModel):
    route: ModelRouteName
    reason: str
    privacy_level: str
    max_tokens: int | None = None
    temperature: float | None = None
    fallback: ModelRouteName | None = None


class ModelParams(ApiModel):
    temperature: float = 0.3
    top_p: float = 0.9
    max_output_tokens: int = 1024
    timeout_seconds: int = 180
    retry_count: int = 1


class ModelRouteDecision(ApiModel):
    route_id: EntityId
    primary_brain_id: EntityId
    fallback_brain_ids: list[EntityId] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    privacy_level: str = "medium"
    privacy_policy: str = "allow_local"
    context_budget: dict[str, int] = Field(default_factory=dict)
    model_params: ModelParams = Field(default_factory=ModelParams)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntentDecision(ApiModel):
    primary_intent: str
    turn_response_kind: str = "clarification_required"
    turn_response_reason_codes: list[str] = Field(default_factory=list)
    secondary_intents: list[str] = Field(default_factory=list)
    semantic_candidates: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    actionable_intents: list[str] = Field(default_factory=list)
    memory_intents: list[str] = Field(default_factory=list)
    tool_intents: list[str] = Field(default_factory=list)
    risk_intents: list[str] = Field(default_factory=list)
    domain: str = "general"
    complexity_score: float = 0.0
    privacy_level: str = "medium"
    risk_signals: list[str] = Field(default_factory=list)
    needs_memory: bool = False
    needs_tool: bool = False
    needs_skill: bool = False
    needs_mcp: bool = False
    needs_task: bool = False
    needs_clarification: bool = False
    confidence: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    rule_hits: list[str] = Field(default_factory=list)
    interaction_class: str = "direct_explanation"
    execution_policy: str = "no_task"
    direct_only_requested: bool = False
    model_hint: dict[str, Any] = Field(default_factory=dict)


class ModeDecision(ApiModel):
    mode: str
    submode: str = "default"
    planner_hint: str | None = None
    requires_approval_before_execute: bool = False
    fallback_mode: str | None = None
    confidence: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)


class ContextDecision(ApiModel):
    include_recent_messages: bool = True
    include_conversation_state: bool = True
    include_session_summary: bool = True
    include_memory: bool = True
    memory_query: dict[str, Any] = Field(default_factory=dict)
    include_persona: bool = True
    include_heart: bool = True
    include_capability_summary: bool = True
    include_asset_handles: bool = False
    include_task_state: bool = False
    include_artifact_summary: bool = False
    untrusted_refs: list[dict[str, Any]] = Field(default_factory=list)
    token_budget_profile: str = "balanced"
    selection_reason: list[str] = Field(default_factory=list)


class BrainDecisionBundle(ApiModel):
    brain_decision_id: EntityId
    intent: IntentDecision
    mode: ModeDecision
    context: ContextDecision
    clarification: dict[str, Any] = Field(default_factory=dict)
    turn_response_kind: str = "clarification_required"
    turn_response_reason_codes: list[str] = Field(default_factory=list)
    dialogue_state: dict[str, Any] | None = None
    semantic_intent_candidates: list[dict[str, Any]] = Field(default_factory=list)
    low_confidence_review: dict[str, Any] | None = None
    semantic_review: dict[str, Any] | None = None
    capability_snapshot: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    status: str = "completed"
    trace_id: EntityId | None = None
    created_at: str | None = None


class DialogueState(ApiModel):
    dialogue_state_id: EntityId
    conversation_id: EntityId
    member_id: EntityId
    active_topic: str | None = None
    user_goal: str | None = None
    goal_status: str = "active"
    goal_history: list[dict[str, Any]] = Field(default_factory=list)
    known_constraints: list[str] = Field(default_factory=list)
    soft_preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    decisions_made: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    pending_confirmation: dict[str, Any] = Field(default_factory=dict)
    topic_shift: bool = False
    last_user_action: str | None = None
    candidate_next_actions: list[str] = Field(default_factory=list)
    referenced_memories: list[dict[str, Any]] = Field(default_factory=list)
    referenced_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.0
    source_turn_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SemanticIntentCandidate(ApiModel):
    semantic_candidate_id: EntityId
    brain_decision_id: EntityId | None = None
    turn_id: EntityId | None = None
    conversation_id: EntityId | None = None
    member_id: EntityId
    primary_intent: str
    secondary_intents: list[str] = Field(default_factory=list)
    actionable_intents: list[str] = Field(default_factory=list)
    non_actionable_intents: list[str] = Field(default_factory=list)
    risk_intents: list[str] = Field(default_factory=list)
    memory_intents: list[str] = Field(default_factory=list)
    tool_intents: list[str] = Field(default_factory=list)
    skill_intents: list[str] = Field(default_factory=list)
    mcp_intents: list[str] = Field(default_factory=list)
    conversation_intents: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    model_hint: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: str | None = None


class LowConfidenceDecisionReview(ApiModel):
    review_id: EntityId
    brain_decision_id: EntityId | None = None
    turn_id: EntityId | None = None
    conversation_id: EntityId | None = None
    member_id: EntityId
    trigger_reasons: list[str] = Field(default_factory=list)
    rule_decision: dict[str, Any] = Field(default_factory=dict)
    verifier_suggestion: dict[str, Any] = Field(default_factory=dict)
    clarification_candidates: list[str] = Field(default_factory=list)
    fallback_used: bool = True
    model_assist_enabled: bool = False
    semantic_review_id: EntityId | None = None
    model_assist_attempted: bool = False
    schema_valid: bool | None = None
    fallback_reason: str | None = None
    risk_guard_applied: bool = False
    confidence: float = 0.0
    status: str = "fallback"
    trace_id: EntityId | None = None
    created_at: str | None = None


class ExecutionEvidenceDecision(ApiModel):
    status: str = "idle"
    is_complete: bool = False
    missing_evidence_types: list[str] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class TurnEnvelope(ApiModel):
    session_key: str = ""
    provider: str = "local"
    thread_key: str = ""
    sender_key: str = ""
    source_message_id: str = ""
    raw_text: str = ""
    normalized_text: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    context_refs: list[dict[str, Any]] = Field(default_factory=list)
    queue_policy: str = "immediate"
    reply_to_turn_id: str | None = None
    latest_instruction_override: bool = False
    active_pending_action_ref: str | None = None
    last_active_action_ref: str | None = None
    last_completed_action_ref: str | None = None
    last_artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    last_visible_reply_kind: str | None = None


class TurnContinuationDecision(ApiModel):
    turn_kind: str = "fresh_request"
    bound_action_ref: str | None = None
    bound_pending_ref: str | None = None
    bound_artifact_ref: str | None = None
    continuation_confidence: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)


class ActionLedgerEntry(ApiModel):
    action_ref: str
    session_key: str = ""
    provider: str = "local"
    route_type: str = ""
    intent: str = ""
    user_visible_goal: str = ""
    target_summary: str = ""
    approval_state: str = "not_required"
    execution_state: str = "idle"
    started_at: str | None = None
    ended_at: str | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    last_tool_result_refs: list[dict[str, Any]] = Field(default_factory=list)
    superseded_by: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class EvidenceLedgerEntry(ApiModel):
    action_ref: str
    evidence_type: str = ""
    ref: dict[str, Any] = Field(default_factory=dict)
    status: str = "recorded"
    created_at: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class VisibleReplyPlan(ApiModel):
    reply_mode: str = "normal"
    source: str = "fallback"
    text: str = ""
    bound_action_ref: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class SemanticReviewRequest(ApiModel):
    semantic_review_id: EntityId
    brain_decision_id: EntityId | None = None
    turn_id: EntityId | None = None
    conversation_id: EntityId | None = None
    member_id: EntityId
    redacted_user_text: str
    dialogue_state_summary: dict[str, Any] = Field(default_factory=dict)
    semantic_candidate_summary: dict[str, Any] = Field(default_factory=dict)
    intent_decision_summary: dict[str, Any] = Field(default_factory=dict)
    mode_decision_summary: dict[str, Any] = Field(default_factory=dict)
    context_decision_summary: dict[str, Any] = Field(default_factory=dict)
    capability_boundary_summary: dict[str, Any] = Field(default_factory=dict)
    risk_signal_summary: dict[str, Any] = Field(default_factory=dict)
    privacy_level: str = "medium"
    privacy_policy: str = "local_only"
    trigger_reasons: list[str] = Field(default_factory=list)
    status: str = "completed"
    trace_id: EntityId | None = None
    created_at: str | None = None


class SemanticReviewSuggestion(ApiModel):
    suggested_primary_intent: str | None = None
    suggested_secondary_intents: list[str] = Field(default_factory=list)
    suggested_mode: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    ambiguous_references: list[str] = Field(default_factory=list)
    context_conflicts: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    memory_notes: list[str] = Field(default_factory=list)
    tool_notes: list[str] = Field(default_factory=list)
    skill_notes: list[str] = Field(default_factory=list)
    mcp_notes: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    reason_summary: str = ""


class SemanticReviewMergeResult(ApiModel):
    merge_id: EntityId
    semantic_review_id: EntityId
    brain_decision_id: EntityId | None = None
    merged_intent: dict[str, Any] = Field(default_factory=dict)
    merged_mode: dict[str, Any] = Field(default_factory=dict)
    merged_context: dict[str, Any] = Field(default_factory=dict)
    merged_clarification: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    risk_monotonic_guard_applied: bool = False
    unsafe_downgrade_count: int = 0
    status: str = "completed"
    trace_id: EntityId | None = None
    created_at: str | None = None


class SemanticReviewResult(ApiModel):
    semantic_review_id: EntityId
    brain_decision_id: EntityId | None = None
    turn_id: EntityId | None = None
    conversation_id: EntityId | None = None
    member_id: EntityId
    request: SemanticReviewRequest
    suggestion: SemanticReviewSuggestion | None = None
    model_call: dict[str, Any] = Field(default_factory=dict)
    merge_result: SemanticReviewMergeResult | None = None
    fallback_used: bool = True
    fallback_reason: str | None = None
    model_assist_attempted: bool = False
    schema_valid: bool | None = None
    risk_guard_applied: bool = False
    unsafe_downgrade_count: int = 0
    status: str = "fallback"
    trace_id: EntityId | None = None
    created_at: str | None = None
