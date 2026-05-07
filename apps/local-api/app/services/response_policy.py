from __future__ import annotations

from app.schemas.chat_quality import ConversationUnderstanding, PresenceState, ResponsePolicyDecision, ResponsePolicyRequest


class ResponsePolicyService:
    def decide(self, request: ResponsePolicyRequest) -> ResponsePolicyDecision:
        understanding = request.understanding
        presence = request.presence_state
        posture = presence.interaction_posture
        continuity_mode = str(presence.conversation_state.get("continuity_mode") or "standalone")
        reason_codes = [f"posture:{posture}", f"mode:{understanding.conversation_mode}"]
        if continuity_mode:
            reason_codes.append(f"continuity:{continuity_mode}")

        if posture == "repair_previous_miss":
            return ResponsePolicyDecision(
                opening_style="repair_soft",
                depth_mode="light",
                followthrough_mode="repair",
                boundary_mode="none",
                progress_mode="reorient",
                memory_reference_mode="only_if_relevant_and_specific",
                structure_mode="minimal",
                tone_guardrails=["repair_first", "no_internal_failure_nouns"],
                continuation_expectation="strong",
                visible_failure_strategy="retry_softly",
                reason_codes=reason_codes,
            )
        if posture == "boundary_but_helpful":
            return ResponsePolicyDecision(
                opening_style="steady_boundary",
                depth_mode="light",
                followthrough_mode="boundary",
                boundary_mode="explicit_honest",
                progress_mode="next_step_after_boundary",
                memory_reference_mode="do_not_force",
                structure_mode="adaptive",
                tone_guardrails=["steady", "no_false_completion", "offer_next_step"],
                continuation_expectation="optional",
                visible_failure_strategy="boundary_helpful",
                reason_codes=reason_codes,
            )
        if understanding.latest_instruction_override:
            return ResponsePolicyDecision(
                opening_style="steady_natural",
                depth_mode="medium" if understanding.conversation_mode == "deep_talk" else "light",
                followthrough_mode="reorient",
                boundary_mode="none",
                progress_mode="answer_then_expand" if understanding.conversation_mode == "deep_talk" else "answer_directly",
                memory_reference_mode="do_not_force",
                structure_mode="structured_when_useful" if understanding.conversation_mode == "deep_talk" else "adaptive",
                tone_guardrails=["current_message_first", "drop_stale_goal", "no_unjustified_followthrough"],
                continuation_expectation="strong",
                visible_failure_strategy="partial_honest",
                reason_codes=reason_codes,
            )
        if understanding.conversation_mode == "deep_talk":
            return ResponsePolicyDecision(
                opening_style="judgment_first",
                depth_mode="deep",
                followthrough_mode="contextual" if continuity_mode == "followthrough" else "standalone",
                boundary_mode="none",
                progress_mode="answer_then_expand",
                memory_reference_mode="only_if_relevant_and_specific",
                structure_mode="structured_when_useful",
                tone_guardrails=["no_exam_template", "keep_human_rhythm", "current_message_first"],
                continuation_expectation="strong" if continuity_mode == "followthrough" else "optional",
                visible_failure_strategy="partial_honest",
                reason_codes=reason_codes,
            )
        if posture == "take_over":
            return ResponsePolicyDecision(
                opening_style="take_over",
                depth_mode="medium",
                followthrough_mode="active",
                boundary_mode="none",
                progress_mode="push_forward",
                memory_reference_mode="only_if_relevant_and_specific",
                structure_mode="adaptive",
                tone_guardrails=["take_over_without_overclaiming", "current_message_first"],
                continuation_expectation="strong",
                visible_failure_strategy="defer_with_anchor",
                reason_codes=reason_codes,
            )
        if understanding.current_turn_priority == "clarify_first":
            return ResponsePolicyDecision(
                opening_style="minimal_clarify",
                depth_mode="light",
                followthrough_mode="clarify",
                boundary_mode="none",
                progress_mode="ask_one_question",
                memory_reference_mode="do_not_force",
                structure_mode="minimal",
                tone_guardrails=["ask_minimum_question"],
                continuation_expectation="strong",
                visible_failure_strategy="defer_with_anchor",
                reason_codes=reason_codes,
            )
        return ResponsePolicyDecision(
            opening_style="steady_natural",
            depth_mode="light",
            followthrough_mode="standalone",
            boundary_mode="none",
            progress_mode="answer_directly",
            memory_reference_mode="only_if_relevant_and_specific" if presence.memory_state.get("active_user_profile") else "do_not_force",
            structure_mode="adaptive",
            tone_guardrails=["natural", "steady", "current_message_first"],
            continuation_expectation="optional",
            visible_failure_strategy="partial_honest",
            reason_codes=reason_codes,
        )
