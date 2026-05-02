from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.skill_governance import (
    SkillAnalysisResponse,
    SkillEvalBindingsResponse,
    SkillGrantCreateRequest,
    SkillGrantListResponse,
    SkillInstallPreviewRequest,
    SkillInstallPreviewResponse,
    SkillOutputTaintResponse,
    SkillRevokeRequest,
    SkillRollbackRequest,
    SkillRollbackResponse,
    SkillUpgradeRequest,
    SkillUpgradeResponse,
)
from app.schemas.skills import (
    BundleInstallRequest,
    BundleInstallResponse,
    SkillCandidateDecisionRequest,
    SkillCandidateListResponse,
    SkillCandidatePromoteResponse,
    SkillCatalogSearchResponse,
    SkillEvalResponse,
    SkillListResponse,
    SkillMatchRequest,
    SkillMatchResponse,
    SkillRepositoryListResponse,
    SkillRepositoryPatchRequest,
    SkillRepositoryRefreshResponse,
    SkillRepositoryUpsertRequest,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/skills", tags=["skills"])


@router.post("/preview-install", response_model=SkillInstallPreviewResponse)
async def preview_skill_bundle_install(
    payload: SkillInstallPreviewRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillInstallPreviewResponse:
    return await registry.skill_governance_service.preview_install(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


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


@router.get("/repositories", response_model=SkillRepositoryListResponse)
async def list_skill_repositories(
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillRepositoryListResponse:
    return SkillRepositoryListResponse(
        items=await registry.skill_repository_service.list_repositories()
    )


@router.put("/repositories/{repository_id}", response_model=SkillRepositoryListResponse)
async def upsert_skill_repository(
    repository_id: str,
    payload: SkillRepositoryUpsertRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillRepositoryListResponse:
    repository = await registry.skill_repository_service.upsert_repository(
        repository_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return SkillRepositoryListResponse(items=[repository])


@router.patch("/repositories/{repository_id}", response_model=SkillRepositoryListResponse)
async def patch_skill_repository(
    repository_id: str,
    payload: SkillRepositoryPatchRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillRepositoryListResponse:
    repository = await registry.skill_repository_service.patch_repository(
        repository_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return SkillRepositoryListResponse(items=[repository])


@router.delete("/repositories/{repository_id}", response_model=SkillRepositoryListResponse)
async def disable_skill_repository(
    repository_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillRepositoryListResponse:
    repository = await registry.skill_repository_service.disable_repository(
        repository_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return SkillRepositoryListResponse(items=[repository])


@router.post(
    "/repositories/{repository_id}/refresh",
    response_model=SkillRepositoryRefreshResponse,
)
async def refresh_skill_repository(
    repository_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillRepositoryRefreshResponse:
    repository, sync_run, indexed_count = (
        await registry.skill_repository_service.refresh_repository(
            repository_id,
            trace_id=getattr(request.state, "trace_id", None),
        )
    )
    return SkillRepositoryRefreshResponse(
        repository=repository,
        sync_run=sync_run,
        indexed_count=indexed_count,
    )


@router.get("/catalog/search", response_model=SkillCatalogSearchResponse)
async def search_skill_catalog(
    q: str | None = None,
    repository_id: str | None = None,
    tag: str | None = None,
    limit: int = 50,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillCatalogSearchResponse:
    return SkillCatalogSearchResponse(
        items=await registry.skill_repository_service.search(
            query=q,
            repository_id=repository_id,
            tag=tag,
            limit=limit,
        )
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


@router.post("/{skill_id}/grants", response_model=SkillGrantListResponse)
async def create_skill_grant(
    skill_id: str,
    payload: SkillGrantCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillGrantListResponse:
    grant = await registry.skill_governance_service.create_grant(
        skill_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return SkillGrantListResponse(items=[grant])


@router.get("/{skill_id}/grants", response_model=SkillGrantListResponse)
async def list_skill_grants(
    skill_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillGrantListResponse:
    return SkillGrantListResponse(
        items=await registry.skill_governance_service.list_grants(skill_id)
    )


@router.post("/{skill_id}/revoke")
async def revoke_skill(
    skill_id: str,
    payload: SkillRevokeRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.skill_governance_service.revoke_skill(
        skill_id,
        actor_member_id=payload.actor_member_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{skill_id}/upgrade", response_model=SkillUpgradeResponse)
async def upgrade_skill(
    skill_id: str,
    payload: SkillUpgradeRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillUpgradeResponse:
    return await registry.skill_governance_service.upgrade_skill(
        skill_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{skill_id}/rollback", response_model=SkillRollbackResponse)
async def rollback_skill(
    skill_id: str,
    payload: SkillRollbackRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillRollbackResponse:
    return await registry.skill_governance_service.rollback_skill(
        skill_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/{skill_id}/analysis", response_model=SkillAnalysisResponse)
async def get_skill_analysis(
    skill_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillAnalysisResponse:
    return SkillAnalysisResponse(
        items=await registry.skill_governance_service.list_analysis(skill_id)
    )


@router.get("/{skill_id}/eval-bindings", response_model=SkillEvalBindingsResponse)
async def get_skill_eval_bindings(
    skill_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillEvalBindingsResponse:
    return SkillEvalBindingsResponse(
        items=await registry.skill_governance_service.list_eval_bindings(skill_id)
    )


@router.get("/{skill_id}/output-taints", response_model=SkillOutputTaintResponse)
async def get_skill_output_taints(
    skill_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillOutputTaintResponse:
    return SkillOutputTaintResponse(
        items=await registry.skill_governance_service.list_output_taints(skill_id)
    )


@router.get("/{skill_id}")
async def get_skill(
    skill_id: str,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.skill_plugin_service.get_skill(skill_id)
