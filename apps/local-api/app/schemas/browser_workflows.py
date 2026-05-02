from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    BrowserWorkflowCandidate,
    BrowserWorkflowDiscoveryResult,
    BrowserWorkflowEvent,
    BrowserWorkflowExecution,
    BrowserWorkflowIntent,
    BrowserWorkflowPlan,
    BrowserWorkflowStep,
    EntityId,
)
from pydantic import Field


class BrowserWorkflowIntentResolveRequest(ApiModel):
    text: str = Field(min_length=1)
    member_id: EntityId = "mem_xiaoyao"
    organization_id: EntityId = "org_default"
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    target_url: str | None = None
    action_type: str | None = None
    content_summary: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)


class BrowserWorkflowIntentResolveResponse(ApiModel):
    intent: BrowserWorkflowIntent
    message: str
    next_step: str | None = None


class BrowserWorkflowPlanCreateRequest(ApiModel):
    intent_id: EntityId
    target_url: str | None = None
    action_type: str | None = None
    goal: str | None = None
    content_summary: str | None = None
    form_data: dict[str, Any] = Field(default_factory=dict)
    file_refs: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = Field(default=8, ge=1, le=16)


class BrowserWorkflowExecuteRequest(ApiModel):
    force_discovery: bool = False
    max_steps: int = Field(default=8, ge=1, le=16)
    approval_id: EntityId | None = None
    dry_run: bool = False
    provider_mode: str = "auto"
    viewport_profile: str = "desktop"
    action_strategy: str = "css"
    wait_until: str | None = None


class BrowserWorkflowResumeRequest(ApiModel):
    approval_id: EntityId | None = None
    human_resolution: dict[str, Any] = Field(default_factory=dict)
    provider_mode: str = "auto"
    viewport_profile: str = "desktop"
    action_strategy: str = "css"


class BrowserWorkflowPlanResponse(ApiModel):
    plan: BrowserWorkflowPlan
    execution: BrowserWorkflowExecution | None = None
    discovery: BrowserWorkflowDiscoveryResult | None = None
    steps: list[BrowserWorkflowStep] = Field(default_factory=list)
    candidate: BrowserWorkflowCandidate | None = None
    message: str
    next_step: str | None = None


class BrowserWorkflowReplayResponse(ApiModel):
    plan: BrowserWorkflowPlan
    executions: list[BrowserWorkflowExecution] = Field(default_factory=list)
    events: list[BrowserWorkflowEvent] = Field(default_factory=list)
    candidates: list[BrowserWorkflowCandidate] = Field(default_factory=list)
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
