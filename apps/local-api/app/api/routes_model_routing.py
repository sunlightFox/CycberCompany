from __future__ import annotations

from brain import BrainRouter, BrainRouteRequest
from core_types import ErrorCode
from fastapi import APIRouter, Depends, Request
from safety_service import SafetyService

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.schemas.model_routing import (
    ModelRoutingPreviewRequest,
    ModelRoutingPreviewResponse,
    ModelRoutingResponse,
    ModelRoutingUpdateRequest,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/model-routing", tags=["model-routing"])


@router.get("", response_model=ModelRoutingResponse)
async def get_model_routing(
    registry: ServiceRegistry = Depends(get_registry),
) -> ModelRoutingResponse:
    return ModelRoutingResponse(config=await registry.model_routing_service.get_config())


@router.patch("", response_model=ModelRoutingResponse)
async def update_model_routing(
    payload: ModelRoutingUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ModelRoutingResponse:
    config = await registry.model_routing_service.update_config(payload.config)
    await registry.audit_service.write_event(
        actor_type="system",
        action="model_routing.updated",
        object_type="model_routing",
        summary="模型路由配置已更新",
        payload={"config": config},
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ModelRoutingResponse(config=config)


@router.post("/preview", response_model=ModelRoutingPreviewResponse)
async def preview_model_routing(
    payload: ModelRoutingPreviewRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> ModelRoutingPreviewResponse:
    member = await registry.members.get_member(payload.member_id)
    if member is None:
        raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
    privacy = SafetyService().classify_chat_input(payload.text)
    decision = await BrainRouter().route(
        BrainRouteRequest(
            text=payload.text,
            member_id=payload.member_id,
            conversation_id=payload.conversation_id,
            default_brain_id=member.get("default_brain_id"),
            privacy_level=payload.privacy_level or privacy.privacy_level,
            available_brains=await registry.brains.list_routable_brains(),
            model_routing_config=await registry.model_routing_service.get_config(),
        )
    )
    return ModelRoutingPreviewResponse(
        intent=decision.intent,
        mode=decision.mode.value,
        route=decision.model_route,
        reason_codes=decision.reason_codes,
        rejected_candidates=decision.rejected_candidates,
    )
