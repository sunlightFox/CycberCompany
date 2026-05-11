from __future__ import annotations

from core_types import IntentDecision, ModeDecision, SemanticIntentCandidate

from app.services.brain_decision_support import ambiguous_scope, clarify, multimodal_attachment_context, no_clarification, safe_plan_only


def clarification_decision(
    text: str,
    intent: IntentDecision,
    mode: ModeDecision,
    semantic: SemanticIntentCandidate | None = None,
) -> dict[str, object]:
    del mode
    conflicts = set(semantic.conflicts if semantic else [])
    if intent.primary_intent in {"boundary_question", "system_filesystem_read", "browser_read"}:
        return no_clarification()
    if "ambiguous_reference" in conflicts:
        return clarify("ambiguous_reference", ["你指的是上一轮哪个对象或方案？"], clarification_type="ambiguous_reference")
    if "old_goal_vs_new_goal" in conflicts:
        if any(marker in text for marker in ["不对", "改成", "只做", "换成"]):
            high_risk_change = bool({"high_risk_financial_or_signature", "external_side_effect"} & set(intent.risk_signals))
            if not high_risk_change:
                return no_clarification()
        return clarify("conflicting_context", ["你是要替换上一轮目标，还是在原方案上调整约束？"], clarification_type="conflicting_context")
    if intent.confidence < 0.45:
        return clarify("low_intent_confidence", ["你希望我先回答、先规划，还是创建任务？"], clarification_type="missing_goal")
    if not intent.risk_signals or safe_plan_only(text):
        return no_clarification()
    if "high_risk_financial_or_signature" in intent.risk_signals:
        return clarify("high_risk_without_confirmation", ["要使用哪个账户或钱包？", "对象、金额或签名内容是什么？", "是否只需要方案说明？"], clarification_type="missing_destination")
    if "external_side_effect" in intent.risk_signals:
        return clarify("missing_target_scope", ["目标平台或账号是什么？", "最终内容和受众范围是什么？", "是否只需要草稿？"], clarification_type="missing_destination")
    if ("destructive_action" in intent.risk_signals or "filesystem_scope_required" in intent.risk_signals) and ambiguous_scope(text):
        if "destructive_action" not in intent.risk_signals and "filesystem_scope_required" in intent.risk_signals and multimodal_attachment_context(text):
            return no_clarification()
        if "任务" in text and "destructive_action" not in intent.risk_signals:
            return no_clarification()
        return clarify("filesystem_scope_missing", ["目标文件或范围是什么？", "是否需要只读预览或备份？"], clarification_type="missing_scope")
    return no_clarification()
