from __future__ import annotations

from core_types import IntentDecision, ModeDecision, TaskMode

from app.services.brain_decision_support import capability_available, execution_risks, mcp_unavailable_reason, skill_unavailable_reason


def mode_decision(intent: IntentDecision, capability_snapshot: dict[str, object]) -> ModeDecision:
    reasons: list[str] = []
    mode = TaskMode.DIRECT.value
    submode = "simple_answer"
    planner_hint = None
    fallback = None
    approval_first = bool(execution_risks(intent.risk_signals))
    if intent.execution_policy == "approval_only":
        submode = "approval_resolution"
        approval_first = False
        reasons.append("approval_only_resolution")
    elif intent.execution_policy == "readonly_tool":
        submode = "readonly_tool"
        approval_first = False
        reasons.append("readonly_tool_supported")
    elif intent.primary_intent in {"memory_query", "memory_update", "memory_correction"}:
        mode = TaskMode.DIRECT_WITH_MEMORY.value
        submode = "memory_answer"
        reasons.append("memory_visible_scope")
    elif intent.primary_intent == "system_filesystem_read":
        submode = "host_filesystem_read"
        approval_first = False
        reasons.append("readonly_host_tool_supported")
    elif intent.primary_intent == "browser_read":
        submode = "browser_readonly"
        approval_first = False
        reasons.append("readonly_browser_tool_supported")
    elif intent.primary_intent in {"task_request", "tool_request", "asset_management"}:
        mode = TaskMode.WORKFLOW.value
        submode = "plan_first" if approval_first else "workflow"
        planner_hint = "task_runtime"
        reasons.append("task_or_tool_required")
    elif intent.primary_intent == "skill_request":
        if capability_available(capability_snapshot, "skill_engine"):
            mode = TaskMode.WORKFLOW.value
            planner_hint = "skill_match_then_task"
            reasons.append("skill_available")
        else:
            submode = "capability_boundary"
            fallback = "explain_unavailable"
            reasons.append(skill_unavailable_reason(capability_snapshot))
    elif intent.primary_intent == "mcp_request":
        if capability_available(capability_snapshot, "mcp"):
            mode = TaskMode.WORKFLOW.value
            planner_hint = "mcp_tool_via_runtime"
            reasons.append("mcp_degraded_or_available")
        else:
            submode = "capability_boundary"
            fallback = "explain_unavailable"
            reasons.append(mcp_unavailable_reason(capability_snapshot))
    elif intent.primary_intent == "complex_dialogue":
        submode = "deep_answer"
        planner_hint = "structured_reasoning"
        reasons.append("complex_but_answerable")
    elif intent.primary_intent == "creative_writing":
        submode = "writing"
        reasons.append("writing_direct")
    elif intent.primary_intent == "boundary_question":
        submode = "boundary_answer"
        reasons.append("persona_capability_boundary")
    elif intent.primary_intent == "system_settings":
        mode = TaskMode.WORKFLOW.value
        planner_hint = "settings_guarded"
        reasons.append("settings_requires_backend_guard")
    elif intent.primary_intent == "unknown" or intent.confidence < 0.45:
        mode = "ask_clarification"
        submode = "low_confidence"
        fallback = "safe_direct"
        reasons.append("low_confidence")
    else:
        reasons.append("direct_answer_supported")
    return ModeDecision(
        mode=mode,
        submode=submode,
        planner_hint=planner_hint,
        requires_approval_before_execute=approval_first,
        fallback_mode=fallback,
        confidence=max(0.45, min(intent.confidence, 0.92)),
        reason_codes=reasons,
    )
