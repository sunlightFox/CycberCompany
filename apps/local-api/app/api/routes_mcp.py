from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.mcp import (
    MCPLifecycleEventListResponse,
    MCPPromptListResponse,
    MCPProtocolReportListResponse,
    MCPResourceListResponse,
    MCPRuntimeProfileResponse,
    MCPSanitizationReportListResponse,
    MCPServerCreateRequest,
    MCPServerListResponse,
    MCPSyncResponse,
    MCPTaintRecordListResponse,
    MCPToolListResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.post("/servers")
async def create_server(
    payload: MCPServerCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.mcp_service.create_server(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/servers", response_model=MCPServerListResponse)
async def list_servers(registry: ServiceRegistry = Depends(get_registry)) -> MCPServerListResponse:
    return MCPServerListResponse(items=await registry.mcp_service.list_servers())


@router.get("/servers/{server_id}")
async def get_server(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.mcp_service.get_server(server_id)


@router.get(
    "/servers/{server_id}/runtime-profile",
    response_model=MCPRuntimeProfileResponse,
)
async def runtime_profile(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPRuntimeProfileResponse:
    return MCPRuntimeProfileResponse(
        **(await registry.mcp_service.runtime_profile(server_id)).model_dump(mode="json")
    )


@router.get(
    "/servers/{server_id}/lifecycle-events",
    response_model=MCPLifecycleEventListResponse,
)
async def lifecycle_events(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPLifecycleEventListResponse:
    return MCPLifecycleEventListResponse(
        items=await registry.mcp_service.lifecycle_events(server_id)
    )


@router.get(
    "/servers/{server_id}/protocol-reports",
    response_model=MCPProtocolReportListResponse,
)
async def protocol_reports(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPProtocolReportListResponse:
    return MCPProtocolReportListResponse(
        items=await registry.mcp_service.protocol_reports(server_id)
    )


@router.get(
    "/servers/{server_id}/sanitization-reports",
    response_model=MCPSanitizationReportListResponse,
)
async def sanitization_reports(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPSanitizationReportListResponse:
    return MCPSanitizationReportListResponse(
        items=await registry.mcp_service.sanitization_reports(server_id)
    )


@router.get(
    "/servers/{server_id}/taint-records",
    response_model=MCPTaintRecordListResponse,
)
async def taint_records(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPTaintRecordListResponse:
    return MCPTaintRecordListResponse(items=await registry.mcp_service.taint_records(server_id))


@router.post("/servers/{server_id}/enable")
async def enable_server(
    server_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.mcp_service.enable_server(
        server_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/servers/{server_id}/disable")
async def disable_server(
    server_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.mcp_service.disable_server(
        server_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/servers/{server_id}/connect")
async def connect_server(
    server_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.mcp_service.connect_server(
        server_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/servers/{server_id}/disconnect")
async def disconnect_server(
    server_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.mcp_service.disconnect_server(
        server_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/servers/{server_id}/sync", response_model=MCPSyncResponse)
async def sync_server(
    server_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPSyncResponse:
    return await registry.mcp_service.sync_server(
        server_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/servers/{server_id}/tools", response_model=MCPToolListResponse)
async def list_tools(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPToolListResponse:
    return MCPToolListResponse(items=await registry.mcp_service.list_tools(server_id))


@router.get("/servers/{server_id}/resources", response_model=MCPResourceListResponse)
async def list_resources(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPResourceListResponse:
    return MCPResourceListResponse(items=await registry.mcp_service.list_resources(server_id))


@router.get("/servers/{server_id}/prompts", response_model=MCPPromptListResponse)
async def list_prompts(
    server_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MCPPromptListResponse:
    return MCPPromptListResponse(items=await registry.mcp_service.list_prompts(server_id))
