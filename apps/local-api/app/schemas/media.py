from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    MediaAnalysis,
    MediaAsset,
    MediaChatBinding,
    MediaDerivative,
    MediaEditPlan,
    MediaIORecord,
    MediaMultimodalSummary,
    MediaProviderHealthRecord,
    MediaSpeechRender,
    MediaSpeechTranscript,
    TaskArtifact,
    VideoWorkflowPlan,
    VideoWorkflowProfile,
    VideoWorkflowStep,
)
from pydantic import Field


class MediaImportArtifactRequest(ApiModel):
    task_id: EntityId
    artifact_id: EntityId
    media_type: str | None = None
    display_name: str | None = None
    sensitivity: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MediaProbeRequest(ApiModel):
    refresh: bool = False


class MediaExtractFramesRequest(ApiModel):
    mode: str = "interval"
    interval_ms: int = Field(default=5000, ge=250, le=600000)
    timestamps_ms: list[int] = Field(default_factory=list)
    max_frames: int = Field(default=5, ge=1, le=25)


class MediaExtractAudioRequest(ApiModel):
    output_format: str = "wav"


class MediaTranscribeAudioRequest(ApiModel):
    provider: str = "local"
    language: str | None = None
    force: bool = False


class MediaSTTRequest(ApiModel):
    provider: str = "local"
    language: str | None = None
    force: bool = False


class MediaTTSRequest(ApiModel):
    task_id: EntityId
    organization_id: EntityId | None = None
    text: str = Field(min_length=1)
    provider: str = "local"
    voice: str | None = None
    output_format: str = "wav"
    sensitivity: str = "low"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MediaSummarizeRequest(ApiModel):
    provider: str = "local"
    summary_type: str | None = None
    force: bool = False


class MediaSceneDetectRequest(ApiModel):
    threshold: float = Field(default=0.35, ge=0.01, le=1.0)
    max_segments: int = Field(default=12, ge=1, le=100)


class MediaTimelineRequest(ApiModel):
    include_transcript: bool = True
    include_frames: bool = True


class MediaEditPlanCreateRequest(ApiModel):
    goal: str = Field(min_length=1)
    output_profile: dict[str, Any] = Field(default_factory=dict)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    render: bool = False


class MediaRenderEditRequest(ApiModel):
    force: bool = False
    render_strategy: str = "copy"


class MediaExportArtifactRequest(ApiModel):
    export_mode: str = "prepare"
    destination: str | None = None


class MediaAssetResponse(ApiModel):
    media: MediaAsset


class MediaDerivativeListResponse(ApiModel):
    items: list[MediaDerivative] = Field(default_factory=list)


class MediaEditPlanResponse(ApiModel):
    edit_plan: MediaEditPlan
    media: MediaAsset | None = None
    artifact: TaskArtifact | None = None
    message: str
    next_step: str | None = None


class MediaOperationResponse(ApiModel):
    media: MediaAsset | None = None
    derivatives: list[MediaDerivative] = Field(default_factory=list)
    analysis: MediaAnalysis | None = None
    edit_plan: MediaEditPlan | None = None
    artifacts: list[TaskArtifact] = Field(default_factory=list)
    io_records: list[MediaIORecord] = Field(default_factory=list)
    provider_health: list[MediaProviderHealthRecord] = Field(default_factory=list)
    transcripts: list[MediaSpeechTranscript] = Field(default_factory=list)
    renders: list[MediaSpeechRender] = Field(default_factory=list)
    summaries: list[MediaMultimodalSummary] = Field(default_factory=list)
    chat_bindings: list[MediaChatBinding] = Field(default_factory=list)
    status: str
    message: str
    degraded_reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class MediaProviderHealthResponse(ApiModel):
    items: list[MediaProviderHealthRecord] = Field(default_factory=list)


class MediaIORecordResponse(ApiModel):
    items: list[MediaIORecord] = Field(default_factory=list)


class VideoWorkflowCreateRequest(ApiModel):
    task_id: EntityId
    media_id: EntityId
    goal: str = Field(min_length=1)
    workflow_profile: VideoWorkflowProfile = Field(default_factory=VideoWorkflowProfile)


class VideoWorkflowExecuteRequest(ApiModel):
    force: bool = False
    approval_id: EntityId | None = None


class VideoWorkflowResumeRequest(ApiModel):
    approval_id: EntityId | None = None


class VideoWorkflowResponse(ApiModel):
    workflow: VideoWorkflowPlan
    steps: list[VideoWorkflowStep] = Field(default_factory=list)
    media: MediaAsset | None = None
    message: str
    next_step: str | None = None
