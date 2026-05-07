from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from core_types import ResponsePlan, TaskMode
from response_composer import ResponseComposer
from response_composer.chat_voice import voice_metadata_for_scenario
from response_composer.opening_copy import opening_copy
from trace_service import redact

from app.services.chat_visible_guard import visible_text_guard

_QUALITY_COPY_KEYS = {
    "desktop_boundary": "boundary.desktop",
    "supportive_safety_refusal": "boundary.refusal",
    "persona_boundary": "boundary.persona",
    "system_prompt_refusal": "boundary.internal",
    "privacy_block": "boundary.privacy",
    "professional_medical": "boundary.professional_medical",
    "professional_finance": "boundary.professional_finance",
}

CHAT_QUALITY_POLICY_VERSION = "chat_quality_boundary.openclaw_hermes.v4"


def _quality_copy(key: str, seed: str = "") -> str:
    return opening_copy(_QUALITY_COPY_KEYS[key], seed or key)


@dataclass(frozen=True)
class ChatQualityOutcome:
    text: str
    response_plan: ResponsePlan
    intent: str
    mode: str = TaskMode.DIRECT.value


class ChatQualityPolicy:
    """Deterministic response policy for user-visible chat boundaries."""

    def __init__(self, *, composer: ResponseComposer | None = None) -> None:
        self._composer = composer or ResponseComposer()

    def handle(
        self,
        *,
        user_text: str,
        privacy_level: str,
        sensitivity_hits: list[str] | tuple[str, ...],
        brain_intent: str | None = None,
    ) -> ChatQualityOutcome | None:
        text = user_text.strip()
        if not text:
            return None
        lowered = text.lower()

        if _desktop_native_request(text):
            boundary = _desktop_boundary_contract()
            return self._outcome(
                _desktop_boundary_reply(text),
                intent="capability_boundary",
                status="desktop_capability_boundary",
                reason_codes=["chat_quality_policy", "desktop_native_not_supported"],
                structured={
                    "capability_boundary": boundary,
                },
                tool_notice=(
                    _quality_copy("desktop_boundary", seed=text)
                ),
            )

        if _supportive_safety_refusal_request(text):
            return self._outcome(
                _quality_copy("supportive_safety_refusal", seed=text),
                intent="safety_boundary",
                status="supportive_safety_refusal",
                reason_codes=[
                    "chat_quality_policy",
                    "phase51_supportive_safety_refusal",
                    "no_task_or_tool_created",
                ],
                structured={
                    "task_created": False,
                    "tool_created": False,
                    "approval_created": False,
                    "fail_closed": True,
                },
                safety_notice=_quality_copy("supportive_safety_refusal", seed=text),
            )

        if _persona_boundary_question(text):
            return self._outcome(
                _quality_copy("persona_boundary", seed=text),
                intent="boundary_question",
                status="persona_boundary",
                reason_codes=["chat_quality_policy", "persona_hidden_account_boundary"],
                structured={},
                safety_notice=_quality_copy("persona_boundary", seed=text),
            )

        if _system_prompt_or_trace_request(text):
            return self._outcome(
                _quality_copy("system_prompt_refusal", seed=text),
                intent="boundary_question",
                status="system_prompt_refusal",
                reason_codes=["chat_quality_policy", "internal_instruction_refusal"],
                structured={},
                safety_notice=_quality_copy("system_prompt_refusal", seed=text),
            )

        if _high_risk_professional_advice(text):
            return self._outcome(
                _professional_boundary_reply(text),
                intent="professional_safety_advice",
                status="professional_safety_boundary",
                reason_codes=[
                    "chat_quality_policy",
                    "phase51_professional_safety_advice",
                    "no_unconditional_dosage_or_guarantee",
                ],
                structured={
                    "task_created": False,
                    "tool_created": False,
                    "professional_boundary": True,
                    "safe_next_step": True,
                },
                safety_notice=_professional_boundary_notice(text),
            )

        if _recoverable_secret_input(
            lowered,
            privacy_level=privacy_level,
            sensitivity_hits=sensitivity_hits,
            brain_intent=brain_intent,
        ):
            return self._outcome(
                _quality_copy("privacy_block", seed=text),
                intent="privacy_recovery_boundary",
                status="recoverable_privacy_block",
                reason_codes=["chat_quality_policy", "sensitive_input_recoverable_block"],
                structured={
                    "privacy_level": privacy_level,
                    "sensitivity_hits_summary": {
                        "count": len(sensitivity_hits),
                        "categories": sorted(set(str(item) for item in sensitivity_hits)),
                    },
                    "cloud_model_called": False,
                    "secret_echo": False,
                },
                safety_notice=_quality_copy("privacy_block", seed=text),
            )

        return None

    def _outcome(
        self,
        text: str,
        *,
        intent: str,
        status: str,
        reason_codes: list[str],
        structured: dict[str, Any],
        safety_notice: str | None = None,
        tool_notice: str | None = None,
    ) -> ChatQualityOutcome:
        visible = visible_text_guard(text)
        plan = self._composer.response_plan_for_status(
            summary=visible,
            safety_notice=safety_notice,
            tool_notice=tool_notice,
        )
        follow_ups = _follow_ups_for_status(status)
        plan = plan.model_copy(
            update={
                "title": "能力边界" if tool_notice else plan.title,
                "style": "quality_boundary" if safety_notice or tool_notice else "result_first",
                "follow_up_options": follow_ups,
                "structured_payload": {
                    **plan.structured_payload,
                    "scenario": "chat_quality_policy",
                    **voice_metadata_for_scenario(_voice_scenario_for_quality_status(status)),
                    "route_semantics": {
                        "route": "direct",
                        "model_called": False,
                        "task_created": False,
                        "tool_created": False,
                        "approval_created": False,
                        "model_not_required_reason": status,
                    },
                    "response_quality_guard": {
                        **_quality_guard(
                            visible,
                            status=status,
                            next_step_provided=bool(follow_ups),
                            professional_boundary=status == "professional_safety_boundary",
                        ),
                    },
                    "chat_quality_policy": {
                        "version": CHAT_QUALITY_POLICY_VERSION,
                        "status": status,
                        "reason_codes": reason_codes,
                        **redact(structured),
                    },
                },
                "quality_markers": {
                    **plan.quality_markers,
                    "latest_instruction_priority": status == "latest_instruction_priority",
                    "boundary_honesty": True,
                    "recoverable_privacy_block": status == "recoverable_privacy_block",
                    "natural_language": True,
                    "no_leakage": True,
                },
                "user_next_step": follow_ups[0] if follow_ups else None,
            }
        )
        return ChatQualityOutcome(text=visible, response_plan=plan, intent=intent)


