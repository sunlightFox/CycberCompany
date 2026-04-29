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
    display_name: str
    description: str | None = None
    author: str | None = None
    bundle_revision: str
    source_type: str
    source_uri: str | None = None
    package_uri: str | None = None
    manifest_hash: str
    signature_status: str
    trust_level: str
    status: str
    permission_summary: dict[str, Any] = Field(default_factory=dict)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    manifest: dict[str, Any] = Field(default_factory=dict)
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
    name: str
    display_name: str
    description: str | None = None
    entrypoint_path: str
    instructions: str
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
