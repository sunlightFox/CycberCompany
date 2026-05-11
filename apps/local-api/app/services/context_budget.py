from __future__ import annotations

from typing import Any, Callable

from trace_service import redact


_BUDGET_PROFILES: dict[str, dict[str, int]] = {
    "balanced": {
        "recent_history": 20,
        "conversation_summary": 8,
        "session_context": 7,
        "memory": 20,
        "persona_heart": 10,
        "capability_and_handles": 10,
        "untrusted_context": 10,
        "reserved": 15,
    },
    "direct_chat": {
        "recent_history": 24,
        "conversation_summary": 7,
        "session_context": 10,
        "memory": 16,
        "persona_heart": 14,
        "capability_and_handles": 7,
        "untrusted_context": 7,
        "reserved": 15,
    },
    "tool_action": {
        "recent_history": 14,
        "conversation_summary": 6,
        "session_context": 12,
        "memory": 14,
        "persona_heart": 8,
        "capability_and_handles": 18,
        "untrusted_context": 12,
        "reserved": 16,
    },
    "knowledge": {
        "recent_history": 14,
        "conversation_summary": 7,
        "session_context": 8,
        "memory": 24,
        "persona_heart": 8,
        "capability_and_handles": 14,
        "untrusted_context": 12,
        "reserved": 13,
    },
}


class ContextBudgetService:
    """Applies conservative history trimming while preserving current-turn priority."""

    def allocate_layer_budgets(
        self,
        *,
        token_budget: int,
        profile: str = "balanced",
    ) -> dict[str, Any]:
        normalized = profile if profile in _BUDGET_PROFILES else "balanced"
        percentages = dict(_BUDGET_PROFILES[normalized])
        allocations = {
            layer: max(1, int(token_budget * pct / 100))
            for layer, pct in percentages.items()
        }
        return {
            "profile": normalized,
            "token_budget": int(token_budget),
            "allocations": allocations,
            "compression_order": ["recent_history", "untrusted_context", "memory"],
            "preserve_layers": [
                "session_context",
                "persona_heart",
                "safety_notes",
            ],
        }

    def select_recent_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        token_budget: int,
        estimate_tokens: Callable[[str], int],
        preserve_latest: int = 2,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        token_total = 0
        dropped_count = 0

        for reverse_index, item in enumerate(reversed(messages)):
            sanitized = _sanitize_message(item)
            text = str(sanitized.get("model_safe_content_text") or "")
            next_total = token_total + estimate_tokens(text)
            must_keep = reverse_index < max(1, preserve_latest)
            if next_total > token_budget and selected and not must_keep:
                dropped_count += 1
                continue
            token_total = next_total
            selected.append(sanitized)

        selected.reverse()
        reason_codes = ["current_message_first"]
        if dropped_count:
            reason_codes.append("history_trimmed_to_budget")
        if len(selected) > preserve_latest:
            reason_codes.append("latest_history_preserved")

        return selected, {
            "token_budget": int(token_budget),
            "selected_count": len(selected),
            "dropped_count": int(dropped_count),
            "current_message_priority_preserved": True,
            "reason_codes": reason_codes,
        }


def _sanitize_message(item: dict[str, Any]) -> dict[str, Any]:
    raw_text = str(item.get("content_text") or "")
    redacted_text = str(redact(raw_text))
    return {
        "author_type": item["author_type"],
        "content_text": redacted_text,
        "model_safe_content_text": redacted_text,
        "redaction_summary": {
            "applied": redacted_text != raw_text,
            "raw_chars": len(raw_text),
            "model_safe_chars": len(redacted_text),
        },
        "created_at": item["created_at"],
        "session_id": (
            item.get("content", {}).get("session_id")
            if isinstance(item.get("content"), dict)
            else None
        ),
    }
