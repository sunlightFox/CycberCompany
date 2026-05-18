from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import RiskLevel


class PermissionPreview(ApiModel):
    bundle_id: EntityId | None = None
    summary: str
    required_tools: list[dict[str, Any]] = Field(default_factory=list)
    required_assets: list[dict[str, Any]] = Field(default_factory=list)
    network: dict[str, Any] = Field(default_factory=dict)
    filesystem: dict[str, Any] = Field(default_factory=dict)
    high_risk_actions: list[dict[str, Any]] = Field(default_factory=list)
    blocked_actions: list[str] = Field(default_factory=list)
    trust: dict[str, Any] = Field(default_factory=dict)
    preview_hash: str


class PluginBundle(ApiModel):
    bundle_id: EntityId
    organization_id: EntityId
    extension_id: EntityId | None = None
    display_name: str
    description: str | None = None
    author: str | None = None
    bundle_revision: str
    package_kind: str = "plugin_bundle"
    source_type: str
    source_format: str = "cycber_bundle_v1"
    source_uri: str | None = None
    package_uri: str | None = None
    manifest_hash: str
    canonical_version: str = "canonical.skill.v1"
    compatibility_status: str = "compatible"
    compatibility_notes: list[str] = Field(default_factory=list)
    signature_status: str
    trust_level: str
    status: str
    binding_status: str = "not_required"
    binding_summary: dict[str, Any] = Field(default_factory=dict)
    permission_summary: dict[str, Any] = Field(default_factory=dict)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
    canonical_snapshot: dict[str, Any] = Field(default_factory=dict)
    installed_by_member_id: EntityId | None = None
    installed_at: datetime | None = None
    enabled_at: datetime | None = None
    disabled_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PluginEvent(ApiModel):
    event_id: EntityId
    organization_id: EntityId
    bundle_id: EntityId | None = None
    skill_id: EntityId | None = None
    server_id: EntityId | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime


class SkillRecord(ApiModel):
    skill_id: EntityId
    organization_id: EntityId
    bundle_id: EntityId
    extension_id: EntityId | None = None
    name: str
    display_name: str
    description: str | None = None
    entrypoint_path: str
    instructions: str
    runtime_kind: str = "workflow_bound"
    source_format: str = "cycber_bundle_v1"
    canonical_version: str = "canonical.skill.v1"
    compatibility_status: str = "compatible"
    compatibility_notes: list[str] = Field(default_factory=list)
    binding_status: str = "not_required"
    binding_summary: dict[str, Any] = Field(default_factory=dict)
    instruction_spec: dict[str, Any] = Field(default_factory=dict)
    execution_binding: dict[str, Any] = Field(default_factory=dict)
    trigger: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_tools: list[str] = Field(default_factory=list)
    required_assets: list[dict[str, Any]] = Field(default_factory=list)
    permission: dict[str, Any] = Field(default_factory=dict)
    risk_policy: dict[str, Any] = Field(default_factory=dict)
    eval_summary: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillMatch(ApiModel):
    skill_id: EntityId
    bundle_id: EntityId
    display_name: str
    confidence: float
    reason: str
    required_tools: list[str] = Field(default_factory=list)
    required_assets: list[dict[str, Any]] = Field(default_factory=list)


class SkillRunRecord(ApiModel):
    skill_run_id: EntityId
    organization_id: EntityId
    skill_id: EntityId
    bundle_id: EntityId
    task_id: EntityId | None = None
    step_id: EntityId | None = None
    owner_member_id: EntityId
    status: str
    input_redacted: dict[str, Any] = Field(default_factory=dict)
    output_redacted: dict[str, Any] = Field(default_factory=dict)
    matched_reason: str | None = None
    confidence: float | None = None
    capability_decision_id: EntityId | None = None
    safety_decision_id: EntityId | None = None
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    resolved_asset_refs: list[dict[str, Any]] = Field(default_factory=list)
    approval_id: EntityId | None = None
    artifact_ids: list[EntityId] = Field(default_factory=list)
    trace_id: EntityId | None = None
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class SkillCandidateRecord(ApiModel):
    candidate_id: EntityId
    organization_id: EntityId
    source_type: str
    source_id: EntityId
    title: str
    description: str | None = None
    draft_manifest: dict[str, Any] = Field(default_factory=dict)
    draft_skill_md: str
    proposed_permissions: dict[str, Any] = Field(default_factory=dict)
    proposed_eval_cases: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    reviewed_by_member_id: EntityId | None = None
    review_reason: str | None = None
    promoted_bundle_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillLifecycleRecord(ApiModel):
    skill_id: EntityId
    organization_id: EntityId
    bundle_id: EntityId
    created_by: str = "system"
    provenance: str = "unknown"
    use_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    last_used_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    pinned: bool = False
    state: str = "active"
    archived_at: datetime | None = None
    archive_reason: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillCuratorPreviewItem(ApiModel):
    skill_id: EntityId
    bundle_id: EntityId
    state: str = "active"
    proposed_action: str
    last_used_at: datetime | None = None
    pinned: bool = False
    stale_cutoff_at: datetime | None = None
    archive_cutoff_at: datetime | None = None
    reason_summary: dict[str, Any] = Field(default_factory=dict)


