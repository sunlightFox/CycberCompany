from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class MediaAsset(ApiModel):
    media_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    source_artifact_id: EntityId
    media_type: str
    display_name: str
    uri: str
    content_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    audio_streams: int = 0
    video_streams: int = 0
    sensitivity: str = "low"
    status: str = "ready"
    io_role: str = "input"
    source_kind: str = "task_artifact"
    privacy_level: str = "standard"
    provider_status: str = "local"
    replay_summary: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MediaDerivative(ApiModel):
    derivative_id: EntityId
    media_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    artifact_id: EntityId
    derivative_type: str
    time_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class MediaAnalysis(ApiModel):
    analysis_id: EntityId
    media_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    analysis_type: str
    status: str = "completed"
    model_route: str | None = None
    segments: list[dict[str, Any]] = Field(default_factory=list)
    transcript_artifact_id: EntityId | None = None
    evidence_artifact_ids: list[EntityId] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MediaEditPlan(ApiModel):
    edit_plan_id: EntityId
    media_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    goal: str
    output_profile: dict[str, Any] = Field(default_factory=dict)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "planned"
    risk_level: str = "R3"
    requires_approval: bool = True
    artifact_id: EntityId | None = None
    rendered_media_id: EntityId | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MediaProviderHealthRecord(ApiModel):
    health_record_id: EntityId
    organization_id: EntityId = "org_default"
    provider_name: str
    capability: str
    provider_type: str = "local"
    status: str
    degraded_reason: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    checked_at: datetime | str
    created_at: datetime | str


class MediaIORecord(ApiModel):
    io_request_id: EntityId
    organization_id: EntityId = "org_default"
    task_id: EntityId | None = None
    media_id: EntityId | None = None
    operation: str
    direction: str
    provider_name: str
    status: str
    degraded_reason: str | None = None
    input_artifact_id: EntityId | None = None
    output_artifact_id: EntityId | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime | str
    updated_at: datetime | str


class MediaSpeechTranscript(ApiModel):
    transcript_id: EntityId
    io_request_id: EntityId
    organization_id: EntityId = "org_default"
    task_id: EntityId
    media_id: EntityId
    artifact_id: EntityId | None = None
    provider_name: str
    language: str | None = None
    status: str
    transcript_preview: str = ""
    summary_text: str = ""
    confidence: float = 0
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | str


class MediaSpeechRender(ApiModel):
    render_id: EntityId
    io_request_id: EntityId
    organization_id: EntityId = "org_default"
    task_id: EntityId
    media_id: EntityId | None = None
    artifact_id: EntityId | None = None
    provider_name: str
    voice: str | None = None
    output_format: str = "wav"
    status: str
    source_text_hash: str
    duration_ms: int | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | str


class MediaMultimodalSummary(ApiModel):
    summary_id: EntityId
    io_request_id: EntityId
    organization_id: EntityId = "org_default"
    task_id: EntityId
    media_id: EntityId
    provider_name: str
    summary_type: str
    status: str
    summary_text: str
    summary: dict[str, Any] = Field(default_factory=dict)
    evidence_artifact_ids: list[EntityId] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | str


class MediaChatBinding(ApiModel):
    binding_id: EntityId
    organization_id: EntityId = "org_default"
    media_id: EntityId | None = None
    io_request_id: EntityId | None = None
    channel: str | None = None
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    message_id: EntityId | None = None
    channel_event_id: EntityId | None = None
    channel_attachment_id: EntityId | None = None
    binding_type: str
    status: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | str
