from __future__ import annotations

from core_types import AuditEventListResponse
from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_registry
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=AuditEventListResponse)
async def list_audit_events(
    limit: int = Query(default=50, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> AuditEventListResponse:
    return await registry.audit_service.list_events(limit=limit)