class SkillCuratorRunResult(ApiModel):
    checked_count: int = 0
    marked_stale_count: int = 0
    archived_count: int = 0
    skipped_pinned_count: int = 0
    items: list[SkillLifecycleRecord] = Field(default_factory=list)
    preview_items: list[SkillCuratorPreviewItem] = Field(default_factory=list)


class SkillEvalRun(ApiModel):
    eval_run_id: EntityId
    organization_id: EntityId
    skill_id: EntityId | None = None
    bundle_id: EntityId | None = None
    status: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    security_failures: int
    result: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class SkillRepositoryRecord(ApiModel):
    repository_id: EntityId
    organization_id: EntityId
    display_name: str
    provider: str
    index_uri: str | None = None
    base_uri: str | None = None
    auth: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100
    is_default: bool = False
    trust_level: str = "restricted"
    status: str
    config: dict[str, Any] = Field(default_factory=dict)
    last_refresh_at: datetime | None = None
    last_error_code: str | None = None
    last_error_summary: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillRepositoryEntry(ApiModel):
    entry_id: EntityId
    organization_id: EntityId
    repository_id: EntityId
    package_ref: str
    bundle_id: EntityId
    display_name: str
    description: str | None = None
    version: str | None = None
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    checksum: str | None = None
    trust_level: str = "restricted"
    status: str
    health_status: str = "unknown"
    quality_score: float = 0.5
    install_count: int = 0
    compatibility: dict[str, Any] = Field(default_factory=dict)
    dependency_summary: dict[str, Any] = Field(default_factory=dict)
    latest_eval_status: str | None = None
    last_health_check_at: datetime | None = None
    health_reason: str | None = None
    package_metadata: dict[str, Any] = Field(default_factory=dict)
    indexed_at: datetime
    updated_at: datetime


class SkillRepositorySyncRun(ApiModel):
    sync_run_id: EntityId
    organization_id: EntityId
    repository_id: EntityId
    status: str
    indexed_count: int = 0
    error_code: str | None = None
    error_summary: str | None = None
    trace_id: EntityId | None = None
    started_at: datetime
    completed_at: datetime | None = None
    created_at: datetime


class SkillMarketplaceHealthRecord(ApiModel):
    health_record_id: EntityId
    organization_id: EntityId
    repository_id: EntityId
    package_ref: str | None = None
    bundle_id: EntityId | None = None
    health_status: str
    provider_status: str = "unknown"
    quality_score: float = 0.5
    reason_codes: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    checked_at: datetime
    created_at: datetime


class SkillMarketplaceInstallRecord(ApiModel):
    install_record_id: EntityId
    organization_id: EntityId
    repository_id: EntityId | None = None
    package_ref: str | None = None
    bundle_id: EntityId | None = None
    installed_bundle_id: EntityId | None = None
    skill_id: EntityId | None = None
    version: str | None = None
    status: str
    gate_status: str
    eval_status: str | None = None
    blocked_reason: str | None = None
    source_uri_hash: str | None = None
    requested_by_member_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime


