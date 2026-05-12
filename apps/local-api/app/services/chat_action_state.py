from __future__ import annotations

from typing import Any

from response_composer import canonical_action_status


def normalize_chat_action_state(
    *,
    task: Any | None = None,
    approval: Any | None = None,
    route_kind: str,
    recovery_payload: dict[str, Any] | None = None,
    pending_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recovery = dict(recovery_payload or {})
    task_status_raw = _task_status_value(task)
    approval_status = _approval_status_value(approval)
    recovery_status = canonical_action_status(str(recovery.get("status") or ""))

    if approval_status == "denied":
        return {
            "action_status": "paused",
            "task_status": "paused",
            "pending_action": None,
            "response_style": "paused",
            "should_fail_turn": False,
            "route_kind": route_kind,
        }

    if task_status_raw == "waiting_approval" or approval_status in {"pending", "required"}:
        return {
            "action_status": "waiting_for_approval",
            "task_status": "waiting_approval",
            "pending_action": pending_action,
            "response_style": "pending_confirmation",
            "should_fail_turn": False,
            "route_kind": route_kind,
        }

    if str(recovery.get("status") or "") == "exhausted":
        return {
            "action_status": "failed_with_reason",
            "task_status": task_status_raw or "failed",
            "pending_action": None,
            "response_style": "failed",
            "should_fail_turn": True,
            "route_kind": route_kind,
        }

    task_status = _normalized_task_status(task_status_raw, recovery_status=recovery_status)
    action_status = _action_status_from_task(task_status)
    return {
        "action_status": action_status,
        "task_status": task_status,
        "pending_action": pending_action if action_status == "waiting_for_approval" else None,
        "response_style": "task_status",
        "should_fail_turn": False,
        "route_kind": route_kind,
    }


def _task_status_value(task: Any | None) -> str:
    if task is None:
        return ""
    status = getattr(task, "status", "")
    return str(getattr(status, "value", status) or "")


def _approval_status_value(approval: Any | None) -> str:
    if approval is None:
        return ""
    status = getattr(approval, "status", "")
    return str(getattr(status, "value", status) or "")


def _normalized_task_status(task_status: str, *, recovery_status: str) -> str:
    raw = str(task_status or "").strip().lower()
    if raw == "paused":
        return "paused"
    if recovery_status == "waiting_for_approval":
        return "waiting_approval"
    return raw or "planned"


def _action_status_from_task(task_status: str) -> str:
    if task_status == "paused":
        return "paused"
    if task_status == "waiting_approval":
        return "waiting_for_approval"
    if task_status in {"completed"}:
        return "completed_with_evidence"
    if task_status in {"failed", "error", "cancelled"}:
        return "failed_with_reason"
    if task_status in {"running"}:
        return "executing"
    return canonical_action_status(task_status, default="planned")
