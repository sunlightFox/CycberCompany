from __future__ import annotations

from core_types import ContextDecision, DialogueState, IntentDecision, ModeDecision, SemanticIntentCandidate, TaskMode


def context_decision(
    intent: IntentDecision,
    mode: ModeDecision,
    has_conversation: bool,
    *,
    working_state: dict[str, object] | None = None,
    dialogue_state: DialogueState | None = None,
    semantic: SemanticIntentCandidate | None = None,
) -> ContextDecision:
    is_continuation = "continue_previous_topic" in intent.secondary_intents
    has_working_state = bool(working_state)
    include_memory = intent.needs_memory or intent.primary_intent == "memory_query"
    include_handles = bool(intent.needs_tool or intent.needs_task or intent.needs_mcp)
    has_context_conflict = bool(semantic and semantic.conflicts)
    include_conversation_state = has_conversation and (
        (is_continuation and has_working_state)
        or (dialogue_state is not None and bool(dialogue_state.topic_shift))
        or has_context_conflict
        or intent.primary_intent in {"complex_dialogue", "memory_query", "memory_update", "memory_correction", "task_request", "skill_request", "mcp_request"}
    )
    include_summary = has_conversation and (include_conversation_state or intent.primary_intent in {"task_request", "skill_request", "mcp_request"})
    include_recent = has_conversation
    token_profile = "deep_dialogue" if intent.primary_intent == "complex_dialogue" else "balanced"
    if mode.submode == "writing":
        token_profile = "long_writing"
    reasons = ["current_input", "capability_boundary_summary"]
    if include_conversation_state:
        reasons.append("working_state_continuation" if is_continuation and has_working_state else "conversation_state")
    if include_recent:
        reasons.append("recent_messages")
    if include_summary:
        reasons.append("session_summary")
    if include_memory:
        reasons.append("memory_explicit_query" if intent.primary_intent == "memory_query" else "memory_query_enabled")
    if include_handles:
        reasons.append("asset_handle_summary_only")
    if mode.submode == "capability_boundary":
        reasons.extend(["capability_boundary_unavailable", "capability_unavailable"])
    if semantic and semantic.conflicts:
        reasons.append("context_conflict_detected")
    return ContextDecision(
        include_recent_messages=include_recent,
        include_conversation_state=include_conversation_state,
        include_session_summary=include_summary,
        include_memory=include_memory,
        memory_query={"enabled": include_memory, "layers": ["semantic", "episodic", "procedural"], "max_items": 8 if include_memory else 0},
        include_persona=True,
        include_heart=True,
        include_capability_summary=True,
        include_asset_handles=include_handles,
        include_task_state=mode.mode in {TaskMode.WORKFLOW.value, TaskMode.AGENT.value},
        include_artifact_summary=intent.primary_intent in {"complex_dialogue", "task_request"},
        untrusted_refs=[],
        token_budget_profile=token_profile,
        selection_reason=reasons,
    )
