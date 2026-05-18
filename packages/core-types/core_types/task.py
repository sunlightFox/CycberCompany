from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import RiskLevel, TaskMode, TaskStatus


class TaskBudget(ApiModel):
    max_steps: int = 20
    max_loop_steps: int = 8
    max_tool_calls: int = 30
    max_runtime_seconds: int = 1800
    max_model_calls: int = 20
    max_total_cost: float = 0.0
    max_artifact_bytes: int = 10_000_000


class TaskProgress(ApiModel):
    total_steps: int = 0
    completed_steps: int = 0
    current_step_key: str | None = None


class TaskPlan(ApiModel):
    task_id: EntityId
    title: str
    goal: str
    domain: str | None = None
    mode: TaskMode
    owner_member_id: EntityId
    host_member_id: EntityId | None = None
    success_criteria: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_assets: list[EntityId] = Field(default_factory=list)
    approval_strategy: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    participants: list[dict[str, Any]] = Field(default_factory=list)
    collaboration: dict[str, Any] = Field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.R1
    budget: TaskBudget = Field(default_factory=TaskBudget)
    checkpoint_policy: dict[str, Any] = Field(default_factory=dict)
    failure_policy: dict[str, Any] = Field(default_factory=dict)
    reflection_policy: dict[str, Any] = Field(default_factory=dict)
    planner_type: str = "rule"
    planner_reason_codes: list[str] = Field(default_factory=list)
    preflight: dict[str, Any] = Field(default_factory=dict)
    artifact_plan: dict[str, Any] = Field(default_factory=dict)


class PlannerDecision(ApiModel):
    planner_decision_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    planner_type: str
    selected_mode: str
    reason_codes: list[str] = Field(default_factory=list)
    capability_snapshot: dict[str, Any] = Field(default_factory=dict)
    skill_match_refs: list[dict[str, Any]] = Field(default_factory=list)
    mcp_tool_refs: list[dict[str, Any]] = Field(default_factory=list)
    model_hint: dict[str, Any] = Field(default_factory=dict)
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class AgentLoopIteration(ApiModel):
    iteration_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    loop_index: int
    observation_id: EntityId | None = None
    observation_summary: str | None = None
    plan_delta: dict[str, Any] = Field(default_factory=dict)
    selected_action: dict[str, Any] = Field(default_factory=dict)
    tool_call_refs: list[dict[str, Any]] = Field(default_factory=list)
    safety_decision_refs: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_result: dict[str, Any] = Field(default_factory=dict)
    next_step_key: str | None = None
    stop_reason: str | None = None
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    status: str
    trace_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TaskObservation(ApiModel):
    observation_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    step_id: EntityId | None = None
    source_type: str
    source_ref: dict[str, Any] = Field(default_factory=dict)
    trusted_level: str
    summary: str
    key_facts: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    sensitivity: str = "low"
    untrusted_instructions_detected: bool = False
    payload_redacted: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class TaskRetryPlan(ApiModel):
    retry_plan_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    reason: str
    suggested_actions: list[str] = Field(default_factory=list)
    resumable_from_step_key: str | None = None
    budget_delta: dict[str, Any] = Field(default_factory=dict)
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class TaskReflectionCandidate(ApiModel):
    candidate_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    candidate_type: str
    status: str
    confidence: float = 0.0
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.R1
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class PlanCandidate(ApiModel):
    candidate_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    planner_type: str
    source: str
    recommended_mode: str
    steps: list[dict[str, Any]] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_hints: list[dict[str, Any]] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_assets: list[EntityId] = Field(default_factory=list)
    confidence: float = 0.0
    reasoning_summary: str
    status: str
    model_assist: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class PlanVerificationResult(ApiModel):
    verification_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    candidate_id: EntityId
    schema_valid: bool = False
    mode_allowed: bool = False
    step_type_allowed: bool = False
    capability_available: bool = False
    asset_handle_allowed: bool = False
    risk_level_acceptable: bool = False
    approval_strategy_present: bool = False
    budget_within_limit: bool = False
    no_direct_secret: bool = False
    no_direct_shell_command_from_model: bool = False
    issues: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class PlanPolicyPrune(ApiModel):
    prune_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    candidate_id: EntityId
    prune_type: str
    original_step: dict[str, Any] = Field(default_factory=dict)
    pruned_step: dict[str, Any] = Field(default_factory=dict)
    reason_codes: list[str] = Field(default_factory=list)
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class PlannerCapabilityCandidate(ApiModel):
    capability_candidate_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    capability_type: str
    capability_id: EntityId | None = None
    name: str | None = None
    match_score: float = 0.0
    risk_level: RiskLevel = RiskLevel.R1
    policy_status: str
    reason_codes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class ModelPlanRequest(ApiModel):
    request_id: EntityId
    task_id: EntityId
    goal: str
    dialogue_state_summary: dict[str, Any] = Field(default_factory=dict)
    intent_summary: dict[str, Any] = Field(default_factory=dict)
    mode_summary: dict[str, Any] = Field(default_factory=dict)
    context_summary: dict[str, Any] = Field(default_factory=dict)
    available_tool_summaries: list[dict[str, Any]] = Field(default_factory=list)
    skill_candidates: list[dict[str, Any]] = Field(default_factory=list)
    mcp_candidates: list[dict[str, Any]] = Field(default_factory=list)
    asset_handle_summaries: list[dict[str, Any]] = Field(default_factory=list)
    risk_policy_summary: dict[str, Any] = Field(default_factory=dict)
    budget: TaskBudget = Field(default_factory=TaskBudget)
    success_criteria: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    privacy_level: str = "medium"
    planner_config: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | str | None = None


