from __future__ import annotations

import re
from typing import Any

from core_types import ApiModel, ErrorCode, ResponsePlan
from pydantic import Field

_REASONING_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_REASONING_OPEN_RE = re.compile(r"<think\b[^>]*>.*", re.IGNORECASE | re.DOTALL)
_REASONING_START = "<think"
_REASONING_END = "</think>"


class ComposeRequest(ApiModel):
    user_text: str = ""
    result_summary: str
    style: str = "result_first"
    scenario: str = "direct"
    persona: dict[str, Any] = Field(default_factory=dict)
    heart: dict[str, Any] = Field(default_factory=dict)
    risk_level: str | None = None
    route_profile: str | None = None
    notices: dict[str, Any] = Field(default_factory=dict)
    trace_refs: list[dict[str, Any]] = Field(default_factory=list)


class ComposeResult(ApiModel):
    text: str
    response_plan: ResponsePlan
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReasoningTagFilter:
    def __init__(self) -> None:
        self._buffer = ""
        self._inside_reasoning = False
        self._hidden_reasoning = False
        self._emitted_visible = False

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self._buffer += text
        output_parts: list[str] = []
        while self._buffer:
            if self._inside_reasoning:
                end_index = self._buffer.lower().find(_REASONING_END)
                if end_index < 0:
                    keep = _longest_suffix_prefix(self._buffer, _REASONING_END)
                    self._buffer = self._buffer[len(self._buffer) - keep :] if keep else ""
                    break
                self._buffer = self._buffer[end_index + len(_REASONING_END) :]
                self._inside_reasoning = False
                self._hidden_reasoning = True
                continue

            start_index = self._buffer.lower().find(_REASONING_START)
            if start_index < 0:
                keep = _longest_suffix_prefix(self._buffer, _REASONING_START)
                safe_text = self._buffer[: len(self._buffer) - keep] if keep else self._buffer
                self._buffer = self._buffer[len(self._buffer) - keep :] if keep else ""
                if safe_text:
                    output_parts.append(self._visible_text(safe_text))
                break

            if start_index:
                output_parts.append(self._visible_text(self._buffer[:start_index]))
            tag_end_index = self._buffer.find(">", start_index)
            if tag_end_index < 0:
                self._buffer = self._buffer[start_index:]
                break
            self._buffer = self._buffer[tag_end_index + 1 :]
            self._inside_reasoning = True
            self._hidden_reasoning = True

        return "".join(output_parts)

    def finish(self) -> str:
        if not self._buffer:
            return ""
        if self._inside_reasoning:
            self._buffer = ""
            self._inside_reasoning = False
            return ""
        buffered = self._buffer
        self._buffer = ""
        if _is_prefix_of_marker(buffered, _REASONING_START):
            return ""
        return self._visible_text(strip_reasoning_tags(buffered))

    def _visible_text(self, text: str) -> str:
        if self._hidden_reasoning and not self._emitted_visible:
            text = text.lstrip()
        if text:
            self._emitted_visible = True
        return text


