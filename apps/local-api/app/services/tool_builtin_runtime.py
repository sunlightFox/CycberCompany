from __future__ import annotations

from typing import Any

from core_types import ErrorCode

from app.core.errors import AppError


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
        name = request.tool_name
        if name.startswith("file."):
            return await self._runtime._execute_file_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("knowledge."):
            return await self._runtime._execute_knowledge_tool(request, trace_id=trace_id)
        if name.startswith("memory."):
            return await self._runtime._execute_memory_tool(request, trace_id=trace_id)
        if name.startswith("asset."):
            return await self._runtime._execute_asset_tool(request, trace_id=trace_id)
        if name.startswith("browser."):
            return await self._runtime._execute_browser_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("terminal."):
            return await self._runtime._terminal_runtime.execute(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith(("project.", "runtime.", "host.")):
            return await self._runtime._execute_deployment_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("media."):
            return await self._runtime._execute_media_tool(request, trace_id=trace_id)
        if name.startswith("office."):
            return await self._runtime._execute_office_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("account."):
            return await self._runtime._execute_account_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("email_test."):
            return await self._runtime._execute_email_test_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name == "hardware.query_status":
            return self._runtime._hardware_query_status_outcome()
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "工具不存在", status_code=404)
