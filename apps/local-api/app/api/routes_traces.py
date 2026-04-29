from __future__ import annotations

from core_types import ErrorCode, Trace
from fastapi import APIRouter, Depends

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("/{trace_id}", response_model=Trace)
async def get_trace(trace_id: str, registry: ServiceRegistry = Depends(get_registry)) -> Trace:
    trace = await registry.trace_service.get_trace(trace_id)
    if trace is None:
        raise AppError(ErrorCode.NOT_FOUND, "trace 不存在", status_code=404)
    return trace

