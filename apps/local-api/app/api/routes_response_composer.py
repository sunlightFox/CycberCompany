from __future__ import annotations

from core_types import TraceSpanType
from fastapi import APIRouter, Depends, Request
from response_composer import ComposeRequest, ResponseComposer
from trace_service import redact

from app.api.dependencies import get_registry
from app.schemas.design_alignment import (
    ResponseComposerPreviewRequest,
    ResponseComposerPreviewResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/response-composer", tags=["response-composer"])


@router.post("/preview", response_model=ResponseComposerPreviewResponse)
async def preview_response_plan(
    payload: ResponseComposerPreviewRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ResponseComposerPreviewResponse:
    trace_id = getattr(request.state, "trace_id", None)
    span_id = (
        await registry.trace_service.start_span(
            trace_id,
            span_type=TraceSpanType.RESPONSE_COMPOSE,
            name="preview response plan",
            input_data=redact(payload.model_dump(mode="json")),
        )
        if trace_id
        else None
    )
    composer = ResponseComposer()
    result = await composer.compose(
        ComposeRequest(
            user_text=payload.user_text,
            result_summary=payload.result_summary,
            style=payload.style,
            scenario=payload.scenario,
            persona=payload.persona,
            heart=payload.heart,
            risk_level=payload.risk_level.value if payload.risk_level else None,
            route_profile=payload.route_profile,
            notices=payload.notices,
            trace_refs=[
                *payload.trace_refs,
                *([{"trace_id": trace_id, "span_type": "response.compose"}] if trace_id else []),
            ],
        )
    )
    if span_id:
        await registry.trace_service.end_span(
            span_id,
            output_data={
                "scenario": payload.scenario,
                "style": payload.style,
                "redaction_summary": result.response_plan.redaction_summary,
            },
        )
    return ResponseComposerPreviewResponse(
        text=result.text,
        response_plan=result.response_plan,
        metadata=result.metadata,
    )
