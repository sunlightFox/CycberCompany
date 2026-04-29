from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import RiskLevel, TraceSpanStatus, TraceSpanType, TraceStatus


class TraceSpan(ApiModel):
    span_id: EntityId
    trace_id: EntityId
    parent_span_id: EntityId | None = None
    span_type: TraceSpanType | str
    name: str
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    ended_at: datetime | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    status: TraceSpanStatus


class Trace(ApiModel):
    trace_id: EntityId
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    task_id: EntityId | None = None
    root_span_id: EntityId | None = None
    status: TraceStatus
    started_at: datetime
    ended_at: datetime | None = None
    spans: list[TraceSpan] = Field(default_factory=list)


class AuditEvent(ApiModel):
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
