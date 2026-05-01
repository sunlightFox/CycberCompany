from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.checkpoints import (
    CheckpointCreateRequest,
    CheckpointDetailResponse,
    CheckpointItemsResponse,
    CheckpointListResponse,
    RollbackEventListResponse,
    RollbackRequest,
    RollbackResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api", tags=["checkpoints"])


@router.post("/tasks/{task_id}/checkpoints", response_model=CheckpointDetailResponse)
async def create_checkpoint(
    task_id: str,
    payload: CheckpointCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> CheckpointDetailResponse:
    checkpoint = await registry.checkpoint_service.create_checkpoint(
        task_id=task_id,
        paths=payload.paths,
        checkpoint_type=payload.checkpoint_type,
        scope=payload.scope,
        step_id=payload.step_id,
        tool_call_id=payload.tool_call_id,
        reason=payload.reason,
        metadata=payload.metadata,
        trace_id=getattr(request.state, "trace_id", None),
    )
    checkpoint, items = await registry.checkpoint_service.checkpoint_detail(
        checkpoint.checkpoint_id
    )
    return CheckpointDetailResponse(
        **checkpoint.model_dump(mode="json"),
        items=items,
    )


@router.get("/tasks/{task_id}/checkpoints", response_model=CheckpointListResponse)
async def list_task_checkpoints(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> CheckpointListResponse:
    return CheckpointListResponse(
        items=await registry.checkpoint_service.list_checkpoints(task_id)
    )


@router.get("/checkpoints/{checkpoint_id}", response_model=CheckpointDetailResponse)
async def get_checkpoint(
    checkpoint_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> CheckpointDetailResponse:
    checkpoint, items = await registry.checkpoint_service.checkpoint_detail(checkpoint_id)
    return CheckpointDetailResponse(
        **checkpoint.model_dump(mode="json"),
        items=items,
    )


@router.get("/checkpoints/{checkpoint_id}/items", response_model=CheckpointItemsResponse)
async def checkpoint_items(
    checkpoint_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> CheckpointItemsResponse:
    return CheckpointItemsResponse(
        items=await registry.checkpoint_service.list_items(checkpoint_id)
    )


@router.post("/checkpoints/{checkpoint_id}/rollback", response_model=RollbackResponse)
async def rollback_checkpoint(
    checkpoint_id: str,
    payload: RollbackRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> RollbackResponse:
    event, items = await registry.checkpoint_service.rollback(
        checkpoint_id,
        requested_by=payload.requested_by,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return RollbackResponse(event=event, items=items)


@router.get("/tasks/{task_id}/rollback-events", response_model=RollbackEventListResponse)
async def list_task_rollback_events(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> RollbackEventListResponse:
    return RollbackEventListResponse(
        items=await registry.checkpoint_service.list_rollback_events(task_id)
    )
