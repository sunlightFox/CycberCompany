from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class SkillBundleSource(ApiModel):
    source_id: EntityId
    organization_id: EntityId
    bundle_id: EntityId
    source_type: str
    source_uri_redacted: str | None = None
    source_uri_hash: str | None = None
    signature_status: str = "unsigned"
    checksum: str | None = None
    trust_level: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillBundleVersion(ApiModel):
    version_id: EntityId
    organization_id: EntityId
    bundle_id: EntityId
    bundle_revision: str
    manifest_hash: str
    signature_status: str = "unsigned"
    trust_level: str
    permission_summary: dict[str, Any] = Field(default_factory=dict)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    manifest_redacted: dict[str, Any] = Field(default_factory=dict)
    status: str
    installed_by_member_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillPermissionPreviewRecord(ApiModel):
    preview_id: EntityId
    organization_id: EntityId
    bundle_id: EntityId | None = None
    bundle_revision: str | None = None
    manifest_hash: str
    trust_level: str
    risk_level: str
    permission_summary: dict[str, Any] = Field(default_factory=dict)
    blocked_reasons: list[str] = Field(default_factory=list)
    requires_user_grant: bool = True
    unattended_allowed: bool = False
    preview_hash: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class SkillGrantRecord(ApiModel):
    skill_grant_id: EntityId
    organization_id: EntityId
    skill_id: EntityId
    bundle_id: EntityId
    subject_type: str
    subject_id: EntityId
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_asset_actions: list[str] = Field(default_factory=list)
    allowed_mcp_tools: list[str] = Field(default_factory=list)
    denied_actions: list[str] = Field(default_factory=list)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    status: str
    grant_scope: str = "explicit"
    created_by_member_id: EntityId | None = None
    revoked_by_member_id: EntityId | None = None
    revoke_reason: str | None = None
    expires_at: datetime | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    revoked_at: datetime | None = None


class SkillStaticAnalysisReport(ApiModel):
    analysis_report_id: EntityId
    organization_id: EntityId
    bundle_id: EntityId | None = None
    bundle_revision: str | None = None
    manifest_hash: str
    status: str
    risk_level: str
    trust_level: str
    reason_codes: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    remediation_hints: list[str] = Field(default_factory=list)
    sensitive_findings: list[dict[str, Any]] = Field(default_factory=list)
    manifest_summary: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class SkillEvalBinding(ApiModel):
    binding_id: EntityId
    organization_id: EntityId
    skill_id: EntityId
    bundle_id: EntityId
    bundle_revision: str
    manifest_hash: str
    eval_run_id: EntityId
    capability_scope: dict[str, Any] = Field(default_factory=dict)
    risk_level: str
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class SkillRollbackPoint(ApiModel):
    rollback_point_id: EntityId
    organization_id: EntityId
    skill_id: EntityId
    bundle_id: EntityId
    from_revision: str
    manifest_hash: str
    skill_snapshot: dict[str, Any] = Field(default_factory=dict)
    bundle_snapshot: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    created_by_member_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class SkillOutputTaintRecord(ApiModel):
    taint_record_id: EntityId
    organization_id: EntityId
    skill_id: EntityId
    bundle_id: EntityId
    skill_run_id: EntityId | None = None
    task_id: EntityId | None = None
    taint_source: str
    output_hash: str
    output_preview: str | None = None
    untrusted_external_content: bool = True
    dlp_findings: list[dict[str, Any]] = Field(default_factory=list)
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    guard_decision: str
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
