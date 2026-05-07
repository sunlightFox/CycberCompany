from __future__ import annotations

from typing import Any

from app.schemas.chat_quality_shadow import ChatDialogueStateShadow


class ChatDialogueStateShadowService:
    def build(
        self,
        *,
        user_text: str,
        recent_messages: list[dict[str, Any]],
        brain_dialogue_state: dict[str, Any] | None = None,
        understanding: Any | None = None,
    ) -> ChatDialogueStateShadow:
        state = brain_dialogue_state if isinstance(brain_dialogue_state, dict) else {}
        active_topic = str(state.get("active_topic") or "").strip() or None
        user_goal = str(state.get("user_goal") or "").strip() or None
        open_loops = [str(item) for item in state.get("open_questions") or [] if str(item).strip()]
        pending_confirmation = dict(state.get("pending_confirmation") or {})
        source_present = bool(state)

        quality_dimensions: list[str] = []
        if getattr(understanding, "continues_previous_turn", False):
            turn_continuity = "followthrough"
            topic_shift_confidence = 0.1
            quality_dimensions.append("multi_turn_continuity")
        elif recent_messages:
            turn_continuity = "contextual"
            topic_shift_confidence = 0.3
        else:
            turn_continuity = "standalone"
            topic_shift_confidence = 0.7

        text = str(user_text or "")
        if any(marker in text for marker in ["换个话题", "另外", "顺便", "不过现在"]):
            turn_continuity = "topic_shift"
            topic_shift_confidence = 0.85

        if not active_topic:
            active_topic = text[:48] if text else None
        if not user_goal:
            user_goal = text[:96] if text else None
        if getattr(understanding, "constraint_tightening", False):
            open_loops.append("latest_instruction_should_override_previous_plan")
        if getattr(understanding, "action_request", False):
            open_loops.append("action_result_not_yet_visible")

        return ChatDialogueStateShadow(
            active_topic=active_topic,
            user_goal=user_goal,
            open_loops=list(dict.fromkeys(open_loops)),
            pending_confirmation=pending_confirmation,
            turn_continuity=turn_continuity,
            topic_shift_confidence=topic_shift_confidence,
            source_dialogue_state_present=source_present,
            quality_dimensions=sorted(set(quality_dimensions)),
        )

