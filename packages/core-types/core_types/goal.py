from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class Goal(ApiModel):
    goal_id: EntityId
    organization_id: EntityId
    owner_member_id: EntityId
    conversation_id: EntityId | None = None
    title: str
    description: str
    domain_label: str = "general"
    status: str
    success_criteria: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    motivation: dict[str, Any] = Field(default_factory=dict)
    active_plan_id: EntityId | None = None
    created_from_turn_id: EntityId | None = None
    trace_id: EntityId | None = None
    archived_at: datetime | None = None
    cancelled_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class GoalPlan(ApiModel):
    goal_plan_id: EntityId
    goal_id: EntityId
    version: int = 1
    status: str
    summary: str
    assumptions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class GoalPlanItem(ApiModel):
    goal_plan_item_id: EntityId
    goal_plan_id: EntityId
    goal_id: EntityId
    title: str
    description: str = ""
    item_type: str = "routine"
    cadence: dict[str, Any] = Field(default_factory=dict)
    success_metric: dict[str, Any] = Field(default_factory=dict)
    status: str = "planned"
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime


class GoalSupervisionPolicy(ApiModel):
    policy_id: EntityId
    goal_id: EntityId
    status: str
    mode: str = "scheduled_checkin"
    frequency: dict[str, Any] = Field(default_factory=dict)
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    tone_policy: dict[str, Any] = Field(default_factory=dict)
    next_checkin_at: datetime | None = None
    scheduled_task_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class GoalCheckin(ApiModel):
    checkin_id: EntityId
    goal_id: EntityId
    policy_id: EntityId | None = None
    scheduled_task_id: EntityId | None = None
    scheduled_run_id: EntityId | None = None
    prompt_text: str
    user_reply_text_redacted: str | None = None
    parsed_status: str = "pending"
    progress_delta: dict[str, Any] = Field(default_factory=dict)
    advice: dict[str, Any] = Field(default_factory=dict)
    encouragement_text: str = ""
    trace_id: EntityId | None = None
    created_at: datetime
    replied_at: datetime | None = None


class GoalProgressSnapshot(ApiModel):
    snapshot_id: EntityId
    goal_id: EntityId
    progress_percent: int = 0
    completed_count: int = 0
    partial_count: int = 0
    missed_count: int = 0
    blocked_count: int = 0
    streak_days: int = 0
    summary: str
    blockers: list[str] = Field(default_factory=list)
    next_focus: list[str] = Field(default_factory=list)
    source_checkin_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime


class GoalEvent(ApiModel):
    event_id: EntityId
    goal_id: EntityId
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime
