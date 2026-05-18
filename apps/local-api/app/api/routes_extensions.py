from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.extensions import (
    ExtensionActionRequest,
    ExtensionBindingResponse,
    ExtensionCompatibilityResponse,
    ExtensionDiagnosticResponse,
    ExtensionImportRequest,
    ExtensionInstallResponse,
    ExtensionListResponse,
    ExtensionPlanRunRequest,
    ExtensionPlanRunResponse,
    ExtensionPreviewResponse,
    ExtensionTaskLaunchRequest,
)
from app.schemas.tasks import TaskDetailResponse
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/extensions", tags=["extensions"])


@router.post("/preview-import", response_model=ExtensionPreviewResponse)
async def preview_import(
    payload: ExtensionImportRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExtensionPreviewResponse:
    return await registry.extension_service.preview_import(payload)


@router.post("/install", response_model=ExtensionInstallResponse)
async def install_extension(
    payload: ExtensionImportRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExtensionInstallResponse:
    bundle, skills, preview = await registry.extension_service.install(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    compatibility = await registry.extension_service.compatibility(bundle.extension_id or f"ext.{bundle.bundle_id}")
    return ExtensionInstallResponse(
        bundle=bundle,
        skills=skills,
        permission_preview=preview,
        compatibility=compatibility[0] if compatibility else None,  # type: ignore[arg-type]
        status=bundle.status,
    )


@router.get("", response_model=ExtensionListResponse)
async def list_extensions(
    registry: ServiceRegistry = Depends(get_registry),
) -> ExtensionListResponse:
    return ExtensionListResponse(items=await registry.extension_service.list_extensions())


@router.get("/{extension_id}")
async def get_extension(
    extension_id: str,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.extension_service.get_extension(extension_id)


@router.get("/{extension_id}/compatibility", response_model=ExtensionCompatibilityResponse)
async def get_compatibility(
    extension_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExtensionCompatibilityResponse:
    return ExtensionCompatibilityResponse(
        items=await registry.extension_service.compatibility(extension_id)
    )


@router.get("/{extension_id}/diagnostics", response_model=ExtensionDiagnosticResponse)
async def get_diagnostics(
    extension_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExtensionDiagnosticResponse:
    return await registry.extension_service.diagnostics(
        extension_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{extension_id}/enable")
async def enable_extension(
    extension_id: str,
    payload: ExtensionActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.extension_service.enable(
        extension_id,
        actor_member_id=payload.actor_member_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{extension_id}/disable")
async def disable_extension(
    extension_id: str,
    payload: ExtensionActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.extension_service.disable(
        extension_id,
        actor_member_id=payload.actor_member_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{extension_id}/bind", response_model=ExtensionBindingResponse)
async def bind_extension(
    extension_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExtensionBindingResponse:
    return await registry.extension_service.bind(
        extension_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{extension_id}/plan-run", response_model=ExtensionPlanRunResponse)
async def plan_run_extension(
    extension_id: str,
    payload: ExtensionPlanRunRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExtensionPlanRunResponse:
    return await registry.extension_service.plan_run(
        extension_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{extension_id}/tasks", response_model=TaskDetailResponse)
async def launch_extension_task(
    extension_id: str,
    payload: ExtensionTaskLaunchRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(
            await registry.extension_service.launch_task(
                extension_id,
                payload,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )
