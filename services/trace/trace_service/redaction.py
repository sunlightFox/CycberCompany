from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEY_PARTS = (
    "api_key",
    "cookie",
    "mnemonic",
    "password",
    "private_key",
    "secret",
    "token",
)

TEXT_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_-]{12,}"), "[REDACTED_API_KEY]"),
    (
        re.compile(r"(?i)(api[_-]?key|token|secret)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        r"\1=[REDACTED_TOKEN]",
    ),
    (
        re.compile(r"(?i)(private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        r"\1=[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"(?i)(password)\s*[:=]\s*['\"]?[^'\"\s,;]+"), r"\1=[REDACTED_PASSWORD]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b", re.I),
        "[REDACTED_MNEMONIC]",
    ),
    (re.compile(r"\b[A-Za-z]:\\Users\\[^\\\s]+(?:\\[^\s,;]+)*"), "[REDACTED_LOCAL_PATH]"),
    (re.compile(r"/(?:Users|home)/[^/\s]+(?:/[^\s,;]+)*"), "[REDACTED_LOCAL_PATH]"),
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = value
        for pattern, replacement in TEXT_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
    return value
