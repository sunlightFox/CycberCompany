from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_registry
from app.schemas.tasks import ArtifactReadResponse
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}", response_model=ArtifactReadResponse)
async def read_artifact(
    artifact_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ArtifactReadResponse:
    artifact, preview = await registry.artifact_store.read_preview(artifact_id)
    return ArtifactReadResponse(artifact=artifact, content_preview=preview)
