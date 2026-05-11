from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core_types import ChatEventType, ErrorCode
from app.core.errors import AppError
from response_composer.opening_copy import opening_copy
from app.services.chat_visible_guard import VISIBLE_GUARD_VERSION, visible_text_guard

_INTERNAL_ID_PATTERNS = {
    "trace_ref": re.compile(r"\b(?:trc|trace)_[A-Za-z0-9_-]+\b", re.IGNORECASE),
    "approval_ref": re.compile(r"\b(?:apr|approval)_[A-Za-z0-9_-]+\b", re.IGNORECASE),
    "task_ref": re.compile(r"\b(?:tsk|task)_[A-Za-z0-9_-]+\b", re.IGNORECASE),
    "turn_ref": re.compile(r"\bturn_[A-Za-z0-9_-]+\b", re.IGNORECASE),
    "message_ref": re.compile(r"\bmsg_[A-Za-z0-9_-]+\b", re.IGNORECASE),
    "tool_ref": re.compile(r"\b(?:toolcall|tool_call|call)_[A-Za-z0-9_-]+\b", re.IGNORECASE),
}

_JARGON_REPLACEMENTS = {
    "approval_id": "确认编号",
    "tool_call_id": "工具记录",
    "trace_id": "审计记录",
    "task_id": "任务记录",
    "turn_id": "对话记录",
    "message_id": "消息记录",
    "browser.download": "下载动作",
    "browser.screenshot": "页面截图",
    "browser.snapshot": "网页快照",
    "R3": "需要确认的风险",
    "R4": "较高风险",
    "R5": "高风险",
}

_SAFETY_PHRASE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"(不(?:会|能|可以|应该)|无法|不能)在后台偷偷"),
        r"\1在后台未经你确认",
        "negated_background_stealth",
    ),
    (
        re.compile(r"(不(?:会|能|可以|应该)|无法|不能)[“\"']?偷偷[”\"']?"),
        r"\1未经你确认",
        "negated_stealth",
    ),
    (
        re.compile(r"后台偷偷"),
        "后台未经你确认",
        "quoted_background_stealth",
    ),
    (
        re.compile(r"不能可靠告诉你?"),
        "无法确认",
        "realtime_uncertainty",
    ),
)

_INTERNAL_TOOL_LEAK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"<\s*/?\s*minimax:tool_call\b[^>]*>", re.IGNORECASE), "model_tool_xml"),
    (re.compile(r"<\s*/?\s*invoke\b[^>]*>", re.IGNORECASE), "model_tool_xml"),
    (re.compile(r"<\s*/?\s*tool_call\b[^>]*>", re.IGNORECASE), "model_tool_xml"),
    (
        re.compile(
            r"```(?:json|python|javascript|ts|typescript)?\s*"
            r"<\s*(?:invoke|minimax:tool_call|tool_call)\b.*?```",
            re.IGNORECASE | re.DOTALL,
        ),
        "model_tool_code_fence",
    ),
)

def _task_status_copy(key: str, *, seed: str = "", **values: Any) -> str:
    return opening_copy(f"task.{key}", seed or key, **values)