class PlanQualityScore(ApiModel):
    score_id: EntityId
    task_id: EntityId
    candidate_id: EntityId
    total_score: float = 0.0
    goal_coverage: float = 0.0
    step_coherence: float = 0.0
    capability_fit: float = 0.0
    safety_compliance: float = 0.0
    budget_efficiency: float = 0.0
    missing_information_handling: float = 0.0
    recoverability: float = 0.0
    artifact_clarity: float = 0.0
    selected: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime | str | None = None


class ModelPlanGenerationResult(ApiModel):
    generation_id: EntityId
    request_id: EntityId
    task_id: EntityId
    status: str
    model_assist_attempted: bool = False
    fallback_used: bool = True
    fallback_reason: str | None = None
    latency_ms: int = 0
    model_call: dict[str, Any] = Field(default_factory=dict)
    candidates: list[PlanCandidate] = Field(default_factory=list)
    quality_scores: list[PlanQualityScore] = Field(default_factory=list)
    selected_candidate_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | str | None = None


class PlanDeltaSuggestion(ApiModel):
    suggestion_id: EntityId
    task_id: EntityId
    trigger_reason: str
    next_action_type: str
    plan_delta: dict[str, Any] = Field(default_factory=dict)
    new_missing_information: list[str] = Field(default_factory=list)
    revised_steps: list[dict[str, Any]] = Field(default_factory=list)
    stop_reason: str | None = None
    confidence: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    model_assist: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | str | None = None


class AgentNextActionDecision(ApiModel):
    decision_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    iteration_id: EntityId | None = None
    loop_index: int
    next_action_type: str
    selected_step_id: EntityId | None = None
    selected_step_key: str | None = None
    plan_delta: dict[str, Any] = Field(default_factory=dict)
    needs_user_input: bool = False
    needs_approval: bool = False
    stop_reason: str | None = None
    confidence: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class AgentLoopSelectedAction(ApiModel):
    action_type: str
    step_id: EntityId | None = None
    step_key: str | None = None
    step_type: str | None = None
    tool_call_refs: list[dict[str, Any]] = Field(default_factory=list)
    safety_decision_refs: list[dict[str, Any]] = Field(default_factory=list)


