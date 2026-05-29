from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    Goal,
    GoalCheckin,
    GoalEvent,
    GoalIntake,
    GoalIntervention,
    GoalMilestone,
    GoalModelCall,
    GoalPlan,
    GoalPlanItem,
    GoalProgressSnapshot,
    GoalRoutine,
    GoalSupervisionPolicy,
)
from pydantic import Field


class GoalCreateRequest(ApiModel):
    conversation_id: EntityId | None = None
    owner_member_id: EntityId = "mem_xiaoyao"
    title: str | None = None
    description: str = Field(min_length=1)
    domain_label: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    motivation: dict[str, Any] = Field(default_factory=dict)
    created_from_turn_id: EntityId | None = None
    intake: dict[str, Any] = Field(default_factory=dict)
    preferred_domain: str | None = None
    planning_mode: str = "model_first"


class GoalConfirmPlanRequest(ApiModel):
    start_supervision: bool = False
    supervision: dict[str, Any] = Field(default_factory=dict)


class GoalSupervisionRequest(ApiModel):
    schedule: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "daily",
            "time": "20:00",
            "timezone": "Asia/Shanghai",
        }
    )
    mode: str = "scheduled_checkin"
    quiet_hours: dict[str, Any] = Field(default_factory=dict)
    tone_policy: dict[str, Any] = Field(default_factory=dict)
    random_jitter_minutes: int = Field(default=0, ge=0, le=720)


class GoalCheckinCreateRequest(ApiModel):
    prompt_text: str | None = None
    policy_id: EntityId | None = None
    scheduled_task_id: EntityId | None = None
    scheduled_run_id: EntityId | None = None


class GoalCheckinReplyRequest(ApiModel):
    reply_text: str = Field(min_length=1)


class GoalActionRequest(ApiModel):
    reason: str | None = None


class GoalIntakeUpdateRequest(ApiModel):
    current_level: str | None = None
    target_level: str | None = None
    target_date: str | None = None
    available_time: dict[str, Any] = Field(default_factory=dict)
    constraints: dict[str, Any] = Field(default_factory=dict)
    motivation: dict[str, Any] = Field(default_factory=dict)
    raw_answers: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False


class GoalReplanRequest(ApiModel):
    reason: str | None = None
    feedback: str | None = None
    planning_mode: str = "model_first"


class GoalDetailResponse(ApiModel):
    goal: Goal
    active_plan: GoalPlan | None = None
    plan_items: list[GoalPlanItem] = Field(default_factory=list)
    supervision_policy: GoalSupervisionPolicy | None = None
    progress: GoalProgressSnapshot | None = None
    intake: GoalIntake | None = None
    milestones: list[GoalMilestone] = Field(default_factory=list)
    routines: list[GoalRoutine] = Field(default_factory=list)
    latest_intervention: GoalIntervention | None = None


class GoalListResponse(ApiModel):
    items: list[Goal] = Field(default_factory=list)


class GoalResponse(Goal):
    pass


class GoalSupervisionResponse(ApiModel):
    policy: GoalSupervisionPolicy
    scheduled_task_id: EntityId | None = None


class GoalCheckinResponse(GoalCheckin):
    pass


class GoalCheckinListResponse(ApiModel):
    items: list[GoalCheckin] = Field(default_factory=list)


class GoalProgressResponse(GoalProgressSnapshot):
    pass


class GoalEventListResponse(ApiModel):
    items: list[GoalEvent] = Field(default_factory=list)


class GoalTimelineResponse(ApiModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class GoalModelCallListResponse(ApiModel):
    items: list[GoalModelCall] = Field(default_factory=list)
