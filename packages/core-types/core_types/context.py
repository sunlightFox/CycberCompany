from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import MemberStatus, RiskLevel


class MemberSummary(ApiModel):
    member_id: EntityId
    display_name: str
    avatar_uri: str | None = None
    status: MemberStatus
    default_brain_id: EntityId | None = None


class BrainSummary(ApiModel):
    brain_id: EntityId
    display_name: str
    provider: str
    model_name: str
    status: str


class PersonaSummary(ApiModel):
    persona_profile_id: EntityId
    summary: str
    mode: str | None = None
    tone_policy: dict[str, Any] = Field(default_factory=dict)
    disclosure_policy: dict[str, Any] = Field(default_factory=dict)
    risk_tone_policy: dict[str, Any] = Field(default_factory=dict)
    allowed_modes: list[str] = Field(default_factory=list)
    default_mode: str | None = None
    tone_hints: list[str] = Field(default_factory=list)
    disclosure_hints: list[str] = Field(default_factory=list)
    style_principles: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    mode_switch_rules: list[dict[str, Any]] = Field(default_factory=list)
    consistency_markers: list[str] = Field(default_factory=list)
    soul_snapshot: dict[str, Any] = Field(default_factory=dict)
    soul_content_hash: str | None = None
    soul_compiled_at: str | None = None
    soul_validation_status: str | None = None
    soul_validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    catchphrases: list[str] = Field(default_factory=list)
    custom_sections: list[dict[str, Any]] = Field(default_factory=list)
    memory_policy: dict[str, Any] = Field(default_factory=dict)


class HeartSummary(ApiModel):
    member_id: EntityId
    snapshot_id: EntityId | None = None
    mood: str = "steady"
    urgency: str = "normal"
    user_state: str = "steady"
    preferred_pace: str = "normal"
    relationship_temperature: float = 0.6
    companionship_intensity: float = 0.5
    deescalation_boundary: str | None = None
    deescalation_required: bool = False
    risk_tone_override: str | None = None
    confidence: float = 0.6
    summary: str = "steady"
    previous_snapshot_id: EntityId | None = None
    source_turn_id: EntityId | None = None
    transition_factors: list[str] = Field(default_factory=list)
    state_delta: dict[str, Any] = Field(default_factory=dict)


class MemoryBlockItem(ApiModel):
    memory_id: EntityId
    kind: str
    summary: str
    confidence: float
    source_ref: dict[str, Any] = Field(default_factory=dict)


class MemoryBlock(ApiModel):
    block_id: EntityId
    block_type: str
    title: str
    items: list[MemoryBlockItem] = Field(default_factory=list)
    token_estimate: int = 0
    selection_reason: list[str] = Field(default_factory=list)


class CapabilitySummary(ApiModel):
    subject_id: EntityId
    allowed_actions: list[str] = Field(default_factory=list)
    denied_actions: list[str] = Field(default_factory=list)
    reason: str | None = None


class ResourceHandleSummary(ApiModel):
    handle_id: EntityId
    asset_id: EntityId
    asset_type: str
    summary: str
    allowed_actions: list[str] = Field(default_factory=list)
    approval_required_actions: list[str] = Field(default_factory=list)
    verification_summary: str | None = None
    freshness_summary: str | None = None


class SafetyNote(ApiModel):
    risk_level: RiskLevel
    summary: str
    source: str | None = None
    reason_codes: list[str] = Field(default_factory=list)


class ConversationContext(ApiModel):
    conversation_id: EntityId
    recent_summary: str | None = None
    last_messages: list[dict[str, Any]] = Field(default_factory=list)
    summary_layers: dict[str, Any] = Field(default_factory=dict)


class WorkbenchContext(ApiModel):
    context_pack_id: EntityId | None = None
    context_file_version_id: EntityId | None = None
    summary: str | None = None
    memory_refs: list[dict[str, Any]] = Field(default_factory=list)
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    context_file_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    token_estimate: int = 0
    generated_at: str | None = None


class ContextPacket(ApiModel):
    context_packet_id: EntityId
    member: MemberSummary
    brain: BrainSummary | None = None
    persona: PersonaSummary | None = None
    heart: HeartSummary | None = None
    conversation: ConversationContext
    session_context: dict[str, Any] = Field(default_factory=dict)
    memories: list[MemoryBlock] = Field(default_factory=list)
    capabilities: list[CapabilitySummary] = Field(default_factory=list)
    resource_handles: list[ResourceHandleSummary] = Field(default_factory=list)
    safety_notes: list[SafetyNote] = Field(default_factory=list)
    untrusted_context: list[dict[str, Any]] = Field(default_factory=list)
    workbench: WorkbenchContext | None = None
    context_diagnostics: dict[str, Any] = Field(default_factory=dict)


