from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.settings import RuntimeSettingsPatch, RuntimeSettingsResponse
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=RuntimeSettingsResponse)
async def get_settings(
    request: Request,
    organization_id: str = Query(default="org_default"),
    registry: ServiceRegistry = Depends(get_registry),
) -> RuntimeSettingsResponse:
    return await registry.settings_service.get_settings(
        organization_id=organization_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.patch("", response_model=RuntimeSettingsResponse)
async def update_settings(
    payload: RuntimeSettingsPatch,
    request: Request,
    organization_id: str = Query(default="org_default"),
    registry: ServiceRegistry = Depends(get_registry),
) -> RuntimeSettingsResponse:
    return await registry.settings_service.update_settings(
        payload,
        organization_id=organization_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
