from __future__ import annotations

from typing import Any

from core_types import ApiModel, EntityId
from pydantic import Field


class RetrievalDiagnosticsResponse(ApiModel):
    retrieval_id: EntityId
    target_type: str
    log: dict[str, Any] = Field(default_factory=dict)
    rerank_runs: list[dict[str, Any]] = Field(default_factory=list)
    suppressed_items: list[dict[str, Any]] = Field(default_factory=list)
    quality_reports: list[dict[str, Any]] = Field(default_factory=list)