class AgentLoopEvaluation(ApiModel):
    task_status: str | None = None
    step_status: str | None = None
    pause_reason: str | None = None
    stop_reason: str | None = None
    recoverable: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    summary: str | None = None


class AgentLoopFrame(ApiModel):
    iteration: AgentLoopIteration
    observation: TaskObservation | None = None
    next_action: AgentNextActionDecision | None = None
    selected_action: AgentLoopSelectedAction | None = None
    evaluation: AgentLoopEvaluation = Field(default_factory=AgentLoopEvaluation)
    domain: str | None = None
    request_type: str | None = None
    provider_ref: str | None = None
    action: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    approval: dict[str, Any] = Field(default_factory=dict)
    plan_delta: dict[str, Any] = Field(default_factory=dict)
    pause_reason: str | None = None
    stop_reason: str | None = None


class AgentLoopState(ApiModel):
    runtime: str = "task_agent_runtime"
    authoritative: bool = True
    task_id: EntityId
    domain: str | None = None
    mode: str = "agent"
    current_status: str
    pause_reason: str | None = None
    stop_reason: str | None = None
    iterations: list[AgentLoopFrame] = Field(default_factory=list)
    latest_observation: TaskObservation | None = None
    latest_next_action: AgentNextActionDecision | None = None
    final_result: dict[str, Any] = Field(default_factory=dict)


class ToolFailureRecoveryPlan(ApiModel):
    recovery_plan_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    step_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    failure_type: str
    recovery_action: str
    suggested_actions: list[str] = Field(default_factory=list)
    retry_allowed: bool = False
    bypass_controls: bool = False
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class ModelRecoverySuggestion(ApiModel):
    suggestion_id: EntityId
    task_id: EntityId
    step_id: EntityId | None = None
    failure_type: str
    recovery_action: str
    suggested_actions: list[str] = Field(default_factory=list)
    retry_allowed: bool = False
    bypass_controls: bool = False
    confidence: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    model_assist: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | str | None = None


class TaskParticipant(ApiModel):
    participant_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    member_id: EntityId
    role_in_task: str
    participant_type: str
    status: str
    selection_reason: str
    context_scope: dict[str, Any] = Field(default_factory=dict)
    allowed_skills: list[str] = Field(default_factory=list)
    allowed_mcp_tools: list[str] = Field(default_factory=list)
    capability_decision_id: EntityId | None = None
    output_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    removed_at: datetime | None = None


class TaskSubtask(ApiModel):
    subtask_id: EntityId
    organization_id: EntityId
    parent_task_id: EntityId
    participant_id: EntityId
    assigned_member_id: EntityId
    title: str
    objective: str
    status: str
    sequence: int
    context_scope: dict[str, Any] = Field(default_factory=dict)
    allowed_skills: list[str] = Field(default_factory=list)
    allowed_mcp_tools: list[str] = Field(default_factory=list)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: EntityId | None = None
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class CollaborationPlan(ApiModel):
    collaboration_plan_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    host_member_id: EntityId
    mode: str
    max_rounds: int = 4
    participant_policy: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CollaborationRoutingDecision(ApiModel):
    routing_decision_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    collaboration_plan_id: EntityId | None = None
    host_member_id: EntityId
    mode: str
    status: str
    selected_member_ids: list[EntityId] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    routing_factors: dict[str, Any] = Field(default_factory=dict)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    boundary_summary: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CollaborationRound(ApiModel):
    round_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    collaboration_plan_id: EntityId
    round_index: int
    mode: str
    status: str
    participant_ids: list[EntityId] = Field(default_factory=list)
    max_turns: int = 1
    max_outputs: int = 10
    prompt_summary: str | None = None
    round_summary: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    completed_at: datetime | None = None


