from __future__ import annotations

from core_types import DialogueState, IntentDecision, PrivacyLevel, SemanticIntentCandidate

from app.services.brain_decision_support import (
    advice_strategy_direct,
    capability_available,
    concept_explanation_request,
    confidence,
    continuation_reference,
    complexity,
    dedupe,
    domain,
    execution_risks,
    filesystem_scope_action,
    log_data_extraction,
    mcp_request,
    memory_correction,
    memory_write,
    persona_boundary_question,
    skill_request,
    skill_unavailable_reason,
    mcp_unavailable_reason,
    system_settings,
    unknown_input,
)
from app.services.chat_intent_router import (
    direct_only_requested,
    format_sensitive_direct_answer_requested,
    is_structured_summary_request,
    is_browser_page_action_request,
    is_explicit_download_request,
    is_file_mutation_request,
    is_readonly_route_request,
    is_host_filesystem_list_request,
    is_host_software_install_request,
    is_office_document_request,
    is_skill_or_mcp_concept_request,
    is_webpage_read_request,
)
from app.services.chat_turn_input_facts import looks_like_execution_state_explanation_request
from app.services.intent_boundaries import assess_intent_boundaries


def intent_decision(
    text: str,
    privacy_level: str,
    capability_snapshot: dict[str, object],
    *,
    working_state: dict[str, object] | None = None,
    dialogue_state: DialogueState | None = None,
    semantic: SemanticIntentCandidate | None = None,
) -> IntentDecision:
    clean = text.strip()
    lowered = clean.lower()
    boundary = assess_intent_boundaries(clean)
    secondary: list[str] = []
    risks: list[str] = []
    rule_hits: list[str] = []
    needs_memory = False
    needs_tool = False
    needs_skill = False
    needs_mcp = False
    needs_task = False
    primary = "casual_chat"
    direct_only = direct_only_requested(clean)
    format_sensitive_request = format_sensitive_direct_answer_requested(clean)
    has_pending_confirmation = _has_pending_confirmation(working_state)
    readonly_route = is_readonly_route_request(clean)
    if unknown_input(clean):
        primary = "unknown"
        rule_hits.append("low_information_input")
    elif log_data_extraction(clean):
        primary = "simple_question"
        secondary.append("data_extraction")
        rule_hits.append("data_extraction_question")
    elif boundary.safe_plan_only:
        primary = "simple_question"
        secondary.append("make_plan")
        rule_hits.append("safe_plan_only")
    elif is_structured_summary_request(clean):
        primary = "summarization"
        rule_hits.append("structured_summary_request")
    elif persona_boundary_question(clean):
        primary = "boundary_question"
        secondary.append("persona_capability_boundary")
        rule_hits.append("persona_boundary_question")
    elif memory_write(clean):
        primary = "memory_update"
        needs_memory = True
        rule_hits.append("explicit_memory_write")
    elif boundary.memory_query:
        primary = "memory_query"
        needs_memory = True
        rule_hits.append("memory_reference")
    elif memory_correction(clean):
        primary = "memory_correction"
        needs_memory = True
        rule_hits.append("memory_correction")
    elif advice_strategy_direct(clean):
        primary = "complex_dialogue"
        secondary.append("make_plan")
        rule_hits.append("phase51_advice_strategy_direct")
    elif format_sensitive_request and is_skill_or_mcp_concept_request(clean):
        primary = "simple_question"
        secondary.extend(["explain_concept", "strict_format_reply"])
        rule_hits.append("format_sensitive_skill_mcp_explanation")
    elif concept_explanation_request(clean):
        primary = "simple_question"
        secondary.append("explain_concept")
        rule_hits.append("concept_explanation_request")
    elif is_office_document_request(clean):
        skill_available = capability_available(capability_snapshot, "skill_engine")
        primary = "skill_request" if skill_available else "task_request"
        needs_skill = skill_available
        needs_task = True
        needs_tool = not skill_available
        secondary.append("generate_document")
        rule_hits.append("office_document_request")
    elif is_host_filesystem_list_request(clean):
        primary = "system_filesystem_read"
        needs_tool = True
        secondary.append("filesystem_readonly")
        rule_hits.append("host_filesystem_list_readonly")
    elif is_webpage_read_request(clean):
        primary = "browser_read"
        needs_tool = True
        secondary.append("webpage_readonly")
        rule_hits.append("browser_read_page_readonly")
    elif is_host_software_install_request(clean):
        primary = "task_request"
        needs_task = True
        needs_tool = True
        secondary.append("host_software_change")
        rule_hits.append("host_software_install_request")
    elif is_file_mutation_request(clean):
        primary = "task_request"
        needs_task = True
        needs_tool = True
        secondary.extend(["delete_or_destructive", "filesystem_scope"])
        risks.extend(["destructive_action", "filesystem_scope_required"])
        rule_hits.append("file_mutation_request")
    elif system_settings(clean):
        primary = "system_settings"
        rule_hits.append("settings_keyword")
    elif _approval_response(clean, has_pending_confirmation):
        primary = "approval_response"
        rule_hits.append("approval_response")
    elif has_pending_confirmation and looks_like_execution_state_explanation_request(clean):
        primary = "simple_question"
        secondary.append("pending_execution_state_explanation")
        rule_hits.append("pending_execution_state_explanation")
    elif "取消" in clean or "重试" in clean:
        primary = "cancel_or_retry"
        rule_hits.append("cancel_retry")
    elif skill_request(clean) and not format_sensitive_request:
        primary = "skill_request"
        needs_skill = capability_available(capability_snapshot, "skill_engine")
        needs_task = needs_skill
        rule_hits.append("skill_keyword")
    elif mcp_request(clean) and not format_sensitive_request:
        primary = "mcp_request"
        needs_mcp = capability_available(capability_snapshot, "mcp")
        needs_task = needs_mcp
        rule_hits.append("mcp_keyword")
    elif boundary.real_task_request:
        primary = "task_request"
        needs_task = True
        rule_hits.append("real_task_request")
    elif is_browser_page_action_request(clean):
        primary = "task_request"
        needs_tool = True
        needs_task = True
        rule_hits.append("browser_page_action")
    elif boundary.tool_request:
        primary = "task_request"
        needs_tool = True
        needs_task = True
        rule_hits.append("tool_or_external_action")
    elif any(word in clean for word in ["写", "润色", "改写", "文案", "草稿", "文章"]):
        primary = "creative_writing"
        secondary.append("generate_document")
        rule_hits.append("writing_keyword")
    elif any(word in clean for word in ["总结", "summary", "summarize"]):
        primary = "summarization"
        rule_hits.append("summary_keyword")
    elif continuation_reference(clean) and working_state:
        primary = "complex_dialogue"
        secondary.append("continue_previous_topic")
        rule_hits.append("working_state_continuation")
    elif complexity(clean) >= 0.5 or any(word in clean for word in ["方案", "对比", "架构", "排查"]):
        primary = "complex_dialogue"
        secondary.append("make_plan")
        rule_hits.append("complex_dialogue_keyword")
    elif clean.endswith("?") or clean.endswith("？") or any(
        word in lowered for word in ["什么", "为什么", "哪些", "什么情况下", "how", "why"]
    ):
        primary = "simple_question"
        rule_hits.append("question")

    if continuation_reference(clean):
        secondary.append("continue_previous_topic")
        if working_state and "working_state_continuation" not in rule_hits:
            rule_hits.append("working_state_continuation")
    if any(word in clean for word in ["对比", "还是", "取舍"]):
        secondary.append("compare_options")
    if any(word in clean for word in ["调试", "报错", "错误", "排查"]):
        secondary.append("debug_problem")
    if any(word in clean for word in ["删除", "清空", "覆盖"]) and not advice_strategy_direct(clean):
        secondary.append("delete_or_destructive")
        risks.append("destructive_action")
    if is_host_software_install_request(clean) and not advice_strategy_direct(clean):
        risks.extend(["host_software_change", "destructive_action"])
    if filesystem_scope_action(clean) and not advice_strategy_direct(clean) and not is_host_filesystem_list_request(clean):
        secondary.append("filesystem_scope")
        risks.append("filesystem_scope_required")
    if is_explicit_download_request(clean) or "截图" in clean:
        secondary.append("browser_side_effect")
        risks.append("browser_artifact_or_download")
    if any(word in clean for word in ["发帖", "发布", "发送", "提交"]):
        secondary.append("external_submit")
        risks.append("external_side_effect")
    if any(word in clean for word in ["购买", "下单", "转账", "支付", "签名"]):
        secondary.append("wallet_or_payment")
        risks.append("high_risk_financial_or_signature")
    if privacy_level == PrivacyLevel.HIGH.value or privacy_level == "high":
        risks.append("secret_or_sensitive")
    if semantic is not None:
        secondary.extend(semantic.secondary_intents)
        risks.extend(semantic.risk_intents)
        if (
            semantic.tool_intents
            and not direct_only
            and not readonly_route
            and not (has_pending_confirmation and looks_like_execution_state_explanation_request(clean))
            and not boundary.safe_plan_only
            and not persona_boundary_question(clean)
            and not advice_strategy_direct(clean)
            and not concept_explanation_request(clean)
            and not is_office_document_request(clean)
            and not is_host_filesystem_list_request(clean)
            and not is_webpage_read_request(clean)
        ):
            primary = "task_request"
            needs_tool = True
            needs_task = True
            rule_hits.append("semantic_tool_intent")
        if "old_goal_vs_new_goal" in semantic.conflicts and dialogue_state is not None:
            secondary.append("goal_changed")
            rule_hits.append("semantic_context_conflict")
        if semantic.conflicts:
            rule_hits.extend(semantic.conflicts)
    interaction_class, execution_policy = _interaction_contract(
        primary_intent=primary,
        needs_task=needs_task,
        direct_only_requested=direct_only,
        readonly_route=readonly_route,
        has_pending_confirmation=has_pending_confirmation,
    )
    return IntentDecision(
        primary_intent=primary,
        secondary_intents=dedupe(secondary),
        semantic_candidates=[{"semantic_candidate_id": semantic.semantic_candidate_id, "primary_intent": semantic.primary_intent, "confidence": semantic.confidence}] if semantic else [],
        conflicts=semantic.conflicts if semantic else [],
        actionable_intents=semantic.actionable_intents if semantic else [],
        memory_intents=semantic.memory_intents if semantic else [],
        tool_intents=semantic.tool_intents if semantic else [],
        risk_intents=semantic.risk_intents if semantic else [],
        domain=domain(clean),
        complexity_score=complexity(clean),
        privacy_level=privacy_level,
        risk_signals=dedupe(risks),
        needs_memory=needs_memory,
        needs_tool=needs_tool,
        needs_skill=needs_skill,
        needs_mcp=needs_mcp,
        needs_task=needs_task,
        needs_clarification=False,
        confidence=confidence(primary, rule_hits, risks, clean),
        reason_codes=dedupe([*rule_hits, *risks]),
        rule_hits=rule_hits,
        interaction_class=interaction_class,
        execution_policy=execution_policy,
        direct_only_requested=direct_only,
        model_hint={"enabled": False, "source": "rule_first_phase18"},
    )