class SkillDependencyEdge(ApiModel):
    edge_id: EntityId
    organization_id: EntityId
    source_type: str
    source_id: EntityId
    target_type: str
    target_id: EntityId
    dependency_kind: str
    required_action: str | None = None
    risk_level: str = "R1"
    status: str
    fail_closed_reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class SkillGrowthCandidate(ApiModel):
    evidence_id: EntityId
    organization_id: EntityId
    candidate_id: EntityId | None = None
    source_type: str
    source_id: EntityId
    experience_id: EntityId | None = None
    task_id: EntityId | None = None
    memory_id: EntityId | None = None
    outcome: str | None = None
    reuse_score: float = 0
    decision: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime


class SkillMarketplacePackageDetail(ApiModel):
    entry: SkillRepositoryEntry
    versions: list[dict[str, Any]] = Field(default_factory=list)
    latest_health: SkillMarketplaceHealthRecord | None = None
    install_records: list[SkillMarketplaceInstallRecord] = Field(default_factory=list)
    dependency_edges: list[SkillDependencyEdge] = Field(default_factory=list)


class CanonicalToolRequirement(ApiModel):
    tool_name: str
    required: bool = True
    source: str | None = None


class CanonicalMcpRequirement(ApiModel):
    server_id: str | None = None
    tool_name: str | None = None
    capability: str | None = None
    required: bool = True
    permission: dict[str, Any] = Field(default_factory=dict)


class CanonicalAssetRequirement(ApiModel):
    asset_type: str
    optional: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalPermissionEnvelope(ApiModel):
    tools: list[dict[str, Any]] = Field(default_factory=list)
    mcp: list[dict[str, Any]] = Field(default_factory=list)
    assets: list[dict[str, Any]] = Field(default_factory=list)
    network: dict[str, Any] = Field(default_factory=dict)
    filesystem: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)


class CanonicalSkillInstruction(ApiModel):
    markdown: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    trigger: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class CanonicalExecutionBinding(ApiModel):
    runtime_kind: str = "instruction_only"
    status: str = "unbound"
    builtin_tools: list[str] = Field(default_factory=list)
    mcp_tools: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)


class CanonicalRuntimeContribution(ApiModel):
    contribution_id: EntityId
    contribution_type: str
    status: str = "registered_disabled"
    runtime_kind: str = "manifest"
    name: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CanonicalSkill(ApiModel):
    skill_id: EntityId
    name: str
    display_name: str
    description: str | None = None
    entrypoint_path: str = "SKILL.md"
    runtime_kind: str = "instruction_only"
    instruction_spec: CanonicalSkillInstruction
    execution_binding: CanonicalExecutionBinding = Field(
        default_factory=CanonicalExecutionBinding
    )
    required_tools: list[CanonicalToolRequirement] = Field(default_factory=list)
    required_assets: list[CanonicalAssetRequirement] = Field(default_factory=list)
    permission_envelope: CanonicalPermissionEnvelope = Field(
        default_factory=CanonicalPermissionEnvelope
    )
    compatibility_status: str = "native"
    compatibility_notes: list[str] = Field(default_factory=list)


class CanonicalCompatibilityReport(ApiModel):
    extension_id: EntityId
    source_format: str
    canonical_version: str = "canonical.skill.v1"
    compatibility_status: str
    compatibility_notes: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    compatibility_tier: str = "manifest_compatible"
    smoke_check: dict[str, Any] = Field(default_factory=dict)
    package_compatibility: dict[str, Any] = Field(default_factory=dict)
    blocked_reasons: list[str] = Field(default_factory=list)


class CanonicalExtensionPackage(ApiModel):
    extension_id: EntityId
    bundle_id: EntityId
    display_name: str
    description: str | None = None
    package_kind: str
    source_type: str
    source_format: str
    source_uri: str | None = None
    manifest_format: str | None = None
    canonical_version: str = "canonical.skill.v1"
    compatibility_status: str = "compatible"
    compatibility_notes: list[str] = Field(default_factory=list)
    trust_level: str = "restricted"
    version: str | None = None
    permission_envelope: CanonicalPermissionEnvelope = Field(
        default_factory=CanonicalPermissionEnvelope
    )
    skills: list[CanonicalSkill] = Field(default_factory=list)
    mcp_requirements: list[CanonicalMcpRequirement] = Field(default_factory=list)
    runtime_compatibility: str = "manifest_compatible"
    config_requirements: list[dict[str, Any]] = Field(default_factory=list)
    secret_requirements: list[dict[str, Any]] = Field(default_factory=list)
    env_requirements: list[dict[str, Any]] = Field(default_factory=list)
    dependency_requirements: list[dict[str, Any]] = Field(default_factory=list)
    runtime_contributions: list[CanonicalRuntimeContribution] = Field(default_factory=list)
    setup_hints: list[dict[str, Any]] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)
    canonical_snapshot: dict[str, Any] = Field(default_factory=dict)


