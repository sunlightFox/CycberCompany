from __future__ import annotations

from typing import Any


class ToolMcpRuntime:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def normalize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        payload = dict(result)
        payload.setdefault("untrusted_external_content", True)
        payload.setdefault("taint", {})
        taint = payload["taint"]
        if isinstance(taint, dict):
            taint.setdefault("untrusted", True)
            taint.setdefault("record_id", payload.get("taint_record_id"))
            taint.setdefault("guard_decision", payload.get("taint_guard_decision"))
        return payload
