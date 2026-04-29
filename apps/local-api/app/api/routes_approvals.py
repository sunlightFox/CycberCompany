from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.tasks import ApprovalDecisionRequest, ApprovalDetailResponse, TaskDetailResponse
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


@router.get("/{approval_id}", response_model=ApprovalDetailResponse)
async def get_approval(
    approval_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ApprovalDetailResponse:
    return ApprovalDetailResponse(
        **(await registry.approval_service.get(approval_id)).model_dump(mode="json")
    )


@router.post("/{approval_id}/approve", response_model=TaskDetailResponse)
async def approve(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    trace_id = getattr(request.state, "trace_id", None)
    await registry.approval_service.approve(
        approval_id,
        actor_type=payload.actor_type,
        actor_id=payload.actor_id,
        reason=payload.reason,
        trace_id=trace_id,
    )
    return TaskDetailResponse(
        **(
            await registry.task_engine.handle_approval_resolved(
                approval_id,
                trace_id=trace_id,
            )
        ).model_dump(mode="json")
    )


@router.post("/{approval_id}/deny", response_model=TaskDetailResponse)
async def deny(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    trace_id = getattr(request.state, "trace_id", None)
    await registry.approval_service.deny(
        approval_id,
        actor_type=payload.actor_type,
        actor_id=payload.actor_id,
        reason=payload.reason,
        trace_id=trace_id,
    )
    return TaskDetailResponse(
        **(
            await registry.task_engine.handle_approval_resolved(
                approval_id,
                trace_id=trace_id,
            )
        ).model_dump(mode="json")
    )


@router.post("/{approval_id}/edit", response_model=TaskDetailResponse)
async def edit(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    trace_id = getattr(request.state, "trace_id", None)
    await registry.approval_service.edit(
        approval_id,
        actor_type=payload.actor_type,
        actor_id=payload.actor_id,
        reason=payload.reason,
        edited_payload=payload.edited_payload or {},
        trace_id=trace_id,
    )
    return TaskDetailResponse(
        **(
            await registry.task_engine.handle_approval_resolved(
                approval_id,
                trace_id=trace_id,
            )
        ).model_dump(mode="json")
    )
