from __future__ import annotations

from core_types import (
    AssetDetail,
    AssetHandleDetail,
    AssetHandleEvent,
    CapabilityEdge,
)
from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.assets import (
    AssetCreateRequest,
    AssetDeleteResponse,
    AssetHandleEventListResponse,
    AssetHandleValidateRequest,
    AssetHandleValidateResponse,
    AssetListResponse,
    AssetQueryApiResponse,
    AssetQueryRequest,
    AssetResolveForToolRequest,
    AssetResolveForToolResponse,
    AssetUpdateRequest,
    AssetVerifyResponse,
    CapabilityGrantCreateRequest,
    CapabilityGrantUpdateRequest,
)
from app.schemas.capabilities import CapabilityGrantListResponse
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.post("", response_model=AssetDetail)
async def create_asset(
    payload: AssetCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetDetail:
    return await registry.asset_service.create_asset(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("", response_model=AssetListResponse)
async def list_assets(
    asset_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetListResponse:
    return AssetListResponse(
        items=await registry.asset_service.list_assets(
            asset_type=asset_type,
            status=status,
            limit=limit,
        )
    )


@router.post("/query", response_model=AssetQueryApiResponse)
async def query_assets(
    payload: AssetQueryRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetQueryApiResponse:
    return await registry.asset_broker_service.query(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/handles/{handle_id}/validate", response_model=AssetHandleValidateResponse)
async def validate_asset_handle(
    handle_id: str,
    payload: AssetHandleValidateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetHandleValidateResponse:
    return await registry.asset_broker_service.validate_handle(
        handle_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/handles/{handle_id}/resolve-for-tool", response_model=AssetResolveForToolResponse)
async def resolve_asset_for_tool(
    handle_id: str,
    payload: AssetResolveForToolRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetResolveForToolResponse:
    return await registry.asset_broker_service.resolve_for_tool(
        handle_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/handles/{handle_id}/revoke", response_model=AssetHandleDetail)
async def revoke_asset_handle(
    handle_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetHandleDetail:
    return await registry.asset_broker_service.revoke_handle(
        handle_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/handles/{handle_id}/events", response_model=AssetHandleEventListResponse)
async def list_asset_handle_events(
    handle_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetHandleEventListResponse:
    rows = await registry.asset_broker_service.list_handle_events(handle_id)
    return AssetHandleEventListResponse(items=[AssetHandleEvent(**row) for row in rows])


@router.post("/grants", response_model=CapabilityEdge)
async def create_asset_grant(
    payload: CapabilityGrantCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> CapabilityEdge:
    return await registry.capability_service.create_grant(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/grants", response_model=CapabilityGrantListResponse)
async def list_asset_grants(
    subject_type: str | None = None,
    subject_id: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> CapabilityGrantListResponse:
    return CapabilityGrantListResponse(
        items=await registry.capability_service.list_grants(
            subject_type=subject_type,
            subject_id=subject_id,
            object_type=object_type,
            object_id=object_id,
            limit=limit,
        )
    )


@router.patch("/grants/{grant_id}", response_model=CapabilityEdge)
async def update_asset_grant(
    grant_id: str,
    payload: CapabilityGrantUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> CapabilityEdge:
    return await registry.capability_service.update_grant(
        grant_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.delete("/grants/{grant_id}", response_model=CapabilityEdge)
async def delete_asset_grant(
    grant_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> CapabilityEdge:
    return await registry.capability_service.delete_grant(
        grant_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/{asset_id}", response_model=AssetDetail)
async def get_asset(
    asset_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetDetail:
    return await registry.asset_service.get_asset(asset_id)


@router.patch("/{asset_id}", response_model=AssetDetail)
async def update_asset(
    asset_id: str,
    payload: AssetUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetDetail:
    return await registry.asset_service.update_asset(
        asset_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{asset_id}/verify", response_model=AssetVerifyResponse)
async def verify_asset(
    asset_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetVerifyResponse:
    return await registry.asset_service.verify_asset(
        asset_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{asset_id}/disable", response_model=AssetDetail)
async def disable_asset(
    asset_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetDetail:
    return await registry.asset_service.set_status(
        asset_id,
        "disabled",
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{asset_id}/archive", response_model=AssetDetail)
async def archive_asset(
    asset_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetDetail:
    return await registry.asset_service.set_status(
        asset_id,
        "archived",
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{asset_id}/restore", response_model=AssetDetail)
async def restore_asset(
    asset_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetDetail:
    return await registry.asset_service.set_status(
        asset_id,
        "active",
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.delete("/{asset_id}", response_model=AssetDeleteResponse)
async def delete_asset(
    asset_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AssetDeleteResponse:
    item = await registry.asset_service.set_status(
        asset_id,
        "deleted",
        trace_id=getattr(request.state, "trace_id", None),
    )
    return AssetDeleteResponse(asset_id=item.asset_id, status=item.status)
