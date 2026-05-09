from __future__ import annotations


class ToolAssetRuntime:
    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "tool_asset_runtime",
            "broker": "asset_broker_service",
            "capability_graph_required": True,
        }