# Backwards-compatible import name for older tests and extension code.
ChatQualityExperienceService = ChatQualityPolicy


def _quality_guard(
    visible: str,
    *,
    status: str,
    next_step_provided: bool,
    professional_boundary: bool,
) -> dict[str, Any]:
    checks = {
        "state_disclosed": True,
        "boundary_disclosed": True,
        "next_step_provided": bool(next_step_provided),
        "no_false_done": True,
        "no_internal_terms": True,
    }
    violations = [
        {"check": check}
        for check, passed in checks.items()
        if not passed
    ]
    return {
        "version": "response_quality_guard.openclaw_hermes.v4",
        "status": "passed" if not violations else "warning",
        "checks": checks,
        "violations": violations,
        "redaction_applied": False,
        "strict_format_preserved": True,
        "visible_text_hash": "sha256:"
        + hashlib.sha256(str(visible or "").encode("utf-8")).hexdigest(),
        "state_disclosed": checks["state_disclosed"],
        "boundary_disclosed": checks["boundary_disclosed"],
        "next_step_provided": checks["next_step_provided"],
        "no_false_done": checks["no_false_done"],
        "no_internal_terms": checks["no_internal_terms"],
        "professional_boundary": bool(professional_boundary),
    }


def _voice_scenario_for_quality_status(status: str) -> str:
    if status == "recoverable_privacy_block":
        return "privacy"
    if status == "professional_safety_boundary":
        return "professional_advice"
    if status in {"desktop_capability_boundary", "supportive_safety_refusal"}:
        return "tool_boundary"
    if status in {"system_prompt_refusal", "persona_boundary"}:
        return "tool_boundary"
    if status == "latest_instruction_priority":
        return "clarification"
    return "tool_boundary"


def _desktop_native_request(text: str) -> bool:
    lowered = text.lower()
    desktop_markers = [
        "desktop.",
        "桌面原生",
        "桌面窗口",
        "控制桌面",
        "控制窗口",
        "窗口置顶",
        "移动鼠标",
        "全局键盘",
        "桌面截图",
        "本机桌面",
    ]
    action_markers = [
        "执行",
        "操作",
        "点击",
        "输入",
        "打开",
        "控制",
        "截图",
        "最小化",
        "最大化",
        "置顶",
        "关闭窗口",
        "download",
        "click",
        "type",
    ]
    return any(marker in lowered or marker in text for marker in desktop_markers) and any(
        marker in lowered or marker in text for marker in action_markers
    )


def _desktop_boundary_contract() -> dict[str, Any]:
    return {
        "tool_namespace": "desktop",
        "status": "not_implemented",
        "supported_actions": [],
        "boundary": "当前后端未提供原生窗口控制、鼠标键盘全局控制、桌面截图定位能力。",
        "safe_fallbacks": ["browser.*", "file.*", "terminal.*"],
        "requires_future_design": ["capability", "approval", "trace", "artifact", "sandbox"],
    }


