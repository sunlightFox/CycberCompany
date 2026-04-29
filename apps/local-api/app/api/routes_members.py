from __future__ import annotations

from core_types import ErrorCode
from fastapi import APIRouter, Depends

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.core.time import utc_now_iso
from app.schemas.member import (
    MemberDefaultBrainUpdateRequest,
    MemberDefaultBrainUpdateResponse,
    MemberListItem,
    MemberListResponse,
)
from app.schemas.tasks import (
    MemberAvailabilityResponse,
    MemberAvailabilityUpdateRequest,
    SkillPolicyResponse,
    SkillPolicyUpdateRequest,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/members", tags=["members"])


@router.get("", response_model=MemberListResponse)
async def list_members(registry: ServiceRegistry = Depends(get_registry)) -> MemberListResponse:
    rows = await registry.members.list_members()
    return MemberListResponse(items=[MemberListItem(**row) for row in rows])


@router.patch("/{member_id}/default-brain", response_model=MemberDefaultBrainUpdateResponse)
async def update_default_brain(
    member_id: str,
    payload: MemberDefaultBrainUpdateRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemberDefaultBrainUpdateResponse:
    member = await registry.members.get_member(member_id)
    if member is None:
        raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
    brain = await registry.brains.get_brain(payload.brain_id)
    if brain is None:
        raise AppError(ErrorCode.NOT_FOUND, "大脑不存在", status_code=404)
    if brain["status"] not in {"configured", "healthy"}:
        raise AppError(ErrorCode.CONFLICT, "只能绑定已配置或健康的大脑", status_code=409)
    updated_at = utc_now_iso()
    await registry.members.update_default_brain(
        member_id=member_id,
        brain_id=payload.brain_id,
        updated_at=updated_at,
    )
    return MemberDefaultBrainUpdateResponse(
        member_id=member_id,
        default_brain_id=payload.brain_id,
        updated_at=updated_at,
    )


@router.get("/{member_id}/availability", response_model=MemberAvailabilityResponse)
async def get_availability(
    member_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemberAvailabilityResponse:
    return MemberAvailabilityResponse(
        **(await registry.supervisor_service.get_availability(member_id)).model_dump(mode="json")
    )


@router.patch("/{member_id}/availability", response_model=MemberAvailabilityResponse)
async def update_availability(
    member_id: str,
    payload: MemberAvailabilityUpdateRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemberAvailabilityResponse:
    return MemberAvailabilityResponse(
        **(
            await registry.supervisor_service.update_availability(
                member_id,
                payload.model_dump(mode="json"),
            )
        ).model_dump(mode="json")
    )


@router.get("/{member_id}/skill-policies", response_model=SkillPolicyResponse)
async def get_member_skill_policy(
    member_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillPolicyResponse:
    return SkillPolicyResponse(
        **(
            await registry.supervisor_service.get_skill_policy("member", member_id)
        ).model_dump(mode="json")
    )


@router.patch("/{member_id}/skill-policies", response_model=SkillPolicyResponse)
async def update_member_skill_policy(
    member_id: str,
    payload: SkillPolicyUpdateRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillPolicyResponse:
    return SkillPolicyResponse(
        **(
            await registry.supervisor_service.update_skill_policy(
                "member",
                member_id,
                payload.model_dump(mode="json"),
            )
        ).model_dump(mode="json")
    )
