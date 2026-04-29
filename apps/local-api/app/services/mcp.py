from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, Protocol

from core_types import (
    ErrorCode,
    MCPPromptRecord,
    MCPResourceRecord,
    MCPServerRecord,
    MCPToolRecord,
    RiskLevel,
    ToolDefinition,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.db.repositories.task_repo import TaskRepository
from app.schemas.mcp import MCPServerCreateRequest, MCPSyncResponse
from app.schemas.tasks import ToolExecuteRequest
from app.services.audit import AuditEventService
from app.services.execution_boundary import ExecutionBoundaryService
from app.services.mcp_runtime import (
    MCP_DEFAULT_TIMEOUT_SECONDS,
    MCP_PROTOCOL_VERSION,
    MCPContentSanitizer,
    MCPLifecycleManager,
    MCPOutputActionGuard,
    MCPProtocolValidator,
    MCPRuntimeProfileService,
    filter_valid_prompts,
    filter_valid_resources,
    filter_valid_tools,
)


class MCPTransport(Protocol):
    async def start(self) -> None: ...

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None: ...

    async def close(self) -> None: ...


class MCPStdioTransport:
    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        timeout_seconds: float = 15.0,
    ) -> None:
        self._command = command
        self._args = args
        self._timeout_seconds = timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def start(self) -> None:
        if not self._command:
            raise AppError(ErrorCode.MCP_CONNECT_FAILED, "stdio MCP command 必填", status_code=422)
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._command,
                *self._args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise AppError(
                ErrorCode.MCP_CONNECT_FAILED,
                "MCP stdio 进程启动失败",
                status_code=502,
                details={"reason": str(redact(str(exc)))},
            ) from exc

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        process = self._require_process()
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        await self._write_json(payload)
        assert process.stdout is not None
        while True:
            try:
                line = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as exc:
                raise AppError(
                    ErrorCode.MCP_CONNECT_FAILED,
                    "MCP stdio 请求超时",
                    status_code=504,
                ) from exc
            if not line:
                raise AppError(ErrorCode.MCP_CONNECT_FAILED, "MCP stdio 已断开", status_code=502)
            message = _loads_json_line(line)
            if message.get("id") != request_id:
                continue
            if message.get("error"):
                raise AppError(
                    ErrorCode.MCP_TOOL_CALL_FAILED,
                    "MCP 返回错误",
                    status_code=502,
                    details={"error": redact(message["error"])},
                )
            result = message.get("result") or {}
            return result if isinstance(result, dict) else {"result": result}

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write_json({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()

    def _require_process(self) -> asyncio.subprocess.Process:
        if self._process is None:
            raise AppError(ErrorCode.MCP_CONNECT_FAILED, "MCP stdio 尚未启动", status_code=500)
        return self._process

    async def _write_json(self, payload: dict[str, Any]) -> None:
        process = self._require_process()
        if process.stdin is None:
            raise AppError(ErrorCode.MCP_CONNECT_FAILED, "MCP stdio 不可写", status_code=502)
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        process.stdin.write(data + b"\n")
        await process.stdin.drain()


class MCPService:
    def __init__(
        self,
        *,
        repo: SkillMcpRepository,
        task_repo: TaskRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
        mcp_config: dict[str, Any] | None = None,
        transport_factory: Callable[[dict[str, Any]], MCPTransport] | None = None,
        execution_boundary_service: ExecutionBoundaryService | None = None,
    ) -> None:
        self._repo = repo
        self._task_repo = task_repo
        self._trace = trace_service
        self._audit = audit_service
        self._mcp_config = _normalize_mcp_config(mcp_config or {})
        self._transport_factory = transport_factory or self._stdio_transport_factory
        self._boundary = execution_boundary_service
        self._profiles = MCPRuntimeProfileService(repo)
        self._lifecycle = MCPLifecycleManager(repo)
        self._protocol = MCPProtocolValidator(repo)
        self._sanitizer = MCPContentSanitizer(repo)
        self._taint_guard = MCPOutputActionGuard(repo)

    def set_transport_factory(self, factory: Callable[[dict[str, Any]], MCPTransport]) -> None:
        self._transport_factory = factory

    async def create_server(
        self,
        request: MCPServerCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> MCPServerRecord:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MCP_SERVER_REGISTER,
            "register mcp server",
            input_data={"server_id": request.server_id, "transport": request.transport},
        )
        try:
            now = utc_now_iso()
            server_id = _safe_id(request.server_id or request.display_name)
            profile = await self._validate_server_config(
                request,
                server_id=server_id,
                trace_id=trace_id,
            )
            await self._repo.upsert_mcp_server(
                {
                    "server_id": server_id,
                    "organization_id": "org_default",
                    "display_name": request.display_name,
                    "description": request.description,
                    "transport": request.transport,
                    "command": request.command,
                    "args": request.args,
                    "url": request.url,
                    "env_refs": request.env_refs,
                    "allowed_skills": request.allowed_skills,
                    "permission": request.permission,
                    "risk_policy": request.risk_policy,
                    "trust_level": request.trust_level,
                    "status": "registered",
                    "runtime_profile_id": profile.profile_id,
                    "lifecycle_status": "created",
                    "circuit_state": "closed",
                    "last_health_check_at": None,
                    "consecutive_failure_count": 0,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            await self._event(
                "mcp.server_registered",
                server_id=server_id,
                payload={"server_id": server_id, "transport": request.transport},
                trace_id=trace_id,
            )
            await self._audit.write_event(
                actor_type="system",
                action="mcp.server_registered",
                object_type="mcp_server",
                object_id=server_id,
                summary="MCP 服务已登记",
                risk_level=RiskLevel.R2,
                payload={"server_id": server_id, "transport": request.transport},
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"server_id": server_id})
            return await self.get_server(server_id)
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def list_servers(self) -> list[MCPServerRecord]:
        return [MCPServerRecord(**row) for row in await self._repo.list_mcp_servers()]

    async def get_server(self, server_id: str) -> MCPServerRecord:
        row = await self._repo.get_mcp_server(server_id)
        if row is None:
            raise AppError(ErrorCode.MCP_SERVER_NOT_FOUND, "MCP 服务不存在", status_code=404)
        return MCPServerRecord(**row)

    async def runtime_profile(self, server_id: str) -> Any:
        await self.get_server(server_id)
        profile = await self._profiles.get_profile(server_id)
        if profile is None:
            raise AppError(
                ErrorCode.MCP_SERVER_NOT_FOUND,
                "MCP runtime profile 不存在",
                status_code=404,
            )
        return profile

    async def lifecycle_events(self, server_id: str) -> list[Any]:
        await self.get_server(server_id)
        return await self._lifecycle.list_events(server_id)

    async def protocol_reports(self, server_id: str) -> list[Any]:
        await self.get_server(server_id)
        return await self._protocol.list_reports(server_id)

    async def sanitization_reports(self, server_id: str) -> list[Any]:
        await self.get_server(server_id)
        return await self._sanitizer.list_reports(server_id)

    async def taint_records(self, server_id: str) -> list[Any]:
        await self.get_server(server_id)
        return await self._taint_guard.list_records(server_id)

    async def enable_server(
        self,
        server_id: str,
        *,
        trace_id: str | None = None,
    ) -> MCPServerRecord:
        server = await self.get_server(server_id)
        if server.status == "revoked":
            raise AppError(ErrorCode.MCP_SERVER_NOT_FOUND, "MCP 服务已撤销", status_code=409)
        now = utc_now_iso()
        await self._repo.update_mcp_server(server_id, {"status": "enabled", "updated_at": now})
        await self._event(
            "mcp.server_enabled",
            server_id=server_id,
            payload={"server_id": server_id},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="mcp.server_enabled",
            object_type="mcp_server",
            object_id=server_id,
            summary="MCP 服务已启用",
            risk_level=RiskLevel.R2,
            payload={"server_id": server_id},
            trace_id=trace_id,
        )
        return await self.get_server(server_id)

    async def disable_server(
        self,
        server_id: str,
        *,
        trace_id: str | None = None,
    ) -> MCPServerRecord:
        now = utc_now_iso()
        server = await self._server_row(server_id)
        await self._repo.update_mcp_server(server_id, {"status": "disabled", "updated_at": now})
        await self._deactivate_mcp_capabilities(server, now)
        await self._event(
            "mcp.server_disabled",
            server_id=server_id,
            payload={"server_id": server_id},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="mcp.server_disabled",
            object_type="mcp_server",
            object_id=server_id,
            summary="MCP 服务已禁用",
            risk_level=RiskLevel.R2,
            payload={"server_id": server_id},
            trace_id=trace_id,
        )
        return await self.get_server(server_id)

    async def connect_server(
        self,
        server_id: str,
        *,
        trace_id: str | None = None,
    ) -> MCPServerRecord:
        server = await self._server_row(server_id)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MCP_SERVER_CONNECT,
            "connect mcp server",
            input_data={"server_id": server_id},
        )
        transport = self._transport_factory(server)
        try:
            await self._lifecycle.start_requested(server, operation="connect", trace_id=trace_id)
            await _start_transport(transport)
            await self._lifecycle.started(server, trace_id=trace_id)
            await self._initialize(transport, server, trace_id=trace_id)
            await transport.close()
            now = utc_now_iso()
            await self._repo.update_mcp_server(
                server_id,
                {
                    "status": "connected",
                    "last_connected_at": now,
                    "last_health_check_at": now,
                    "lifecycle_status": "ready",
                    "circuit_state": "closed",
                    "consecutive_failure_count": 0,
                    "last_error_code": None,
                    "last_error_summary": None,
                    "updated_at": now,
                },
            )
            await self._event(
                "mcp.server_connected",
                server_id=server_id,
                payload={"server_id": server_id},
                trace_id=trace_id,
            )
            await self._audit.write_event(
                actor_type="system",
                action="mcp.server_connected",
                object_type="mcp_server",
                object_id=server_id,
                summary="MCP 服务已连接",
                risk_level=RiskLevel.R2,
                payload={"server_id": server_id},
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"status": "connected"})
            await self._lifecycle.ready(server, operation="connect", trace_id=trace_id)
            return await self.get_server(server_id)
        except Exception as exc:
            await transport.close()
            await self._lifecycle.failed(server, operation="connect", error=exc, trace_id=trace_id)
            await self._repo.update_mcp_server(
                server_id,
                {
                    "status": "degraded",
                    "last_error_code": getattr(exc, "code", ErrorCode.MCP_CONNECT_FAILED.value),
                    "last_error_summary": str(redact(str(exc))),
                    "updated_at": utc_now_iso(),
                },
            )
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            if isinstance(exc, AppError):
                raise
            raise AppError(
                ErrorCode.MCP_CONNECT_FAILED,
                "MCP 服务连接失败",
                status_code=502,
            ) from exc

    async def disconnect_server(
        self,
        server_id: str,
        *,
        trace_id: str | None = None,
    ) -> MCPServerRecord:
        server = await self._server_row(server_id)
        await self._repo.update_mcp_server(
            server_id,
            {"status": "disconnected", "lifecycle_status": "stopped", "updated_at": utc_now_iso()},
        )
        await self._deactivate_mcp_capabilities(server, utc_now_iso())
        await self._event(
            "mcp.server_disconnected",
            server_id=server_id,
            payload={"server_id": server_id},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="mcp.server_disconnected",
            object_type="mcp_server",
            object_id=server_id,
            summary="MCP 服务已断开",
            risk_level=RiskLevel.R2,
            payload={"server_id": server_id},
            trace_id=trace_id,
        )
        await self._lifecycle.stopped(server, trace_id=trace_id)
        return await self.get_server(server_id)

    async def sync_server(self, server_id: str, *, trace_id: str | None = None) -> MCPSyncResponse:
        server = await self._server_row(server_id)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MCP_SERVER_SYNC,
            "sync mcp server",
            input_data={"server_id": server_id},
        )
        transport = self._transport_factory(server)
        try:
            await self._lifecycle.start_requested(server, operation="sync", trace_id=trace_id)
            await _start_transport(transport)
            await self._lifecycle.started(server, trace_id=trace_id)
            await self._initialize(transport, server, trace_id=trace_id)
            raw_tools = await _paged_list(transport, "tools/list", "tools")
            raw_resources = await _paged_list(transport, "resources/list", "resources")
            raw_prompts = await _paged_list(transport, "prompts/list", "prompts")
            await self._protocol.validate_list_response(
                server_id=server_id,
                organization_id=server["organization_id"],
                operation="tools/list",
                items=raw_tools,
                trace_id=trace_id,
            )
            await self._protocol.validate_list_response(
                server_id=server_id,
                organization_id=server["organization_id"],
                operation="resources/list",
                items=raw_resources,
                trace_id=trace_id,
            )
            await self._protocol.validate_list_response(
                server_id=server_id,
                organization_id=server["organization_id"],
                operation="prompts/list",
                items=raw_prompts,
                trace_id=trace_id,
            )
            tools = filter_valid_tools(raw_tools)
            resources = filter_valid_resources(raw_resources)
            prompts = filter_valid_prompts(raw_prompts)
            now = utc_now_iso()
            for item in tools:
                await self._upsert_tool(server, item, now)
            for item in resources:
                await self._sanitize_sync_item(server, "resource", item, trace_id=trace_id)
                await self._upsert_resource(server, item, now)
            for item in prompts:
                await self._sanitize_sync_item(server, "prompt", item, trace_id=trace_id)
                await self._upsert_prompt(server, item, now)
            stale_tools = await self._repo.disable_mcp_tools_absent(
                server_id,
                _mcp_names(tools, "name"),
                now,
            )
            for stale_tool in stale_tools:
                await self._upsert_registry_tool(
                    server,
                    stale_tool,
                    now,
                    status="disabled",
                )
            stale_resources = await self._repo.disable_mcp_resources_absent(
                server_id,
                _mcp_names(resources, "uri"),
                now,
            )
            stale_prompts = await self._repo.disable_mcp_prompts_absent(
                server_id,
                _mcp_names(prompts, "name"),
                now,
            )
            await transport.close()
            await self._repo.update_mcp_server(
                server_id,
                {
                    "status": "ready",
                    "last_sync_at": now,
                    "last_health_check_at": now,
                    "lifecycle_status": "ready",
                    "circuit_state": "closed",
                    "consecutive_failure_count": 0,
                    "last_error_code": None,
                    "last_error_summary": None,
                    "updated_at": now,
                },
            )
            await self._event(
                "mcp.tools_synced",
                server_id=server_id,
                payload={
                    "server_id": server_id,
                    "tools": len(tools),
                    "resources": len(resources),
                    "prompts": len(prompts),
                    "stale_tools_disabled": len(stale_tools),
                    "stale_resources_disabled": stale_resources,
                    "stale_prompts_disabled": stale_prompts,
                },
                trace_id=trace_id,
            )
            await self._audit.write_event(
                actor_type="system",
                action="mcp.tools_synced",
                object_type="mcp_server",
                object_id=server_id,
                summary="MCP 能力已同步",
                risk_level=RiskLevel.R2,
                payload={
                    "tools": len(tools),
                    "resources": len(resources),
                    "prompts": len(prompts),
                    "stale_tools_disabled": len(stale_tools),
                    "stale_resources_disabled": stale_resources,
                    "stale_prompts_disabled": stale_prompts,
                },
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                output_data={
                    "tools": len(tools),
                    "resources": len(resources),
                    "prompts": len(prompts),
                    "stale_tools_disabled": len(stale_tools),
                    "stale_resources_disabled": stale_resources,
                    "stale_prompts_disabled": stale_prompts,
                },
            )
            await self._lifecycle.ready(server, operation="sync", trace_id=trace_id)
            return MCPSyncResponse(
                server=await self.get_server(server_id),
                tools_synced=len(tools),
                resources_synced=len(resources),
                prompts_synced=len(prompts),
            )
        except Exception as exc:
            await transport.close()
            await self._lifecycle.failed(server, operation="sync", error=exc, trace_id=trace_id)
            await self._repo.update_mcp_server(
                server_id,
                {
                    "status": "degraded",
                    "last_error_code": getattr(exc, "code", ErrorCode.MCP_SYNC_FAILED.value),
                    "last_error_summary": str(redact(str(exc))),
                    "updated_at": utc_now_iso(),
                },
            )
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            if isinstance(exc, AppError):
                raise
            raise AppError(ErrorCode.MCP_SYNC_FAILED, "MCP 能力同步失败", status_code=502) from exc

    async def list_tools(self, server_id: str) -> list[MCPToolRecord]:
        await self.get_server(server_id)
        return [MCPToolRecord(**row) for row in await self._repo.list_mcp_tools(server_id)]

    async def list_resources(self, server_id: str) -> list[MCPResourceRecord]:
        await self.get_server(server_id)
        return [MCPResourceRecord(**row) for row in await self._repo.list_mcp_resources(server_id)]

    async def list_prompts(self, server_id: str) -> list[MCPPromptRecord]:
        await self.get_server(server_id)
        return [MCPPromptRecord(**row) for row in await self._repo.list_mcp_prompts(server_id)]

    async def call_tool(
        self,
        *,
        tool: ToolDefinition,
        request: ToolExecuteRequest,
        tool_call_id: str,
        organization_id: str,
        safety_decision_id: str | None = None,
        policy_snapshot: dict[str, Any] | None = None,
        resolved_asset_refs: list[dict[str, Any]] | None = None,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        registry_tool_name = tool.tool_name
        mcp_tool = await self._repo.get_mcp_tool_by_registry_name(registry_tool_name)
        if mcp_tool is None:
            raise AppError(ErrorCode.MCP_TOOL_NOT_FOUND, "MCP 工具不存在", status_code=404)
        server = await self._server_row(mcp_tool["server_id"])
        if server["status"] != "ready":
            raise AppError(ErrorCode.MCP_CONNECT_FAILED, "MCP 服务未就绪", status_code=409)
        scope_policy = _mcp_scope_policy(server, request.member_id)
        if not scope_policy["allowed"]:
            raise AppError(
                ErrorCode.MCP_TOOL_PERMISSION_DENIED,
                "当前成员无权调用该 MCP 工具",
                status_code=403,
                details={"reason": scope_policy["reason"]},
            )
        merged_policy_snapshot = {
            **(policy_snapshot or {}),
            "mcp_scope": scope_policy,
            "server_permission": _redacted_permission(server.get("permission", {})),
            "runtime_profile_id": server.get("runtime_profile_id"),
            "mcp_runtime": {
                "lifecycle_status": server.get("lifecycle_status"),
                "circuit_state": server.get("circuit_state"),
                "untrusted_output_marker": True,
            },
        }
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.MCP_TOOL_CALL,
            "call mcp tool",
            input_data={"server_id": server["server_id"], "tool_name": mcp_tool["tool_name"]},
        )
        now = utc_now_iso()
        mcp_call_id = new_id("mcpcall")
        await self._repo.insert_mcp_call(
            {
                "mcp_call_id": mcp_call_id,
                "organization_id": organization_id,
                "server_id": server["server_id"],
                "mcp_tool_id": mcp_tool["mcp_tool_id"],
                "task_id": request.task_id,
                "step_id": request.step_id,
                "tool_call_id": tool_call_id,
                "status": "running",
                "request_redacted": {
                    "name": mcp_tool["tool_name"],
                    "arguments": redact(request.args),
                },
                "safety_decision_id": safety_decision_id,
                "policy_snapshot": merged_policy_snapshot,
                "resolved_asset_refs": resolved_asset_refs or [],
                "trace_id": trace_id,
                "started_at": now,
                "created_at": now,
            }
        )
        transport = self._transport_factory(server)
        try:
            await self._lifecycle.start_requested(server, operation="tool_call", trace_id=trace_id)
            await _start_transport(transport)
            await self._lifecycle.started(server, trace_id=trace_id)
            await self._initialize(transport, server, trace_id=trace_id)
            await self._event(
                "mcp.tool_started",
                server_id=server["server_id"],
                payload={"mcp_call_id": mcp_call_id, "tool_name": mcp_tool["tool_name"]},
                trace_id=trace_id,
            )
            result = await _request_with_timeout(
                transport,
                "tools/call",
                {"name": mcp_tool["tool_name"], "arguments": request.args},
            )
            protocol_report = await self._protocol.validate_tool_call_result(
                server_id=server["server_id"],
                organization_id=organization_id,
                mcp_call_id=mcp_call_id,
                result=result,
                trace_id=trace_id,
            )
            await transport.close()
            completed_at = utc_now_iso()
            response = redact(_normalize_tool_result(result))
            dlp_report_id = None
            if self._boundary is not None:
                dlp = await self._boundary.scan_output(
                    organization_id=organization_id,
                    source_type="mcp_response",
                    source_id=mcp_call_id,
                    scan_target="response",
                    value=response,
                    tool_call_id=tool_call_id,
                    mcp_call_id=mcp_call_id,
                    task_id=request.task_id,
                    trace_id=trace_id,
                )
                response = redact(dlp.redacted_value)
                dlp_report_id = dlp.report.dlp_report_id
            sanitization_report = await self._sanitizer.sanitize(
                organization_id=organization_id,
                server_id=server["server_id"],
                source_type="tool_output",
                source_id=mcp_call_id,
                value=response,
                dlp_report_id=dlp_report_id,
                trace_id=trace_id,
            )
            taint_record = await self._taint_guard.record_taint(
                organization_id=organization_id,
                server_id=server["server_id"],
                mcp_call_id=mcp_call_id,
                tool_call_id=tool_call_id,
                value=response,
                target_action=tool.tool_name,
                target_risk_level=RiskLevel(tool.risk_policy.get("default", "R2")),
                trace_id=trace_id,
            )
            response_payload = {
                "response": response,
                "untrusted_external_content": True,
                "dlp_report_id": dlp_report_id,
                "sanitization_report_id": sanitization_report.sanitization_report_id,
                "taint_record_id": taint_record.taint_record_id,
                "taint_guard_decision": taint_record.guard_decision,
            }
            merged_policy_snapshot = {
                **merged_policy_snapshot,
                "protocol_validation_report_id": protocol_report.validation_report_id,
                "sanitization_report_id": sanitization_report.sanitization_report_id,
                "taint_record_id": taint_record.taint_record_id,
                "taint_guard_decision": taint_record.guard_decision,
                "taint_reason_codes": taint_record.reason_codes,
            }
            await self._repo.update_mcp_call(
                mcp_call_id,
                {
                    "status": "completed",
                    "response_redacted": response_payload,
                    "policy_snapshot": merged_policy_snapshot,
                    "completed_at": completed_at,
                },
            )
            await self._event(
                "mcp.tool_completed",
                server_id=server["server_id"],
                payload={"mcp_call_id": mcp_call_id, "tool_name": mcp_tool["tool_name"]},
                trace_id=trace_id,
            )
            await self._audit.write_event(
                actor_type="system",
                action="mcp.tool_called",
                object_type="mcp_call",
                object_id=mcp_call_id,
                summary="MCP 工具调用完成",
                risk_level=RiskLevel(tool.risk_policy.get("default", "R2")),
                payload={"server_id": server["server_id"], "tool_name": mcp_tool["tool_name"]},
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"status": "completed"})
            await self._lifecycle.ready(server, operation="tool_call", trace_id=trace_id)
            return {"mcp_call_id": mcp_call_id, **response_payload}
        except Exception as exc:
            await transport.close()
            await self._lifecycle.failed(
                server,
                operation="tool_call",
                error=exc,
                trace_id=trace_id,
            )
            await self._repo.update_mcp_call(
                mcp_call_id,
                {
                    "status": "failed",
                    "error_code": getattr(exc, "code", ErrorCode.MCP_TOOL_CALL_FAILED.value),
                    "error_summary": str(redact(str(exc))),
                    "completed_at": utc_now_iso(),
                },
            )
            await self._event(
                "mcp.tool_failed",
                server_id=server["server_id"],
                payload={"mcp_call_id": mcp_call_id, "error": str(redact(str(exc)))},
                trace_id=trace_id,
            )
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            if isinstance(exc, AppError):
                raise
            raise AppError(
                ErrorCode.MCP_TOOL_CALL_FAILED,
                "MCP 工具调用失败",
                status_code=502,
            ) from exc

    async def replay_mcp_calls(self, task_id: str) -> list[dict[str, Any]]:
        return [redact(row) for row in await self._repo.list_mcp_calls(task_id)]

    async def _server_row(self, server_id: str) -> dict[str, Any]:
        row = await self._repo.get_mcp_server(server_id)
        if row is None:
            raise AppError(ErrorCode.MCP_SERVER_NOT_FOUND, "MCP 服务不存在", status_code=404)
        return row

    async def _initialize(
        self,
        transport: MCPTransport,
        server: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> None:
        result = await _request_with_timeout(
            transport,
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "cycbercompany-local-api", "version": "0.1.0"},
            },
        )
        await self._protocol.validate_initialize(
            server_id=server["server_id"],
            organization_id=str(server.get("organization_id") or "org_default"),
            result=result,
            trace_id=trace_id,
        )
        protocol_version = result.get("protocolVersion") or result.get("protocol_version")
        if protocol_version and str(protocol_version) != MCP_PROTOCOL_VERSION:
            # MCP allows protocol negotiation, but keep the negotiated value visible in trace/audit
            # without failing older compatible test transports.
            server["negotiated_protocol_version"] = protocol_version
        await transport.notify("notifications/initialized", {})

    async def _sanitize_sync_item(
        self,
        server: dict[str, Any],
        source_type: str,
        item: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> None:
        source_id = str(item.get("uri") or item.get("name") or "")
        await self._sanitizer.sanitize(
            organization_id=str(server.get("organization_id") or "org_default"),
            server_id=server["server_id"],
            source_type=source_type,
            source_id=source_id,
            value=item,
            mime_type=item.get("mimeType") or item.get("mime_type"),
            trace_id=trace_id,
        )

    async def _upsert_tool(self, server: dict[str, Any], item: dict[str, Any], now: str) -> None:
        name = str(item.get("name") or "")
        if not name:
            return
        server_id = server["server_id"]
        mcp_tool_id = f"mcptool_{_safe_id(server_id)}_{_safe_id(name)}"
        registry_name = f"mcp.{_safe_id(server_id)}.{_safe_id(name)}"
        input_schema = item.get("inputSchema") or item.get("input_schema") or {}
        risk_policy = _risk_policy_for_mcp_tool(item, server.get("risk_policy", {}))
        status = _initial_mcp_tool_status(
            item,
            risk_policy,
            default_unknown_status=str(
                self._mcp_config.get("default_unknown_tool_status") or "disabled"
            ),
        )
        row = {
            "mcp_tool_id": mcp_tool_id,
            "organization_id": "org_default",
            "server_id": server_id,
            "tool_name": name,
            "registry_tool_name": registry_name,
            "description": item.get("description"),
            "input_schema": input_schema,
            "output_schema": item.get("outputSchema") or item.get("output_schema") or {},
            "risk_policy": risk_policy,
            "required_handle_types": item.get("requiredHandleTypes") or [],
            "status": status,
            "synced_at": now,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.upsert_mcp_tool(row)
        await self._upsert_registry_tool(server, row, now, status=status)

    async def _upsert_registry_tool(
        self,
        server: dict[str, Any],
        tool: dict[str, Any],
        now: str,
        *,
        status: str,
    ) -> None:
        await self._task_repo.upsert_tool(
            {
                "tool_name": tool["registry_tool_name"],
                "display_name": tool["tool_name"],
                "description": tool.get("description") or f"MCP tool {tool['tool_name']}",
                "source": "mcp",
                "input_schema": tool.get("input_schema", {}),
                "output_schema": tool.get("output_schema", {}),
                "risk_policy": tool.get("risk_policy", {"default": "R2"}),
                "required_handle_types": tool.get("required_handle_types", []),
                "status": status,
                "mcp_server_id": server["server_id"],
                "mcp_tool_id": tool["mcp_tool_id"],
                "adapter_config": {
                    "kind": "mcp_tool",
                    "server_id": server["server_id"],
                    "tool_name": tool["tool_name"],
                },
                "trust_level": server.get("trust_level", "restricted"),
                "created_at": now,
                "updated_at": now,
            }
        )

    async def _deactivate_mcp_capabilities(self, server: dict[str, Any], now: str) -> None:
        tools = await self._repo.list_mcp_tools(server["server_id"])
        await self._repo.disable_mcp_tools_absent(server["server_id"], set(), now)
        await self._repo.disable_mcp_resources_absent(server["server_id"], set(), now)
        await self._repo.disable_mcp_prompts_absent(server["server_id"], set(), now)
        for tool in tools:
            await self._upsert_registry_tool(server, tool, now, status="disabled")

    async def _upsert_resource(
        self,
        server: dict[str, Any],
        item: dict[str, Any],
        now: str,
    ) -> None:
        uri = str(item.get("uri") or "")
        if not uri:
            return
        await self._repo.upsert_mcp_resource(
            {
                "resource_id": f"mcpres_{_safe_id(server['server_id'])}_{_safe_id(uri)}",
                "organization_id": "org_default",
                "server_id": server["server_id"],
                "uri": uri,
                "name": item.get("name"),
                "description": item.get("description"),
                "mime_type": item.get("mimeType") or item.get("mime_type"),
                "trust_level": "untrusted_external_content",
                "sensitivity": "low",
                "metadata": {"source": "mcp_resource", "untrusted_external_content": True},
                "status": "active",
                "synced_at": now,
                "created_at": now,
                "updated_at": now,
            }
        )

    async def _upsert_prompt(self, server: dict[str, Any], item: dict[str, Any], now: str) -> None:
        name = str(item.get("name") or "")
        if not name:
            return
        arguments = item.get("arguments") or item.get("argumentsSchema") or {}
        if isinstance(arguments, list):
            arguments = {"arguments": arguments}
        await self._repo.upsert_mcp_prompt(
            {
                "prompt_id": f"mcpprompt_{_safe_id(server['server_id'])}_{_safe_id(name)}",
                "organization_id": "org_default",
                "server_id": server["server_id"],
                "name": name,
                "description": item.get("description"),
                "arguments_schema": arguments,
                "prompt_template_redacted": None,
                "trust_level": "mcp_prompt_template",
                "status": "active",
                "synced_at": now,
                "created_at": now,
                "updated_at": now,
            }
        )

    async def _event(
        self,
        event_type: str,
        *,
        server_id: str,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> None:
        await self._repo.insert_event(
            {
                "event_id": new_id("pevt"),
                "organization_id": "org_default",
                "server_id": server_id,
                "event_type": event_type,
                "payload": payload,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )

    async def _validate_server_config(
        self,
        request: MCPServerCreateRequest,
        *,
        server_id: str,
        trace_id: str | None,
    ) -> Any:
        transport_valid = request.transport == "stdio"
        command_present = bool(request.command)
        command_allowed = bool(
            request.command
            and _allowed_stdio_command(
                request.command,
                request.args,
                allowed_commands=self._mcp_config["allowed_stdio_commands"],
                blocked_markers=self._mcp_config["blocked_stdio_markers"],
            )
        )
        env_refs_only = all("=" not in item for item in request.env_refs)
        no_inline_secret = not _contains_inline_secret(request.env_refs)
        reason_codes: list[str] = []
        if not transport_valid:
            reason_codes.append("mcp_transport_not_supported")
        if not command_present:
            reason_codes.append("mcp_stdio_command_required")
        if command_present and not command_allowed:
            reason_codes.append("mcp_command_not_allowlisted")
        if not env_refs_only:
            reason_codes.append("mcp_env_refs_must_not_inline_env")
        if not no_inline_secret:
            reason_codes.append("mcp_inline_secret_denied")
        profile = await self._profiles.create_profile(
            server_id=server_id,
            organization_id="org_default",
            display_name=request.display_name,
            transport=request.transport,
            command=request.command,
            args=request.args,
            env_refs=request.env_refs,
            permission=request.permission,
            trust_level=request.trust_level,
            command_allowed=command_allowed,
            env_refs_only=env_refs_only,
            no_inline_secret=no_inline_secret,
            trace_id=trace_id,
        )
        if self._boundary is not None:
            await self._boundary.check_mcp_process_policy(
                organization_id="org_default",
                server_id=server_id,
                display_name=request.display_name,
                command=request.command,
                command_allowed=command_allowed,
                args_schema_valid=transport_valid and command_present,
                env_refs_only=env_refs_only,
                no_inline_secret=no_inline_secret,
                server_scope_valid=True,
                member_scope_valid=True,
                reason_codes=reason_codes,
                trace_id=trace_id,
            )
        if not transport_valid:
            raise AppError(
                ErrorCode.MCP_CONNECT_FAILED,
                "当前阶段仅支持 stdio MCP transport",
                status_code=422,
            )
        if not command_present:
            raise AppError(ErrorCode.MCP_CONNECT_FAILED, "stdio command 必填", status_code=422)
        if not command_allowed:
            raise AppError(
                ErrorCode.MCP_CONNECT_FAILED,
                "stdio command 不在本地安全允许范围内",
                status_code=422,
            )
        if not env_refs_only:
            raise AppError(
                ErrorCode.MCP_CONNECT_FAILED,
                "env_refs 只能保存 SecretStore 引用，不允许明文环境变量",
                status_code=422,
            )
        if not no_inline_secret:
            raise AppError(
                ErrorCode.MCP_CONNECT_FAILED,
                "env_refs 不允许包含 secret 明文",
                status_code=422,
            )
        return profile

    def _stdio_transport_factory(self, server: dict[str, Any]) -> MCPTransport:
        return MCPStdioTransport(
            command=str(server.get("command") or ""),
            args=server.get("args", []),
        )

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        input_data: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            input_data=redact(input_data or {}),
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                status=status,
                output_data=redact(output_data or {}),
            )


async def _paged_list(
    transport: MCPTransport,
    method: str,
    result_key: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        params = {"cursor": cursor} if cursor else {}
        result = await _request_with_timeout(transport, method, params)
        raw_items = result.get(result_key) or []
        items.extend(item for item in raw_items if isinstance(item, dict))
        cursor = result.get("nextCursor") or result.get("next_cursor")
        if not cursor:
            return items


async def _start_transport(transport: MCPTransport) -> None:
    await asyncio.wait_for(transport.start(), timeout=MCP_DEFAULT_TIMEOUT_SECONDS)


async def _request_with_timeout(
    transport: MCPTransport,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        result = await asyncio.wait_for(
            transport.request(method, params),
            timeout=MCP_DEFAULT_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise AppError(
            ErrorCode.MCP_TOOL_CALL_FAILED
            if method == "tools/call"
            else ErrorCode.MCP_CONNECT_FAILED,
            f"MCP request timed out: {method}",
            status_code=504,
        ) from exc
    if not isinstance(result, dict):
        raise AppError(
            ErrorCode.MCP_TOOL_CALL_FAILED
            if method == "tools/call"
            else ErrorCode.MCP_CONNECT_FAILED,
            "MCP response is not an object",
            status_code=502,
        )
    return result


def _normalize_mcp_config(config: dict[str, Any]) -> dict[str, Any]:
    nested = config.get("mcp")
    raw: dict[str, Any] = nested if isinstance(nested, dict) else config
    allowed = raw.get("allowed_stdio_commands") or []
    blocked = raw.get("blocked_stdio_markers") or []
    return {
        "allowed_stdio_commands": {str(item).lower() for item in allowed},
        "blocked_stdio_markers": [str(item).lower() for item in blocked],
        "default_unknown_tool_status": str(
            raw.get("default_unknown_tool_status") or "disabled"
        ),
    }


def _risk_policy_for_mcp_tool(
    item: dict[str, Any],
    server_policy: dict[str, Any],
) -> dict[str, str]:
    annotations = item.get("annotations") or {}
    if annotations.get("destructiveHint") or annotations.get("openWorldHint"):
        return {"default": str(server_policy.get("destructive", "R4"))}
    if annotations.get("readOnlyHint"):
        return {"default": "R1"}
    return {"default": str(server_policy.get("default", "R2"))}


def _initial_mcp_tool_status(
    item: dict[str, Any],
    risk_policy: dict[str, str],
    *,
    default_unknown_status: str,
) -> str:
    annotations = item.get("annotations") or {}
    risk = str(risk_policy.get("default") or "R2")
    if annotations.get("readOnlyHint") and risk in {"R0", "R1"}:
        return "active"
    if risk in {"R0", "R1"}:
        return "active"
    if risk in {"R3", "R4", "R5"}:
        return "approval_required"
    return "disabled" if default_unknown_status == "disabled" else "approval_required"


def _normalize_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    if "content" in result:
        return {"content": result["content"], "is_error": bool(result.get("isError"))}
    return result


def _mcp_scope_policy(server: dict[str, Any], member_id: str) -> dict[str, Any]:
    raw_permission = server.get("permission")
    permission: dict[str, Any] = raw_permission if isinstance(raw_permission, dict) else {}
    denied_members = {str(item) for item in permission.get("denied_members", [])}
    denied_members.update(str(item) for item in permission.get("denied_member_ids", []))
    allowed_members = {str(item) for item in permission.get("allowed_members", [])}
    allowed_members.update(str(item) for item in permission.get("allowed_member_ids", []))
    if "*" in denied_members or member_id in denied_members:
        return {"allowed": False, "reason": "member_denied", "member_id": member_id}
    if allowed_members and member_id not in allowed_members:
        return {"allowed": False, "reason": "member_not_in_allowlist", "member_id": member_id}
    return {
        "allowed": True,
        "reason": "member_scope_allowed",
        "member_id": member_id,
        "policy_sources": ["mcp_server.permission"],
    }


def _redacted_permission(permission: Any) -> dict[str, Any]:
    return redact(permission if isinstance(permission, dict) else {})


def _mcp_names(items: list[dict[str, Any]], key: str) -> set[str]:
    return {str(item.get(key) or "") for item in items if item.get(key)}


def _loads_json_line(line: bytes) -> dict[str, Any]:
    try:
        value = json.loads(line.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AppError(
            ErrorCode.MCP_TOOL_CALL_FAILED,
            "MCP 返回了非法 JSON",
            status_code=502,
        ) from exc
    if not isinstance(value, dict):
        raise AppError(ErrorCode.MCP_TOOL_CALL_FAILED, "MCP 返回不是 JSON object", status_code=502)
    return value


def _safe_id(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in value)
    return safe.strip("._-") or "mcp"


def _allowed_stdio_command(
    command: str,
    args: list[str],
    *,
    allowed_commands: set[str],
    blocked_markers: list[str],
) -> bool:
    lowered = command.lower()
    executable = lowered.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    blocked_executables = {"powershell", "powershell.exe", "cmd", "cmd.exe", "pwsh", "pwsh.exe"}
    if executable in blocked_executables:
        return False
    if executable not in allowed_commands and lowered not in allowed_commands:
        return False
    joined = " ".join([command, *args]).lower()
    return not any(marker in joined for marker in blocked_markers)


def _contains_inline_secret(env_refs: list[str]) -> bool:
    markers = ("secret=", "token=", "api_key=", "private_key=", "cookie=", "mnemonic=")
    return any(marker in item.lower() for item in env_refs for marker in markers)