class CollaborationHandoffRecord(ApiModel):
    handoff_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    collaboration_plan_id: EntityId | None = None
    subtask_id: EntityId
    from_participant_id: EntityId | None = None
    from_member_id: EntityId | None = None
    to_participant_id: EntityId | None = None
    to_member_id: EntityId
    reason: str
    status: str
    context_summary: dict[str, Any] = Field(default_factory=dict)
    boundary_summary: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CollaborationContextBoundary(ApiModel):
    boundary_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    collaboration_plan_id: EntityId | None = None
    participant_id: EntityId | None = None
    member_id: EntityId
    context_scope: dict[str, Any] = Field(default_factory=dict)
    allowed_context: list[str] = Field(default_factory=list)
    excluded_context: list[str] = Field(default_factory=list)
    asset_scope: list[dict[str, Any]] = Field(default_factory=list)
    memory_scope: str = "member_private_only"
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CollaborationOutput(ApiModel):
    output_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    collaboration_plan_id: EntityId
    round_id: EntityId
    subtask_id: EntityId
    participant_id: EntityId
    member_id: EntityId
    output_type: str
    status: str
    content_redacted: str
    summary: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_ids: list[EntityId] = Field(default_factory=list)
    trace_id: EntityId | None = None
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime | None = None


class HostDecision(ApiModel):
    decision_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    collaboration_plan_id: EntityId
    host_member_id: EntityId
    decision_type: str
    status: str
    summary: str
    rationale: str | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class MemberAvailability(ApiModel):
    member_id: EntityId
    organization_id: EntityId
    status: str = "available"
    capacity: int = 1
    current_load: int = 0
    unavailable_reason: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"
    updated_at: datetime | str | None = None


class SkillPolicy(ApiModel):
    policy_id: EntityId
    organization_id: EntityId
    subject_type: str
    subject_id: EntityId
    allowed_skills: list[str] = Field(default_factory=list)
    denied_skills: list[str] = Field(default_factory=list)
    allowed_mcp_tools: list[str] = Field(default_factory=list)
    denied_mcp_tools: list[str] = Field(default_factory=list)
    risk_policy: dict[str, Any] = Field(default_factory=dict)
    source: str = "manual"
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class TaskSummary(ApiModel):
    task_id: EntityId
    organization_id: EntityId
    title: str
    goal: str
    mode: TaskMode
    status: TaskStatus
    risk_level: RiskLevel
    owner_member_id: EntityId | None = None
    conversation_id: EntityId | None = None
    parent_task_id: EntityId | None = None
    host_member_id: EntityId | None = None
    collaboration_plan_id: EntityId | None = None
    supervisor_mode: str | None = None
    success_criteria: list[str] = Field(default_factory=list)
    progress: TaskProgress = Field(default_factory=TaskProgress)
    current_approval_id: EntityId | None = None
    artifact_count: int = 0
    failure_reason: str | None = None
    cancellation_reason: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskDetail(TaskSummary):
    plan: TaskPlan | None = None
    budget: TaskBudget = Field(default_factory=TaskBudget)
    preflight: dict[str, Any] = Field(default_factory=dict)
    artifact_plan: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class TaskClosureRecord(ApiModel):
    closure_record_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    release_gate_id: EntityId | None = None
    source_eval_run_id: EntityId | None = None
    domain: str
    task_tier: str
    delivery_status: str
    delivery_blockers: list[str] = Field(default_factory=list)
    handoff_reason: str | None = None
    approval_interruption: bool = False
    recovery_summary: dict[str, Any] = Field(default_factory=dict)
    verification_status: str
    once_success: bool = False
    final_deliverable: bool = False
    human_handoff: bool = False
    error_recovered: bool = False
    round_count: int = 0
    tool_call_count: int = 0
    replan_count: int = 0
    stop_reason: str | None = None
    untrusted_observation_triggered: bool = False
    residual_risk_present: bool = False
    created_at: datetime | None = None


