from __future__ import annotations

from core_types import ErrorCode, TaskStatus

from app.core.errors import AppError

TASK_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.CREATED.value: {TaskStatus.PLANNING.value, TaskStatus.CANCELLED.value},
    TaskStatus.PLANNING.value: {
        TaskStatus.PARTICIPANT_SELECTING.value,
        TaskStatus.PLANNED.value,
        TaskStatus.PRECHECK_FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.PARTICIPANT_SELECTING.value: {
        TaskStatus.PLANNED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.PLANNED.value: {
        TaskStatus.RUNNING.value,
        TaskStatus.WAITING_APPROVAL.value,
        TaskStatus.PAUSED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.RUNNING.value: {
        TaskStatus.WAITING_APPROVAL.value,
        TaskStatus.PAUSED.value,
        TaskStatus.SYNTHESIZING.value,
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.WAITING_APPROVAL.value: {
        TaskStatus.RUNNING.value,
        TaskStatus.PAUSED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.PAUSED.value: {TaskStatus.RUNNING.value, TaskStatus.CANCELLED.value},
    TaskStatus.SYNTHESIZING.value: {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value},
    TaskStatus.COMPLETED.value: {TaskStatus.ARCHIVED.value},
    TaskStatus.FAILED.value: {TaskStatus.ARCHIVED.value},
    TaskStatus.CANCELLED.value: {TaskStatus.ARCHIVED.value},
    TaskStatus.PRECHECK_FAILED.value: {TaskStatus.ARCHIVED.value},
    TaskStatus.ARCHIVED.value: set(),
}


def ensure_task_transition(current: str, target: str) -> None:
    if current == target:
        return
    if target not in TASK_TRANSITIONS.get(current, set()):
        raise AppError(
            ErrorCode.TASK_STATE_INVALID,
            f"任务状态不能从 {current} 切换到 {target}",
            status_code=409,
            details={"current": current, "target": target},
        )
