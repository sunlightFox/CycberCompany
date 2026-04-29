from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.design_alignment import (
    VectorProviderConfigResponse,
    VectorProviderListResponse,
    VectorProviderUpdateRequest,
    VectorStatusResponse,
    VectorSyncJobCreateRequest,
    VectorSyncJobResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/vector", tags=["vector"])


@router.post("/sync-jobs", response_model=VectorSyncJobResponse)
async def create_vector_sync_job(
    payload: VectorSyncJobCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> VectorSyncJobResponse:
    return await registry.vector_service.create_sync_job(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/sync-jobs/{job_id}", response_model=VectorSyncJobResponse)
async def get_vector_sync_job(
    job_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> VectorSyncJobResponse:
    return await registry.vector_service.get_job(job_id)


@router.get("/status", response_model=VectorStatusResponse)
async def get_vector_status(
    registry: ServiceRegistry = Depends(get_registry),
) -> VectorStatusResponse:
    return await registry.vector_service.status()


@router.get("/providers", response_model=VectorProviderListResponse)
async def list_vector_providers(
    registry: ServiceRegistry = Depends(get_registry),
) -> VectorProviderListResponse:
    return await registry.vector_service.list_providers()


@router.patch("/providers/{provider_id}", response_model=VectorProviderConfigResponse)
async def update_vector_provider(
    provider_id: str,
    payload: VectorProviderUpdateRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> VectorProviderConfigResponse:
    return await registry.vector_service.update_provider(provider_id, payload)
