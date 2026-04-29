from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.tasks import (
    AgentLoopListResponse,
    AgentNextActionDecisionListResponse,
    CollaborationReplayResponse,
    ParticipantRemoveRequest,
    PlanCandidateListResponse,
    PlannerCapabilityCandidateListResponse,
    PlannerDecisionListResponse,
    PlanPolicyPruneListResponse,
    PlanVerificationResultListResponse,
    SubtaskActionRequest,
    TaskActionRequest,
    TaskArtifactListResponse,
    TaskCreateRequest,
    TaskDetailResponse,
    TaskEventListResponse,
    TaskListResponse,
    TaskObservationListResponse,
    TaskParticipantListResponse,
    TaskParticipantResponse,
    TaskPlanResponse,
    TaskReflectionCandidateListResponse,
    TaskReplayResponse,
    TaskRetryPlanListResponse,
    TaskSubtaskListResponse,
    TaskSubtaskResponse,
    ToolFailureRecoveryPlanListResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskDetailResponse)
async def create_task(
    payload: TaskCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(
            await registry.task_engine.create_task(
                payload,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    status: str | None = None,
    owner_member_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskListResponse:
    return TaskListResponse(
        items=await registry.task_engine.list_tasks(
            status=status,
            owner_member_id=owner_member_id,
            limit=limit,
        )
    )


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(await registry.task_engine.detail(task_id)).model_dump(mode="json")
    )


@router.post("/{task_id}/start", response_model=TaskDetailResponse)
async def start_task(
    task_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(
            await registry.task_engine.start_task(
                task_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post("/{task_id}/pause", response_model=TaskDetailResponse)
async def pause_task(
    task_id: str,
    payload: TaskActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(
            await registry.task_engine.pause_task(
                task_id,
                reason=payload.reason,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post("/{task_id}/resume", response_model=TaskDetailResponse)
async def resume_task(
    task_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(
            await registry.task_engine.resume_task(
                task_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post("/{task_id}/cancel", response_model=TaskDetailResponse)
async def cancel_task(
    task_id: str,
    payload: TaskActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(
            await registry.task_engine.cancel_task(
                task_id,
                reason=payload.reason,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post("/{task_id}/retry", response_model=TaskDetailResponse)
async def retry_task(
    task_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(
            await registry.task_engine.retry_task(
                task_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.get("/{task_id}/events", response_model=TaskEventListResponse)
async def task_events(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskEventListResponse:
    return TaskEventListResponse(items=await registry.task_engine.events(task_id))


@router.get("/{task_id}/replay", response_model=TaskReplayResponse)
async def task_replay(
    task_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskReplayResponse:
    return TaskReplayResponse(
        **(
            await registry.task_engine.replay(
                task_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.get("/{task_id}/plan", response_model=TaskPlanResponse)
async def task_plan(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskPlanResponse:
    task = await registry.task_engine.detail(task_id)
    return TaskPlanResponse(plan=task.plan.model_dump(mode="json") if task.plan else {})


@router.get("/{task_id}/planner-decisions", response_model=PlannerDecisionListResponse)
async def task_planner_decisions(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PlannerDecisionListResponse:
    return PlannerDecisionListResponse(
        items=await registry.task_engine.planner_decisions(task_id)
    )


@router.get("/{task_id}/agent-loop", response_model=AgentLoopListResponse)
async def task_agent_loop(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentLoopListResponse:
    return AgentLoopListResponse(items=await registry.task_engine.agent_loop(task_id))


@router.get("/{task_id}/observations", response_model=TaskObservationListResponse)
async def task_observations(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskObservationListResponse:
    return TaskObservationListResponse(
        items=await registry.task_engine.observations(task_id)
    )


@router.get(
    "/{task_id}/reflection-candidates",
    response_model=TaskReflectionCandidateListResponse,
)
async def task_reflection_candidates(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskReflectionCandidateListResponse:
    return TaskReflectionCandidateListResponse(
        items=await registry.task_engine.reflection_candidates(task_id)
    )


@router.get("/{task_id}/retry-plans", response_model=TaskRetryPlanListResponse)
async def task_retry_plans(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskRetryPlanListResponse:
    return TaskRetryPlanListResponse(items=await registry.task_engine.retry_plans(task_id))


@router.get("/{task_id}/model-plan-candidates", response_model=PlanCandidateListResponse)
async def task_model_plan_candidates(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PlanCandidateListResponse:
    return PlanCandidateListResponse(
        items=await registry.task_engine.model_plan_candidates(task_id)
    )


@router.get(
    "/{task_id}/plan-verification-results",
    response_model=PlanVerificationResultListResponse,
)
async def task_plan_verification_results(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PlanVerificationResultListResponse:
    return PlanVerificationResultListResponse(
        items=await registry.task_engine.plan_verification_results(task_id)
    )


@router.get("/{task_id}/plan-policy-prunes", response_model=PlanPolicyPruneListResponse)
async def task_plan_policy_prunes(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PlanPolicyPruneListResponse:
    return PlanPolicyPruneListResponse(
        items=await registry.task_engine.plan_policy_prunes(task_id)
    )


@router.get(
    "/{task_id}/planner-capability-candidates",
    response_model=PlannerCapabilityCandidateListResponse,
)
async def task_planner_capability_candidates(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> PlannerCapabilityCandidateListResponse:
    return PlannerCapabilityCandidateListResponse(
        items=await registry.task_engine.planner_capability_candidates(task_id)
    )


@router.get("/{task_id}/agent-next-actions", response_model=AgentNextActionDecisionListResponse)
async def task_agent_next_actions(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> AgentNextActionDecisionListResponse:
    return AgentNextActionDecisionListResponse(
        items=await registry.task_engine.agent_next_actions(task_id)
    )


@router.get(
    "/{task_id}/failure-recovery-plans",
    response_model=ToolFailureRecoveryPlanListResponse,
)
async def task_failure_recovery_plans(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolFailureRecoveryPlanListResponse:
    return ToolFailureRecoveryPlanListResponse(
        items=await registry.task_engine.failure_recovery_plans(task_id)
    )


@router.get("/{task_id}/artifacts", response_model=TaskArtifactListResponse)
async def task_artifacts(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskArtifactListResponse:
    return TaskArtifactListResponse(items=await registry.task_engine.artifacts(task_id))


@router.post("/{task_id}/supervisor/plan", response_model=CollaborationReplayResponse)
async def supervisor_plan(
    task_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> CollaborationReplayResponse:
    return CollaborationReplayResponse(
        **(
            await registry.supervisor_service.plan(
                task_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post("/{task_id}/supervisor/start", response_model=TaskDetailResponse)
async def supervisor_start(
    task_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskDetailResponse:
    return TaskDetailResponse(
        **(
            await registry.task_engine.start_task(
                task_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.get("/{task_id}/participants", response_model=TaskParticipantListResponse)
async def task_participants(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskParticipantListResponse:
    return TaskParticipantListResponse(
        items=await registry.supervisor_service.participants(task_id)
    )


@router.get("/{task_id}/subtasks", response_model=TaskSubtaskListResponse)
async def task_subtasks(
    task_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskSubtaskListResponse:
    return TaskSubtaskListResponse(items=await registry.supervisor_service.subtasks(task_id))


@router.get("/{task_id}/collaboration-replay", response_model=CollaborationReplayResponse)
async def collaboration_replay(
    task_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> CollaborationReplayResponse:
    return CollaborationReplayResponse(
        **(
            await registry.supervisor_service.replay(
                task_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.post(
    "/{task_id}/participants/{participant_id}/remove",
    response_model=TaskParticipantResponse,
)
async def remove_participant(
    task_id: str,
    participant_id: str,
    payload: ParticipantRemoveRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskParticipantResponse:
    participant = await registry.supervisor_service.remove_participant(
        task_id,
        participant_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return TaskParticipantResponse(**participant.model_dump(mode="json"))


@router.post("/{task_id}/subtasks/{subtask_id}/retry", response_model=TaskSubtaskResponse)
async def retry_subtask(
    task_id: str,
    subtask_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskSubtaskResponse:
    subtask = await registry.supervisor_service.retry_subtask(
        task_id,
        subtask_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return TaskSubtaskResponse(**subtask.model_dump(mode="json"))


@router.post("/{task_id}/subtasks/{subtask_id}/skip", response_model=TaskSubtaskResponse)
async def skip_subtask(
    task_id: str,
    subtask_id: str,
    payload: SubtaskActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> TaskSubtaskResponse:
    subtask = await registry.supervisor_service.skip_subtask(
        task_id,
        subtask_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return TaskSubtaskResponse(**subtask.model_dump(mode="json"))
