from __future__ import annotations

from typing import Any


class ToolBuiltinRuntime:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    async def execute(
        self,
        request: Any,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> Any:
        return await self._runtime._execute_builtin_impl(
            request,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            trace_id=trace_id,
        )
