from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    PermissionPreview,
    SkillBundleSource,
    SkillBundleVersion,
    SkillEvalBinding,
    SkillGrantRecord,
    SkillOutputTaintRecord,
    SkillPermissionPreviewRecord,
    SkillRollbackPoint,
    SkillStaticAnalysisReport,
)
from pydantic import Field

from app.schemas.skills import BundleInstallRequest


class SkillInstallPreviewResponse(ApiModel):
    preview: PermissionPreview
    governance_preview: SkillPermissionPreviewRecord
    static_analysis: SkillStaticAnalysisReport
    source: SkillBundleSource | None = None
    version: SkillBundleVersion | None = None
    blocked: bool = False


class SkillGrantCreateRequest(ApiModel):
    subject_type: str = "member"
    subject_id: EntityId = "mem_xiaoyao"
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_asset_actions: list[str] = Field(default_factory=list)
    allowed_mcp_tools: list[str] = Field(default_factory=list)
    denied_actions: list[str] = Field(default_factory=list)
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    grant_scope: str = "explicit"
    created_by_member_id: EntityId = "mem_xiaoyao"
    expires_at: str | None = None


class SkillGrantListResponse(ApiModel):
    items: list[SkillGrantRecord] = Field(default_factory=list)


class SkillRevokeRequest(ApiModel):
    actor_member_id: EntityId = "mem_xiaoyao"
    reason: str | None = None


class SkillUpgradeRequest(ApiModel):
    actor_member_id: EntityId = "mem_xiaoyao"
    bundle_revision: str | None = None
    display_name: str | None = None
    description: str | None = None
    required_tools: list[str] | None = None
    steps: list[dict[str, Any]] | None = None
    manifest_patch: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class SkillRollbackRequest(ApiModel):
    actor_member_id: EntityId = "mem_xiaoyao"
    rollback_point_id: EntityId | None = None
    reason: str | None = None


class SkillAnalysisResponse(ApiModel):
    items: list[SkillStaticAnalysisReport] = Field(default_factory=list)


class SkillEvalBindingsResponse(ApiModel):
    items: list[SkillEvalBinding] = Field(default_factory=list)


class SkillOutputTaintResponse(ApiModel):
    items: list[SkillOutputTaintRecord] = Field(default_factory=list)


class SkillUpgradeResponse(ApiModel):
    rollback_point: SkillRollbackPoint
    skill: dict[str, Any]
    bundle: dict[str, Any]


class SkillRollbackResponse(ApiModel):
    rollback_point: SkillRollbackPoint
    skill: dict[str, Any]
    bundle: dict[str, Any]


class SkillInstallPreviewRequest(BundleInstallRequest):
    pass
