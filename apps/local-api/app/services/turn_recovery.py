from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core_types import (
    ChatEventType,
    ErrorCode,
    RiskLevel,
    TaskStatus,
    TraceSpanStatus,
    TraceSpanType,
)
from response_composer import ResponseComposer
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository

RECOVERY_ACTIONS = {
    "retry_failed_step",
    "retry_task_from_recovery_plan",
    "rebuild_minimal_context",
    "fallback_model_route",
    "ask_user_for_missing_input",
    "request_approval",
    "stop_unrecoverable",
}
MAX_ATTEMPTS_PER_TURN = 3
MAX_ATTEMPTS_PER_ERROR = 2


@dataclass(slots=True)
class RecoveryEvent:
    event_type: ChatEventType
    payload: dict[str, Any]


@dataclass(slots=True)
class TurnRecoveryResult:
    task: Any
    events: list[RecoveryEvent] = field(default_factory=list)
    response_prefix: str = ""
    recovery_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def attempted(self) -> bool:
        return bool(self.recovery_payload.get("attempt_count"))


class TurnRecoveryService:
    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        task_engine: Any,
        trace_service: TraceService,
        composer: ResponseComposer,
    ) -> None:
        self._chat_repo = chat_repo
        self._task_engine = task_engine
        self._trace = trace_service
        self._composer = composer

    async def recover_task_for_turn(
        self,
        *,
        turn: dict[str, Any],
        task: Any,
        root_span_id: str | None,
    ) -> TurnRecoveryResult:
        if str(task.status.value) not in {TaskStatus.FAILED.value, TaskStatus.PAUSED.value}:
            return TurnRecoveryResult(
                task=task,
                recovery_payload=_recovery_payload(
                    status=_status_for_task(task),
                    attempt_count=0,
                    root_cause=None,
                    actions_taken=[],
                    next_action=None,
                    task_id=str(task.task_id),
                ),
            )

        attempts = await self._chat_repo.list_recovery_attempts(str(turn["turn_id"]))
        root_cause = _root_cause(task)
        failure_type = _failure_type(root_cause)
        if not _task_recoverable_by_status(task):
            return await self._stop(
                turn=turn,
                task=task,
                root_cause=root_cause,
                failure_type=failure_type,
                status="waiting_approval"
                if str(task.status.value) == TaskStatus.WAITING_APPROVAL.value
                else "needs_user_input",
                next_action=_next_action_for_failure(failure_type, task),
            )

        if _high_risk_failed_step(task):
            return await self._stop(
                turn=turn,
                task=task,
                root_cause=root_cause,
                failure_type=failure_type,
                status="waiting_approval"
                if _has_current_approval(task)
                else "needs_user_input",
                next_action="request_approval" if _has_current_approval(task) else "ask_user",
            )

        if len(attempts) >= MAX_ATTEMPTS_PER_TURN:
            return await self._stop(
                turn=turn,
                task=task,
                root_cause=root_cause,
                failure_type=failure_type,
                status="exhausted",
                next_action="ask_user",
            )
        if Counter(str(item.get("root_cause") or "") for item in attempts)[root_cause] >= (
            MAX_ATTEMPTS_PER_ERROR
        ):
            return await self._stop(
                turn=turn,
                task=task,
                root_cause=root_cause,
                failure_type=failure_type,
                status="exhausted",
                next_action="ask_user",
            )

        attempt_index = len(attempts) + 1
        action = _action_for_failure(failure_type)
        if action not in RECOVERY_ACTIONS:
            action = "stop_unrecoverable"
        events = [
            RecoveryEvent(
                ChatEventType.TURN_RECOVERY_STARTED,
                {
                    "task_id": str(task.task_id),
                    "attempt_index": attempt_index,
                    "max_attempts": MAX_ATTEMPTS_PER_TURN,
                },
            ),
            RecoveryEvent(
                ChatEventType.TURN_RECOVERY_DIAGNOSED,
                {
                    "task_id": str(task.task_id),
                    "attempt_index": attempt_index,
                    "failure_type": failure_type,
                    "root_cause": root_cause,
                    "recovery_action": action,
                },
            ),
        ]
        if action == "stop_unrecoverable":
            stopped = await self._stop(
                turn=turn,
                task=task,
                root_cause=root_cause,
                failure_type=failure_type,
                status="unrecoverable",
                next_action="ask_user",
            )
            stopped.events = events + stopped.events
            return stopped

        started_at = utc_now_iso()
        attempt_id = new_id("trec")
        diagnostic = {
            "task_id": str(task.task_id),
            "failure_type": failure_type,
            "root_cause": root_cause,
            "recovery_action": action,
            "attempt_index": attempt_index,
            "bypass_controls": False,
        }
        await self._chat_repo.insert_recovery_attempt(
            {
                "recovery_attempt_id": attempt_id,
                "organization_id": str(getattr(task, "organization_id", "org_default")),
                "turn_id": str(turn["turn_id"]),
                "task_id": str(task.task_id),
                "attempt_index": attempt_index,
                "failure_type": failure_type,
                "root_cause": root_cause,
                "recovery_action": action,
                "status": "running",
                "diagnostic_payload": redact(diagnostic),
                "trace_id": turn.get("trace_id"),
                "started_at": started_at,
            }
        )
        span_id = await self._trace.start_span(
            str(turn["trace_id"]),
            span_type=TraceSpanType.TURN_RECOVERY,
            name="recover chat turn task",
            parent_span_id=root_span_id,
            input_data=redact(diagnostic),
        )
        events.append(
            RecoveryEvent(
                ChatEventType.TURN_RECOVERY_ACTION,
                {
                    "task_id": str(task.task_id),
                    "attempt_index": attempt_index,
                    "recovery_action": action,
                    "status": "running",
                },
            )
        )
        try:
            recovered_task = await self._task_engine.retry_task(
                str(task.task_id),
                trace_id=str(turn["trace_id"]),
            )
            status = _status_for_task(recovered_task)
            payload = _recovery_payload(
                status=status,
                attempt_count=attempt_index,
                root_cause=root_cause,
                actions_taken=[action],
                next_action=_next_action_for_task(recovered_task),
                task_id=str(task.task_id),
            )
            completed_payload = {
                **payload,
                "failure_type": failure_type,
                "recovery_action": action,
            }
            await self._chat_repo.update_recovery_attempt(
                attempt_id,
                status=status,
                diagnostic_payload=redact(completed_payload),
                completed_at=utc_now_iso(),
            )
            await self._trace.end_span(
                span_id,
                status=TraceSpanStatus.COMPLETED
                if status in {"recovered", "waiting_approval", "needs_user_input"}
                else TraceSpanStatus.FAILED,
                output_data=redact(completed_payload),
            )
            events.append(
                RecoveryEvent(
                    ChatEventType.TURN_RECOVERY_COMPLETED,
                    {
                        "task_id": str(task.task_id),
                        "attempt_index": attempt_index,
                        "status": status,
                        "next_action": payload.get("next_action"),
                    },
                )
            )
            prefix = "我刚才检查了失败原因并自动重试了一轮；" if status == "recovered" else ""
            return TurnRecoveryResult(
                task=recovered_task,
                events=events,
                response_prefix=prefix,
                recovery_payload=payload,
            )
        except AppError as exc:
            status = (
                "waiting_approval"
                if exc.code == ErrorCode.TOOL_APPROVAL_REQUIRED
                else "exhausted"
            )
            payload = _recovery_payload(
                status=status,
                attempt_count=attempt_index,
                root_cause=root_cause,
                actions_taken=[action],
                next_action=_next_action_for_failure(failure_type, task),
                task_id=str(task.task_id),
            )
            payload["error_code"] = exc.code.value if hasattr(exc.code, "value") else str(exc.code)
            payload["error_message"] = exc.message
            await self._chat_repo.update_recovery_attempt(
                attempt_id,
                status=status,
                diagnostic_payload=redact(payload),
                completed_at=utc_now_iso(),
            )
            await self._trace.end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data=redact(payload),
                error_code=payload["error_code"],
            )
            events.append(
                RecoveryEvent(
                    ChatEventType.TURN_RECOVERY_COMPLETED,
                    {
                        "task_id": str(task.task_id),
                        "attempt_index": attempt_index,
                        "status": status,
                        "next_action": payload.get("next_action"),
                    },
                )
            )
            return TurnRecoveryResult(task=task, events=events, recovery_payload=payload)

    async def _stop(
        self,
        *,
        turn: dict[str, Any],
        task: Any,
        root_cause: str,
        failure_type: str,
        status: str,
        next_action: str | None,
    ) -> TurnRecoveryResult:
        attempts = await self._chat_repo.list_recovery_attempts(str(turn["turn_id"]))
        action = "request_approval" if status == "waiting_approval" else "stop_unrecoverable"
        payload = _recovery_payload(
            status=status,
            attempt_count=len(attempts),
            root_cause=root_cause,
            actions_taken=[],
            next_action=next_action,
            task_id=str(task.task_id),
        )
        payload["failure_type"] = failure_type
        return TurnRecoveryResult(
            task=task,
            events=[
                RecoveryEvent(
                    ChatEventType.TURN_RECOVERY_COMPLETED,
                    {
                        "task_id": str(task.task_id),
                        "status": status,
                        "recovery_action": action,
                        "next_action": next_action,
                    },
                )
            ],
            recovery_payload=payload,
        )

    def response_plan_for_task(
        self,
        *,
        summary: str,
        task_status: dict[str, Any],
        recovery_payload: dict[str, Any],
        safety_notice: str | None = None,
        tool_notice: str | None = None,
    ) -> Any:
        base = self._composer.response_plan_for_status(
            summary=summary,
            task_status=task_status,
            safety_notice=safety_notice,
            tool_notice=tool_notice,
        )
        if not recovery_payload.get("attempt_count"):
            return base
        suggested = _suggested_actions_for_recovery(recovery_payload)
        return self._composer.response_plan_for_recovery(
            summary=summary,
            error_code=str(recovery_payload.get("root_cause") or "TASK_RECOVERY"),
            recoverable=str(recovery_payload.get("status")) not in {"unrecoverable", "exhausted"},
            suggested_next_actions=suggested,
            base_plan=base,
            recovery=recovery_payload,
        )


