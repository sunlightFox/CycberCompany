from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.scheduled_tasks import (
    ScheduledTaskActionRequest,
    ScheduledTaskCreateRequest,
    ScheduledTaskEventListResponse,
    ScheduledTaskListResponse,
    ScheduledTaskResponse,
    ScheduledTaskRunListResponse,
    ScheduledTaskRunResponse,
    ScheduledTaskTriggerRequest,
    ScheduledTaskUpdateRequest,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/scheduled-tasks", tags=["scheduled-tasks"])
run_router = APIRouter(prefix="/api/scheduled-runs", tags=["scheduled-tasks"])


@router.post("", response_model=ScheduledTaskResponse)
async def create_scheduled_task(
    payload: ScheduledTaskCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskResponse:
    task = await registry.scheduled_task_service.create(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ScheduledTaskResponse(**task.model_dump(mode="json"))


@router.get("", response_model=ScheduledTaskListResponse)
async def list_scheduled_tasks(
    status: str | None = None,
    owner_member_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskListResponse:
    return ScheduledTaskListResponse(
        items=await registry.scheduled_task_service.list(
            status=status,
            owner_member_id=owner_member_id,
            limit=limit,
        )
    )


@router.get("/{scheduled_task_id}", response_model=ScheduledTaskResponse)
async def get_scheduled_task(
    scheduled_task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskResponse:
    task = await registry.scheduled_task_service.detail(scheduled_task_id)
    return ScheduledTaskResponse(**task.model_dump(mode="json"))


@router.patch("/{scheduled_task_id}", response_model=ScheduledTaskResponse)
async def update_scheduled_task(
    scheduled_task_id: str,
    payload: ScheduledTaskUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskResponse:
    task = await registry.scheduled_task_service.update(
        scheduled_task_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ScheduledTaskResponse(**task.model_dump(mode="json"))


@router.post("/{scheduled_task_id}/pause", response_model=ScheduledTaskResponse)
async def pause_scheduled_task(
    scheduled_task_id: str,
    payload: ScheduledTaskActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskResponse:
    task = await registry.scheduled_task_service.pause(
        scheduled_task_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ScheduledTaskResponse(**task.model_dump(mode="json"))


@router.post("/{scheduled_task_id}/resume", response_model=ScheduledTaskResponse)
async def resume_scheduled_task(
    scheduled_task_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskResponse:
    task = await registry.scheduled_task_service.resume(
        scheduled_task_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ScheduledTaskResponse(**task.model_dump(mode="json"))


@router.post("/{scheduled_task_id}/cancel", response_model=ScheduledTaskResponse)
async def cancel_scheduled_task(
    scheduled_task_id: str,
    payload: ScheduledTaskActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskResponse:
    task = await registry.scheduled_task_service.cancel(
        scheduled_task_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ScheduledTaskResponse(**task.model_dump(mode="json"))


@router.post("/{scheduled_task_id}/archive", response_model=ScheduledTaskResponse)
async def archive_scheduled_task(
    scheduled_task_id: str,
    payload: ScheduledTaskActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskResponse:
    task = await registry.scheduled_task_service.archive(
        scheduled_task_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ScheduledTaskResponse(**task.model_dump(mode="json"))


@router.post("/{scheduled_task_id}/trigger", response_model=ScheduledTaskRunResponse)
async def trigger_scheduled_task(
    scheduled_task_id: str,
    payload: ScheduledTaskTriggerRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskRunResponse:
    run = await registry.scheduled_task_service.trigger(
        scheduled_task_id,
        trigger_type="manual",
        scheduled_for=payload.scheduled_for,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ScheduledTaskRunResponse(
        **run.model_dump(mode="json"),
        task_replay_ref=_task_replay_ref(run.task_id),
    )


@router.get("/{scheduled_task_id}/runs", response_model=ScheduledTaskRunListResponse)
async def list_scheduled_task_runs(
    scheduled_task_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskRunListResponse:
    return ScheduledTaskRunListResponse(
        items=await registry.scheduled_task_service.list_runs(
            scheduled_task_id,
            limit=limit,
        )
    )


@router.get("/{scheduled_task_id}/events", response_model=ScheduledTaskEventListResponse)
async def list_scheduled_task_events(
    scheduled_task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskEventListResponse:
    return ScheduledTaskEventListResponse(
        items=await registry.scheduled_task_service.list_events(scheduled_task_id)
    )


@run_router.get("/{run_id}", response_model=ScheduledTaskRunResponse)
async def get_scheduled_task_run(
    run_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ScheduledTaskRunResponse:
    run = await registry.scheduled_task_service.get_run(run_id)
    return ScheduledTaskRunResponse(
        **run.model_dump(mode="json"),
        task_replay_ref=_task_replay_ref(run.task_id),
    )


def _task_replay_ref(task_id: str | None) -> dict[str, str] | None:
    if task_id is None:
        return None
    return {"task_id": task_id, "href": f"/api/tasks/{task_id}/replay"}
