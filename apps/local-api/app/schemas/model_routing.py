from __future__ import annotations

from typing import Any

from core_types import ApiModel, ModelRouteDecision
from pydantic import Field


class ModelRoutingResponse(ApiModel):
    config: dict[str, Any]


class ModelRoutingUpdateRequest(ApiModel):
    config: dict[str, Any] = Field(default_factory=dict)


class ModelRoutingPreviewRequest(ApiModel):
    member_id: str
    text: str
    conversation_id: str | None = None
    privacy_level: str | None = None


class ModelRoutingPreviewResponse(ApiModel):
    intent: str
    mode: str
    route: ModelRouteDecision | None = None
    reason_codes: list[str]
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
