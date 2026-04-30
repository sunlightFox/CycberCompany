from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    AssetCategory,
    AssetHandleDetail,
    AssetHandleEvent,
    AssetQueryResponse,
    AssetSummary,
    EntityId,
    ResolvedAsset,
    RiskLevel,
)
from pydantic import Field


class AssetCreateRequest(ApiModel):
    asset_type: AssetCategory
    display_name: str = Field(min_length=1)
    provider: str | None = None
    sensitivity: str = "low"
    config: dict[str, Any] = Field(default_factory=dict)
    secret_value: str | None = None
    owner_scope_type: str = "member"
    owner_scope_id: EntityId | None = "mem_xiaoyao"
    visibility: str = "private"
    risk_level: RiskLevel = RiskLevel.R1
    summary_text: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = None


class AssetUpdateRequest(ApiModel):
    display_name: str | None = Field(default=None, min_length=1)
    provider: str | None = None
    sensitivity: str | None = None
    config: dict[str, Any] | None = None
    secret_value: str | None = None
    owner_scope_type: str | None = None
    owner_scope_id: EntityId | None = None
    visibility: str | None = None
    risk_level: RiskLevel | None = None
    summary_text: str | None = None
    capabilities: list[str] | None = None
    policy: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    expires_at: str | None = None


class AssetListResponse(ApiModel):
    items: list[AssetSummary] = Field(default_factory=list)


class AssetVerifyResponse(ApiModel):
    asset_id: EntityId
    status: str
    message: str
    checked_actions: list[str] = Field(default_factory=list)


class AssetQueryRequest(ApiModel):
    subject_type: str = "member"
    subject_id: EntityId = "mem_xiaoyao"
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None
    asset_type: AssetCategory | None = None
    intent: str | None = None
    requested_actions: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class AssetHandleValidateRequest(ApiModel):
    subject_type: str = "member"
    subject_id: EntityId = "mem_xiaoyao"
    action: str
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None
    approval_id: EntityId | None = None


class AssetHandleValidateResponse(ApiModel):
    handle: AssetHandleDetail
    allowed: bool
    action: str


class AssetResolveForToolRequest(ApiModel):
    subject_id: EntityId = "mem_xiaoyao"
    action: str
    tool_name: str
    task_id: EntityId | None = None
    conversation_id: EntityId | None = None
    approval_id: EntityId | None = None


class AssetResolveForToolResponse(ResolvedAsset):
    pass


class AssetHandleEventListResponse(ApiModel):
    items: list[AssetHandleEvent] = Field(default_factory=list)


class CapabilityGrantCreateRequest(ApiModel):
    subject_type: str = "member"
    subject_id: EntityId
    object_type: str = "asset"
    object_id: EntityId
    action: str
    effect: str = "allow"
    risk_level: RiskLevel = RiskLevel.R1
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    condition: dict[str, Any] = Field(default_factory=dict)
    source_type: str = "member_grant"
    source_id: EntityId | None = None
    priority: int = 0
    valid_from: str | None = None
    valid_to: str | None = None


class CapabilityGrantUpdateRequest(ApiModel):
    effect: str | None = None
    risk_level: RiskLevel | None = None
    approval_policy: dict[str, Any] | None = None
    condition: dict[str, Any] | None = None
    priority: int | None = None
    status: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


class AssetQueryApiResponse(AssetQueryResponse):
    handles: list[AssetHandleDetail] = Field(default_factory=list)


class AssetDeleteResponse(ApiModel):
    asset_id: EntityId
    status: str
