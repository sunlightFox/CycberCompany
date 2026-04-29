from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_registry
from app.schemas.retrieval import RetrievalDiagnosticsResponse
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/retrieval", tags=["retrieval"])


@router.get("/diagnostics/{retrieval_id}", response_model=RetrievalDiagnosticsResponse)
async def get_retrieval_diagnostics(
    retrieval_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> RetrievalDiagnosticsResponse:
    return await registry.retrieval_service.diagnostics(retrieval_id)
