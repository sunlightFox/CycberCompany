from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class ExternalPlatformAdapter(ApiModel):
    adapter_id: EntityId
    organization_id: EntityId
    platform_key: str
    action_type: str
    adapter_type: str
    display_name: str
    status: str = "active"
    supported_actions: list[str] = Field(default_factory=list)
    required_asset_types: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalPlatformAdapterVersion(ApiModel):
    adapter_version_id: EntityId
    adapter_id: EntityId
    version: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    manifest_checksum: str
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalPlatformAdapterStep(ApiModel):
    step_id: EntityId
    plan_id: EntityId
    adapter_id: EntityId
    adapter_version_id: EntityId
    step_name: str
    executor: str
    tool_name: str | None = None
    risk_level: str = "R1"
    requires_approval: bool = False
    status: str = "planned"
    input_redacted: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    approval_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    mcp_call_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalPlatformAdapterExecution(ApiModel):
    adapter_execution_id: EntityId
    plan_id: EntityId
    adapter_id: EntityId
    adapter_version_id: EntityId
    status: str
    executor: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExternalPlatformAdapterDriftEvent(ApiModel):
    drift_event_id: EntityId
    plan_id: EntityId
    adapter_id: EntityId
    step_id: EntityId | None = None
    drift_type: str
    status: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
