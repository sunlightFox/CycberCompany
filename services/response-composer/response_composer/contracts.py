from __future__ import annotations

import hashlib
import re
from typing import Any

from core_types import ApiModel, ErrorCode, ResponsePlan
from core_types.voice_copy import pick_variant
from pydantic import Field
from response_composer.chat_voice import voice_metadata_for_scenario
from response_composer.opening_copy import (
    apply_conversation_voice,
    conversation_voice_strategy,
    opening_copy,
    strip_mechanical_openers,
)

_REASONING_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_REASONING_OPEN_RE = re.compile(r"<think\b[^>]*>.*", re.IGNORECASE | re.DOTALL)
_REASONING_START = "<think"
_REASONING_END = "</think>"
_FACE_EMOJI_RE = re.compile(r"[\U0001f600-\U0001f64f]")
_READING_MARKERS = ("📘", "📌", "§", "▸", "🧠", "✨", "⚡", "🎯", "🧩", "📝", "🔍", "📎", "💡", "🛠️", "✍️")
_MAX_WECHAT_READING_MARKERS = 4
RESPONSE_QUALITY_GUARD_VERSION = "response_quality_guard.openclaw_hermes.v4"
_VISIBLE_INTERNAL_TERMS = (
    "trace_id",
    "approval_id",
    "tool_call_id",
    "task_id",
    "turn_id",
    "message_id",
    "model_safe_text",
    "prompt_snapshot_id",
)
_VISIBLE_FALSE_DONE_TERMS = (
    "已经执行",
    "已执行",
    "执行完成",
    "已经完成操作",
    "已经删除",
    "已删除",
    "已经安装",
    "已安装",
    "已经下载",
    "已下载",
    "已经提交",
    "已提交",
)
_VISIBLE_INTERNAL_LABELS = {
    "trace_id": "过程记录",
    "approval_id": "确认记录",
    "tool_call_id": "工具记录",
    "task_id": "任务记录",
    "turn_id": "对话记录",
    "message_id": "消息记录",
    "model_safe_text": "脱敏文本",
    "prompt_snapshot_id": "提示词快照",
}
_VISIBLE_INTERNAL_FIELD_RE = re.compile(
    r"\b("
    + "|".join(re.escape(term) for term in _VISIBLE_INTERNAL_TERMS)
    + r")\b\s*[:=]\s*[^\s，。；;,]+",
    re.IGNORECASE,
)
_VISIBLE_INTERNAL_BARE_RE = re.compile(
    r"\b(" + "|".join(re.escape(term) for term in _VISIBLE_INTERNAL_TERMS) + r")\b",
    re.IGNORECASE,
)
_READING_MARKER_HINTS = {
    "目标": "📘",
    "结论": "📘",
    "摘要": "📝",
    "总结": "📝",
    "步骤": "📌",
    "行动项": "📌",
    "计划": "📌",
    "风险": "§",
    "边界": "§",
    "审批": "§",
    "下一步": "▸",
    "建议": "▸",
    "取舍": "▸",
    "分析": "🧠",
    "原因": "🧠",
    "复盘": "🧠",
    "优化": "⚡",
    "提速": "⚡",
    "耗时": "⚡",
    "验证": "🔍",
    "检查": "🔍",
    "验收": "🔍",
    "工具": "🛠️",
    "落地": "🛠️",
}
class ComposeRequest(ApiModel):
    user_text: str = ""
    result_summary: str
    style: str = "result_first"
    scenario: str = "direct"
    persona: dict[str, Any] = Field(default_factory=dict)
    heart: dict[str, Any] = Field(default_factory=dict)
    risk_level: str | None = None
    route_profile: str | None = None
    channel_profile: str | None = None
    delivery_mode: str = "final"
    prompt_mode: str | None = None
    prompt_snapshot_id: str | None = None
    prompt_assembly_version: str | None = None
    stable_prompt_hash: str | None = None
    dynamic_context_hash: str | None = None
    trusted_context_hash: str | None = None
    untrusted_context_hash: str | None = None
    history_context_hash: str | None = None
    current_message_hash: str | None = None
    prompt_section_ids: list[str] = Field(default_factory=list)
    prompt_sections: list[dict[str, Any]] = Field(default_factory=list)
    presence_runtime: dict[str, Any] = Field(default_factory=dict)
    response_policy: dict[str, Any] = Field(default_factory=dict)
    session_context: dict[str, Any] = Field(default_factory=dict)
    action_dialogue: dict[str, Any] = Field(default_factory=dict)
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
        copy_seed = "|".join(
            [
                scenario,
                request.style or "",
                request.user_text or "",
                request.result_summary or "",
            ]
        )
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
            safety_notice = opening_copy("notice.high_risk_default", copy_seed)
        if scenario == "tool_boundary" and not tool_notice:
            tool_notice = opening_copy("notice.tool_boundary", copy_seed)
        redaction_summary = _merge_redaction_summaries(
            redaction_summary,
            safety_redactions,
            tool_redactions,
            approval_redactions,
            follow_up_redactions,
        )
        result_summary, conversation_voice = apply_conversation_voice(
            result_summary,
            seed=copy_seed,
            scenario=scenario,
            persona=request.persona,
            heart=request.heart,
            high_risk=bool(_is_high_risk(request)),
        )
        result_summary = _apply_channel_readability(
            result_summary,
            channel_profile=request.channel_profile or request.notices.get("channel_profile"),
            scenario=scenario,
            section_count=1,
        )
        response_quality_guard = _response_quality_guard(
            text=result_summary,
            original_text=raw_summary,
            scenario=scenario,
            user_text=request.user_text,
            redaction_summary=redaction_summary,
            high_risk=bool(_is_high_risk(request)),
            channel_profile=request.channel_profile or request.notices.get("channel_profile"),
            conversation_voice=conversation_voice,
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
                "conversation_voice": conversation_voice,
                "presence_runtime": _redact_payload(dict(request.presence_runtime or {})),
                "response_policy": _redact_payload(dict(request.response_policy or {})),
                "session_context": _redact_payload(dict(request.session_context or {})),
                "action_dialogue": _redact_payload(dict(request.action_dialogue or {})),
                **_voice_metadata_payload(
                    scenario=scenario,
                    channel_profile=request.channel_profile or request.notices.get("channel_profile"),
                    delivery_mode=request.delivery_mode,
                    prompt_mode=request.prompt_mode,
                    prompt_snapshot_id=request.prompt_snapshot_id,
                ),
                **_prompt_payload(request),
                "response_quality_guard": response_quality_guard,
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
                "voice_policy_version": plan.structured_payload.get("voice_policy_version"),
                "scenario_id": plan.structured_payload.get("scenario_id"),
                "channel_profile": plan.structured_payload.get("channel_profile"),
                "delivery_mode": plan.structured_payload.get("delivery_mode"),
                "prompt_snapshot_id": plan.structured_payload.get("prompt_snapshot_id"),
                "prompt_assembly_version": plan.structured_payload.get(
                    "prompt_assembly_version"
                ),
                "stable_prompt_hash": plan.structured_payload.get("stable_prompt_hash"),
                "dynamic_context_hash": plan.structured_payload.get("dynamic_context_hash"),
                "trusted_context_hash": plan.structured_payload.get("trusted_context_hash"),
                "untrusted_context_hash": plan.structured_payload.get("untrusted_context_hash"),
                "history_context_hash": plan.structured_payload.get("history_context_hash"),
                "current_message_hash": plan.structured_payload.get("current_message_hash"),
                "prompt_section_ids": plan.structured_payload.get("prompt_section_ids"),
                "prompt_sections": plan.structured_payload.get("prompt_sections"),
                "redacted": redaction_summary["applied"],
            },
        )

    def begin_delta_stream(self) -> ReasoningTagFilter:
        return ReasoningTagFilter()

    def compose_delta(self, text: str) -> str:
        return strip_reasoning_tags(text)

    def style_text(
        self,
        text: str,
        *,
        ui_mode: str | None = None,
        response_plan: ResponsePlan | None = None,
    ) -> str:
        visible, _ = redact_visible_text(strip_reasoning_tags(str(text or "")))
        if ui_mode == "wechat_chat":
            scenario = None
            section_count = 0
            if response_plan is not None:
                scenario = str(response_plan.structured_payload.get("scenario") or "")
                section_count = len(response_plan.sections or [])
            return _wechat_short_reply(
                visible,
                scenario=scenario,
                section_count=section_count,
            )
        return visible

    def compose_tool_unavailable(self) -> str:
        return opening_copy("notice.tool_boundary", "tool_unavailable")

    def compose_clarification(self, questions: list[str]) -> str:
        visible_questions = [item for item in questions[:3] if item]
        if not visible_questions:
            return "可以，我先按只读方式帮你看，不过我还差一点关键信息。"
        return "可以，我先按只读方式看重点，不过我还缺这几项信息：\n" + "\n".join(
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
        visible_summary, conversation_voice = apply_conversation_voice(
            visible_summary,
            seed=f"{scenario}|{visible_summary}",
            scenario=scenario,
            high_risk=bool(safety_notice or approval_prompt),
        )
        visible_summary = _apply_channel_readability(
            visible_summary,
            channel_profile=None,
            scenario=scenario,
            section_count=1,
        )
        tone_metadata = _default_tone_metadata(
            scenario=scenario,
            high_risk=bool(safety_notice or approval_prompt),
        )
        response_quality_guard = _response_quality_guard(
            text=visible_summary,
            original_text=summary,
            scenario=scenario,
            user_text="",
            redaction_summary=redaction_summary,
            high_risk=bool(safety_notice or approval_prompt),
            channel_profile=None,
            conversation_voice=conversation_voice,
            completion_evidence=task_status,
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
                "conversation_voice": conversation_voice,
                "task_status": task_status or {},
                "approval_prompt": approval_prompt or {},
                "artifact_refs": artifact_refs if isinstance(artifact_refs, list) else [],
                "safety_notice": safety_notice,
                "memory_notice": memory_notice,
                "tool_notice": tool_notice,
                **_voice_metadata_payload(scenario=scenario),
                "response_quality_guard": response_quality_guard,
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

    def response_plan_for_action_status(
        self,
        *,
        facts: dict[str, Any],
        task_status: dict[str, Any] | None = None,
        trace_refs: list[dict[str, Any]] | None = None,
    ) -> ResponsePlan:
        text = _compose_action_status_text(facts)
        visible_summary, redaction_summary = redact_visible_text(text)
        reply_options = [
            str(item)
            for item in facts.get("reply_options") or []
            if str(item).strip()
        ]
        reply_option_items = [
            item
            for item in facts.get("reply_option_items") or []
            if isinstance(item, dict)
        ]
        status = str(facts.get("status") or "pending_action")
        high_risk = bool(
            facts.get("approval_required") or facts.get("risk_level") in {"R5", "R6", "R7"}
        )
        visible_summary, conversation_voice = apply_conversation_voice(
            visible_summary,
            seed=f"action_status|{status}|{visible_summary}",
            scenario="action_status",
            high_risk=high_risk,
        )
        visible_summary = _apply_channel_readability(
            visible_summary,
            channel_profile=None,
            scenario="action_status",
            section_count=1,
        )
        tone_metadata = _default_tone_metadata(
            scenario="action_status",
            high_risk=high_risk,
        )
        response_quality_guard = _response_quality_guard(
            text=visible_summary,
            original_text=text,
            scenario="action_status",
            user_text="",
            redaction_summary=redaction_summary,
            high_risk=high_risk,
            channel_profile=None,
            conversation_voice=conversation_voice,
            completion_evidence=task_status or facts,
        )
        action_buttons = _action_buttons(
            scenario="approval_required" if facts.get("approval_required") else "direct",
            approval_prompt={"status": "required"} if facts.get("approval_required") else None,
            follow_up_options=reply_options,
        )
        return ResponsePlan(
            title=_action_status_title(status),
            style="natural_action",
            sections=[{"kind": "natural_interaction", "text": visible_summary}],
            action_buttons=action_buttons,
            summary=visible_summary,
            task_status=task_status,
            follow_up_options=reply_options,
            tone_metadata=tone_metadata,
            redaction_summary=redaction_summary,
            trace_refs=trace_refs or [],
            plain_text=visible_summary,
            structured_payload={
                "source": "response_composer",
                "scenario": "action_status",
                "conversation_voice": conversation_voice,
                **_voice_metadata_payload(scenario="action_status"),
                "action_status": _redact_payload(facts),
                "reply_option_items": _redact_payload(reply_option_items),
                "task_status": task_status or {},
                "response_quality_guard": response_quality_guard,
            },
            tone_mode=_tone_mode_from_metadata(tone_metadata),
            quality_markers={
                **_baseline_quality_markers(
                    scenario="approval_required" if high_risk else "direct",
                    high_risk=high_risk,
                ),
                "natural_language": True,
                "no_false_done": True,
            },
            boundary_notice=_action_boundary_notice(facts) if high_risk else None,
            user_next_step=_first_next_step(reply_options),
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
                    **base_plan.structured_payload,
                    "scenario": "clarification",
                    **_voice_metadata_payload(scenario="clarification"),
                    "clarification_decision": decision,
                    "response_quality_guard": _response_quality_guard(
                        text=visible_summary,
                        original_text=summary,
                        scenario="clarification",
                        user_text="",
                        redaction_summary=base_plan.redaction_summary,
                        high_risk=bool(decision.get("blocker_level") == "high"),
                        channel_profile=None,
                        conversation_voice=base_plan.structured_payload.get(
                            "conversation_voice"
                        ),
                    ),
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
        seed = f"{summary}|{required_capability}|{','.join(next_actions)}"
        base_plan = self.response_plan_for_status(
            summary=summary,
            safety_notice=safety_notice,
            tool_notice=opening_copy("notice.tool_boundary", seed),
        )
        return base_plan.model_copy(
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
                    **base_plan.structured_payload,
                    "scenario": "tool_boundary",
                    **_voice_metadata_payload(scenario="tool_boundary"),
                    "required_capability": required_capability,
                    "next_actions": next_actions,
                    "safety_notice": safety_notice,
                    "tool_notice": opening_copy("notice.tool_boundary", seed),
                    "response_quality_guard": _response_quality_guard(
                        text=base_plan.plain_text or base_plan.summary or summary,
                        original_text=summary,
                        scenario="tool_boundary",
                        user_text="",
                        redaction_summary=base_plan.redaction_summary,
                        high_risk=bool(safety_notice),
                        channel_profile=None,
                        conversation_voice=base_plan.structured_payload.get(
                            "conversation_voice"
                        ),
                    ),
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
        recovery: dict[str, Any] | None = None,
    ) -> ResponsePlan:
        plan = base_plan or self.response_plan_for_status(summary=summary)
        recovery_payload = recovery or {
            "status": "needs_user_input" if recoverable else "unrecoverable",
            "attempt_count": 0,
            "root_cause": error_code,
            "actions_taken": [],
            "next_action": suggested_next_actions[0] if suggested_next_actions else None,
            "task_id": None,
        }
        structured = {
            **plan.structured_payload,
            "scenario": "failure_recovery",
            **_voice_metadata_payload(scenario="failure_recovery"),
            "error_code": error_code,
            "recoverable": recoverable,
            "suggested_next_actions": suggested_next_actions,
            "recovery": _redact_payload(recovery_payload),
            "response_quality_guard": _response_quality_guard(
                text=plan.plain_text or plan.summary or summary,
                original_text=summary,
                scenario="failure_recovery",
                user_text="",
                redaction_summary=plan.redaction_summary,
                high_risk=False,
                channel_profile=None,
                conversation_voice=plan.structured_payload.get("conversation_voice"),
            ),
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
        return opening_copy("notice.privacy_block", "privacy_block")

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
                    **_voice_metadata_payload(scenario="failure"),
                    "conversation_voice": {},
                    "status": "failed",
                    "error_code": code_value,
                    "safety_notice": safety_notice,
                    "response_quality_guard": _response_quality_guard(
                        text=message,
                        original_text=message,
                        scenario="failure",
                        user_text="",
                        redaction_summary={"applied": False, "categories": []},
                        high_risk=safety_notice is not None,
                        channel_profile=None,
                        conversation_voice={},
                    ),
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
    internal_redacted = _VISIBLE_INTERNAL_FIELD_RE.sub(
        lambda match: _VISIBLE_INTERNAL_LABELS.get(match.group(1).lower(), "内部记录"),
        redacted,
    )
    internal_redacted = _VISIBLE_INTERNAL_BARE_RE.sub(
        lambda match: _VISIBLE_INTERNAL_LABELS.get(match.group(1).lower(), "内部记录"),
        internal_redacted,
    )
    if internal_redacted != redacted:
        categories.append("internal_field")
        redacted = internal_redacted
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


def _apply_channel_readability(
    text: str,
    *,
    channel_profile: str | None,
    scenario: str | None,
    section_count: int,
) -> str:
    if str(channel_profile or "").lower() != "wechat_chat":
        return text
    return _wechat_short_reply(text, scenario=scenario, section_count=section_count)


def _response_quality_guard(
    *,
    text: str,
    original_text: str,
    scenario: str,
    user_text: str,
    redaction_summary: dict[str, Any],
    high_risk: bool,
    channel_profile: str | None,
    conversation_voice: dict[str, Any] | None,
    completion_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    visible = str(text or "")
    lowered = visible.lower()
    internal_terms = [
        term for term in _VISIBLE_INTERNAL_TERMS if term.lower() in lowered
    ]
    false_done_terms = _visible_false_done_terms(visible)
    if _has_completion_evidence(completion_evidence):
        false_done_terms = []
    strict_format_preserved = _strict_format_preserved(original_text, visible)
    boundary_required = high_risk or scenario in {
        "approval_required",
        "safety_deny",
        "tool_boundary",
        "privacy",
        "professional_advice",
    }
    boundary_honesty = (
        not boundary_required
        or any(marker in visible for marker in ["不会", "不能", "确认", "授权", "边界", "还没", "不"])
    )
    current_message_priority = _current_message_priority_ok(user_text, visible)
    mechanical_clean = visible_opening_is_clean(visible)
    wechat_readability = (
        str(channel_profile or "").lower() != "wechat_chat"
        or (
            _FACE_EMOJI_RE.search(visible) is None
            and (strict_format_preserved or not _strict_format_text_contract(visible))
        )
    )
    multimodal_grounded = _multimodal_grounded(user_text, visible, scenario=scenario)
    checks = {
        "no_internal_terms": not internal_terms,
        "no_false_done": not false_done_terms,
        "boundary_honesty": boundary_honesty,
        "privacy_redacted": bool(redaction_summary.get("applied"))
        or not _payload_redaction_summary(visible).get("applied"),
        "current_message_priority": current_message_priority,
        "evidence_required_before_done": not false_done_terms,
        "strict_format_preserved": strict_format_preserved,
        "no_mechanical_opening": mechanical_clean,
        "wechat_readability": wechat_readability,
        "multimodal_grounded": multimodal_grounded,
    }
    violations: list[dict[str, Any]] = []
    if internal_terms:
        violations.append({"check": "no_internal_terms", "terms": internal_terms})
    if false_done_terms:
        violations.append({"check": "no_false_done", "terms": false_done_terms})
    for check, passed in checks.items():
        if not passed and not any(item["check"] == check for item in violations):
            violations.append({"check": check})
    return {
        "version": RESPONSE_QUALITY_GUARD_VERSION,
        "status": "passed" if all(checks.values()) else "warning",
        "checks": checks,
        "violations": violations,
        "redaction_applied": bool(redaction_summary.get("applied")),
        "strict_format_preserved": strict_format_preserved,
        "visible_text_hash": _visible_text_hash(visible),
        "conversation_voice": {
            key: value
            for key, value in dict(conversation_voice or {}).items()
            if key
            in {
                "strategy_version",
                "scene",
                "warmth_level",
                "humor_level",
                "directness_level",
                "deescalated",
                "strict_format",
                "opener_policy",
            }
        },
    }


def visible_opening_is_clean(text: str) -> bool:
    return strip_mechanical_openers(text) == str(text or "").strip()


def _visible_text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _has_completion_evidence(evidence: dict[str, Any] | None) -> bool:
    if not isinstance(evidence, dict):
        return False
    status = str(evidence.get("status") or evidence.get("detail_status") or "")
    return bool(evidence.get("completed")) or status == "completed"


def _current_message_priority_ok(user_text: str, visible: str) -> bool:
    user = str(user_text or "")
    visible_text = str(visible or "")
    if not user:
        return True
    if not any(
        marker in user
        for marker in ["停", "停止", "改成", "换成", "只做", "不要执行", "只讨论", "不讨论", "只回答这句", "按我最新这句"]
    ):
        return True
    target = _topic_switch_target(user)
    if target and target in visible_text:
        return True
    return any(marker in visible_text for marker in ["当前", "新的", "改成", "先停", "前一个", "只做", "为准", "切到", "只讨论", "按你最新这句"])


def _visible_false_done_terms(text: str) -> list[str]:
    visible = str(text or "")
    terms: list[str] = []
    for term in _VISIBLE_FALSE_DONE_TERMS:
        start = 0
        while True:
            index = visible.find(term, start)
            if index < 0:
                break
            context = visible[max(0, index - 8) : min(len(visible), index + len(term) + 8)]
            if not any(marker in context for marker in ["不该", "不要", "不能", "别", "别把", "假装", "说成"]):
                terms.append(term)
                break
            start = index + len(term)
    return terms


def _topic_switch_target(user_text: str) -> str:
    user = str(user_text or "")
    patterns = [
        r"(?:改成|换成|只讨论|不讨论|只回答这句)([^，。；\n]+)",
        r"(?:按我最新这句)([^，。；\n]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, user)
        if match:
            return match.group(1).strip(" ：:")
    return ""


def _multimodal_grounded(user_text: str, visible: str, *, scenario: str) -> bool:
    source = f"{user_text}\n{visible}"
    if scenario != "multimodal" and not any(marker in source for marker in ["图片", "图", "语音", "文件"]):
        return True
    generic_bad = [
        "收到图片",
        "收到语音",
        "收到文件",
        "我来处理",
        "继续处理",
    ]
    if any(marker in visible for marker in generic_bad) and not any(
        marker in visible
        for marker in ["看到", "听到", "读到", "识别", "转写", "摘录", "看不清", "听不全", "读不全"]
    ):
        return False
    return True


def _strict_format_preserved(original_text: str, visible: str) -> bool:
    if not _strict_format_text_contract(original_text):
        return True
    if any(marker in visible for marker in _READING_MARKERS):
        return False
    if _looks_like_json_only(original_text):
        return _looks_like_json_only(visible)
    if _looks_like_markdown_table(original_text):
        return _looks_like_markdown_table(visible)
    return _strict_format_text_contract(visible)


def _strict_format_text_contract(text: str) -> bool:
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


def _wechat_short_reply(
    text: str,
    *,
    scenario: str | None = None,
    section_count: int = 0,
) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw
    candidate = _strip_face_emoji(raw)
    candidate = strip_mechanical_openers(candidate)
    return _enrich_wechat_reading_markers(
        candidate or raw,
        scenario=scenario,
        section_count=section_count,
    )


def _wechat_marker_for_text(text: str, *, fallback_index: int = 0) -> str:
    stripped = text.strip()
    for keyword, marker in _READING_MARKER_HINTS.items():
        if keyword in stripped:
            return marker
    return _READING_MARKERS[fallback_index % len(_READING_MARKERS)]


def _strip_face_emoji(text: str) -> str:
    stripped = _FACE_EMOJI_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", stripped).strip()


def _enrich_wechat_reading_markers(
    text: str,
    *,
    scenario: str | None = None,
    section_count: int = 0,
) -> str:
    if not _should_enrich_wechat_reading_markers(
        text,
        scenario=scenario,
        section_count=section_count,
    ):
        return text

    lines = text.splitlines()
    changed = False
    marker_index = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        section = re.match(
            r"^(目标|步骤|风险|建议|结论|下一步|验收|问题|行动项|完成|计划|取舍|边界|"
            r"分析|原因|复盘|优化|提速|耗时|验证|检查|总结|工具|落地)"
            r"([：:])(?:\s*(.+))?$",
            stripped,
        )
        if section and marker_index < _MAX_WECHAT_READING_MARKERS:
            marker = _wechat_marker_for_text(section.group(1), fallback_index=marker_index)
            body = section.group(3)
            if body:
                lines[index] = f"{marker} {section.group(1)}{section.group(2)}{body}"
            else:
                lines[index] = f"{marker} {section.group(1)}{section.group(2)}"
            marker_index += 1
            changed = True
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading and marker_index < _MAX_WECHAT_READING_MARKERS:
            marker = _wechat_marker_for_text(heading.group(2), fallback_index=marker_index)
            lines[index] = f"{marker} {heading.group(2).strip()}"
            marker_index += 1
            changed = True
            continue
        if re.match(r"^[-*]\s+\S+", stripped) and marker_index < _MAX_WECHAT_READING_MARKERS:
            marker = _wechat_marker_for_text(stripped, fallback_index=marker_index)
            lines[index] = re.sub(r"^(\s*)[-*]\s+", rf"\1{marker} ", line, count=1)
            marker_index += 1
            changed = True

    if changed:
        return "\n".join(lines)

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^(\d+[\.、]|[一二三四五六七八九十]+[、.])\s*\S+", stripped):
            lines[index] = f"{_wechat_marker_for_text(stripped, fallback_index=index)} {stripped}"
            return "\n".join(lines)
        if len(stripped) <= 36 and re.search(r"(目标|步骤|风险|建议|结论|下一步|验收|说明)[:：]?$", stripped):
            lines[index] = f"{_wechat_marker_for_text(stripped, fallback_index=index)} {stripped}"
            return "\n".join(lines)
    return text


def _should_enrich_wechat_reading_markers(
    text: str,
    *,
    scenario: str | None = None,
    section_count: int = 0,
) -> bool:
    stripped = text.strip()
    section_heading_count = len(
        re.findall(
            r"(^|\n)\s*(目标|步骤|风险|建议|结论|下一步|验收|问题|行动项|完成|计划|取舍|边界|"
            r"分析|原因|复盘|优化|提速|耗时|验证|检查|总结|工具|落地)[：:]",
            stripped,
        )
    )
    if len(stripped) < 120 and not (len(stripped) >= 80 and section_heading_count >= 2):
        return False
    if section_count < 2 and not any(
        symbol in stripped for symbol in ("目标", "步骤", "风险", "建议", "验收", "下一步")
    ):
        return False
    if scenario in {"approval_required", "safety_deny", "tool_boundary", "failure", "failure_recovery"}:
        return False
    if any(marker in stripped for marker in _READING_MARKERS):
        return False
    if "```" in stripped or _looks_like_json_only(stripped) or _looks_like_markdown_table(stripped):
        return False
    if re.search(r"(只输出\s*JSON|不要\s*Markdown|纯文本|不要解释)", stripped, flags=re.I):
        return False
    return bool(
        re.search(r"(^|\n)#{1,3}\s+\S+", stripped)
        or re.search(r"(^|\n)\s*[-*]\s+\S+", stripped)
        or re.search(r"(^|\n)\s*\d+[\.、]\s+\S+", stripped)
        or any(
            word in stripped
            for word in [
                "目标",
                "步骤",
                "风险",
                "建议",
                "验收",
                "下一步",
                "结论",
                "总结",
                "分析",
                "复盘",
                "优化",
                "耗时",
                "检查",
                "验证",
                "落地",
            ]
        )
    )


def _looks_like_json_only(text: str) -> bool:
    stripped = text.strip()
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return False
    return "\n#" not in stripped


def _looks_like_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return any("|" in line for line in lines) and any(
        re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", line)
        for line in lines[:4]
    )


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
    voice_metadata = _voice_metadata_payload(
        scenario=request.scenario,
        channel_profile=request.channel_profile or request.notices.get("channel_profile"),
        delivery_mode=request.delivery_mode,
        prompt_mode=request.prompt_mode,
        prompt_snapshot_id=request.prompt_snapshot_id,
    )
    return {
        "scenario": request.scenario,
        "voice_policy_version": voice_metadata["voice_policy_version"],
        "scenario_id": voice_metadata["scenario_id"],
        "channel_profile": voice_metadata["channel_profile"],
        "delivery_mode": voice_metadata["delivery_mode"],
        "prompt_mode": request.prompt_mode,
        "prompt_snapshot_id": request.prompt_snapshot_id,
        "prompt_assembly_version": request.prompt_assembly_version,
        "stable_prompt_hash": request.stable_prompt_hash,
        "dynamic_context_hash": request.dynamic_context_hash,
        "trusted_context_hash": request.trusted_context_hash,
        "untrusted_context_hash": request.untrusted_context_hash,
        "history_context_hash": request.history_context_hash,
        "current_message_hash": request.current_message_hash,
        "prompt_section_ids": request.prompt_section_ids,
        "prompt_sections": request.prompt_sections,
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


def _voice_metadata_payload(
    *,
    scenario: str | None,
    channel_profile: str | None = None,
    delivery_mode: str | None = None,
    prompt_mode: str | None = None,
    prompt_snapshot_id: str | None = None,
) -> dict[str, Any]:
    return voice_metadata_for_scenario(
        scenario,
        channel_profile=channel_profile,
        delivery_mode=delivery_mode,
        prompt_mode=prompt_mode,
        prompt_snapshot_id=prompt_snapshot_id,
    )


def _prompt_payload(request: ComposeRequest) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if request.prompt_assembly_version:
        payload["prompt_assembly_version"] = request.prompt_assembly_version
    if request.stable_prompt_hash:
        payload["stable_prompt_hash"] = request.stable_prompt_hash
    if request.dynamic_context_hash:
        payload["dynamic_context_hash"] = request.dynamic_context_hash
    if request.trusted_context_hash:
        payload["trusted_context_hash"] = request.trusted_context_hash
    if request.untrusted_context_hash:
        payload["untrusted_context_hash"] = request.untrusted_context_hash
    if request.history_context_hash:
        payload["history_context_hash"] = request.history_context_hash
    if request.current_message_hash:
        payload["current_message_hash"] = request.current_message_hash
    if request.prompt_section_ids:
        payload["prompt_section_ids"] = list(request.prompt_section_ids)
    if request.prompt_sections:
        payload["prompt_sections"] = [dict(item) for item in request.prompt_sections]
    return payload


def _default_tone_metadata(*, scenario: str, high_risk: bool) -> dict[str, Any]:
    voice_metadata = _voice_metadata_payload(scenario=scenario)
    return {
        "scenario": scenario,
        "voice_policy_version": voice_metadata["voice_policy_version"],
        "scenario_id": voice_metadata["scenario_id"],
        "channel_profile": voice_metadata["channel_profile"],
        "delivery_mode": voice_metadata["delivery_mode"],
        "deescalation_required": high_risk,
        "risk_tone": "clear_and_calm" if high_risk else None,
        "safety_overrides_tone": True,
        "anthropomorphic_level": 0.1 if high_risk else 0.35,
    }


def _apply_runtime_response_policy(
    text: str,
    *,
    response_policy: dict[str, Any],
    session_context: dict[str, Any],
    action_dialogue: dict[str, Any],
    scenario: str,
    user_text: str,
) -> str:
    del response_policy, session_context, action_dialogue, scenario, user_text
    return str(text or "").strip()


def _split_sentences(text: str, *, max_sentences: int, multiline: bool) -> str:
    stripped = str(text or "").strip()
    if not stripped or "\n" in stripped:
        return stripped
    parts = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?])\s*", stripped)
        if item.strip()
    ]
    if len(parts) <= 1:
        return stripped
    selected = parts[:max_sentences]
    separator = "\n" if multiline else ""
    return separator.join(selected)


def _ensure_result_first(text: str, *, user_text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped:
        return stripped
    if any(marker in stripped[:12] for marker in ["结果", "我看完了", "我已经拿到", "我刚看了", "我查完了"]):
        return stripped
    if any(marker in str(user_text or "") for marker in ["网页", "命令", "目录", "文件"]):
        return f"我先说结果。{stripped}"
    return stripped


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
    return "我会先把话说清楚，等该确认的点确认完再往下走。"


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


_PENDING_REASON_ACTION_TYPES = frozenset(
    {
        "host.uninstall_software",
        "host.install_software",
        "browser.download",
        "file.delete",
    }
)
_PENDING_OPENER_ACTION_TYPES = frozenset(
    {
        "host.install_software",
        "host.uninstall_software",
        "file.delete",
        "browser.download",
    }
)


def _action_seed(facts: dict[str, Any]) -> str:
    return "|".join(
        [
            str(facts.get("status") or ""),
            str(facts.get("action_type") or ""),
            str(facts.get("action_label") or ""),
            str(facts.get("target") or ""),
            str(facts.get("failure_reason") or ""),
            str(facts.get("detail_status") or ""),
        ]
    )


def _compose_action_status_text(facts: dict[str, Any]) -> str:
    status = str(facts.get("status") or "pending_action")
    action_label = str(facts.get("action_label") or "这一步操作").strip()
    target = str(facts.get("target") or "").strip()
    label = (
        action_label
        if target and target in action_label
        else f"{action_label} {target}".strip()
    )
    seed = _action_seed(facts)
    if status == "already_absent" or facts.get("already_absent"):
        return pick_variant(
            seed,
            (
                f"我查了一圈，{target or label}不在本机安装清单里，这次不用处理卸载。",
                f"{target or label}现在不在这台机器上，所以没有卸载动作发生。",
                f"{target or label}本来就不在本机里，这次不用动它。",
            ),
        )
    if status in {"pending_action", "waiting_approval"}:
        opener = _pending_opener(facts)
        reason = _pending_reason_text(facts)
        options = [str(item) for item in facts.get("reply_options") or [] if str(item).strip()]
        prompt = opening_copy("action.pending", seed, label=label)
        lines = [prompt, reason]
        if options:
            lines.append("你直接回我：" + "、".join(options[:4]) + "。")
        lines.append(
            pick_variant(
                seed,
                (
                    "你确认前，我先不往前推，免得替你越线。",
                    "你没点头前，这一步我先收着，不提前往下走。",
                    "先等你一句准话，我再继续，不替你擅自做决定。",
                ),
            )
        )
        return "\n".join(line for line in lines if line)
    if status == "manual_only":
        reason = str(facts.get("failure_reason") or facts.get("safe_next_step") or "").strip()
        return opening_copy(
            "action.manual_only",
            seed,
            label=label,
            reason=reason or "现在还缺一个更稳的可信来源。",
        )
    if status == "blocked":
        reason = str(facts.get("failure_reason") or facts.get("safe_next_step") or "").strip()
        return opening_copy(
            "action.blocked",
            seed,
            label=label,
            reason=reason or "这一步先停住，我不能把它说成已经完成。",
        )
    if status == "approved":
        detail_status = str(facts.get("detail_status") or "")
        evidence = str(facts.get("evidence_summary") or "").strip()
        if detail_status == "completed" or facts.get("completed"):
            return opening_copy("action.approved_completed", seed, label=label) + _friendly_evidence_text(
                evidence,
                seed=seed,
            )
        if detail_status in {"paused", "waiting_approval"}:
            return opening_copy("action.approved_waiting", seed, label=label)
        if detail_status == "failed" or facts.get("failed"):
            reason = str(facts.get("failure_reason") or "").strip()
            return opening_copy(
                "action.approved_failed",
                seed,
                label=label,
                reason=reason or "你可以换个目标或来源，我再试一轮。",
            )
        return opening_copy("action.approved_progress", seed, label=label)
    if status == "denied":
        return opening_copy("action.denied", seed, label=label)
    if status == "edited":
        return opening_copy("action.edited", seed, label=label)
    if status == "edit_missing_target":
        reason = str(facts.get("failure_reason") or "").strip()
        return opening_copy("action.edit_missing_target", seed, label=label, reason=reason)
    if status == "resolution_failed":
        reason = str(facts.get("failure_reason") or "").strip()
        fallback = "你可以修改目标后重试，或取消这次操作。"
        return opening_copy("action.resolution_failed", seed, label=label, reason=reason or fallback)
    if status == "no_pending_action":
        return opening_copy("action.no_pending", seed, label=label)
    if status == "multiple_pending_actions":
        labels = str(facts.get("labels") or label)
        return opening_copy("action.multiple_pending", seed, labels=labels)
    if status == "ambiguous_confirmation_blocked":
        return opening_copy("action.ambiguous_blocked", seed, label=label)
    return opening_copy("action.default", seed, label=label, status=status)


def _pending_reason_text(facts: dict[str, Any]) -> str:
    action_type = str(facts.get("action_type") or "")
    seed = _action_seed(facts)
    if action_type in _PENDING_REASON_ACTION_TYPES:
        return opening_copy(f"action.pending_reason.{action_type}", seed)
    return _soften_action_text(
        str(facts.get("impact_summary") or "这一步有实际影响，需要你明确确认后才会继续。")
    )


def _pending_opener(facts: dict[str, Any]) -> str:
    action_type = str(facts.get("action_type") or "")
    seed = _action_seed(facts)
    if action_type in _PENDING_OPENER_ACTION_TYPES:
        return opening_copy(f"action.pending_opener.{action_type}", seed)
    return pick_variant(
        seed,
        (
            "我先把这步摆好：",
            "我先把这步收住：",
            "我先看住这一步：",
        ),
    )


def _friendly_evidence_text(evidence: str, *, seed: str = "") -> str:
    text = _soften_action_text(evidence).strip()
    if not text:
        return pick_variant(
            seed or "evidence",
            (
                "我也把过程记下来了，后面要查还能翻得到。",
                "过程记录我也留好了，回头能复核。",
                "我把记录也收好了，后面想查随时能翻。",
            ),
        )
    return text


def _soften_action_text(text: str) -> str:
    replacements = {
        "受控链路": "处理流程",
        "受控任务链路": "处理流程",
        "受控任务": "处理流程",
        "任务链路": "处理流程",
        "工具边界": "处理限制",
        "任务回放": "结果记录",
        "工件": "结果记录",
        "回放证据": "过程记录",
        "内部 trace": "过程记录",
        "Capability Graph": "权限范围",
        "Asset Broker": "授权资源通道",
        "Safety": "风险检查",
        "Approval": "确认",
        "本机软件状态": "电脑里的软件",
        "需要你明确确认后才会继续": "需要你点头后我才会继续",
        "确认前尚未安装": "确认前还没安装",
        "确认前尚未卸载": "确认前还没卸载",
        "确认前尚未下载": "确认前还没下载",
        "确认前尚未提交": "确认前还没提交",
        "确认前尚未保存": "确认前还没保存",
        "系统安全提示": "安全提示",
        "来源校验": "来源检查",
    }
    friendly_evidence = "我也把过程记下来了，后面要查还能翻得到。"
    replacements["结果可以通过任务记录、结果记录或过程记录复核。"] = friendly_evidence
    replacements["结果可以通过任务记录、工件或回放证据复核。"] = friendly_evidence
    result = text
    for old, new in replacements.items():
        result = result.replace(old, new)
    return result


def _action_status_title(status: str) -> str | None:
    if status == "already_absent":
        return "无需操作"
    if status in {"pending_action", "waiting_approval"}:
        return "等待确认"
    if status == "manual_only":
        return "需要人工处理"
    if status == "approved":
        return "已确认"
    if status == "denied":
        return "已取消"
    if status == "edited":
        return "已更新"
    if status == "edit_missing_target":
        return "需要新目标"
    if status == "resolution_failed":
        return "未完成"
    return None


def _action_boundary_notice(facts: dict[str, Any]) -> str | None:
    action_type = str(facts.get("action_type") or "")
    seed = _action_seed(facts)
    if action_type.startswith("host."):
        return opening_copy("action.boundary.host", seed)
    if facts.get("approval_required"):
        return opening_copy("action.boundary.approval", seed)
    return None


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
