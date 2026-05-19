from __future__ import annotations

from typing import Any

from core_types import ExecutionEvidenceDecision


def decide_execution_evidence(
    *,
    pending_actions: list[dict[str, Any]] | None = None,
    action: dict[str, Any] | None = None,
    task_status: dict[str, Any] | None = None,
    detail: Any | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
    user_text: str = "",
    action_started: bool = False,
) -> ExecutionEvidenceDecision:
    evidence_refs: list[dict[str, Any]] = []
    reason_codes: list[str] = []
    missing: list[str] = []

    if artifact_refs:
        evidence_refs.extend(dict(item) for item in artifact_refs if isinstance(item, dict))
        reason_codes.append("artifact_ref_present")
    if isinstance(task_status, dict) and task_status:
        status = str(task_status.get("status") or task_status.get("detail_status") or "")
        if status:
            evidence_refs.append({"type": "task_status", "status": status})
        if status in {"completed", "completed_with_evidence"}:
            reason_codes.append("task_completion_record_present")
    detail_status = str(getattr(detail, "status", "") or "")
    if detail_status:
        evidence_refs.append({"type": "detail_status", "status": detail_status})
        if detail_status in {"completed", "completed_with_evidence"}:
            reason_codes.append("timeline_or_replay_completion_present")

    normalized_action = dict(action or {})
    if normalized_action:
        if normalized_action.get("artifact_id") or normalized_action.get("artifact_refs"):
            reason_codes.append("tool_result_evidence_present")
            raw_refs = normalized_action.get("artifact_refs") or []
            evidence_refs.extend(dict(item) for item in raw_refs if isinstance(item, dict))
        if str(normalized_action.get("task_id") or ""):
            evidence_refs.append({"type": "task_ref", "task_id": str(normalized_action.get("task_id"))})

    if pending_actions:
        reason_codes.append("pending_action_present")
    if user_text and any(marker in user_text for marker in ("证据", "完成", "执行", "下载那一步", "还没真正执行")):
        reason_codes.append("user_requested_execution_state")

    is_complete = any(
        code
        in reason_codes
        for code in (
            "artifact_ref_present",
            "task_completion_record_present",
            "timeline_or_replay_completion_present",
            "tool_result_evidence_present",
        )
    )
    if is_complete:
        return ExecutionEvidenceDecision(
            status="completed",
            is_complete=True,
            missing_evidence_types=[],
            evidence_refs=evidence_refs,
            reason_codes=reason_codes,
        )

    if pending_actions or action_started or "user_requested_execution_state" in reason_codes:
        if "artifact_ref_present" not in reason_codes:
            missing.append("artifact_ref")
        if "task_completion_record_present" not in reason_codes:
            missing.append("task_completion_record")
        if "timeline_or_replay_completion_present" not in reason_codes:
            missing.append("timeline_or_replay_record")
        return ExecutionEvidenceDecision(
            status="waiting_evidence",
            is_complete=False,
            missing_evidence_types=missing,
            evidence_refs=evidence_refs,
            reason_codes=[*reason_codes, "waiting_for_execution_evidence"],
        )

    return ExecutionEvidenceDecision(
        status="idle",
        is_complete=False,
        missing_evidence_types=[],
        evidence_refs=evidence_refs,
        reason_codes=reason_codes,
    )
