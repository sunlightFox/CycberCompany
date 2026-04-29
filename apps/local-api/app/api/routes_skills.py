from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.skills import (
    BundleInstallRequest,
    BundleInstallResponse,
    SkillCandidateDecisionRequest,
    SkillCandidateListResponse,
    SkillCandidatePromoteResponse,
    SkillEvalResponse,
    SkillListResponse,
    SkillMatchRequest,
    SkillMatchResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.post("/install", response_model=BundleInstallResponse)
async def install_skill_bundle(
    payload: BundleInstallRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BundleInstallResponse:
    bundle, skills, preview = await registry.skill_plugin_service.install_bundle(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BundleInstallResponse(
        bundle=bundle,
        skills=skills,
        permission_preview=preview,
        status=bundle.status,
    )


@router.get("", response_model=SkillListResponse)
async def list_skills(
    status: str | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillListResponse:
    return SkillListResponse(items=await registry.skill_plugin_service.list_skills(status=status))


@router.get("/candidates", response_model=SkillCandidateListResponse)
async def list_candidates(
    status: str | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillCandidateListResponse:
    return SkillCandidateListResponse(
        items=await registry.skill_plugin_service.list_candidates(status=status)
    )


@router.post("/candidates/{candidate_id}/promote", response_model=SkillCandidatePromoteResponse)
async def promote_candidate(
    candidate_id: str,
    payload: SkillCandidateDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillCandidatePromoteResponse:
    bundle, skills = await registry.skill_plugin_service.promote_candidate(
        candidate_id,
        reviewed_by_member_id=payload.reviewed_by_member_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return SkillCandidatePromoteResponse(bundle=bundle, skills=skills, status=bundle.status)


@router.post("/candidates/{candidate_id}/reject")
async def reject_candidate(
    candidate_id: str,
    payload: SkillCandidateDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.skill_plugin_service.reject_candidate(
        candidate_id,
        reviewed_by_member_id=payload.reviewed_by_member_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{skill_id}/enable", response_model=SkillListResponse)
async def enable_skill(
    skill_id: str,
    payload: SkillCandidateDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillListResponse:
    return SkillListResponse(
        items=[
            await registry.skill_plugin_service.enable_skill(
                skill_id,
                actor_member_id=payload.reviewed_by_member_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ]
    )


@router.post("/{skill_id}/disable", response_model=SkillListResponse)
async def disable_skill(
    skill_id: str,
    payload: SkillCandidateDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillListResponse:
    return SkillListResponse(
        items=[
            await registry.skill_plugin_service.disable_skill(
                skill_id,
                actor_member_id=payload.reviewed_by_member_id,
                reason=payload.reason,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ]
    )


@router.post("/{skill_id}/eval", response_model=SkillEvalResponse)
async def eval_skill(
    skill_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillEvalResponse:
    return SkillEvalResponse(
        **(
            await registry.skill_plugin_service.run_eval(
                skill_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post("/match", response_model=SkillMatchResponse)
async def match_skills(
    payload: SkillMatchRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillMatchResponse:
    return SkillMatchResponse(
        items=await registry.skill_plugin_service.match_skills(
            payload,
            trace_id=getattr(request.state, "trace_id", None),
        )
    )


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.skill_plugin_service.get_skill(skill_id)
