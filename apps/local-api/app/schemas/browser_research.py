from __future__ import annotations

from typing import Any

from core_types import ApiModel
from pydantic import Field


class BrowserResearchPlan(ApiModel):
    query: str
    citation_required: bool = False
    requested_sections: list[str] = Field(default_factory=list)
    presentation_style: str = "default"


class RawEvidence(ApiModel):
    snippet: str
    source_url: str | None = None
    source_title: str | None = None
    source_type: str = "browser_search_result"


class EvidenceAssessment(ApiModel):
    freshness: str
    source_rank: str
    conflict_level: str
    confidence: str
    notes: list[str] = Field(default_factory=list)


class SearchRequest(ApiModel):
    member_id: str
    turn_id: str
    trace_id: str | None = None
    query: str


class SearchResult(ApiModel):
    title: str
    url: str = ""
    http_status: int | str | None = None
    content_preview: str = ""
    raw_result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    raw_evidence: list[RawEvidence] = Field(default_factory=list)
    tool_name: str = "browser.search"
    tool_call_id: str | None = None


class CapabilityExecutionResult(ApiModel):
    capability_name: str
    plan: dict[str, Any] = Field(default_factory=dict)
    authorize: dict[str, Any] = Field(default_factory=dict)
    execute: dict[str, Any] = Field(default_factory=dict)
    summarize: dict[str, Any] = Field(default_factory=dict)
    emit_evidence: dict[str, Any] = Field(default_factory=dict)