class MCPServerRecord(ApiModel):
    server_id: EntityId
    organization_id: EntityId
    display_name: str
    description: str | None = None
    transport: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_refs: list[str] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    permission: dict[str, Any] = Field(default_factory=dict)
    risk_policy: dict[str, Any] = Field(default_factory=dict)
    trust_level: str
    status: str
    runtime_profile_id: EntityId | None = None
    lifecycle_status: str = "created"
    circuit_state: str = "closed"
    last_health_check_at: datetime | None = None
    consecutive_failure_count: int = 0
    last_connected_at: datetime | None = None
    last_sync_at: datetime | None = None
    last_error_code: str | None = None
    last_error_summary: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MCPToolRecord(ApiModel):
    mcp_tool_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    tool_name: str
    registry_tool_name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk_policy: dict[str, Any] = Field(default_factory=dict)
    required_handle_types: list[str] = Field(default_factory=list)
    status: str
    synced_at: datetime
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MCPResourceRecord(ApiModel):
    resource_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    uri: str
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None
    trust_level: str
    sensitivity: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    synced_at: datetime
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MCPPromptRecord(ApiModel):
    prompt_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    name: str
    description: str | None = None
    arguments_schema: dict[str, Any] = Field(default_factory=dict)
    prompt_template_redacted: str | None = None
    trust_level: str
    status: str
    synced_at: datetime
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MCPCallRecord(ApiModel):
    mcp_call_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    mcp_tool_id: EntityId | None = None
    task_id: EntityId | None = None
    step_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    status: str
    request_redacted: dict[str, Any] = Field(default_factory=dict)
    response_redacted: dict[str, Any] = Field(default_factory=dict)
    capability_decision_id: EntityId | None = None
    safety_decision_id: EntityId | None = None
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    resolved_asset_refs: list[dict[str, Any]] = Field(default_factory=list)
    approval_id: EntityId | None = None
    trace_id: EntityId | None = None
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class MCPRuntimeProfile(ApiModel):
    profile_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    transport: str
    command_policy: dict[str, Any] = Field(default_factory=dict)
    args_policy: dict[str, Any] = Field(default_factory=dict)
    env_policy: dict[str, Any] = Field(default_factory=dict)
    member_scope_policy: dict[str, Any] = Field(default_factory=dict)
    network_policy: str
    filesystem_policy: dict[str, Any] = Field(default_factory=dict)
    sandbox_backend: str
    timeout_policy: dict[str, Any] = Field(default_factory=dict)
    resource_trust_policy: str
    prompt_trust_policy: str
    status: str
    reason_codes: list[str] = Field(default_factory=list)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MCPLifecycleEvent(ApiModel):
    lifecycle_event_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    profile_id: EntityId | None = None
    event_type: str
    previous_status: str | None = None
    current_status: str
    circuit_state: str = "closed"
    payload_redacted: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class MCPProtocolValidationReport(ApiModel):
    validation_report_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    mcp_call_id: EntityId | None = None
    operation: str
    protocol_version: str | None = None
    schema_valid: bool = False
    capability_valid: bool = False
    validation_status: str
    issue_codes: list[str] = Field(default_factory=list)
    sanitized_payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class MCPContentSanitizationReport(ApiModel):
    sanitization_report_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    source_type: str
    source_id: EntityId | None = None
    trust_level: str
    content_hash: str | None = None
    size_bytes: int = 0
    mime_type: str | None = None
    injection_detected: bool = False
    dlp_report_id: EntityId | None = None
    sanitized_preview: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class MCPOutputTaintRecord(ApiModel):
    taint_record_id: EntityId
    organization_id: EntityId
    server_id: EntityId
    mcp_call_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    taint_source: str
    target_action: str | None = None
    target_risk_level: RiskLevel = RiskLevel.R1
    guard_decision: str
    reason_codes: list[str] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class RiskPreview(ApiModel):
    risk_level: RiskLevel = RiskLevel.R1
    approval_required: bool = False
    reason: str
