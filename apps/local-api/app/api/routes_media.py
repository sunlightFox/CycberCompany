from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.media import (
    MediaAssetResponse,
    MediaDerivativeListResponse,
    MediaEditPlanCreateRequest,
    MediaEditPlanResponse,
    MediaExportArtifactRequest,
    MediaExtractAudioRequest,
    MediaExtractFramesRequest,
    MediaIORecordResponse,
    MediaImportArtifactRequest,
    MediaOperationResponse,
    MediaProbeRequest,
    MediaProviderHealthResponse,
    MediaRenderEditRequest,
    MediaSceneDetectRequest,
    MediaSTTRequest,
    MediaSummarizeRequest,
    MediaTimelineRequest,
    MediaTTSRequest,
    MediaTranscribeAudioRequest,
    VideoWorkflowCreateRequest,
    VideoWorkflowExecuteRequest,
    VideoWorkflowResponse,
    VideoWorkflowResumeRequest,
)
from app.schemas.tasks import ToolExecuteRequest, ToolExecuteResponse
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/media", tags=["media"])


@router.post("/import-artifact", response_model=MediaOperationResponse)
async def import_artifact(
    payload: MediaImportArtifactRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.import_artifact(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/video-workflows", response_model=VideoWorkflowResponse)
async def create_video_workflow(
    payload: VideoWorkflowCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> VideoWorkflowResponse:
    return await registry.video_workflow_service.create(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/video-workflows/{workflow_id}", response_model=VideoWorkflowResponse)
async def get_video_workflow(
    workflow_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> VideoWorkflowResponse:
    return await registry.video_workflow_service.get(workflow_id)


@router.post("/video-workflows/{workflow_id}/execute", response_model=VideoWorkflowResponse)
async def execute_video_workflow(
    workflow_id: str,
    request: Request,
    payload: VideoWorkflowExecuteRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> VideoWorkflowResponse:
    return await registry.video_workflow_service.execute(
        workflow_id,
        payload or VideoWorkflowExecuteRequest(),
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/video-workflows/{workflow_id}/resume", response_model=VideoWorkflowResponse)
async def resume_video_workflow(
    workflow_id: str,
    payload: VideoWorkflowResumeRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> VideoWorkflowResponse:
    return await registry.video_workflow_service.resume(
        workflow_id,
        payload.approval_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/{media_id}", response_model=MediaAssetResponse)
async def get_media(
    media_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaAssetResponse:
    return MediaAssetResponse(media=await registry.media_service.get_media(media_id))


@router.get("/{media_id}/derivatives", response_model=MediaDerivativeListResponse)
async def list_derivatives(
    media_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaDerivativeListResponse:
    return MediaDerivativeListResponse(
        items=await registry.media_service.list_derivatives(media_id)
    )


@router.post("/{media_id}/probe", response_model=MediaOperationResponse)
async def probe_media(
    media_id: str,
    request: Request,
    payload: MediaProbeRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.probe(
        media_id,
        payload or MediaProbeRequest(),
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{media_id}/extract-frames", response_model=MediaOperationResponse)
async def extract_frames(
    media_id: str,
    payload: MediaExtractFramesRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.extract_frames(
        media_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{media_id}/extract-audio", response_model=MediaOperationResponse)
async def extract_audio(
    media_id: str,
    payload: MediaExtractAudioRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.extract_audio(
        media_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{media_id}/transcribe", response_model=MediaOperationResponse)
async def transcribe_audio(
    media_id: str,
    payload: MediaTranscribeAudioRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.transcribe_audio(
        media_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{media_id}/stt", response_model=MediaOperationResponse)
async def stt_media(
    media_id: str,
    payload: MediaSTTRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.stt(
        media_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/tts", response_model=MediaOperationResponse)
async def tts_media(
    payload: MediaTTSRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.tts(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{media_id}/summarize", response_model=MediaOperationResponse)
async def summarize_media(
    media_id: str,
    payload: MediaSummarizeRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.summarize(
        media_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/{media_id}/io-records", response_model=MediaIORecordResponse)
async def list_media_io_records(
    media_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaIORecordResponse:
    return await registry.media_service.list_io_records(media_id)


@router.get("/providers/health", response_model=MediaProviderHealthResponse)
async def media_provider_health(
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaProviderHealthResponse:
    return await registry.media_service.provider_health()


@router.post("/{media_id}/scene-detect", response_model=MediaOperationResponse)
async def scene_detect(
    media_id: str,
    payload: MediaSceneDetectRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.scene_detect(
        media_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{media_id}/timeline", response_model=MediaOperationResponse)
async def timeline(
    media_id: str,
    request: Request,
    payload: MediaTimelineRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.timeline(
        media_id,
        payload or MediaTimelineRequest(),
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{media_id}/edit-plans", response_model=MediaEditPlanResponse)
async def create_edit_plan(
    media_id: str,
    payload: MediaEditPlanCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaEditPlanResponse:
    return await registry.media_service.create_edit_plan(
        media_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/edit-plans/{edit_plan_id}", response_model=MediaEditPlanResponse)
async def get_edit_plan(
    edit_plan_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaEditPlanResponse:
    edit_plan = await registry.media_service.get_edit_plan(edit_plan_id)
    media = await registry.media_service.get_media(edit_plan.media_id)
    return MediaEditPlanResponse(
        edit_plan=edit_plan,
        media=media,
        message="剪辑计划已加载",
    )


@router.post("/edit-plans/{edit_plan_id}/render", response_model=ToolExecuteResponse)
async def render_edit_plan(
    edit_plan_id: str,
    request: Request,
    payload: MediaRenderEditRequest | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolExecuteResponse:
    edit_plan = await registry.media_service.get_edit_plan(edit_plan_id)
    return await registry.tool_runtime.execute(
        ToolExecuteRequest(
            task_id=edit_plan.task_id,
            tool_name="media.render_edit",
            args={
                "edit_plan_id": edit_plan_id,
                **(payload or MediaRenderEditRequest()).model_dump(),
            },
        ),
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/{media_id}/export", response_model=MediaOperationResponse)
async def export_artifact(
    media_id: str,
    payload: MediaExportArtifactRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MediaOperationResponse:
    return await registry.media_service.export_artifact(
        media_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