def _task_recoverable_by_status(task: Any) -> bool:
    return str(task.status.value) in {TaskStatus.FAILED.value, TaskStatus.PAUSED.value}


def _status_for_task(task: Any) -> str:
    status = str(task.status.value)
    if status == TaskStatus.COMPLETED.value:
        return "recovered"
    if status == TaskStatus.WAITING_APPROVAL.value:
        return "waiting_approval"
    if status == TaskStatus.PAUSED.value:
        return "needs_user_input"
    if status == TaskStatus.FAILED.value:
        return "exhausted"
    return "needs_user_input"


def _root_cause(task: Any) -> str:
    if getattr(task, "failure_reason", None):
        return str(redact(task.failure_reason))
    result = getattr(task, "result", {}) or {}
    if isinstance(result, dict) and result.get("stop_reason"):
        return str(redact(result["stop_reason"]))
    return "task_failed"


def _failure_type(root_cause: str) -> str:
    lowered = root_cause.lower()
    if "approval" in lowered:
        return "approval_required"
    if "safety" in lowered or "blocked" in lowered:
        return "safety_blocked"
    if "permission" in lowered or "capability" in lowered or "denied" in lowered:
        return "permission_denied"
    if "timeout" in lowered:
        return "timeout"
    if "budget" in lowered:
        return "budget_exhausted"
    if "model" in lowered:
        return "model_unavailable"
    if "schema" in lowered or "invalid" in lowered:
        return "invalid_output"
    return "tool_unavailable"


