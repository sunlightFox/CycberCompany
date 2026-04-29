from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.design_alignment import (
    HeartStateResponse,
    HeartStateTransitionsResponse,
    PersonaConsistencyProfileResponse,
    PersonaHeartReplayRunCreateRequest,
    PersonaHeartReplayRunResponse,
    PersonaProfileListResponse,
    PersonaProfileResponse,
    PersonaProfileUpdateRequest,
)
from app.services.registry import ServiceRegistry

persona_router = APIRouter(prefix="/api/persona", tags=["persona"])
heart_router = APIRouter(prefix="/api/heart", tags=["heart"])
persona_heart_router = APIRouter(prefix="/api/persona-heart", tags=["persona-heart"])


@persona_router.get("/profiles", response_model=PersonaProfileListResponse)
async def list_persona_profiles(
    registry: ServiceRegistry = Depends(get_registry),
) -> PersonaProfileListResponse:
    return PersonaProfileListResponse(items=await registry.persona_heart_service.list_profiles())


@persona_router.get("/profiles/{profile_id}", response_model=PersonaProfileResponse)
async def get_persona_profile(
    profile_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PersonaProfileResponse:
    return await registry.persona_heart_service.get_profile(profile_id)


@persona_router.get(
    "/profiles/{profile_id}/consistency",
    response_model=PersonaConsistencyProfileResponse,
)
async def get_persona_consistency_profile(
    profile_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PersonaConsistencyProfileResponse:
    return await registry.persona_heart_service.get_consistency_profile(profile_id)


@persona_router.patch("/profiles/{profile_id}", response_model=PersonaProfileResponse)
async def update_persona_profile(
    profile_id: str,
    payload: PersonaProfileUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> PersonaProfileResponse:
    return await registry.persona_heart_service.update_profile(
        profile_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@heart_router.get("/state/{member_id}", response_model=HeartStateResponse)
async def get_heart_state(
    member_id: str,
    request: Request,
    text: str | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> HeartStateResponse:
    return await registry.persona_heart_service.heart_state(
        member_id,
        text=text,
        trace_id=getattr(request.state, "trace_id", None),
    )


@heart_router.get("/state/{member_id}/transitions", response_model=HeartStateTransitionsResponse)
async def list_heart_state_transitions(
    member_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> HeartStateTransitionsResponse:
    return await registry.persona_heart_service.list_heart_transitions(member_id)


@persona_heart_router.post("/replay-runs", response_model=PersonaHeartReplayRunResponse)
async def create_persona_heart_replay_run(
    payload: PersonaHeartReplayRunCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> PersonaHeartReplayRunResponse:
    return await registry.persona_heart_service.create_replay_run(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@persona_heart_router.get("/replay-runs/{run_id}", response_model=PersonaHeartReplayRunResponse)
async def get_persona_heart_replay_run(
    run_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PersonaHeartReplayRunResponse:
    return await registry.persona_heart_service.get_replay_run(run_id)
