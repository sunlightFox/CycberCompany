from __future__ import annotations

from typing import Any

from app.schemas.chat_quality import PresenceState, PresenceStateRequest


class PresenceStateResolverService:
    def resolve(self, request: PresenceStateRequest) -> PresenceState:
        understanding = request.understanding
        working_state = dict(request.working_state or {})
        latest_continuity = dict(request.latest_continuity or {})
        user_profile = dict(request.user_profile or {})
        recent_messages = list(request.recent_messages or [])
        memory_candidates = list(request.memory_candidates or [])

        emotional_state = understanding.emotional_state
        identity_state = {
            "is_real_human": False,
            "has_hidden_account": False,
            "stable_style": _stable_style(user_profile),
            "identity_guardrails": [
                "not_real_human",
                "no_hidden_account",
                "no_false_completion",
            ],
        }
        relationship_state = {
            "familiarity": _familiarity(recent_messages, latest_continuity),
            "user_pressure": emotional_state if emotional_state in {"urgent", "anxious", "frustrated"} else "steady",
            "response_pacing": _response_pacing(user_profile, emotional_state),
            "humor_allowed": bool(user_profile.get("humor_allowed", emotional_state == "playful")),
        }
        continuity_mode = _continuity_mode(understanding=understanding)
        conversation_state = {
            "active_topic": str(
                understanding.user_goal
                if understanding.latest_instruction_override
                else (
                    working_state.get("active_topic")
                    or latest_continuity.get("topic_anchor")
                    or understanding.user_goal
                    or request.user_text[:48]
                )
            ),
            "user_goal": str(
                understanding.user_goal
                if understanding.latest_instruction_override
                else (working_state.get("user_goal") or understanding.user_goal)
            ),
            "latest_instruction_override": understanding.latest_instruction_override,
            "continuity_mode": continuity_mode,
        }
        pending_confirmation = dict(working_state.get("pending_confirmation") or {})
        action_state = {
            "pending_approval": bool(pending_confirmation),
            "running_task": bool(working_state.get("candidate_actions")),
            "blocked_action": bool(understanding.current_turn_priority == "block_first"),
            "recently_finished_action": _has_real_recent_completion(
                working_state=working_state,
                latest_continuity=latest_continuity,
            ),
        }
        memory_state = {
            "relevant_memory_count": len(memory_candidates),
            "active_user_profile": user_profile,
            "continuity_summary": str(latest_continuity.get("continuity_summary") or ""),
            "has_conflict_with_current_turn": understanding.latest_instruction_override,
        }
        session_state = {
            "continuity_summary": str(latest_continuity.get("continuity_summary") or ""),
            "repair_needed": bool(understanding.repair_needed or latest_continuity.get("repair_cue")),
            "followup_candidates": list(latest_continuity.get("followup_candidates") or []),
            "recent_turn_count": len(recent_messages),
        }
        interaction_posture = _interaction_posture(
            understanding=understanding,
            action_state=action_state,
            session_state=session_state,
        )
        return PresenceState(
            identity_state=identity_state,
            relationship_state=relationship_state,
            conversation_state=conversation_state,
            action_state=action_state,
            memory_state=memory_state,
            session_state=session_state,
            interaction_posture=interaction_posture,
            reason_codes=[
                f"mode:{understanding.conversation_mode}",
                f"priority:{understanding.current_turn_priority}",
                f"posture:{interaction_posture}",
            ],
        )


def _stable_style(user_profile: dict[str, Any]) -> list[str]:
    style = list(user_profile.get("stable_style") or [])
    if not style:
        style = ["warm", "direct", "honest"]
    return style


def _familiarity(recent_messages: list[dict[str, Any]], latest_continuity: dict[str, Any]) -> str:
    if latest_continuity.get("continuity_summary"):
        return "ongoing"
    if len(recent_messages) >= 6:
        return "familiar"
    if len(recent_messages) >= 2:
        return "warming"
    return "new"


def _response_pacing(user_profile: dict[str, Any], emotional_state: str) -> str:
    if emotional_state in {"urgent", "anxious"}:
        return "fast_steady"
    preference = str(user_profile.get("explanation_density") or "")
    if preference == "short":
        return "short_first"
    return "adaptive"


def _interaction_posture(
    *,
    understanding: Any,
    action_state: dict[str, Any],
    session_state: dict[str, Any],
) -> str:
    if session_state.get("repair_needed") or understanding.current_turn_priority == "repair_first":
        return "repair_previous_miss"
    if action_state.get("pending_approval") or understanding.conversation_mode == "boundary":
        return "boundary_but_helpful"
    if understanding.current_turn_priority == "clarify_first":
        return "clarify_minimally"
    if understanding.conversation_mode == "task_request":
        return "take_over"
    if action_state.get("recently_finished_action"):
        return "result_delivery"
    return "steady"


def _continuity_mode(*, understanding: Any) -> str:
    reason_codes = {str(item) for item in getattr(understanding, "reason_codes", [])}
    if understanding.latest_instruction_override:
        return "topic_switch"
    if "continuation_marker" in reason_codes and "reference_without_followthrough" not in reason_codes:
        return "followthrough"
    if understanding.conversation_mode in {"confirmation", "memory_correction"}:
        return "followthrough"
    if "reference_without_followthrough" in reason_codes:
        return "reference_only"
    return "standalone"


def _has_real_recent_completion(
    *,
    working_state: dict[str, Any],
    latest_continuity: dict[str, Any],
) -> bool:
    completion_markers = [
        working_state.get("recent_action_status"),
        working_state.get("last_action_status"),
        latest_continuity.get("action_status"),
        latest_continuity.get("detail_status"),
    ]
    return any(str(marker) in {"completed", "succeeded", "finished"} for marker in completion_markers)
