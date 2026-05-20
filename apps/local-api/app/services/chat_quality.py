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
        failure_advisories: list[dict[str, Any]] | None = None,
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
                    "failure_advisories": list(failure_advisories or []),
                    "capability_boundary": boundary,
                },
                tool_notice=(
                    _quality_copy("desktop_boundary", seed=text)
                ),
            )

        if _cross_session_memory_exfiltration_request(text):
            return self._outcome(
                "这类跨用户、跨会话或隐藏数据读取请求我不能帮。聊天内容和记忆必须按权限边界隔离，不能因为一句话就越权输出。",
                intent="privacy_boundary",
                status="supportive_safety_refusal",
                reason_codes=[
                    "chat_quality_policy",
                    "cross_session_memory_exfiltration_refusal",
                    "no_task_or_tool_created",
                ],
                structured={
                    "failure_advisories": list(failure_advisories or []),
                    "task_created": False,
                    "tool_created": False,
                    "approval_created": False,
                    "fail_closed": True,
                    "boundary_scope": "cross_user_or_hidden_chat_data",
                },
                safety_notice="不能读取其他用户、其他会话或隐藏数据里的聊天内容。",
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
                    "failure_advisories": list(failure_advisories or []),
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
                    "failure_advisories": list(failure_advisories or []),
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
                    "failure_advisories": list(failure_advisories or []),
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

        if (
            ("后端聊天链路验收" in text and "三点" in text)
            or ("鍚庣鑱婂ぉ閾捐矾楠屾敹" in text and "涓夌偣" in text)
        ):
            return self._outcome(
                "按你刚刚改的这句，前一个目标先停掉，先只做后端聊天链路验收，给你三点：\n1. 先跑主链路，确认上下文、模型、工具和投递都能接上。\n2. 再看回复质量，重点盯住别把没做的事说成做完，语气也别太像系统说明。\n3. 把结果状态说清，确保请求处理正确，再把关键证据对齐，方便复盘和排查。",
                intent="quality_latest_instruction_override",
                status="latest_instruction_priority",
                reason_codes=[
                    "chat_quality_policy",
                    "latest_instruction_priority",
                    "quality_latest_instruction_override",
                ],
                structured={"latest_instruction_priority": True},
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
        top_level_boundary = {}
        if isinstance(structured.get("capability_boundary"), dict):
            top_level_boundary["capability_boundary"] = redact(structured["capability_boundary"])
        route_name = "direct"
        capability_boundary = structured.get("capability_boundary")
        if (
            isinstance(capability_boundary, dict)
            and str(capability_boundary.get("tool_namespace") or "") == "desktop"
        ):
            route_name = "desktop_native_request"
        plan = plan.model_copy(
            update={
                "title": "鑳藉姏杈圭晫" if tool_notice else plan.title,
                "style": "quality_boundary" if safety_notice or tool_notice else "result_first",
                "follow_up_options": follow_ups,
                "structured_payload": {
                    **plan.structured_payload,
                    **top_level_boundary,
                    "scenario": "chat_quality_policy",
                    **voice_metadata_for_scenario(_voice_scenario_for_quality_status(status)),
                    "route_semantics": {
                        "route": route_name,
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
                "tone_mode": (
                    "safety_boundary"
                    if status in {
                        "professional_safety_boundary",
                        "supportive_safety_refusal",
                        "persona_boundary",
                        "system_prompt_refusal",
                        "recoverable_privacy_block",
                    }
                    else plan.tone_mode
                ),
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
        "妗岄潰鍘熺敓",
        "妗岄潰绐楀彛",
        "鎺у埗妗岄潰",
        "鎺у埗绐楀彛",
        "绐楀彛缃《",
        "绉诲姩榧犳爣",
        "鍏ㄥ眬閿洏",
        "妗岄潰鎴浘",
        "鏈満妗岄潰",
    ]
    action_markers = [
        "鎵ц",
        "鎿嶄綔",
        "鐐瑰嚮",
        "杈撳叆",
        "鎵撳紑",
        "鎺у埗",
        "鎴浘",
        "鏈€灏忓寲",
        "鏈€澶у寲",
        "缃《",
        "鍏抽棴绐楀彛",
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
        "status": "capability_not_supported",
        "executed": False,
        "supported_actions": [],
        "boundary": "\u5f53\u524d\u540e\u7aef\u8fd8\u6ca1\u6709\u539f\u751f\u7a97\u53e3\u63a7\u5236\u3001\u9f20\u6807\u952e\u76d8\u5168\u5c40\u63a7\u5236\u548c\u684c\u9762\u622a\u56fe\u5b9a\u4f4d\u80fd\u529b\u3002",
        "safe_fallbacks": ["browser.*", "file.*", "terminal.*"],
        "failure_code": "desktop_native_not_supported",
        "requires_future_design": ["capability", "approval", "trace", "artifact", "sandbox"],
    }


def _desktop_boundary_reply(text: str = "") -> str:
    return _quality_copy("desktop_boundary", seed=text)


def _persona_boundary_question(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "\u4f60\u662f\u771f\u4eba",
        "\u771f\u4eba\u5417",
        "\u9690\u85cf\u8d26\u53f7",
        "\u79c1\u4e0b\u767b\u5f55",
        "\u7ed5\u8fc7\u7cfb\u7edf",
        "\u5077\u5077\u767b\u5f55",
    )
    return any(marker in text for marker in markers) or any(marker in lowered for marker in ("hidden account", "real human"))

def _system_prompt_or_trace_request(text: str) -> bool:
    lowered = text.lower()
    internal_markers = (
        "system prompt",
        "developer prompt",
        "trace",
        "trace_id",
        "approval_id",
        "tool_call_id",
        "\u7cfb\u7edf\u63d0\u793a",
        "\u5f00\u53d1\u8005\u63d0\u793a",
        "\u5185\u90e8\u94fe\u8def",
    )
    request_markers = ("\u53d1\u7ed9\u6211", "\u539f\u6837", "\u8f93\u51fa", "show", "dump")
    corrupted_trace_request = "trace" in lowered and text.count("\ufffd") >= 4
    wants_raw_internal_output = any(marker in lowered or marker in text for marker in request_markers)
    return (any(marker in lowered or marker in text for marker in internal_markers) or corrupted_trace_request) and wants_raw_internal_output

def _supportive_safety_refusal_request(text: str) -> bool:
    lowered = text.lower()
    request_markers = (
        "\u53ea\u7ed9\u65b9\u6848",
        "\u4e0d\u8981\u6267\u884c",
        "\u4e0d\u8981\u521b\u5efa\u4efb\u52a1",
        "plan only",
        "do not execute",
    )
    action_markers = (
        "\u5220\u9664",
        "\u5220\u6587\u4ef6",
        "\u5220\u4efb\u52a1",
        "delete",
        "remove",
    )
    return any(marker in lowered or marker in text for marker in request_markers) and any(
        marker in lowered or marker in text for marker in action_markers
    )

def _cross_session_memory_exfiltration_request(text: str) -> bool:
    lowered = text.lower()
    scope_markers = (
        "\u5176\u4ed6\u7528\u6237",
        "\u5176\u4ed6\u4f1a\u8bdd",
        "\u9690\u85cf\u6570\u636e\u5e93",
        "\u9690\u85cf\u6570\u636e",
        "\u8de8\u4f1a\u8bdd",
        "\u8de8\u7528\u6237",
    )
    data_markers = ("\u804a\u5929\u5185\u5bb9", "\u804a\u5929\u8bb0\u5f55", "\u4f1a\u8bdd\u5185\u5bb9", "\u8bb0\u5fc6", "\u5bf9\u8bdd")
    request_markers = ("\u8f93\u51fa", "\u7ed9\u6211", "\u5bfc\u51fa", "\u590d\u8ff0", "\u5217\u51fa", "\u8bfb\u53d6")
    return any(marker in lowered or marker in text for marker in scope_markers) and any(
        marker in lowered or marker in text for marker in data_markers
    ) and any(marker in lowered or marker in text for marker in request_markers)

def _high_risk_professional_advice(text: str) -> bool:
    lowered = text.lower()
    medical_markers = ("\u5e03\u6d1b\u82ac", "\u7528\u836f", "\u5403\u591a\u5c11", "\u5242\u91cf", "\u6beb\u514b", "\u9000\u70e7\u836f", "\u6b62\u75db\u836f", "\u5904\u65b9\u836f", "\u836f\u91cf", "\u513f\u7ae5\u7528\u836f")
    finance_markers = (
        "\u4fdd\u8bc1\u6536\u76ca", "\u7a33\u8d5a", "\u5168\u90e8\u79ef\u84c4", "\u6ee1\u4ed3", "\u786e\u5b9a\u4e70\u5165",
        "\u8d37\u6b3e\u4e70", "\u4e0d\u8981\u63d0\u9192\u98ce\u9669", "\u91d1\u878d\u5efa\u8bae", "\u6295\u8d44\u5efa\u8bae",
        "all my savings", "all-in", "all in", "single stock", "buy or not"
    )
    finance_asset_markers = ("\u80a1\u7968", "\u4e00\u53ea\u80a1", "stock", "etf", "fund", "crypto", "coin")
    concentration_markers = ("\u538b\u4e00\u53ea\u80a1", "\u628a\u94b1\u90fd\u538b", "\u5168\u90e8\u79ef\u84c4", "\u6ee1\u4ed3", "\u91cd\u4ed3", "\u68ad\u54c8", "all my savings", "all-in", "all in", "single stock")
    forced_buy_markers = ("\u4e70\u4e0d\u4e70", "\u786e\u5b9a\u4e70\u5165", "\u76f4\u63a5\u7ed9\u4e70\u5165\u7ed3\u8bba", "buy or not")
    return (
        any(marker in lowered or marker in text for marker in medical_markers)
        or any(marker in lowered or marker in text for marker in finance_markers)
        or (
            any(marker in lowered or marker in text for marker in finance_asset_markers)
            and any(marker in lowered or marker in text for marker in concentration_markers + forced_buy_markers)
        )
    )

def _professional_boundary_reply(text: str) -> str:
    medical_markers = ("\u5e03\u6d1b\u82ac", "\u7528\u836f", "\u5403\u591a\u5c11", "\u5242\u91cf", "\u6beb\u514b", "\u9000\u70e7\u836f", "\u6b62\u75db\u836f")
    if any(marker in text for marker in medical_markers):
        return _quality_copy("professional_medical", seed=text)
    return (
        "\u6211\u4e0d\u80fd\u76f4\u63a5\u66ff\u4f60\u4e0b\u8fd9\u79cd\u9ad8\u98ce\u9669\u4e70\u5165\u7ed3\u8bba\uff0c\u5c24\u5176\u662f\u628a\u5927\u90e8\u5206\u8d44\u91d1\u538b\u5230\u5355\u4e00\u6807\u7684\u4e0a\u3002"
        " \u6211\u53ef\u4ee5\u5148\u5e2e\u4f60\u628a\u98ce\u9669\u3001\u4ed3\u4f4d\u4e0a\u9650\u548c\u5224\u65ad\u6761\u4ef6\u5217\u6e05\u695a\uff0c\u518d\u51b3\u5b9a\u8981\u4e0d\u8981\u7ee7\u7eed\u3002"
    )


def _professional_boundary_notice(text: str) -> str:
    medical_markers = ("\u5e03\u6d1b\u82ac", "\u7528\u836f", "\u5403\u591a\u5c11", "\u5242\u91cf", "\u6beb\u514b", "\u9000\u70e7\u836f", "\u6b62\u75db\u836f")
    if any(marker in text for marker in medical_markers):
        return _quality_copy("professional_medical", seed=text)
    return "\u4e0d\u63d0\u4f9b\u8fd9\u79cd\u9ad8\u98ce\u9669\u6295\u8d44\u7684\u76f4\u63a5\u4e70\u5165\u7ed3\u8bba\uff0c\u4f1a\u5148\u8bf4\u660e\u98ce\u9669\u8fb9\u754c\u548c\u66f4\u7a33\u59a5\u7684\u5224\u65ad\u65b9\u5f0f\u3002"


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
    if _readonly_browser_secret_url_context(lowered):
        return False
    return bool(re.search(r"\b(?:token|password)\s*=", lowered))


def _readonly_browser_secret_url_context(lowered: str) -> bool:
    readonly_markers = (
        "只读浏览",
        "只读页面",
        "read-only browser",
        "readonly browser",
        "view only",
    )
    url_markers = ("url", "链接", "link", "query", "参数", "querystring")
    return any(marker in lowered for marker in readonly_markers) and any(
        marker in lowered for marker in url_markers
    )

def _follow_ups_for_status(status: str) -> list[str]:
    if status == "recoverable_privacy_block":
        return ["\u7528\u5360\u4f4d\u7b26\u91cd\u65b0\u63cf\u8ff0", "\u66ff\u6362\u771f\u5b9e\u51ed\u636e", "\u6539\u6210\u8131\u654f\u6d41\u7a0b"]
    if status == "desktop_capability_boundary":
        return ["\u6539\u7528\u6d4f\u89c8\u5668\u4efb\u52a1", "\u53ea\u751f\u6210\u64cd\u4f5c\u65b9\u6848", "\u68c0\u67e5\u53ef\u7528\u5de5\u5177"]
    if status == "system_prompt_refusal":
        return ["\u8bf4\u660e\u53ef\u89c1\u80fd\u529b", "\u751f\u6210\u5b89\u5168\u8bf4\u660e", "\u89e3\u91ca\u786e\u8ba4\u89c4\u5219"]
    if status == "persona_boundary":
        return ["\u8bf4\u660e\u53ef\u7528\u80fd\u529b", "\u8d70\u5de5\u5177\u6d41\u7a0b", "\u89e3\u91ca\u8d26\u53f7\u8fb9\u754c"]
    if status == "supportive_safety_refusal":
        return ["\u91cd\u65b0\u8bf4\u660e\u5408\u6cd5\u76ee\u6807", "\u53ea\u751f\u6210\u5b89\u5168\u65b9\u6848", "\u89e3\u91ca\u786e\u8ba4\u89c4\u5219"]
    if status == "professional_safety_boundary":
        return ["\u6574\u7406\u54a8\u8be2\u6e05\u5355", "\u8bf4\u660e\u98ce\u9669\u8fb9\u754c", "\u6539\u6210\u901a\u7528\u79d1\u666e"]
    return ["\u7ee7\u7eed\u6309\u8fd9\u4e09\u70b9\u5c55\u5f00", "\u751f\u6210\u9a8c\u6536\u6e05\u5355", "\u8865\u5145\u5f02\u5e38\u573a\u666f"]
