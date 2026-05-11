from __future__ import annotations

from typing import Any, Callable

from app.services.chat_heuristics import (
    phase89_heuristic_inventory,
    phase89_heuristic_summary,
)
from app.services.chat_turn_input_facts import (
    format_sensitive_chat_request,
    looks_like_ambiguous_clarification_followup,
    looks_like_execution_state_explanation_request,
    looks_like_explicit_continuation,
    looks_like_latest_instruction_override,
    looks_like_plain_analysis_request,
    looks_like_short_followup,
    needs_recent_history_lookup,
)


def phase89_heuristic_runtime(
    user_text: str,
    *,
    pending_clarification_active: bool,
    deterministic_boundary_reply: Callable[[str], str | None],
) -> dict[str, Any]:
    soft_reason_codes: list[str] = []
    if looks_like_explicit_continuation(user_text):
        soft_reason_codes.append("explicit_continuation")
    if looks_like_plain_analysis_request(user_text):
        soft_reason_codes.append("plain_analysis_request")
    if looks_like_latest_instruction_override(user_text):
        soft_reason_codes.append("latest_instruction_override")
    if needs_recent_history_lookup(user_text):
        soft_reason_codes.append("recent_history_lookup")
    if looks_like_short_followup(user_text):
        soft_reason_codes.append("short_followup")
    if looks_like_execution_state_explanation_request(user_text):
        soft_reason_codes.append("execution_state_explanation_request")
    if looks_like_ambiguous_clarification_followup(user_text):
        soft_reason_codes.append("clarification_followup_candidate")

    hard_reason_codes: list[str] = []
    if deterministic_boundary_reply(user_text) is not None:
        hard_reason_codes.append("deterministic_boundary_guard")
    if format_sensitive_chat_request(user_text):
        hard_reason_codes.append("strict_format_guard")

    summary = phase89_heuristic_summary()
    return {
        "heuristic_inventory": phase89_heuristic_inventory(),
        "heuristic_reason_codes": {
            "hard_guard": hard_reason_codes,
            "soft_heuristic": soft_reason_codes,
        },
        "heuristic_governance": {
            "phase89_registry_version": summary["phase89_registry_version"],
            "hard_guard_count": summary["hard_guard_count"],
            "deprecated_soft_heuristic_count": summary[
                "deprecated_soft_heuristic_count"
            ],
            "terminal_soft_heuristic_count": summary[
                "terminal_soft_heuristic_count"
            ],
            "pending_clarification_active": pending_clarification_active,
            "soft_heuristics_do_not_terminate_mainline": True,
        },
        "false_interception_evidence_refs": [
            {
                "type": "doc",
                "path": "docs/测试/聊天主链路/2026-05-07-wechat-20-scenarios/evidence/summary.json",
            }
        ],
    }
