from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    CheckpointItem,
    EntityId,
    RollbackEvent,
    RollbackItem,
    TaskCheckpoint,
)
from pydantic import Field


class CheckpointCreateRequest(ApiModel):
    step_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    checkpoint_type: str = "manual"
    scope: str = "task_artifacts"
    paths: list[str] = Field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CheckpointListResponse(ApiModel):
    items: list[TaskCheckpoint] = Field(default_factory=list)


class CheckpointDetailResponse(TaskCheckpoint):
    items: list[CheckpointItem] = Field(default_factory=list)


class CheckpointItemsResponse(ApiModel):
    items: list[CheckpointItem] = Field(default_factory=list)


class RollbackRequest(ApiModel):
    requested_by: str = "user_local_owner"
    reason: str | None = None


class RollbackResponse(ApiModel):
    event: RollbackEvent
    items: list[RollbackItem] = Field(default_factory=list)


class RollbackEventListResponse(ApiModel):
    items: list[RollbackEvent] = Field(default_factory=list)