class ResponseComposer:
    async def compose(self, request: ComposeRequest) -> ComposeResult:
        raw_summary = strip_reasoning_tags(request.result_summary).strip()
        result_summary, redaction_summary = redact_visible_text(raw_summary)
        scenario = request.scenario or "direct"
        tone_metadata = _tone_metadata(request)
        safety_notice, safety_redactions = _redact_optional_string(
            request.notices.get("safety_notice")
        )
        tool_notice, tool_redactions = _redact_optional_string(request.notices.get("tool_notice"))
        raw_approval_prompt = request.notices.get("approval_prompt")
        raw_follow_ups = request.notices.get("follow_up_options") or []
        approval_redactions = _payload_redaction_summary(raw_approval_prompt)
        follow_up_redactions = _payload_redaction_summary(raw_follow_ups)
        approval_prompt = _redact_payload(raw_approval_prompt)
        follow_ups = _redact_payload(raw_follow_ups)
        if _is_high_risk(request) and not safety_notice:
            safety_notice = (
                "这属于高影响或高风险场景；在受控任务、Safety 和 Approval 链路确认前，"
                "我不会声称已经执行。"
            )
        redaction_summary = _merge_redaction_summaries(
            redaction_summary,
            safety_redactions,
            tool_redactions,
            approval_redactions,
            follow_up_redactions,
        )
        plan = ResponsePlan(
            style=request.style,
            title=_title_for_scenario(scenario),
            summary=result_summary,
            sections=_sections_for_scenario(scenario, result_summary),
            plain_text=result_summary,
            approval_prompt=approval_prompt if isinstance(approval_prompt, dict) else None,
            safety_notice=safety_notice,
            tool_notice=tool_notice,
            follow_up_options=[item for item in follow_ups if isinstance(item, str)],
            action_buttons=_action_buttons(
                scenario=scenario,
                approval_prompt=approval_prompt if isinstance(approval_prompt, dict) else None,
                follow_up_options=[item for item in follow_ups if isinstance(item, str)],
            ),
            tone_metadata=tone_metadata,
            redaction_summary=redaction_summary,
            trace_refs=request.trace_refs,
            structured_payload={
                "source": "response_composer",
                "scenario": scenario,
                "route_profile": request.route_profile,
                "risk_level": request.risk_level,
                "notices": _structured_notices(
                    {
                        **request.notices,
                        "safety_notice": safety_notice,
                        "tool_notice": tool_notice,
                        "approval_prompt": approval_prompt,
                        "follow_up_options": follow_ups,
                    }
                ),
            },
            tone_mode=_tone_mode_from_metadata(tone_metadata),
            quality_markers=_baseline_quality_markers(
                scenario=scenario,
                high_risk=bool(tone_metadata.get("deescalation_required")),
            ),
            boundary_notice=safety_notice or tool_notice,
            deescalation_notice=_deescalation_notice(tone_metadata),
            user_next_step=_first_next_step([item for item in follow_ups if isinstance(item, str)]),
        )
        return ComposeResult(
            text=result_summary,
            response_plan=plan,
            metadata={
                "source": "response_composer",
                "scenario": scenario,
                "redacted": redaction_summary["applied"],
            },
        )

    def begin_delta_stream(self) -> ReasoningTagFilter:
        return ReasoningTagFilter()

    def compose_delta(self, text: str) -> str:
        return strip_reasoning_tags(text)

    def compose_tool_unavailable(self) -> str:
        return (
            "我识别到这需要受控工具或真实执行能力。当前请求没有匹配到可执行路径；"
            "我可以先给出计划、风险点和下一步检查清单。"
        )

    def compose_clarification(self, questions: list[str]) -> str:
        visible_questions = [item for item in questions[:3] if item]
        if not visible_questions:
            return "我需要先确认几个关键信息，再继续。"
        return "我需要先确认：\n" + "\n".join(
            f"{index}. {question}"
            for index, question in enumerate(visible_questions, start=1)
        )

    def response_plan_for_status(
        self,
        *,
        summary: str,
        task_status: dict[str, Any] | None = None,
        approval_prompt: dict[str, Any] | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        safety_notice: str | None = None,
        memory_notice: str | None = None,
        tool_notice: str | None = None,
        trace_refs: list[dict[str, Any]] | None = None,
    ) -> ResponsePlan:
        visible_summary, redaction_summary = redact_visible_text(summary)
        safety_notice, safety_redactions = _redact_optional_string(safety_notice)
        memory_notice, memory_redactions = _redact_optional_string(memory_notice)
        tool_notice, tool_redactions = _redact_optional_string(tool_notice)
        approval_redactions = _payload_redaction_summary(approval_prompt)
        artifact_redactions = _payload_redaction_summary(artifact_refs or [])
        approval_prompt = _redact_payload(approval_prompt)
        artifact_refs = _redact_payload(artifact_refs or [])
        redaction_summary = _merge_redaction_summaries(
            redaction_summary,
            safety_redactions,
            memory_redactions,
            tool_redactions,
            approval_redactions,
            artifact_redactions,
        )
        style = "approval_required" if approval_prompt else "result_first"
        if safety_notice and not approval_prompt:
            style = "safety_boundary"
        title = "等待确认" if approval_prompt else None
        if safety_notice and not approval_prompt:
            title = "安全边界"
        scenario = _scenario_for_status(
            task_status=task_status,
            approval_prompt=approval_prompt,
            safety_notice=safety_notice,
            memory_notice=memory_notice,
            tool_notice=tool_notice,
        )
        tone_metadata = _default_tone_metadata(
            scenario=scenario,
            high_risk=bool(safety_notice or approval_prompt),
        )
        return ResponsePlan(
            title=title,
            style=style,
            summary=visible_summary,
            sections=[{"kind": "summary", "text": visible_summary}],
            task_status=task_status,
            approval_prompt=approval_prompt,
            artifact_refs=artifact_refs if isinstance(artifact_refs, list) else [],
            safety_notice=safety_notice,
            memory_notice=memory_notice,
            tool_notice=tool_notice,
            action_buttons=_action_buttons(
                scenario=scenario,
                approval_prompt=approval_prompt if isinstance(approval_prompt, dict) else None,
                follow_up_options=[],
            ),
            tone_metadata=tone_metadata,
            redaction_summary=redaction_summary,
            trace_refs=trace_refs or [],
            plain_text=visible_summary,
            structured_payload={
                "scenario": scenario,
                "task_status": task_status or {},
                "approval_prompt": approval_prompt or {},
                "artifact_refs": artifact_refs if isinstance(artifact_refs, list) else [],
                "safety_notice": safety_notice,
                "memory_notice": memory_notice,
                "tool_notice": tool_notice,
            },
            tone_mode=_tone_mode_from_metadata(tone_metadata),
            quality_markers=_baseline_quality_markers(
                scenario=scenario,
                high_risk=bool(safety_notice or approval_prompt),
            ),
            boundary_notice=safety_notice or tool_notice,
            deescalation_notice=_deescalation_notice(tone_metadata),
            user_next_step=_first_next_step([]),
        )

    def response_plan_for_clarification(
        self,
        *,
        summary: str,
        decision: dict[str, Any],
    ) -> ResponsePlan:
        base_plan = self.response_plan_for_status(summary=summary)
        visible_summary = base_plan.summary or base_plan.plain_text or ""
        return base_plan.model_copy(
            update={
                "title": "需要确认",
                "style": "clarification",
                "sections": [
                    {"kind": "clarification", "text": visible_summary},
                ],
                "action_buttons": _action_buttons(
                    scenario="clarification",
                    follow_up_options=["回答澄清问题", "只生成方案"],
                    approval_prompt=None,
                ),
                "tone_metadata": _default_tone_metadata(
                    scenario="clarification",
                    high_risk=bool(decision.get("blocker_level") == "high"),
                ),
                "structured_payload": {
                    "scenario": "clarification",
                    "clarification_decision": decision,
                },
            }
        )

    def response_plan_for_tool_boundary(
        self,
        *,
        summary: str,
        required_capability: str,
        next_actions: list[str],
        safety_notice: str | None = None,
    ) -> ResponsePlan:
        return self.response_plan_for_status(
            summary=summary,
            safety_notice=safety_notice,
            tool_notice="需要受控工具、Skill、MCP 或任务链路后才能执行。",
        ).model_copy(
            update={
                "title": "能力边界",
                "style": "tool_boundary",
                "follow_up_options": next_actions,
                "action_buttons": _action_buttons(
                    scenario="tool_boundary",
                    follow_up_options=next_actions,
                    approval_prompt=None,
                ),
                "tone_metadata": _default_tone_metadata(
                    scenario="tool_boundary",
                    high_risk=bool(safety_notice),
                ),
                "structured_payload": {
                    "scenario": "tool_boundary",
                    "required_capability": required_capability,
                    "next_actions": next_actions,
                    "safety_notice": safety_notice,
                    "tool_notice": "需要受控工具、Skill、MCP 或任务链路后才能执行。",
                },
            }
        )

    def response_plan_for_recovery(
        self,
        *,
        summary: str,
        error_code: str,
        recoverable: bool,
        suggested_next_actions: list[str],
        base_plan: ResponsePlan | None = None,
    ) -> ResponsePlan:
        plan = base_plan or self.response_plan_for_status(summary=summary)
        structured = {
            **plan.structured_payload,
            "scenario": "failure_recovery",
            "error_code": error_code,
            "recoverable": recoverable,
            "suggested_next_actions": suggested_next_actions,
        }
        return plan.model_copy(
            update={
                "style": "failure_recovery",
                "title": "可恢复失败" if recoverable else "执行失败",
                "follow_up_options": suggested_next_actions,
                "action_buttons": _action_buttons(
                    scenario="failure_recovery",
                    follow_up_options=suggested_next_actions,
                    approval_prompt=None,
                ),
                "tone_metadata": _default_tone_metadata(
                    scenario="failure_recovery",
                    high_risk=False,
                ),
                "structured_payload": structured,
            }
        )

    def compose_privacy_block(self) -> str:
        return (
            "我看到了疑似敏感信息，所以不会复述或继续处理这些值，也不会把它发送到云端模型。"
            "建议你立即轮换真实 token/password/private key；如果只是测试，"
            "请用 [REDACTED_SECRET] 或示例占位符继续描述你想验证的流程。"
        )

    def compose_model_not_configured(self) -> str:
        return (
            "需要先配置一个可用大脑。我已经保留这轮输入；"
            "配置本地或兼容 OpenAI 的模型后就能继续生成。"
        )

    def compose_cancelled(self, partial_text: str) -> str:
        if partial_text:
            return partial_text
        return "已停止生成。"

    def compose_failure(self, code: ErrorCode | str, message: str) -> str:
        code_value = code.value if isinstance(code, ErrorCode) else code
        if code_value == ErrorCode.MODEL_AUTH_FAILED.value:
            return "模型认证失败，请检查大脑配置中的密钥或 endpoint。"
        if code_value == ErrorCode.MODEL_TIMEOUT.value:
            return "模型响应超时，可以稍后重试或切换到更快的本地模型。"
        if code_value == ErrorCode.MODEL_ROUTE_BLOCKED_BY_PRIVACY.value:
            return self.compose_privacy_block()
        if code_value == ErrorCode.MODEL_NOT_CONFIGURED.value:
            return self.compose_model_not_configured()
        return f"这轮生成失败了：{message}"

    def response_plan_for_failure(self, *, code: ErrorCode | str, message: str) -> ResponsePlan:
        code_value = code.value if isinstance(code, ErrorCode) else code
        safety_notice = None
        if code_value in {
            ErrorCode.MODEL_ROUTE_BLOCKED_BY_PRIVACY.value,
            ErrorCode.SAFETY_BLOCKED.value,
            ErrorCode.TOOL_OUTPUT_BLOCKED.value,
        }:
            safety_notice = message
        return self.response_plan_for_status(
            summary=message,
            safety_notice=safety_notice,
        ).model_copy(
            update={
                "title": "生成失败" if safety_notice is None else "安全边界",
                "style": "failure",
                "tone_metadata": _default_tone_metadata(
                    scenario="failure",
                    high_risk=safety_notice is not None,
                ),
                "structured_payload": {
                    "scenario": "failure",
                    "status": "failed",
                    "error_code": code_value,
                    "safety_notice": safety_notice,
                }
            }
        )


