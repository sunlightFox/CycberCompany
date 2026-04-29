from __future__ import annotations

from core_types import ApiModel, CapabilityDecision, CapabilityEdge
from pydantic import Field


class CapabilityDecisionResponse(CapabilityDecision):
    pass


class CapabilityGrantListResponse(ApiModel):
    items: list[CapabilityEdge] = Field(default_factory=list)
