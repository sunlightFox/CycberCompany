from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.tasks import (
    ToolActionPolicyListResponse,
    ToolBoundaryResponse,
    ToolDlpReportListResponse,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolListResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("", response_model=ToolListResponse)
async def list_tools(registry: ServiceRegistry = Depends(get_registry)) -> ToolListResponse:
    return ToolListResponse(items=await registry.tool_runtime.list_tools())


@router.get("/policies", response_model=ToolActionPolicyListResponse)
async def list_tool_policies(
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolActionPolicyListResponse:
    return ToolActionPolicyListResponse(
        items=await registry.tool_runtime.list_action_policies()
    )


@router.get("/calls/{tool_call_id}/boundary", response_model=ToolBoundaryResponse)
async def tool_call_boundary(
    tool_call_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolBoundaryResponse:
    return ToolBoundaryResponse(
        **await registry.tool_runtime.boundary_for_tool_call(tool_call_id)
    )


@router.get("/calls/{tool_call_id}/dlp", response_model=ToolDlpReportListResponse)
async def tool_call_dlp(
    tool_call_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolDlpReportListResponse:
    return ToolDlpReportListResponse(
        items=await registry.tool_runtime.dlp_reports_for_tool_call(tool_call_id)
    )


@router.get("/{tool_name}")
async def get_tool(
    tool_name: str,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.tool_runtime.get_tool(tool_name)


@router.post("/execute", response_model=ToolExecuteResponse, response_model_exclude_none=True)
async def execute_tool(
    payload: ToolExecuteRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolExecuteResponse:
    return await registry.tool_runtime.execute(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
