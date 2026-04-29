from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    MemoryCandidate,
    MemoryItem,
    MemoryLayer,
    MemorySearchHit,
    MemorySearchResponse,
)
from pydantic import Field


class MemoryListResponse(ApiModel):
    items: list[MemoryItem] = Field(default_factory=list)


class MemoryUpdateRequest(ApiModel):
    summary_text: str | None = Field(default=None, min_length=1)
    payload: dict[str, Any] | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    review_required: bool | None = None
    metadata: dict[str, Any] | None = None


class MemorySearchApiRequest(ApiModel):
    query: str = Field(min_length=1)
    member_id: EntityId | None = None
    conversation_id: EntityId | None = None
    intent: str | None = None
    layers: list[MemoryLayer] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=50)
    include_archived: bool = False
    include_sensitive: bool = False
    include_asset_scoped: bool = False
    asset_scope_ids: list[EntityId] = Field(default_factory=list)


class MemorySearchApiResponse(MemorySearchResponse):
    items: list[MemorySearchHit] = Field(default_factory=list)


class MemoryExtractRequest(ApiModel):
    text: str | None = Field(default=None, min_length=1)
    turn_id: EntityId | None = None
    member_id: EntityId | None = None
    conversation_id: EntityId | None = None
    trace_id: EntityId | None = None


class MemoryExtractResponse(ApiModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)
    memories: list[MemoryItem] = Field(default_factory=list)
    blocked: bool = False
    reason: str | None = None


class MemoryCandidateListResponse(ApiModel):
    items: list[MemoryCandidate] = Field(default_factory=list)


class MemoryJobItem(ApiModel):
    job_id: EntityId
    organization_id: EntityId
    turn_id: EntityId | None = None
    idempotency_key: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int = 3
    next_run_at: str | None = None
    locked_by: str | None = None
    locked_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    completed_at: str | None = None


class MemoryJobListResponse(ApiModel):
    items: list[MemoryJobItem] = Field(default_factory=list)


class MemoryCandidateDecisionResponse(ApiModel):
    candidate: MemoryCandidate
    memory: MemoryItem | None = None


class MemoryRelationItem(ApiModel):
    relation_id: EntityId
    organization_id: EntityId
    source_memory_id: EntityId
    target_memory_id: EntityId
    relation_type: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class MemoryRelationsResponse(ApiModel):
    items: list[MemoryRelationItem] = Field(default_factory=list)


class MemorySourceMessage(ApiModel):
    message_id: EntityId
    conversation_id: EntityId
    turn_id: EntityId | None = None
    author_type: str
    author_id: EntityId | None = None
    content_type: str
    content_text: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: str


class MemorySourceResponse(ApiModel):
    memory_id: EntityId
    source: dict[str, Any]
    source_message: MemorySourceMessage | None = None
    trace_id: EntityId | None = None


class MemoryDeleteResponse(ApiModel):
    memory_id: EntityId
    status: str
