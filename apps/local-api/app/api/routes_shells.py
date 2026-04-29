from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.shell import (
    CurrentShellResponse,
    ShellDetailResponse,
    ShellListItem,
    ShellListResponse,
    ShellSwitchPreviewResponse,
    ShellSwitchRequest,
    ShellTemplateApplicationResponse,
    ShellTemplateApplyRequest,
    ShellTemplateListResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/shells", tags=["shells"])


@router.get("", response_model=ShellListResponse)
async def list_shells(registry: ServiceRegistry = Depends(get_registry)) -> ShellListResponse:
    rows = await registry.shells.list_shells()
    return ShellListResponse(
        items=[
            ShellListItem(
                shell_id=row["shell_id"],
                display_name=row["display_name"],
                version=row["version"],
                is_enabled=bool(row["is_enabled"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]
    )


@router.get("/current", response_model=CurrentShellResponse)
async def current_shell(registry: ServiceRegistry = Depends(get_registry)) -> CurrentShellResponse:
    organization = await registry.organization.get_current()
    shell_id = organization["shell_id"] if organization else registry.config.app.default_shell
    shell = registry.shell_runtime.load(shell_id)
    return CurrentShellResponse(**shell.model_dump())


@router.post("/switch/preview", response_model=ShellSwitchPreviewResponse)
async def shell_switch_preview(
    payload: ShellSwitchRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ShellSwitchPreviewResponse:
    return ShellSwitchPreviewResponse(
        **(
            await registry.shell_switch_service.preview(
                payload.shell_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post("/switch", response_model=ShellSwitchPreviewResponse)
async def switch_shell(
    payload: ShellSwitchRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ShellSwitchPreviewResponse:
    return ShellSwitchPreviewResponse(
        **(
            await registry.shell_switch_service.switch(
                payload.shell_id,
                actor_member_id=payload.actor_member_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.get("/{shell_id}", response_model=ShellDetailResponse)
async def get_shell(
    shell_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ShellDetailResponse:
    return ShellDetailResponse(**registry.shell_switch_service.get_shell_detail(shell_id))


@router.get("/{shell_id}/templates", response_model=ShellTemplateListResponse)
async def shell_templates(
    shell_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ShellTemplateListResponse:
    return ShellTemplateListResponse(
        shell_id=shell_id,
        templates=registry.shell_switch_service.templates(shell_id),
    )


@router.post(
    "/{shell_id}/templates/{template_key}/apply",
    response_model=ShellTemplateApplicationResponse,
)
async def apply_shell_template(
    shell_id: str,
    template_key: str,
    request: Request,
    payload: ShellTemplateApplyRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> ShellTemplateApplicationResponse:
    return ShellTemplateApplicationResponse(
        **(
            await registry.shell_switch_service.apply_template(
                shell_id,
                template_key,
                actor_member_id=payload.actor_member_id if payload else "mem_xiaoyao",
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )
