from __future__ import annotations

import re
from contextvars import ContextVar, Token

from trace_service import redact

VISIBLE_GUARD_VERSION = "chat_visible_filter.openclaw_hermes.v4"

FORBIDDEN_MAIN_REPLY_TERMS = {
    "approval_id": "确认编号",
    "tool_call_id": "工具记录",
    "trace_id": "审计记录",
    "task_id": "任务记录",
    "turn_id": "对话记录",
    "message_id": "消息记录",
    "prompt_snapshot_id": "提示快照",
    "model_safe_text": "安全文本",
    "内部 trace": "过程记录",
    "browser.download": "下载动作",
    "browser.snapshot": "网页快照",
    "browser.screenshot": "页面截图",
    "Capability Graph": "权限范围",
    "Asset Broker": "授权资源通道",
    "Safety": "风险检查",
    "Approval": "确认",
    "R3": "需要确认的风险",
    "R4": "较高风险",
    "R5": "高风险",
    "/api/approvals": "确认接口",
}

_VISIBLE_REDACTION_PROFILE: ContextVar[str] = ContextVar(
    "chat_visible_redaction_profile",
    default="strict",
)
_RELAXED_SECRET_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}"), "[REDACTED_API_KEY]"),
    (
        re.compile(
            r"(?i)(api[_-]?key|token|secret|cookie|password|passwd|pwd)"
            r"\s*[:=]\s*['\"]?[^'\"\s,;]+"
        ),
        r"\1=[REDACTED_TOKEN]",
    ),
    (
        re.compile(
            r"(?i)([?&](?:api[_-]?key|token|secret|cookie|password|passwd|pwd)=)"
            r"[^&\s,;]+"
        ),
        r"\1[REDACTED_TOKEN]",
    ),
    (
        re.compile(r"(?i)(private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        r"\1=[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b", re.I),
        "[REDACTED_MNEMONIC]",
    ),
)
_RELAXED_SENSITIVE_LOCAL_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)(?:[A-Za-z]:\\Users\\[^\\\s]+|/(?:Users|home)/[^/\s]+)"
            r"(?:[\\/][^\s,;]*)?"
            r"(?:[\\/](?:\.ssh|\.gnupg|wallet|browser profiles?|secrets?)[\\/][^\s,;]*)"
        ),
        "[REDACTED_SENSITIVE_LOCAL_PATH]",
    ),
    (
        re.compile(
            r"(?i)(?:[A-Za-z]:\\Users\\[^\\\s]+|/(?:Users|home)/[^/\s]+)"
            r"(?:[\\/][^\s,;]*)?[\\/](?:\.env(?:\.local)?|id_rsa|id_ed25519|"
            r"master\.key|local_secrets\.json|cookies|login data)"
        ),
        "[REDACTED_SENSITIVE_LOCAL_PATH]",
    ),
)


def set_visible_redaction_profile(profile: str) -> Token[str]:
    return _VISIBLE_REDACTION_PROFILE.set(_normalize_visible_profile(profile))


def reset_visible_redaction_profile(token: Token[str]) -> None:
    _VISIBLE_REDACTION_PROFILE.reset(token)


def visible_text_guard(text: str, *, profile: str | None = None) -> str:
    active_profile = _normalize_visible_profile(profile or _VISIBLE_REDACTION_PROFILE.get())
    result = (
        _relaxed_visible_redact(str(text))
        if active_profile == "relaxed"
        else str(redact(text))
    )
    for term, replacement in FORBIDDEN_MAIN_REPLY_TERMS.items():
        result = re.sub(re.escape(term), replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"\btrc_[A-Za-z0-9_-]+", "审计记录", result)
    result = re.sub(r"\bapr_[A-Za-z0-9_-]+", "确认编号", result)
    result = re.sub(r"\b(?:toolcall|tool_call|call)_[A-Za-z0-9_-]+", "工具记录", result)
    result = re.sub(r"\b(?:tsk|task)_[A-Za-z0-9_-]+", "任务记录", result)
    return result


def _normalize_visible_profile(profile: str) -> str:
    return "relaxed" if str(profile or "").lower() == "relaxed" else "strict"


def _relaxed_visible_redact(text: str) -> str:
    result = text
    for pattern, replacement in _RELAXED_SECRET_TEXT_PATTERNS:
        result = pattern.sub(replacement, result)
    for pattern, replacement in _RELAXED_SENSITIVE_LOCAL_PATH_PATTERNS:
        result = pattern.sub(replacement, result)
    return result