@dataclass
class ChatVisibleOutputFilter:
    """Filters user-visible chat deltas before SSE/event persistence."""

    tail_window: int = 256
    input_chars: int = 0
    output_chars: int = 0
    changed_count: int = 0
    blocked_terms: set[str] = field(default_factory=set)
    _buffer: str = ""
    _seen_browser_snapshot: bool = False
    _seen_browser_screenshot: bool = False
    _seen_browser_evidence: bool = False
    _seen_browser_artifact: bool = False
    _seen_browser_selector: bool = False
    _strict_format_output: bool = False
    _last_visible_text: str = ""

    def feed(self, text: str) -> str:
        if not text:
            return ""
        self.input_chars += len(text)
        self._buffer += text
        if len(self._buffer) <= self.tail_window:
            return ""
        visible = self._buffer[:-self.tail_window]
        self._buffer = self._buffer[-self.tail_window :]
        return self._filter_visible(visible)

    def finish(self) -> str:
        visible = self._buffer
        self._buffer = ""
        filtered = self._filter_visible(visible)
        appendix = self._browser_evidence_appendix()
        if appendix:
            filtered += appendix
            self.output_chars += len(appendix)
            self.changed_count += 1
            self.blocked_terms.add("browser_selector_evidence_hint")
            self._seen_browser_selector = True
        return filtered

    def summary(self, *, visible_text: str | None = None) -> dict[str, Any]:
        normalized_visible = visible_text
        if normalized_visible is None:
            normalized_visible = self._last_visible_text
        return {
            "component": "ChatVisibleOutputFilter",
            "version": VISIBLE_GUARD_VERSION,
            "input_chars": self.input_chars,
            "output_chars": self.output_chars,
            "changed_count": self.changed_count,
            "blocked_terms": sorted(self.blocked_terms),
            "visible_text": normalized_visible or "",
            "filtered_segments": [
                {"reason": reason, "suppressed": True}
                for reason in sorted(self.blocked_terms)
            ],
            "suppression_reason_codes": sorted(self.blocked_terms),
            "stream_safe": True,
            "final_from_filtered_delta": True,
        }

    @classmethod
    def filter_text(cls, text: str) -> tuple[str, dict[str, Any]]:
        filter_ = cls(tail_window=0)
        filter_.input_chars = len(text or "")
        filtered = filter_._filter_visible(text)
        appendix = filter_._browser_evidence_appendix()
        if appendix:
            filtered += appendix
            filter_.output_chars += len(appendix)
            filter_.changed_count += 1
            filter_.blocked_terms.add("browser_selector_evidence_hint")
            filter_._seen_browser_selector = True
        filter_._last_visible_text = filtered
        return filtered, filter_.summary(visible_text=filtered)

    def _filter_visible(self, text: str) -> str:
        if not text:
            return ""
        original = text
        filtered = visible_text_guard(text)
        for pattern, label in _INTERNAL_TOOL_LEAK_PATTERNS:
            if pattern.search(filtered):
                self.blocked_terms.add(label)
                filtered = pattern.sub("", filtered)
        for pattern, replacement, label in _SAFETY_PHRASE_REPLACEMENTS:
            if pattern.search(filtered):
                self.blocked_terms.add(label)
                filtered = pattern.sub(replacement, filtered)
        for term, replacement in _JARGON_REPLACEMENTS.items():
            if re.search(re.escape(term), filtered, flags=re.IGNORECASE):
                self.blocked_terms.add(term)
                filtered = re.sub(re.escape(term), replacement, filtered, flags=re.IGNORECASE)
        for category, pattern in _INTERNAL_ID_PATTERNS.items():
            if pattern.search(filtered):
                self.blocked_terms.add(category)
                filtered = pattern.sub(_replacement_for_internal_ref(category), filtered)
        if _looks_like_strict_format(filtered):
            self._strict_format_output = True
        self._update_browser_evidence_flags(filtered)
        if filtered != original:
            self.changed_count += 1
        self.output_chars += len(filtered)
        self._last_visible_text = filtered
        return filtered

    def _update_browser_evidence_flags(self, text: str) -> None:
        lowered = text.lower()
        if "browser.snapshot" in lowered or "snapshot" in lowered or "网页快照" in text:
            self._seen_browser_snapshot = True
        if "browser.screenshot" in lowered or "screenshot" in lowered or "页面截图" in text:
            self._seen_browser_screenshot = True
        if "evidence" in lowered or "证据" in text:
            self._seen_browser_evidence = True
        if "artifact" in lowered or "工件" in text:
            self._seen_browser_artifact = True
        if "selector" in lowered or "选择器" in text:
            self._seen_browser_selector = True

    def _browser_evidence_appendix(self) -> str:
        if self._strict_format_output:
            return ""
        if (
            self._seen_browser_snapshot
            and self._seen_browser_screenshot
            and (self._seen_browser_evidence or self._seen_browser_artifact)
            and not self._seen_browser_selector
        ):
            return (
                "\n\n补充：selector 应记录元素定位依据，"
                "便于把网页快照中的按钮、输入框和链接映射到可复核的操作证据。"
            )
        return ""


def _looks_like_strict_format(text: str) -> bool:
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


class ChatTurnAccessPolicy:
    def assert_can_write(
        self,
        *,
        member: dict[str, Any],
        conversation: dict[str, Any],
    ) -> None:
        if member.get("organization_id") != conversation.get("organization_id"):
            raise _conversation_not_found()
        member_id = str(member.get("member_id") or "")
        participant_ids = {
            str(item.get("id") or item.get("member_id") or "")
            for item in conversation.get("participants", [])
            if isinstance(item, dict)
            and str(item.get("type") or item.get("participant_type") or "member")
            in {"member", "host", "participant"}
        }
        primary_member_id = str(conversation.get("primary_member_id") or "")
        if member_id != primary_member_id and member_id not in participant_ids:
            raise _conversation_not_found()


@dataclass(frozen=True)
class TaskStatusPresentation:
    text: str
    task_status: dict[str, Any]
    event_type: ChatEventType | None
    event_payload: dict[str, Any]
    safety_notice: str | None = None
    tool_notice: str | None = None


