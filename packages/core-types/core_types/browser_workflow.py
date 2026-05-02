from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class BrowserWorkflowIntent(ApiModel):
    intent_id: EntityId
    organization_id: EntityId = "org_default"
    member_id: EntityId = "mem_xiaoyao"
    conversation_id: EntityId | None = None
    turn_id: EntityId | None = None
    trace_id: EntityId | None = None
    natural_language_goal: str
    action_type: str
    target_url: str | None = None
    target_key: str | None = None
    content_summary: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    status: str = "resolved"
    confidence: float = 0
    resolver_evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BrowserWorkflowStep(ApiModel):
    step_id: EntityId
    plan_id: EntityId
    step_order: int = 0
    step_type: str
    tool_name: str | None = None
    selector: str | None = None
    label: str | None = None
    status: str = "planned"
    risk_level: str = "R1"
    requires_approval: bool = False
    input_redacted: dict[str, Any] = Field(default_factory=dict)
    output_redacted: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    approval_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BrowserWorkflowPlan(ApiModel):
    plan_id: EntityId
    intent_id: EntityId
    organization_id: EntityId = "org_default"
    member_id: EntityId = "mem_xiaoyao"
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None
    approval_id: EntityId | None = None
    trace_id: EntityId | None = None
    action_type: str
    target_url: str | None = None
    target_key: str | None = None
    goal: str
    status: str = "planned"
    risk_level: str = "R1"
    current_url: str | None = None
    content_summary: str | None = None
    form_data: dict[str, Any] = Field(default_factory=dict)
    file_refs: list[dict[str, Any]] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    approval_binding: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BrowserWorkflowDiscoveryResult(ApiModel):
    discovery_id: EntityId
    plan_id: EntityId
    action_type: str
    target_url: str | None = None
    status: str
    learned_workflow_manifest: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    failure_reason: str | None = None
    user_visible_message: str
    candidate_id: EntityId | None = None


class BrowserWorkflowExecution(ApiModel):
    execution_id: EntityId
    plan_id: EntityId
    organization_id: EntityId = "org_default"
    member_id: EntityId = "mem_xiaoyao"
    action_type: str
    status: str
    approval_id: EntityId | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    failure_reason: str | None = None
    user_visible_message: str | None = None
    trace_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BrowserWorkflowCandidate(ApiModel):
    candidate_id: EntityId
    organization_id: EntityId = "org_default"
    target_key: str | None = None
    host: str
    action_type: str
    status: str = "test_only"
    source: str = "autonomous_browser_workflow"
    manifest: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    confidence: float = 0
    recommended: bool = False
    last_plan_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BrowserWorkflowEvent(ApiModel):
    event_id: EntityId
    plan_id: EntityId
    organization_id: EntityId = "org_default"
    execution_id: EntityId | None = None
    event_type: str
    payload_redacted: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
