from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import MemoryLayer


class MemorySource(ApiModel):
    type: str
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    task_id: EntityId | None = None
    step_id: EntityId | None = None
    message_id: EntityId | None = None
    channel_event_id: EntityId | None = None
    channel_attachment_id: EntityId | None = None
    media_id: EntityId | None = None
    artifact_id: EntityId | None = None
    media_io_request_id: EntityId | None = None
    attachment_type: str | None = None
    trace_id: EntityId | None = None


class MemoryItem(ApiModel):
    memory_id: EntityId
    organization_id: EntityId
    member_id: EntityId | None = None
    user_id: EntityId
    layer: MemoryLayer
    kind: str
    scope_type: str = "member"
    scope_id: EntityId | None = None
    summary_text: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source: MemorySource
    confidence: float
    importance: float = 0.5
    sensitivity: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes: EntityId | None = None
    status: str
    last_accessed_at: datetime | None = None
    access_count: int = 0
    quality_score: float = 0.5
    quality_breakdown: dict[str, Any] = Field(default_factory=dict)
    version_index: int = 1
    conflict_group_id: EntityId | None = None
    conflict_status: str = "clear"
    reuse_score: float = 0
    reuse_count: int = 0
    last_reused_at: datetime | None = None
    retention_policy: str = "standard"
    retention_reason: str | None = None
    expires_reason: str | None = None
    review_required: bool = False
    embedding_status: str = "pending"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemoryCandidate(ApiModel):
    candidate_id: EntityId
    organization_id: EntityId
    member_id: EntityId | None = None
    user_id: EntityId
    source: MemorySource
    proposed_layer: MemoryLayer
    proposed_kind: str
    proposed_scope_type: str
    proposed_scope_id: EntityId | None = None
    summary_text: str
    payload: dict[str, Any] = Field(default_factory=dict)
    score: dict[str, Any] = Field(default_factory=dict)
    final_score: float
    sensitivity: str
    decision: str
    decision_reason: str | None = None
    decided_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemoryExperienceRecord(ApiModel):
    experience_id: EntityId
    organization_id: EntityId
    member_id: EntityId | None = None
    task_id: EntityId | None = None
    conversation_id: EntityId | None = None
    memory_id: EntityId | None = None
    conflict_group_id: EntityId | None = None
    layer: MemoryLayer
    kind: str
    outcome: str
    summary_text: str
    source: MemorySource
    evidence: dict[str, Any] = Field(default_factory=dict)
    score: dict[str, Any] = Field(default_factory=dict)
    confidence_score: float = 0
    reuse_score: float = 0
    decision: str
    status: str = "recorded"
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemoryConflictRecord(ApiModel):
    conflict_id: EntityId
    organization_id: EntityId
    member_id: EntityId | None = None
    memory_id: EntityId | None = None
    related_memory_id: EntityId | None = None
    candidate_id: EntityId | None = None
    conflict_group_id: EntityId
    conflict_type: str
    status: str
    resolution: str | None = None
    summary_text: str
    source: MemorySource = Field(default_factory=lambda: MemorySource(type="unknown"))
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemoryReuseFeedback(ApiModel):
    feedback_id: EntityId
    organization_id: EntityId
    member_id: EntityId | None = None
    retrieval_id: EntityId
    memory_id: EntityId
    task_id: EntityId | None = None
    feedback_type: str
    rating: float = 0
    source: MemorySource = Field(default_factory=lambda: MemorySource(type="unknown"))
    evidence: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MemorySearchRequest(ApiModel):
    member_id: EntityId
    query: str
    conversation_id: EntityId | None = None
    intent: str | None = None
    layers: list[MemoryLayer] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=50)
    include_archived: bool = False
    include_sensitive: bool = False
    include_asset_scoped: bool = False
    asset_scope_ids: list[EntityId] = Field(default_factory=list)


class MemorySearchHit(ApiModel):
    memory_id: EntityId
    layer: MemoryLayer
    kind: str
    summary_text: str
    score: float
    confidence: float
    importance: float
    sensitivity: str = "low"
    validity: str = "current"
    embedding_status: str = "pending"
    quality_score: float = 0.5
    quality_breakdown: dict[str, Any] = Field(default_factory=dict)
    version_index: int = 1
    conflict_group_id: EntityId | None = None
    conflict_status: str = "clear"
    reuse_score: float = 0
    reuse_count: int = 0
    retrieval_source: str = "fts_fallback"
    selection_reason: list[str] = Field(default_factory=list)
    provider: str | None = None
    embedding_model: str | None = None
    fallback_chain: list[str] = Field(default_factory=list)
    degraded_reason: str | None = None
    rerank_score: float | None = None
    selection_confidence: float | None = None
    conflict_notes: list[str] = Field(default_factory=list)
    suppressed_reason: str | None = None
    requires_user_confirmation: bool = False
    source: MemorySource


class MemorySearchFilteredItem(ApiModel):
    memory_id: EntityId
    reason: str


class MemorySearchRankingItem(ApiModel):
    memory_id: EntityId
    score: float
    reason_codes: list[str] = Field(default_factory=list)


class MemorySearchResponse(ApiModel):
    retrieval_id: EntityId
    items: list[MemorySearchHit] = Field(default_factory=list)
    selected_memory_ids: list[EntityId] = Field(default_factory=list)
    filtered: list[MemorySearchFilteredItem] = Field(default_factory=list)
    ranking: list[MemorySearchRankingItem] = Field(default_factory=list)
    degraded: bool = False
    provider: str | None = None
    degraded_reason: str | None = None
