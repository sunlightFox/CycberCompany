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
