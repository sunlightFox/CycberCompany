from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import RiskLevel


class CapabilitySubject(ApiModel):
    subject_type: str
    subject_id: EntityId


class CapabilityObject(ApiModel):
    object_type: str
    object_id: EntityId


class CapabilityRequest(ApiModel):
    subject: CapabilitySubject
    object: CapabilityObject
    action: str
    context: dict[str, Any] = Field(default_factory=dict)


class CapabilityDecision(ApiModel):
    allowed: bool
    risk_level: RiskLevel
    approval_required: bool
    reason: str
    policy_sources: list[str] = Field(default_factory=list)
    decision_id: EntityId | None = None
    blocked_actions: list[str] = Field(default_factory=list)


class CapabilityEdge(ApiModel):
    edge_id: EntityId
    organization_id: EntityId
    subject_type: str
    subject_id: EntityId
    object_type: str
    object_id: EntityId
    action: str
    effect: str
    risk_level: RiskLevel
    approval_policy: dict[str, Any] = Field(default_factory=dict)
    condition: dict[str, Any] = Field(default_factory=dict)
    source_type: str
    source_id: EntityId | None = None
    priority: int = 0
    status: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CapabilityDecisionLog(ApiModel):
    decision_id: EntityId
    organization_id: EntityId
    trace_id: EntityId | None = None
    subject_type: str
    subject_id: EntityId
    object_type: str
    object_id: EntityId
    action: str
    context_hash: str
    decision: str
    risk_level: RiskLevel
    approval_required: bool
    reason: str
    policy_sources: list[str] = Field(default_factory=list)
    created_at: datetime
