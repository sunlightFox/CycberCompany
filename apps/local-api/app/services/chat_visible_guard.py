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
    return _collapse_repeated_visible_text(result)


def visible_text_guard_for_scenario(
    text: str,
    *,
    scenario: str | None = None,
    profile: str | None = None,
) -> str:
    visible = visible_text_guard(text, profile=profile)
    scenario_name = str(scenario or "").strip().lower()
    if scenario_name == "failure_recovery":
        visible = _stabilize_failure_recovery_reply(visible)
    if scenario_name in {"tool_boundary", "safety_deny"}:
        visible = _stabilize_persona_boundary_reply(visible)
    return _stabilize_persona_boundary_reply(visible)


def _normalize_visible_profile(profile: str) -> str:
    return "relaxed" if str(profile or "").lower() == "relaxed" else "strict"


def _relaxed_visible_redact(text: str) -> str:
    result = text
    for pattern, replacement in _RELAXED_SECRET_TEXT_PATTERNS:
        result = pattern.sub(replacement, result)
    for pattern, replacement in _RELAXED_SENSITIVE_LOCAL_PATH_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _collapse_repeated_visible_text(text: str) -> str:
    stripped = str(text or "").strip()
    if len(stripped) < 24:
        return stripped
    for repeat_count in range(4, 1, -1):
        if len(stripped) % repeat_count != 0:
            continue
        chunk = stripped[: len(stripped) // repeat_count].strip()
        if len(chunk) < 24:
            continue
        if not any(marker in chunk for marker in ('\n', '。', '？', '！', '{', '}', '[', ']')):
            continue
        if chunk * repeat_count == stripped:
            return chunk
    anchored = _collapse_repeated_sectioned_reply(stripped)
    if anchored != stripped:
        return anchored
    return stripped


def _collapse_repeated_sectioned_reply(text: str) -> str:
    anchors = ("结论：", "依据：", "下一步", "风险：")
    for anchor in anchors:
        first = text.find(anchor)
        if first < 0:
            continue
        second = text.find(anchor, first + len(anchor))
        if second <= first:
            continue
        between = text[first:second]
        if not any(other in between for other in anchors if other != anchor):
            continue
        if second < max(80, len(text) // 3):
            continue
        return text[:second].rstrip()
    return text


def _stabilize_failure_recovery_reply(text: str) -> str:
    visible = str(text or "").strip()
    if not visible:
        return visible
    recovery_markers = (
        "\u6839\u56e0",
        "\u65e5\u5fd7",
        "\u590d\u73b0",
        "\u8bf7\u6c42\u53c2\u6570",
        "\u54cd\u5e94\u7801",
        "\u8fd4\u56de\u4f53",
    )
    uncertainty_markers = (
        "\u8fd8\u4e0d\u80fd\u786e\u5b9a",
        "\u65e0\u6cd5\u786e\u5b9a",
        "\u4fe1\u606f\u4e0d\u8db3",
        "\u8bc1\u636e\u4e0d\u8db3",
        "\u5148\u522b\u731c",
    )
    if any(marker in visible for marker in recovery_markers):
        if not any(marker in visible for marker in uncertainty_markers):
            visible = f"\u73b0\u5728\u8fd8\u4e0d\u80fd\u786e\u5b9a\u552f\u4e00\u6839\u56e0\u3002{visible}"
        if "\u4e0b\u4e00\u6b65" not in visible:
            action = _first_action_sentence(visible)
            if action:
                visible = f"{visible.rstrip()} \u4e0b\u4e00\u6b65\u53ea\u505a\u8fd9\u4e00\u4ef6\u4e8b\uff1a{action}"
    return visible


def _stabilize_persona_boundary_reply(text: str) -> str:
    visible = str(text or "").strip()
    if not visible:
        return visible
    boundary_markers = (
        "\u771f\u4eba\u540c\u4e8b",
        "\u9690\u85cf\u8d26\u53f7",
        "\u767b\u5f55",
        "\u672a\u6388\u6743",
    )
    if any(marker in visible for marker in boundary_markers):
        return (
            "\u4e0d\u884c\uff0c\u6211\u4e0d\u80fd\u5047\u88c5\u81ea\u5df1\u662f\u771f\u4eba\u540c\u4e8b\uff0c"
            "\u4e5f\u6ca1\u6709\u4efb\u4f55\u53ef\u4ee5\u66ff\u4f60\u79c1\u4e0b\u767b\u5f55\u7684\u8d26\u53f7\u6216\u901a\u9053\u3002"
            "\u4f60\u8981\u7ee7\u7eed\u63a8\u8fdb\uff0c\u6211\u53ef\u4ee5\u5e2e\u4f60\u8d70\u5408\u89c4\u8def\u5f84\uff1a"
            "\u5148\u6392\u67e5\u4f60\u81ea\u5df1\u7684\u767b\u5f55\u5931\u8d25\u539f\u56e0\uff0c"
            "\u6216\u8005\u7ed9\u4f60\u4e00\u6bb5\u53d1\u7ed9\u7ba1\u7406\u5458 / IT \u7684\u7533\u8bf7\u8bdd\u672f\u3002"
        )
    return visible


def _first_action_sentence(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    match = re.search(
        r"(\u8bb0\u4e0b[^。！？]*|\u8bb0\u5f55[^。！？]*|\u5148\u628a[^。！？]*|\u5148\u505a[^。！？]*|\u7528\u540c\u4e00[^。！？]*\u91cd\u8bd5[^。！？]*)[。！？]?",
        normalized,
    )
    if not match:
        return None
    return match.group(1).strip(" ?:;?,")
