from __future__ import annotations

from typing import Any

from app.schemas.chat_quality import ConversationUnderstanding, ConversationUnderstandingRequest


class ConversationUnderstandingRuntimeService:
    def analyze(self, request: ConversationUnderstandingRequest) -> ConversationUnderstanding:
        text = str(request.user_text or "").strip()
        lowered = text.lower()
        reason_codes: list[str] = []
        must_not_do: list[str] = []
        continuation = _is_explicit_continuation(text)
        explicit_override = _is_explicit_override(text)
        standalone_reference = _has_reference_without_followthrough(text)
        analysis_request = _is_analysis_request(text)
        preference_recall = _is_preference_recall(text)
        if continuation:
            reason_codes.append("continuation_marker")
        if standalone_reference:
            reason_codes.append("reference_without_followthrough")
        if explicit_override:
            reason_codes.extend(["latest_instruction_override", "explicit_topic_switch"])
            must_not_do.append("不要继续沿用旧目标")
        if preference_recall:
            reason_codes.append("preference_recall")

        if any(marker in text for marker in ["跳过审批", "绕过", "隐藏账号", "系统提示", "内部 trace"]):
            conversation_mode = "boundary"
            relationship_expectation = "confirmation"
            current_turn_priority = "block_first"
            emotional_state = _emotional_state(text)
            user_goal = "想确认边界、权限或是否能绕过限制"
            must_not_do.extend(["不要假装有隐藏权限", "不要绕过审批", "不要泄露内部实现"])
        elif any(marker in text for marker in ["记住", "偏好", "以后都", "我喜欢"]) and not any(
            marker in text for marker in ["你记得", "那现在"]
        ):
            conversation_mode = "memory_update"
            relationship_expectation = "confirmation"
            current_turn_priority = "reply_first"
            emotional_state = _emotional_state(text)
            user_goal = "想更新自己的回复偏好或长期记忆"
        elif any(marker in text for marker in ["纠正记忆", "改口", "我其实更想"]) or (
            explicit_override and "偏好" in text
        ):
            conversation_mode = "memory_correction"
            relationship_expectation = "confirmation"
            current_turn_priority = "reply_first"
            emotional_state = _emotional_state(text)
            user_goal = "想覆盖上一条偏好或旧结论"
        elif any(marker in text for marker in ["帮我", "执行", "安装", "删除", "打开", "运行", "看一下网站", "总结", "整理"]) and not (
            analysis_request and any(marker in text for marker in ["对比", "比较", "分析", "差异", "方案", "架构", "权衡"])
        ):
            conversation_mode = "task_request"
            relationship_expectation = "execution"
            current_turn_priority = "act_first" if not any(marker in text for marker in ["不要执行", "先别执行"]) else "reply_first"
            emotional_state = _emotional_state(text)
            user_goal = _task_goal(text)
            if "不要执行" in text or "先别执行" in text:
                must_not_do.append("不要直接执行")
        elif preference_recall or (continuation and not explicit_override and not analysis_request):
            conversation_mode = "confirmation"
            relationship_expectation = "confirmation"
            current_turn_priority = "reply_first"
            emotional_state = _emotional_state(text)
            user_goal = "想确认你是否承接了前文或记住了刚才的口径"
        elif analysis_request:
            conversation_mode = "deep_talk"
            relationship_expectation = "explanation"
            current_turn_priority = "reply_first"
            emotional_state = _emotional_state(text)
            user_goal = "想得到有判断、有层次的分析"
            if standalone_reference:
                reason_codes.append("analysis_request_overrides_boundary")
        elif any(marker in text for marker in ["缺什么", "能不能确认", "不要猜"]) or "?" in text or "？" in text:
            conversation_mode = "question"
            relationship_expectation = "explanation"
            current_turn_priority = "reply_first"
            emotional_state = _emotional_state(text)
            user_goal = "想得到明确回答或知道当前还能确认到哪一步"
        else:
            conversation_mode = "casual"
            relationship_expectation = "companionship" if any(marker in text for marker in ["你好", "在吗", "聊聊", "有点焦虑"]) else "advice"
            current_turn_priority = "reply_first"
            emotional_state = _emotional_state(text)
            user_goal = "想先被接住，再得到一个自然继续的回应"

        if request.has_pending_action and any(marker in text for marker in ["继续", "就这次", "确认", "算了", "拒绝"]):
            current_turn_priority = "reply_first"
            reason_codes.append("pending_action_resolution")
        if (
            explicit_override
            and request.recent_messages
            and current_turn_priority not in {"block_first", "act_first"}
        ):
            current_turn_priority = "repair_first" if any("失败" in str(item.get("content_text") or "") for item in request.recent_messages[-2:]) else "reply_first"
        if any(marker in text for marker in ["急", "赶", "马上"]) and current_turn_priority == "clarify_first":
            current_turn_priority = "reply_first"
        interaction_posture_candidates = _posture_candidates(
            conversation_mode,
            current_turn_priority,
            emotional_state,
            request.has_pending_action,
            explicit_override=explicit_override,
        )
        repair_needed = bool(explicit_override and request.latest_summary and "失败" in request.latest_summary)
        if repair_needed:
            reason_codes.append("repair_needed")

        return ConversationUnderstanding(
            conversation_mode=conversation_mode,
            user_goal=user_goal,
            relationship_expectation=relationship_expectation,
            current_turn_priority=current_turn_priority,
            emotional_state=emotional_state,
            latest_instruction_override=explicit_override,
            must_not_do=list(dict.fromkeys(must_not_do)),
            confidence=_confidence(conversation_mode, reason_codes),
            reason_codes=reason_codes,
            interaction_posture_candidates=interaction_posture_candidates,
            repair_needed=repair_needed,
        )


