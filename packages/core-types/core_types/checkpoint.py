from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class TaskCheckpoint(ApiModel):
    checkpoint_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    step_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    checkpoint_type: str
    scope: str
    status: str
    item_count: int = 0
    size_bytes: int = 0
    restorable: bool = True
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    expires_at: datetime | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CheckpointItem(ApiModel):
    checkpoint_item_id: EntityId
    checkpoint_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    target_uri: str
    target_path_redacted: str
    item_type: str
    exists_before: bool = False
    before_checksum: str | None = None
    before_size_bytes: int = 0
    after_exists: bool | None = None
    after_checksum: str | None = None
    after_size_bytes: int | None = None
    snapshot_artifact_id: EntityId | None = None
    snapshot_uri: str | None = None
    content_type: str | None = None
    sensitivity: str = "low"
    restorable: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RollbackEvent(ApiModel):
    rollback_id: EntityId
    organization_id: EntityId
    checkpoint_id: EntityId
    task_id: EntityId
    requested_by: str
    reason: str | None = None
    status: str
    restored_items: int = 0
    skipped_items: int = 0
    conflict_items: int = 0
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class RollbackItem(ApiModel):
    rollback_item_id: EntityId
    rollback_id: EntityId
    checkpoint_item_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    target_uri: str
    action: str
    status: str
    reason: str | None = None
    before_checksum: str | None = None
    current_checksum: str | None = None
    restored_checksum: str | None = None
    created_at: datetime | None = None
