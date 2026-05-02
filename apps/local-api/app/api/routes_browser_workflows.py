from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.browser_workflows import (
    BrowserWorkflowExecuteRequest,
    BrowserWorkflowIntentResolveRequest,
    BrowserWorkflowIntentResolveResponse,
    BrowserWorkflowPlanCreateRequest,
    BrowserWorkflowPlanResponse,
    BrowserWorkflowReplayResponse,
    BrowserWorkflowResumeRequest,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/browser-workflows", tags=["browser-workflows"])


@router.post(
    "/intents/resolve",
    response_model=BrowserWorkflowIntentResolveResponse,
)
async def resolve_browser_workflow_intent(
    payload: BrowserWorkflowIntentResolveRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserWorkflowIntentResolveResponse:
    return await registry.autonomous_browser_workflow_service.resolve_intent(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/plans", response_model=BrowserWorkflowPlanResponse)
async def create_browser_workflow_plan(
    payload: BrowserWorkflowPlanCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserWorkflowPlanResponse:
    return await registry.autonomous_browser_workflow_service.create_plan(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/plans/{plan_id}", response_model=BrowserWorkflowPlanResponse)
async def get_browser_workflow_plan(
    plan_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserWorkflowPlanResponse:
    return await registry.autonomous_browser_workflow_service.get_plan(plan_id)


@router.post("/plans/{plan_id}/execute", response_model=BrowserWorkflowPlanResponse)
async def execute_browser_workflow_plan(
    plan_id: str,
    request: Request,
    payload: BrowserWorkflowExecuteRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserWorkflowPlanResponse:
    return await registry.autonomous_browser_workflow_service.execute_plan(
        plan_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/plans/{plan_id}/resume-after-human",
    response_model=BrowserWorkflowPlanResponse,
)
async def resume_browser_workflow_plan(
    plan_id: str,
    request: Request,
    payload: BrowserWorkflowResumeRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserWorkflowPlanResponse:
    return await registry.autonomous_browser_workflow_service.resume_after_human(
        plan_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/plans/{plan_id}/replay", response_model=BrowserWorkflowReplayResponse)
async def replay_browser_workflow_plan(
    plan_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserWorkflowReplayResponse:
    return await registry.autonomous_browser_workflow_service.replay(plan_id)
