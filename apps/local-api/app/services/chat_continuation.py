from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core_types import ContextPacket
from response_composer.chat_voice import render_continuation_revision_prompt
from trace_service import redact

CHAT_CONTINUATION_GATE_VERSION = "chat_continuation.openclaw_hermes.v4"

_DIAGNOSTIC_KEYS = (
    "content",
    "structure",
    "voice",
    "safety",
    "evidence",
    "multimodal",
    "latency",
    "composer_guard",
)
_STATUS_ORDER = {"skip": 0, "ok": 1, "warn": 2, "fail": 3}
_FIXED_BOUNDARY_INTENTS = {
    "boundary_question",
    "capability_boundary",
    "clarification",
    "privacy_recovery_boundary",
    "professional_safety_advice",
    "safety_boundary",
}
_BLOCKING_TAGS = {
    "missing_reply",
    "internal_jargon",
    "secret_leak",
    "false_done",
    "strict_format_polluted",
}
_FACE_EMOJI_RE = re.compile(r"[\U0001f600-\U0001f64f]")
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|cookie|password|passwd|pwd|private[_-]?key)"
    r"\b\s*[:=]\s*[^ \n\r\t，。；;]+|"
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_READING_MARKERS = (
    "📘",
    "📌",
    "§",
    "▸",
    "🧠",
    "✨",
    "⚡",
    "🎯",
    "🧩",
    "📝",
    "🔍",
    "📎",
    "💡",
    "🛠️",
    "✍️",
)
_INTERNAL_TERMS = (
    "trace_id",
    "task_id",
    "approval_id",
    "tool_call_id",
    "turn_id",
    "message_id",
    "understanding_status",
    "provider",
    "metadata",
    "channel_attachment_id",
    "media_id",
    "artifact_id",
    "model_safe_text",
    "prompt_snapshot_id",
    "<minimax:tool_call",
    "<tool_call",
    "<invoke",
)
_MULTIMODAL_MARKERS = (
    "图片内容线索",
    "语音内容线索",
    "语音转成文字",
    "文件内容摘录",
    "用户还附带了一张图片",
    "用户还附带了一段语音",
    "用户还附带了一个文件",
    "图片",
    "语音",
    "文件",
)
_GENERIC_MULTIMODAL_ACKS = (
    "收到图片",
    "图片我收到了",
    "收到语音",
    "语音我收到了",
    "收到文件",
    "文件我收到了",
    "我收到了这张图",
    "我收到了这段语音",
    "我收到了这个文件",
)
_CONTENTFUL_MULTIMODAL_REPLY_HINTS = (
    "图里",
    "这张图里",
    "我看到",
    "我读到",
    "我听到",
    "你说",
    "你这段语音",
    "文件里",
    "重点",
    "大概是",
    "意思是",
    "提到",
    "看不清",
    "听不全",
    "读不全",
)
_MECHANICAL_REPLY_STARTERS = (
    "好的",
    "明白",
    "收到",
    "已收到",
    "我来",
    "当然可以",
    "当前",
    "摘要",
    "处理结果",
    "下面是",
    "以下是",
)
_CONVERSATIONAL_CUES = (
    "咱们",
    "我跟你说",
    "你这个",
    "别急",
    "先别急",
    "有点",
    "说真的",
    "实话说",
    "我琢磨",
    "顺手",
    "我听到",
    "我看到",
    "我读到",
    "你说",
    "哈哈",
    "行吧",
    "确实",
    "挺",
)
_SYSTEMIC_TONE_CUES = (
    "当前状态",
    "处理结果",
    "摘要如下",
    "总结如下",
    "以下是",
    "下面是",
    "接下来我将",
    "我将为你",
    "作为一个ai",
    "作为ai",
    "系统说明",
    "状态报告",
    "接口说明",
)
_HARD_BOUNDARY_TONE_CUES = (
    "必须经过",
    "必须走",
    "安全边界",
    "系统安全边界",
    "当前后端没有",
    "不会把它伪装",
    "我没有创建任务，也没有调用工具",
    "这部分我不能完整输出",
    "得你点头后",
    "不会把没做的事说成做完",
    "先停一下",
    "确认前",
)
_ROBOTIC_TEMPLATE_CUES = (
    "好的，",
    "明白，",
    "收到，",
    "我来帮你",
    "我先帮你",
    "下面我来",
    "先说结论",
    "先说结果",
    "总的来说",
    "继续处理",
)
_WARM_HUMOR_CUES = (
    "哈哈",
    "行吧",
    "有点",
    "别急",
    "说白了",
    "我跟你说",
    "咱们",
    "机灵",
    "顺手",
    "轻松",
)
_STRICT_FORMAT_MARKERS = (
    "只输出json",
    "只输出 json",
    "json-only",
    "不要markdown",
    "不要 markdown",
    "代码块",
    "markdown表格",
    "markdown 表格",
)
_QUALITY_RISK_PATTERNS = (
    "让我继续",
    "我会继续",
    "后续我会",
    "作为一个ai",
    "作为ai",
    "作为一个大语言模型",
)
_FALSE_DONE_CLAIMS = (
    "已经执行",
    "已执行",
    "执行完成",
    "已经完成操作",
    "已经删除",
    "已删除",
    "安装完成",
    "已经安装",
    "已安装",
    "已经下载",
    "已下载",
    "已经提交",
    "已提交",
)
_SHORT_ANSWER_SALIENT_MARKERS = (
    "刚才",
    "偏好",
    "顺序",
    "记住",
    "继续",
    "边界",
    "登录",
    "执行",
    "风险",
    "原因",
    "纯文本",
    "只读",
    "Skill",
    "skill",
    "汇报",
    "邮件",
    "客服",
    "表格",
    "适用场景",
    "展开",
    "要点",
    "耗时",
    "归因",
    "温和",
    "对比",
    "排序",
    "排个序",
    "重要性",
    "排一下",
    "优先",
    "优先级",
    "简洁",
    "三步",
    "行动项",
    "SOP",
    "隐私",
    "证据",
    "完成",
    "审批",
    "高风险",
    "复盘",
    "周会",
    "反馈",
    "培训",
    "面试",
    "发布",
    "工单",
    "故障",
    "学习",
    "标题",
    "体验",
    "总结",
)


