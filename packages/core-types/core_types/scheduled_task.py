from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class ScheduledTask(ApiModel):
    scheduled_task_id: EntityId
    organization_id: EntityId
    conversation_id: EntityId | None = None
    owner_member_id: EntityId
    title: str
    goal: str
    status: str
    schedule: dict[str, Any] = Field(default_factory=dict)
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    consecutive_failure_count: int = 0
    max_consecutive_failures: int = 3
    dead_letter_reason: str | None = None
    created_by_member_id: EntityId | None = None
    trace_id: EntityId | None = None
    archived_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScheduledTaskRun(ApiModel):
    run_id: EntityId
    scheduled_task_id: EntityId
    organization_id: EntityId
    task_id: EntityId | None = None
    trace_id: EntityId | None = None
    trigger_type: str
    idempotency_key: str
    scheduled_for: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: str
    failure_reason: str | None = None
    missed_reason: str | None = None
    policy_decision: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ScheduledTaskEvent(ApiModel):
    event_id: EntityId
    scheduled_task_id: EntityId
    organization_id: EntityId
    run_id: EntityId | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime
