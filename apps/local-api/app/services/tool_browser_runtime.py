from __future__ import annotations


class ToolBrowserRuntime:
    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "tool_browser_runtime",
            "executor": "browser_executor",
            "stateful_session_binding": True,
        }