class TaskClosureScorecard(ApiModel):
    domain: str
    total_tasks: int = 0
    final_deliverable_rate: float = 0.0
    once_success_rate: float = 0.0
    handoff_rate: float = 0.0
    approval_interruption_rate: float = 0.0
    recovery_success_rate: float | None = None
    completed_unverified_count: int = 0
    failed_verification_count: int = 0
    average_round_count: float = 0.0
    average_tool_call_count: float = 0.0
    replan_rate: float = 0.0
    stop_reason_distribution: dict[str, int] = Field(default_factory=dict)
    blocker_codes: list[str] = Field(default_factory=list)
    threshold_status: dict[str, bool] = Field(default_factory=dict)


class TaskClosureTrendSnapshot(ApiModel):
    domain: str
    sample_size: int = 0
    final_deliverable_rate: float = 0.0
    once_success_rate: float = 0.0
    handoff_rate: float = 0.0
    approval_interruption_rate: float = 0.0
    recovery_success_rate: float | None = None
    delta: dict[str, float] = Field(default_factory=dict)
    generated_at: datetime | None = None


class TaskStep(ApiModel):
    step_id: EntityId
    organization_id: EntityId | None = None
    task_id: EntityId
    step_key: str
    step_type: str
    title: str
    status: str
    sequence: int
    risk_level: RiskLevel = RiskLevel.R1
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    approval_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    subtask_id: EntityId | None = None
    participant_id: EntityId | None = None
    assigned_member_id: EntityId | None = None
    error_code: str | None = None
    error_summary: str | None = None
    idempotency_key: str | None = None
    retry_count: int = 0
    max_retries: int = 2
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskEvent(ApiModel):
    event_id: EntityId
    organization_id: EntityId | None = None
    task_id: EntityId
    step_id: EntityId | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime


