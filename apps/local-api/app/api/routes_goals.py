from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.goals import (
    GoalActionRequest,
    GoalCheckinCreateRequest,
    GoalCheckinListResponse,
    GoalCheckinReplyRequest,
    GoalCheckinResponse,
    GoalConfirmPlanRequest,
    GoalCreateRequest,
    GoalDetailResponse,
    GoalEventListResponse,
    GoalListResponse,
    GoalProgressResponse,
    GoalResponse,
    GoalSupervisionRequest,
    GoalSupervisionResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/goals", tags=["goals"])


@router.post("", response_model=GoalDetailResponse)
async def create_goal(
    payload: GoalCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalDetailResponse:
    bundle = await registry.goal_service.create_goal(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return _detail_response(bundle)


@router.get("", response_model=GoalListResponse)
async def list_goals(
    status: str | None = None,
    owner_member_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalListResponse:
    return GoalListResponse(
        items=await registry.goal_service.list_goals(
            owner_member_id=owner_member_id,
            conversation_id=conversation_id,
            status=status,
            limit=limit,
        )
    )


@router.get("/{goal_id}", response_model=GoalDetailResponse)
async def get_goal(
    goal_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalDetailResponse:
    return _detail_response(await registry.goal_service.detail(goal_id))


@router.post("/{goal_id}/plans/{goal_plan_id}/confirm", response_model=GoalDetailResponse)
async def confirm_goal_plan(
    goal_id: str,
    goal_plan_id: str,
    payload: GoalConfirmPlanRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalDetailResponse:
    bundle = await registry.goal_service.confirm_plan(
        goal_id,
        goal_plan_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    if payload.start_supervision:
        await registry.goal_service.create_supervision(
            goal_id,
            GoalSupervisionRequest(**payload.supervision),
            trace_id=getattr(request.state, "trace_id", None),
        )
        bundle = await registry.goal_service.detail(goal_id)
    return _detail_response(bundle)


@router.post("/{goal_id}/supervision", response_model=GoalSupervisionResponse)
async def start_goal_supervision(
    goal_id: str,
    payload: GoalSupervisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalSupervisionResponse:
    policy = await registry.goal_service.create_supervision(
        goal_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return GoalSupervisionResponse(
        policy=policy,
        scheduled_task_id=policy.scheduled_task_id,
    )


@router.post("/{goal_id}/pause", response_model=GoalResponse)
async def pause_goal(
    goal_id: str,
    payload: GoalActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalResponse:
    del payload
    goal = await registry.goal_service.pause(
        goal_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return GoalResponse(**goal.model_dump(mode="json"))


@router.post("/{goal_id}/resume", response_model=GoalResponse)
async def resume_goal(
    goal_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalResponse:
    goal = await registry.goal_service.resume(
        goal_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return GoalResponse(**goal.model_dump(mode="json"))


@router.post("/{goal_id}/cancel", response_model=GoalResponse)
async def cancel_goal(
    goal_id: str,
    payload: GoalActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalResponse:
    del payload
    goal = await registry.goal_service.cancel(
        goal_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return GoalResponse(**goal.model_dump(mode="json"))


@router.post("/{goal_id}/archive", response_model=GoalResponse)
async def archive_goal(
    goal_id: str,
    payload: GoalActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalResponse:
    del payload
    goal = await registry.goal_service.archive(
        goal_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return GoalResponse(**goal.model_dump(mode="json"))


@router.post("/{goal_id}/checkins", response_model=GoalCheckinResponse)
async def create_goal_checkin(
    goal_id: str,
    payload: GoalCheckinCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalCheckinResponse:
    checkin = await registry.goal_service.create_checkin(
        goal_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return GoalCheckinResponse(**checkin.model_dump(mode="json"))


@router.get("/{goal_id}/checkins", response_model=GoalCheckinListResponse)
async def list_goal_checkins(
    goal_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalCheckinListResponse:
    return GoalCheckinListResponse(
        items=await registry.goal_service.list_checkins(goal_id, limit=limit)
    )


@router.post("/{goal_id}/checkins/{checkin_id}/reply", response_model=GoalProgressResponse)
async def reply_goal_checkin(
    goal_id: str,
    checkin_id: str,
    payload: GoalCheckinReplyRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalProgressResponse:
    progress = await registry.goal_service.reply_checkin(
        goal_id,
        checkin_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return GoalProgressResponse(**progress.model_dump(mode="json"))


@router.get("/{goal_id}/progress", response_model=GoalProgressResponse)
async def get_goal_progress(
    goal_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalProgressResponse:
    progress = await registry.goal_service.latest_progress(goal_id)
    return GoalProgressResponse(**progress.model_dump(mode="json"))


@router.get("/{goal_id}/events", response_model=GoalEventListResponse)
async def list_goal_events(
    goal_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> GoalEventListResponse:
    return GoalEventListResponse(
        items=await registry.goal_service.list_events(goal_id, limit=limit)
    )


def _detail_response(bundle) -> GoalDetailResponse:  # type: ignore[no-untyped-def]
    return GoalDetailResponse(
        goal=bundle.goal,
        active_plan=bundle.active_plan,
        plan_items=bundle.plan_items,
        supervision_policy=bundle.supervision_policy,
        progress=bundle.progress,
    )
