from __future__ import annotations

from core_types import ErrorCode, MemoryItem
from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.schemas.memory import (
    MemoryCandidateDecisionResponse,
    MemoryCandidateListResponse,
    MemoryDeleteResponse,
    MemoryExtractRequest,
    MemoryExtractResponse,
    MemoryJobListResponse,
    MemoryListResponse,
    MemoryRelationItem,
    MemoryRelationsResponse,
    MemorySearchApiRequest,
    MemorySearchApiResponse,
    MemorySourceMessage,
    MemorySourceResponse,
    MemoryUpdateRequest,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("", response_model=MemoryListResponse)
async def list_memory(
    member_id: str | None = None,
    status: str | None = None,
    layer: str | None = None,
    kind: str | None = None,
    sensitivity: str | None = None,
    query: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryListResponse:
    return MemoryListResponse(
        items=await registry.memory_service.list_memories(
            member_id=member_id,
            status=status,
            layer=layer,
            kind=kind,
            sensitivity=sensitivity,
            query=query,
            limit=limit,
        )
    )


@router.post("/search", response_model=MemorySearchApiResponse)
async def search_memory(
    payload: MemorySearchApiRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemorySearchApiResponse:
    return await registry.memory_service.search(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/extract", response_model=MemoryExtractResponse)
async def extract_memory(
    payload: MemoryExtractRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryExtractResponse:
    trace_id = payload.trace_id or getattr(request.state, "trace_id", None)
    if payload.turn_id:
        return await registry.memory_service.extract_from_turn(
            payload.turn_id,
            trace_id=trace_id,
        )
    if not payload.text:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "text 或 turn_id 必须提供一个",
            status_code=422,
        )
    member_id = payload.member_id or "mem_xiaoyao"
    return await registry.memory_service.extract_from_text(
        payload.text,
        member_id=member_id,
        conversation_id=payload.conversation_id,
        trace_id=trace_id,
        force=True,
    )


@router.get("/candidates", response_model=MemoryCandidateListResponse)
async def list_memory_candidates(
    member_id: str | None = None,
    decision: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryCandidateListResponse:
    return MemoryCandidateListResponse(
        items=await registry.memory_service.list_candidates(
            member_id=member_id,
            decision=decision,
            limit=limit,
        )
    )


@router.get("/jobs", response_model=MemoryJobListResponse)
async def list_memory_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryJobListResponse:
    return MemoryJobListResponse(
        items=await registry.memory_service.list_jobs(
            status=status,
            job_type=job_type,
            limit=limit,
        )
    )


@router.post(
    "/candidates/{candidate_id}/approve",
    response_model=MemoryCandidateDecisionResponse,
)
async def approve_memory_candidate(
    candidate_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryCandidateDecisionResponse:
    candidate, memory = await registry.memory_service.approve_candidate(
        candidate_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return MemoryCandidateDecisionResponse(candidate=candidate, memory=memory)


@router.post(
    "/candidates/{candidate_id}/reject",
    response_model=MemoryCandidateDecisionResponse,
)
async def reject_memory_candidate(
    candidate_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryCandidateDecisionResponse:
    candidate = await registry.memory_service.reject_candidate(
        candidate_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return MemoryCandidateDecisionResponse(candidate=candidate, memory=None)


@router.get("/{memory_id}", response_model=MemoryItem)
async def get_memory(
    memory_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryItem:
    return await registry.memory_service.get_memory(memory_id)


@router.patch("/{memory_id}", response_model=MemoryItem)
async def update_memory(
    memory_id: str,
    payload: MemoryUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryItem:
    return await registry.memory_service.update_memory(
        memory_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{memory_id}/archive", response_model=MemoryItem)
async def archive_memory(
    memory_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryItem:
    return await registry.memory_service.archive_memory(
        memory_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{memory_id}/restore", response_model=MemoryItem)
async def restore_memory(
    memory_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryItem:
    return await registry.memory_service.restore_memory(
        memory_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.delete("/{memory_id}", response_model=MemoryDeleteResponse)
async def delete_memory(
    memory_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryDeleteResponse:
    item = await registry.memory_service.delete_memory(
        memory_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return MemoryDeleteResponse(memory_id=item.memory_id, status=item.status)


@router.get("/{memory_id}/relations", response_model=MemoryRelationsResponse)
async def get_memory_relations(
    memory_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemoryRelationsResponse:
    rows = await registry.memory_service.list_relations(memory_id)
    return MemoryRelationsResponse(items=[MemoryRelationItem(**row) for row in rows])


@router.get("/{memory_id}/source", response_model=MemorySourceResponse)
async def get_memory_source(
    memory_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemorySourceResponse:
    source = await registry.memory_service.source_for_memory(memory_id)
    message = source.get("source_message")
    return MemorySourceResponse(
        memory_id=source["memory_id"],
        source=source["source"],
        source_message=MemorySourceMessage(**message) if message else None,
        trace_id=source.get("trace_id"),
    )
