from __future__ import annotations


class MCPCallRuntime:
    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "mcp_call_runtime",
            "approval_bypass_allowed": False,
            "untrusted_output_marker": True,
        }
