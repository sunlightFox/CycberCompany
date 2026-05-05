from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.agent_workbench import (
    AgentContextFileDiffResponse,
    AgentContextFileReplayResponse,
    AgentContextFileVersionListResponse,
    AgentContextFileVersionResponse,
    AgentWorkbenchContextPackBuildRequest,
    AgentWorkbenchContextPackResponse,
    AgentWorkbenchJobListResponse,
    AgentWorkbenchProcessResponse,
    AgentWorkbenchReflectRequest,
    AgentWorkbenchReflectResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/agent-workbench", tags=["agent-workbench"])


@router.post("/turns/{turn_id}/reflect", response_model=AgentWorkbenchReflectResponse)
async def reflect_turn(
    turn_id: str,
    request: Request,
    payload: AgentWorkbenchReflectRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentWorkbenchReflectResponse:
    mode = (payload.mode if payload else "enqueue").strip() or "enqueue"
    trace_id = getattr(request.state, "trace_id", None)
    if mode == "immediate":
        return AgentWorkbenchReflectResponse(
            status="completed",
            result=await registry.agent_workbench_service.reflect_turn(
                turn_id,
                trace_id=trace_id,
            ),
        )
    job = await registry.agent_workbench_service.enqueue_reflect_after_turn(turn_id)
    return AgentWorkbenchReflectResponse(status="queued" if job else "skipped", job=job)


@router.get("/reflection-jobs", response_model=AgentWorkbenchJobListResponse)
async def list_reflection_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentWorkbenchJobListResponse:
    return AgentWorkbenchJobListResponse(
        items=await registry.agent_workbench_service.list_jobs(
            status=status,
            job_type=job_type,
            limit=limit,
        )
    )


@router.post("/reflection-jobs/process", response_model=AgentWorkbenchProcessResponse)
async def process_reflection_jobs(
    request: Request,
    limit: int = Query(default=10, ge=1, le=50),
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentWorkbenchProcessResponse:
    return AgentWorkbenchProcessResponse(
        processed_jobs=await registry.agent_workbench_service.process_pending_jobs(
            limit=limit,
            trace_id=getattr(request.state, "trace_id", None),
        )
    )


@router.post("/context-packs/build", response_model=AgentWorkbenchContextPackResponse)
async def build_context_pack(
    payload: AgentWorkbenchContextPackBuildRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentWorkbenchContextPackResponse:
    return AgentWorkbenchContextPackResponse(
        pack=await registry.agent_workbench_service.build_context_pack(
            member_id=payload.member_id,
            conversation_id=payload.conversation_id,
            turn_id=payload.turn_id,
            persist=payload.persist,
            trace_id=getattr(request.state, "trace_id", None),
        )
    )


@router.get("/context-packs/latest", response_model=AgentWorkbenchContextPackResponse)
async def latest_context_pack(
    member_id: str,
    conversation_id: str | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentWorkbenchContextPackResponse:
    return AgentWorkbenchContextPackResponse(
        pack=await registry.agent_workbench_service.latest_context_pack(
            member_id=member_id,
            conversation_id=conversation_id,
        )
    )


@router.get("/context-files", response_model=AgentContextFileVersionListResponse)
async def list_context_files(
    member_id: str | None = None,
    conversation_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentContextFileVersionListResponse:
    return AgentContextFileVersionListResponse(
        items=await registry.agent_workbench_service.list_context_files(
            member_id=member_id,
            conversation_id=conversation_id,
            limit=limit,
        )
    )


@router.get("/context-files/diff", response_model=AgentContextFileDiffResponse)
async def diff_context_files(
    from_version_id: str,
    to_version_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentContextFileDiffResponse:
    return AgentContextFileDiffResponse(
        diff=await registry.agent_workbench_service.diff_context_files(
            from_version_id=from_version_id,
            to_version_id=to_version_id,
        )
    )


@router.get("/context-files/{version_id}", response_model=AgentContextFileVersionResponse)
async def get_context_file(
    version_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentContextFileVersionResponse:
    return AgentContextFileVersionResponse(
        version=await registry.agent_workbench_service.get_context_file_version(version_id)
    )


@router.get("/context-files/{version_id}/replay", response_model=AgentContextFileReplayResponse)
async def replay_context_file(
    version_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentContextFileReplayResponse:
    return AgentContextFileReplayResponse(
        replay=await registry.agent_workbench_service.replay_context_file(version_id)
    )