class TaskArtifact(ApiModel):
    artifact_id: EntityId
    organization_id: EntityId | None = None
    task_id: EntityId
    step_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    artifact_type: str
    display_name: str
    uri: str
    content_type: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    sensitivity: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ToolDefinition(ApiModel):
    tool_name: str
    display_name: str
    description: str
    source: str = "builtin"
    bundle_id: EntityId | None = None
    skill_id: EntityId | None = None
    mcp_server_id: EntityId | None = None
    mcp_tool_id: EntityId | None = None
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    trust_level: str = "local"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk_policy: dict[str, Any] = Field(default_factory=dict)
    required_handle_types: list[str] = Field(default_factory=list)
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ToolCallRecord(ApiModel):
    tool_call_id: EntityId
    organization_id: EntityId | None = None
    task_id: EntityId | None = None
    step_id: EntityId | None = None
    tool_name: str
    source: str = "builtin"
    status: str
    idempotency_key: str | None = None
    args_redacted: dict[str, Any] = Field(default_factory=dict)
    result_redacted: dict[str, Any] = Field(default_factory=dict)
    handle_ids: list[EntityId] = Field(default_factory=list)
    artifact_ids: list[EntityId] = Field(default_factory=list)
    capability_decision_id: EntityId | None = None
    safety_decision_id: EntityId | None = None
    safety_decision: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    resolved_asset_refs: list[dict[str, Any]] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.R1
    approval_id: EntityId | None = None
    timeout_seconds: int | None = None
    error_code: str | None = None
    error_summary: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ToolActionPolicy(ApiModel):
    policy_id: EntityId
    tool_name: str
    source: str
    action_category: str
    risk_level: RiskLevel = RiskLevel.R1
    allowed_scopes: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_asset_kinds: list[str] = Field(default_factory=list)
    requires_task_binding: bool = False
    requires_approval_from: str | None = None
    deny_patterns: list[str] = Field(default_factory=list)
    output_dlp_policy: dict[str, Any] = Field(default_factory=dict)
    audit_level: str = "standard"
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ToolPolicyDecision(ApiModel):
    decision_id: EntityId
    organization_id: EntityId
    tool_call_id: EntityId | None = None
    task_id: EntityId | None = None
    member_id: EntityId | None = None
    tool_name: str
    policy_id: EntityId | None = None
    source: str
    action_category: str
    requested_risk_level: RiskLevel = RiskLevel.R1
    effective_risk_level: RiskLevel = RiskLevel.R1
    decision: str
    reason_codes: list[str] = Field(default_factory=list)
    required_controls: list[str] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    sandbox_profile_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class TerminalSandboxProfile(ApiModel):
    profile_id: EntityId
    working_dir_policy: str
    allowed_executables: list[str] = Field(default_factory=list)
    denied_executables: list[str] = Field(default_factory=list)
    env_policy: dict[str, Any] = Field(default_factory=dict)
    network_policy: str
    filesystem_policy: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 30
    max_output_bytes: int = 200000
    os_sandbox_backend: str = "none_with_policy_guard"
    degraded_reason: str | None = None
    status: str = "active"
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ToolOutputDlpReport(ApiModel):
    dlp_report_id: EntityId
    organization_id: EntityId
    tool_call_id: EntityId | None = None
    mcp_call_id: EntityId | None = None
    task_id: EntityId | None = None
    source_type: str
    source_id: EntityId | None = None
    scan_target: str
    findings: list[dict[str, Any]] = Field(default_factory=list)
    redaction_count: int = 0
    blocked: bool = False
    manual_review_required: bool = False
    risk_level: RiskLevel = RiskLevel.R1
    redacted_preview: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class MCPProcessPolicyCheck(ApiModel):
    check_id: EntityId
    organization_id: EntityId
    server_id: EntityId | None = None
    display_name: str | None = None
    command: str | None = None
    command_allowed: bool = False
    args_schema_valid: bool = False
    env_refs_only: bool = False
    no_inline_secret: bool = False
    server_scope_valid: bool = False
    member_scope_valid: bool = False
    network_policy: str = "local_stdio_only"
    safety_preflight: str = "not_required"
    decision: str
    reason_codes: list[str] = Field(default_factory=list)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class ExecutionBoundaryDiagnostic(ApiModel):
    diagnostic_id: EntityId
    organization_id: EntityId
    subject_type: str
    subject_id: EntityId | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    status: str
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class ApprovalDetail(ApiModel):
    approval_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    step_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    approval_type: str = "action"
    requested_action: str
    risk_level: RiskLevel
    summary: str
    payload_redacted: dict[str, Any] = Field(default_factory=dict)
    options: list[str] = Field(default_factory=list)
    status: str
    decision_reason: str | None = None
    edited_payload: dict[str, Any] | None = None
    expires_at: datetime | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class TaskReplay(ApiModel):
    task: TaskDetail
    agent_loop: AgentLoopState | None = None
    domain: str | None = None
    domain_request: dict[str, Any] = Field(default_factory=dict)
    domain_result: dict[str, Any] = Field(default_factory=dict)
    steps: list[TaskStep] = Field(default_factory=list)
    events: list[TaskEvent] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    approvals: list[ApprovalDetail] = Field(default_factory=list)
    artifacts: list[TaskArtifact] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)
    memory_writes: list[dict[str, Any]] = Field(default_factory=list)
    skill_runs: list[dict[str, Any]] = Field(default_factory=list)
    mcp_calls: list[dict[str, Any]] = Field(default_factory=list)
    plugin_events: list[dict[str, Any]] = Field(default_factory=list)
    eval_refs: list[dict[str, Any]] = Field(default_factory=list)
    planner_decisions: list[PlannerDecision] = Field(default_factory=list)
    agent_loop_iterations: list[AgentLoopIteration] = Field(default_factory=list)
    observations: list[TaskObservation] = Field(default_factory=list)
    retry_plans: list[TaskRetryPlan] = Field(default_factory=list)
    reflection_candidates: list[TaskReflectionCandidate] = Field(default_factory=list)
    skill_candidates: list[dict[str, Any]] = Field(default_factory=list)
    model_plan_candidates: list[PlanCandidate] = Field(default_factory=list)
    plan_verification_results: list[PlanVerificationResult] = Field(default_factory=list)
    plan_policy_prunes: list[PlanPolicyPrune] = Field(default_factory=list)
    planner_capability_candidates: list[PlannerCapabilityCandidate] = Field(default_factory=list)
    agent_next_action_decisions: list[AgentNextActionDecision] = Field(default_factory=list)
    tool_failure_recovery_plans: list[ToolFailureRecoveryPlan] = Field(default_factory=list)
    browser_evidence: list[dict[str, Any]] = Field(default_factory=list)
    media_evidence: list[dict[str, Any]] = Field(default_factory=list)
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    rollback_events: list[dict[str, Any]] = Field(default_factory=list)
    collaboration_plan: CollaborationPlan | None = None
    routing_decisions: list[CollaborationRoutingDecision] = Field(default_factory=list)
    participants: list[TaskParticipant] = Field(default_factory=list)
    subtasks: list[TaskSubtask] = Field(default_factory=list)
    handoff_records: list[CollaborationHandoffRecord] = Field(default_factory=list)
    context_boundaries: list[CollaborationContextBoundary] = Field(default_factory=list)
    rounds: list[CollaborationRound] = Field(default_factory=list)
    outputs: list[CollaborationOutput] = Field(default_factory=list)
    host_decisions: list[HostDecision] = Field(default_factory=list)
    workflow_evidence: dict[str, Any] = Field(default_factory=dict)
    agent_loop_evidence: dict[str, Any] = Field(default_factory=dict)
    domain_evidence: dict[str, Any] = Field(default_factory=dict)
    recovery_evidence: dict[str, Any] = Field(default_factory=dict)
    handoff_evidence: dict[str, Any] = Field(default_factory=dict)
    final_result: dict[str, Any] = Field(default_factory=dict)


