from __future__ import annotations


class ReleaseReportBuilder:
    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "release_report_builder",
            "compat_shape": True,
        }