def _action_for_failure(failure_type: str) -> str:
    if failure_type in {"permission_denied", "safety_blocked", "approval_required"}:
        return "request_approval" if failure_type == "approval_required" else "stop_unrecoverable"
    if failure_type == "model_unavailable":
        return "fallback_model_route"
    if failure_type == "budget_exhausted":
        return "ask_user_for_missing_input"
    return "retry_failed_step"


def _has_current_approval(task: Any) -> bool:
    return bool(getattr(task, "current_approval_id", None))


def _high_risk_failed_step(task: Any) -> bool:
    plan = getattr(task, "plan", None)
    if plan is None:
        return False
    for step in getattr(plan, "steps", []) or []:
        risk = str(step.get("risk_level") or "R1")
        try:
            if int(risk.removeprefix("R")) >= int(RiskLevel.R3.value.removeprefix("R")):
                return True
        except ValueError:
            continue
    return False


def _next_action_for_task(task: Any) -> str | None:
    status = str(task.status.value)
    if status == TaskStatus.COMPLETED.value:
        return None
    if status == TaskStatus.WAITING_APPROVAL.value:
        return "request_approval"
    if status == TaskStatus.PAUSED.value:
        return "ask_user_for_missing_input"
    if status == TaskStatus.FAILED.value:
        return "ask_user"
    return None


def _next_action_for_failure(failure_type: str, task: Any) -> str:
    if _has_current_approval(task) or failure_type == "approval_required":
        return "request_approval"
    if failure_type in {"permission_denied", "safety_blocked"}:
        return "ask_user_for_missing_input"
    return "ask_user"


def _recovery_payload(
    *,
    status: str,
    attempt_count: int,
    root_cause: str | None,
    actions_taken: list[str],
    next_action: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "attempt_count": attempt_count,
        "root_cause": root_cause,
        "actions_taken": actions_taken,
        "next_action": next_action,
        "task_id": task_id,
    }


def _suggested_actions_for_recovery(recovery_payload: dict[str, Any]) -> list[str]:
    status = str(recovery_payload.get("status") or "")
    if status == "recovered":
        return []
    if status == "waiting_approval":
        return ["确认后继续", "拒绝或修改这一步"]
    if status == "exhausted":
        return ["缩小任务范围后重试", "查看失败原因"]
    return ["补充缺失信息后继续", "调整目标后重试"]
