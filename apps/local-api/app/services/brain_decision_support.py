from __future__ import annotations

from typing import Any

from trace_service import redact

from app.services.chat_intent_router import is_office_document_request
from app.services.chat_turn_input_facts import (
    explicit_preference_recall_query,
    preference_application_request,
    structured_summary_chat_request,
)
from app.services.intent_boundaries import (
    assess_intent_boundaries as _assess_intent_boundaries,
    looks_like_chatty_delivery as _shared_chatty_delivery_request,
    looks_like_safe_plan_only as _shared_safe_plan_only,
    should_treat_as_memory_query as _shared_memory_query,
    should_treat_as_real_task_request as _shared_real_task_request,
    should_treat_as_tool_request as _shared_tool_request,
)


def clarify(reason: str, questions: list[str], *, clarification_type: str) -> dict[str, Any]:
    return {
        "needs_clarification": True,
        "needed": True,
        "reason": reason,
        "clarification_type": clarification_type,
        "blocking_level": "blocks_execution",
        "questions": questions[:3],
        "assumptions_if_continue": [],
        "safe_partial_answer_allowed": reason != "high_risk_without_confirmation",
    }


def no_clarification() -> dict[str, Any]:
    return {
        "needs_clarification": False,
        "needed": False,
        "reason": "safe_to_continue",
        "clarification_type": "none",
        "blocking_level": "none",
        "questions": [],
        "assumptions_if_continue": [],
        "safe_partial_answer_allowed": True,
    }


def summary(text: str) -> str:
    clean = str(redact(text)).strip().replace("\n", " ")
    return clean if len(clean) <= 160 else f"{clean[:160]}..."


def complexity(text: str) -> float:
    score = min(len(text.strip()) / 260, 0.45)
    score += 0.08 * sum(
        1
        for marker in ["方案", "架构", "对比", "权衡", "排查", "继续", "长期", "多步骤"]
        if marker in text
    )
    return round(min(score, 1.0), 2)


def confidence(primary: str, rule_hits: list[str], risks: list[str], text: str) -> float:
    if not text.strip():
        return 0.2
    score = 0.55
    if primary in {"unknown"}:
        score -= 0.2
    if "low_information_input" in rule_hits:
        score -= 0.15
    score += min(len(rule_hits) * 0.03, 0.18)
    score -= min(len(risks) * 0.02, 0.1)
    return round(max(0.2, min(score, 0.95)), 2)


def safe_plan_only(text: str) -> bool:
    return _shared_safe_plan_only(text)


def multimodal_attachment_context(text: str) -> bool:
    return any(marker in text for marker in ["截图", "图片", "附件", "语音", "音频"])


def log_data_extraction(text: str) -> bool:
    return any(marker in text for marker in ["日志", "表格", "数据里", "提取", "筛出"])


def unknown_input(text: str) -> bool:
    stripped = text.strip().strip("。.!！?？~～ ")
    return stripped in {"", "？", "?", "继续", "这个", "那个"}


def memory_query(text: str) -> bool:
    return _assess_intent_boundaries(text).memory_query


def memory_write(text: str) -> bool:
    return any(marker in text for marker in ["记住", "帮我记住", "以后按这个"])


def memory_correction(text: str) -> bool:
    return any(marker in text for marker in ["纠正记忆", "更正", "改一下偏好"])


def system_settings(text: str) -> bool:
    return any(marker in text for marker in ["设置", "配置", "开关", "默认值"])


def approval_response(text: str) -> bool:
    return any(marker in text for marker in ["确认", "同意", "拒绝", "取消这次", "本会话内同类"])


def cancel_or_retry(text: str) -> bool:
    return any(marker in text for marker in ["取消任务", "重试", "重新来", "撤销"])


def skill_request(text: str) -> bool:
    lowered = text.lower()
    if "skill" in lowered:
        return True
    explicit_skill_markers = (
        "调用技能",
        "用技能",
        "通过技能",
        "使用技能",
        "启用技能",
        "安装技能",
        "配置技能",
        "技能列表",
        "有哪些技能",
        "这个技能",
        "系统技能",
    )
    return any(marker in text for marker in explicit_skill_markers)


def office_document_request(text: str) -> bool:
    return is_office_document_request(text)


def mcp_request(text: str) -> bool:
    lowered = text.lower()
    return "mcp" in lowered or "服务器工具" in text


def needs_live_skill_mcp_snapshot(text: str) -> bool:
    return skill_request(text) or mcp_request(text)


def persona_boundary_question(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "你能做什么",
            "你不能做什么",
            "边界",
            "权限",
            "系统提示",
            "隐藏账号",
            "绕过审批",
            "直接登录",
            "private key",
            "私钥",
            "助记词",
        ]
    )


def explicit_task_creation(text: str) -> bool:
    return any(marker in text for marker in ["创建任务", "建个任务", "排个任务"])


def real_task_request(text: str) -> bool:
    return _assess_intent_boundaries(text).real_task_request


def tool_request(text: str) -> bool:
    return _assess_intent_boundaries(text).tool_request


def advice_strategy_direct(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "建议",
            "方案",
            "取舍",
            "优化思路",
            "风险",
            "核验",
            "止损",
            "沟通",
            "比较安全",
            "安全回复",
            "安全做法",
            "怎么处理",
            "怎么回复",
            "怎么回",
            "怎么拦",
            "该怎么回",
            "应该先",
            "先怎么",
            "你先怎么",
            "哪些必须",
            "哪些需要",
            "必须先确认",
            "先确认哪些",
            "审批",
            "授权",
            "高风险",
        ]
    )


