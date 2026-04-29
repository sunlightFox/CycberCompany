from __future__ import annotations

from core_types import ApiModel, EntityId, RiskLevel


class ToolCallRequest(ApiModel):
    tool_name: str
    member_id: EntityId
    risk_level: RiskLevel


class ToolCallResult(ApiModel):
    status: str
    summary: str


class ToolRuntime:
    async def call(self, request: ToolCallRequest) -> ToolCallResult:
        raise NotImplementedError("Tool Runtime contract requires an application implementation")
