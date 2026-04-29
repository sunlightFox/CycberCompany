from __future__ import annotations

from core_types import CapabilityRequest
from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.capabilities import CapabilityDecisionResponse
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.post("/decide", response_model=CapabilityDecisionResponse)
async def decide_capability(
    payload: CapabilityRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> CapabilityDecisionResponse:
    decision = await registry.capability_service.decide(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return CapabilityDecisionResponse(**decision.model_dump(mode="json"))