class ChatTaskStatusPresenter:
    def present(self, task: Any) -> TaskStatusPresentation:
        status = _task_value(getattr(task, "status", None))
        mode = _task_value(getattr(task, "mode", None))
        title = str(getattr(task, "title", "") or "任务")
        task_id = str(getattr(task, "task_id", "") or "")
        base_status = {
            "task_id": task_id,
            "status": status,
            "mode": mode,
            "title": title,
            "visible_state": _visible_state(status),
            "completed": status == "completed",
            "false_completion_guard": status != "completed",
        }
        payload = {"task_id": task_id, "status": status}
        if status == "completed":
            return TaskStatusPresentation(
                text=_task_status_copy("completed", seed=f"{status}|{title}", title=title),
                task_status={
                    **base_status,
                    "user_visible_text": "completed",
                    "evidence_requirements": [
                        "task_replay",
                        "step_records",
                        "artifact_or_page_state",
                    ],
                },
                event_type=ChatEventType.TASK_COMPLETED,
                event_payload=payload,
            )
        if status == "waiting_approval":
            return TaskStatusPresentation(
                text=_task_status_copy("waiting_approval", seed=f"{status}|{title}", title=title),
                task_status={**base_status, "user_visible_text": "waiting_approval"},
                event_type=None,
                event_payload=payload,
                safety_notice="这一步还等你点头，确认前不会往前执行。",
            )
        if status == "failed":
            return TaskStatusPresentation(
                text=_task_status_copy("failed", seed=f"{status}|{title}", title=title),
                task_status={**base_status, "user_visible_text": "failed"},
                event_type=ChatEventType.TASK_FAILED,
                event_payload=payload,
                tool_notice="这轮没有跑完，我不会把失败标成完成。",
            )
        if status == "paused":
            return TaskStatusPresentation(
                text=_task_status_copy("paused", seed=f"{status}|{title}", title=title),
                task_status={**base_status, "user_visible_text": "paused"},
                event_type=ChatEventType.TASK_PAUSED,
                event_payload=payload,
            )
        if status == "cancelled":
            return TaskStatusPresentation(
                text=_task_status_copy("cancelled", seed=f"{status}|{title}", title=title),
                task_status={**base_status, "user_visible_text": "cancelled"},
                event_type=ChatEventType.TASK_CANCELLED_EVENT,
                event_payload=payload,
            )
        if status == "running":
            return TaskStatusPresentation(
                text=_task_status_copy("running", seed=f"{status}|{title}", title=title),
                task_status={**base_status, "user_visible_text": "running"},
                event_type=ChatEventType.TASK_STARTED,
                event_payload=payload,
            )
        return TaskStatusPresentation(
            text=_task_status_copy(
                "default",
                seed=f"{status}|{title}",
                title=title,
                state=_visible_state(status),
            ),
            task_status={**base_status, "user_visible_text": "not_completed"},
            event_type=None,
            event_payload=payload,
        )


def response_filter_payload(summary: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(summary or {})
    payload.setdefault("component", "ChatVisibleOutputFilter")
    payload.setdefault("version", VISIBLE_GUARD_VERSION)
    payload.setdefault("visible_text", "")
    payload.setdefault("filtered_segments", [])
    payload.setdefault("suppression_reason_codes", [])
    payload.setdefault("stream_safe", True)
    payload.setdefault("final_from_filtered_delta", True)
    return payload


def context_redaction_summary(
    context: Any,
    *,
    sensitivity_hits: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    messages = list(getattr(getattr(context, "conversation", None), "last_messages", []) or [])
    redacted_count = 0
    for item in messages:
        if isinstance(item, dict):
            summary = item.get("redaction_summary") or {}
            if summary.get("applied"):
                redacted_count += 1
    return {
        "selected_count": len(messages),
        "redacted_count": redacted_count,
        "sensitivity_hits_summary": {
            "count": len(sensitivity_hits),
            "categories": sorted(set(str(item) for item in sensitivity_hits)),
        },
        "model_safe_fields": ["model_safe_content_text", "recent_summary", "memory_summary"],
        "raw_content_text_used_for_model": False,
    }


def planner_privacy_context(
    *,
    privacy_level: str,
    allow_cloud: bool,
    sensitivity_hits: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    return {
        "privacy_level": privacy_level,
        "allow_cloud": bool(allow_cloud) and privacy_level != "high",
        "sensitivity_hits_summary": {
            "count": len(sensitivity_hits),
            "categories": sorted(set(str(item) for item in sensitivity_hits)),
        },
        "cloud_planner_allowed": bool(allow_cloud) and privacy_level != "high",
    }


def _replacement_for_internal_ref(category: str) -> str:
    return {
        "trace_ref": "审计记录",
        "approval_ref": "确认编号",
        "task_ref": "任务记录",
        "turn_ref": "对话记录",
        "message_ref": "消息记录",
        "tool_ref": "工具记录",
    }.get(category, "内部记录")


def _task_value(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(getattr(value, "value", value))


def _visible_state(status: str) -> str:
    return {
        "created": "已创建",
        "draft": "草稿",
        "planning": "规划中",
        "participant_selecting": "选择参与者",
        "planned": "已规划",
        "preflight_failed": "预检查失败",
        "running": "运行中",
        "waiting_approval": "等待确认",
        "paused": "暂停",
        "synthesizing": "整理结果",
        "completed": "完成",
        "failed": "失败",
        "cancelled": "取消",
        "archived": "归档",
    }.get(status, status or "未知")


def _conversation_not_found() -> AppError:
    return AppError(
        ErrorCode.NOT_FOUND,
        "会话不存在",
        status_code=404,
        details={"access_policy": "conversation_member_scope"},
    )
