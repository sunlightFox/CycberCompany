from __future__ import annotations


class ToolMemoryRuntime:
    def diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "tool_memory_runtime",
            "search_layers": ["session", "episodic", "semantic", "procedural_candidate"],
            "trace_safe": True,
        }
