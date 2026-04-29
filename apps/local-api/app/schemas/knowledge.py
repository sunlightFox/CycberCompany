from __future__ import annotations

from core_types import (
    ApiModel,
    EntityId,
    KnowledgeSearchResponse,
    KnowledgeSource,
)
from pydantic import Field


class KnowledgeSourceCreateRequest(ApiModel):
    asset_id: EntityId
    source_type: str
    source_uri: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    sensitivity: str = "low"
    metadata: dict[str, str] = Field(default_factory=dict)


class KnowledgeIndexResponse(ApiModel):
    source: KnowledgeSource
    chunk_count: int
    status: str


class KnowledgeSearchRequest(ApiModel):
    subject_type: str = "member"
    subject_id: EntityId = "mem_xiaoyao"
    asset_id: EntityId | None = None
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None


class KnowledgeAccessLogItem(ApiModel):
    access_id: EntityId
    organization_id: EntityId
    asset_id: EntityId
    source_id: EntityId | None = None
    subject_type: str
    subject_id: EntityId
    action: str
    decision_id: EntityId | None = None
    trace_id: EntityId | None = None
    query_hash: str | None = None
    selected_chunk_ids: list[EntityId] = Field(default_factory=list)
    filtered_chunk_ids: list[EntityId] = Field(default_factory=list)
    reason: str | None = None
    created_at: str


class KnowledgeAccessLogListResponse(ApiModel):
    items: list[KnowledgeAccessLogItem] = Field(default_factory=list)


class KnowledgeSearchApiResponse(KnowledgeSearchResponse):
    pass
