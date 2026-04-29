from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class KnowledgeSource(ApiModel):
    source_id: EntityId
    organization_id: EntityId
    asset_id: EntityId
    source_type: str
    source_uri: str
    display_name: str
    status: str
    sensitivity: str
    content_hash: str | None = None
    last_scanned_at: datetime | None = None
    last_indexed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KnowledgeChunk(ApiModel):
    chunk_id: EntityId
    organization_id: EntityId
    asset_id: EntityId
    source_id: EntityId
    chunk_index: int
    content_text: str
    summary_text: str | None = None
    token_estimate: int | None = None
    sensitivity: str
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KnowledgeSearchHit(ApiModel):
    chunk_id: EntityId
    asset_id: EntityId
    source_id: EntityId
    summary_text: str | None = None
    content_preview: str
    score: float
    sensitivity: str
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
    untrusted_external_content: bool = False
    source_ref: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSearchResponse(ApiModel):
    retrieval_id: EntityId | None = None
    items: list[KnowledgeSearchHit] = Field(default_factory=list)
    selected_chunk_ids: list[EntityId] = Field(default_factory=list)
    filtered_chunk_ids: list[EntityId] = Field(default_factory=list)
    access_id: EntityId | None = None
    degraded: bool = False
    provider: str | None = None
    degraded_reason: str | None = None
