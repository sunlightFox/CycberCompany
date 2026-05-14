from __future__ import annotations

from typing import Any

from core_types import (
    AgentLoopFrame,
    AgentLoopState,
    AgentLoopIteration,
    AgentNextActionDecision,
    ApiModel,
    ApprovalDetail,
    CollaborationReplay,
    CollaborationContextBoundary,
    CollaborationHandoffRecord,
    CollaborationRoutingDecision,
    EntityId,
    HostDecision,
    MemberAvailability,
    PlanCandidate,
    PlannerCapabilityCandidate,
    PlannerDecision,
    PlanPolicyPrune,
    PlanVerificationResult,
    RiskLevel,
    SkillPolicy,
    OfficeTaskRequest,
    TaskArtifact,
    TaskDetail,
    TaskEvent,
    TaskMode,
    TaskObservation,
    TaskParticipant,
    TaskReflectionCandidate,
    TaskReplay,
    TaskRetryPlan,
    TaskSubtask,
    TaskSummary,
    TerminalSandboxProfile,
    ToolActionPolicy,
    ToolCallRecord,
    ToolDefinition,
    ToolFailureRecoveryPlan,
    ToolOutputDlpReport,
    ToolPolicyDecision,
)
from pydantic import Field


class TaskCreateRequest(ApiModel):
    conversation_id: EntityId | None = None
    owner_member_id: EntityId = "mem_xiaoyao"
    goal: str = Field(min_length=1)
    domain: str | None = None
    domain_request: dict[str, Any] = Field(default_factory=dict)
    mode_hint: TaskMode | None = None
    success_criteria: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    resource_handle_ids: list[EntityId] = Field(default_factory=list)
    budget_override: dict[str, int] = Field(default_factory=dict)
    brain_decision_id: EntityId | None = None
    planner_context: dict[str, Any] = Field(default_factory=dict)
    office_request: OfficeTaskRequest | None = None
    client_request_id: str | None = None
    auto_start: bool = True


class TaskListResponse(ApiModel):
    items: list[TaskSummary] = Field(default_factory=list)


class TaskEventListResponse(ApiModel):
    items: list[TaskEvent] = Field(default_factory=list)


class TaskArtifactListResponse(ApiModel):
    items: list[TaskArtifact] = Field(default_factory=list)


class TaskActionRequest(ApiModel):
    reason: str | None = None


class ApprovalDecisionRequest(ApiModel):
    actor_type: str = "user"
    actor_id: EntityId | None = "user_local_owner"
    reason: str | None = None
    edited_payload: dict[str, Any] | None = None


class ToolListResponse(ApiModel):
    items: list[ToolDefinition] = Field(default_factory=list)


class ToolActionPolicyListResponse(ApiModel):
    items: list[ToolActionPolicy] = Field(default_factory=list)


class ToolBoundaryResponse(ApiModel):
    tool_call: ToolCallRecord | None = None
    decisions: list[ToolPolicyDecision] = Field(default_factory=list)
    sandbox_profile: TerminalSandboxProfile | None = None


class ToolDlpReportListResponse(ApiModel):
    items: list[ToolOutputDlpReport] = Field(default_factory=list)


class ToolExecuteRequest(ApiModel):
    task_id: EntityId | None = None
    step_id: EntityId | None = None
    turn_id: EntityId | None = None
    conversation_id: EntityId | None = None
    session_id: EntityId | None = None
    channel: str | None = None
    member_id: EntityId = "mem_xiaoyao"
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    approval_id: EntityId | None = None


class ToolExecuteResponse(ApiModel):
    tool_call: ToolCallRecord
    approval: ApprovalDetail | None = None
    artifacts: list[TaskArtifact] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class ArtifactReadResponse(ApiModel):
    artifact: TaskArtifact
    content_preview: str | None = None


class TaskDetailResponse(TaskDetail):
    pass


class TaskReplayResponse(TaskReplay):
    pass


class TaskPlanResponse(ApiModel):
    plan: dict[str, Any]


class PlannerDecisionListResponse(ApiModel):
    items: list[PlannerDecision] = Field(default_factory=list)


