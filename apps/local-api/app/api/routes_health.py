from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.health import HealthResponse
from app.schemas.release import FullHealthApiResponse
from app.services.registry import ServiceRegistry

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> HealthResponse:
    await registry.db.fetch_one("SELECT 1")
    return HealthResponse(
        status="ok",
        db="ok",
        default_shell=registry.config.app.default_shell,
        version=registry.config.app.version,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/api/health/full", response_model=FullHealthApiResponse)
async def full_health(
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> FullHealthApiResponse:
    return FullHealthApiResponse(
        **(
            await registry.release_gate_service.full_health(
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )
