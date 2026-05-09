from __future__ import annotations


class ReleaseGateRuntime:
    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "release_gate_runtime",
            "report_builder": "release_report_builder",
            "phase_analyzers": "release_phase_analyzers",
        }