def _task_goal(text: str) -> str:
    if "对比" in text:
        return "想要一版可直接阅读的对比结果"
    if "总结" in text or "整理" in text:
        return "想把输入内容整理成可直接使用的结果"
    if "安装" in text or "删除" in text:
        return "想确认是否能推进真实动作并知道当前边界"
    return "想让助手接手当前目标并往前推进"


def _emotional_state(text: str) -> str:
    if any(marker in text for marker in ["焦虑", "慌", "难受"]):
        return "anxious"
    if any(marker in text for marker in ["急", "赶", "马上"]):
        return "urgent"
    if any(marker in text for marker in ["烦", "生气", "火大"]):
        return "frustrated"
    if any(marker in text for marker in ["聊聊", "轻松", "哈哈"]):
        return "playful"
    if any(marker in text for marker in ["你好", "在吗"]):
        return "warm"
    return "neutral"


def _posture_candidates(
    conversation_mode: str,
    current_turn_priority: str,
    emotional_state: str,
    has_pending_action: bool,
    *,
    explicit_override: bool,
) -> list[str]:
    items: list[str] = []
    if current_turn_priority == "repair_first":
        items.append("repair_previous_miss")
    if conversation_mode == "boundary" or has_pending_action:
        items.append("boundary_but_helpful")
    if current_turn_priority == "clarify_first":
        items.append("clarify_minimally")
    if conversation_mode == "task_request" and not explicit_override:
        items.append("take_over")
    if emotional_state in {"anxious", "urgent", "frustrated"}:
        items.append("steady")
    items.append("steady")
    return list(dict.fromkeys(items))


def _confidence(conversation_mode: str, reason_codes: list[str]) -> float:
    if conversation_mode in {"boundary", "memory_update", "memory_correction"}:
        return 0.9
    if reason_codes:
        return 0.78
    return 0.62


def _is_explicit_continuation(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "继续刚才",
            "接着刚才",
            "顺着刚才",
            "沿着刚才",
            "继续上一条",
            "接上刚才",
            "继续那个方案",
        ]
    )


def _is_explicit_override(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "改成",
            "换成",
            "只讨论",
            "不讨论",
            "先别执行",
            "前面那个不算",
            "先别沿用前面",
            "先别管刚才",
            "刚才那个先别管",
            "按我最新这句",
            "现在只回答这句",
            "纠正记忆",
            "我其实更想",
        ]
    )


def _has_reference_without_followthrough(text: str) -> bool:
    has_reference = any(marker in text for marker in ["刚才", "前面", "上一条", "上个", "之前"])
    if not has_reference:
        return False
    return not _is_explicit_continuation(text)


def _is_analysis_request(text: str) -> bool:
    return any(marker in text for marker in ["怎么", "为什么", "对比", "比较", "详细", "架构", "分析", "方案", "权衡", "差异", "讨论"])


def _is_preference_recall(text: str) -> bool:
    return any(marker in text for marker in ["你记得", "那现在", "我刚才说的", "回复偏好是什么"])
