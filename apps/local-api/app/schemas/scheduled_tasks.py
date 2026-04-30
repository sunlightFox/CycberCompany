from __future__ import annotations

from datetime import datetime
from typing import Any

from core_types import ApiModel, EntityId, ScheduledTask, ScheduledTaskEvent, ScheduledTaskRun
from pydantic import Field


class ScheduledTaskCreateRequest(ApiModel):
    conversation_id: EntityId | None = None
    owner_member_id: EntityId = "mem_xiaoyao"
    title: str | None = None
    goal: str = Field(min_length=1)
    schedule: dict[str, Any]
    execution_policy: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    created_by_member_id: EntityId | None = "user_local_owner"


class ScheduledTaskUpdateRequest(ApiModel):
    title: str | None = None
    goal: str | None = None
    schedule: dict[str, Any] | None = None
    execution_policy: dict[str, Any] | None = None
    constraints: dict[str, Any] | None = None
    next_run_at: datetime | None = None
    max_consecutive_failures: int | None = Field(default=None, ge=1, le=20)


class ScheduledTaskActionRequest(ApiModel):
    reason: str | None = None


class ScheduledTaskTriggerRequest(ApiModel):
    reason: str | None = None
    scheduled_for: datetime | None = None


class ScheduledTaskResponse(ScheduledTask):
    pass


class ScheduledTaskListResponse(ApiModel):
    items: list[ScheduledTask] = Field(default_factory=list)


class ScheduledTaskRunResponse(ScheduledTaskRun):
    task_replay_ref: dict[str, Any] | None = None


class ScheduledTaskRunListResponse(ApiModel):
    items: list[ScheduledTaskRun] = Field(default_factory=list)


class ScheduledTaskEventListResponse(ApiModel):
    items: list[ScheduledTaskEvent] = Field(default_factory=list)