def concept_explanation_request(text: str) -> bool:
    return any(marker in text for marker in ["解释", "区别", "为什么", "作用", "模板"]) and any(
        marker in text
        for marker in [
            "网页快照",
            "截图",
            "浏览器任务",
            "下载",
            "确认",
            "结果",
        ]
    )


def filesystem_scope_action(text: str) -> bool:
    return any(marker in text for marker in ["文件", "目录", "路径", "桌面", "下载目录"])


def ambiguous_scope(text: str) -> bool:
    if any(marker in text for marker in ["那个文件", "这个目录", "那一堆", "这些东西"]):
        return True
    if any(marker in text for marker in ["那个", "这个", "某个"]) and any(
        marker in text for marker in ["文件", "目录", "材料", "资料"]
    ):
        return True
    if any(marker in text for marker in ["整理文件夹", "整理目录", "整理文件"]):
        return not any(
            marker in text
            for marker in ["桌面", "下载", "文档", "/", "\\", "：", ":", ".txt", ".md", ".doc"]
        )
    return False


def domain(text: str) -> str:
    if any(marker in text for marker in ["代码", "接口", "服务", "测试"]):
        return "engineering"
    if any(marker in text for marker in ["文档", "汇报", "表格", "PPT", "Word"]):
        return "office"
    return "general"


def capability_available(snapshot: dict[str, Any], key: str) -> bool:
    if key == "skill_engine" and isinstance(snapshot.get("skill"), dict):
        skill = snapshot["skill"]
        return bool(skill.get("available")) and int(skill.get("enabled_count") or 0) > 0
    if key == "mcp" and isinstance(snapshot.get("mcp_runtime"), dict):
        mcp = snapshot["mcp_runtime"]
        return (
            bool(mcp.get("available"))
            and int(mcp.get("ready_server_count") or 0) > 0
            and int(mcp.get("active_tool_count") or 0) > 0
        )
    value = snapshot.get(key)
    if isinstance(value, dict):
        return bool(value.get("available"))
    return bool(value)


def execution_risks(risks: list[str]) -> list[str]:
    risky_actions = {
        "destructive_action",
        "external_side_effect",
        "high_risk_financial_or_signature",
        "host_software_change",
    }
    return [item for item in risks if item in risky_actions]


def continuation_reference(text: str) -> bool:
    return any(marker in text for marker in ["继续刚才", "接着", "上一条", "刚才", "那个方案"])


def skill_unavailable_reason(snapshot: dict[str, Any]) -> str:
    return (
        "skill_unavailable"
        if not capability_available(snapshot, "skill_engine")
        else "skill_available"
    )


def mcp_unavailable_reason(snapshot: dict[str, Any]) -> str:
    return "mcp_unavailable" if not capability_available(snapshot, "mcp") else "mcp_available"


def dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def approval_response(text: str) -> bool:
    if any(marker in text for marker in ["确认", "同意", "拒绝", "取消这次", "本会话内同类"]):
        return True
    return any(marker in text for marker in ["确认", "同意", "拒绝", "取消这次", "本会话内同类"])


def persona_boundary_question(text: str) -> bool:
    if structured_summary_chat_request(text):
        return False
    if _secret_safety_advice_context(text):
        return False
    strong_markers = [
        "你能做什么",
        "你不能做什么",
        "系统提示",
        "隐藏账号",
        "绕过审批",
        "直接登录",
        "忽略规则",
        "附件里让我",
        "private key",
        "私钥",
        "助记词",
    ]
    if any(marker in text for marker in strong_markers):
        return True
    if any(marker in text for marker in ["边界", "权限"]):
        return any(
            scope in text
            for scope in [
                "你能",
                "你不能",
                "你的",
                "系统",
                "能力",
                "工具",
                "登录",
                "审批",
                "安全",
                "绕过",
                "忽略",
            ]
        )
    return False


def _secret_safety_advice_context(text: str) -> bool:
    if not any(marker in text.lower() for marker in ("private key", "mnemonic")) and not any(
        marker in text for marker in ("私钥", "助记词", "密钥")
    ):
        return False
    safety_markers = (
        "客服让我",
        "有人让我",
        "发过去",
        "发给客服",
        "恢复资产",
        "明确阻止",
        "安全替代",
        "替代办法",
        "风险",
        "骗局",
        "核验",
        "不要发",
        "继续上一个",
        "还是上一个",
        "三句同步",
        "证据缺口",
        "下一步",
        "边界复核",
        "场景",
    )
    return any(marker in text for marker in safety_markers)


def real_task_request(text: str) -> bool:
    return _assess_intent_boundaries(text).real_task_request


def tool_request(text: str) -> bool:
    return _assess_intent_boundaries(text).tool_request


def concept_explanation_request(text: str) -> bool:
    if any(marker in text for marker in ["解释", "区别", "为什么", "作用", "模板"]) and any(
        marker in text
        for marker in [
            "网页快照",
            "截图",
            "浏览器任务",
            "下载",
            "确认",
            "结果",
        ]
    ):
        return True
    return any(marker in text for marker in ["解释", "区别", "为什么", "作用", "模板"]) and any(
        marker in text
        for marker in [
            "网页快照",
            "截图",
            "浏览器任务",
            "下载",
            "确认",
            "结果",
        ]
    )


def _chatty_delivery_request(text: str) -> bool:
    return _shared_chatty_delivery_request(text)


def tool_request(text: str) -> bool:
    return _assess_intent_boundaries(text).tool_request
