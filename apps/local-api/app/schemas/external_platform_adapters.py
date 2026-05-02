from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    ExternalPlatformActionPlan,
    ExternalPlatformAdapter,
    ExternalPlatformAdapterDriftEvent,
    ExternalPlatformAdapterExecution,
    ExternalPlatformAdapterStep,
    ExternalPlatformAdapterVersion,
)
from pydantic import Field


class ExternalPlatformAdapterCreateRequest(ApiModel):
    platform_key: str = Field(min_length=1)
    action_type: str = "publish_content"
    adapter_type: str = Field(default="browser", pattern="^(browser|mcp)$")
    display_name: str = Field(min_length=1)
    status: str = "active"
    supported_actions: list[str] = Field(default_factory=list)
    required_asset_types: list[str] = Field(default_factory=lambda: ["account"])
    allowed_domains: list[str] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)
    version: str = "1.0.0"
    metadata: dict[str, Any] = Field(default_factory=dict)
    organization_id: EntityId = "org_default"


class ExternalPlatformAdapterValidateResponse(ApiModel):
    adapter_id: EntityId | None = None
    valid: bool
    status: str
    issues: list[dict[str, Any]] = Field(default_factory=list)
    message: str


class ExternalPlatformAdapterResponse(ApiModel):
    adapter: ExternalPlatformAdapter
    version: ExternalPlatformAdapterVersion | None = None
    validation: ExternalPlatformAdapterValidateResponse | None = None
    message: str


class ExternalPlatformAdapterListResponse(ApiModel):
    items: list[ExternalPlatformAdapter] = Field(default_factory=list)


class ExternalPlatformAdapterCompileRequest(ApiModel):
    adapter_id: EntityId | None = None
    adapter_type: str | None = Field(default=None, pattern="^(browser|mcp)$")
    force_recompile: bool = False


class ExternalPlatformAdapterExecuteRequest(ApiModel):
    adapter_id: EntityId | None = None
    adapter_type: str | None = Field(default=None, pattern="^(browser|mcp)$")
    approval_id: EntityId | None = None
    force: bool = False
    allow_discovery: bool = True


class ExternalPlatformAdapterResumeRequest(ApiModel):
    adapter_id: EntityId | None = None
    adapter_type: str | None = Field(default=None, pattern="^(browser|mcp)$")
    approval_id: EntityId | None = None
    human_resolution: dict[str, Any] = Field(default_factory=dict)


class ExternalPlatformDiscoveryResult(ApiModel):
    discovery_id: EntityId
    plan_id: EntityId
    platform_key: str
    action_type: str
    status: str
    learned_adapter_manifest: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    failure_reason: str | None = None
    user_visible_message: str
    adapter_id: EntityId | None = None


class ExternalPlatformAdapterPlanResponse(ApiModel):
    plan: ExternalPlatformActionPlan
    adapter: ExternalPlatformAdapter | None = None
    version: ExternalPlatformAdapterVersion | None = None
    execution: ExternalPlatformAdapterExecution | None = None
    steps: list[ExternalPlatformAdapterStep] = Field(default_factory=list)
    drift_events: list[ExternalPlatformAdapterDriftEvent] = Field(default_factory=list)
    discovery: ExternalPlatformDiscoveryResult | None = None
    message: str
    next_step: str | None = None