class ResponsePlan(ApiModel):
    VISIBLE_LAYER_FIELDS: ClassVar[tuple[str, ...]] = (
        "plain_text",
        "sections",
        "reply_blocks",
        "approval_prompt",
        "action_buttons",
        "user_next_step",
        "visible_status_hint",
        "channel_render_overrides",
    )
    INTERNAL_LAYER_FIELDS: ClassVar[tuple[str, ...]] = (
        "structured_payload",
        "response_filter",
        "response_quality_guard",
        "route_semantics",
        "task_status_semantics",
        "tool_status_semantics",
        "memory_write_hints",
        "prompt_contract_metadata",
    )

    title: str | None = None
    style: str = "result_first"
    sections: list[dict[str, Any]] = Field(default_factory=list)
    reply_blocks: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    code_blocks: list[dict[str, Any]] = Field(default_factory=list)
    action_buttons: list[dict[str, Any]] = Field(default_factory=list)
    tone: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    approval_prompt: dict[str, Any] | None = None
    visible_status_hint: str | None = None
    channel_render_overrides: dict[str, Any] = Field(default_factory=dict)
    task_status: dict[str, Any] | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    safety_notice: str | None = None
    memory_notice: str | None = None
    tool_notice: str | None = None
    follow_up_options: list[str] = Field(default_factory=list)
    tone_metadata: dict[str, Any] = Field(default_factory=dict)
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    trace_refs: list[dict[str, Any]] = Field(default_factory=list)
    plain_text: str | None = None
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    response_filter: dict[str, Any] = Field(default_factory=dict)
    response_quality_guard: dict[str, Any] = Field(default_factory=dict)
    route_semantics: dict[str, Any] = Field(default_factory=dict)
    task_status_semantics: dict[str, Any] = Field(default_factory=dict)
    tool_status_semantics: dict[str, Any] = Field(default_factory=dict)
    memory_write_hints: dict[str, Any] = Field(default_factory=dict)
    prompt_contract_metadata: dict[str, Any] = Field(default_factory=dict)
    tone_mode: str | None = None
    quality_markers: dict[str, Any] = Field(default_factory=dict)
    boundary_notice: str | None = None
    continuity_refs: list[dict[str, Any]] = Field(default_factory=list)
    deescalation_notice: str | None = None
    user_next_step: str | None = None

    def visible_layer_payload(self) -> dict[str, Any]:
        return {
            "plain_text": self.plain_text,
            "sections": list(self.sections),
            "reply_blocks": list(self.reply_blocks or self.sections),
            "approval_prompt": self.approval_prompt,
            "action_buttons": list(self.action_buttons),
            "user_next_step": self.user_next_step,
            "visible_status_hint": self.visible_status_hint,
            "channel_render_overrides": dict(self.channel_render_overrides),
        }

    def internal_layer_payload(self) -> dict[str, Any]:
        return {
            "structured_payload": dict(self.structured_payload),
            "response_filter": dict(self.response_filter),
            "response_quality_guard": dict(self.response_quality_guard),
            "route_semantics": dict(self.route_semantics),
            "task_status_semantics": dict(self.task_status_semantics),
            "tool_status_semantics": dict(self.tool_status_semantics),
            "memory_write_hints": dict(self.memory_write_hints),
            "prompt_contract_metadata": dict(self.prompt_contract_metadata),
        }

    def layer_diagnostics(self) -> dict[str, Any]:
        return {
            "visible_fields": list(self.VISIBLE_LAYER_FIELDS),
            "internal_fields": list(self.INTERNAL_LAYER_FIELDS),
            "visible_authority": "response_plan_plain_text",
        }


class PersonaConsistencyProfile(ApiModel):
    consistency_profile_id: EntityId
    organization_id: EntityId = "org_default"
    persona_profile_id: EntityId
    member_id: EntityId | None = None
    style_principles: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    mode_switch_rules: list[dict[str, Any]] = Field(default_factory=list)
    consistency_markers: list[str] = Field(default_factory=list)
    disabled_patterns: list[str] = Field(default_factory=list)
    source: str = "phase22_default"
    status: str = "active"
    trace_id: EntityId | None = None
    created_at: str | None = None
    updated_at: str | None = None


class HeartStateTransition(ApiModel):
    transition_id: EntityId
    organization_id: EntityId = "org_default"
    member_id: EntityId
    previous_snapshot_id: EntityId | None = None
    current_snapshot_id: EntityId
    source_turn_id: EntityId | None = None
    transition_factors: list[str] = Field(default_factory=list)
    state_delta: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.6
    status: str = "active"
    trace_id: EntityId | None = None
    created_at: str


class TonePolicyResolution(ApiModel):
    resolution_id: EntityId
    organization_id: EntityId = "org_default"
    turn_id: EntityId | None = None
    member_id: EntityId | None = None
    persona_profile_id: EntityId | None = None
    heart_snapshot_id: EntityId | None = None
    scenario: str
    risk_level: str = "R1"
    tone_mode: str
    conciseness: float = 0.72
    warmth: float = 0.68
    directness: float = 0.78
    technical_depth: float = 0.66
    anthropomorphic_level: float = 0.35
    disclosure_required: bool = False
    safety_notice_required: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: str


class ResponseQualityEvaluation(ApiModel):
    evaluation_id: EntityId
    organization_id: EntityId = "org_default"
    turn_id: EntityId | None = None
    response_plan: dict[str, Any] = Field(default_factory=dict)
    rubric: dict[str, Any] = Field(default_factory=dict)
    quality_markers: dict[str, Any] = Field(default_factory=dict)
    violations: list[dict[str, Any]] = Field(default_factory=list)
    score: float = 0.0
    passed: bool = False
    internal_leakage_count: int = 0
    high_risk_boundary_violation_count: int = 0
    trace_id: EntityId | None = None
    created_at: str


class PersonaHeartReplayRun(ApiModel):
    run_id: EntityId
    organization_id: EntityId = "org_default"
    suite_id: EntityId = "suite_phase22_persona_heart_experience"
    case_key: str
    status: str
    turn_count: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    violation_counts: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: str
    completed_at: str | None = None
