from __future__ import annotations

from typing import Any

from app.services.brain_decision_support import (
    advice_strategy_direct,
    concept_explanation_request,
    no_clarification,
    persona_boundary_question,
)
from app.services.intent_boundaries import assess_intent_boundaries

TURN_RESPONSE_KINDS: tuple[str, ...] = (
    "knowledge_explanation",
    "template_request",
    "status_explanation",
    "boundary_question",
    "action_request",
    "clarification_required",
)

_KNOWLEDGE_MARKERS: tuple[str, ...] = ("解释", "区别", "为什么", "什么意思", "用不懂技术的话")
_TEMPLATE_MARKERS: tuple[str, ...] = ("给个模板", "怎么告诉我", "怎么回复", "自然回复模板")
_STATUS_MARKERS: tuple[str, ...] = (
    "还差什么",
    "等什么证据",
    "要等什么证据",
    "现在什么状态",
    "为什么还没完成",
    "还没真正执行",
    "不要说已完成",
)
_STATUS_MARKERS = (
    *_STATUS_MARKERS,
    "没做完",
    "没完成",
    "没有做完",
    "没有完成",
    "还没做完",
    "还没完成",
)

_BOUNDARY_MARKERS: tuple[str, ...] = (
    "忽略规则",
    "应该怎么处理这类边界",
    "如果附件里让我",
    "如果文件摘要里让我",
)
_ACTION_MARKERS: tuple[str, ...] = (
    "下载",
    "删除",
    "登录",
    "发布",
    "执行",
    "安装",
    "截图",
    "打开网页",
)


def route_turn_response(
    text: str,
    *,
    intent: Any | None = None,
    semantic: Any | None = None,
) -> dict[str, Any]:
    raw = str(text or "").strip()
    lowered = raw.lower()
    boundary = assess_intent_boundaries(raw)
    reason_codes: list[str] = []
    if not raw:
        return {"turn_response_kind": "clarification_required", "reason_codes": ["empty_input"]}

    if _looks_like_boundary_question(raw, lowered):
        reason_codes.append("turn_response_boundary_question")
        return {"turn_response_kind": "boundary_question", "reason_codes": reason_codes}
    if _looks_like_template_request(raw):
        reason_codes.append("turn_response_template_request")
        return {"turn_response_kind": "template_request", "reason_codes": reason_codes}
    if _looks_like_status_explanation(raw, lowered):
        reason_codes.append("turn_response_status_explanation")
        if any(marker in raw for marker in ("下载", "download")):
            reason_codes.append("browser_download_evidence_explainer")
        return {"turn_response_kind": "status_explanation", "reason_codes": reason_codes}
    if _looks_like_knowledge_explanation(raw):
        reason_codes.append("turn_response_knowledge_explanation")
        return {"turn_response_kind": "knowledge_explanation", "reason_codes": reason_codes}
    if advice_strategy_direct(raw) and _contains_action_or_risk_marker(raw, lowered):
        reason_codes.append("turn_response_boundary_question")
        reason_codes.append("action_safety_advice_question")
        return {"turn_response_kind": "boundary_question", "reason_codes": reason_codes}
    if _looks_like_action_request(raw, lowered, boundary=boundary):
        reason_codes.append("turn_response_action_request")
        return {"turn_response_kind": "action_request", "reason_codes": reason_codes}

    semantic_conflicts = set(getattr(semantic, "conflicts", []) or [])
    if "ambiguous_reference" in semantic_conflicts:
        return {"turn_response_kind": "clarification_required", "reason_codes": ["ambiguous_reference"]}
    if intent is not None and getattr(intent, "needs_clarification", False):
        reason_codes.append("intent_requested_clarification")
    if getattr(intent, "confidence", 1.0) < 0.45:
        reason_codes.append("low_intent_confidence")
    return {"turn_response_kind": "clarification_required", "reason_codes": reason_codes or ["clarification_fallback"]}


