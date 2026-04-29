from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import AssetCategory, RiskLevel


class AssetSummary(ApiModel):
    asset_id: EntityId
    organization_id: EntityId
    asset_type: AssetCategory
    display_name: str
    provider: str | None = None
    status: str
    sensitivity: str
    visibility: str = "private"
    risk_level: RiskLevel = RiskLevel.R1
    summary_text: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    has_secret: bool = False
    expires_at: datetime | None = None
    last_verified_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssetDetail(AssetSummary):
    owner_scope_type: str = "member"
    owner_scope_id: EntityId | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    secret_ref: EntityId | None = None


class AssetPolicy(ApiModel):
    policy_id: EntityId
    organization_id: EntityId
    asset_id: EntityId
    policy_type: str
    action: str
    effect: str
    risk_level: RiskLevel
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    condition: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssetHandle(ApiModel):
    handle_id: EntityId
    asset_id: EntityId
    asset_type: AssetCategory
    summary: str
    allowed_actions: list[str] = Field(default_factory=list)
    blocked_actions: list[str] = Field(default_factory=list)
    approval_required_actions: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.R1
    expires_at: datetime | None = None


class AssetHandleDetail(AssetHandle):
    organization_id: EntityId
    subject_type: str
    subject_id: EntityId
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None
    status: str
    issued_at: datetime
    revoked_at: datetime | None = None
    trace_id: EntityId | None = None
    policy_sources: list[str] = Field(default_factory=list)


class AssetHandleEvent(ApiModel):
    event_id: EntityId
    organization_id: EntityId
    handle_id: EntityId
    event_type: str
    reason: str | None = None
    actor_type: str | None = None
    actor_id: EntityId | None = None
    trace_id: EntityId | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AssetQuery(ApiModel):
    member_id: EntityId
    asset_type: AssetCategory | None = None
    action: str | None = None
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None
    requested_actions: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class AssetQueryResponse(ApiModel):
    handles: list[AssetHandleDetail] = Field(default_factory=list)


class ResolvedAsset(ApiModel):
    handle_id: EntityId
    asset_id: EntityId
    asset_type: AssetCategory
    action: str
    tool_name: str
    member_id: EntityId
    task_id: EntityId | None = None
    summary: str
    allowed_actions: list[str] = Field(default_factory=list)
    approval_required_actions: list[str] = Field(default_factory=list)
    resource: dict[str, Any] = Field(default_factory=dict)
    has_secret: bool = False
    expires_at: datetime | None = None
