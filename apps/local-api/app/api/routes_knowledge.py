from __future__ import annotations

from core_types import KnowledgeSource
from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.knowledge import (
    KnowledgeAccessLogListResponse,
    KnowledgeIndexResponse,
    KnowledgeSearchApiResponse,
    KnowledgeSearchRequest,
    KnowledgeSourceCreateRequest,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.post("/sources", response_model=KnowledgeSource)
async def create_knowledge_source(
    payload: KnowledgeSourceCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> KnowledgeSource:
    return await registry.knowledge_service.create_source(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/sources/{source_id}/index", response_model=KnowledgeIndexResponse)
async def index_knowledge_source(
    source_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> KnowledgeIndexResponse:
    return await registry.knowledge_service.index_source(
        source_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/search", response_model=KnowledgeSearchApiResponse)
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> KnowledgeSearchApiResponse:
    return await registry.knowledge_service.search(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/access-logs", response_model=KnowledgeAccessLogListResponse)
async def list_knowledge_access_logs(
    limit: int = Query(default=50, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> KnowledgeAccessLogListResponse:
    return KnowledgeAccessLogListResponse(
        items=await registry.knowledge_service.list_access_logs(limit)
    )
