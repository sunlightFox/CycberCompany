from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    CanonicalCompatibilityReport,
    EntityId,
    PermissionPreview,
    PluginBundle,
    SkillRecord,
)
from pydantic import Field


class ExtensionImportRequest(ApiModel):
    source_type: str = "local_directory"
    source_uri: str = Field(min_length=1)
    requested_by_member_id: EntityId = "mem_xiaoyao"
    install_options: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    repository_id: EntityId | None = None
    package_ref: str | None = None


class ExtensionPreviewResponse(ApiModel):
    extension_id: EntityId
    package_kind: str
    source_format: str
    canonical_version: str
    compatibility_status: str
    compatibility_notes: list[str] = Field(default_factory=list)
    permission_preview: PermissionPreview
    bundle_preview: PluginBundle
    skills_preview: list[SkillRecord] = Field(default_factory=list)


class ExtensionInstallResponse(ApiModel):
    bundle: PluginBundle
    skills: list[SkillRecord] = Field(default_factory=list)
    permission_preview: PermissionPreview
    compatibility: CanonicalCompatibilityReport | None = None
    status: str


class ExtensionListResponse(ApiModel):
    items: list[PluginBundle] = Field(default_factory=list)


class ExtensionCompatibilityResponse(ApiModel):
    items: list[CanonicalCompatibilityReport] = Field(default_factory=list)


class ExtensionBindingSnapshot(ApiModel):
    snapshot_id: EntityId
    extension_id: EntityId
    bundle_id: EntityId | None = None
    skill_id: EntityId | None = None
    binding_status: str
    binding_summary: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ExtensionBindingResponse(ApiModel):
    bundle: PluginBundle
    skills: list[SkillRecord] = Field(default_factory=list)
    snapshots: list[ExtensionBindingSnapshot] = Field(default_factory=list)


class ExtensionDiagnosticResponse(ApiModel):
    extension_id: EntityId
    bundle_id: EntityId | None = None
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    compatibility: dict[str, Any] = Field(default_factory=dict)
    binding: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, Any] = Field(default_factory=dict)
    contributions: list[dict[str, Any]] = Field(default_factory=list)
    health: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    runtime_snapshot: dict[str, Any] = Field(default_factory=dict)


class ExtensionActionRequest(ApiModel):
    actor_member_id: EntityId = "mem_xiaoyao"
    reason: str | None = None


class ExtensionPlanRunRequest(ApiModel):
    owner_member_id: EntityId = "mem_xiaoyao"
    goal: str = Field(min_length=1)
    intent: str | None = None


class ExtensionPlanRunResponse(ApiModel):
    extension_id: EntityId
    bundle: PluginBundle
    matches: list[dict[str, Any]] = Field(default_factory=list)
    runnable: bool = False
    runnable_state: str = "blocked"
    blocked_by: list[str] = Field(default_factory=list)
    missing_bindings: list[str] = Field(default_factory=list)
    required_approvals: list[dict[str, Any]] = Field(default_factory=list)
    selected_capabilities: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    runtime_snapshot: dict[str, Any] = Field(default_factory=dict)


class ExtensionTaskLaunchRequest(ApiModel):
    conversation_id: EntityId | None = None
    owner_member_id: EntityId = "mem_xiaoyao"
    goal: str = Field(min_length=1)
    intent: str | None = None
    skill_input: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = None
    auto_start: bool = True
