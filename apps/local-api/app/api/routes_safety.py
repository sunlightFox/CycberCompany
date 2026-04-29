from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.design_alignment import SafetyDecisionResponse, SafetyEvaluateRequest
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/safety", tags=["safety"])


@router.post("/evaluate", response_model=SafetyDecisionResponse)
async def evaluate_safety(
    payload: SafetyEvaluateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SafetyDecisionResponse:
    return await registry.safety_decision_service.evaluate(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/decisions/{decision_id}", response_model=SafetyDecisionResponse)
async def get_safety_decision(
    decision_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SafetyDecisionResponse:
    return await registry.safety_decision_service.get(decision_id)