class AgentLoopListResponse(ApiModel):
    runtime: str = "task_agent_runtime"
    authoritative: bool = True
    task_id: EntityId | None = None
    current_status: str | None = None
    pause_reason: str | None = None
    stop_reason: str | None = None
    items: list[AgentLoopFrame] = Field(default_factory=list)


class TaskObservationListResponse(ApiModel):
    runtime: str = "task_agent_runtime"
    authoritative: bool = True
    task_id: EntityId | None = None
    items: list[TaskObservation] = Field(default_factory=list)


class TaskReflectionCandidateListResponse(ApiModel):
    items: list[TaskReflectionCandidate] = Field(default_factory=list)


class TaskRetryPlanListResponse(ApiModel):
    items: list[TaskRetryPlan] = Field(default_factory=list)


class PlanCandidateListResponse(ApiModel):
    items: list[PlanCandidate] = Field(default_factory=list)


class PlanVerificationResultListResponse(ApiModel):
    items: list[PlanVerificationResult] = Field(default_factory=list)


class PlanPolicyPruneListResponse(ApiModel):
    items: list[PlanPolicyPrune] = Field(default_factory=list)


class PlannerCapabilityCandidateListResponse(ApiModel):
    items: list[PlannerCapabilityCandidate] = Field(default_factory=list)


class AgentNextActionDecisionListResponse(ApiModel):
    runtime: str = "task_agent_runtime"
    authoritative: bool = True
    task_id: EntityId | None = None
    items: list[AgentNextActionDecision] = Field(default_factory=list)


class ToolFailureRecoveryPlanListResponse(ApiModel):
    items: list[ToolFailureRecoveryPlan] = Field(default_factory=list)


class CollaborationReplayResponse(CollaborationReplay):
    pass


class CollaborationRoutePreviewRequest(ApiModel):
    host_member_id: EntityId | None = None
    mode: str | None = None
    resource_handle_ids: list[EntityId] = Field(default_factory=list)


class CollaborationRoutePreviewResponse(ApiModel):
    routing_decision: CollaborationRoutingDecision
    selected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    context_boundaries: list[CollaborationContextBoundary] = Field(default_factory=list)


class CollaborationRoutingDecisionListResponse(ApiModel):
    items: list[CollaborationRoutingDecision] = Field(default_factory=list)


class CollaborationHandoffRecordListResponse(ApiModel):
    items: list[CollaborationHandoffRecord] = Field(default_factory=list)


class CollaborationContextBoundaryListResponse(ApiModel):
    items: list[CollaborationContextBoundary] = Field(default_factory=list)


class TaskParticipantListResponse(ApiModel):
    items: list[TaskParticipant] = Field(default_factory=list)


class TaskSubtaskListResponse(ApiModel):
    items: list[TaskSubtask] = Field(default_factory=list)


class TaskParticipantResponse(TaskParticipant):
    pass


class TaskSubtaskResponse(TaskSubtask):
    pass


class HostDecisionListResponse(ApiModel):
    items: list[HostDecision] = Field(default_factory=list)


class ParticipantRemoveRequest(ApiModel):
    reason: str = "removed_by_user"


class CollaborationHandoffRequest(ApiModel):
    to_member_id: EntityId
    reason: str = "task_handoff"


class SubtaskActionRequest(ApiModel):
    reason: str | None = None


class MemberAvailabilityUpdateRequest(ApiModel):
    status: str = "available"
    capacity: int = Field(default=1, ge=0, le=10)
    current_load: int = Field(default=0, ge=0, le=10)
    unavailable_reason: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)


class MemberAvailabilityResponse(MemberAvailability):
    pass


class SkillPolicyUpdateRequest(ApiModel):
    allowed_skills: list[str] = Field(default_factory=list)
    denied_skills: list[str] = Field(default_factory=list)
    allowed_mcp_tools: list[str] = Field(default_factory=list)
    denied_mcp_tools: list[str] = Field(default_factory=list)
    risk_policy: dict[str, Any] = Field(default_factory=dict)


class SkillPolicyResponse(SkillPolicy):
    pass


class ApprovalDetailResponse(ApprovalDetail):
    pass


class TaskRiskPreview(ApiModel):
    risk_level: RiskLevel
    approval_required: bool
    reason: str
