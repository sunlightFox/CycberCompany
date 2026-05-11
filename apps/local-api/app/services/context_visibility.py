from __future__ import annotations

from typing import Any

from trace_service import redact


class ContextVisibilityService:
    """Keeps context scoped to the active session/thread and marks untrusted defaults."""

    def filter_recent_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        user_message: dict[str, Any] | None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        session_id = _session_id_from_message(user_message)
        if not session_id:
            return messages, {
                "same_session_only": False,
                "selected_count": len(messages),
                "filtered_count": 0,
                "untrusted_defaults": _UNTRUSTED_DEFAULTS,
                "reason_codes": ["session_context_unavailable", "current_message_first"],
            }

        filtered = [
            message
            for message in messages
            if message.get("session_id") in {None, session_id}
        ]
        selected = filtered or messages[-2:]
        filtered_count = max(0, len(messages) - len(selected))
        reason_codes = ["same_session_preferred", "current_message_first"]
        if filtered_count:
            reason_codes.append("cross_session_history_filtered")

        return selected, {
            "same_session_only": True,
            "selected_count": len(selected),
            "filtered_count": filtered_count,
            "untrusted_defaults": _UNTRUSTED_DEFAULTS,
            "reason_codes": reason_codes,
        }

    def build_untrusted_context(
        self,
        *,
        context_refs: list[dict[str, Any]],
        explicit_refs: list[dict[str, Any]] | None = None,
        trace_id: str | None = None,
        captured_at: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        items: list[dict[str, Any]] = []
        reason_codes = ["untrusted_context_collected"]
        for raw in [*list(context_refs or []), *list(explicit_refs or [])]:
            item = _normalize_untrusted_item(
                raw,
                trace_id=trace_id,
                captured_at=captured_at,
            )
            if item is None:
                continue
            items.append(item)
        if not items:
            reason_codes = ["untrusted_context_empty"]
        else:
            source_types = sorted(
                {
                    str(item.get("source_type") or "")
                    for item in items
                    if str(item.get("source_type") or "").strip()
                }
            )
            if source_types:
                reason_codes.append("source_types:" + ",".join(source_types[:4]))
        return items, {
            "selected_count": len(items),
            "reason_codes": reason_codes,
            "source_types": sorted(
                {
                    str(item.get("source_type") or "")
                    for item in items
                    if str(item.get("source_type") or "").strip()
                }
            ),
        }


_UNTRUSTED_DEFAULTS = ["context_refs", "quoted_forwarded_content", "tool_result_verbatim"]


def _session_id_from_message(message: dict[str, Any] | None) -> str | None:
    content = message.get("content") if message else {}
    value = content.get("session_id") if isinstance(content, dict) else None
    return str(value) if value else None


def _normalize_untrusted_item(
    item: dict[str, Any] | None,
    *,
    trace_id: str | None,
    captured_at: str | None,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    summary = str(
        item.get("summary")
        or item.get("snippet")
        or item.get("text")
        or item.get("title")
        or item.get("url")
        or ""
    ).strip()
    if not summary:
        return None
    redacted_summary = str(redact(summary))[:280]
    source_type = str(
        item.get("source_type")
        or item.get("type")
        or item.get("kind")
        or item.get("source_kind")
        or "external_context"
    )
    source_ref = {
        "id": item.get("id"),
        "url": item.get("url"),
        "label": item.get("label") or item.get("title"),
    }
    trusted_level = str(item.get("trusted_level") or _trusted_level_for_source(source_type))
    return {
        "source_type": source_type,
        "source_ref": {key: value for key, value in source_ref.items() if value},
        "trusted_level": trusted_level,
        "captured_at": captured_at,
        "trace_ref": {"trace_id": trace_id} if trace_id else {},
        "redaction_summary": {
            "applied": redacted_summary != summary,
            "raw_chars": len(summary),
            "model_safe_chars": len(redacted_summary),
        },
        "summary": redacted_summary,
    }


def _trusted_level_for_source(source_type: str) -> str:
    normalized = source_type.lower()
    if normalized in {"browser_evidence", "browser_page", "webpage", "search_result", "url"}:
        return "untrusted_external_content"
    if normalized in {"upload", "attachment", "user_file"}:
        return "user_provided_unverified"
    if normalized in {"tool_result", "tool_output", "mcp_output"}:
        return "tool_generated_unverified"
    if normalized in {"channel_context", "local_runtime"}:
        return "local_runtime"
    return "trusted_internal" if normalized.startswith("internal") else "untrusted_external_content"
