from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.external_platform import (
    ExternalPlatformAccountCandidatesRequest,
    ExternalPlatformAccountCandidatesResponse,
    ExternalPlatformActionPlanCreateRequest,
    ExternalPlatformActionPlanResponse,
    ExternalPlatformIntentResolveRequest,
    ExternalPlatformIntentResolveResponse,
    ExternalPlatformPlanClarifyRequest,
    ExternalPlatformPlanExecuteRequest,
    ExternalPlatformProviderInfo,
    ExternalPlatformProviderListResponse,
    ExternalPlatformTargetCreateRequest,
    ExternalPlatformTargetListResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/external-platform", tags=["external-platform"])


@router.post("/targets")
async def create_target(
    payload: ExternalPlatformTargetCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.external_platform_action_service.create_target(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/targets", response_model=ExternalPlatformTargetListResponse)
async def list_targets(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformTargetListResponse:
    return ExternalPlatformTargetListResponse(
        items=await registry.external_platform_action_service.list_targets(
            status=status,
            limit=limit,
        )
    )


@router.get("/providers", response_model=ExternalPlatformProviderListResponse)
async def list_providers(
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformProviderListResponse:
    return ExternalPlatformProviderListResponse(
        items=[
            ExternalPlatformProviderInfo(**item.__dict__)
            for item in registry.external_platform_action_service.list_providers()
        ]
    )


@router.post("/intents/resolve", response_model=ExternalPlatformIntentResolveResponse)
async def resolve_intent(
    payload: ExternalPlatformIntentResolveRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformIntentResolveResponse:
    return await registry.external_platform_action_service.resolve_intent(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/account-candidates",
    response_model=ExternalPlatformAccountCandidatesResponse,
)
async def account_candidates(
    payload: ExternalPlatformAccountCandidatesRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAccountCandidatesResponse:
    return await registry.external_platform_action_service.account_candidates(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/action-plans", response_model=ExternalPlatformActionPlanResponse)
async def create_action_plan(
    payload: ExternalPlatformActionPlanCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformActionPlanResponse:
    return await registry.external_platform_action_service.create_plan(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/action-plans/{plan_id}", response_model=ExternalPlatformActionPlanResponse)
async def get_action_plan(
    plan_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformActionPlanResponse:
    return await registry.external_platform_action_service.get_plan(plan_id)


@router.post("/action-plans/{plan_id}/clarify", response_model=ExternalPlatformActionPlanResponse)
async def clarify_action_plan(
    plan_id: str,
    payload: ExternalPlatformPlanClarifyRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformActionPlanResponse:
    return await registry.external_platform_action_service.clarify_plan(
        plan_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/action-plans/{plan_id}/execute", response_model=ExternalPlatformActionPlanResponse)
async def execute_action_plan(
    plan_id: str,
    request: Request,
    payload: ExternalPlatformPlanExecuteRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformActionPlanResponse:
    return await registry.external_platform_action_service.execute_plan(
        plan_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
