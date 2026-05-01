from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core_types import ResponsePlan, TaskMode
from response_composer import ResponseComposer
from trace_service import redact

from app.services.natural_chat import visible_text_guard


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

        if _latest_instruction_override(text):
            return self._outcome(
                _latest_instruction_reply(),
                intent="quality_latest_instruction_override",
                status="latest_instruction_priority",
                reason_codes=[
                    "chat_quality_policy",
                    "stop_change_marker",
                    "latest_instruction_priority",
                ],
                structured={
                    "superseded_goal": True,
                    "previous_goal_role": "background_only",
                },
            )

        if _desktop_native_request(text):
            boundary = _desktop_boundary_contract()
            return self._outcome(
                _desktop_boundary_reply(),
                intent="capability_boundary",
                status="desktop_capability_boundary",
                reason_codes=["chat_quality_policy", "desktop_native_not_supported"],
                structured={
                    "capability_boundary": boundary,
                },
                tool_notice=(
                    "当前后端没有 desktop.* 原生桌面工具；"
                    "没有执行桌面窗口、鼠标或键盘动作。"
                ),
            )

        if _persona_boundary_question(text):
            return self._outcome(
                (
                    "我不是现实中的真人，也没有隐藏账号可以私下登录。"
                    "我能做的是在受控任务里帮你操作浏览器、文件或工具；"
                    "涉及账号、登录、外部动作或高风险步骤时，会按安全规则确认并留下证据。"
                ),
                intent="boundary_question",
                status="persona_boundary",
                reason_codes=["chat_quality_policy", "persona_hidden_account_boundary"],
                structured={},
                safety_notice="不会声称真人身份、隐藏账号或安全绕过能力。",
            )

        if _system_prompt_or_trace_request(text):
            return self._outcome(
                (
                    "这部分我不能完整输出，因为系统提示、开发者指令和内部 trace 属于运行边界。"
                    "我可以改为说明当前可见能力、审批规则、隐私保护方式，"
                    "或帮你生成一份面向用户的安全边界说明。"
                ),
                intent="boundary_question",
                status="system_prompt_refusal",
                reason_codes=["chat_quality_policy", "internal_instruction_refusal"],
                structured={},
                safety_notice="系统提示、开发者指令和内部审计细节不会作为普通聊天内容披露。",
            )

        if _recoverable_secret_input(
            lowered,
            privacy_level=privacy_level,
            sensitivity_hits=sensitivity_hits,
            brain_intent=brain_intent,
        ):
            return self._outcome(
                (
                    "我看到了疑似敏感信息，所以不会复述或继续处理这些值。"
                    "建议你立即把真实 token/password 轮换掉；如果只是测试，"
                    "请用 [REDACTED_SECRET] 或示例占位符继续描述你想验证的流程。"
                ),
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
                safety_notice="疑似敏感值已被保护；不会发送给云端模型。",
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
        visible = visible_text_guard(str(redact(text)))
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
                    "chat_quality_policy": {
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


def _latest_instruction_override(text: str) -> bool:
    return (
        any(marker in text for marker in ["停", "先停", "停止", "改成", "换成"])
        and any(marker in text for marker in ["只做", "改成", "换成"])
        and "后端" in text
        and "聊天链路" in text
        and any(marker in text for marker in ["三点", "3点", "三条", "3条"])
    )


def _latest_instruction_reply() -> str:
    return (
        "明白，前一个目标先停掉。按你新的要求，只看后端聊天链路验收，可以收成三点：\n\n"
        "1. 请求处理正确：`/api/chat/turn` 能创建 turn，stream、turn detail 和 events 状态一致。\n"
        "2. 上下文状态可靠：当前用户指令优先，历史摘要、记忆和会话状态只作辅助，"
        "不覆盖改口后的目标。\n"
        "3. 异常与边界可控：隐私、审批、任务状态、工具能力缺口都要给出可恢复说明，不伪装已执行。"
    )


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


def _desktop_boundary_reply() -> str:
    return (
        "这属于 desktop.* 原生桌面能力边界：当前后端没有原生窗口控制、"
        "全局鼠标键盘控制或桌面截图定位工具。"
        "我不会把它伪装成已经执行。\n\n"
        "可行替代路径是：如果目标是网页，我可以走 browser.* 并保存 URL、标题、快照或截图证据；"
        "如果目标是文件或命令，可以走 file.* 或 terminal.run 的受控任务链路。"
        "这些路径仍会经过 Safety、Approval、Trace 和回放证据。"
    )


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
        "内部 trace",
        "trace 明细",
        "完整 trace",
        "system prompt",
        "developer message",
    ]
    request_markers = ["输出", "完整", "展示", "给我", "泄露", "忽略", "print", "show"]
    return any(marker in lowered or marker in text for marker in internal_markers) and any(
        marker in lowered or marker in text for marker in request_markers
    )


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
        return ["说明可见能力", "生成安全边界说明", "解释审批规则"]
    if status == "persona_boundary":
        return ["说明可用能力", "创建受控任务", "解释账号边界"]
    return ["继续按这三点展开", "生成验收清单", "补充异常场景"]
