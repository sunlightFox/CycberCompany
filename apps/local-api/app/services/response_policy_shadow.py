from __future__ import annotations

from typing import Any

from app.schemas.chat_quality_shadow import ResponsePolicyShadow


class ResponsePolicyShadowService:
    def recommend(
        self,
        *,
        understanding: Any,
        dialogue_state: Any,
        response_plan: dict[str, Any],
        privacy_level: str | None = None,
    ) -> ResponsePolicyShadow:
        scene = str(getattr(understanding, "primary_scene", "general_chat"))
        dimensions = list(getattr(understanding, "quality_dimensions", []) or [])
        risk_notes: list[str] = []

        if scene == "boundary_question":
            opening_style = "boundary_honest"
            boundary_mode = "explicit_honest"
            tool_narration_mode = "state_limits_then_next_step"
        elif scene == "deep_chat":
            opening_style = "natural_direct"
            boundary_mode = "none"
            tool_narration_mode = "answer_directly"
        elif getattr(understanding, "action_request", False):
            opening_style = "action_contextual"
            boundary_mode = "none"
            tool_narration_mode = "narrate_only_when_needed"
        else:
            opening_style = "natural_direct"
            boundary_mode = "none"
            tool_narration_mode = "answer_directly"

        depth_mode = "deep" if scene == "deep_chat" else "light"
        followthrough_mode = str(getattr(dialogue_state, "turn_continuity", "standalone"))
        continuation_expectation = "strong" if followthrough_mode == "followthrough" else "optional"

        route_semantics = (
            response_plan.get("route_semantics") if isinstance(response_plan, dict) else None
        )
        if isinstance(route_semantics, dict) and route_semantics.get("tool_created"):
            tool_narration_mode = "brief_progress"
            dimensions.append("tool_call_narration")
        if isinstance(route_semantics, dict) and route_semantics.get("approval_created"):
            boundary_mode = "approval_boundary"
            risk_notes.append("approval_pending_should_not_sound_completed")

        if getattr(understanding, "memory_related", False):
            memory_reference_mode = "only_if_relevant_and_specific"
            dimensions.append("memory_reference_fitness")
        else:
            memory_reference_mode = "do_not_force"

        if privacy_level in {"high", "restricted"}:
            risk_notes.append("privacy_high_keep_reply_plain_and_minimal")

        return ResponsePolicyShadow(
            opening_style=opening_style,
            depth_mode=depth_mode,
            followthrough_mode=followthrough_mode,
            boundary_mode=boundary_mode,
            tool_narration_mode=tool_narration_mode,
            memory_reference_mode=memory_reference_mode,
            continuation_expectation=continuation_expectation,
            quality_dimensions=sorted(set(dimensions)),
            risk_notes=risk_notes,
        )