def strip_reasoning_tags(text: str) -> str:
    if not text:
        return text
    without_closed_blocks = _REASONING_BLOCK_RE.sub("", text)
    return _REASONING_OPEN_RE.sub("", without_closed_blocks)


_SENSITIVE_PATTERNS = {
    "secret": re.compile(
        r"(?i)\b(secret|token|password|cookie|mnemonic|private[_-]?key|api[_-]?key)"
        r"\s*[:=]\s*([^'\"\s,;{}]+)"
    ),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*", re.DOTALL),
    "local_path": re.compile(r"(?i)(?:[a-z]:\\users\\[^\s,;]+|/(?:users|home)/[^\s,;]+)"),
}


def redact_visible_text(text: str) -> tuple[str, dict[str, Any]]:
    redacted = text
    categories: list[str] = []
    for category, pattern in _SENSITIVE_PATTERNS.items():
        if pattern.search(redacted):
            categories.append(category)
            if category == "secret":
                redacted = pattern.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
    return redacted, {"applied": bool(categories), "categories": sorted(set(categories))}


def _redact_optional_string(value: Any) -> tuple[str | None, dict[str, Any]]:
    if value is None:
        return None, {"applied": False, "categories": []}
    redacted, summary = redact_visible_text(str(value))
    return redacted, summary


