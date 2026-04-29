from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    HeartStateTransition,
    PersonaConsistencyProfile,
    PersonaHeartReplayRun,
    ResponsePlan,
    ResponseQualityEvaluation,
    RiskLevel,
    SafetyDecision,
    TonePolicyResolution,
)
from pydantic import Field


class SafetyEvaluateRequest(ApiModel):
    actor_type: str = "member"
    actor_id: EntityId = "mem_xiaoyao"
    organization_id: EntityId = "org_default"
    task_id: EntityId | None = None
    action_type: str = "generic"
    action: str
    object_type: str = "runtime_action"
    object_id: EntityId | None = None
    tool_name: str | None = None
    skill_id: str | None = None
    mcp_server_id: str | None = None
    mcp_tool_id: str | None = None
    payload_summary: dict[str, Any] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    asset_handles: list[EntityId] = Field(default_factory=list)
    destination: str | None = None
    risk_hints: list[str] = Field(default_factory=list)
    untrusted_refs: list[dict[str, Any]] = Field(default_factory=list)


class SafetyDecisionResponse(SafetyDecision):
    organization_id: EntityId = "org_default"
    actor_type: str = "member"
    actor_id: EntityId = "mem_xiaoyao"
    task_id: EntityId | None = None
    action_type: str = "generic"
    action: str
    object_type: str
    object_id: EntityId | None = None
    asset_handles: list[EntityId] = Field(default_factory=list)
    destination: str | None = None
    trace_id: EntityId | None = None
    created_at: str | None = None


class PersonaProfileResponse(ApiModel):
    persona_profile_id: EntityId
    organization_id: EntityId = "org_default"
    member_id: EntityId | None = None
    display_name: str
    summary: str
    tone_policy: dict[str, Any] = Field(default_factory=dict)
    disclosure_policy: dict[str, Any] = Field(default_factory=dict)
    risk_tone_policy: dict[str, Any] = Field(default_factory=dict)
    allowed_modes: list[str] = Field(default_factory=list)
    default_mode: str = "default"
    style_principles: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    mode_switch_rules: list[dict[str, Any]] = Field(default_factory=list)
    consistency_markers: list[str] = Field(default_factory=list)
    shell_label_mapping: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None


class PersonaProfileUpdateRequest(ApiModel):
    display_name: str | None = None
    summary: str | None = None
    tone_policy: dict[str, Any] | None = None
    disclosure_policy: dict[str, Any] | None = None
    risk_tone_policy: dict[str, Any] | None = None
    allowed_modes: list[str] | None = None
    default_mode: str | None = None
    style_principles: list[str] | None = None
    forbidden_claims: list[str] | None = None
    mode_switch_rules: list[dict[str, Any]] | None = None
    consistency_markers: list[str] | None = None
    disabled_patterns: list[str] | None = None
    shell_label_mapping: dict[str, Any] | None = None
    status: str | None = None


class PersonaProfileListResponse(ApiModel):
    items: list[PersonaProfileResponse] = Field(default_factory=list)


class HeartStateResponse(ApiModel):
    snapshot_id: EntityId
    organization_id: EntityId = "org_default"
    member_id: EntityId
    mood: str
    urgency: str
    user_state: str = "steady"
    preferred_pace: str = "normal"
    relationship_temperature: float
    companionship_intensity: float
    deescalation_boundary: str | None = None
    deescalation_required: bool = False
    risk_tone_override: str | None = None
    confidence: float = 0.6
    summary: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    previous_snapshot_id: EntityId | None = None
    source_turn_id: EntityId | None = None
    transition_factors: list[str] = Field(default_factory=list)
    state_delta: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: str


class PersonaConsistencyProfileResponse(PersonaConsistencyProfile):
    pass


class HeartStateTransitionsResponse(ApiModel):
    items: list[HeartStateTransition] = Field(default_factory=list)


class TonePolicyResolutionResponse(TonePolicyResolution):
    pass


class ResponseQualityEvaluationResponse(ResponseQualityEvaluation):
    pass


class PersonaHeartReplayRunCreateRequest(ApiModel):
    member_id: EntityId = "mem_xiaoyao"
    case_key: str = "manual_preview"
    turns: list[dict[str, Any]] = Field(default_factory=list)
    scenario: str = "longitudinal_replay"


class PersonaHeartReplayRunResponse(PersonaHeartReplayRun):
    pass


class VectorSyncJobCreateRequest(ApiModel):
    job_type: str = Field(default="sync", pattern="^(sync|reindex)$")
    target_type: str = Field(pattern="^(memory|knowledge)$")
    target_id: EntityId | None = None
    collection_name: str | None = None
    source_provider: str | None = None
    target_provider: str | None = None
    strategy: str = Field(
        default="dual_write",
        pattern="^(dual_write|shadow_index|validate_before_switch)$",
    )
    dry_run: bool = False
    privacy_level: str = "medium"
    payload: dict[str, Any] = Field(default_factory=dict)


class VectorSyncJobResponse(ApiModel):
    job_id: EntityId
    organization_id: EntityId = "org_default"
    target_type: str
    target_id: EntityId | None = None
    collection_id: EntityId | None = None
    provider: str
    status: str
    degraded_reason: str | None = None
    item_count: int = 0
    vector_ref_ids: list[EntityId] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class VectorStatusResponse(ApiModel):
    provider: str
    status: str
    available: bool
    embedding_model: str
    embedding_dim: int
    privacy_policy: str = "local_only"
    allow_cloud: bool = False
    secret_ref_present: bool = False
    collections: list[dict[str, Any]] = Field(default_factory=list)
    degraded_reason: str | None = None
    fallback_policy: str = "fts"
    chroma_available: bool = False
    local_embedding_count: int = 0
    active_provider_id: EntityId | None = None
    fallback_chain: list[str] = Field(default_factory=list)
    health_status: str | None = None
    privacy_block_reason: str | None = None


class VectorProviderConfigResponse(ApiModel):
    provider_id: EntityId
    provider_type: str
    provider_name: str
    embedding_model: str
    embedding_dim: int
    status: str
    privacy_policy: str = "local_only"
    allow_cloud: bool = False
    secret_ref_present: bool = False
    fallback_policy: str = "fts"
    degraded_reason: str | None = None
    health_status: str = "unknown"
    last_checked_at: str | None = None
    embedding_cost_policy: dict[str, Any] = Field(default_factory=dict)
    max_text_tokens: int | None = None
    privacy_block_reason: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None


class VectorProviderListResponse(ApiModel):
    items: list[VectorProviderConfigResponse] = Field(default_factory=list)


class VectorProviderUpdateRequest(ApiModel):
    provider_name: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, pattern="^(active|disabled|degraded)$")
    privacy_policy: str | None = None
    allow_cloud: bool | None = None
    secret_ref: str | None = None
    fallback_policy: str | None = None
    degraded_reason: str | None = None
    config: dict[str, Any] | None = None


class RiskPreview(ApiModel):
    risk_level: RiskLevel
    approval_required: bool
    decision: str


class ResponseComposerPreviewRequest(ApiModel):
    user_text: str = ""
    result_summary: str
    style: str = "result_first"
    scenario: str = "direct"
    persona: dict[str, Any] = Field(default_factory=dict)
    heart: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel | None = None
    route_profile: str | None = None
    notices: dict[str, Any] = Field(default_factory=dict)
    trace_refs: list[dict[str, Any]] = Field(default_factory=list)


class ResponseComposerPreviewResponse(ApiModel):
    text: str
    response_plan: ResponsePlan
    metadata: dict[str, Any] = Field(default_factory=dict)
