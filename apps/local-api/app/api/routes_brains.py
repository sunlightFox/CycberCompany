from __future__ import annotations

from core_types import ErrorCode
from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.schemas.brain import (
    BrainCreateRequest,
    BrainDecisionPreviewRequest,
    BrainDecisionPreviewResponse,
    BrainListResponse,
    BrainProviderPresetListResponse,
    BrainResponse,
    BrainUpdateRequest,
    BrainVerifyResponse,
)
from app.services.brain_provider_catalog import list_provider_presets
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/brains", tags=["brains"])
decision_router = APIRouter(prefix="/api/brain", tags=["brain"])


@router.get("", response_model=BrainListResponse)
async def list_brains(registry: ServiceRegistry = Depends(get_registry)) -> BrainListResponse:
    return BrainListResponse(items=await registry.brain_service.list_brains())


@router.get("/providers", response_model=BrainProviderPresetListResponse)
async def list_brain_providers() -> BrainProviderPresetListResponse:
    return BrainProviderPresetListResponse(items=list_provider_presets())


@router.post("", response_model=BrainResponse)
async def create_brain(
    payload: BrainCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrainResponse:
    return await registry.brain_service.create_brain(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/{brain_id}", response_model=BrainResponse)
async def get_brain(
    brain_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrainResponse:
    brain = await registry.brain_service.get_brain(brain_id)
    if brain is None:
        raise AppError(ErrorCode.NOT_FOUND, "大脑不存在", status_code=404)
    return brain


@router.patch("/{brain_id}", response_model=BrainResponse)
async def update_brain(
    brain_id: str,
    payload: BrainUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrainResponse:
    return await registry.brain_service.update_brain(
        brain_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{brain_id}/verify", response_model=BrainVerifyResponse)
async def verify_brain(
    brain_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrainVerifyResponse:
    return await registry.brain_service.verify_brain(
        brain_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{brain_id}/enable", response_model=BrainResponse)
async def enable_brain(
    brain_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrainResponse:
    return await registry.brain_service.set_enabled(
        brain_id,
        True,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{brain_id}/disable", response_model=BrainResponse)
async def disable_brain(
    brain_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrainResponse:
    return await registry.brain_service.set_enabled(
        brain_id,
        False,
        trace_id=getattr(request.state, "trace_id", None),
    )


@decision_router.post("/decision-preview", response_model=BrainDecisionPreviewResponse)
async def preview_brain_decision(
    payload: BrainDecisionPreviewRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrainDecisionPreviewResponse:
    decision = await registry.brain_decision_service.decide(
        text=payload.text,
        member_id=payload.member_id,
        conversation_id=payload.conversation_id,
        privacy_level=payload.privacy_level,
        trace_id=getattr(request.state, "trace_id", None),
        persist=False,
    )
    return BrainDecisionPreviewResponse(**decision.model_dump(mode="json"))