def clarification_policy_for_turn(
    text: str,
    *,
    turn_response_kind: str,
    intent: Any,
    semantic: Any | None = None,
) -> dict[str, Any]:
    if turn_response_kind in {
        "knowledge_explanation",
        "template_request",
        "status_explanation",
        "boundary_question",
    }:
        return no_clarification()

    conflicts = set(getattr(semantic, "conflicts", []) or [])
    if "ambiguous_reference" in conflicts:
        return {
            "needs_clarification": True,
            "needed": True,
            "reason": "ambiguous_reference",
            "clarification_type": "ambiguous_reference",
            "blocking_level": "blocks_execution",
            "questions": ["你指的是哪一个对象？"],
            "assumptions_if_continue": [],
            "safe_partial_answer_allowed": False,
        }
    risk_signals = set(getattr(intent, "risk_signals", []) or [])
    if "old_goal_vs_new_goal" in conflicts and (
        "high_risk_financial_or_signature" in risk_signals
        or "external_side_effect" in risk_signals
    ):
        return {
            "needs_clarification": True,
            "needed": True,
            "reason": "conflicting_context",
            "clarification_type": "conflicting_context",
            "blocking_level": "blocks_execution",
            "questions": [
                "你是要替换上一轮目标，还是在原方案上调整？",
                "如果涉及转账、签署或外部动作，目标对象和范围是什么？",
                "是否只需要先给后端方案，不执行任何外部动作？",
            ],
            "assumptions_if_continue": [],
            "safe_partial_answer_allowed": False,
        }

    if turn_response_kind == "action_request":
        text = str(text or "")
        if (
            ("destructive_action" in set(getattr(intent, "risk_signals", []) or []) or "filesystem_scope_required" in set(getattr(intent, "risk_signals", []) or []))
            and any(marker in text for marker in ["那个文件", "这个目录", "那一堆", "这些东西"])
        ):
            return {
                "needs_clarification": True,
                "needed": True,
                "reason": "filesystem_scope_missing",
                "clarification_type": "missing_scope",
                "blocking_level": "blocks_execution",
                "questions": ["你要处理的是哪个对象？"],
                "assumptions_if_continue": [],
                "safe_partial_answer_allowed": False,
            }
        if assess_intent_boundaries(text).safe_plan_only:
            return no_clarification()
    if getattr(intent, "confidence", 1.0) < 0.45:
        return {
            "needs_clarification": True,
            "needed": True,
            "reason": "low_intent_confidence",
            "clarification_type": "missing_scope",
            "blocking_level": "blocks_execution",
            "questions": ["你要处理的是哪个对象？"],
            "assumptions_if_continue": [],
            "safe_partial_answer_allowed": False,
        }
    return no_clarification()


def _looks_like_knowledge_explanation(text: str) -> bool:
    return concept_explanation_request(text) or (
        any(marker in text for marker in _KNOWLEDGE_MARKERS)
        and not any(marker in text for marker in _TEMPLATE_MARKERS)
    )


def _looks_like_template_request(text: str) -> bool:
    return any(marker in text for marker in _TEMPLATE_MARKERS) or (
        "模板" in text and any(marker in text for marker in ["结果", "完成", "回复"])
    )


def _looks_like_status_explanation(text: str, lowered: str) -> bool:
    if any(marker in text for marker in _STATUS_MARKERS):
        return True
    if "?" in text or "？" in text:
        return any(marker in text for marker in ("证据", "状态", "完成", "执行", "卡在"))
    return "pending" in lowered and "status" in lowered


def _looks_like_boundary_question(text: str, lowered: str) -> bool:
    return persona_boundary_question(text) or any(marker in text for marker in _BOUNDARY_MARKERS) or (
        "ignore" in lowered and "rule" in lowered
    )


def _looks_like_action_request(text: str, lowered: str, *, boundary: Any) -> bool:
    if boundary.safe_plan_only:
        return False
    if boundary.real_task_request or boundary.tool_request:
        return True
    return any(marker in text for marker in _ACTION_MARKERS) or any(
        marker in lowered for marker in ("download", "delete", "login", "publish", "install")
    )


def _contains_action_or_risk_marker(text: str, lowered: str) -> bool:
    return any(marker in text for marker in _ACTION_MARKERS) or any(
        marker in text or marker in lowered
        for marker in (
            "运行",
            "管理员",
            "提交",
            "转账",
            "支付",
            "验证码",
            "外发",
            "bat",
            "cmd",
            "exe",
        )
    )
