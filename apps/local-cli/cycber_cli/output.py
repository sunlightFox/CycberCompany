from __future__ import annotations

import json
import sys
from typing import Any

from cycber_cli.redaction import redact


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        encoded = text.encode(
            sys.stdout.encoding or "utf-8",
            errors="backslashreplace",
        )
        sys.stdout.buffer.write(encoded + b"\n")


def print_payload(payload: Any, *, json_mode: bool = False) -> None:
    safe = redact(payload)
    if json_mode:
        _safe_print(json.dumps(safe, ensure_ascii=False, default=str))
    elif isinstance(safe, str):
        _safe_print(safe)
    else:
        _safe_print(json.dumps(safe, ensure_ascii=False, indent=2, default=str))


def assistant_delta(event: dict[str, Any]) -> str:
    payload_obj = event.get("payload")
    payload: dict[str, Any] = payload_obj if isinstance(payload_obj, dict) else {}
    return str(payload.get("text") or "")


def persisted_assistant_text(events: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in events:
        event_type = str(item.get("event_type") or item.get("event") or "")
        payload_obj = item.get("payload")
        payload: dict[str, Any] = payload_obj if isinstance(payload_obj, dict) else {}
        nested_obj = payload.get("payload")
        nested: dict[str, Any] = nested_obj if isinstance(nested_obj, dict) else {}
        text = payload.get("text") or nested.get("text")
        if event_type == "response.delta" and text:
            parts.append(str(text))
    return "".join(parts).strip()


def compact_turn(turn: dict[str, Any]) -> dict[str, Any]:
    return {
        "turn_id": turn.get("turn_id"),
        "conversation_id": turn.get("conversation_id"),
        "status": turn.get("status"),
        "intent": turn.get("intent"),
        "mode": turn.get("mode"),
        "trace_id": turn.get("trace_id"),
    }
