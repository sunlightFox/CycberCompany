from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_registry
from app.schemas.tasks import SkillPolicyResponse, SkillPolicyUpdateRequest
from app.services.registry import ServiceRegistry

departments_router = APIRouter(prefix="/api/departments", tags=["departments"])
roles_router = APIRouter(prefix="/api/roles", tags=["roles"])


@departments_router.get("/{department_id}/skill-policies", response_model=SkillPolicyResponse)
async def get_department_skill_policy(
    department_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillPolicyResponse:
    return SkillPolicyResponse(
        **(
            await registry.supervisor_service.get_skill_policy("department", department_id)
        ).model_dump(mode="json")
    )


@departments_router.patch("/{department_id}/skill-policies", response_model=SkillPolicyResponse)
async def update_department_skill_policy(
    department_id: str,
    payload: SkillPolicyUpdateRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillPolicyResponse:
    return SkillPolicyResponse(
        **(
            await registry.supervisor_service.update_skill_policy(
                "department",
                department_id,
                payload.model_dump(mode="json"),
            )
        ).model_dump(mode="json")
    )


@roles_router.get("/{role_id}/skill-policies", response_model=SkillPolicyResponse)
async def get_role_skill_policy(
    role_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillPolicyResponse:
    return SkillPolicyResponse(
        **(
            await registry.supervisor_service.get_skill_policy("role", role_id)
        ).model_dump(mode="json")
    )


@roles_router.patch("/{role_id}/skill-policies", response_model=SkillPolicyResponse)
async def update_role_skill_policy(
    role_id: str,
    payload: SkillPolicyUpdateRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> SkillPolicyResponse:
    return SkillPolicyResponse(
        **(
            await registry.supervisor_service.update_skill_policy(
                "role",
                role_id,
                payload.model_dump(mode="json"),
            )
        ).model_dump(mode="json")
    )
