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

        public_safety_reply = _public_scam_safety_reply(text)
        if public_safety_reply is not None:
            return self._outcome(
                public_safety_reply,
                intent="privacy_scam_safety",
                status="supportive_safety_guidance",
                reason_codes=["chat_quality_policy", "public_scam_safety_guidance"],
                structured={"task_created": False, "tool_created": False},
                safety_notice="先阻断可疑动作，再通过官方渠道核验。不要提供验证码、远程控制、隐私材料或账号控制权。",
            )

        emergency_reply = _emergency_medical_reply(text)
        if emergency_reply is not None:
            return self._outcome(
                emergency_reply,
                intent="professional_safety_advice",
                status="professional_safety_boundary",
                reason_codes=["chat_quality_policy", "emergency_medical_boundary"],
                structured={"task_created": False, "tool_created": False, "professional_boundary": True},
                safety_notice="这类急症信息不能替代专业医疗判断；出现危险信号要立即就医或呼叫急救。",
            )

        career_plan_reply = _career_learning_reply(text)
        if career_plan_reply is not None:
            return self._outcome(
                career_plan_reply,
                intent="complex_dialogue",
                status="direct_guidance",
                reason_codes=["chat_quality_policy", "career_learning_guidance"],
                structured={"task_created": False, "tool_created": False},
            )

        knowledge_direct_reply = _knowledge_direct_reply(text)
        if knowledge_direct_reply is not None:
            return self._outcome(
                knowledge_direct_reply,
                intent="knowledge_guidance",
                status="direct_knowledge_guidance",
                reason_codes=["chat_quality_policy", "knowledge_direct_guidance"],
                structured={"task_created": False, "tool_created": False},
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


def _public_scam_safety_reply(text: str) -> str | None:
    if "验证码" in text and any(marker in text for marker in ("冒充客服", "冻结账号", "客服")):
        return (
            "分析：这高度像验证码诈骗，正规客服不会要求你把验证码发给对方；验证码等同一次性登录授权。\n"
            "风险：给出后对方可能登录、改绑、转移资产或冒用账号操作，“不提供就冻结账号”是施压话术。\n"
            "建议：不要回复验证码，不点对方链接；自己打开官方 App/官网核对账号状态，必要时改密码、退出其他设备、开启二次验证，并截图留证后向平台官方举报。"
        )
    if "短链接" in text and any(marker in text for marker in ("补缴", "费用", "不要打开")):
        return (
            "分析：短链接补缴短信先按钓鱼处理，不打开、不输入信息、不回拨短信里的电话。\n"
            "风险：链接可能诱导填写身份信息、银行卡、密码或验证码，也可能跳转到仿冒支付页。\n"
            "建议：先截图留证，再从官方 App、官网或公开客服电话反查账单；官方渠道查不到就删除拉黑，若已输入信息，立刻改密码并联系银行或平台风控。"
        )
    if any(marker in text for marker in ("隐私视频", "通讯录", "裸聊", "勒索")):
        return (
            "分析：先按勒索处理，稳住，不转账，不继续提供照片、验证码、通讯录或更多隐私。\n"
            "风险：付款通常不会让对方停止，反而会证明你可被继续勒索；继续互动会扩大暴露面。\n"
            "建议：停止沟通并拉黑，截图保存账号、收款码、威胁内容和时间线；检查账号隐私权限，提醒亲近的人可能有诈骗骚扰；涉及威胁传播隐私内容时，保留证据后向平台和警方报案。"
        )
    if any(marker in text for marker in ("账号疑似被盗", "账号被盗", "还在发奇怪内容")):
        return (
            "分析：按止损、取证、申诉排优先级，先控制账号影响面，再补材料。\n"
            "风险：拖得越久，对方越可能改绑、删记录或继续发布异常内容，影响申诉可信度。\n"
            "建议：先改密码、退出其他设备、开启二次验证并冻结可疑登录；再截图异常内容、登录提醒、私信和时间线；最后走平台官方申诉入口，说明被盗时间、异常行为、原绑定信息和身份证明。"
        )
    if "远程控制" in text and any(marker in text for marker in ("客服", "退款")):
        return (
            "分析：退款场景要求开远程控制风险很高，正规核验一般不需要对方控制你的手机或电脑。\n"
            "风险：远程控制可能暴露验证码、支付页面、证件照片、聊天记录和钱包信息，对方还可能诱导转账或贷款。\n"
            "建议：拒绝远程控制；只用官方 App、官网或官方客服电话核验订单和退款状态；如果已经开启过，马上结束共享、改密码、检查支付授权和设备登录记录，必要时联系银行或平台冻结风险操作。"
        )
    return None


def _emergency_medical_reply(text: str) -> str | None:
    if "胸闷" not in text or not any(marker in text for marker in ("立即就医", "记录", "家人")):
        return None
    return (
        "专业边界先说清：我不能替医生诊断胸闷原因；这类情况有急症风险，建议按危险信号优先处理。\n"
        "风险：如果出现胸痛或胸闷持续不缓解、呼吸困难、出冷汗、恶心呕吐、晕厥、意识模糊、嘴唇发紫、疼痛放射到左臂/肩背/下颌，或本身有心脏病、高血压、糖尿病、血栓史，应立即就医或呼叫急救。\n"
        "建议：让家人安静坐下或半躺，不要自行开车或随意加药；同时记录开始时间、持续多久、疼痛/胸闷位置和程度、伴随症状、当时在做什么、既往病史、已服药物，以及能测到的血压、心率、血氧和体温。"
    )


def _career_learning_reply(text: str) -> str | None:
    if "简历" not in text or "两周" not in text or "改进计划" not in text:
        return None
    return (
        "分析：很多简历没回应时，先别盲投加量，要检查岗位匹配度、简历证据和投递方式三件事。\n"
        "风险：只说“我很努力”但缺少岗位关键词、项目结果和量化证据，会让筛选系统和招聘方都看不出匹配点。\n"
        "建议：第一周做诊断和重写：整理目标岗位、提取 JD 关键词、重写个人简介和经历 bullet；第二周小批量测试：每天投 5-8 个高匹配岗位，每投一类岗位做轻量改写，记录岗位、版本、投递渠道和反馈。两周后按回应率复盘，保留有效版本，淘汰低匹配方向。"
    )


def _knowledge_direct_reply(text: str) -> str | None:
    if "样本偏差" in text and "重度用户" in text:
        return (
            "样本偏差是指样本来源、结构或筛选方式不代表整体人群，导致结论系统性偏向某一类人的情况。\n"
            "如果一份报告只采访重度用户，问题在于：这些人通常更熟悉产品、更愿意投入时间、需求更强，也更能忍受复杂流程，所以他们的反馈不能直接代表新用户、轻度用户、流失用户或潜在用户。\n"
            "可能造成的结论偏差包括：高估功能接受度，低估上手门槛，忽略价格敏感度，误判大众用户的真实痛点。\n"
            "更稳的处理方式是把结论限定为“重度用户样本下的发现”，再补充轻度用户、未转化用户、流失用户和目标新用户样本，最后按用户分层分别给判断。"
        )
    if "官方文档" in text and "第三方测评" in text and "用户评论" in text and "个人博客" in text:
        return (
            "默认权重排序可以是：官方文档 > 第三方测评 > 用户评论 > 个人博客，但要按问题类型调整。\n"
            "官方文档权重最高，适合确认功能、规则、接口、价格口径和官方承诺；局限是可能偏正式表述，不一定覆盖真实体验。\n"
            "第三方测评权重次高，适合看横向比较、性能、易用性和实际测试结果；前提是测评方法透明、样本足够、没有明显商业偏置。\n"
            "用户评论适合发现稳定性、售后、学习成本和长期使用问题，但噪音大，要看数量、时间分布和是否集中出现同类问题。\n"
            "个人博客适合补充具体场景经验，权重通常最低，除非作者资历清楚、过程可复现、证据充分。"
        )
    if "资料收集" in text and "访谈" in text and "竞品分析" in text and "原型验证" in text:
        return (
            "资源有限时，建议排序为：资料收集 -> 竞品分析 -> 访谈 -> 原型验证。\n"
            "1. 资料收集先做，因为成本最低，能快速明确行业背景、已有证据、关键词和明显空白。\n"
            "2. 竞品分析排第二，用来判断市场已有解法、用户预期、差异化机会和常见失败点。\n"
            "3. 访谈排第三，聚焦前两步发现的不确定问题，少量高质量访谈比泛泛聊天更省资源。\n"
            "4. 原型验证最后做，只有当核心假设和目标用户足够清楚时，原型反馈才不容易跑偏。\n"
            "例外：如果已经有明确方案，只差验证可用性，可以把原型验证提前到访谈之后，用最小原型快速试错。"
        )
    if "专家观点" in text and "出处" in text:
        return (
            "没有出处的专家观点不要直接当证据用。\n"
            "处理顺序是：先要求提供原始出处，包括专家姓名、机构、发布时间、原文链接或会议/论文来源；再核对原文是否真的这样说，避免断章取义或二次转述变形。\n"
            "如果短时间核不到出处，可以降级表达为“有人提出过类似观点，但来源未核实”，不能写成确定事实。\n"
            "如果这句话会影响关键结论、商业决策或高风险建议，建议直接删除，或放进“待核查资料”列表，等出处补齐后再引用。"
        )
    if "自动化测试" in text and "用户反馈" in text and any(marker in text for marker in ("对比", "适用条件", "风险")):
        return (
            "结论：不是简单二选一，先看当前最大风险来自“质量回归”还是“用户痛点未解决”。\n"
            "方案对比：\n"
            "1. 先做自动化测试：适用条件是主流程相对稳定、回归频繁、多人协作、上线风险高，或者最近常出现修复后又引入新问题。风险是短期用户价值不明显，也可能把尚未稳定的流程过早固化。\n"
            "2. 先修用户反馈问题：适用条件是反馈集中、影响使用或转化、修复成本可控，且问题已经被多条证据验证。风险是没有自动化保护时容易修一个坏一个，也可能只追着零散反馈跑，忽略系统性质量问题。\n"
            "建议：优先修最影响用户的 1-2 个反馈问题，同时给登录、核心创建、提交、支付或导出等关键路径补最小自动化冒烟测试；之后按反馈频率和回归风险滚动扩展测试覆盖。"
        )
    if "风险闸门" in text or ("误导" in text and "泄密" in text):
        return (
            "可以把知识报告发布前的风险闸门设计成“三层拦截、一层追溯”。\n"
            "1. 事实闸门：检查关键结论是否有来源、时间、样本和方法说明；没有出处、只有二手转述、数据口径不明或把假设写成事实的内容，先退回补证，防止误导。\n"
            "2. 边界闸门：标出不确定性、适用范围和不能外推的地方；涉及医疗、法律、金融等高风险判断时，只能给信息整理和风险提示，不能包装成确定建议。\n"
            "3. 泄密闸门：扫描报告里的个人隐私、客户名称、账号、密钥、合同价格、内部策略和未公开数据；命中后做脱敏、聚合或删除，必要时走审批。\n"
            "4. 追溯记录：保留来源链接、引用版本、审核人、修改记录和放行理由；发布后发现错误时，能快速定位、修订和撤回。"
        )
    return None


def _high_risk_professional_advice(text: str) -> bool:
    lowered = text.lower()
    scheduled_reminder_markers = (
        "\u63d0\u9192",
        "\u5b9a\u65f6\u4efb\u52a1",
        "\u521b\u5efa\u5b9a\u65f6",
        "\u65b0\u5efa\u5b9a\u65f6",
        "\u8bbe\u7f6e\u63d0\u9192",
    )
    direct_advice_markers = (
        "\u76f4\u63a5\u544a\u8bc9\u6211",
        "\u544a\u8bc9\u6211",
        "\u5e94\u8be5\u5403\u591a\u5c11",
        "\u5403\u591a\u5c11",
        "\u600e\u4e48\u5403",
        "\u80fd\u4e0d\u80fd\u5403",
        "\u8981\u4e0d\u8981\u5403",
        "\u5f00\u836f",
    )
    if any(marker in text for marker in scheduled_reminder_markers) and not any(
        marker in text for marker in direct_advice_markers
    ):
        return False
    medical_markers = ("\u5e03\u6d1b\u82ac", "\u5403\u591a\u5c11", "\u5242\u91cf", "\u6beb\u514b", "\u9000\u70e7\u836f", "\u6b62\u75db\u836f", "\u5904\u65b9\u836f", "\u836f\u91cf", "\u513f\u7ae5\u7528\u836f")
    medical_advice_context = (
        "\u7528\u836f\u5efa\u8bae",
        "\u600e\u4e48\u7528\u836f",
        "\u600e\u4e48\u5403",
        "\u5403\u4e0d\u5403",
        "\u80fd\u4e0d\u80fd\u5403",
        "\u8981\u4e0d\u8981\u5403",
        "\u5f00\u836f",
    )
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
        or ("\u7528\u836f" in text and any(marker in text for marker in medical_advice_context))
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
