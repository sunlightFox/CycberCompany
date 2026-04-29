from __future__ import annotations

from typing import Literal

from core_types import ApiModel, EntityId
from pydantic import Field


class ModelRoutingSettings(ApiModel):
    default_route: str = "local_main"
    allow_cloud_fallback: bool = True
    high_privacy_allow_cloud: bool = False
    medium_privacy_allow_cloud: bool = True
    reserved_output_tokens: int = Field(default=1024, ge=128, le=32768)
    context_budget_tokens: int = Field(default=8192, ge=1024, le=262144)


class SafetySettings(ApiModel):
    require_confirmation: list[str] = Field(default_factory=list)
    deny_paths: list[str] = Field(default_factory=list)
    terminal_policy_profile: str = "task_artifact_sandbox"
    approval_policy: dict[str, str | bool | int | float] = Field(default_factory=dict)


class VectorSettings(ApiModel):
    provider: Literal["chroma", "disabled", "fts_fallback"] = "chroma"
    enabled: bool = True
    degraded_fallback: Literal["fts", "disabled"] = "fts"


class MCPSettings(ApiModel):
    enabled: bool = False
    allowed_stdio_commands: list[str] = Field(default_factory=list)
    blocked_stdio_markers: list[str] = Field(default_factory=list)
    default_unknown_tool_status: Literal["disabled", "approval_required"] = "disabled"


class MemorySettings(ApiModel):
    implicit_extraction_enabled: bool = True
    candidate_review_threshold: float = Field(default=0.55, ge=0.0, le=1.0)


class RuntimeSettings(ApiModel):
    model_routing: ModelRoutingSettings = Field(default_factory=ModelRoutingSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    vector: VectorSettings = Field(default_factory=VectorSettings)
    mcp: MCPSettings = Field(default_factory=MCPSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)


class RuntimeSettingsPatch(ApiModel):
    model_routing: ModelRoutingSettings | None = None
    safety: SafetySettings | None = None
    vector: VectorSettings | None = None
    mcp: MCPSettings | None = None
    memory: MemorySettings | None = None
    updated_by_member_id: EntityId | None = None


class RuntimeSettingsResponse(ApiModel):
    setting_id: EntityId
    organization_id: EntityId
    settings: RuntimeSettings
    version: int
    source: str = "runtime_settings"
    trace_id: EntityId | None = None
    updated_by_member_id: EntityId | None = None
    created_at: str
    updated_at: str