@dataclass(frozen=True)
class ContinuationDecision:
    enabled: bool
    reason_codes: list[str] = field(default_factory=list)
    latency_budget_ms: int = 20_000
    max_iterations: int = 1
    trigger_profile: str = "wechat_quality_gate"
    version: str = CHAT_CONTINUATION_GATE_VERSION


@dataclass(frozen=True)
class ContinuationEvaluation:
    verdict: str
    tags: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    diagnostics: dict[str, str] = field(default_factory=dict)

    @property
    def should_revise(self) -> bool:
        return self.verdict in {"revise", "block"} and bool(self.tags)


class ChatContinuationCoordinator:
    """v4 continuation gate for WeChat-visible model replies."""

    def decide(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        context: ContextPacket,
        intent: str,
        mode: str,
    ) -> ContinuationDecision:
        del context
        if _ui_mode_for_turn(turn) != "wechat_chat":
            return ContinuationDecision(enabled=False, reason_codes=["not_wechat_chat"])
        if _strict_format_request(user_text):
            return ContinuationDecision(enabled=False, reason_codes=["strict_format_request"])
        if intent in _FIXED_BOUNDARY_INTENTS:
            return ContinuationDecision(enabled=False, reason_codes=["fixed_boundary_reply"])
        if mode not in {"direct", "direct_with_memory"}:
            return ContinuationDecision(enabled=False, reason_codes=["non_direct_mode"])

        experience = dict(turn.get("experience") or {})
        complexity = _float(experience.get("complexity_score"))
        reasons = list(experience.get("context_selection_reason") or [])
        route_profile = str(experience.get("route_profile") or "")
        enabled_reasons: list[str] = []
        if complexity >= 0.48:
            enabled_reasons.append("complexity_high")
        if bool(experience.get("needs_strong_reasoning")) or route_profile == "deep_reasoning":
            enabled_reasons.append("deep_reasoning")
        if bool(experience.get("needs_long_output")) or _needs_long_output(user_text):
            enabled_reasons.append("long_output")
        if any("continuation" in str(item) for item in reasons):
            enabled_reasons.append("context_continuation")
        if _multimodal_context(user_text):
            enabled_reasons.append("multimodal_attachment_context")
        if _office_or_online_user_topic(user_text):
            enabled_reasons.append("high_value_user_topic")

        enabled_reasons = _dedupe(enabled_reasons)
        if not enabled_reasons:
            return ContinuationDecision(enabled=False, reason_codes=["plain_fast_path"])
        if len(user_text.strip()) <= 24 and not any(
            item
            in {
                "context_continuation",
                "high_value_user_topic",
                "multimodal_attachment_context",
            }
            for item in enabled_reasons
        ):
            return ContinuationDecision(enabled=False, reason_codes=["short_chat_fast_path"])
        return ContinuationDecision(enabled=True, reason_codes=enabled_reasons)

    def evaluate(
        self,
        *,
        text: str,
        user_text: str,
        decision: ContinuationDecision,
        elapsed_ms: int | None = None,
        response_quality_guard: dict[str, Any] | None = None,
    ) -> ContinuationEvaluation:
        tags: list[str] = []
        suggestions: list[str] = []
        diagnostics = _empty_diagnostics()
        visible = str(text or "").strip()
        lowered = visible.lower()
        user_lowered = str(user_text or "").lower()
        strict_requested = _strict_format_request(user_text)
        short_answer_sufficient = _short_answer_sufficient(user_text=user_text, visible=visible)

        if not visible:
            _mark(diagnostics, "content", "fail")
            tags.append("missing_reply")
            suggestions.append("重新生成一个直接回答用户当前消息的回复。")
        elif (
            _complex_user_request(user_text)
            and len(visible) < 90
            and not short_answer_sufficient
        ):
            _mark(diagnostics, "content", "warn")
            tags.append("too_short")
            suggestions.append("补足判断、取舍或下一步，避免只给一句泛泛回答。")

        if len(visible) > 420 and "\n" not in visible:
            _mark(diagnostics, "structure", "warn")
            tags.append("weak_structure")
            suggestions.append("把长回复拆成结论、要点和下一步，方便微信阅读。")
        if strict_requested and not _strict_format_text(visible):
            _mark(diagnostics, "structure", "fail")
            tags.append("strict_format_polluted")
            suggestions.append("严格 JSON、表格或代码块请求必须保持纯净格式。")
        if _strict_format_text(visible) and any(marker in visible for marker in _READING_MARKERS):
            _mark(diagnostics, "structure", "fail")
            tags.append("strict_format_polluted")
            suggestions.append("严格格式内不要加入阅读型符号或额外说明。")

        if any(term in lowered for term in _INTERNAL_TERMS):
            _mark(diagnostics, "safety", "fail")
            tags.append("internal_jargon")
            suggestions.append("去掉内部字段、记录编号和工具细节，换成用户能理解的状态说明。")
        if _SECRET_RE.search(visible):
            _mark(diagnostics, "safety", "fail")
            tags.append("secret_leak")
            suggestions.append("删除疑似 secret、token、私钥或本地敏感路径，只保留脱敏说明。")
        if _FACE_EMOJI_RE.search(visible):
            _mark(diagnostics, "voice", "warn")
            tags.append("face_emoji")
            suggestions.append("删除圆脸 emoji，微信长回复只允许少量阅读型符号。")

        normalized_lowered = re.sub(r"^[^\w\u4e00-\u9fff]+", "", lowered)
        if not short_answer_sufficient:
            if any(normalized_lowered.startswith(cue.strip()) for cue in _ROBOTIC_TEMPLATE_CUES):
                _mark(diagnostics, "voice", "warn")
                tags.append("robotic_template")
                suggestions.append("减少模板开头和固定套话，直接回应用户意图再展开。")
            if any(cue in lowered for cue in _SYSTEMIC_TONE_CUES):
                _mark(diagnostics, "voice", "warn")
                tags.append("systemic_tone")
                suggestions.append("把系统说明味压下去，换成微信里更自然的表达。")
            if visible.startswith(_MECHANICAL_REPLY_STARTERS) or any(
                pattern in lowered for pattern in _QUALITY_RISK_PATTERNS
            ):
                _mark(diagnostics, "voice", "warn")
                tags.append("too_hardcoded")
                suggestions.append("减少模板开头和空话，直接进入结论、依据和下一步。")
            if any(cue in visible for cue in _HARD_BOUNDARY_TONE_CUES):
                _mark(diagnostics, "voice", "warn")
                tags.append("hard_boundary_tone")
                suggestions.append("边界内容可以保留，但口吻再自然一点，别像系统回执。")
            if len(visible) > 220 and not any(cue in visible for cue in _WARM_HUMOR_CUES):
                _mark(diagnostics, "voice", "warn")
                tags.append("too_stiff")
                suggestions.append("安全场景外可以更像聊天，少一点说明书口吻。")
            if len(visible) > 140 and not any(cue in visible for cue in _CONVERSATIONAL_CUES):
                _mark(diagnostics, "voice", "warn")
                tags.append("weak_persona")
                suggestions.append("先回应对方这句话，再补一点自然承接。")

        if _action_request(user_lowered, user_text) and any(
            claim in visible for claim in _FALSE_DONE_CLAIMS
        ):
            _mark(diagnostics, "evidence", "fail")
            tags.append("false_done")
            suggestions.append("没有真实执行和确认记录时，不要声称已经执行或完成。")

        if _multimodal_context(user_text):
            if _generic_multimodal_acknowledgement(visible):
                _mark(diagnostics, "multimodal", "warn")
                tags.append("multimodal_generic_reply")
                suggestions.append("围绕图片、语音或文件里已经识别到的具体内容回复，不要只说收到了。")
            else:
                _mark(diagnostics, "multimodal", "ok")
        else:
            diagnostics["multimodal"] = "skip"

        if elapsed_ms is not None:
            if elapsed_ms > decision.latency_budget_ms:
                _mark(diagnostics, "latency", "warn")
                tags.append("latency_slow")
                suggestions.append("续跑修订超过预算，优先减少二次模型调用或缩短重写上下文。")
            else:
                _mark(diagnostics, "latency", "ok")
        else:
            diagnostics["latency"] = "skip"

        _apply_guard_diagnostics(
            diagnostics,
            tags,
            suggestions,
            response_quality_guard=response_quality_guard,
        )

        tags = _dedupe(tags)
        suggestions = _dedupe(suggestions)
        if set(tags) & _BLOCKING_TAGS:
            verdict = "block"
        elif any(status == "fail" for status in diagnostics.values()):
            verdict = "block"
        elif tags:
            verdict = "revise"
        else:
            verdict = "good"
            if decision.enabled:
                suggestions.append("保持当前回复，只做常规微信风格清理。")
        return ContinuationEvaluation(
            verdict=verdict,
            tags=tags,
            suggestions=suggestions,
            diagnostics=diagnostics,
        )

    def revision_messages(
        self,
        *,
        messages: list[dict[str, str]],
        user_text: str,
        draft_text: str,
        evaluation: ContinuationEvaluation,
    ) -> list[dict[str, str]]:
        instruction = render_continuation_revision_prompt(
            user_text=user_text,
            draft_text=draft_text,
            quality_tags=evaluation.tags,
            suggestions=evaluation.suggestions,
            diagnostics=evaluation.diagnostics,
        )
        return [
            *messages,
            {"role": "assistant", "content": str(redact(draft_text))},
            {"role": "user", "content": instruction},
        ]

    def payload(
        self,
        *,
        decision: ContinuationDecision,
        evaluation: ContinuationEvaluation,
        iterations: int,
        budget_exhausted: bool = False,
        used_revision: bool = False,
        used_safe_fallback: bool = False,
        initial_latency_ms: int | None = None,
        revision_latency_ms: int | None = None,
        total_latency_ms: int | None = None,
    ) -> dict[str, Any]:
        return {
            "version": decision.version,
            "enabled": decision.enabled,
            "trigger_profile": decision.trigger_profile,
            "iterations": iterations,
            "reason_codes": decision.reason_codes,
            "quality_verdict": evaluation.verdict,
            "quality_tags": evaluation.tags,
            "diagnostics": dict(evaluation.diagnostics),
            "latency_budget_ms": decision.latency_budget_ms,
            "initial_latency_ms": initial_latency_ms,
            "revision_latency_ms": revision_latency_ms,
            "total_latency_ms": total_latency_ms,
            "budget_exhausted": budget_exhausted,
            "used_revision": used_revision,
            "used_safe_fallback": used_safe_fallback,
        }

    def accepts_revision(self, evaluation: ContinuationEvaluation) -> bool:
        return not bool(set(evaluation.tags) & _BLOCKING_TAGS)

    def safe_fallback_text(
        self,
        *,
        user_text: str,
        evaluation: ContinuationEvaluation,
    ) -> str:
        tags = set(evaluation.tags)
        if "false_done" in tags:
            return (
                "这一步我还不能说已经完成。确认或真实执行结果出来前，我只能先说明边界和下一步；"
                "你要继续的话，给我明确口径或补充目标。"
            )
        if "secret_leak" in tags or "internal_jargon" in tags:
            return (
                "刚才那版不够稳，我先收住：敏感值、内部记录和工具细节我不能直接发出来。"
                "你可以换成脱敏内容，我再继续帮你处理。"
            )
        if "multimodal_generic_reply" in tags or _multimodal_context(user_text):
            return (
                "我现在还没拿到足够可靠的识别内容，先不瞎猜。"
                "你可以补充图片、语音或文件里的重点，我再接着看。"
            )
        return "这版回复质量不够稳，我先收住不直接发。你把目标再说具体一点，我马上接着处理。"


