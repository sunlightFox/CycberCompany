from __future__ import annotations

from typing import Any

CANONICAL_ACTION_STATUSES = {
    "requested",
    "planned",
    "waiting_for_approval",
    "executing",
    "partially_completed",
    "completed_with_evidence",
    "failed_with_reason",
    "blocked_by_boundary",
    "cancelled",
}

_LEGACY_STATUS_MAP = {
    "": "requested",
    "requested": "requested",
    "no_action": "requested",
    "not_created": "planned",
    "queued": "planned",
    "created": "planned",
    "scheduled": "planned",
    "planned": "planned",
    "pending_action": "waiting_for_approval",
    "pending_approval": "waiting_for_approval",
    "approval_pending": "waiting_for_approval",
    "approval_required": "waiting_for_approval",
    "waiting_approval": "waiting_for_approval",
    "waiting_for_approval": "waiting_for_approval",
    "approved": "executing",
    "running": "executing",
    "in_progress": "executing",
    "executing": "executing",
    "paused": "partially_completed",
    "partial": "partially_completed",
    "degraded": "partially_completed",
    "actionable": "partially_completed",
    "partially_completed": "partially_completed",
    "completed": "completed_with_evidence",
    "succeeded": "completed_with_evidence",
    "published": "completed_with_evidence",
    "ok": "completed_with_evidence",
    "already_absent": "completed_with_evidence",
    "completed_with_evidence": "completed_with_evidence",
    "timeout": "failed_with_reason",
    "failed": "failed_with_reason",
    "error": "failed_with_reason",
    "http_error": "failed_with_reason",
    "failed_retryable": "failed_with_reason",
    "retryable_failure": "failed_with_reason",
    "failed_with_reason": "failed_with_reason",
    "blocked": "blocked_by_boundary",
    "manual_only": "blocked_by_boundary",
    "unsupported": "blocked_by_boundary",
    "hard_block": "blocked_by_boundary",
    "no_pending_action": "blocked_by_boundary",
    "multiple_pending_actions": "blocked_by_boundary",
    "ambiguous_confirmation_blocked": "blocked_by_boundary",
    "always_denied_for_risk": "blocked_by_boundary",
    "edit_missing_target": "blocked_by_boundary",
    "pending_action_invalid": "blocked_by_boundary",
    "plain_next_step": "blocked_by_boundary",
    "resolution_failed": "failed_with_reason",
    "denied": "cancelled",
    "cancelled": "cancelled",
    "edited": "planned",
}


def canonical_action_status(status: str | None, *, default: str = "requested") -> str:
    raw = str(status or "").strip().lower()
    if raw in CANONICAL_ACTION_STATUSES:
        return raw
    return _LEGACY_STATUS_MAP.get(raw, default)


def status_reason_codes(*values: Any) -> list[str]:
    codes: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip():
            codes.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    codes.append(item.strip())
    seen: set[str] = set()
    result: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            result.append(code)
    return result


def has_completion_evidence(payload: dict[str, Any] | None) -> bool:
    data = dict(payload or {})
    if data.get("evidence_refs"):
        return True
    if data.get("artifact_refs"):
        return True
    if str(data.get("evidence_summary") or "").strip():
        return True
    if data.get("completed_parts"):
        return True
    task_ref = data.get("task_ref")
    if isinstance(task_ref, dict) and (
        str(task_ref.get("task_id") or "").strip() or str(task_ref.get("status") or "").strip()
    ):
        return True
    tool_ref = data.get("tool_ref")
    if isinstance(tool_ref, dict) and (
        str(tool_ref.get("tool_call_id") or "").strip()
        or str(tool_ref.get("tool_name") or "").strip()
    ):
        return True
    return False


def normalize_action_status_semantics(
    payload: dict[str, Any] | None,
    *,
    default_status: str = "requested",
    scope: str | None = None,
) -> dict[str, Any]:
    data = dict(payload or {})
    approval_state = dict(data.get("approval_state") or {})
    status = canonical_action_status(data.get("status"), default=default_status)
    reason_codes = status_reason_codes(
        data.get("reason_codes"),
        data.get("reason_code"),
        data.get("failure_reason"),
        data.get("block_reason"),
    )
    evidence_refs = [
        item for item in list(data.get("evidence_refs") or []) if isinstance(item, dict)
    ]
    artifact_refs = [
        item for item in list(data.get("artifact_refs") or []) if isinstance(item, dict)
    ]
    completed_parts = [
        str(item).strip()
        for item in list(data.get("completed_parts") or [])
        if str(item).strip()
    ]
    remaining_parts = [
        str(item).strip()
        for item in list(data.get("remaining_parts") or [])
        if str(item).strip()
    ]
    pending_work = [
        str(item).strip()
        for item in list(data.get("pending_work") or [])
        if str(item).strip()
    ]
    semantics = {
        "status": status,
        "scope": str(data.get("scope") or scope or "workflow_summary"),
        "reason_codes": reason_codes,
        "evidence_summary": str(data.get("evidence_summary") or "").strip(),
        "evidence_refs": evidence_refs,
        "approval_state": approval_state,
        "completed_parts": completed_parts,
        "remaining_parts": remaining_parts,
        "pending_work": pending_work,
        "task_ref": dict(data.get("task_ref") or {}),
        "tool_ref": dict(data.get("tool_ref") or {}),
        "artifact_refs": artifact_refs,
    }
    if str(data.get("failure_summary") or "").strip():
        semantics["failure_summary"] = str(data.get("failure_summary") or "").strip()
    if str(data.get("failure_reason") or "").strip():
        semantics["failure_reason"] = str(data.get("failure_reason") or "").strip()
    if str(approval_state.get("status") or "") == "required":
        semantics["status"] = "waiting_for_approval"
    if semantics["status"] == "completed_with_evidence" and not has_completion_evidence(semantics):
        if semantics["failure_reason"] if "failure_reason" in semantics else False:
            semantics["status"] = "partially_completed"
        elif semantics["remaining_parts"] or semantics["pending_work"]:
            semantics["status"] = "partially_completed"
        elif semantics["task_ref"] or semantics["tool_ref"]:
            semantics["status"] = "executing"
        else:
            semantics["status"] = "planned"
    return semantics


def mirrored_status_payload(
    semantics: dict[str, Any] | None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_action_status_semantics(semantics)
    return {
        **dict(extra or {}),
        "status": normalized["status"],
        "reason_codes": list(normalized.get("reason_codes") or []),
        "evidence_summary": normalized.get("evidence_summary"),
        "evidence_refs": list(normalized.get("evidence_refs") or []),
        "approval_state": dict(normalized.get("approval_state") or {}),
        "completed_parts": list(normalized.get("completed_parts") or []),
        "remaining_parts": list(normalized.get("remaining_parts") or []),
        "pending_work": list(normalized.get("pending_work") or []),
        "artifact_refs": list(normalized.get("artifact_refs") or []),
    }
