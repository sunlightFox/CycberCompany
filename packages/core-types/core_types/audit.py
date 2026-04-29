from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import RiskLevel


class AuditEventResponse(ApiModel):
    audit_id: EntityId
    actor_type: str
    actor_id: EntityId | None = None
    action: str
    object_type: str
    object_id: EntityId | None = None
    risk_level: RiskLevel
    summary: str
    payload_redacted: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime


class AuditEventListResponse(ApiModel):
    items: list[AuditEventResponse] = Field(default_factory=list)