def _empty_diagnostics() -> dict[str, str]:
    return {key: "ok" for key in _DIAGNOSTIC_KEYS}


def _mark(diagnostics: dict[str, str], key: str, status: str) -> None:
    current = diagnostics.get(key, "ok")
    if _STATUS_ORDER.get(status, 0) > _STATUS_ORDER.get(current, 0):
        diagnostics[key] = status


def _apply_guard_diagnostics(
    diagnostics: dict[str, str],
    tags: list[str],
    suggestions: list[str],
    *,
    response_quality_guard: dict[str, Any] | None,
) -> None:
    if not isinstance(response_quality_guard, dict):
        diagnostics["composer_guard"] = "skip"
        return
    if str(response_quality_guard.get("status") or "") == "passed":
        _mark(diagnostics, "composer_guard", "ok")
        return
    checks = response_quality_guard.get("checks")
    checks = checks if isinstance(checks, dict) else {}
    if checks.get("no_internal_terms") is False:
        tags.append("internal_jargon")
    if checks.get("no_false_done") is False or checks.get("evidence_required_before_done") is False:
        tags.append("false_done")
    if checks.get("strict_format_preserved") is False:
        tags.append("strict_format_polluted")
    if any(tag in tags for tag in _BLOCKING_TAGS):
        _mark(diagnostics, "composer_guard", "fail")
    else:
        _mark(diagnostics, "composer_guard", "warn")
        suggestions.append("最终回复需要再过一遍可见文本质量检查。")


