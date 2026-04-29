from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.skills import (
    PermissionPreviewResponse,
    PluginActionRequest,
    PluginEventsResponse,
    PluginListResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/plugins", tags=["plugins"])


@router.get("", response_model=PluginListResponse)
async def list_plugins(registry: ServiceRegistry = Depends(get_registry)) -> PluginListResponse:
    return PluginListResponse(items=await registry.skill_plugin_service.list_bundles())


@router.get("/{bundle_id}")
async def get_plugin(
    bundle_id: str,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.skill_plugin_service.get_bundle(bundle_id)


@router.post("/{bundle_id}/preview-permissions", response_model=PermissionPreviewResponse)
async def preview_permissions(
    bundle_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> PermissionPreviewResponse:
    return PermissionPreviewResponse(
        **(
            await registry.skill_plugin_service.preview_permissions(
                bundle_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post("/{bundle_id}/enable")
async def enable_plugin(
    bundle_id: str,
    payload: PluginActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.skill_plugin_service.enable_bundle(
        bundle_id,
        actor_member_id=payload.actor_member_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{bundle_id}/disable")
async def disable_plugin(
    bundle_id: str,
    payload: PluginActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.skill_plugin_service.disable_bundle(
        bundle_id,
        actor_member_id=payload.actor_member_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{bundle_id}/revoke")
async def revoke_plugin(
    bundle_id: str,
    payload: PluginActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.skill_plugin_service.revoke_bundle(
        bundle_id,
        actor_member_id=payload.actor_member_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/{bundle_id}/events", response_model=PluginEventsResponse)
async def plugin_events(
    bundle_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PluginEventsResponse:
    return PluginEventsResponse(items=await registry.skill_plugin_service.list_events(bundle_id))
