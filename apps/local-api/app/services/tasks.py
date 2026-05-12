from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core_types import (
    AgentLoopEvaluation,
    AgentLoopFrame,
    AgentLoopIteration,
    AgentLoopSelectedAction,
    AgentLoopState,
    AgentNextActionDecision,
    CollaborationOutput,
    CollaborationPlan,
    CollaborationRound,
    CollaborationContextBoundary,
    CollaborationHandoffRecord,
    CollaborationRoutingDecision,
    ErrorCode,
    HostDecision,
    PlanCandidate,
    PlannerCapabilityCandidate,
    PlannerDecision,
    PlanPolicyPrune,
    PlanVerificationResult,
    RiskLevel,
    TaskArtifact,
    TaskBudget,
    TaskDetail,
    TaskEvent,
    TaskMode,
    TaskObservation,
    TaskParticipant,
    TaskPlan,
    TaskReflectionCandidate,
    TaskReplay,
    TaskRetryPlan,
    TaskStatus,
    TaskStep,
    TaskSubtask,
    TaskSummary,
    ToolCallRecord,
    ToolFailureRecoveryPlan,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.task_repo import TaskRepository
from app.schemas.skill_governance import SkillGrantCreateRequest
from app.schemas.skills import BundleInstallRequest, SkillMatchRequest
from app.schemas.tasks import TaskCreateRequest, ToolExecuteRequest
from app.services.artifacts import ArtifactStore
from app.services.audit import AuditEventService
from app.services.chat_intent_router import (
    is_explicit_download_request,
    parse_office_chat_request,
)
from app.services.chat_intent_router import (
    office_skill_input as office_chat_skill_input,
)
from app.services.memory import MemoryService
from app.services.model_planner import (
    AgentNextActionSelector,
    BrainModelPlannerAdapter,
    ModelPlannerService,
    ObservationAwareReplanner,
    PlanningEvidence,
    ToolFailureRecoveryPlanner,
)
from app.services.task_state import ensure_task_transition
from app.services.task_agent_runtime import TaskAgentRuntime
from app.services.task_planning_runtime import TaskPlanningRuntime
from app.services.task_resume_runtime import TaskResumeRuntime
from app.services.task_workflow_runtime import TaskWorkflowRuntime
from app.services.tools import ToolRuntime
from app.services.skill_candidate_extractor import SkillCandidateExtractor

if TYPE_CHECKING:
    from app.services.mcp import MCPService
    from app.services.skill_governance import SkillGovernanceService
    from app.services.skill_plugin import SkillPluginService
    from app.services.skill_repositories import SkillRepositoryService
    from app.services.supervisor import SupervisorService


class TaskEngine:
    def __init__(
        self,
        *,
        repo: TaskRepository,
        member_repo: MemberRepository,
        tool_runtime: ToolRuntime,
        artifact_store: ArtifactStore,
        memory_service: MemoryService,
        trace_service: TraceService,
        audit_service: AuditEventService,
        brain_repo: Any | None = None,
        model_routing_service: Any | None = None,
        secret_store: Any | None = None,
    ) -> None:
        self._repo = repo
        self._members = member_repo
        self._tools = tool_runtime
        self._artifacts = artifact_store
        self._memory = memory_service
        self._trace = trace_service
        self._audit = audit_service
        self._skills: SkillPluginService | None = None
        self._skill_governance: SkillGovernanceService | None = None
        self._skill_repositories: SkillRepositoryService | None = None
        self._mcp: MCPService | None = None
        self._supervisor: SupervisorService | None = None
        self._browser_evidence_provider: (
            Callable[[str], Awaitable[list[Any]]] | None
        ) = None
        self._checkpoint_replay_provider: (
            Callable[[str], Awaitable[tuple[list[dict[str, Any]], list[dict[str, Any]]]]]
            | None
        ) = None
        self._media_replay_provider: Callable[[str], Awaitable[list[dict[str, Any]]]] | None = (
            None
        )
        planner_adapter = (
            BrainModelPlannerAdapter(
                brain_repo=brain_repo,
                model_routing_service=model_routing_service,
                secret_store=secret_store,
            )
            if brain_repo is not None
            and model_routing_service is not None
            and secret_store is not None
            else None
        )
        self._model_planner = ModelPlannerService(adapter=planner_adapter)
        self._next_action_selector = AgentNextActionSelector()
        self._replanner = ObservationAwareReplanner()
        self._failure_recovery = ToolFailureRecoveryPlanner()
        self._planning_runtime = TaskPlanningRuntime(self)
        self._workflow_runtime = TaskWorkflowRuntime(self)
        self._agent_runtime = TaskAgentRuntime(self)
        self._resume_runtime = TaskResumeRuntime(self)
        self._skill_candidate_extractor = SkillCandidateExtractor()

    def set_extension_services(
        self,
        *,
        skill_plugin_service: SkillPluginService | None = None,
        skill_governance_service: SkillGovernanceService | None = None,
        skill_repository_service: SkillRepositoryService | None = None,
        mcp_service: MCPService | None = None,
    ) -> None:
        self._skills = skill_plugin_service
        self._skill_governance = skill_governance_service
        self._skill_repositories = skill_repository_service
        self._mcp = mcp_service

    def set_supervisor_service(self, supervisor_service: SupervisorService) -> None:
        self._supervisor = supervisor_service

    def set_browser_evidence_provider(
        self,
        provider: Callable[[str], Awaitable[list[Any]]],
    ) -> None:
        self._browser_evidence_provider = provider

    def set_checkpoint_replay_provider(
        self,
        provider: Callable[
            [str],
            Awaitable[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
        ],
    ) -> None:
        self._checkpoint_replay_provider = provider

    def set_media_replay_provider(
        self,
        provider: Callable[[str], Awaitable[list[dict[str, Any]]]],
    ) -> None:
        self._media_replay_provider = provider

    def set_model_planner_adapter(self, adapter: Any | None) -> None:
        self._model_planner.set_adapter(adapter)

    def _ensure_task_transition(self, current: str, target: str) -> None:
        ensure_task_transition(current, target)

    def _merge_edited_step_input(
        self,
        current_input: dict[str, Any] | None,
        edited_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return _merge_edited_step_input(current_input, edited_payload)

    def runtime_diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "task_runtime",
            "workflow": self._workflow_runtime.diagnostic(),
            "agent": self._agent_runtime.diagnostic(),
            "resume": self._resume_runtime.diagnostic(),
            "planning": self._planning_runtime.diagnostic(),
            "entry_mode": "runtime_single_track",
            "agent_runtime_authority": "task_agent_runtime",
            "supervisor_integrated": self._supervisor is not None,
        }

    async def create_task(
        self,
        request: TaskCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> TaskDetail:
        return await self._planning_runtime.create_task(request, trace_id=trace_id)

    async def _create_task_impl(
        self,
        request: TaskCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> TaskDetail:
        if request.client_request_id:
            existing = await self._repo.get_task_by_client_request_id(request.client_request_id)
            if existing is not None:
                return await self.detail(existing["task_id"])
        member = await self._members.get_member(request.owner_member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        task_id = new_id("tsk")
        now = utc_now_iso()
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.TASK_CREATE,
            "create task",
            input_data={"goal": redact(request.goal), "owner_member_id": request.owner_member_id},
        )
        if request.mode_hint in {TaskMode.DIRECT, TaskMode.DIRECT_WITH_MEMORY}:
            raise AppError(
                ErrorCode.TASK_PLAN_FAILED,
                "direct/direct_with_memory 不是可执行任务模式",
                status_code=422,
                details={"mode_hint": request.mode_hint.value if request.mode_hint else None},
            )
        plan = await self._plan(task_id, request, trace_id=trace_id)
        plan.steps = _normalize_plan_steps(plan.steps)
        phase19_evidence = await self._model_planner.build_evidence(
            task_id=task_id,
            request=request,
            plan=plan,
            trace_id=trace_id,
        )
        if phase19_evidence is not None:
            plan.steps = _normalize_plan_steps(phase19_evidence.final_steps)
            plan.required_capabilities = _required_capabilities_for_steps(plan.steps)
            high_risk_steps = [
                step for step in plan.steps if _risk_order(step.get("risk_level", "R1")) >= 3
            ]
            plan.approval_strategy = {
                "strategy": "plan_first_then_step_gate"
                if high_risk_steps
                else "step_gate",
                "required_before_execution": bool(high_risk_steps),
                "high_risk_step_keys": [step["step_key"] for step in high_risk_steps],
                "phase19_candidate_id": phase19_evidence.candidate.candidate_id,
                "phase25_selected_candidate_id": phase19_evidence.candidate.candidate_id,
            }
            generation = phase19_evidence.generation
            selected_quality = phase19_evidence.candidate.model_assist.get(
                "quality_score",
                {},
            )
            plan.preflight["phase19"] = {
                "candidate_id": phase19_evidence.candidate.candidate_id,
                "verification_status": phase19_evidence.verification.status,
                "policy_prune_count": len(phase19_evidence.prunes),
                "capability_candidate_count": len(phase19_evidence.capability_candidates),
                "model_assist_enabled": generation.model_assist_attempted,
                "safe_final_step_count": len(phase19_evidence.final_steps),
                "fallback_used": generation.fallback_used,
                "unsafe_prune_types": sorted(
                    {
                        prune.prune_type
                        for prune in phase19_evidence.prunes
                        if prune.prune_type
                        in {
                            "remove_dangerous_shell_command",
                            "remove_sensitive_payload",
                            "fallback_to_rule_plan",
                        }
                    }
                ),
            }
            plan.preflight["phase25"] = {
                "generation_id": generation.generation_id,
                "candidate_count": len(generation.candidates),
                "selected_candidate_id": generation.selected_candidate_id,
                "selected_candidate_source": phase19_evidence.candidate.source,
                "selected_quality_score": selected_quality.get("total_score"),
                "quality_score": selected_quality,
                "model_assist_attempted": generation.model_assist_attempted,
                "fallback_used": generation.fallback_used,
                "fallback_reason": generation.fallback_reason,
                "model_call": generation.model_call,
                "candidate_only": True,
            }
            if phase19_evidence.prunes:
                plan.preflight.setdefault("policy_prunes", [])
                plan.preflight["policy_prunes"].extend(
                    [
                        {
                            "prune_id": prune.prune_id,
                            "prune_type": prune.prune_type,
                            "reason_codes": prune.reason_codes,
                        }
                        for prune in phase19_evidence.prunes
                    ]
                )
        if request.planner_context.get("scheduled_task"):
            plan.preflight["phase36"] = {
                "scheduled_task": redact(request.planner_context.get("scheduled_task", {})),
                "scheduled_run_id": request.planner_context.get("scheduled_task", {}).get(
                    "run_id"
                ),
                "background_execution": redact(
                    request.planner_context.get("background_execution_policy", {})
                ),
                "session_approval_reuse": False,
                "candidate_only": False,
            }
        async with self._repo.transaction():
            await self._repo.insert_task(
                {
                    "task_id": task_id,
                    "organization_id": "org_default",
                    "conversation_id": request.conversation_id,
                    "owner_member_id": request.owner_member_id,
                    "title": plan.title,
                    "goal": request.goal,
                    "mode": plan.mode.value,
                    "status": TaskStatus.CREATED.value,
                    "risk_level": plan.risk_level.value,
                    "success_criteria": plan.success_criteria,
                    "plan": plan.model_dump(mode="json"),
                    "budget": plan.budget.model_dump(mode="json"),
                    "preflight": plan.preflight,
                    "artifact_plan": plan.artifact_plan,
                    "retry_policy": {"max_step_retries": 2},
                    "progress": {"total_steps": len(plan.steps), "completed_steps": 0},
                    "client_request_id": request.client_request_id,
                    "trace_id": trace_id,
                    "host_member_id": plan.host_member_id,
                    "supervisor_mode": plan.collaboration.get("mode")
                    if plan.mode == TaskMode.SUPERVISOR
                    else None,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            await self._event(
                task_id,
                "task.created",
                {"task_id": task_id, "title": plan.title, "mode": plan.mode.value},
                trace_id=trace_id,
                created_at=now,
            )
            await self._repo.insert_planner_decision(
                {
                    "planner_decision_id": new_id("plndec"),
                    "organization_id": "org_default",
                    "task_id": task_id,
                    "planner_type": plan.planner_type,
                    "selected_mode": plan.mode.value,
                    "reason_codes": plan.planner_reason_codes,
                    "capability_snapshot": plan.preflight.get("capability_snapshot", {}),
                    "skill_match_refs": plan.preflight.get("skill_match_refs", []),
                    "mcp_tool_refs": plan.preflight.get("mcp_tool_refs", []),
                    "model_hint": {
                        "enabled": bool(
                            phase19_evidence is not None
                            and phase19_evidence.generation.model_assist_attempted
                        ),
                        "reason": "phase19_model_planner_contract"
                        if phase19_evidence is not None
                        else "rule_first_planner",
                        "brain_decision_id": request.brain_decision_id,
                        "candidate_id": phase19_evidence.candidate.candidate_id
                        if phase19_evidence is not None
                        else None,
                        "verification_status": phase19_evidence.verification.status
                        if phase19_evidence is not None
                        else None,
                        "phase25": (
                            {
                                **phase19_evidence.generation.model_dump(mode="json"),
                                "candidate_only": True,
                                "selected_quality_score": selected_quality.get("total_score"),
                            }
                            if phase19_evidence is not None
                            else {}
                        ),
                    },
                    "status": "completed",
                    "trace_id": trace_id,
                    "created_at": now,
                }
            )
            await self._event(
                task_id,
                "planner.selected",
                {
                    "planner_type": plan.planner_type,
                    "mode": plan.mode.value,
                    "reason_codes": plan.planner_reason_codes,
                },
                trace_id=trace_id,
                created_at=now,
            )
            if phase19_evidence is not None:
                await self._persist_phase19_planning_evidence(phase19_evidence)
                await self._event(
                    task_id,
                    "planner.model_candidate_created",
                    {
                        "candidate_id": phase19_evidence.candidate.candidate_id,
                        "verification_status": phase19_evidence.verification.status,
                        "policy_prune_count": len(phase19_evidence.prunes),
                    },
                    trace_id=trace_id,
                    created_at=now,
                )
            await self._transition_task(task_id, TaskStatus.PLANNING.value)
            for index, step in enumerate(plan.steps, start=1):
                step_key = str(step.get("step_key") or f"step_{index}")
                await self._repo.insert_step(
                    {
                        "step_id": new_id("step"),
                        "organization_id": "org_default",
                        "task_id": task_id,
                        "step_key": step_key,
                        "idempotency_key": f"{task_id}:{step_key}",
                        "sequence": index,
                        "step_type": step.get("step_type", "compose"),
                        "title": step.get("title") or _title_for_step(step, index),
                        "status": "pending",
                        "input": step.get("input", {}),
                        "risk_level": step.get("risk_level", "R1"),
                        "metadata": step.get("metadata", {}),
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            await self._transition_task(task_id, TaskStatus.PLANNED.value)
            await self._event(
                task_id,
                "task.planned",
                {
                    "task_id": task_id,
                    "step_count": len(plan.steps),
                    "risk_level": plan.risk_level.value,
                },
                trace_id=trace_id,
                created_at=utc_now_iso(),
            )
            await self._repo.upsert_job(
                {
                    "job_id": new_id("tjob"),
                    "organization_id": "org_default",
                    "task_id": task_id,
                    "job_type": "run_task",
                    "idempotency_key": f"task.run:{task_id}",
                    "status": "pending",
                    "payload": {"auto_start": request.auto_start},
                    "created_at": now,
                    "updated_at": now,
                }
            )
        await self._audit.write_event(
            actor_type="system",
            action="task.created",
            object_type="task",
            object_id=task_id,
            summary="任务已创建",
            risk_level=plan.risk_level,
            payload={"task_id": task_id, "mode": plan.mode.value},
            trace_id=trace_id,
        )
        await self._end_span(span_id, output_data={"task_id": task_id})
        if request.auto_start:
            await self.start_task(task_id, trace_id=trace_id)
        return await self.detail(task_id)

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        owner_member_id: str | None = None,
        limit: int = 100,
    ) -> list[TaskSummary]:
        return [
            await self._summary(row)
            for row in await self._repo.list_tasks(
                status=status,
                owner_member_id=owner_member_id,
                limit=limit,
            )
        ]

    async def detail(self, task_id: str) -> TaskDetail:
        task = await self._get_task(task_id)
        summary = await self._summary(task)
        normalized_result = await self._normalized_task_result(task)
        return TaskDetail(
            **summary.model_dump(mode="json"),
            plan=TaskPlan(**task["plan"]) if task.get("plan") else None,
            budget=TaskBudget(**task.get("budget", {})),
            preflight=task.get("preflight", {}),
            artifact_plan=task.get("artifact_plan", {}),
            result=normalized_result,
        )

    async def start_task(self, task_id: str, *, trace_id: str | None = None) -> TaskDetail:
        return await self._workflow_runtime.start_task(task_id, trace_id=trace_id)

    async def pause_task(
        self,
        task_id: str,
        *,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> TaskDetail:
        task = await self._get_task(task_id)
        ensure_task_transition(task["status"], TaskStatus.PAUSED.value)
        await self._transition_task(task_id, TaskStatus.PAUSED.value, trace_id=trace_id)
        await self._repo.update_task(
            task_id,
            {"failure_reason": reason, "updated_at": utc_now_iso()},
        )
        await self._mark_run_job(task_id, "paused")
        return await self.detail(task_id)

    async def resume_task(self, task_id: str, *, trace_id: str | None = None) -> TaskDetail:
        return await self._workflow_runtime.resume_task(task_id, trace_id=trace_id)

    async def cancel_task(
        self,
        task_id: str,
        *,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> TaskDetail:
        task = await self._get_task(task_id)
        ensure_task_transition(task["status"], TaskStatus.CANCELLED.value)
        await self._transition_task(task_id, TaskStatus.CANCELLED.value, trace_id=trace_id)
        await self._repo.update_task(
            task_id,
            {"cancellation_reason": reason, "updated_at": utc_now_iso()},
        )
        await self._mark_run_job(task_id, "cancelled")
        return await self.detail(task_id)

    async def retry_task(self, task_id: str, *, trace_id: str | None = None) -> TaskDetail:
        return await self._resume_runtime.retry_task(task_id, trace_id=trace_id)

    async def _retry_task_impl(self, task_id: str, *, trace_id: str | None = None) -> TaskDetail:
        return await self._resume_runtime.retry_task(task_id, trace_id=trace_id)

    async def handle_approval_resolved(
        self,
        approval_id: str,
        *,
        trace_id: str | None = None,
    ) -> TaskDetail:
        return await self._resume_runtime.handle_approval_resolved(
            approval_id,
            trace_id=trace_id,
        )

    async def _handle_approval_resolved_impl(
        self,
        approval_id: str,
        *,
        trace_id: str | None = None,
    ) -> TaskDetail:
        approval = await self._repo.get_approval(approval_id)
        if approval is None:
            raise AppError(ErrorCode.NOT_FOUND, "审批不存在", status_code=404)
        task_id = approval["task_id"]
        task = await self._get_task(task_id)
        if approval["status"] == "denied":
            if (
                task.get("current_approval_id") != approval_id
                and task["status"] != TaskStatus.WAITING_APPROVAL.value
            ):
                return await self.detail(task_id)
            if approval.get("step_id"):
                await self._repo.update_step(
                    approval["step_id"],
                    {
                        "status": "failed",
                        "error_code": ErrorCode.APPROVAL_DENIED.value,
                        "error_summary": "用户拒绝审批",
                        "updated_at": utc_now_iso(),
                    },
                )
            await self._transition_task(
                task_id,
                TaskStatus.PAUSED.value,
                trace_id=trace_id,
                extra={"failure_reason": "approval_denied"},
            )
            return await self.detail(task_id)
        if approval["status"] in {"approved", "edited"}:
            step = (
                await self._repo.get_step(approval["step_id"])
                if approval.get("step_id")
                else None
            )
            if (
                task.get("current_approval_id") != approval_id
                and task["status"] != TaskStatus.WAITING_APPROVAL.value
                and (
                    step is None
                    or step["status"] in {"completed", "running", "failed", "cancelled"}
                )
            ):
                return await self.detail(task_id)
            await self._event(
                task_id,
                "approval.resume.started",
                {"approval_id": approval_id, "step_id": approval.get("step_id")},
                step_id=approval.get("step_id"),
                trace_id=trace_id,
            )
            if approval.get("step_id"):
                if step is not None and step["status"] not in {"completed", "running"}:
                    next_fields = {
                        "status": "pending",
                        "updated_at": utc_now_iso(),
                    }
                    if approval.get("edited_payload"):
                        next_fields["input"] = _merge_edited_step_input(
                            step["input"],
                            approval["edited_payload"],
                        )
                    await self._repo.update_step(approval["step_id"], next_fields)
            await self._mark_run_job(task_id, "running")
            await self._transition_task(
                task_id,
                TaskStatus.RUNNING.value,
                trace_id=trace_id,
                extra={"current_approval_id": None},
            )
            await self._run_task(task_id, trace_id=trace_id)
            await self._sync_run_job_to_task(task_id)
            await self._event(
                task_id,
                "approval.resume.completed",
                {"approval_id": approval_id, "step_id": approval.get("step_id")},
                step_id=approval.get("step_id"),
                trace_id=trace_id,
            )
        return await self.detail(task_id)

    async def events(self, task_id: str) -> list[TaskEvent]:
        await self._get_task(task_id)
        return [TaskEvent(**row) for row in await self._repo.list_events(task_id)]

    async def planner_decisions(self, task_id: str) -> list[PlannerDecision]:
        await self._get_task(task_id)
        return [
            PlannerDecision(**row)
            for row in await self._repo.list_planner_decisions(task_id)
        ]

    async def agent_loop(self, task_id: str) -> list[AgentLoopFrame]:
        return (await self._build_agent_loop_state(task_id)).iterations

    async def observations(self, task_id: str) -> list[TaskObservation]:
        await self._get_task(task_id)
        return [
            TaskObservation(**row) for row in await self._repo.list_task_observations(task_id)
        ]

    async def retry_plans(self, task_id: str) -> list[TaskRetryPlan]:
        await self._get_task(task_id)
        return [
            TaskRetryPlan(**row) for row in await self._repo.list_task_retry_plans(task_id)
        ]

    async def reflection_candidates(self, task_id: str) -> list[TaskReflectionCandidate]:
        await self._get_task(task_id)
        return [
            TaskReflectionCandidate(**row)
            for row in await self._repo.list_task_reflection_candidates(task_id)
        ]

    async def model_plan_candidates(self, task_id: str) -> list[PlanCandidate]:
        await self._get_task(task_id)
        return [
            PlanCandidate(**row)
            for row in await self._repo.list_model_plan_candidates(task_id)
        ]

    async def plan_verification_results(self, task_id: str) -> list[PlanVerificationResult]:
        await self._get_task(task_id)
        return [
            PlanVerificationResult(**row)
            for row in await self._repo.list_plan_verification_results(task_id)
        ]

    async def plan_policy_prunes(self, task_id: str) -> list[PlanPolicyPrune]:
        await self._get_task(task_id)
        return [
            PlanPolicyPrune(**row) for row in await self._repo.list_plan_policy_prunes(task_id)
        ]

    async def planner_capability_candidates(
        self,
        task_id: str,
    ) -> list[PlannerCapabilityCandidate]:
        await self._get_task(task_id)
        return [
            PlannerCapabilityCandidate(**row)
            for row in await self._repo.list_planner_capability_candidates(task_id)
        ]

    async def agent_next_actions(self, task_id: str) -> list[AgentNextActionDecision]:
        await self._get_task(task_id)
        return [
            AgentNextActionDecision(**row)
            for row in await self._repo.list_agent_next_action_decisions(task_id)
        ]

    async def agent_loop_state(self, task_id: str) -> AgentLoopState:
        return await self._build_agent_loop_state(task_id)

    async def failure_recovery_plans(self, task_id: str) -> list[ToolFailureRecoveryPlan]:
        await self._get_task(task_id)
        return [
            ToolFailureRecoveryPlan(**row)
            for row in await self._repo.list_tool_failure_recovery_plans(task_id)
        ]

    async def artifacts(self, task_id: str) -> list[TaskArtifact]:
        await self._get_task(task_id)
        return [TaskArtifact(**row) for row in await self._repo.list_artifacts(task_id)]

    async def replay(self, task_id: str, *, trace_id: str | None = None) -> TaskReplay:
        return await self._replay_impl(task_id, trace_id=trace_id)

    async def _replay_impl(self, task_id: str, *, trace_id: str | None = None) -> TaskReplay:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.TASK_REPLAY,
            "build task replay",
            input_data={"task_id": task_id},
        )
        try:
            task = await self.detail(task_id)
            skill_runs = (
                await self._skills.replay_skill_runs(task_id) if self._skills is not None else []
            )
            mcp_calls = await self._mcp.replay_mcp_calls(task_id) if self._mcp is not None else []
            plugin_events = (
                await self._skills.replay_plugin_events(task_id)
                if self._skills is not None
                else []
            )
            browser_evidence = (
                await self._browser_evidence_provider(task_id)
                if self._browser_evidence_provider is not None
                else []
            )
            checkpoint_data = (
                await self._checkpoint_replay_provider(task_id)
                if self._checkpoint_replay_provider is not None
                else ([], [])
            )
            media_evidence = (
                await self._media_replay_provider(task_id)
                if self._media_replay_provider is not None
                else []
            )
            steps = [TaskStep(**row) for row in await self._repo.list_steps(task_id)]
            retry_plans = [
                TaskRetryPlan(**row)
                for row in await self._repo.list_task_retry_plans(task_id)
            ]
            recovery_plans = [
                ToolFailureRecoveryPlan(**row)
                for row in await self._repo.list_tool_failure_recovery_plans(task_id)
            ]
            handoff_records = [
                CollaborationHandoffRecord(**row)
                for row in await self._repo.list_handoff_records(task_id)
            ]
            agent_loop_state = await self._build_agent_loop_state(task_id)
            final_result = await self._normalized_task_result(
                task.model_dump(mode="json") if hasattr(task, "model_dump") else dict(task),
                raw_result=dict(task.result or {}),
            )
            if agent_loop_state.stop_reason and "stop_reason" not in final_result:
                final_result["stop_reason"] = agent_loop_state.stop_reason
            replay = TaskReplay(
                task=task,
                agent_loop=agent_loop_state,
                steps=steps,
                events=[TaskEvent(**row) for row in await self._repo.list_events(task_id)],
                tool_calls=[
                    ToolCallRecord(**row) for row in await self._repo.list_tool_calls(task_id)
                ],
                approvals=[row for row in await self._repo.list_approvals(task_id)],
                artifacts=[
                    TaskArtifact(**row) for row in await self._repo.list_artifacts(task_id)
                ],
                skill_runs=skill_runs,
                mcp_calls=mcp_calls,
                plugin_events=plugin_events,
                browser_evidence=[
                    item.model_dump(mode="json") if hasattr(item, "model_dump") else item
                    for item in browser_evidence
                ],
                media_evidence=media_evidence,
                checkpoints=checkpoint_data[0],
                rollback_events=checkpoint_data[1],
                trace={"trace_id": task.trace_id, "span_refs": []},
                memory_writes=[
                    item.model_dump(mode="json")
                    for item in await self._memory.list_experience_records(
                        task_id=task_id,
                        limit=50,
                    )
                ],
                planner_decisions=[
                    PlannerDecision(**row)
                    for row in await self._repo.list_planner_decisions(task_id)
                ],
                agent_loop_iterations=[
                    AgentLoopIteration(**row)
                    for row in await self._repo.list_agent_loop_iterations(task_id)
                ],
                observations=[
                    TaskObservation(**row)
                    for row in await self._repo.list_task_observations(task_id)
                ],
                retry_plans=retry_plans,
                reflection_candidates=[
                    TaskReflectionCandidate(**row)
                    for row in await self._repo.list_task_reflection_candidates(task_id)
                ],
                skill_candidates=[],
                model_plan_candidates=[
                    PlanCandidate(**row)
                    for row in await self._repo.list_model_plan_candidates(task_id)
                ],
                plan_verification_results=[
                    PlanVerificationResult(**row)
                    for row in await self._repo.list_plan_verification_results(task_id)
                ],
                plan_policy_prunes=[
                    PlanPolicyPrune(**row)
                    for row in await self._repo.list_plan_policy_prunes(task_id)
                ],
                planner_capability_candidates=[
                    PlannerCapabilityCandidate(**row)
                    for row in await self._repo.list_planner_capability_candidates(task_id)
                ],
                agent_next_action_decisions=[
                    AgentNextActionDecision(**row)
                    for row in await self._repo.list_agent_next_action_decisions(task_id)
                ],
                tool_failure_recovery_plans=recovery_plans,
                collaboration_plan=(
                    CollaborationPlan(**plan_row)
                    if (plan_row := await self._repo.get_collaboration_plan(task_id))
                    else None
                ),
                routing_decisions=[
                    CollaborationRoutingDecision(**row)
                    for row in await self._repo.list_routing_decisions(task_id)
                ],
                participants=[
                    TaskParticipant(**row)
                    for row in await self._repo.list_participants(task_id)
                ],
                subtasks=[TaskSubtask(**row) for row in await self._repo.list_subtasks(task_id)],
                handoff_records=handoff_records,
                context_boundaries=[
                    CollaborationContextBoundary(**row)
                    for row in await self._repo.list_context_boundaries(task_id)
                ],
                rounds=[CollaborationRound(**row) for row in await self._repo.list_rounds(task_id)],
                outputs=[
                    CollaborationOutput(**row)
                    for row in await self._repo.list_collaboration_outputs(task_id)
                ],
                host_decisions=[
                    HostDecision(**row) for row in await self._repo.list_host_decisions(task_id)
                ],
                workflow_evidence={
                    "mode": task.mode.value,
                    "step_count": len(steps),
                    "completed_step_count": sum(
                        1 for step in steps if step.status == "completed"
                    ),
                    "repo_execution": {
                        "enabled": bool(final_result.get("repo_request_type")),
                        "deliverable": bool(final_result.get("deliverable")),
                        "files_changed_count": len(final_result.get("files_changed") or []),
                    },
                    "code_hosting": {
                        "enabled": bool(final_result.get("code_hosting_request_type")),
                        "request_type": final_result.get("code_hosting_request_type"),
                        "skill_binding": {
                            "package_ref": final_result.get("code_hosting_package_ref"),
                            "bundle_id": final_result.get("code_hosting_bundle_id"),
                            "skill_id": final_result.get("code_hosting_skill_id"),
                            "selection_reason": final_result.get("code_hosting_selection_reason"),
                        },
                        "remote_execution": {
                            "remote_artifact_count": len(final_result.get("remote_artifacts") or []),
                            "deliverable": bool(final_result.get("deliverable")),
                        },
                        "approval_state": {
                            "blocked": "approval_required"
                            in set(final_result.get("publish_blockers") or []),
                            "publish_blockers": list(final_result.get("publish_blockers") or []),
                        },
                    },
                },
                agent_loop_evidence={
                    "runtime": agent_loop_state.runtime,
                    "authoritative": agent_loop_state.authoritative,
                    "iteration_count": len(agent_loop_state.iterations),
                    "pause_reason": agent_loop_state.pause_reason,
                    "stop_reason": agent_loop_state.stop_reason,
                    "repo_execution": {
                        "repair_attempted": bool(final_result.get("repair_attempted")),
                        "repair_outcome": final_result.get("repair_outcome"),
                    },
                    "code_hosting": {
                        "publish_blocker_count": len(final_result.get("publish_blockers") or []),
                        "remote_artifact_count": len(final_result.get("remote_artifacts") or []),
                    },
                },
                recovery_evidence={
                    "retry_plan_count": len(retry_plans),
                    "failure_recovery_plan_count": len(recovery_plans),
                    "repo_execution": {
                        "residual_risk": list(final_result.get("residual_risk") or []),
                    },
                    "code_hosting": {
                        "publish_blockers": list(final_result.get("publish_blockers") or []),
                        "residual_risk": list(final_result.get("residual_risk") or []),
                    },
                },
                handoff_evidence={
                    "handoff_count": len(handoff_records),
                    "handoff_records_present": bool(handoff_records),
                },
                final_result=final_result,
            )
            replay.skill_candidates = self._skill_candidate_extractor.extract_from_replay(replay)
            await self._end_span(span_id, output_data={"task_id": task_id})
            return replay
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def _build_agent_loop_state(self, task_id: str) -> AgentLoopState:
        task = await self.detail(task_id)
        observations = [
            TaskObservation(**row) for row in await self._repo.list_task_observations(task_id)
        ]
        iterations = [
            AgentLoopIteration(**row)
            for row in await self._repo.list_agent_loop_iterations(task_id)
        ]
        next_actions = [
            AgentNextActionDecision(**row)
            for row in await self._repo.list_agent_next_action_decisions(task_id)
        ]
        observation_by_id = {
            str(item.observation_id): item
            for item in observations
            if item.observation_id is not None
        }
        next_action_by_iteration = {
            str(item.iteration_id): item
            for item in next_actions
            if item.iteration_id is not None
        }
        frames: list[AgentLoopFrame] = []
        for iteration in iterations:
            observation = (
                observation_by_id.get(str(iteration.observation_id))
                if iteration.observation_id is not None
                else None
            )
            next_action = next_action_by_iteration.get(str(iteration.iteration_id))
            selected_action = AgentLoopSelectedAction(
                action_type=str(
                    (iteration.selected_action or {}).get("next_action_type")
                    or (next_action.next_action_type if next_action is not None else "unknown")
                ),
                step_id=(iteration.selected_action or {}).get("step_id"),
                step_key=(iteration.selected_action or {}).get("step_key"),
                step_type=(iteration.selected_action or {}).get("step_type"),
                tool_call_refs=list(iteration.tool_call_refs or []),
                safety_decision_refs=list(iteration.safety_decision_refs or []),
            )
            evaluation_payload = dict(iteration.evaluation_result or {})
            pause_reason = str(
                evaluation_payload.get("pause_reason")
                or evaluation_payload.get("reason")
                or (
                    "approval_waiting"
                    if iteration.stop_reason == "approval_required"
                    else "budget_exhausted"
                    if iteration.stop_reason == "budget_exhausted"
                    else ""
                )
            ).strip() or None
            stop_reason = _phase96_stop_reason(iteration.stop_reason, task.status.value)
            frames.append(
                AgentLoopFrame(
                    iteration=iteration,
                    observation=observation,
                    next_action=next_action,
                    selected_action=selected_action,
                    evaluation=AgentLoopEvaluation(
                        task_status=str(evaluation_payload.get("task_status") or task.status.value),
                        step_status=(
                            str(evaluation_payload.get("step_status"))
                            if evaluation_payload.get("step_status") is not None
                            else None
                        ),
                        pause_reason=pause_reason,
                        stop_reason=stop_reason,
                        recoverable=bool(evaluation_payload.get("recoverable")),
                        reason_codes=list(evaluation_payload.get("reason_codes") or []),
                        summary=(
                            observation.summary
                            if observation is not None
                            else iteration.observation_summary
                        ),
                    ),
                    plan_delta=dict(iteration.plan_delta or {}),
                    pause_reason=pause_reason,
                    stop_reason=stop_reason,
                )
            )
        latest_observation = observations[-1] if observations else None
        latest_next_action = next_actions[-1] if next_actions else None
        final_stop_reason = _phase96_stop_reason(
            str(task.result.get("stop_reason") or task.failure_reason or ""),
            task.status.value,
        )
        pause_reason = None
        if task.status.value == TaskStatus.WAITING_APPROVAL.value:
            pause_reason = "approval_waiting"
        elif task.status.value == TaskStatus.PAUSED.value:
            pause_reason = final_stop_reason
        normalized_result = await self._normalized_task_result(
            task.model_dump(mode="json") if hasattr(task, "model_dump") else dict(task),
            raw_result=dict(task.result or {}),
        )
        return AgentLoopState(
            task_id=task.task_id,
            current_status=task.status.value,
            pause_reason=pause_reason,
            stop_reason=final_stop_reason,
            iterations=frames,
            latest_observation=latest_observation,
            latest_next_action=latest_next_action,
            final_result=normalized_result,
        )

    async def recover_stale_jobs(self) -> None:
        await self._resume_runtime.recover_stale_jobs()

    async def _recover_stale_jobs_impl(self) -> None:
        await self._resume_runtime.recover_stale_jobs()

    async def _persist_phase19_planning_evidence(self, evidence: PlanningEvidence) -> None:
        seen_candidates: set[str] = set()
        seen_verifications: set[str] = set()
        seen_prunes: set[str] = set()
        for item in evidence.candidates:
            if item.candidate.candidate_id not in seen_candidates:
                await self._repo.insert_model_plan_candidate(
                    item.candidate.model_dump(mode="json")
                )
                seen_candidates.add(item.candidate.candidate_id)
            if item.verification.verification_id not in seen_verifications:
                await self._repo.insert_plan_verification_result(
                    item.verification.model_dump(mode="json")
                )
                seen_verifications.add(item.verification.verification_id)
            for prune in item.prunes:
                if prune.prune_id in seen_prunes:
                    continue
                await self._repo.insert_plan_policy_prune(prune.model_dump(mode="json"))
                seen_prunes.add(prune.prune_id)
        for candidate in evidence.capability_candidates:
            await self._repo.insert_planner_capability_candidate(
                candidate.model_dump(mode="json")
            )

    async def _run_task(self, task_id: str, *, trace_id: str | None) -> None:
        await self._workflow_runtime.run_task(task_id, trace_id=trace_id)

    async def _run_task_impl(self, task_id: str, *, trace_id: str | None) -> None:
        await self._workflow_runtime.run_task(task_id, trace_id=trace_id)

    async def _reset_recoverable_failed_steps(
        self,
        task: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> int:
        reset_count = 0
        for step in await self._repo.list_steps(task["task_id"]):
            if step["status"] != "failed":
                continue
            if _risk_order(str(step.get("risk_level") or "R1")) >= 3:
                await self._create_tool_failure_recovery_plan(
                    task=task,
                    step=step,
                    failure_reason=str(step.get("error_code") or "high_risk_retry_blocked"),
                    trace_id=trace_id,
                )
                continue
            retry_count = int(step.get("retry_count") or 0)
            max_retries = int(step.get("max_retries") or 0)
            if retry_count >= max_retries:
                await self._create_tool_failure_recovery_plan(
                    task=task,
                    step=step,
                    failure_reason=str(step.get("error_code") or "retry_exhausted"),
                    trace_id=trace_id,
                )
                continue
            next_retry = retry_count + 1
            base_key = str(step.get("idempotency_key") or f"{task['task_id']}:{step['step_key']}")
            await self._repo.update_step(
                step["step_id"],
                {
                    "status": "pending",
                    "retry_count": next_retry,
                    "idempotency_key": f"{base_key}:retry:{next_retry}",
                    "approval_id": None,
                    "tool_call_id": None,
                    "error_code": None,
                    "error_summary": None,
                    "updated_at": utc_now_iso(),
                },
            )
            await self._event(
                task["task_id"],
                "task.step.retry",
                {
                    "step_id": step["step_id"],
                    "step_key": step["step_key"],
                    "retry_count": next_retry,
                },
                step_id=step["step_id"],
                trace_id=trace_id,
            )
            reset_count += 1
        if reset_count:
            await self._update_progress(task["task_id"])
        return reset_count

    async def _latest_failed_step(self, task_id: str) -> dict[str, Any] | None:
        failed = [
            step
            for step in await self._repo.list_steps(task_id)
            if step["status"] == "failed"
        ]
        return failed[-1] if failed else None

    async def _run_agent_loop(self, task_id: str, *, trace_id: str | None) -> None:
        await self._agent_runtime.run_agent_loop(task_id, trace_id=trace_id)

    async def _run_agent_loop_impl(self, task_id: str, *, trace_id: str | None) -> None:
        await self._agent_runtime.run_agent_loop(task_id, trace_id=trace_id)
        return
        task = await self._get_task(task_id)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.TASK_RUN,
            "run agent loop",
            input_data={"task_id": task_id, "mode": TaskMode.AGENT.value},
        )
        loop_steps = 0
        tool_calls = 0
        stop_reason = "completed"
        try:
            await self._event(
                task_id,
                "agent.loop_started",
                {"task_id": task_id, "mode": TaskMode.AGENT.value},
                trace_id=trace_id,
            )
            await self._event(
                task_id,
                "agent.observe",
                {
                    "goal": task["goal"],
                    "budget": task.get("budget", {}),
                    "resource_handles": task.get("resource_handle_ids", []),
                },
                trace_id=trace_id,
            )
            steps = await self._repo.list_steps(task_id)
            budget = TaskBudget(**task.get("budget", {}))
            for step in steps:
                fresh = await self._get_task(task_id)
                if fresh["status"] == TaskStatus.CANCELLED.value:
                    stop_reason = "cancelled"
                    await self._event(
                        task_id,
                        "agent.stop",
                        {
                            "stop_reason": stop_reason,
                            "loop_steps": loop_steps,
                            "tool_calls": tool_calls,
                        },
                        trace_id=trace_id,
                    )
                    await self._event(
                        task_id,
                        "agent.stopped",
                        {
                            "stop_reason": stop_reason,
                            "loop_steps": loop_steps,
                            "tool_calls": tool_calls,
                        },
                        trace_id=trace_id,
                    )
                    await self._end_span(
                        span_id,
                        output_data={"status": "cancelled", "stop_reason": stop_reason},
                    )
                    return
                if step["status"] in {"completed", "failed"}:
                    continue
                if loop_steps >= budget.max_loop_steps:
                    stop_reason = "budget_exhausted"
                    break
                if tool_calls >= budget.max_tool_calls:
                    stop_reason = "budget_exhausted"
                    break
                loop_steps += 1
                observe_span = await self._start_span(
                    trace_id,
                    TraceSpanType.AGENT_OBSERVE,
                    "agent observe",
                    input_data={"loop_index": loop_steps, "step_key": step["step_key"]},
                )
                observation = await self._create_observation(
                    task=fresh,
                    step=step,
                    source_type="task_state",
                    source_ref={"step_id": step["step_id"], "step_key": step["step_key"]},
                    summary=f"准备执行步骤：{step['title']}",
                    payload={"goal": fresh["goal"], "step_input": step.get("input", {})},
                    trace_id=trace_id,
                )
                await self._end_span(
                    observe_span,
                    output_data={
                        "observation_id": observation["observation_id"],
                        "summary": observation["summary"],
                    },
                )
                plan_span = await self._start_span(
                    trace_id,
                    TraceSpanType.AGENT_PLAN,
                    "agent plan",
                    input_data={"loop_index": loop_steps, "step_type": step["step_type"]},
                )
                await self._event(
                    task_id,
                    "agent.plan",
                    {
                        "loop_index": loop_steps,
                        "next_step_key": step["step_key"],
                        "step_type": step["step_type"],
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                await self._end_span(
                    plan_span,
                    output_data={
                        "selected_action": step["step_key"],
                        "reason": "next_pending_step",
                    },
                )
                act_span = await self._start_span(
                    trace_id,
                    TraceSpanType.AGENT_ACT,
                    "agent act",
                    input_data={"loop_index": loop_steps, "step_key": step["step_key"]},
                )
                await self._event(
                    task_id,
                    "agent.act",
                    {"loop_index": loop_steps, "step_key": step["step_key"]},
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                if step["step_type"] in {"tool_call", "mcp_call", "skill_run"}:
                    tool_calls += 1
                await self._run_step(fresh, step, trace_id=trace_id)
                after_step = await self._repo.get_step(step["step_id"]) or step
                await self._end_span(
                    act_span,
                    output_data={
                        "status": after_step["status"],
                        "tool_call_id": after_step.get("tool_call_id"),
                    },
                )
                result_observation = await self._create_observation(
                    task=fresh,
                    step=after_step,
                    source_type=after_step["step_type"],
                    source_ref={
                        "step_id": after_step["step_id"],
                        "step_key": after_step["step_key"],
                        "tool_call_id": after_step.get("tool_call_id"),
                    },
                    summary=_observation_summary_for_step(after_step),
                    payload=after_step.get("output", {}),
                    trace_id=trace_id,
                )
                current = await self._get_task(task_id)
                evaluate_span = await self._start_span(
                    trace_id,
                    TraceSpanType.AGENT_EVALUATE,
                    "agent evaluate",
                    input_data={"loop_index": loop_steps, "step_status": after_step["status"]},
                )
                await self._event(
                    task_id,
                    "agent.evaluate",
                    {
                        "loop_index": loop_steps,
                        "task_status": current["status"],
                        "step_key": step["step_key"],
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                iteration_stop_reason = None
                if current["status"] == TaskStatus.WAITING_APPROVAL.value:
                    stop_reason = "approval_required"
                    iteration_stop_reason = stop_reason
                elif current["status"] in {TaskStatus.FAILED.value, TaskStatus.PAUSED.value}:
                    stop_reason = (
                        "blocked_by_safety"
                        if after_step.get("error_code") in {
                            ErrorCode.SAFETY_BLOCKED.value,
                            ErrorCode.TOOL_PERMISSION_DENIED.value,
                        }
                        else "failed"
                    )
                    iteration_stop_reason = stop_reason
                await self._end_span(
                    evaluate_span,
                    output_data={
                        "task_status": current["status"],
                        "stop_reason": iteration_stop_reason,
                    },
                )
                next_pending_step_key = _next_pending_step_key(
                    await self._repo.list_steps(task_id)
                )
                budget_snapshot = {
                    "loop_steps": loop_steps,
                    "max_loop_steps": budget.max_loop_steps,
                    "tool_calls": tool_calls,
                    "max_tool_calls": budget.max_tool_calls,
                }
                iteration_id = new_id("agit")
                plan_delta_suggestion = self._replanner.suggest(
                    task=current,
                    step=after_step,
                    loop_index=loop_steps,
                    task_status=current["status"],
                    step_status=after_step["status"],
                    next_step_key=next_pending_step_key,
                    stop_reason=iteration_stop_reason,
                    budget_snapshot=budget_snapshot,
                    trace_id=trace_id,
                )
                next_action = self._next_action_selector.select(
                    task=task,
                    step=after_step,
                    iteration_id=iteration_id,
                    loop_index=loop_steps,
                    task_status=current["status"],
                    step_status=after_step["status"],
                    next_step_key=next_pending_step_key,
                    stop_reason=iteration_stop_reason,
                    budget_snapshot=budget_snapshot,
                    trace_id=trace_id,
                    plan_delta_suggestion=plan_delta_suggestion,
                )
                await self._repo.insert_agent_loop_iteration(
                    {
                        "iteration_id": iteration_id,
                        "organization_id": task["organization_id"],
                        "task_id": task_id,
                        "loop_index": loop_steps,
                        "observation_id": result_observation["observation_id"],
                        "observation_summary": result_observation["summary"],
                        "plan_delta": next_action.plan_delta,
                        "selected_action": {
                            "step_id": after_step["step_id"],
                            "step_key": after_step["step_key"],
                            "step_type": after_step["step_type"],
                            "next_action_type": next_action.next_action_type,
                        },
                        "tool_call_refs": _tool_call_refs(after_step),
                        "safety_decision_refs": await self._safety_refs_for_step(after_step),
                        "evaluation_result": {
                            "task_status": current["status"],
                            "step_status": after_step["status"],
                            "recoverable": current["status"]
                            in {TaskStatus.PAUSED.value, TaskStatus.WAITING_APPROVAL.value},
                        },
                        "next_step_key": next_pending_step_key,
                        "stop_reason": iteration_stop_reason,
                        "budget_snapshot": budget_snapshot,
                        "status": "completed",
                        "trace_id": trace_id,
                        "started_at": utc_now_iso(),
                        "completed_at": utc_now_iso(),
                    }
                )
                await self._repo.insert_agent_next_action_decision(
                    next_action.model_dump(mode="json")
                )
                if next_action.next_action_type == "revise_plan":
                    await self._event(
                        task_id,
                        "agent.revise",
                        {
                            "loop_index": loop_steps,
                            "decision_id": next_action.decision_id,
                            "reason_codes": next_action.reason_codes,
                            "plan_delta_suggestion_id": plan_delta_suggestion.suggestion_id,
                        },
                        step_id=step["step_id"],
                        trace_id=trace_id,
                    )
                await self._event(
                    task_id,
                    "agent.next_action_selected",
                    {
                        "loop_index": loop_steps,
                        "decision_id": next_action.decision_id,
                        "next_action_type": next_action.next_action_type,
                        "reason_codes": next_action.reason_codes,
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                if (
                    after_step["status"] == "failed"
                    or iteration_stop_reason in {"failed", "blocked_by_safety", "approval_required"}
                ):
                    recovery_reason = (
                        iteration_stop_reason
                        or after_step.get("error_code")
                        or "failed"
                    )
                    await self._create_tool_failure_recovery_plan(
                        task=current,
                        step=after_step,
                        failure_reason=recovery_reason,
                        trace_id=trace_id,
                    )
                await self._event(
                    task_id,
                    "agent.iteration_completed",
                    {
                        "loop_index": loop_steps,
                        "observation_id": result_observation["observation_id"],
                        "stop_reason": iteration_stop_reason,
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                if current["status"] == TaskStatus.WAITING_APPROVAL.value:
                    await self._event(
                        task_id,
                        "agent.stop",
                        {
                            "stop_reason": stop_reason,
                            "loop_steps": loop_steps,
                            "tool_calls": tool_calls,
                        },
                        trace_id=trace_id,
                    )
                    await self._event(
                        task_id,
                        "agent.stopped",
                        {
                            "stop_reason": stop_reason,
                            "loop_steps": loop_steps,
                            "tool_calls": tool_calls,
                        },
                        trace_id=trace_id,
                    )
                    await self._end_span(
                        span_id,
                        output_data={
                            "status": "waiting_approval",
                            "stop_reason": stop_reason,
                        },
                    )
                    return
                if current["status"] in {TaskStatus.FAILED.value, TaskStatus.PAUSED.value}:
                    break
            final_task = await self._get_task(task_id)
            if final_task["status"] == TaskStatus.FAILED.value:
                await self._safe_reflect(task_id, trace_id=trace_id)
            if stop_reason == "budget_exhausted":
                next_pending_step_key = _next_pending_step_key(await self._repo.list_steps(task_id))
                budget_observation = await self._create_observation(
                    task=await self._get_task(task_id),
                    step=None,
                    source_type="agent_budget",
                    source_ref={"task_id": task_id, "reason": stop_reason},
                    summary="Agent loop 因预算限制停止，未选择新的执行动作。",
                    payload={
                        "reason": stop_reason,
                        "loop_steps": loop_steps,
                        "max_loop_steps": budget.max_loop_steps,
                        "tool_calls": tool_calls,
                        "max_tool_calls": budget.max_tool_calls,
                        "next_step_key": next_pending_step_key,
                    },
                    trace_id=trace_id,
                )
                budget_snapshot = {
                    "loop_steps": loop_steps,
                    "max_loop_steps": budget.max_loop_steps,
                    "tool_calls": tool_calls,
                    "max_tool_calls": budget.max_tool_calls,
                }
                iteration_id = new_id("agit")
                plan_delta_suggestion = self._replanner.suggest(
                    task=task,
                    step=None,
                    loop_index=loop_steps + 1,
                    task_status=TaskStatus.PAUSED.value,
                    step_status=None,
                    next_step_key=next_pending_step_key,
                    stop_reason=stop_reason,
                    budget_snapshot=budget_snapshot,
                    trace_id=trace_id,
                )
                next_action = self._next_action_selector.select(
                    task=task,
                    step=None,
                    iteration_id=iteration_id,
                    loop_index=loop_steps + 1,
                    task_status=TaskStatus.PAUSED.value,
                    step_status=None,
                    next_step_key=next_pending_step_key,
                    stop_reason=stop_reason,
                    budget_snapshot=budget_snapshot,
                    trace_id=trace_id,
                    plan_delta_suggestion=plan_delta_suggestion,
                )
                await self._repo.insert_agent_loop_iteration(
                    {
                        "iteration_id": iteration_id,
                        "organization_id": task["organization_id"],
                        "task_id": task_id,
                        "loop_index": loop_steps + 1,
                        "observation_id": budget_observation["observation_id"],
                        "observation_summary": budget_observation["summary"],
                        "plan_delta": next_action.plan_delta,
                        "selected_action": {"next_action_type": next_action.next_action_type},
                        "tool_call_refs": [],
                        "safety_decision_refs": [],
                        "evaluation_result": {
                            "task_status": TaskStatus.PAUSED.value,
                            "recoverable": True,
                            "reason": stop_reason,
                        },
                        "next_step_key": next_pending_step_key,
                        "stop_reason": stop_reason,
                        "budget_snapshot": budget_snapshot,
                        "status": "stopped",
                        "trace_id": trace_id,
                        "started_at": utc_now_iso(),
                        "completed_at": utc_now_iso(),
                    }
                )
                await self._repo.insert_agent_next_action_decision(
                    next_action.model_dump(mode="json")
                )
                await self._create_tool_failure_recovery_plan(
                    task=task,
                    step=None,
                    failure_reason=stop_reason,
                    trace_id=trace_id,
                )
                await self._repo.update_task(
                    task_id,
                    {
                        "status": TaskStatus.PAUSED.value,
                        "failure_reason": stop_reason,
                        "result": {
                            "summary": "Agent loop 已因预算耗尽暂停。",
                            "stop_reason": stop_reason,
                            "loop_steps": loop_steps,
                            "tool_calls": tool_calls,
                        },
                        "updated_at": utc_now_iso(),
                    },
                )
                await self._create_retry_plan(
                    task,
                    reason=stop_reason,
                    suggested_actions=["提高预算后重试", "缩小任务范围后重试"],
                    trace_id=trace_id,
                )
            elif stop_reason in {"failed", "blocked_by_safety"}:
                await self._create_retry_plan(
                    await self._get_task(task_id),
                    reason=stop_reason,
                    suggested_actions=["检查失败步骤后重试", "移除受阻动作后重试"],
                    trace_id=trace_id,
                )
            else:
                await self._complete_task(
                    task_id,
                    {
                        "summary": "Agent loop 已完成。",
                        "stop_reason": stop_reason,
                        "loop_steps": loop_steps,
                        "tool_calls": tool_calls,
                    },
                    trace_id=trace_id,
                )
            await self._event(
                task_id,
                "agent.stop",
                {
                    "stop_reason": stop_reason,
                    "loop_steps": loop_steps,
                    "tool_calls": tool_calls,
                },
                trace_id=trace_id,
            )
            await self._event(
                task_id,
                "agent.stopped",
                {
                    "stop_reason": stop_reason,
                    "loop_steps": loop_steps,
                    "tool_calls": tool_calls,
                },
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                output_data={"status": "completed", "stop_reason": stop_reason},
            )
        except Exception as exc:
            await self._repo.update_task(
                task_id,
                {
                    "status": TaskStatus.FAILED.value,
                    "failure_reason": str(redact(str(exc))),
                    "updated_at": utc_now_iso(),
                },
            )
            await self._event(
                task_id,
                "agent.stop",
                {
                    "stop_reason": "failed",
                    "error_code": getattr(exc, "code", ErrorCode.TASK_STEP_FAILED.value),
                    "message": str(redact(str(exc))),
                },
                trace_id=trace_id,
            )
            await self._event(
                task_id,
                "agent.stopped",
                {
                    "stop_reason": "failed",
                    "error_code": getattr(exc, "code", ErrorCode.TASK_STEP_FAILED.value),
                },
                trace_id=trace_id,
            )
            await self._create_retry_plan(
                await self._get_task(task_id),
                reason="failed",
                suggested_actions=["检查失败步骤后重试", "缩小范围后重试"],
                trace_id=trace_id,
            )
            await self._safe_reflect(task_id, trace_id=trace_id)
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            if isinstance(exc, AppError):
                return
            raise

    async def _run_step(
        self,
        task: dict[str, Any],
        step: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> None:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.TASK_STEP_RUN,
            "run task step",
            input_data={"step_id": step["step_id"], "step_key": step["step_key"]},
        )
        now = utc_now_iso()
        output: dict[str, Any] = {}
        tool_call_id: str | None = None
        await self._repo.update_step(step["step_id"], {"status": "running", "updated_at": now})
        await self._event(
            task["task_id"],
            "task.step_started",
            {"step_id": step["step_id"], "step_key": step["step_key"]},
            step_id=step["step_id"],
            trace_id=trace_id,
        )
        try:
            if step["step_type"] in {"tool_call", "mcp_call"}:
                tool_request = ToolExecuteRequest(
                    task_id=task["task_id"],
                    step_id=step["step_id"],
                    member_id=task["owner_member_id"],
                    tool_name=step["input"]["tool_name"],
                    args=step["input"].get("args", {}),
                    idempotency_key=(
                        f"{step.get('idempotency_key')}:approved:{step.get('approval_id')}"
                        if step.get("approval_id")
                        else step.get("idempotency_key")
                    ),
                    approval_id=step.get("approval_id"),
                )
                response = await self._tools.execute(tool_request, trace_id=trace_id)
                if response.approval:
                    await self._repo.update_step(
                        step["step_id"],
                        {
                            "status": "waiting_approval",
                            "approval_id": response.approval.approval_id,
                            "tool_call_id": response.tool_call.tool_call_id,
                            "updated_at": utc_now_iso(),
                        },
                    )
                    await self._repo.update_task(
                        task["task_id"],
                        {
                            "status": TaskStatus.WAITING_APPROVAL.value,
                            "current_approval_id": response.approval.approval_id,
                            "updated_at": utc_now_iso(),
                        },
                    )
                    await self._end_span(
                        span_id,
                        output_data={
                            "status": "waiting_approval",
                            "approval_id": response.approval.approval_id,
                        },
                    )
                    return
                output = response.result
                tool_call_id = response.tool_call.tool_call_id
            elif step["step_type"] == "skill_match":
                if self._skills is None:
                    raise AppError(
                        ErrorCode.SKILL_MATCH_FAILED,
                        "Skill Engine 未初始化",
                        status_code=500,
                    )
                matches = await self._skills.match_skills(
                    SkillMatchRequest(
                        owner_member_id=task["owner_member_id"],
                        conversation_id=task.get("conversation_id"),
                        task_id=task["task_id"],
                        intent=str(step["input"].get("intent") or "task_execution"),
                        goal=str(step["input"].get("goal") or task["goal"]),
                    ),
                    trace_id=trace_id,
                )
                output = {"matches": [match.model_dump(mode="json") for match in matches]}
                await self._event(
                    task["task_id"],
                    "skill.matched",
                    {
                        "match_count": len(matches),
                        "top_skill_id": matches[0].skill_id if matches else None,
                    },
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                tool_call_id = None
            elif step["step_type"] == "skill_run":
                if self._skills is None:
                    raise AppError(
                        ErrorCode.SKILL_RUN_FAILED,
                        "Skill Engine 未初始化",
                        status_code=500,
                    )
                skill_input = step["input"].get("input", {"goal": task["goal"]})
                if not isinstance(skill_input, dict):
                    skill_input = {"input": skill_input, "goal": task["goal"]}
                skill_input = await self._prepare_skill_input_for_task(
                    task,
                    skill_input,
                    trace_id=trace_id,
                )
                phase36 = task.get("preflight", {}).get("phase36", {})
                background_policy = (
                    phase36.get("background_execution")
                    if isinstance(phase36, dict)
                    else None
                )
                if isinstance(background_policy, dict):
                    execution_context = dict(skill_input.get("execution_context") or {})
                    execution_context.setdefault(
                        "attendance",
                        background_policy.get("attendance", "unattended"),
                    )
                    execution_context["session_approval_reuse"] = False
                    execution_context["scheduled_task"] = phase36.get("scheduled_task")
                    skill_input["execution_context"] = execution_context
                skill_run = await self._skills.run_skill(
                    str(step["input"]["skill_id"]),
                    task_id=task["task_id"],
                    step_id=step["step_id"],
                    owner_member_id=task["owner_member_id"],
                    input_data=skill_input,
                    matched_reason=str(step["input"].get("matched_reason") or "task_plan"),
                    confidence=step["input"].get("confidence"),
                    approval_id=step.get("approval_id"),
                    trace_id=trace_id,
                )
                output = {"skill_run": skill_run.model_dump(mode="json")}
                if skill_run.status == "waiting_approval" and skill_run.approval_id:
                    await self._repo.update_step(
                        step["step_id"],
                        {
                            "status": "waiting_approval",
                            "approval_id": skill_run.approval_id,
                            "output": redact(output),
                            "updated_at": utc_now_iso(),
                        },
                    )
                    await self._repo.update_task(
                        task["task_id"],
                        {
                            "status": TaskStatus.WAITING_APPROVAL.value,
                            "current_approval_id": skill_run.approval_id,
                            "updated_at": utc_now_iso(),
                        },
                    )
                    await self._end_span(
                        span_id,
                        output_data={
                            "status": "waiting_approval",
                            "approval_id": skill_run.approval_id,
                        },
                    )
                    return
                tool_call_id = None
            elif step["step_type"] == "compose":
                artifact = await self._artifacts.write_text(
                    task_id=task["task_id"],
                    organization_id=task["organization_id"],
                    step_id=step["step_id"],
                    display_name="task-report.md",
                    content=_report_for_task(task, await self._repo.list_steps(task["task_id"])),
                    artifact_type="report",
                    trace_id=trace_id,
                )
                await self._event(
                    task["task_id"],
                    "artifact.created",
                    {"artifact_id": artifact.artifact_id, "uri": artifact.uri},
                    step_id=step["step_id"],
                    trace_id=trace_id,
                )
                output = {"artifact_id": artifact.artifact_id, "uri": artifact.uri}
                tool_call_id = None
            else:
                output = {"status": "skipped", "reason": f"unsupported_step:{step['step_type']}"}
                tool_call_id = None
            if _repo_profile_from_task(task).get("enabled"):
                output, tool_call_id = await self._finalize_repo_step_output(
                    task=task,
                    step=step,
                    output=output,
                    tool_call_id=tool_call_id,
                    trace_id=trace_id,
                )
                if output.get("repo_verify_failed"):
                    raise AppError(
                        ErrorCode.TASK_STEP_FAILED,
                        "代码验证失败，且当前任务未收敛。",
                        status_code=409,
                    )
            await self._repo.update_step(
                step["step_id"],
                {
                    "status": "completed",
                    "output": redact(output),
                    "tool_call_id": tool_call_id,
                    "updated_at": utc_now_iso(),
                },
            )
            await self._update_progress(task["task_id"])
            await self._event(
                task["task_id"],
                "task.step_completed",
                {"step_id": step["step_id"], "step_key": step["step_key"]},
                step_id=step["step_id"],
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"status": "completed"})
        except Exception as exc:
            await self._repo.update_step(
                step["step_id"],
                {
                    "status": "failed",
                    "output": redact(output),
                    "error_code": getattr(exc, "code", ErrorCode.TASK_STEP_FAILED.value),
                    "error_summary": str(redact(str(exc))),
                    "updated_at": utc_now_iso(),
                },
            )
            await self._event(
                task["task_id"],
                "task.step_failed",
                {"step_id": step["step_id"], "error": str(redact(str(exc)))},
                step_id=step["step_id"],
                trace_id=trace_id,
            )
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def _prepare_skill_input_for_task(
        self,
        task: dict[str, Any],
        skill_input: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> dict[str, Any]:
        source_artifact_id = str(skill_input.get("source_artifact_id") or "").strip()
        if not source_artifact_id:
            return skill_input
        try:
            source_artifact, _preview = await self._artifacts.read_preview(
                source_artifact_id,
                limit=32,
            )
        except AppError:
            return skill_input
        if source_artifact.task_id == task["task_id"]:
            return skill_input
        if not _is_office_artifact(source_artifact):
            return skill_input
        source_task = await self._repo.get_task(source_artifact.task_id)
        if source_task is None:
            return skill_input
        if source_task.get("conversation_id") != task.get("conversation_id"):
            return skill_input
        if source_task.get("owner_member_id") != task.get("owner_member_id"):
            return skill_input
        source_path = self._artifacts.path_for_artifact(source_artifact)
        copied = await self._artifacts.write_bytes(
            task_id=task["task_id"],
            organization_id=task["organization_id"],
            display_name=source_artifact.display_name,
            content=source_path.read_bytes(),
            artifact_type=source_artifact.artifact_type,
            content_type=source_artifact.content_type or "application/octet-stream",
            sensitivity=source_artifact.sensitivity,
            metadata={
                **dict(source_artifact.metadata or {}),
                "copied_for_office_edit": True,
                "source_artifact_id": source_artifact.artifact_id,
                "source_task_id": source_artifact.task_id,
            },
            trace_id=trace_id,
        )
        return {**skill_input, "source_artifact_id": copied.artifact_id}

    async def _ensure_repo_workspace_baseline(
        self,
        task_id: str,
        *,
        trace_id: str | None,
    ) -> dict[str, str]:
        task = await self._get_task(task_id)
        repo_profile = _repo_profile_from_task(task)
        code_hosting_profile = _code_hosting_profile_from_task(task)
        if not repo_profile.get("enabled") and not code_hosting_profile.get("enabled"):
            return {}
        preflight = dict(task.get("preflight") or {})
        repo_execution = dict(preflight.get("repo_execution") or {})
        if "workspace_baseline" in repo_execution and isinstance(
            repo_execution.get("workspace_baseline"),
            dict,
        ):
            baseline = repo_execution.get("workspace_baseline")
            return {str(key): str(value) for key, value in baseline.items()}
        snapshot = _workspace_snapshot(self._artifacts.task_dir(task_id))
        repo_execution["workspace_baseline"] = snapshot
        repo_execution["workspace_dirty_preexisting"] = sorted(snapshot.keys())
        preflight["repo_execution"] = repo_execution
        await self._repo.update_task(
            task_id,
            {
                "preflight": preflight,
                "updated_at": utc_now_iso(),
            },
        )
        await self._event(
            task_id,
            "repo.workspace_baseline_captured",
            {
                "file_count": len(snapshot),
                "workspace_dirty_preexisting": sorted(snapshot.keys()),
            },
            trace_id=trace_id,
        )
        await self._create_observation(
            task=await self._get_task(task_id),
            step=None,
            source_type="workspace_dirty_state",
            source_ref={"task_id": task_id},
            summary=(
                "检测到预存工作区文件。"
                if snapshot
                else "工作区在任务开始前是干净的。"
            ),
            payload={
                "workspace_dirty_preexisting": sorted(snapshot.keys()),
                "workspace_baseline_count": len(snapshot),
            },
            trace_id=trace_id,
        )
        return snapshot

    async def _normalized_task_result(
        self,
        task: dict[str, Any],
        *,
        raw_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = dict(raw_result or task.get("result") or {})
        repo_profile = _repo_profile_from_task(task)
        code_hosting_profile = _code_hosting_profile_from_task(task)
        if not repo_profile.get("enabled") and not code_hosting_profile.get("enabled"):
            return result
        steps = await self._repo.list_steps(task["task_id"])
        workspace_dir = self._artifacts.task_dir(task["task_id"])
        workspace_snapshot = _workspace_snapshot(workspace_dir)
        preflight = dict(task.get("preflight") or {})
        repo_execution = dict(preflight.get("repo_execution") or {})
        baseline = {
            str(key): str(value)
            for key, value in dict(repo_execution.get("workspace_baseline") or {}).items()
        }
        files_changed = _workspace_diff_files(baseline, workspace_snapshot)
        workspace_dirty_preexisting = sorted(
            str(item) for item in list(repo_execution.get("workspace_dirty_preexisting") or [])
        )
        verification_steps = [step for step in steps if _repo_step_role(step) == "verify"]
        verification_checks: list[dict[str, Any]] = []
        failed_checks: list[str] = []
        verification_ran = False
        verification_passed = not bool(repo_profile.get("requires_verification"))
        for step in verification_steps:
            output = dict(step.get("output") or {})
            repair_info = dict(output.get("repo_repair") or {})
            retry_output = dict(output.get("verification_retry") or {})
            check_status = "not_run"
            exit_code = None
            if repair_info.get("outcome") == "resolved":
                verification_ran = True
                exit_code = retry_output.get("exit_code", 0)
                check_status = "passed"
            elif step["status"] == "completed":
                verification_ran = True
                exit_code = output.get("exit_code")
                if exit_code in {0, "0", None}:
                    check_status = "passed"
                else:
                    check_status = "failed"
                    failed_checks.append(step["step_key"])
            elif step["status"] == "failed":
                verification_ran = True
                check_status = "failed"
                failed_checks.append(step["step_key"])
                exit_code = output.get("exit_code")
            verification_checks.append(
                {
                    "step_key": step["step_key"],
                    "title": step["title"],
                    "status": check_status,
                    "command": ((step.get("input") or {}).get("args") or {}).get("command"),
                    "exit_code": exit_code,
                }
            )
        if repo_profile.get("requires_verification"):
            verification_passed = (
                verification_ran
                and bool(verification_steps)
                and not failed_checks
                and all(item["status"] == "passed" for item in verification_checks)
            )
        repair_outputs = [
            dict(step.get("output") or {}).get("repo_repair")
            for step in steps
            if dict(step.get("output") or {}).get("repo_repair") is not None
        ]
        repair_attempted = any(
            isinstance(item, dict) and bool(item.get("attempted")) for item in repair_outputs
        )
        repair_outcome = "not_needed"
        if repair_attempted:
            repair_outcome = "failed"
            if any(isinstance(item, dict) and item.get("outcome") == "resolved" for item in repair_outputs):
                repair_outcome = "resolved"
        not_run_checks: list[str] = []
        if repo_profile.get("requires_verification") and not verification_ran:
            not_run_checks.append("targeted_verification")
        residual_risk: list[str] = []
        if repo_profile.get("requires_verification") and not verification_ran:
            residual_risk.append("代码改动未经过验证。")
        if failed_checks:
            residual_risk.append("至少一个验证检查失败。")
        if task["status"] in {
            TaskStatus.WAITING_APPROVAL.value,
            TaskStatus.PAUSED.value,
            TaskStatus.FAILED.value,
        }:
            residual_risk.append(f"任务当前状态为 {task['status']}，不可视为最终可交付。")
        deliverable = task["status"] == TaskStatus.COMPLETED.value
        if repo_profile.get("requires_verification"):
            deliverable = deliverable and verification_passed
        if repo_profile.get("allow_write") and files_changed and not verification_ran:
            deliverable = False
        if task["status"] != TaskStatus.COMPLETED.value:
            deliverable = False
        patch_summary = {
            "changed_files_count": len(files_changed),
            "changed_files": files_changed,
            "summary": (
                f"修改了 {len(files_changed)} 个文件。"
                if files_changed
                else "没有检测到新的文件改动。"
            ),
        }
        verification_summary = {
            "required": bool(repo_profile.get("requires_verification")),
            "ran": verification_ran,
            "passed": verification_passed,
            "status": (
                "passed"
                if verification_passed
                else "failed"
                if failed_checks
                else "not_run"
                if repo_profile.get("requires_verification")
                else "not_required"
            ),
            "scope": str(repo_profile.get("verification_scope") or "targeted"),
            "checks": verification_checks,
            "failed_checks": failed_checks,
        }
        normalized_result = {
            **result,
            "files_changed": files_changed,
            "workspace_dirty_preexisting": workspace_dirty_preexisting,
        }
        if repo_profile.get("enabled"):
            summary = str(result.get("summary") or "").strip()
            if not summary:
                if deliverable:
                    summary = "代码仓任务已完成，改动和验证都已收口。"
                elif failed_checks:
                    summary = "代码仓任务已产出改动，但验证未通过。"
                elif not_run_checks:
                    summary = "代码仓任务已产出改动，但还没有完成验证。"
                else:
                    summary = "代码仓任务已结束。"
            normalized_result.update(
                {
                    "summary": summary,
                    "repo_request_type": repo_profile.get("request_type"),
                    "patch_summary": patch_summary,
                    "verification_summary": verification_summary,
                    "not_run_checks": not_run_checks,
                    "residual_risk": residual_risk,
                    "repair_attempted": repair_attempted,
                    "repair_outcome": repair_outcome,
                    "deliverable": deliverable,
                }
            )
        if code_hosting_profile.get("enabled"):
            binding = dict(code_hosting_profile.get("skill_binding") or {})
            code_steps = [
                step
                for step in steps
                if str(dict(step.get("metadata") or {}).get("code_hosting_step_role") or "")
                == "remote_execution"
            ]
            code_step = code_steps[-1] if code_steps else None
            code_step_output = dict(code_step.get("output") or {}) if code_step is not None else {}
            skill_run = (
                dict(code_step_output.get("skill_run") or {})
                if isinstance(code_step_output.get("skill_run"), dict)
                else {}
            )
            skill_output = (
                dict(skill_run.get("output_redacted") or {})
                if isinstance(skill_run.get("output_redacted"), dict)
                else {}
            )
            branch_state = dict(skill_output.get("branch_state") or {})
            if not branch_state:
                branch_state = {
                    "base_branch": result.get("base_branch")
                    or binding.get("base_branch")
                    or task.get("plan", {}).get("constraints", {}).get("base_branch")
                    or preflight.get("planner_context", {}).get("base_branch")
                    or None,
                    "target_branch": result.get("target_branch")
                    or binding.get("target_branch")
                    or task.get("plan", {}).get("constraints", {}).get("target_branch")
                    or preflight.get("planner_context", {}).get("target_branch")
                    or None,
                    "workspace_dirty": bool(files_changed),
                }
            else:
                branch_state.setdefault("workspace_dirty", bool(files_changed))
            remote_artifacts = skill_output.get("remote_artifacts")
            if not isinstance(remote_artifacts, list):
                remote_artifacts = []
            commit_summary = dict(skill_output.get("commit_summary") or {})
            pr_summary = dict(skill_output.get("pr_summary") or {})
            review_outcome = dict(skill_output.get("review_outcome") or {})
            release_summary = dict(skill_output.get("release_summary") or {})
            publish_blockers = [
                str(item)
                for item in list(skill_output.get("publish_blockers") or [])
                if str(item).strip()
            ]
            if not binding.get("ready"):
                publish_blockers.append(
                    str(binding.get("blocked_reason") or "code_hosting_skill_binding_unavailable")
                )
            if code_step is not None and code_step.get("status") == "failed":
                publish_blockers.append(
                    str(code_step.get("error_code") or code_step.get("error_summary") or "skill_run_failed")
                )
            if task["status"] == TaskStatus.WAITING_APPROVAL.value:
                publish_blockers.append("approval_required")
            if task["status"] in {TaskStatus.PAUSED.value, TaskStatus.FAILED.value}:
                publish_blockers.append(f"task_status:{task['status']}")
            publish_blockers = sorted({item for item in publish_blockers if item})
            code_residual_risk = list(normalized_result.get("residual_risk") or [])
            for blocker in publish_blockers:
                if blocker == "approval_required":
                    code_residual_risk.append("远程工程动作仍在等待审批。")
                elif blocker == "clawhub_catalog_search_empty":
                    code_residual_risk.append("ClawHub 中没有找到可绑定的 GitHub Skill。")
                elif blocker == "code_hosting_skill_binding_unavailable":
                    code_residual_risk.append("远程工程 Skill 绑定未就绪。")
                else:
                    code_residual_risk.append(f"远程工程协作存在阻断：{blocker}。")
            code_deliverable = (
                task["status"] == TaskStatus.COMPLETED.value and not publish_blockers
            )
            if repo_profile.get("requires_verification"):
                code_deliverable = code_deliverable and verification_passed
            deliverable = bool(normalized_result.get("deliverable", task["status"] == TaskStatus.COMPLETED.value))
            deliverable = deliverable and code_deliverable
            summary = str(normalized_result.get("summary") or "").strip()
            if not summary:
                if deliverable:
                    summary = "远程工程协作任务已完成，本地与远程证据都已收口。"
                elif publish_blockers:
                    summary = "远程工程协作任务已生成过程证据，但仍存在阻断。"
                else:
                    summary = "远程工程协作任务已结束。"
            normalized_result.update(
                {
                    "summary": summary,
                    "code_hosting_request_type": code_hosting_profile.get("request_type"),
                    "forge_provider_type": binding.get("provider_type")
                    or code_hosting_profile.get("provider_type")
                    or "github",
                    "code_hosting_package_ref": binding.get("package_ref"),
                    "code_hosting_bundle_id": binding.get("bundle_id"),
                    "code_hosting_skill_id": binding.get("skill_id"),
                    "code_hosting_selection_reason": binding.get("selection_reason"),
                    "remote_artifacts": remote_artifacts,
                    "branch_state": branch_state,
                    "commit_summary": commit_summary,
                    "pr_summary": pr_summary,
                    "review_outcome": review_outcome,
                    "release_summary": release_summary,
                    "publish_blockers": publish_blockers,
                    "verification_summary": normalized_result.get("verification_summary")
                    or verification_summary,
                    "not_run_checks": normalized_result.get("not_run_checks") or not_run_checks,
                    "residual_risk": code_residual_risk,
                    "deliverable": deliverable,
                }
            )
        return normalized_result

    async def _finalize_repo_step_output(
        self,
        *,
        task: dict[str, Any],
        step: dict[str, Any],
        output: dict[str, Any],
        tool_call_id: str | None,
        trace_id: str | None,
    ) -> tuple[dict[str, Any], str | None]:
        role = _repo_step_role(step)
        if role == "explore":
            await self._create_observation(
                task=task,
                step=step,
                source_type="workspace_dirty_state",
                source_ref={"step_id": step["step_id"], "step_key": step["step_key"]},
                summary="已采集代码仓工作区概况。",
                payload={"workspace_items": output.get("items") or []},
                trace_id=trace_id,
            )
            return output, tool_call_id
        if role == "patch":
            args = dict((step.get("input") or {}).get("args") or {})
            await self._create_observation(
                task=task,
                step=step,
                source_type="diff_summary",
                source_ref={"step_id": step["step_id"], "step_key": step["step_key"]},
                summary=f"已写入文件：{args.get('path') or 'unknown'}",
                payload={
                    "path": args.get("path"),
                    "overwrite": bool(args.get("overwrite")),
                },
                trace_id=trace_id,
            )
            return output, tool_call_id
        if role != "verify":
            return output, tool_call_id
        metadata = dict(step.get("metadata") or {})
        verify_mode = str(metadata.get("repo_verify_mode") or "").strip()
        if verify_mode == "content_contains":
            expected = str(metadata.get("repo_verify_expected_text") or "")
            content = str(output.get("content") or "")
            output = {
                **output,
                "exit_code": 0 if expected in content else 1,
                "verification_mode": verify_mode,
            }
        exit_code = output.get("exit_code")
        if exit_code in {None, 0, "0"}:
            await self._create_observation(
                task=task,
                step=step,
                source_type="verification_summary",
                source_ref={"step_id": step["step_id"], "step_key": step["step_key"]},
                summary="目标验证已通过。",
                payload=output,
                trace_id=trace_id,
            )
            return output, tool_call_id
        verify_kind = str(metadata.get("repo_verify_kind") or "test")
        await self._create_observation(
            task=task,
            step=step,
            source_type="lint_failure" if verify_kind == "lint" else "test_failure",
            source_ref={"step_id": step["step_id"], "step_key": step["step_key"]},
            summary="目标验证失败，准备评估是否可局部修复。",
            payload=output,
            trace_id=trace_id,
        )
        repair = await self._attempt_repo_repair(
            task=task,
            step=step,
            verify_output=output,
            trace_id=trace_id,
        )
        if repair is None:
            return {
                **output,
                "repo_verify_failed": True,
                "repo_verify_failure_reason": "no_repair_available",
            }, tool_call_id
        output = {
            **output,
            "repo_repair": {
                "attempted": True,
                "outcome": "resolved" if repair["resolved"] else "failed",
                "repair_paths": repair["repair_paths"],
                "initial_exit_code": exit_code,
                "final_exit_code": repair["final_output"].get("exit_code"),
            },
            "verification_retry": repair["final_output"],
        }
        tool_call_id = repair["tool_call_id"]
        await self._create_observation(
            task=task,
            step=step,
            source_type="partial_success_recovery",
            source_ref={"step_id": step["step_id"], "step_key": step["step_key"]},
            summary=(
                "验证失败后已通过局部修复收敛。"
                if repair["resolved"]
                else "验证失败后尝试过局部修复，但仍未收敛。"
            ),
            payload=output,
            trace_id=trace_id,
        )
        if not repair["resolved"]:
            return {
                **output,
                "repo_verify_failed": True,
                "repo_verify_failure_reason": "repair_failed",
            }, tool_call_id
        return output, tool_call_id

    async def _attempt_repo_repair(
        self,
        *,
        task: dict[str, Any],
        step: dict[str, Any],
        verify_output: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any] | None:
        profile = _repo_profile_from_task(task)
        if not profile.get("allow_auto_repair"):
            return None
        metadata = dict(step.get("metadata") or {})
        repair_writes = metadata.get("repo_repair_writes") or []
        if not isinstance(repair_writes, list) or not repair_writes:
            single_path = metadata.get("repo_repair_path")
            single_content = metadata.get("repo_repair_content")
            if single_path and single_content is not None:
                repair_writes = [{"path": single_path, "content": single_content}]
        if not repair_writes:
            return None
        repair_paths: list[str] = []
        for index, item in enumerate(repair_writes, start=1):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            repair_paths.append(path)
            resolved = self._artifacts.resolve_task_relative_path(task["task_id"], path)
            await self._artifacts.write_text(
                task_id=task["task_id"],
                organization_id=str(task.get("organization_id") or "org_default"),
                step_id=step["step_id"],
                tool_call_id=f"{step['step_id']}:repo-repair:{index}",
                display_name=resolved.name,
                content=str(item.get("content") or ""),
                artifact_type="text",
                subdir=resolved.parent.relative_to(
                    self._artifacts.task_dir(task["task_id"])
                ).as_posix(),
                trace_id=trace_id,
            )
        verify_args = dict((step.get("input") or {}).get("args") or {})
        metadata = dict(step.get("metadata") or {})
        verify_mode = str(metadata.get("repo_verify_mode") or "").strip()
        verify_tool_name = "terminal.run"
        if verify_mode == "content_contains":
            verify_tool_name = "file.read"
        rerun = await self._tools.execute(
            ToolExecuteRequest(
                task_id=task["task_id"],
                step_id=step["step_id"],
                member_id=task["owner_member_id"],
                tool_name=verify_tool_name,
                args=verify_args,
                idempotency_key=f"{step['step_id']}:repo-verify-retry",
            ),
            trace_id=trace_id,
        )
        final_output = dict(rerun.result or {})
        if verify_mode == "content_contains":
            expected = str(metadata.get("repo_verify_expected_text") or "")
            content = str(final_output.get("content") or "")
            final_output["exit_code"] = 0 if expected in content else 1
            final_output["verification_mode"] = verify_mode
        return {
            "resolved": final_output.get("exit_code") in {None, 0, "0"},
            "repair_paths": repair_paths,
            "final_output": final_output,
            "tool_call_id": rerun.tool_call.tool_call_id,
            "initial_output": verify_output,
        }

    async def _complete_task(
        self,
        task_id: str,
        result: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> None:
        await self._transition_task(
            task_id,
            TaskStatus.COMPLETED.value,
            trace_id=trace_id,
            extra={"result": result, "current_approval_id": None},
        )
        task = await self._get_task(task_id)
        normalized_result = await self._normalized_task_result(task, raw_result=result)
        if normalized_result != task.get("result", {}):
            await self._repo.update_task(
                task_id,
                {
                    "result": normalized_result,
                    "updated_at": utc_now_iso(),
                },
            )
        else:
            normalized_result = task.get("result", {})
        await self._event(task_id, "task.completed", normalized_result, trace_id=trace_id)
        await self._audit.write_event(
            actor_type="system",
            action="task.completed",
            object_type="task",
            object_id=task_id,
            summary="任务已完成",
            risk_level=RiskLevel.R1,
            payload={"task_id": task_id},
            trace_id=trace_id,
        )
        await self._safe_reflect(task_id, trace_id=trace_id)

    async def _safe_reflect(self, task_id: str, *, trace_id: str | None) -> None:
        try:
            await self._reflect(task_id, trace_id=trace_id)
        except Exception as exc:
            await self._event(
                task_id,
                "task.reflection_failed",
                {"error": str(redact(str(exc)))},
                trace_id=trace_id,
            )

    async def _reflect(self, task_id: str, *, trace_id: str | None) -> None:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.TASK_REFLECTION,
            "reflect task result",
            input_data={"task_id": task_id},
        )
        task = await self._get_task(task_id)
        steps = await self._repo.list_steps(task_id)
        result_summary = str(
            task.get("result", {}).get("summary")
            or task.get("result", {}).get("message")
            or "任务已完成"
        )
        try:
            await self._memory.consolidate_experience(
                member_id=task["owner_member_id"],
                task_id=task_id,
                conversation_id=task.get("conversation_id"),
                outcome=task["status"],
                summary_text=(
                    f"任务经验：{task['title']}。目标：{task['goal']}。结果：{result_summary}"
                ),
                source={
                    "type": "task_result",
                    "task_id": task_id,
                    "conversation_id": task.get("conversation_id"),
                    "trace_id": trace_id,
                },
                evidence={
                    "task_status": task["status"],
                    "goal": task["goal"],
                    "result": task.get("result", {}),
                    "steps": [
                        {
                            "step_id": step["step_id"],
                            "step_key": step["step_key"],
                            "step_type": step["step_type"],
                            "status": step["status"],
                            "error_code": step.get("error_code"),
                        }
                        for step in steps
                    ],
                },
                steps=steps,
                trace_id=trace_id,
                root_span_id=span_id,
            )
        except Exception:
            pass
        await self._create_reflection_candidate(
            task,
            candidate_type="memory_candidate",
            summary=f"任务经历可作为长期记忆候选：{task['title']}",
            payload={
                "goal": task["goal"],
                "status": task["status"],
                "result": task.get("result", {}),
                "step_count": len(steps),
            },
            confidence=0.72 if task["status"] == TaskStatus.COMPLETED.value else 0.58,
            trace_id=trace_id,
        )
        if task["mode"] in {TaskMode.WORKFLOW.value, TaskMode.AGENT.value}:
            await self._create_reflection_candidate(
                task,
                candidate_type="workflow_template_candidate",
                summary=f"可复用流程候选：{task['title']}",
                payload={
                    "mode": task["mode"],
                    "task_status": task["status"],
                    "steps": [
                        {"step_key": step["step_key"], "step_type": step["step_type"]}
                        for step in steps
                    ],
                    "default_status": "disabled",
                },
                confidence=0.64,
                trace_id=trace_id,
            )
        if task["mode"] == TaskMode.AGENT.value:
            await self._create_reflection_candidate(
                task,
                candidate_type="skill_candidate",
                summary=f"Skill 草稿候选：{task['title']}",
                payload={
                    "source": "agent_reflection",
                    "default_status": "disabled",
                    "requires_eval_before_enable": True,
                },
                confidence=0.58,
                risk_level=task["risk_level"],
                trace_id=trace_id,
            )
        await self._event(
            task_id,
            "reflection_completed",
            {"candidate_only": True},
            trace_id=trace_id,
        )
        await self._end_span(span_id, output_data={"task_id": task_id})

    async def _create_observation(
        self,
        *,
        task: dict[str, Any],
        step: dict[str, Any] | None,
        source_type: str,
        source_ref: dict[str, Any],
        summary: str,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any]:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.TASK_OBSERVATION_CREATE,
            "create task observation",
            input_data={"task_id": task["task_id"], "source_type": source_type},
        )
        now = utc_now_iso()
        payload_redacted = redact(payload)
        untrusted = _untrusted_observation(source_type, payload_redacted)
        observation = {
            "observation_id": new_id("obs"),
            "organization_id": task["organization_id"],
            "task_id": task["task_id"],
            "step_id": step["step_id"] if step else None,
            "source_type": source_type,
            "source_ref": redact(source_ref),
            "trusted_level": "untrusted_external_content" if untrusted else "local_runtime",
            "summary": str(redact(summary))[:500],
            "key_facts": _key_facts(payload_redacted),
            "errors": _observation_errors(step, payload_redacted),
            "artifact_refs": _artifact_refs(payload_redacted),
            "sensitivity": "low",
            "untrusted_instructions_detected": untrusted,
            "payload_redacted": payload_redacted if isinstance(payload_redacted, dict) else {},
            "trace_id": trace_id,
            "created_at": now,
        }
        await self._repo.insert_task_observation(observation)
        await self._event(
            task["task_id"],
            "task.observation_created",
            {
                "observation_id": observation["observation_id"],
                "source_type": source_type,
                "trusted_level": observation["trusted_level"],
                "summary": observation["summary"],
            },
            step_id=step["step_id"] if step else None,
            trace_id=trace_id,
        )
        await self._end_span(
            span_id,
            output_data={
                "observation_id": observation["observation_id"],
                "untrusted": untrusted,
            },
        )
        return observation

    async def _create_retry_plan(
        self,
        task: dict[str, Any],
        *,
        reason: str,
        suggested_actions: list[str],
        trace_id: str | None,
    ) -> None:
        now = utc_now_iso()
        pending = _next_pending_step_key(await self._repo.list_steps(task["task_id"]))
        await self._repo.insert_task_retry_plan(
            {
                "retry_plan_id": new_id("retry"),
                "organization_id": task["organization_id"],
                "task_id": task["task_id"],
                "reason": reason,
                "suggested_actions": suggested_actions,
                "resumable_from_step_key": pending,
                "budget_delta": {"max_loop_steps": 2} if reason == "budget_exhausted" else {},
                "status": "open",
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        await self._event(
            task["task_id"],
            "task.retry_plan_created",
            {"reason": reason, "resumable_from_step_key": pending},
            trace_id=trace_id,
        )
        if reason in {"failed", "blocked_by_safety", "budget_exhausted"}:
            await self._create_reflection_candidate(
                task,
                candidate_type="failure_pattern_candidate",
                summary=f"失败模式候选：{reason}",
                payload={"reason": reason, "suggested_actions": suggested_actions},
                confidence=0.67,
                trace_id=trace_id,
            )

    async def _create_tool_failure_recovery_plan(
        self,
        *,
        task: dict[str, Any],
        step: dict[str, Any] | None,
        failure_reason: str,
        trace_id: str | None,
    ) -> None:
        recovery = self._failure_recovery.plan(
            task=task,
            step=step,
            failure_reason=failure_reason,
            trace_id=trace_id,
        )
        await self._repo.insert_tool_failure_recovery_plan(recovery.model_dump(mode="json"))
        await self._event(
            task["task_id"],
            "tool.failure_recovery_plan_created",
            {
                "recovery_plan_id": recovery.recovery_plan_id,
                "failure_type": recovery.failure_type,
                "recovery_action": recovery.recovery_action,
                "retry_allowed": recovery.retry_allowed,
            },
            step_id=step["step_id"] if step else None,
            trace_id=trace_id,
        )

    async def _create_reflection_candidate(
        self,
        task: dict[str, Any],
        *,
        candidate_type: str,
        summary: str,
        payload: dict[str, Any],
        confidence: float,
        risk_level: str | RiskLevel = RiskLevel.R1,
        trace_id: str | None,
    ) -> None:
        candidate = {
            "candidate_id": new_id("tcand"),
            "organization_id": task["organization_id"],
            "task_id": task["task_id"],
            "candidate_type": candidate_type,
            "status": "pending_review",
            "confidence": confidence,
            "summary": str(redact(summary))[:500],
            "payload": redact(payload),
            "source_refs": [
                {"type": "task", "task_id": task["task_id"]},
                {"type": "trace", "trace_id": trace_id},
            ],
            "risk_level": risk_level.value if isinstance(risk_level, RiskLevel) else risk_level,
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_task_reflection_candidate(candidate)
        await self._event(
            task["task_id"],
            "task.reflection_candidate_created",
            {
                "candidate_id": candidate["candidate_id"],
                "candidate_type": candidate_type,
                "status": candidate["status"],
            },
            trace_id=trace_id,
        )

    async def _safety_refs_for_step(self, step: dict[str, Any]) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        tool_call_id = step.get("tool_call_id")
        if tool_call_id:
            tool_call = await self._repo.get_tool_call(str(tool_call_id))
            if tool_call and tool_call.get("safety_decision_id"):
                refs.append(
                    {
                        "type": "tool_call",
                        "tool_call_id": tool_call_id,
                        "safety_decision_id": tool_call["safety_decision_id"],
                    }
                )
        output = step.get("output") or {}
        skill_run = output.get("skill_run") if isinstance(output, dict) else None
        if isinstance(skill_run, dict) and skill_run.get("safety_decision_id"):
            refs.append(
                {
                    "type": "skill_run",
                    "skill_run_id": skill_run.get("skill_run_id"),
                    "safety_decision_id": skill_run["safety_decision_id"],
                }
            )
        mcp_call = output.get("mcp_call") if isinstance(output, dict) else None
        if isinstance(mcp_call, dict) and mcp_call.get("safety_decision_id"):
            refs.append(
                {
                    "type": "mcp_call",
                    "mcp_call_id": mcp_call.get("mcp_call_id"),
                    "safety_decision_id": mcp_call["safety_decision_id"],
                }
            )
        return refs

    async def _resolve_code_hosting_skill_binding(
        self,
        request: TaskCreateRequest,
        *,
        trace_id: str | None,
    ) -> dict[str, Any]:
        query = str(request.constraints.get("code_hosting_skill_query") or "github").strip() or (
            "github"
        )
        if (
            self._skills is None
            or self._skill_governance is None
            or self._skill_repositories is None
        ):
            return {
                "enabled": True,
                "ready": False,
                "provider_type": "github",
                "repository_id": "clawhub",
                "query": query,
                "selection_reason": "code_hosting_services_unavailable",
                "blocked_reason": "code_hosting_services_unavailable",
            }
        try:
            entries = await self._skill_repositories.search(
                query=query,
                repository_id="clawhub",
                limit=20,
            )
        except Exception as exc:
            return {
                "enabled": True,
                "ready": False,
                "provider_type": "github",
                "repository_id": "clawhub",
                "query": query,
                "selection_reason": "clawhub_catalog_search_failed",
                "blocked_reason": str(redact(str(exc)))[:160],
            }
        if not entries:
            return {
                "enabled": True,
                "ready": False,
                "provider_type": "github",
                "repository_id": "clawhub",
                "query": query,
                "selection_reason": "clawhub_catalog_search_empty",
                "blocked_reason": "clawhub_catalog_search_empty",
            }
        selected = entries[0]
        package_ref = str(selected.package_ref)
        source_uri = f"{selected.repository_id}:{package_ref}"
        try:
            bundle, skills, _preview = await self._skills.install_bundle(
                BundleInstallRequest(
                    source_type="repository_ref",
                    source_uri=source_uri,
                    requested_by_member_id=request.owner_member_id,
                    idempotency_key=f"phase98:code_hosting:{source_uri}",
                ),
                trace_id=trace_id,
            )
            if bundle.status != "enabled":
                bundle = await self._skills.enable_bundle(
                    bundle.bundle_id,
                    actor_member_id=request.owner_member_id,
                    trace_id=trace_id,
                )
            bound_skill = skills[0] if skills else None
            if bound_skill is None:
                return {
                    "enabled": True,
                    "ready": False,
                    "provider_type": "github",
                    "repository_id": selected.repository_id,
                    "package_ref": package_ref,
                    "bundle_id": bundle.bundle_id,
                    "selection_reason": "clawhub_catalog_search_first_result",
                    "blocked_reason": "clawhub_package_has_no_skills",
                }
            skill = await self._skills.get_skill(bound_skill.skill_id)
            grants = await self._skill_governance.list_grants(skill.skill_id)
            active_grants = [
                item
                for item in grants
                if item.status == "active" and item.subject_id == request.owner_member_id
            ]
            if not active_grants:
                await self._skill_governance.create_grant(
                    skill.skill_id,
                    SkillGrantCreateRequest(
                        subject_id=request.owner_member_id,
                        created_by_member_id=request.owner_member_id,
                    ),
                    trace_id=trace_id,
                )
            return {
                "enabled": True,
                "ready": True,
                "provider_type": "github",
                "repository_id": selected.repository_id,
                "package_ref": package_ref,
                "bundle_id": bundle.bundle_id,
                "skill_id": skill.skill_id,
                "selection_reason": "clawhub_catalog_search_first_result",
                "query": query,
            }
        except Exception as exc:
            return {
                "enabled": True,
                "ready": False,
                "provider_type": "github",
                "repository_id": selected.repository_id,
                "package_ref": package_ref,
                "bundle_id": getattr(selected, "bundle_id", None),
                "selection_reason": "clawhub_catalog_search_first_result",
                "blocked_reason": str(getattr(exc, "code", ErrorCode.SKILL_RUN_FAILED.value)),
                "error_summary": str(redact(str(exc)))[:160],
            }

    async def _plan(
        self,
        task_id: str,
        request: TaskCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> TaskPlan:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.PLANNER_SELECT,
            "select task planner",
            input_data={
                "task_id": task_id,
                "goal": redact(request.goal),
                "mode_hint": request.mode_hint.value if request.mode_hint else None,
            },
        )
        mode = _select_mode(request)
        risk = _risk_for_goal(request.goal)
        budget = TaskBudget(**{**TaskBudget().model_dump(), **request.budget_override})
        resolved_constraints = dict(request.constraints or {})
        code_hosting_profile = _code_hosting_profile_for_request(request)
        code_hosting_binding = (
            await self._resolve_code_hosting_skill_binding(request, trace_id=trace_id)
            if code_hosting_profile["enabled"]
            else {"enabled": False, "ready": False}
        )
        for key in (
            "package_ref",
            "bundle_id",
            "skill_id",
            "selection_reason",
            "provider_type",
        ):
            value = code_hosting_binding.get(key)
            if value:
                resolved_constraints[f"code_hosting_{key}"] = value
        planning_request = (
            request.model_copy(update={"constraints": resolved_constraints})
            if resolved_constraints != dict(request.constraints or {})
            else request
        )
        capability_snapshot = await self._capability_snapshot(planning_request, trace_id=trace_id)
        repo_profile = _repo_profile_for_request(planning_request)
        steps = (
            _code_hosting_steps_for_request(
                planning_request,
                repo_profile,
                code_hosting_profile,
                code_hosting_binding,
            )
            if code_hosting_profile["enabled"]
            else _repo_steps_for_request(planning_request, repo_profile)
            if repo_profile["enabled"]
            else _steps_for_goal(planning_request, mode, capability_snapshot)
        )
        planner_reason_codes = _planner_reason_codes(planning_request, mode, risk)
        planner_reason_codes.extend(capability_snapshot.get("reason_codes", []))
        if repo_profile["enabled"]:
            planner_reason_codes.extend(
                [
                    "phase97_repo_execution_profile",
                    f"repo_request_type_{repo_profile['request_type']}",
                ]
            )
        if code_hosting_profile["enabled"]:
            planner_reason_codes.extend(
                [
                    "phase98_code_hosting_profile",
                    f"code_hosting_request_type_{code_hosting_profile['request_type']}",
                    str(code_hosting_binding.get("selection_reason") or "code_hosting_binding"),
                ]
            )
        blocked_actions: list[dict[str, Any]] = []

        if planning_request.constraints.get("mcp_tool_name") and not capability_snapshot.get(
            "mcp_tool_refs"
        ):
            steps = [step for step in steps if step.get("step_key") != "mcp_call"]
            planner_reason_codes.append("mcp_tool_unavailable_removed_from_plan")
            blocked_actions.append(
                {
                    "type": "mcp_call",
                    "tool_name": planning_request.constraints.get("mcp_tool_name"),
                    "reason": "mcp_tool_not_ready_or_not_active",
                    "execution_created": False,
                }
            )
        if planning_request.constraints.get("skill_id") and not capability_snapshot.get(
            "explicit_skill_available",
            False,
        ):
            steps = [step for step in steps if step.get("step_key") != "skill_run"]
            planner_reason_codes.append("skill_unavailable_removed_from_plan")
            blocked_actions.append(
                {
                    "type": "skill_run",
                    "skill_id": planning_request.constraints.get("skill_id"),
                    "reason": "skill_not_enabled_or_bundle_unavailable",
                    "execution_created": False,
                }
            )
        if code_hosting_profile["enabled"] and not code_hosting_binding.get("ready"):
            blocked_actions.append(
                {
                    "type": "skill_run",
                    "skill_id": resolved_constraints.get("code_hosting_skill_id"),
                    "reason": str(
                        code_hosting_binding.get("blocked_reason")
                        or "code_hosting_skill_binding_unavailable"
                    ),
                    "execution_created": False,
                }
            )

        required_capabilities = _required_capabilities_for_steps(steps)
        required_assets = list(planning_request.resource_handle_ids)
        required_approvals = [step for step in steps if _risk_order(step["risk_level"]) >= 3]
        preflight = {
            "required_handles": planning_request.resource_handle_ids,
            "required_approvals": required_approvals,
            "blocked_actions": blocked_actions,
            "capability_snapshot": capability_snapshot,
            "skill_match_refs": capability_snapshot.get("skill_match_refs", []),
            "mcp_tool_refs": capability_snapshot.get("mcp_tool_refs", []),
            "planner_context": redact(planning_request.planner_context),
            "brain_decision_id": planning_request.brain_decision_id,
            "repo_execution": repo_profile,
            "code_hosting": {
                **code_hosting_profile,
                "skill_binding": code_hosting_binding,
            },
        }
        planner_type = _planner_type(mode)
        assumptions = _planner_assumptions(planning_request, mode, capability_snapshot)
        await self._end_span(
            span_id,
            output_data={
                "planner_type": planner_type,
                "mode": mode.value,
                "reason_codes": planner_reason_codes,
                "step_count": len(steps),
            },
        )
        return TaskPlan(
            task_id=task_id,
            title=_title_from_goal(request.goal),
            goal=request.goal,
            mode=mode,
            owner_member_id=request.owner_member_id,
            host_member_id=request.owner_member_id if mode == TaskMode.SUPERVISOR else None,
            success_criteria=request.success_criteria or ["任务产生可回放结果"],
            constraints=redact(resolved_constraints),
            assumptions=assumptions,
            required_capabilities=required_capabilities,
            required_assets=required_assets,
            approval_strategy={
                "strategy": "plan_first_then_step_gate" if required_approvals else "step_gate",
                "required_before_execution": bool(required_approvals),
                "high_risk_step_keys": [step["step_key"] for step in required_approvals],
            },
            steps=steps,
            risk_level=risk,
            budget=budget,
            checkpoint_policy={
                "checkpoint_after_each_step": mode == TaskMode.AGENT,
                "persist_observations": mode == TaskMode.AGENT,
            },
            failure_policy={
                "on_step_failure": "pause_and_create_retry_plan"
                if mode == TaskMode.AGENT
                else "fail_task",
                "allow_alternate_path_without_approval": False,
            },
            reflection_policy={
                "candidate_only": True,
                "candidate_types": [
                    "memory_candidate",
                    "workflow_template_candidate",
                    "skill_candidate",
                    "failure_pattern_candidate",
                ],
            },
            planner_type=planner_type,
            planner_reason_codes=planner_reason_codes,
            preflight=preflight,
            artifact_plan={
                "expected_outputs": ["collaboration_report"]
                if mode == TaskMode.SUPERVISOR
                else ["task_report"]
            },
        )

    async def _capability_snapshot(
        self,
        request: TaskCreateRequest,
        *,
        trace_id: str | None,
    ) -> dict[str, Any]:
        reason_codes: list[str] = []
        skill_match_refs: list[dict[str, Any]] = []
        mcp_tool_refs: list[dict[str, Any]] = []
        explicit_skill_available = False
        explicit_skill_state = "missing"
        enabled_skill_count = 0
        ready_mcp_server_count = 0
        active_mcp_tool_count = 0

        if self._skills is None:
            if _goal_mentions_skill(request.goal) or request.constraints.get("skill_id"):
                reason_codes.append("skill_engine_unavailable")
        else:
            try:
                enabled_skills = await self._skills.list_skills(status="enabled")
                enabled_skill_count = len(enabled_skills)
                explicit_skill_id = request.constraints.get("skill_id")
                explicit_skill_available = any(
                    item.skill_id == explicit_skill_id and item.status == "enabled"
                    for item in enabled_skills
                )
                if explicit_skill_available:
                    explicit_skill_state = "enabled"
                if explicit_skill_id and not explicit_skill_available:
                    try:
                        explicit_skill = await self._skills.get_skill(str(explicit_skill_id))
                        explicit_bundle = await self._skills.get_bundle(explicit_skill.bundle_id)
                        explicit_skill_available = (
                            explicit_skill.status == "enabled"
                            and explicit_bundle.status == "enabled"
                        )
                        if explicit_skill_available:
                            explicit_skill_state = "enabled"
                            enabled_skill_count = max(enabled_skill_count, 1)
                        elif explicit_skill.status != "enabled":
                            explicit_skill_state = "skill_disabled"
                        elif explicit_bundle.status != "enabled":
                            explicit_skill_state = "bundle_disabled"
                        else:
                            explicit_skill_state = "not_listed"
                    except Exception:
                        explicit_skill_available = False
                        explicit_skill_state = "missing"
                if explicit_skill_id and not explicit_skill_available:
                    reason_codes.append(
                        {
                            "skill_disabled": "skill_explicit_skill_disabled",
                            "bundle_disabled": "skill_explicit_bundle_disabled",
                            "missing": "skill_explicit_missing",
                            "not_listed": "skill_explicit_not_listed",
                        }.get(explicit_skill_state, "skill_no_enabled_skill")
                    )
                matches = await self._skills.match_skills(
                    SkillMatchRequest(
                        owner_member_id=request.owner_member_id,
                        conversation_id=request.conversation_id,
                        task_id=None,
                        intent="task_planning",
                        goal=request.goal,
                        resource_handle_ids=request.resource_handle_ids,
                    ),
                    trace_id=trace_id,
                )
                skill_match_refs = [
                    {
                        "skill_id": match.skill_id,
                        "bundle_id": match.bundle_id,
                        "confidence": match.confidence,
                        "reason": match.reason,
                    }
                    for match in matches[:3]
                ]
                if _goal_mentions_skill(request.goal) and not skill_match_refs:
                    reason_codes.append("skill_no_enabled_skill")
            except Exception as exc:
                reason_codes.append("skill_snapshot_failed")
                skill_match_refs = [{"error": str(redact(str(exc)))[:160]}]

        if self._mcp is None:
            if _goal_mentions_mcp(request.goal) or request.constraints.get("mcp_tool_name"):
                reason_codes.append("mcp_no_ready_server")
        else:
            try:
                servers = await self._mcp.list_servers()
                ready_servers = [server for server in servers if server.status == "ready"]
                ready_mcp_server_count = len(ready_servers)
                requested_tool = request.constraints.get("mcp_tool_name")
                for server in ready_servers:
                    for tool in await self._mcp.list_tools(server.server_id):
                        if tool.status in {"active", "approval_required"}:
                            active_mcp_tool_count += 1
                            if requested_tool and requested_tool in {
                                tool.tool_name,
                                tool.registry_tool_name,
                            }:
                                mcp_tool_refs.append(
                                    {
                                        "server_id": server.server_id,
                                        "mcp_tool_id": tool.mcp_tool_id,
                                        "tool_name": tool.tool_name,
                                        "status": tool.status,
                                    }
                                )
                if (_goal_mentions_mcp(request.goal) or requested_tool) and not ready_servers:
                    reason_codes.append("mcp_no_ready_server")
                elif (
                    _goal_mentions_mcp(request.goal) or requested_tool
                ) and active_mcp_tool_count == 0:
                    reason_codes.append("mcp_no_active_tool")
                elif requested_tool and not mcp_tool_refs:
                    reason_codes.append("mcp_tool_not_active_or_not_found")
            except Exception as exc:
                reason_codes.append("mcp_snapshot_failed")
                mcp_tool_refs = [{"error": str(redact(str(exc)))[:160]}]

        return {
            "enabled_skill_count": enabled_skill_count,
            "ready_mcp_server_count": ready_mcp_server_count,
            "active_mcp_tool_count": active_mcp_tool_count,
            "explicit_skill_available": explicit_skill_available,
            "explicit_skill_state": explicit_skill_state,
            "skill_match_refs": skill_match_refs,
            "mcp_tool_refs": mcp_tool_refs,
            "reason_codes": sorted(set(reason_codes)),
            "model_assist": {"enabled": False, "reason": "rule_first_planner"},
        }

    async def _transition_task(
        self,
        task_id: str,
        target: str,
        *,
        trace_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        task = await self._get_task(task_id)
        ensure_task_transition(task["status"], target)
        fields = {"status": target, "updated_at": utc_now_iso(), **(extra or {})}
        await self._repo.update_task(task_id, fields)
        await self._event(task_id, f"task.{target}", {"status": target}, trace_id=trace_id)

    async def _mark_run_job(
        self,
        task_id: str,
        status: str,
        *,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        now = utc_now_iso()
        await self._repo.update_job_by_idempotency(
            f"task.run:{task_id}",
            {
                "status": status,
                "locked_by": "local-api" if status == "running" else None,
                "locked_at": now if status == "running" else None,
                "error_code": error_code,
                "error_summary": error_summary,
                "updated_at": now,
            },
        )

    async def _sync_run_job_to_task(self, task_id: str) -> None:
        task = await self._get_task(task_id)
        status = task["status"]
        error_code = None
        error_summary = None
        if status == TaskStatus.FAILED.value:
            error_code = ErrorCode.TASK_STEP_FAILED.value
            error_summary = task.get("failure_reason")
        await self._mark_run_job(
            task_id,
            status,
            error_code=error_code,
            error_summary=error_summary,
        )

    async def _update_progress(self, task_id: str) -> None:
        steps = await self._repo.list_steps(task_id)
        completed = len([step for step in steps if step["status"] == "completed"])
        current = next((step["step_key"] for step in steps if step["status"] != "completed"), None)
        await self._repo.update_task(
            task_id,
            {
                "progress": {
                    "total_steps": len(steps),
                    "completed_steps": completed,
                    "current_step_key": current,
                },
                "updated_at": utc_now_iso(),
            },
        )

    async def _event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        step_id: str | None = None,
        trace_id: str | None = None,
        created_at: str | None = None,
    ) -> None:
        await self._repo.insert_event(
            {
                "event_id": new_id("tevt"),
                "organization_id": "org_default",
                "task_id": task_id,
                "step_id": step_id,
                "event_type": event_type,
                "payload": payload,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": created_at or utc_now_iso(),
            }
        )

    async def _summary(self, task: dict[str, Any]) -> TaskSummary:
        artifacts = await self._repo.list_artifacts(task["task_id"])
        return TaskSummary(
            task_id=task["task_id"],
            organization_id=task["organization_id"],
            conversation_id=task.get("conversation_id"),
            owner_member_id=task.get("owner_member_id"),
            parent_task_id=task.get("parent_task_id"),
            host_member_id=task.get("host_member_id"),
            collaboration_plan_id=task.get("collaboration_plan_id"),
            supervisor_mode=task.get("supervisor_mode"),
            title=task["title"],
            goal=task["goal"],
            mode=TaskMode(task["mode"]),
            status=TaskStatus(task["status"]),
            risk_level=RiskLevel(task["risk_level"]),
            success_criteria=task.get("success_criteria", []),
            progress=task.get("progress", {}),
            current_approval_id=task.get("current_approval_id"),
            artifact_count=len(artifacts),
            failure_reason=task.get("failure_reason"),
            cancellation_reason=task.get("cancellation_reason"),
            trace_id=task.get("trace_id"),
            created_at=task.get("created_at"),
            updated_at=task.get("updated_at"),
        )

    async def _get_task(self, task_id: str) -> dict[str, Any]:
        task = await self._repo.get_task(task_id)
        if task is None:
            raise AppError(ErrorCode.NOT_FOUND, "任务不存在", status_code=404)
        return task

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        input_data: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            input_data=input_data,
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                status=status,
                output_data=redact(output_data or {}),
            )


def _select_mode(request: TaskCreateRequest) -> TaskMode:
    if request.mode_hint is not None:
        return request.mode_hint
    if _code_hosting_profile_for_request(request)["enabled"]:
        return TaskMode.AGENT
    if _repo_profile_for_request(request)["enabled"]:
        return TaskMode.AGENT
    text = request.goal.lower()
    if any(word in text for word in ["调研", "研究", "搜索", "竞品", "网页", "research"]):
        return TaskMode.AGENT
    if any(word in text for word in ["多成员", "协作", "共同", "一起", "supervisor"]):
        return TaskMode.SUPERVISOR
    return TaskMode.WORKFLOW


def _planner_type(mode: TaskMode) -> str:
    if mode == TaskMode.SUPERVISOR:
        return "supervisor_planner"
    if mode == TaskMode.AGENT:
        return "agent_exploratory_planner"
    if mode == TaskMode.WORKFLOW:
        return "workflow_template_planner"
    return "rule_planner"


def _planner_reason_codes(
    request: TaskCreateRequest,
    mode: TaskMode,
    risk: RiskLevel,
) -> list[str]:
    reasons = [f"mode_{mode.value}", "rule_first_planner"]
    if request.mode_hint is not None:
        reasons.append("explicit_mode_hint")
    if _risk_order(risk.value) >= 5:
        reasons.append("high_risk_plan_first")
    if _goal_mentions_skill(request.goal) or request.constraints.get("skill_id"):
        reasons.append("skill_considered")
    if _code_hosting_profile_for_request(request)["enabled"]:
        reasons.append("code_hosting_considered")
    if _goal_mentions_mcp(request.goal) or request.constraints.get("mcp_tool_name"):
        reasons.append("mcp_considered")
    if request.brain_decision_id:
        reasons.append("brain_decision_linked")
    if request.resource_handle_ids:
        reasons.append("asset_handles_declared")
    return reasons


def _planner_assumptions(
    request: TaskCreateRequest,
    mode: TaskMode,
    capability_snapshot: dict[str, Any],
) -> list[str]:
    assumptions = ["规则优先 planner；未启用模型辅助规划。"]
    repo_profile = _repo_profile_for_request(request)
    code_hosting_profile = _code_hosting_profile_for_request(request)
    if repo_profile["enabled"]:
        assumptions.append("代码仓任务以 Task/Agent 主链为权威执行入口。")
        if repo_profile.get("requires_verification"):
            assumptions.append("出现代码改动时默认必须完成目标验证。")
        if repo_profile.get("allow_auto_repair"):
            assumptions.append("验证失败后只允许一次局部自动修复。")
    if code_hosting_profile["enabled"]:
        assumptions.append("远程工程协作通过 ClawHub 中的 GitHub Skill 进入受控 Skill 主链。")
        assumptions.append("若未绑定到可用 GitHub Skill，任务只会留下阻断证据，不会伪造远程执行。")
    if mode == TaskMode.AGENT:
        assumptions.append("Agent loop 只按预算执行当前任务步骤，不做后台自主动作。")
    if not request.resource_handle_ids:
        assumptions.append("未声明资产句柄，步骤只能使用无需资产的能力。")
    if _goal_mentions_mcp(request.goal) and capability_snapshot.get("ready_mcp_server_count") == 0:
        assumptions.append("MCP 当前无 ready server，计划不会伪造 MCP 执行。")
    if _goal_mentions_skill(request.goal) and capability_snapshot.get("enabled_skill_count") == 0:
        assumptions.append("当前无 enabled Skill，计划不会伪造 Skill 执行。")
    if _goal_mentions_office(request.goal):
        assumptions.append("Office 文件请求只通过受控 Office 工具写入任务 artifact。")
    return assumptions


def _required_capabilities_for_steps(steps: list[dict[str, Any]]) -> list[str]:
    capabilities: list[str] = []
    for step in steps:
        step_type = step.get("step_type")
        if step_type == "tool_call":
            tool_name = step.get("input", {}).get("tool_name")
            capabilities.append(f"tool:{tool_name}" if tool_name else "tool")
        elif step_type == "mcp_call":
            tool_name = step.get("input", {}).get("tool_name")
            capabilities.append(f"mcp:{tool_name}" if tool_name else "mcp")
        elif step_type == "skill_run":
            skill_id = step.get("input", {}).get("skill_id")
            capabilities.append(f"skill:{skill_id}" if skill_id else "skill")
        elif step_type == "skill_match":
            capabilities.append("skill_match")
    return sorted(set(capabilities))


def _goal_mentions_skill(goal: str) -> bool:
    lowered = goal.lower()
    return any(word in lowered for word in ["skill", "技能", "插件", "流程"]) or (
        _goal_mentions_office(goal)
    )


def _goal_mentions_office(goal: str) -> bool:
    lowered = goal.lower()
    return any(
        word in lowered
        for word in [
            "word",
            "docx",
            "excel",
            "xlsx",
            "ppt",
            "pptx",
            "powerpoint",
            "文档",
            "表格",
            "演示稿",
            "周报",
            "项目周报",
        ]
    )


def _goal_mentions_mcp(goal: str) -> bool:
    lowered = goal.lower()
    return any(word in lowered for word in ["mcp", "外部工具", "server", "服务器工具"])


def _risk_for_goal(goal: str) -> RiskLevel:
    lowered = goal.lower()
    if any(word in lowered for word in ["删除", "delete", "terminal", "终端", "命令"]):
        return RiskLevel.R5
    if any(word in lowered for word in ["发布", "发帖", "发送", "提交", "publish"]):
        return RiskLevel.R4
    if any(word in lowered for word in ["浏览器", "overwrite"]) or (
        is_explicit_download_request(goal)
    ):
        return RiskLevel.R3
    return RiskLevel.R2


def _steps_for_goal(
    request: TaskCreateRequest,
    mode: TaskMode,
    capability_snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if mode == TaskMode.SUPERVISOR:
        return [
            {
                "step_key": "supervisor_collaboration",
                "step_type": "compose",
                "title": "执行多成员协作计划",
                "risk_level": "R1",
                "input": {},
            }
        ]
    steps: list[dict[str, Any]] = []
    goal = request.goal.lower()
    skill_match_refs = (
        capability_snapshot.get("skill_match_refs", []) if capability_snapshot is not None else []
    )
    office_skill_id = _office_skill_id_for_goal(request.goal, skill_match_refs)
    if request.constraints.get("skill_id"):
        steps.append(
            {
                "step_key": "skill_run",
                "step_type": "skill_run",
                "title": "执行匹配 Skill",
                "risk_level": "R2",
                "input": {
                    "skill_id": request.constraints["skill_id"],
                    "input": request.constraints.get("skill_input") or {"goal": request.goal},
                },
            }
        )
    elif office_skill_id:
        steps.append(
            {
                "step_key": "skill_run",
                "step_type": "skill_run",
                "title": "执行 Office Skill",
                "risk_level": "R2",
                "input": {
                    "skill_id": office_skill_id,
                    "input": _office_skill_input(request),
                    "matched_reason": "office_intent_route",
                    "confidence": _office_skill_confidence(office_skill_id, skill_match_refs),
                },
            }
        )
    elif any(word in goal for word in ["skill", "技能", "插件", "流程"]) or _goal_mentions_office(
        request.goal
    ):
        steps.append(
            {
                "step_key": "skill_match",
                "step_type": "skill_match",
                "title": "匹配可用 Skill",
                "risk_level": "R1",
                "input": {"goal": request.goal, "intent": "task_execution"},
            }
        )
    if request.constraints.get("mcp_tool_name"):
        steps.append(
            {
                "step_key": "mcp_call",
                "step_type": "mcp_call",
                "title": "调用 MCP 工具",
                "risk_level": "R2",
                "input": {
                    "tool_name": request.constraints["mcp_tool_name"],
                    "args": request.constraints.get("mcp_args") or {},
                },
            }
        )
    if any(word in goal for word in ["知识", "搜索", "调研", "research", "网页"]):
        steps.append(
            {
                "step_key": "knowledge_search",
                "step_type": "tool_call",
                "title": "检索知识库",
                "risk_level": "R1",
                "input": {
                    "tool_name": "knowledge.search",
                    "args": {"query": request.goal, "limit": 5},
                },
            }
        )
    if any(word in goal for word in ["终端", "terminal", "命令"]):
        steps.append(
            {
                "step_key": "terminal_run",
                "step_type": "tool_call",
                "title": "执行终端命令",
                "risk_level": "R5",
                "input": {
                    "tool_name": "terminal.run",
                    "args": {"command": request.constraints.get("command") or "echo task"},
                },
            }
        )
    has_delete_request = any(word in goal for word in ["删除", "delete", "删掉"])
    if is_explicit_download_request(request.goal) and not has_delete_request:
        steps.append(
            {
                "step_key": "browser_download",
                "step_type": "tool_call",
                "title": "下载文件",
                "risk_level": "R3",
                "input": {
                    "tool_name": "browser.download",
                    "args": {
                        "url": _first_url(request.goal),
                        "display_name": _download_display_name(request.goal),
                    },
                },
            }
        )
    if any(word in goal for word in ["截图", "screenshot"]):
        steps.append(
            {
                "step_key": "browser_screenshot",
                "step_type": "tool_call",
                "title": "保存页面截图",
                "risk_level": "R3",
                "input": {
                    "tool_name": "browser.screenshot",
                    "args": {"url": _first_url(request.goal) or "http://127.0.0.1/"},
                },
            }
        )
    if has_delete_request:
        steps.append(
            {
                "step_key": "file_delete",
                "step_type": "tool_call",
                "title": "删除文件",
                "risk_level": "R5",
                "input": {
                    "tool_name": "file.delete",
                    "args": {"path": request.constraints.get("path") or "outputs/target.txt"},
                },
            }
        )
    steps.append(
        {
            "step_key": "compose_report",
            "step_type": "compose",
            "title": "生成任务报告",
            "risk_level": "R1",
            "input": {},
        }
    )
    return steps


def _code_hosting_profile_for_request(request: TaskCreateRequest) -> dict[str, Any]:
    request_type = _code_hosting_request_type(request)
    enabled = request_type is not None
    return {
        "enabled": enabled,
        "request_type": request_type,
        "provider_type": str(request.constraints.get("forge_provider_type") or "github"),
        "tracks_workspace": enabled,
    }


def _code_hosting_request_type(request: TaskCreateRequest) -> str | None:
    explicit = str(request.constraints.get("code_hosting_request_type") or "").strip()
    if explicit in {
        "code_hosting_readonly_request",
        "code_hosting_sync_request",
        "code_hosting_pr_request",
        "code_hosting_review_request",
        "code_hosting_release_request",
    }:
        return explicit
    planner_context = dict(request.planner_context or {})
    route_intent = str(planner_context.get("route_intent") or planner_context.get("intent") or "")
    if route_intent in {
        "code_hosting_readonly_request",
        "code_hosting_sync_request",
        "code_hosting_pr_request",
        "code_hosting_review_request",
        "code_hosting_release_request",
    }:
        return route_intent
    text = f"{request.goal} {' '.join(str(item) for item in request.success_criteria)}".lower()
    has_provider = any(marker in text for marker in ["github", "代码托管", "远程仓库", "github.com"])
    has_hosting_action = any(
        marker in text
        for marker in ["pull request", "merge", "release", "push", "branch", "issue", "sync"]
    ) or bool(re.search(r"\bpr\b", text))
    if not has_provider and not has_hosting_action:
        return None
    if any(marker in text for marker in ["状态", "看看", "查看", "list", "read-only", "readonly", "只读"]):
        return "code_hosting_readonly_request"
    if any(marker in text for marker in ["release", "发布", "release note"]):
        return "code_hosting_release_request"
    if any(marker in text for marker in ["review", "评审", "审查", "comment", "评论"]):
        return "code_hosting_review_request"
    if any(marker in text for marker in ["pull request", "pr", "合并请求"]):
        return "code_hosting_pr_request"
    if any(marker in text for marker in ["push", "branch", "同步", "sync", "merge"]):
        return "code_hosting_sync_request"
    if any(marker in text for marker in ["github", "issue", "只读", "readonly", "read only"]):
        return "code_hosting_readonly_request"
    return None


def _code_hosting_steps_for_request(
    request: TaskCreateRequest,
    repo_profile: dict[str, Any],
    code_hosting_profile: dict[str, Any],
    code_hosting_binding: dict[str, Any],
) -> list[dict[str, Any]]:
    if repo_profile.get("enabled"):
        steps = _repo_steps_for_request(request, repo_profile)
        steps = [step for step in steps if step.get("step_key") != "compose_report"]
    else:
        steps = [
            {
                "step_key": "repo_explore",
                "step_type": "tool_call",
                "title": "扫描代码仓工作区",
                "risk_level": "R1",
                "input": {"tool_name": "file.list", "args": {"path": "."}},
                "metadata": {
                    "repo_step_role": "explore",
                    "code_hosting_step_role": "local_state",
                },
            }
        ]
    skill_id = str(request.constraints.get("code_hosting_skill_id") or "").strip()
    if code_hosting_binding.get("ready") and skill_id:
        steps.append(
            {
                "step_key": "code_hosting_skill_run",
                "step_type": "skill_run",
                "title": "执行 Git 托管协作 Skill",
                "risk_level": _code_hosting_risk_level(code_hosting_profile.get("request_type")),
                "input": {
                    "skill_id": skill_id,
                    "input": _code_hosting_skill_input(request),
                    "matched_reason": str(
                        code_hosting_binding.get("selection_reason")
                        or "code_hosting_clawhub_binding"
                    ),
                    "confidence": 1.0,
                },
                "metadata": {
                    "code_hosting_step_role": "remote_execution",
                    "code_hosting_request_type": code_hosting_profile.get("request_type"),
                },
            }
        )
    steps.append(
        {
            "step_key": "compose_report",
            "step_type": "compose",
            "title": "生成远程工程协作结果摘要",
            "risk_level": "R1",
            "input": {},
            "metadata": {"code_hosting_step_role": "summarize"},
        }
    )
    return steps


def _repo_profile_for_request(request: TaskCreateRequest) -> dict[str, Any]:
    request_type = _repo_request_type(request)
    enabled = request_type is not None
    verification_scope = str(
        request.constraints.get("verification_scope")
        or ("targeted" if enabled else "")
    ).strip() or "targeted"
    requires_approval = bool(request.constraints.get("requires_approval"))
    allow_write = request_type in {
        "repo_patch_request",
        "repo_fix_after_failure",
        "repo_refactor_request",
    }
    requires_verification = request_type in {
        "repo_patch_request",
        "repo_test_request",
        "repo_fix_after_failure",
        "repo_refactor_request",
    }
    return {
        "enabled": enabled,
        "request_type": request_type,
        "allow_write": allow_write,
        "requires_verification": requires_verification,
        "allow_auto_repair": enabled and request_type == "repo_fix_after_failure",
        "requires_approval": requires_approval,
        "verification_scope": verification_scope,
    }


def _repo_request_type(request: TaskCreateRequest) -> str | None:
    explicit = str(request.constraints.get("repo_request_type") or "").strip()
    if explicit in {
        "repo_readonly_request",
        "repo_patch_request",
        "repo_test_request",
        "repo_fix_after_failure",
        "repo_refactor_request",
    }:
        return explicit
    planner_context = dict(request.planner_context or {})
    route_intent = str(planner_context.get("route_intent") or planner_context.get("intent") or "")
    if route_intent in {
        "repo_readonly_request",
        "repo_patch_request",
        "repo_test_request",
        "repo_fix_after_failure",
        "repo_refactor_request",
    }:
        return route_intent
    text = f"{request.goal} {' '.join(str(item) for item in request.success_criteria)}".lower()
    if not any(
        marker in text
        for marker in [
            "repo",
            "仓库",
            "代码",
            "测试",
            "pytest",
            "bug",
            "修复",
            "refactor",
            "补丁",
            "patch",
            "代码库",
        ]
    ):
        return None
    if any(marker in text for marker in ["只读", "read only", "readonly", "阅读代码", "看代码"]):
        return "repo_readonly_request"
    if any(marker in text for marker in ["修复失败", "fix after failure", "失败后修", "修复测试"]):
        return "repo_fix_after_failure"
    if any(marker in text for marker in ["重构", "refactor"]):
        return "repo_refactor_request"
    if any(marker in text for marker in ["测试", "pytest", "验证", "typecheck", "lint"]):
        return "repo_test_request"
    if any(marker in text for marker in ["修改", "补丁", "patch", "写代码", "改代码", "bugfix", "修复"]):
        return "repo_patch_request"
    return None


def _repo_steps_for_request(
    request: TaskCreateRequest,
    repo_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    constraints = dict(request.constraints or {})
    steps: list[dict[str, Any]] = [
        {
            "step_key": "repo_explore",
            "step_type": "tool_call",
            "title": "扫描代码仓工作区",
            "risk_level": "R1",
            "input": {"tool_name": "file.list", "args": {"path": "."}},
            "metadata": {"repo_step_role": "explore"},
        }
    ]
    patch_path = str(constraints.get("repo_patch_path") or "").strip()
    patch_content = constraints.get("repo_patch_content")
    if repo_profile.get("allow_write") and patch_path and patch_content is not None:
        steps.append(
            {
                "step_key": "repo_patch_apply",
                "step_type": "tool_call",
                "title": "应用代码修改",
                "risk_level": "R2",
                "input": {
                    "tool_name": "file.write",
                    "args": {
                        "path": patch_path,
                        "content": str(patch_content),
                        "overwrite": True,
                    },
                },
                "metadata": {"repo_step_role": "patch"},
            }
        )
    extra_writes = constraints.get("repo_patch_writes") or []
    if isinstance(extra_writes, list):
        for index, item in enumerate(extra_writes, start=1):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            steps.append(
                {
                    "step_key": f"repo_patch_apply_{index}",
                    "step_type": "tool_call",
                    "title": f"应用代码修改 {index}",
                    "risk_level": "R2",
                    "input": {
                        "tool_name": "file.write",
                        "args": {
                            "path": path,
                            "content": str(item.get("content") or ""),
                            "overwrite": True,
                        },
                    },
                    "metadata": {"repo_step_role": "patch"},
                }
            )
    verify_read_path = str(constraints.get("verify_read_path") or "").strip()
    verify_contains_text = constraints.get("verify_contains_text")
    verify_command = str(constraints.get("verify_command") or "").strip()
    if (
        repo_profile.get("requires_verification")
        and verify_read_path
        and verify_contains_text is not None
    ):
        steps.append(
            {
                "step_key": "repo_verify_targeted",
                "step_type": "tool_call",
                "title": "运行目标验证",
                "risk_level": "R1",
                "input": {
                    "tool_name": "file.read",
                    "args": {
                        "path": verify_read_path,
                    },
                },
                "metadata": {
                    "repo_step_role": "verify",
                    "repo_verify_kind": str(constraints.get("verify_kind") or "test"),
                    "repo_verify_mode": "content_contains",
                    "repo_verify_expected_text": str(verify_contains_text),
                    "repo_repair_path": constraints.get("repair_patch_path"),
                    "repo_repair_content": constraints.get("repair_patch_content"),
                    "repo_repair_writes": constraints.get("repair_patch_writes") or [],
                },
            }
        )
    elif repo_profile.get("requires_verification") and verify_command:
        steps.append(
            {
                "step_key": "repo_verify_targeted",
                "step_type": "tool_call",
                "title": "运行目标验证",
                "risk_level": "R2",
                "input": {
                    "tool_name": "terminal.run",
                    "args": {
                        "command": verify_command,
                    },
                },
                "metadata": {
                    "repo_step_role": "verify",
                    "repo_verify_kind": str(constraints.get("verify_kind") or "test"),
                    "repo_repair_path": constraints.get("repair_patch_path"),
                    "repo_repair_content": constraints.get("repair_patch_content"),
                    "repo_repair_writes": constraints.get("repair_patch_writes") or [],
                },
            }
        )
    steps.append(
        {
            "step_key": "compose_report",
            "step_type": "compose",
            "title": "生成工程代理结果摘要",
            "risk_level": "R1",
            "input": {},
            "metadata": {"repo_step_role": "summarize"},
        }
    )
    return steps


def _normalize_plan_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, step in enumerate(steps, start=1):
        normalized.append(
            {
                **step,
                "step_key": str(step.get("step_key") or f"step_{index}"),
                "step_type": str(step.get("step_type") or "compose"),
                "title": str(step.get("title") or _title_for_step(step, index)),
                "risk_level": str(step.get("risk_level") or "R1"),
                "input": step.get("input") if isinstance(step.get("input"), dict) else {},
                "metadata": step.get("metadata") if isinstance(step.get("metadata"), dict) else {},
            }
        )
    return normalized


def _repo_profile_from_task(task: dict[str, Any]) -> dict[str, Any]:
    preflight = dict(task.get("preflight") or {})
    repo_execution = dict(preflight.get("repo_execution") or {})
    if "enabled" not in repo_execution:
        repo_execution["enabled"] = False
    return repo_execution


def _code_hosting_profile_from_task(task: dict[str, Any]) -> dict[str, Any]:
    preflight = dict(task.get("preflight") or {})
    code_hosting = dict(preflight.get("code_hosting") or {})
    if "enabled" not in code_hosting:
        code_hosting["enabled"] = False
    return code_hosting


def _repo_step_role(step: dict[str, Any]) -> str | None:
    metadata = dict(step.get("metadata") or {})
    role = str(metadata.get("repo_step_role") or "").strip()
    return role or None


def _workspace_snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    snapshot: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        snapshot[rel] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _workspace_diff_files(
    baseline: dict[str, str],
    current: dict[str, str],
) -> list[str]:
    ignored_prefixes = ("logs/", "outputs/")
    changed = [
        path
        for path in sorted(set(baseline) | set(current))
        if not path.startswith(ignored_prefixes)
        if baseline.get(path) != current.get(path)
    ]
    return changed


def _title_for_step(step: dict[str, Any], index: int) -> str:
    step_type = str(step.get("step_type") or "compose")
    step_key = str(step.get("step_key") or f"step_{index}")
    tool_name = ""
    raw_input = step.get("input")
    if isinstance(raw_input, dict):
        tool_name = str(raw_input.get("tool_name") or "")
    if tool_name:
        return f"执行 {tool_name}"
    if step_type == "tool_call":
        return "执行受控工具"
    if step_type == "mcp_call":
        return "调用 MCP 工具"
    if step_type == "skill_run":
        return "执行匹配 Skill"
    if step_type == "skill_match":
        return "匹配可用 Skill"
    repo_role = _repo_step_role(step)
    if repo_role == "explore":
        return "扫描代码仓工作区"
    if repo_role == "patch":
        return "应用代码修改"
    if repo_role == "verify":
        return "运行目标验证"
    if step_key != f"step_{index}":
        return step_key.replace("_", " ")
    return "生成任务报告"


def _title_from_goal(goal: str) -> str:
    text = " ".join(goal.strip().split())
    return text[:32] or "新任务"


def _risk_order(value: str) -> int:
    return int(str(value).removeprefix("R"))


def _first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s，。；;）)]+", text, flags=re.IGNORECASE)
    return match.group(0) if match else None


def _download_display_name(text: str) -> str:
    url = _first_url(text)
    if not url:
        return "download.bin"
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    return name or "download.bin"


def _office_skill_id_for_goal(goal: str, matches: list[dict[str, Any]]) -> str | None:
    if not _goal_mentions_office(goal):
        return None
    lowered = goal.lower()
    preferred_bundle = None
    edit_markers = ["编辑", "修改", "追加", "增加", "替换", "完善", "改"]
    wants_edit = any(marker in goal for marker in edit_markers)
    if any(marker in lowered for marker in ["excel", "xlsx"]) or "表格" in goal:
        preferred_bundle = "clawhub-excel-edit" if wants_edit else "clawhub-excel-analysis-workbook"
    elif any(marker in lowered for marker in ["ppt", "pptx", "powerpoint"]) or "演示稿" in goal:
        preferred_bundle = "clawhub-ppt-edit" if wants_edit else "clawhub-ppt-briefing"
    elif (
        any(marker in lowered for marker in ["word", "docx"])
        or "文档" in goal
        or "周报" in goal
        or "报告" in goal
    ):
        preferred_bundle = "clawhub-word-edit" if wants_edit else "clawhub-word-report"
    if preferred_bundle:
        for match in matches:
            if match.get("bundle_id") == preferred_bundle and match.get("skill_id"):
                return str(match["skill_id"])
    return str(matches[0]["skill_id"]) if matches and matches[0].get("skill_id") else None


def _office_skill_confidence(skill_id: str, matches: list[dict[str, Any]]) -> float | None:
    for match in matches:
        if match.get("skill_id") == skill_id:
            confidence = match.get("confidence")
            return float(confidence) if confidence is not None else None
    return None


def _office_skill_input(request: TaskCreateRequest) -> dict[str, Any]:
    skill_input = dict(request.constraints.get("skill_input") or {})
    office_request = parse_office_chat_request(request.goal)
    if office_request is not None:
        for key, value in office_chat_skill_input(office_request).items():
            skill_input.setdefault(key, value)
    skill_input.setdefault("goal", request.goal)
    skill_input.setdefault("content", request.constraints.get("content") or request.goal)
    if request.constraints.get("source_artifact_id"):
        skill_input.setdefault("source_artifact_id", request.constraints["source_artifact_id"])
    return skill_input


def _code_hosting_skill_input(request: TaskCreateRequest) -> dict[str, Any]:
    skill_input = dict(request.constraints.get("skill_input") or {})
    skill_input.setdefault("goal", request.goal)
    skill_input.setdefault(
        "code_hosting_request_type",
        request.constraints.get("code_hosting_request_type"),
    )
    skill_input.setdefault(
        "forge_provider_type",
        request.constraints.get("forge_provider_type") or "github",
    )
    for key in (
        "remote_repo_ref",
        "base_branch",
        "target_branch",
        "pr_ref",
        "issue_ref",
        "review_action",
        "release_kind",
    ):
        if request.constraints.get(key) is not None:
            skill_input.setdefault(key, request.constraints.get(key))
    return skill_input


def _code_hosting_risk_level(request_type: Any) -> str:
    mapping = {
        "code_hosting_readonly_request": "R1",
        "code_hosting_sync_request": "R3",
        "code_hosting_pr_request": "R3",
        "code_hosting_review_request": "R3",
        "code_hosting_release_request": "R4",
    }
    return mapping.get(str(request_type or ""), "R2")


def _is_office_artifact(artifact: TaskArtifact) -> bool:
    content_type = str(artifact.content_type or "")
    return any(
        marker in content_type
        for marker in [
            "wordprocessingml.document",
            "spreadsheetml.sheet",
            "presentationml.presentation",
        ]
    )


def _merge_edited_step_input(
    current_input: dict[str, Any],
    edited_payload: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(current_input)
    current_args = dict(updated.get("args", {}))
    if isinstance(edited_payload.get("args"), dict):
        current_args.update(edited_payload["args"])
        extras = {key: value for key, value in edited_payload.items() if key != "args"}
        updated.update(extras)
    else:
        current_args.update(edited_payload)
    updated["args"] = current_args
    return updated


def _report_for_task(task: dict[str, Any], steps: list[dict[str, Any]]) -> str:
    lines = [
        f"# {task['title']}",
        "",
        f"- 目标：{task['goal']}",
        f"- 模式：{task['mode']}",
        f"- 风险：{task['risk_level']}",
        "",
        "## 步骤",
    ]
    for step in steps:
        lines.append(f"- {step['step_key']}: {step['status']}")
    return "\n".join(lines)


def _observation_summary_for_step(step: dict[str, Any]) -> str:
    title = str(step.get("title") or step.get("step_key") or "step")
    status = str(step.get("status") or "unknown")
    if status == "completed":
        return f"{title} 已完成。"
    if status == "waiting_approval":
        return f"{title} 正在等待审批。"
    if status == "failed":
        error = step.get("error_summary") or step.get("error_code") or "unknown_error"
        return f"{title} 失败：{redact(str(error))}"
    return f"{title} 状态：{status}。"


def _tool_call_refs(step: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if step.get("tool_call_id"):
        refs.append(
            {
                "type": "tool_call",
                "tool_call_id": step["tool_call_id"],
                "step_id": step.get("step_id"),
            }
        )
    output = step.get("output") or {}
    if not isinstance(output, dict):
        return refs
    skill_run = output.get("skill_run")
    if isinstance(skill_run, dict) and skill_run.get("skill_run_id"):
        refs.append(
            {
                "type": "skill_run",
                "skill_run_id": skill_run["skill_run_id"],
                "step_id": step.get("step_id"),
            }
        )
    mcp_call = output.get("mcp_call")
    if isinstance(mcp_call, dict) and mcp_call.get("mcp_call_id"):
        refs.append(
            {
                "type": "mcp_call",
                "mcp_call_id": mcp_call["mcp_call_id"],
                "step_id": step.get("step_id"),
            }
        )
    return refs


def _next_pending_step_key(steps: list[dict[str, Any]]) -> str | None:
    for step in steps:
        if step.get("status") not in {"completed", "failed"}:
            return str(step.get("step_key"))
    return None


def _phase96_stop_reason(raw_reason: str | None, task_status: str | None) -> str | None:
    reason = str(raw_reason or "").strip()
    if reason == "approval_required":
        return "approval_waiting"
    if reason == "blocked_by_safety":
        return "boundary_blocked"
    if reason == "failed":
        return "recovery_exhausted"
    if reason == "completed":
        return "goal_satisfied"
    if reason:
        return reason
    status = str(task_status or "").strip()
    if status == TaskStatus.COMPLETED.value:
        return "goal_satisfied"
    if status == TaskStatus.WAITING_APPROVAL.value:
        return "approval_waiting"
    if status == TaskStatus.CANCELLED.value:
        return "cancelled"
    if status == TaskStatus.PAUSED.value:
        return "budget_exhausted"
    if status == TaskStatus.FAILED.value:
        return "recovery_exhausted"
    return None


def _untrusted_observation(source_type: str, payload: Any) -> bool:
    if source_type in {"mcp_call", "mcp_resource", "knowledge_search", "web_page", "pdf"}:
        return True
    text = str(payload).lower()
    markers = [
        "ignore previous",
        "ignore all previous",
        "system prompt",
        "developer message",
        "绕过",
        "忽略之前",
        "泄露",
    ]
    return any(marker in text for marker in markers)


def _key_facts(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        text = str(redact(payload)).strip()
        return [text[:160]] if text else []
    facts: list[str] = []
    for key in (
        "status",
        "summary",
        "message",
        "artifact_id",
        "uri",
        "matches",
        "skill_run",
        "result",
    ):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, list):
            facts.append(f"{key}: {len(value)} item(s)")
        elif isinstance(value, dict):
            nested_status = value.get("status") or value.get("summary") or value.get("skill_run_id")
            facts.append(f"{key}: {str(redact(nested_status or 'present'))[:120]}")
        else:
            facts.append(f"{key}: {str(redact(value))[:120]}")
        if len(facts) >= 5:
            break
    return facts


def _observation_errors(
    step: dict[str, Any] | None,
    payload: Any,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if step and step.get("error_code"):
        errors.append(
            {
                "error_code": step.get("error_code"),
                "summary": str(redact(step.get("error_summary") or ""))[:200],
            }
        )
    if isinstance(payload, dict):
        error = payload.get("error") or payload.get("error_summary")
        if error:
            errors.append({"summary": str(redact(error))[:200]})
    return errors


def _artifact_refs(payload: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if not isinstance(payload, dict):
        return refs
    if payload.get("artifact_id"):
        refs.append({"artifact_id": payload["artifact_id"], "uri": payload.get("uri")})
    artifact_ids = payload.get("artifact_ids")
    if isinstance(artifact_ids, list):
        refs.extend({"artifact_id": item} for item in artifact_ids[:10])
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts[:10]:
            if isinstance(artifact, dict) and artifact.get("artifact_id"):
                refs.append(
                    {
                        "artifact_id": artifact["artifact_id"],
                        "uri": artifact.get("uri"),
                    }
                )
    return refs