def _has_pending_confirmation(working_state: dict[str, object] | None) -> bool:
    if not isinstance(working_state, dict):
        return False
    pending = working_state.get("pending_confirmation")
    return isinstance(pending, dict) and bool(pending)


def _approval_response(text: str, has_pending_confirmation: bool) -> bool:
    if not has_pending_confirmation:
        return False
    if "确认这次" in text:
        return True
    stripped = text.strip()
    if stripped in {"同意", "批准", "拒绝", "deny", "approve"}:
        return True
    return any(marker in text for marker in ["我批准", "我拒绝", "确认执行", "只允许这一次", "本会话内同类操作都允许"])


def _interaction_contract(
    *,
    primary_intent: str,
    needs_task: bool,
    direct_only_requested: bool,
    readonly_route: bool,
    has_pending_confirmation: bool,
) -> tuple[str, str]:
    if primary_intent == "boundary_question":
        return "boundary_block", "no_task"
    if primary_intent == "approval_response":
        return "approval_resolution", "approval_only"
    if readonly_route or primary_intent in {
        "browser_read",
        "system_filesystem_read",
    }:
        return "direct_readonly", "readonly_tool"
    if needs_task or primary_intent in {"task_request", "skill_request", "mcp_request", "system_settings"}:
        return "workflow_action", "task_required"
    if direct_only_requested or has_pending_confirmation:
        return "direct_explanation", "no_task"
    return "direct_explanation", "no_task"