def _redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_visible_text(value)[0]
    if isinstance(value, dict):
        return {str(key): _redact_payload(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


def _payload_redaction_summary(value: Any) -> dict[str, Any]:
    categories: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, str):
            summary = redact_visible_text(item)[1]
            categories.update(summary.get("categories", []))
        elif isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return {"applied": bool(categories), "categories": sorted(categories)}


def _merge_redaction_summaries(*summaries: dict[str, Any]) -> dict[str, Any]:
    categories: set[str] = set()
    for summary in summaries:
        categories.update(str(item) for item in summary.get("categories", []))
    return {"applied": bool(categories), "categories": sorted(categories)}


def _action_buttons(
    *,
    scenario: str,
    approval_prompt: dict[str, Any] | None,
    follow_up_options: list[str],
) -> list[dict[str, Any]]:
    buttons: list[dict[str, Any]] = []
    if approval_prompt is not None or scenario == "approval_required":
        buttons.extend(
            [
                {
                    "action": "approval.review",
                    "label": "查看确认项",
                    "style": "primary",
                    "requires_confirmation": True,
                },
                {
                    "action": "approval.deny",
                    "label": "拒绝执行",
                    "style": "secondary",
                    "requires_confirmation": False,
                },
            ]
        )
    elif scenario in {"tool_boundary", "safety_deny"}:
        buttons.extend(
            [
                {
                    "action": "task.create_plan",
                    "label": "只生成计划",
                    "style": "primary",
                    "requires_confirmation": False,
                },
                {
                    "action": "capability.configure",
                    "label": "检查能力配置",
                    "style": "secondary",
                    "requires_confirmation": False,
                },
            ]
        )
    elif scenario == "clarification":
        buttons.append(
            {
                "action": "chat.answer_clarification",
                "label": "回答问题",
                "style": "primary",
                "requires_confirmation": False,
            }
        )
    elif scenario == "failure_recovery":
        buttons.append(
            {
                "action": "turn.retry",
                "label": "重试",
                "style": "primary",
                "requires_confirmation": False,
            }
        )
    for option in follow_up_options[:3]:
        buttons.append(
            {
                "action": "chat.follow_up",
                "label": option,
                "style": "secondary",
                "requires_confirmation": False,
            }
        )
    return buttons


def _title_for_scenario(scenario: str) -> str | None:
    return {
        "clarification": "需要确认",
        "tool_boundary": "能力边界",
        "approval_required": "等待确认",
        "safety_deny": "安全边界",
        "failure_recovery": "可恢复失败",
        "task_created": "任务已创建",
        "task_completed": "任务完成",
        "memory_written": "记忆已更新",
        "memory_conflict": "记忆需要确认",
        "complex_dialogue": "方案",
    }.get(scenario)


def _sections_for_scenario(scenario: str, text: str) -> list[dict[str, Any]]:
    kind = {
        "clarification": "clarification",
        "tool_boundary": "boundary",
        "approval_required": "approval",
        "safety_deny": "safety_notice",
        "failure_recovery": "recovery",
        "complex_dialogue": "summary",
    }.get(scenario, "summary")
    return [{"kind": kind, "text": text}]


def _tone_metadata(request: ComposeRequest) -> dict[str, Any]:
    heart = request.heart or {}
    persona = request.persona or {}
    high_risk = _is_high_risk(request)
    return {
        "scenario": request.scenario,
        "route_profile": request.route_profile,
        "persona_mode": persona.get("mode") or persona.get("default_mode") or "default",
        "tone_hints": persona.get("tone_hints", []),
        "disclosure_hints": persona.get("disclosure_hints", []),
        "heart_mood": heart.get("mood"),
        "heart_urgency": heart.get("urgency"),
        "preferred_pace": heart.get("preferred_pace"),
        "deescalation_required": bool(
            high_risk or heart.get("deescalation_required") or heart.get("risk_tone_override")
        ),
        "risk_tone": "clear_and_calm" if high_risk else heart.get("risk_tone_override"),
        "safety_overrides_tone": True,
        "anthropomorphic_level": 0.1 if high_risk else 0.35,
    }


def _default_tone_metadata(*, scenario: str, high_risk: bool) -> dict[str, Any]:
    return {
        "scenario": scenario,
        "deescalation_required": high_risk,
        "risk_tone": "clear_and_calm" if high_risk else None,
        "safety_overrides_tone": True,
        "anthropomorphic_level": 0.1 if high_risk else 0.35,
    }


def _tone_mode_from_metadata(tone_metadata: dict[str, Any]) -> str:
    scenario = str(tone_metadata.get("scenario") or "")
    if tone_metadata.get("deescalation_required") or scenario in {
        "approval_required",
        "safety_deny",
        "tool_boundary",
    }:
        return "safety_boundary"
    if scenario in {"failure", "failure_recovery"}:
        return "failure_recovery"
    return str(tone_metadata.get("persona_mode") or "default")


def _baseline_quality_markers(*, scenario: str, high_risk: bool) -> dict[str, Any]:
    return {
        "directness": True,
        "boundary_honesty": True,
        "failure_recoverability": True,
        "heart_appropriateness": not high_risk
        or scenario in {"approval_required", "safety_deny", "tool_boundary"},
        "no_leakage": True,
    }


def _deescalation_notice(tone_metadata: dict[str, Any]) -> str | None:
    if not tone_metadata.get("deescalation_required"):
        return None
    return "我会保持克制和清楚，先确认边界再继续。"


def _first_next_step(options: list[str]) -> str | None:
    for option in options:
        if option.strip():
            return option
    return None


def _is_high_risk(request: ComposeRequest) -> bool:
    risk_level = request.risk_level or ""
    if risk_level in {"R5", "R6", "R7"}:
        return True
    scenario = request.scenario or ""
    if scenario in {"approval_required", "safety_deny", "tool_boundary"}:
        return True
    text = f"{request.user_text}\n{request.result_summary}".lower()
    return any(
        marker in text
        for marker in [
            "删除",
            "转账",
            "支付",
            "签名",
            "购买",
            "发帖",
            "delete",
            "transfer",
            "payment",
            "sign",
            "post",
        ]
    )


def _structured_notices(notices: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in notices.items()
        if key
        in {
            "safety_notice",
            "memory_notice",
            "tool_notice",
            "approval_prompt",
            "follow_up_options",
        }
    }


def _scenario_for_status(
    *,
    task_status: dict[str, Any] | None,
    approval_prompt: dict[str, Any] | None,
    safety_notice: str | None,
    memory_notice: str | None,
    tool_notice: str | None,
) -> str:
    if approval_prompt:
        return "approval_required"
    if safety_notice:
        return "safety_deny"
    if task_status:
        status = str(task_status.get("status") or "")
        return "task_completed" if status == "completed" else "task_status"
    if memory_notice:
        return "memory_written"
    if tool_notice:
        return "tool_boundary"
    return "direct"


def _longest_suffix_prefix(text: str, marker: str) -> int:
    lower_text = text.lower()
    lower_marker = marker.lower()
    max_length = min(len(lower_text), len(lower_marker) - 1)
    for length in range(max_length, 0, -1):
        if lower_marker.startswith(lower_text[-length:]):
            return length
    return 0


def _is_prefix_of_marker(text: str, marker: str) -> bool:
    return bool(text) and marker.lower().startswith(text.lower())
