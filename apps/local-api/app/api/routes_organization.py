from __future__ import annotations

import json

from core_types import ErrorCode
from fastapi import APIRouter, Depends

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.schemas.organization import OrganizationSummary
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/organization", tags=["organization"])


@router.get("/current", response_model=OrganizationSummary)
async def current_organization(
    registry: ServiceRegistry = Depends(get_registry),
) -> OrganizationSummary:
    row = await registry.organization.get_current()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "当前组织不存在", status_code=404)
    return OrganizationSummary(
        organization_id=row["organization_id"],
        shell_id=row["shell_id"],
        display_name=row["display_name"],
        owner_user_id=row["owner_user_id"],
        owner_title=row["owner_title"],
        settings=json.loads(row["settings_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

