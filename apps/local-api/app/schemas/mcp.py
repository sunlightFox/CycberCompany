from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    MCPContentSanitizationReport,
    MCPLifecycleEvent,
    MCPOutputTaintRecord,
    MCPPromptRecord,
    MCPProtocolValidationReport,
    MCPResourceRecord,
    MCPRuntimeProfile,
    MCPServerRecord,
    MCPToolRecord,
)
from pydantic import Field


class MCPServerCreateRequest(ApiModel):
    server_id: EntityId | None = None
    display_name: str = Field(min_length=1)
    description: str | None = None
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env_refs: list[str] = Field(default_factory=list)
    allowed_skills: list[str] = Field(default_factory=list)
    permission: dict[str, Any] = Field(default_factory=dict)
    risk_policy: dict[str, Any] = Field(default_factory=dict)
    trust_level: str = "restricted"


class MCPServerListResponse(ApiModel):
    items: list[MCPServerRecord] = Field(default_factory=list)


class MCPRuntimeProfileResponse(MCPRuntimeProfile):
    pass


class MCPLifecycleEventListResponse(ApiModel):
    items: list[MCPLifecycleEvent] = Field(default_factory=list)


class MCPProtocolReportListResponse(ApiModel):
    items: list[MCPProtocolValidationReport] = Field(default_factory=list)


class MCPSanitizationReportListResponse(ApiModel):
    items: list[MCPContentSanitizationReport] = Field(default_factory=list)


class MCPTaintRecordListResponse(ApiModel):
    items: list[MCPOutputTaintRecord] = Field(default_factory=list)


class MCPToolListResponse(ApiModel):
    items: list[MCPToolRecord] = Field(default_factory=list)


class MCPResourceListResponse(ApiModel):
    items: list[MCPResourceRecord] = Field(default_factory=list)


class MCPPromptListResponse(ApiModel):
    items: list[MCPPromptRecord] = Field(default_factory=list)


class MCPSyncResponse(ApiModel):
    server: MCPServerRecord
    tools_synced: int = 0
    resources_synced: int = 0
    prompts_synced: int = 0