def _ui_mode_for_turn(turn: dict[str, Any]) -> str | None:
    experience = turn.get("experience") or {}
    client_context = experience.get("client_context") if isinstance(experience, dict) else {}
    if not isinstance(client_context, dict):
        return None
    value = client_context.get("ui_mode")
    return str(value) if value else None


def _strict_format_request(text: str) -> bool:
    compact = re.sub(r"\s+", "", str(text or "").lower())
    return any(marker in compact for marker in _STRICT_FORMAT_MARKERS) or bool(
        re.search(r"```|^\s*[{[]", str(text or ""))
    )


def _multimodal_context(text: str) -> bool:
    return any(marker in str(text or "") for marker in _MULTIMODAL_MARKERS)


def _generic_multimodal_acknowledgement(text: str) -> bool:
    visible = str(text or "").strip()
    if not visible or len(visible) > 180:
        return False
    if not any(marker in visible for marker in _GENERIC_MULTIMODAL_ACKS):
        return False
    return not any(marker in visible for marker in _CONTENTFUL_MULTIMODAL_REPLY_HINTS)


def _strict_format_text(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if "```" in stripped:
        return True
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        return True
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    return len(lines) >= 2 and any("|---" in line or "---|" in line for line in lines[:4])


def _short_answer_sufficient(*, user_text: str, visible: str) -> bool:
    text = str(visible or "").strip()
    if not text:
        return False
    if len(text) < 12 or len(text) > 160:
        return False
    lowered = text.lower()
    normalized_lowered = re.sub(r"^[^\w\u4e00-\u9fff]+", "", lowered)
    if any(normalized_lowered.startswith(cue.strip()) for cue in _ROBOTIC_TEMPLATE_CUES):
        return False
    if text.startswith(_MECHANICAL_REPLY_STARTERS) or any(
        pattern in lowered for pattern in _QUALITY_RISK_PATTERNS
    ):
        return False
    if any(cue in text for cue in _HARD_BOUNDARY_TONE_CUES):
        return False
    if any(term in lowered for term in _INTERNAL_TERMS) or _SECRET_RE.search(text):
        return False
    if _strict_format_request(user_text) and not _strict_format_text(text):
        return False
    if _multimodal_context(user_text) and _generic_multimodal_acknowledgement(text):
        return False
    request = str(user_text or "")
    request_lowered = request.lower()
    if not any(
        marker in request or marker.lower() in request_lowered
        for marker in _SHORT_ANSWER_SALIENT_MARKERS
    ):
        return False
    return len(re.findall(r"[\u4e00-\u9fff]", text)) >= 6


def _needs_long_output(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "详细",
            "完整",
            "展开",
            "方案",
            "计划",
            "复盘",
            "总结",
            "对比",
            "优化",
            "分析",
            "拆解",
            "50",
        ]
    )


def _office_or_online_user_topic(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in text or marker in lowered
        for marker in [
            "办公",
            "周报",
            "汇报",
            "会议",
            "邮件",
            "文档",
            "表格",
            "ppt",
            "用户",
            "网友",
            "网上",
            "小红书",
            "抖音",
            "知乎",
            "微信",
            "客服",
        ]
    )


def _complex_user_request(text: str) -> bool:
    return len(text.strip()) >= 80 or _needs_long_output(text) or _office_or_online_user_topic(text)


def _action_request(lowered: str, text: str) -> bool:
    return any(
        marker in text or marker in lowered
        for marker in [
            "删除",
            "安装",
            "下载",
            "执行",
            "运行",
            "打开网页",
            "登录",
            "发到",
            "发布",
            "send",
            "delete",
            "install",
            "download",
        ]
    )


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
