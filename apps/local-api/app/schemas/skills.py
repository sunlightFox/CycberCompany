from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    PermissionPreview,
    PluginBundle,
    PluginEvent,
    SkillCandidateRecord,
    SkillEvalRun,
    SkillMatch,
    SkillRecord,
)
from pydantic import Field


class BundleInstallRequest(ApiModel):
    source_type: str = "local_directory"
    source_uri: str = Field(min_length=1)
    requested_by_member_id: EntityId = "mem_xiaoyao"
    install_options: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


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
