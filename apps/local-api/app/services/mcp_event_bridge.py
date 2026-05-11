from __future__ import annotations

from typing import Any

from trace_service import redact


class MCPEventBridge:
    def normalize_events(self, events: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for event in events:
            normalized.append(
                {
                    "source": source,
                    "event_type": event.get("event_type") or event.get("event") or "unknown",
                    "payload": redact(dict(event.get("payload") or event.get("payload_redacted") or {})),
                    "created_at": event.get("created_at"),
                    "trace_id": event.get("trace_id"),
                }
            )
        return normalized

