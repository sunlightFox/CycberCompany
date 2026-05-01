from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class ExternalPlatformTarget(ApiModel):
    target_id: EntityId
    organization_id: EntityId
    platform_key: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    supported_actions: list[str] = Field(default_factory=list)
    required_asset_types: list[str] = Field(default_factory=list)
    execution_modes: list[str] = Field(default_factory=list)
    risk_defaults: dict[str, str] = Field(default_factory=dict)
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalPlatformActionIntent(ApiModel):
    intent_id: EntityId
    organization_id: EntityId
    member_id: EntityId
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    trace_id: EntityId | None = None
    platform_hint: str | None = None
    platform_key: str | None = None
    action_type: str
    content_redacted: str | None = None
    content_summary: str | None = None
    target_hint: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0
    status: str
    missing_fields: list[str] = Field(default_factory=list)
    resolver_evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AccountAssetCandidate(ApiModel):
    asset_id: EntityId
    handle_id: EntityId | None = None
    asset_type: str = "account"
    provider_key: str | None = None
    display_name: str
    owner_scope: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    approval_required_actions: list[str] = Field(default_factory=list)
    sensitivity: str = "medium"
    risk_level: str = "R1"
    selection_reason: str
    secret_material_visible: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)


class ExternalPlatformActionPlan(ApiModel):
    plan_id: EntityId
    intent_id: EntityId
    organization_id: EntityId
    member_id: EntityId
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None
    approval_id: EntityId | None = None
    trace_id: EntityId | None = None
    platform_key: str | None = None
    target_id: EntityId | None = None
    selected_asset_id: EntityId | None = None
    selected_handle_id: EntityId | None = None
    action_type: str
    execution_mode: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    risk_level: str = "R1"
    content_summary: str | None = None
    failure_reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalPlatformExecution(ApiModel):
    execution_id: EntityId
    plan_id: EntityId
    organization_id: EntityId
    member_id: EntityId
    executor: str
    step_type: str
    status: str
    request_summary: dict[str, Any] = Field(default_factory=dict)
    response_summary: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None
    latency_ms: int = 0
    trace_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None


class ExternalPlatformPlanEvent(ApiModel):
    event_id: EntityId
    plan_id: EntityId
    organization_id: EntityId
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_redacted: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