class CollaborationReplay(ApiModel):
    task: TaskDetail
    collaboration_plan: CollaborationPlan | None = None
    routing_decisions: list[CollaborationRoutingDecision] = Field(default_factory=list)
    participants: list[TaskParticipant] = Field(default_factory=list)
    subtasks: list[TaskSubtask] = Field(default_factory=list)
    handoff_records: list[CollaborationHandoffRecord] = Field(default_factory=list)
    context_boundaries: list[CollaborationContextBoundary] = Field(default_factory=list)
    rounds: list[CollaborationRound] = Field(default_factory=list)
    outputs: list[CollaborationOutput] = Field(default_factory=list)
    host_decisions: list[HostDecision] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    skill_runs: list[dict[str, Any]] = Field(default_factory=list)
    mcp_calls: list[dict[str, Any]] = Field(default_factory=list)
    approvals: list[ApprovalDetail] = Field(default_factory=list)
    artifacts: list[TaskArtifact] = Field(default_factory=list)
    trace: dict[str, Any] = Field(default_factory=dict)
    final_result: dict[str, Any] = Field(default_factory=dict)


class ShellSwitchPreview(ApiModel):
    from_shell_id: EntityId
    to_shell_id: EntityId
    changed_labels: list[dict[str, str | None]] = Field(default_factory=list)
    fixed_rules: dict[str, Any] = Field(default_factory=dict)
    blocked_mutations: list[str] = Field(default_factory=list)
    business_values_unchanged: bool = True
    trace_id: EntityId | None = None


class ShellTemplateApplication(ApiModel):
    application_id: EntityId
    organization_id: EntityId
    shell_id: EntityId
    template_type: str
    template_key: str
    object_type: str | None = None
    object_id: EntityId | None = None
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    actor_member_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | str | None = None