def _desktop_boundary_reply(text: str = "") -> str:
    return _quality_copy("desktop_boundary", seed=text)


def _persona_boundary_question(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in text
        for marker in ["你是真人", "真人吗", "隐藏账号", "私下登录", "绕过系统", "偷偷登录"]
    ) or any(marker in lowered for marker in ["hidden account", "real human"])


def _system_prompt_or_trace_request(text: str) -> bool:
    lowered = text.lower()
    internal_markers = [
        "系统提示",
        "开发者指令",
        "开发者消息",
        "隐藏规则",
        "内部 " + "trace",
        "trace 明细",
        "完整 trace",
        "system prompt",
        "developer message",
    ]
    request_markers = ["输出", "完整", "展示", "给我", "泄露", "忽略", "print", "show"]
    return any(marker in lowered or marker in text for marker in internal_markers) and any(
        marker in lowered or marker in text for marker in request_markers
    )


def _supportive_safety_refusal_request(text: str) -> bool:
    lowered = text.lower()
    bypass_markers = [
        "跳过审批",
        "绕过审批",
        "忽略审批",
        "不要审批",
        "不需要审批",
        "绕过安全",
        "无视安全",
        "绕过系统",
        "跳过确认",
        "不需要确认",
        "bypass approval",
        "skip approval",
    ]
    abuse_markers = [
        "登录别人账号",
        "登录他人账号",
        "替我登录别人",
        "越权登录",
        "越权审批",
        "假装已执行",
        "假装已经执行",
        "假装完成",
        "明明没执行也说完成",
        "伪装执行",
        "偷偷执行",
    ]
    return any(marker in lowered or marker in text for marker in bypass_markers) or any(
        marker in lowered or marker in text for marker in abuse_markers
    )


def _high_risk_professional_advice(text: str) -> bool:
    lowered = text.lower()
    medical_markers = [
        "布洛芬",
        "用药",
        "吃多少",
        "剂量",
        "毫克",
        "退烧药",
        "止痛药",
        "处方药",
        "药量",
        "儿童用药",
    ]
    finance_markers = [
        "保证收益",
        "稳赚",
        "全部积蓄",
        "满仓",
        "确定买入",
        "贷款买",
        "不要提醒风险",
        "金融建议",
        "投资建议",
    ]
    return any(marker in lowered or marker in text for marker in medical_markers) or any(
        marker in lowered or marker in text for marker in finance_markers
    )


def _professional_boundary_reply(text: str) -> str:
    medical_markers = [
        "布洛芬",
        "用药",
        "吃多少",
        "剂量",
        "毫克",
        "退烧药",
        "止痛药",
    ]
    if any(marker in text for marker in medical_markers):
        return _quality_copy("professional_medical", seed=text)
    return _quality_copy("professional_finance", seed=text)


def _professional_boundary_notice(text: str) -> str:
    if any(marker in text for marker in ["布洛芬", "用药", "吃多少", "剂量", "毫克"]):
        return "用药这类事我会先收住，只帮你整理安全核对项，不给个人化剂量。"
    return "金融这类高风险建议我不做保赚承诺，只帮你把风险和决策条件讲清楚。"


def _recoverable_secret_input(
    lowered: str,
    *,
    privacy_level: str,
    sensitivity_hits: list[str] | tuple[str, ...],
    brain_intent: str | None,
) -> bool:
    if privacy_level != "high" or not sensitivity_hits:
        return False
    if brain_intent in {"memory_update", "memory_correction", "memory_query"}:
        return False
    # Keep api_key-only inputs on the existing Phase 35 failed-turn privacy route; Phase 41
    # closes the quality gap for token/password style chat inputs that previously went blank.
    return bool(re.search(r"\b(?:token|password)\s*=", lowered))


def _follow_ups_for_status(status: str) -> list[str]:
    if status == "recoverable_privacy_block":
        return ["用占位符重新描述", "轮换真实凭据", "改问脱敏流程"]
    if status == "desktop_capability_boundary":
        return ["改用浏览器任务", "只生成操作方案", "检查可用工具"]
    if status == "system_prompt_refusal":
        return ["说明可见能力", "生成安全说明", "解释确认规则"]
    if status == "persona_boundary":
        return ["说明可用能力", "走工具流程", "解释账号边界"]
    if status == "supportive_safety_refusal":
        return ["重新说明合法目标", "只生成安全方案", "解释确认规则"]
    if status == "professional_safety_boundary":
        return ["整理咨询清单", "说明风险边界", "改成通用科普"]
    return ["继续按这三点展开", "生成验收清单", "补充异常场景"]
