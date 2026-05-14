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
from app.schemas.external_platform_adapters import (
    ExternalPlatformAdapterCompileRequest,
    ExternalPlatformAdapterCreateRequest,
    ExternalPlatformAdapterExecuteRequest,
    ExternalPlatformAdapterListResponse,
    ExternalPlatformAdapterPlanResponse,
    ExternalPlatformAdapterResponse,
    ExternalPlatformAdapterResumeRequest,
    ExternalPlatformAdapterValidateResponse,
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


@router.post("/adapters", response_model=ExternalPlatformAdapterResponse)
async def register_adapter(
    payload: ExternalPlatformAdapterCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterResponse:
    return await registry.external_platform_adapter_service.register_adapter(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/adapters", response_model=ExternalPlatformAdapterListResponse)
async def list_adapters(
    platform_key: str | None = None,
    adapter_type: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterListResponse:
    return ExternalPlatformAdapterListResponse(
        items=await registry.external_platform_adapter_service.list_adapters(
            platform_key=platform_key,
            adapter_type=adapter_type,
            status=status,
            limit=limit,
        )
    )


@router.get("/adapters/{adapter_id}", response_model=ExternalPlatformAdapterResponse)
async def get_adapter(
    adapter_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterResponse:
    return await registry.external_platform_adapter_service.get_adapter(adapter_id)


@router.post(
    "/adapters/{adapter_id}/validate",
    response_model=ExternalPlatformAdapterValidateResponse,
)
async def validate_adapter(
    adapter_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterValidateResponse:
    return await registry.external_platform_adapter_service.validate_adapter(adapter_id)


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


@router.post(
    "/action-plans/{plan_id}/compile",
    response_model=ExternalPlatformAdapterPlanResponse,
)
async def compile_action_plan_adapter(
    plan_id: str,
    request: Request,
    payload: ExternalPlatformAdapterCompileRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterPlanResponse:
    return await registry.external_platform_adapter_service.compile_plan(
        plan_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/action-plans/{plan_id}/execute-adapter",
    response_model=ExternalPlatformAdapterPlanResponse,
)
async def execute_action_plan_adapter(
    plan_id: str,
    request: Request,
    payload: ExternalPlatformAdapterExecuteRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterPlanResponse:
    return await registry.external_platform_adapter_service.execute_adapter(
        plan_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/action-plans/{plan_id}/discover-adapter",
    response_model=ExternalPlatformAdapterPlanResponse,
)
async def discover_action_plan_adapter(
    plan_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterPlanResponse:
    return await registry.external_platform_adapter_service.discover_adapter(
        plan_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/action-plans/{plan_id}/resume-after-human",
    response_model=ExternalPlatformAdapterPlanResponse,
)
async def resume_action_plan_adapter(
    plan_id: str,
    request: Request,
    payload: ExternalPlatformAdapterResumeRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterPlanResponse:
    return await registry.external_platform_adapter_service.resume_after_human(
        plan_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/action-plans/{plan_id}/resume-after-login",
    response_model=ExternalPlatformAdapterPlanResponse,
)
async def resume_action_plan_adapter_after_login(
    plan_id: str,
    request: Request,
    payload: ExternalPlatformAdapterResumeRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExternalPlatformAdapterPlanResponse:
    resume_payload = payload or ExternalPlatformAdapterResumeRequest()
    if "login_completed" not in resume_payload.human_resolution:
        resume_payload = ExternalPlatformAdapterResumeRequest(
            adapter_id=resume_payload.adapter_id,
            adapter_type=resume_payload.adapter_type,
            approval_id=resume_payload.approval_id,
            human_resolution={**resume_payload.human_resolution, "login_completed": True},
        )
    return await registry.external_platform_adapter_service.resume_after_human(
        plan_id,
        resume_payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
