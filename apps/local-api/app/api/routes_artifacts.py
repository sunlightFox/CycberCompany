from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

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


@router.get("/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> FileResponse:
    artifact, path = await registry.artifact_store.open_download(artifact_id)
    filename = path.name
    encoded = quote(filename)
    return FileResponse(
        path,
        media_type=artifact.content_type or "application/octet-stream",
        filename=filename,
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{_ascii_filename(filename)}\"; "
                f"filename*=UTF-8''{encoded}"
            )
        },
    )


def _ascii_filename(filename: str) -> str:
    safe = "".join(
        char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_"
        for char in filename
    )
    return safe or "artifact"
