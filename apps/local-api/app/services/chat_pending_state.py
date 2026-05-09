from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.time import utc_now_iso


def message_fingerprint(text: str) -> str:
    normalized = " ".join(str(text or "").strip().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_typed_pending_state(
    *,
    conversation_id: str,
    session_id: str | None,
    source_turn_id: str,
    source_text: str,
    clarification: dict[str, Any] | None = None,
    approval_action: dict[str, Any] | None = None,
    execution_resume: dict[str, Any] | None = None,
    state_version: int = 1,
) -> dict[str, Any]:
    created_at = utc_now_iso()
    fingerprint = message_fingerprint(source_text)
    return {
        "pending_clarification": _pending_entry(
            kind="clarification",
            conversation_id=conversation_id,
            session_id=session_id,
            source_turn_id=source_turn_id,
            source_message_fingerprint=fingerprint,
            payload=clarification,
            state_version=state_version,
            ttl_minutes=30,
        ),
        "pending_approval_action": _pending_entry(
            kind="approval",
            conversation_id=conversation_id,
            session_id=session_id,
            source_turn_id=source_turn_id,
            source_message_fingerprint=fingerprint,
            payload=approval_action,
            state_version=state_version,
            ttl_minutes=120,
        ),
        "pending_execution_resume": _pending_entry(
            kind="execution_resume",
            conversation_id=conversation_id,
            session_id=session_id,
            source_turn_id=source_turn_id,
            source_message_fingerprint=fingerprint,
            payload=execution_resume,
            state_version=state_version,
            ttl_minutes=30,
        ),
        "pending_state_created_at": created_at,
    }


def project_legacy_pending_confirmation(working_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(working_state or {})
    approval = dict(state.get("pending_approval_action") or {})
    clarification = dict(state.get("pending_clarification") or {})
    if _is_active_pending(approval):
        payload = dict(approval.get("payload") or {})
        return {
            "kind": "natural_pending_actions",
            "session_id": approval.get("session_id"),
            "actions": list(payload.get("actions") or []),
            "questions": list(payload.get("questions") or []),
            "created_at": approval.get("created_at"),
            "expires_at": approval.get("expires_at"),
            "reason": approval.get("reason_code"),
            "turn_id": approval.get("source_turn_id"),
            "action_type": payload.get("action_type"),
            "task_id": payload.get("task_id"),
            "approval_id": payload.get("approval_id"),
        }
    if _is_active_pending(clarification):
        payload = dict(clarification.get("payload") or {})
        return {
            "kind": "clarification",
            "session_id": clarification.get("session_id"),
            "questions": list(payload.get("questions") or []),
            "created_at": clarification.get("created_at"),
            "expires_at": clarification.get("expires_at"),
            "reason": clarification.get("reason_code"),
            "turn_id": clarification.get("source_turn_id"),
        }
    return {}


def active_pending_clarification(
    working_state: dict[str, Any] | None,
    *,
    session_id: str | None,
) -> dict[str, Any] | None:
    state = dict(working_state or {})
    clarification = dict(state.get("pending_clarification") or {})
    if not _is_active_pending(clarification):
        return None
    pending_session = str(clarification.get("session_id") or "")
    if session_id and pending_session and pending_session != str(session_id):
        return None
    return clarification


def active_pending_approval_actions(
    working_state: dict[str, Any] | None,
    *,
    session_id: str | None,
) -> list[dict[str, Any]]:
    state = dict(working_state or {})
    approval = dict(state.get("pending_approval_action") or {})
    if not _is_active_pending(approval):
        return []
    pending_session = str(approval.get("session_id") or "")
    if session_id and pending_session and pending_session != str(session_id):
        return []
    payload = dict(approval.get("payload") or {})
    return [
        dict(item)
        for item in payload.get("actions") or []
        if isinstance(item, dict) and item.get("approval_id")
    ]


def explicit_pending_approval_actions(
    working_state: dict[str, Any] | None,
    *,
    user_text: str,
) -> list[dict[str, Any]]:
    state = dict(working_state or {})
    approval = dict(state.get("pending_approval_action") or {})
    if not _is_active_pending(approval):
        return []
    payload = dict(approval.get("payload") or {})
    actions = [
        dict(item)
        for item in payload.get("actions") or []
        if isinstance(item, dict) and item.get("approval_id")
    ]
    matched = [item for item in actions if _matches_explicit_action_binding(item, user_text)]
    if len(matched) == 1:
        return matched
    return []


def clear_pending_state(working_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(working_state or {})
    state.update(
        {
            "pending_clarification": {},
            "pending_approval_action": {},
            "pending_execution_resume": {},
            "pending_confirmation": {},
        }
    )
    return state


def _pending_entry(
    *,
    kind: str,
    conversation_id: str,
    session_id: str | None,
    source_turn_id: str,
    source_message_fingerprint: str,
    payload: dict[str, Any] | None,
    state_version: int,
    ttl_minutes: int,
) -> dict[str, Any]:
    if not payload:
        return {}
    created = _now()
    expires = created + timedelta(minutes=ttl_minutes)
    return {
        "kind": kind,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "source_turn_id": source_turn_id,
        "source_message_fingerprint": source_message_fingerprint,
        "status": "active",
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "state_version": state_version,
        "reason_code": str(payload.get("reason") or payload.get("reason_code") or kind),
        "payload": payload,
    }


def _is_active_pending(pending: dict[str, Any]) -> bool:
    if not pending:
        return False
    if str(pending.get("status") or "active") != "active":
        return False
    expires_at = str(pending.get("expires_at") or "")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) <= _now():
                return False
        except ValueError:
            return False
    return True


def _now() -> datetime:
    return datetime.now(UTC)


def _matches_explicit_action_binding(action: dict[str, Any], user_text: str) -> bool:
    normalized = str(user_text or "").strip().lower()
    if not normalized:
        return False
    if not any(marker in normalized for marker in ("确认", "同意", "允许", "改成", "改为", "拒绝", "取消")):
        return False
    keywords = {
        str(action.get("action_type") or "").replace(".", " ").lower(),
        str(action.get("action_label") or "").lower(),
        str(action.get("user_label") or "").lower(),
        str(action.get("user_summary") or "").lower(),
    }
    payload_summary = action.get("payload_summary") or {}
    if isinstance(payload_summary, dict):
        for key in ("display_name", "requested_software", "url", "path"):
            value = str(payload_summary.get(key) or "").strip().lower()
            if value:
                keywords.add(value)
                tail = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                if tail:
                    keywords.add(tail)
    compact_keywords = {
        item.replace("browser.", "").replace("terminal.", "").replace("file.", "").strip()
        for item in keywords
        if item
    }
    if any(keyword and keyword in normalized for keyword in compact_keywords):
        return True
    if action.get("action_type") == "browser.download":
        return "下载" in normalized and any(token in normalized for token in ("csv", "report", "文件"))
    if action.get("action_type") == "terminal.run":
        return "终端" in normalized or "命令" in normalized
    return False
