from __future__ import annotations

from typing import Any


class ToolTerminalRuntime:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def enrich_policy_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        sandbox_result: Any,
        log_artifact_id: str,
        dlp_report_id: str | None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        next_snapshot = dict(snapshot)
        next_snapshot["terminal_sandbox_result"] = {
            "selected_backend": sandbox_result.backend,
            "backend": sandbox_result.backend,
            "backend_status": sandbox_result.backend_status,
            "fallback_chain": sandbox_result.fallback_chain,
            "degraded_reason": sandbox_result.degraded_reason,
            "timed_out": sandbox_result.timed_out,
            "output_truncated": sandbox_result.output_truncated,
            "resource_usage": sandbox_result.resource_usage,
            "cleanup": sandbox_result.cleanup,
            "approval_binding": approval_id,
            "log_artifact_id": log_artifact_id,
            "dlp_report_id": dlp_report_id,
        }
        return next_snapshot
