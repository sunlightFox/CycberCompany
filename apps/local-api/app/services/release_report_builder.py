from __future__ import annotations

from typing import Any


class ReleaseReportBuilder:
    def augment_summary(
        self,
        summary: dict[str, Any],
        *,
        gate_status: str,
        evidence_count: int,
        blocker_count: int,
    ) -> dict[str, Any]:
        return {
            **summary,
            "phase70_runtime": {
                "release_runtime": "release_gate_runtime",
                "report_builder": "release_report_builder",
                "gate_status": gate_status,
                "evidence_count": evidence_count,
                "blocker_count": blocker_count,
            },
        }

    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "release_report_builder",
            "compat_shape": True,
        }
