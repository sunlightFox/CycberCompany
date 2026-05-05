from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    PermissionPreview,
    PluginBundle,
    PluginEvent,
    SkillCandidateRecord,
    SkillDependencyEdge,
    SkillEvalRun,
    SkillGrowthCandidate,
    SkillMatch,
    SkillMarketplaceHealthRecord,
    SkillMarketplaceInstallRecord,
    SkillMarketplacePackageDetail,
    SkillRecord,
    SkillRepositoryEntry,
    SkillRepositoryRecord,
    SkillRepositorySyncRun,
)
from pydantic import Field


class BundleInstallRequest(ApiModel):
    source_type: str = "local_directory"
    source_uri: str = Field(min_length=1)
    requested_by_member_id: EntityId = "mem_xiaoyao"
    install_options: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    repository_id: EntityId | None = None
    package_ref: str | None = None


class BundleInstallResponse(ApiModel):
    bundle: PluginBundle
    skills: list[SkillRecord] = Field(default_factory=list)
    permission_preview: PermissionPreview
    status: str


class SkillListResponse(ApiModel):
    items: list[SkillRecord] = Field(default_factory=list)


class SkillMatchRequest(ApiModel):
    owner_member_id: EntityId = "mem_xiaoyao"
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None
    intent: str | None = None
    goal: str = Field(min_length=1)
    required_outputs: list[str] = Field(default_factory=list)
    resource_handle_ids: list[EntityId] = Field(default_factory=list)


class SkillMatchResponse(ApiModel):
    items: list[SkillMatch] = Field(default_factory=list)


class SkillCandidateListResponse(ApiModel):
    items: list[SkillCandidateRecord] = Field(default_factory=list)


class SkillCandidatePromoteResponse(ApiModel):
    bundle: PluginBundle
    skills: list[SkillRecord] = Field(default_factory=list)
    status: str


class SkillCandidateDecisionRequest(ApiModel):
    reviewed_by_member_id: EntityId = "mem_xiaoyao"
    reason: str | None = None


class SkillEvalResponse(SkillEvalRun):
    pass


class PluginListResponse(ApiModel):
    items: list[PluginBundle] = Field(default_factory=list)


class PluginEventsResponse(ApiModel):
    items: list[PluginEvent] = Field(default_factory=list)


class PluginActionRequest(ApiModel):
    actor_member_id: EntityId = "mem_xiaoyao"
    reason: str | None = None


class PermissionPreviewResponse(PermissionPreview):
    pass


class SkillRepositoryUpsertRequest(ApiModel):
    display_name: str
    provider: str = "index_json"
    index_uri: str | None = None
    base_uri: str | None = None
    auth: dict[str, Any] = Field(default_factory=dict)
    priority: int = 100
    is_default: bool = False
    trust_level: str = "restricted"
    status: str = "enabled"
    config: dict[str, Any] = Field(default_factory=dict)


class SkillRepositoryPatchRequest(ApiModel):
    display_name: str | None = None
    provider: str | None = None
    index_uri: str | None = None
    base_uri: str | None = None
    auth: dict[str, Any] | None = None
    priority: int | None = None
    is_default: bool | None = None
    trust_level: str | None = None
    status: str | None = None
    config: dict[str, Any] | None = None


class SkillRepositoryListResponse(ApiModel):
    items: list[SkillRepositoryRecord] = Field(default_factory=list)


class SkillRepositoryRefreshResponse(ApiModel):
    repository: SkillRepositoryRecord
    sync_run: SkillRepositorySyncRun
    indexed_count: int


class SkillCatalogSearchResponse(ApiModel):
    items: list[SkillRepositoryEntry] = Field(default_factory=list)


class SkillMarketplacePackageResponse(ApiModel):
    package: SkillMarketplacePackageDetail


class SkillMarketplaceHealthRefreshResponse(ApiModel):
    items: list[SkillMarketplaceHealthRecord] = Field(default_factory=list)


class SkillMarketplaceInstallRecordsResponse(ApiModel):
    items: list[SkillMarketplaceInstallRecord] = Field(default_factory=list)


class SkillDependencyEdgesResponse(ApiModel):
    items: list[SkillDependencyEdge] = Field(default_factory=list)


class SkillGrowthCandidateConsolidateRequest(ApiModel):
    member_id: EntityId | None = "mem_xiaoyao"
    task_id: EntityId | None = None
    experience_id: EntityId | None = None
    limit: int = Field(default=20, ge=1, le=100)


class SkillGrowthCandidateResponse(ApiModel):
    items: list[SkillGrowthCandidate] = Field(default_factory=list)
