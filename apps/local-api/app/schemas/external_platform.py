from __future__ import annotations

from typing import Any

from core_types import (
    AccountAssetCandidate,
    ApiModel,
    EntityId,
    ExternalPlatformActionIntent,
    ExternalPlatformActionPlan,
    ExternalPlatformExecution,
    ExternalPlatformPlanEvent,
    ExternalPlatformTarget,
)
from pydantic import Field


class ExternalPlatformTargetCreateRequest(ApiModel):
    platform_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    supported_actions: list[str] = Field(default_factory=list)
    required_asset_types: list[str] = Field(default_factory=lambda: ["account"])
    execution_modes: list[str] = Field(default_factory=lambda: ["fake_provider"])
    risk_defaults: dict[str, str] = Field(default_factory=dict)
    status: str = "active"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalPlatformTargetListResponse(ApiModel):
    items: list[ExternalPlatformTarget] = Field(default_factory=list)


class ExternalPlatformProviderInfo(ApiModel):
    provider_key: str
    display_name: str
    execution_modes: list[str] = Field(default_factory=list)
    status: str
    real_external_platform_integration: bool = False
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalPlatformProviderListResponse(ApiModel):
    items: list[ExternalPlatformProviderInfo] = Field(default_factory=list)


class ExternalPlatformIntentResolveRequest(ApiModel):
    text: str = Field(min_length=1)
    member_id: EntityId = "mem_xiaoyao"
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    organization_id: EntityId = "org_default"
    constraints: dict[str, Any] = Field(default_factory=dict)


class ExternalPlatformIntentResolveResponse(ApiModel):
    intent: ExternalPlatformActionIntent
    message: str
    next_step: str


class ExternalPlatformAccountCandidatesRequest(ApiModel):
    intent_id: EntityId | None = None
    platform_key: str | None = None
    action_type: str | None = None
    member_id: EntityId = "mem_xiaoyao"
    conversation_id: EntityId | None = None
    organization_id: EntityId = "org_default"
    keywords: list[str] = Field(default_factory=list)


class ExternalPlatformAccountCandidatesResponse(ApiModel):
    intent_id: EntityId | None = None
    platform_key: str | None = None
    action_type: str | None = None
    candidates: list[AccountAssetCandidate] = Field(default_factory=list)
    status: str
    message: str
    recovery_options: list[str] = Field(default_factory=list)


class ExternalPlatformActionPlanCreateRequest(ApiModel):
    intent_id: EntityId
    selected_asset_id: EntityId | None = None
    selected_handle_id: EntityId | None = None
    execution_mode: str = "fake_provider"
    member_id: EntityId | None = None
    conversation_id: EntityId | None = None
    publish_text: str | None = None
    comment_text: str | None = None
    target_post_hint: str | None = None
    target_post_selector: str | None = None
    target_post_url: str | None = None
    published_post_ref: str | None = None
    provider_mode: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalPlatformPlanClarifyRequest(ApiModel):
    selected_asset_id: EntityId | None = None
    selected_handle_id: EntityId | None = None
    selected_display_name: str | None = None
    text: str | None = None


class ExternalPlatformPlanExecuteRequest(ApiModel):
    force: bool = False
    executor: str | None = None


class ExternalPlatformActionPlanResponse(ApiModel):
    plan: ExternalPlatformActionPlan
    intent: ExternalPlatformActionIntent | None = None
    target: ExternalPlatformTarget | None = None
    approval: dict[str, Any] | None = None
    candidates: list[AccountAssetCandidate] = Field(default_factory=list)
    executions: list[ExternalPlatformExecution] = Field(default_factory=list)
    events: list[ExternalPlatformPlanEvent] = Field(default_factory=list)
    message: str
    next_step: str | None = None
