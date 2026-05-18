from __future__ import annotations

from typing import Any

from core_types import ErrorCode, TraceSpanStatus, TraceSpanType

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.schemas.tasks import ToolExecuteResponse


class ToolDispatcher:
    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    async def execute(
        self,
        request: Any,
        *,
        trace_id: str | None = None,
    ) -> ToolExecuteResponse:
        from app.services.tools import (
            _handle_ids_from_args,
            _public_memory_search_tool_call,
            _risk_for,
            _sanitize_tool_request_for_execution,
            _strip_internal_trace_fields,
            _terminal_command_policy,
        )

        try:
            tool = await self._runtime.get_tool(request.tool_name)
        except AppError as exc:
            if exc.code == ErrorCode.TOOL_NOT_FOUND.value:
                await self._runtime._safety_bridge.handle_unknown_tool(
                    tool_name=request.tool_name,
                    args=request.args,
                    task_id=request.task_id,
                    member_id=request.member_id,
                    trace_id=trace_id,
                )
            raise
        if tool.status not in {"active", "approval_required"}:
            raise AppError(ErrorCode.TOOL_NOT_FOUND, "工具未启用", status_code=404)
        request = _sanitize_tool_request_for_execution(request)
        if request.idempotency_key:
            existing = await self._runtime._repo.get_tool_call_by_idempotency(
                request.idempotency_key
            )
            if existing is not None:
                if existing["tool_name"] != request.tool_name:
                    raise AppError(
                        ErrorCode.CONFLICT,
                        "idempotency_key 已被其他工具调用使用",
                        status_code=409,
                    )
                return await self._runtime._response_from_existing_call(existing)
        self._runtime._validate_args(tool, request.args)
        risk_level = _risk_for(tool, request.args)
        terminal_command_policy = (
            self._runtime._boundary.classify_terminal_command(
                str(request.args.get("command") or "")
            )
            if tool.tool_name == "terminal.run" and self._runtime._boundary is not None
            else _terminal_command_policy(str(request.args.get("command") or ""))
            if tool.tool_name == "terminal.run"
            else None
        )
        task = await self._runtime._task_for_request(request)
        organization_id = task["organization_id"] if task else "org_default"
        tool_call_id = new_id("call")
        now = utc_now_iso()
        handle_ids = _handle_ids_from_args(request.args)
        span_id = await self._runtime._start_span(
            trace_id,
            TraceSpanType.TOOL_CALL,
            "execute tool",
            metadata={
                "tool_call_id": tool_call_id,
                "tool_name": request.tool_name,
                "task_id": request.task_id,
            },
        )
        try:
            await self._runtime._repo.insert_tool_call(
                {
                    "tool_call_id": tool_call_id,
                    "organization_id": organization_id,
                    "task_id": request.task_id,
                    "step_id": request.step_id,
                    "tool_name": tool.tool_name,
                    "source": tool.source,
                    "status": "running",
                    "approval_id": request.approval_id,
                    "idempotency_key": request.idempotency_key,
                    "args_redacted": self._runtime._redact_payload(request.args),
                    "handle_ids": handle_ids,
                    "risk_level": risk_level.value,
                    "trace_id": trace_id,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            envelope = await self._runtime._safety_bridge.evaluate(
                request=request,
                tool=tool,
                tool_call_id=tool_call_id,
                trace_id=trace_id,
                handle_ids=handle_ids,
                risk_level=risk_level,
                organization_id=organization_id,
                terminal_command_policy=terminal_command_policy,
            )
            approval = await self._runtime._safety_bridge.approval_if_required(
                request=request,
                tool=tool,
                tool_call_id=tool_call_id,
                organization_id=envelope.organization_id,
                risk_level=envelope.risk_level,
                terminal_command_policy=envelope.terminal_command_policy,
                boundary_required_controls=(
                    envelope.boundary_decision.required_controls
                    if envelope.boundary_decision is not None
                    else None
                ),
                trace_id=trace_id,
            )
            if approval is not None:
                await self._runtime._repo.update_tool_call(
                    tool_call_id,
                    {
                        "status": "approval_required",
                        "approval_id": approval.approval_id,
                        "updated_at": utc_now_iso(),
                    },
                )
                record = await self._runtime._tool_call_record(tool_call_id, request.task_id)
                await self._runtime._end_span(
                    span_id,
                    output_data={
                        "status": "approval_required",
                        "approval_id": approval.approval_id,
                    },
                )
                return ToolExecuteResponse(tool_call=record, approval=approval)

            resolved_asset_refs = await self._runtime._resolve_handles_for_tool(
                request,
                trace_id=trace_id,
            )
            await self._runtime._repo.update_tool_call(
                tool_call_id,
                {"resolved_asset_refs": resolved_asset_refs, "updated_at": utc_now_iso()},
            )
            if tool.source == "mcp":
                outcome = await self._runtime._execute_mcp_tool(
                    request,
                    tool=tool,
                    tool_call_id=tool_call_id,
                    organization_id=envelope.organization_id,
                    safety_decision_id=envelope.safety_decision.safety_decision_id,
                    policy_snapshot=envelope.policy_snapshot,
                    resolved_asset_refs=resolved_asset_refs,
                    trace_id=trace_id,
                )
            elif tool.source == "skill":
                outcome = await self._runtime._execute_skill_tool(
                    request,
                    tool=tool,
                    tool_call_id=tool_call_id,
                    organization_id=envelope.organization_id,
                    trace_id=trace_id,
                )
            else:
                outcome = await self._runtime._builtin_runtime.execute(
                    request,
                    tool_call_id=tool_call_id,
                    organization_id=envelope.organization_id,
                    trace_id=trace_id,
                )
            artifact_ids = [artifact.artifact_id for artifact in outcome.artifacts]
            redacted_result: dict[str, Any] = dict(self._runtime._redact_payload(outcome.result))
            if self._runtime._boundary is not None:
                dlp = await self._runtime._boundary.scan_output(
                    organization_id=envelope.organization_id,
                    source_type="tool_result",
                    source_id=tool_call_id,
                    scan_target="result",
                    value=outcome.result,
                    tool_call_id=tool_call_id,
                    task_id=request.task_id,
                    trace_id=trace_id,
                )
                redacted_result = dict(self._runtime._redact_payload(dlp.redacted_value))
            if request.tool_name == "memory.search":
                redacted_result = _strip_internal_trace_fields(redacted_result)
            await self._runtime._repo.update_tool_call(
                tool_call_id,
                {
                    "status": "completed",
                    "result_redacted": redacted_result,
                    "artifact_ids": artifact_ids,
                    "updated_at": utc_now_iso(),
                },
            )
            await self._runtime._audit.write_event(
                actor_type="system",
                action="tool.called",
                object_type="tool_call",
                object_id=tool_call_id,
                summary="工具调用完成",
                risk_level=envelope.risk_level,
                payload={
                    "tool_name": tool.tool_name,
                    "task_id": request.task_id,
                    "artifact_ids": artifact_ids,
                },
                trace_id=trace_id,
            )
            await self._runtime._end_span(
                span_id,
                output_data={"status": "completed", "artifact_count": len(outcome.artifacts)},
            )
            record = await self._runtime._tool_call_record(tool_call_id, request.task_id)
            if request.tool_name == "memory.search":
                record = _public_memory_search_tool_call(record)
            return ToolExecuteResponse(
                tool_call=record,
                artifacts=outcome.artifacts,
                result=redacted_result,
            )
        except Exception as exc:
            await self._runtime._repo.update_tool_call(
                tool_call_id,
                {
                    "status": "failed",
                    "error_code": getattr(exc, "code", ErrorCode.TOOL_EXECUTION_FAILED.value),
                    "error_summary": str(self._runtime._redact_payload(str(exc))),
                    "updated_at": utc_now_iso(),
                },
            )
            await self._runtime._audit.write_event(
                actor_type="system",
                action="tool.blocked" if isinstance(exc, AppError) else "tool.failed",
                object_type="tool_call",
                object_id=tool_call_id,
                summary="工具调用失败",
                risk_level=risk_level,
                payload={
                    "tool_name": tool.tool_name,
                    "error": str(self._runtime._redact_payload(str(exc))),
                },
                trace_id=trace_id,
            )
            await self._runtime._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.TOOL_EXECUTION_FAILED)},
            )
            raise
