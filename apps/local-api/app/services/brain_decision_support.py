from __future__ import annotations

from typing import Any

from core_types import DialogueState, IntentDecision, PrivacyLevel, SemanticIntentCandidate, TaskMode
from trace_service import redact

from app.services.chat_intent_router import (
    is_explicit_download_request,
    is_file_mutation_request,
    is_host_filesystem_list_request,
    is_host_software_install_request,
    is_office_document_request,
    is_webpage_read_request,
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
    return any(marker in text for marker in ["只做分析", "只给方案", "不要执行", "先别执行"])


def multimodal_attachment_context(text: str) -> bool:
    return any(marker in text for marker in ["截图", "图片", "附件", "语音", "音频"])


def log_data_extraction(text: str) -> bool:
    return any(marker in text for marker in ["日志", "表格", "数据里", "提取", "筛出"])


def unknown_input(text: str) -> bool:
    stripped = text.strip().strip("。.!！?？~～ ")
    return stripped in {"", "？", "?", "继续", "这个", "那个"}


def memory_query(text: str) -> bool:
    return any(marker in text for marker in ["记得", "还记得", "偏好", "上次说过", "我刚才说"])


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
    return "skill" in text.lower() or "技能" in text


def office_document_request(text: str) -> bool:
    return is_office_document_request(text)


def mcp_request(text: str) -> bool:
    lowered = text.lower()
    return "mcp" in lowered or "服务器工具" in text


def needs_live_skill_mcp_snapshot(text: str) -> bool:
    return skill_request(text) or mcp_request(text)


def persona_boundary_question(text: str) -> bool:
    return any(marker in text for marker in ["你能做什么", "你不能做什么", "边界", "权限"])


def explicit_task_creation(text: str) -> bool:
    return any(marker in text for marker in ["创建任务", "建个任务", "排个任务"])


def real_task_request(text: str) -> bool:
    return explicit_task_creation(text) or any(
        marker in text for marker in ["帮我做", "去执行", "帮我处理", "跑一下", "装一下"]
    )


def tool_request(text: str) -> bool:
    return any(marker in text for marker in ["调用工具", "打开网页", "下载", "截图", "安装"])


def advice_strategy_direct(text: str) -> bool:
    return any(marker in text for marker in ["建议", "方案", "取舍", "优化思路"])


def filesystem_scope_action(text: str) -> bool:
    return any(marker in text for marker in ["文件", "目录", "路径", "桌面", "下载目录"])


def ambiguous_scope(text: str) -> bool:
    return any(marker in text for marker in ["那个文件", "这个目录", "那一堆", "这些东西"])


def domain(text: str) -> str:
    if any(marker in text for marker in ["代码", "接口", "服务", "测试"]):
        return "engineering"
    if any(marker in text for marker in ["文档", "汇报", "表格", "PPT", "Word"]):
        return "office"
    return "general"


def capability_available(snapshot: dict[str, Any], key: str) -> bool:
    value = snapshot.get(key)
    if isinstance(value, dict):
        return bool(value.get("available"))
    return bool(value)


def execution_risks(risks: list[str]) -> list[str]:
    return [item for item in risks if item in {"destructive_action", "external_side_effect", "high_risk_financial_or_signature", "host_software_change"}]


def continuation_reference(text: str) -> bool:
    return any(marker in text for marker in ["继续刚才", "接着", "上一条", "刚才", "那个方案"])


def skill_unavailable_reason(snapshot: dict[str, Any]) -> str:
    return "skill_unavailable" if not capability_available(snapshot, "skill_engine") else "skill_available"


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
