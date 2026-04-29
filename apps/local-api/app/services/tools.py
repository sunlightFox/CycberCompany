from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from core_types import (
    ApprovalDetail,
    ErrorCode,
    RiskLevel,
    TaskArtifact,
    ToolCallRecord,
    ToolDefinition,
    TraceSpanStatus,
    TraceSpanType,
)
from safety_service import ActionRequest
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.task_repo import TaskRepository
from app.schemas.assets import (
    AssetHandleValidateRequest,
    AssetQueryRequest,
    AssetResolveForToolRequest,
)
from app.schemas.knowledge import KnowledgeSearchRequest
from app.schemas.memory import MemorySearchApiRequest
from app.schemas.tasks import ToolExecuteRequest, ToolExecuteResponse
from app.services.approvals import ApprovalService
from app.services.artifacts import ArtifactStore
from app.services.asset_broker import AssetBrokerService
from app.services.audit import AuditEventService
from app.services.design_alignment import SafetyDecisionService
from app.services.knowledge import KnowledgeService
from app.services.memory import MemoryService

if TYPE_CHECKING:
    from app.services.execution_boundary import ExecutionBoundaryService
    from app.services.mcp import MCPService
    from app.services.skill_plugin import SkillPluginService


@dataclass(frozen=True)
class ToolRunOutcome:
    result: dict[str, Any]
    artifacts: list[TaskArtifact]


_BROWSER_EXECUTABLE_PATH_ENV = "CYCBER_BROWSER_EXECUTABLE_PATH"
_BROWSER_CHANNEL_ENV = "CYCBER_BROWSER_CHANNEL"


def _browser_launch_options() -> dict[str, Any]:
    configured_path = _configured_browser_executable_path()
    if configured_path is not None:
        return {"executable_path": str(configured_path)}

    channel = os.environ.get(_BROWSER_CHANNEL_ENV, "").strip()
    if channel:
        return {"channel": channel}

    default_path = _default_browser_executable_path()
    if default_path is not None:
        return {"executable_path": str(default_path)}

    return {}


def _configured_browser_executable_path() -> Path | None:
    value = os.environ.get(_BROWSER_EXECUTABLE_PATH_ENV, "").strip()
    if not value:
        return None
    return Path(os.path.expandvars(value)).expanduser()


def _default_browser_executable_path() -> Path | None:
    for candidate in _browser_executable_candidates():
        if candidate.exists():
            return candidate
    return None


def _browser_executable_candidates() -> list[Path]:
    if os.name != "nt":
        return []

    bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    roots = [Path(base) for base in bases if base]
    return [
        *(root / "Google" / "Chrome" / "Application" / "chrome.exe" for root in roots),
        *(root / "Microsoft" / "Edge" / "Application" / "msedge.exe" for root in roots),
    ]


def _redact_browser_failure(reason: str) -> str:
    text = reason
    executable_path = _browser_launch_options().get("executable_path")
    if executable_path:
        text = text.replace(executable_path, "[REDACTED_BROWSER_PATH]")
    return str(redact(text))


class ToolRuntime:
    def __init__(
        self,
        *,
        repo: TaskRepository,
        artifact_store: ArtifactStore,
        approval_service: ApprovalService,
        asset_broker: AssetBrokerService,
        knowledge_service: KnowledgeService,
        memory_service: MemoryService,
        trace_service: TraceService,
        audit_service: AuditEventService,
        safety_decision_service: SafetyDecisionService,
        execution_boundary_service: ExecutionBoundaryService | None = None,
    ) -> None:
        self._repo = repo
        self._artifacts = artifact_store
        self._approvals = approval_service
        self._asset_broker = asset_broker
        self._knowledge = knowledge_service
        self._memory = memory_service
        self._trace = trace_service
        self._audit = audit_service
        self._safety_decisions = safety_decision_service
        self._boundary = execution_boundary_service
        self._skill_plugin: SkillPluginService | None = None
        self._mcp: MCPService | None = None

    def set_extension_services(
        self,
        *,
        skill_plugin_service: SkillPluginService | None = None,
        mcp_service: MCPService | None = None,
    ) -> None:
        self._skill_plugin = skill_plugin_service
        self._mcp = mcp_service

    async def ensure_builtin_tools(self) -> None:
        now = utc_now_iso()
        for tool in BUILTIN_TOOLS:
            await self._repo.upsert_tool({**tool, "created_at": now, "updated_at": now})
        if self._boundary is not None:
            await self._boundary.ensure_defaults(BUILTIN_TOOLS)

    async def list_tools(self) -> list[ToolDefinition]:
        return [ToolDefinition(**row) for row in await self._repo.list_tools()]

    async def get_tool(self, tool_name: str) -> ToolDefinition:
        row = await self._repo.get_tool(tool_name)
        if row is None:
            raise AppError(ErrorCode.TOOL_NOT_FOUND, "工具不存在", status_code=404)
        return ToolDefinition(**row)

    async def list_action_policies(self) -> list[Any]:
        if self._boundary is None:
            return []
        return await self._boundary.list_policies()

    async def boundary_for_tool_call(self, tool_call_id: str) -> dict[str, Any]:
        row = await self._repo.get_tool_call(tool_call_id)
        if row is None:
            raise AppError(ErrorCode.TOOL_NOT_FOUND, "工具调用不存在", status_code=404)
        decisions = (
            await self._boundary.list_decisions(tool_call_id)
            if self._boundary is not None
            else []
        )
        sandbox_profile = None
        if self._boundary is not None:
            for decision in reversed(decisions):
                if decision.sandbox_profile_id:
                    sandbox_profile = await self._boundary.sandbox_profile(
                        decision.sandbox_profile_id
                    )
                    break
        return {
            "tool_call": ToolCallRecord(**row),
            "decisions": decisions,
            "sandbox_profile": sandbox_profile,
        }

    async def dlp_reports_for_tool_call(self, tool_call_id: str) -> list[Any]:
        if await self._repo.get_tool_call(tool_call_id) is None:
            raise AppError(ErrorCode.TOOL_NOT_FOUND, "工具调用不存在", status_code=404)
        if self._boundary is None:
            return []
        return await self._boundary.list_dlp_reports(tool_call_id)

    async def execute(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None = None,
    ) -> ToolExecuteResponse:
        try:
            tool = await self.get_tool(request.tool_name)
        except AppError as exc:
            if exc.code == ErrorCode.TOOL_NOT_FOUND.value and self._boundary is not None:
                await self._boundary.decide_tool_action(
                    organization_id="org_default",
                    tool_name=request.tool_name,
                    source="unknown",
                    requested_risk_level=RiskLevel.R7,
                    args=request.args,
                    task_id=request.task_id,
                    member_id=request.member_id,
                    tool_call_id=None,
                    trace_id=trace_id,
                )
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "未知工具默认拒绝执行",
                    status_code=403,
                ) from exc
            raise
        if tool.status != "active":
            raise AppError(ErrorCode.TOOL_NOT_FOUND, "工具未启用", status_code=404)
        if request.idempotency_key:
            existing = await self._repo.get_tool_call_by_idempotency(request.idempotency_key)
            if existing is not None:
                if existing["tool_name"] != request.tool_name:
                    raise AppError(
                        ErrorCode.CONFLICT,
                        "idempotency_key 已被其他工具调用使用",
                        status_code=409,
                    )
                return await self._response_from_existing_call(existing)
        self._validate_args(tool, request.args)
        risk_level = _risk_for(tool, request.args)

        task = await self._task_for_request(request)
        organization_id = task["organization_id"] if task else "org_default"
        tool_call_id = new_id("call")
        now = utc_now_iso()
        handle_ids = _handle_ids_from_args(request.args)
        span_id = await self._start_span(
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
            await self._repo.insert_tool_call(
                {
                    "tool_call_id": tool_call_id,
                    "organization_id": organization_id,
                    "task_id": request.task_id,
                    "step_id": request.step_id,
                    "tool_name": tool.tool_name,
                    "source": tool.source,
                    "status": "running",
                    "idempotency_key": request.idempotency_key,
                    "args_redacted": redact(request.args),
                    "handle_ids": handle_ids,
                    "risk_level": risk_level.value,
                    "trace_id": trace_id,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            boundary_decision = None
            if self._boundary is not None:
                boundary_decision = await self._boundary.decide_tool_action(
                    organization_id=organization_id,
                    tool_name=tool.tool_name,
                    source=tool.source,
                    requested_risk_level=risk_level,
                    args=request.args,
                    task_id=request.task_id,
                    member_id=request.member_id,
                    tool_call_id=tool_call_id,
                    trace_id=trace_id,
                )
                risk_level = _max_risk(risk_level, boundary_decision.effective_risk_level)
                boundary_snapshot = {
                    **boundary_decision.policy_snapshot,
                    "boundary_decision_id": boundary_decision.decision_id,
                    "boundary_decision": boundary_decision.decision,
                    "boundary_reason_codes": boundary_decision.reason_codes,
                    "required_controls": boundary_decision.required_controls,
                }
                await self._repo.update_tool_call(
                    tool_call_id,
                    {
                        "policy_snapshot": boundary_snapshot,
                        "risk_level": risk_level.value,
                        "updated_at": utc_now_iso(),
                    },
                )
                if boundary_decision.decision == "deny" and not _should_defer_to_safety(
                    tool.tool_name,
                    boundary_decision.reason_codes,
                ):
                    raise AppError(
                        ErrorCode.TOOL_PERMISSION_DENIED,
                        "执行边界策略拒绝该工具动作",
                        status_code=403,
                        details={
                            "decision_id": boundary_decision.decision_id,
                            "reason_codes": boundary_decision.reason_codes,
                        },
                    )
            safety_decision = await self._safety_decisions.evaluate(
                _safety_request_for_tool(
                    request=request,
                    tool=tool,
                    risk_level=risk_level,
                    organization_id=organization_id,
                    handle_ids=handle_ids,
                ),
                trace_id=trace_id,
            )
            risk_level = _max_risk(risk_level, safety_decision.risk_level)
            policy_snapshot = {
                **(
                    {
                        **boundary_decision.policy_snapshot,
                        "boundary_decision_id": boundary_decision.decision_id,
                        "boundary_decision": boundary_decision.decision,
                        "boundary_reason_codes": boundary_decision.reason_codes,
                    }
                    if boundary_decision is not None
                    else {}
                ),
                "risk_level": risk_level.value,
                "required_controls": safety_decision.required_controls,
                "policy_sources": safety_decision.policy_sources,
                "decision": safety_decision.decision,
            }
            await self._repo.update_tool_call(
                tool_call_id,
                {
                    "safety_decision_id": safety_decision.safety_decision_id,
                    "safety_decision": safety_decision.model_dump(mode="json"),
                    "policy_snapshot": policy_snapshot,
                    "risk_level": risk_level.value,
                    "updated_at": utc_now_iso(),
                },
            )
            if not safety_decision.allowed:
                raise AppError(
                    ErrorCode.SAFETY_BLOCKED,
                    "安全策略阻断了该工具动作",
                    status_code=403,
                    details={
                        "safety_decision_id": safety_decision.safety_decision_id,
                        "reason": safety_decision.reason,
                    },
                )
            approval = await self._approval_if_required(
                request=request,
                tool=tool,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                risk_level=risk_level,
                trace_id=trace_id,
            )
            if approval is not None:
                await self._repo.update_tool_call(
                    tool_call_id,
                    {
                        "status": "approval_required",
                        "approval_id": approval.approval_id,
                        "updated_at": utc_now_iso(),
                    },
                )
                record = await self._tool_call_record(tool_call_id, request.task_id)
                await self._end_span(
                    span_id,
                    output_data={
                        "status": "approval_required",
                        "approval_id": approval.approval_id,
                    },
                )
                return ToolExecuteResponse(tool_call=record, approval=approval)

            resolved_asset_refs = await self._resolve_handles_for_tool(request, trace_id=trace_id)
            await self._repo.update_tool_call(
                tool_call_id,
                {"resolved_asset_refs": resolved_asset_refs, "updated_at": utc_now_iso()},
            )
            if tool.source == "mcp":
                outcome = await self._execute_mcp_tool(
                    request,
                    tool=tool,
                    tool_call_id=tool_call_id,
                    organization_id=organization_id,
                    safety_decision_id=safety_decision.safety_decision_id,
                    policy_snapshot=policy_snapshot,
                    resolved_asset_refs=resolved_asset_refs,
                    trace_id=trace_id,
                )
            elif tool.source == "skill":
                outcome = await self._execute_skill_tool(
                    request,
                    tool=tool,
                    tool_call_id=tool_call_id,
                    organization_id=organization_id,
                    trace_id=trace_id,
                )
            else:
                outcome = await self._execute_builtin(
                    request,
                    tool_call_id=tool_call_id,
                    organization_id=organization_id,
                    trace_id=trace_id,
                )
            artifact_ids = [artifact.artifact_id for artifact in outcome.artifacts]
            redacted_result: dict[str, Any] = dict(redact(outcome.result))
            if self._boundary is not None:
                dlp = await self._boundary.scan_output(
                    organization_id=organization_id,
                    source_type="tool_result",
                    source_id=tool_call_id,
                    scan_target="result",
                    value=outcome.result,
                    tool_call_id=tool_call_id,
                    task_id=request.task_id,
                    trace_id=trace_id,
                )
                redacted_result = dict(redact(dlp.redacted_value))
            await self._repo.update_tool_call(
                tool_call_id,
                {
                    "status": "completed",
                    "result_redacted": redacted_result,
                    "artifact_ids": artifact_ids,
                    "updated_at": utc_now_iso(),
                },
            )
            await self._audit.write_event(
                actor_type="system",
                action="tool.called",
                object_type="tool_call",
                object_id=tool_call_id,
                summary="工具调用完成",
                risk_level=risk_level,
                payload={
                    "tool_name": tool.tool_name,
                    "task_id": request.task_id,
                    "artifact_ids": artifact_ids,
                },
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                output_data={"status": "completed", "artifact_count": len(outcome.artifacts)},
            )
            return ToolExecuteResponse(
                tool_call=await self._tool_call_record(tool_call_id, request.task_id),
                artifacts=outcome.artifacts,
                result=redacted_result,
            )
        except Exception as exc:
            await self._repo.update_tool_call(
                tool_call_id,
                {
                    "status": "failed",
                    "error_code": getattr(exc, "code", ErrorCode.TOOL_EXECUTION_FAILED.value),
                    "error_summary": str(redact(str(exc))),
                    "updated_at": utc_now_iso(),
                },
            )
            await self._audit.write_event(
                actor_type="system",
                action="tool.blocked" if isinstance(exc, AppError) else "tool.failed",
                object_type="tool_call",
                object_id=tool_call_id,
                summary="工具调用失败",
                risk_level=risk_level,
                payload={
                    "tool_name": tool.tool_name,
                    "error": str(redact(str(exc))),
                },
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.TOOL_EXECUTION_FAILED)},
            )
            raise

    async def _approval_if_required(
        self,
        *,
        request: ToolExecuteRequest,
        tool: ToolDefinition,
        tool_call_id: str,
        organization_id: str,
        risk_level: RiskLevel,
        trace_id: str | None,
    ) -> ApprovalDetail | None:
        if _risk_order(risk_level) < _risk_order(RiskLevel.R3):
            return None
        if request.approval_id:
            approval = await self._approvals.get(request.approval_id)
            if approval.status in {"approved", "edited"}:
                if approval.edited_payload:
                    request.args.update(_normalize_approval_args(approval.edited_payload))
                return None
            if approval.status == "denied":
                raise AppError(ErrorCode.APPROVAL_DENIED, "审批已拒绝", status_code=409)
            raise AppError(ErrorCode.TOOL_APPROVAL_REQUIRED, "工具动作需要审批", status_code=409)
        task_id = request.task_id
        if task_id is None:
            raise AppError(
                ErrorCode.TOOL_APPROVAL_REQUIRED,
                "高风险工具必须绑定任务并创建审批",
                status_code=409,
            )
        return await self._approvals.create_approval(
            task_id=task_id,
            organization_id=organization_id,
            step_id=request.step_id,
            tool_call_id=tool_call_id,
            requested_action=tool.tool_name,
            risk_level=risk_level,
            summary=f"需要确认执行 {tool.tool_name}",
            payload=redact(request.args),
            trace_id=trace_id,
        )

    async def _execute_builtin(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        name = request.tool_name
        if name.startswith("file."):
            return await self._execute_file_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("knowledge."):
            return await self._execute_knowledge_tool(request, trace_id=trace_id)
        if name.startswith("memory."):
            return await self._execute_memory_tool(request, trace_id=trace_id)
        if name.startswith("asset."):
            return await self._execute_asset_tool(request, trace_id=trace_id)
        if name.startswith("browser."):
            return await self._execute_browser_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("terminal."):
            return await self._execute_terminal_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name == "account.create_draft_artifact":
            return await self._write_artifact_result(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                content=str(request.args.get("draft") or request.args.get("content") or ""),
                display_name=str(request.args.get("display_name") or "account-draft.md"),
                artifact_type="markdown",
                trace_id=trace_id,
                result_key="draft_artifact",
            )
        if name == "hardware.query_status":
            return ToolRunOutcome(
                result={
                    "status": "unknown",
                    "message": "本阶段只返回本地配置状态，不控制硬件。",
                },
                artifacts=[],
            )
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "工具不存在", status_code=404)

    async def _execute_mcp_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool: ToolDefinition,
        tool_call_id: str,
        organization_id: str,
        safety_decision_id: str | None,
        policy_snapshot: dict[str, Any],
        resolved_asset_refs: list[dict[str, Any]],
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if self._mcp is None:
            raise AppError(ErrorCode.MCP_UNAVAILABLE, "MCP 运行时未初始化", status_code=500)
        result = await self._mcp.call_tool(
            tool=tool,
            request=request,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            safety_decision_id=safety_decision_id,
            policy_snapshot=policy_snapshot,
            resolved_asset_refs=resolved_asset_refs,
            trace_id=trace_id,
        )
        return ToolRunOutcome(result=result, artifacts=[])

    async def _execute_skill_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool: ToolDefinition,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        del tool_call_id, organization_id
        if self._skill_plugin is None:
            raise AppError(ErrorCode.SKILL_RUN_FAILED, "Skill 运行时未初始化", status_code=500)
        skill_id = tool.skill_id or str(tool.adapter_config.get("skill_id") or "")
        if not skill_id:
            raise AppError(ErrorCode.SKILL_NOT_FOUND, "工具未绑定 Skill", status_code=404)
        run = await self._skill_plugin.run_skill(
            skill_id,
            task_id=request.task_id,
            step_id=request.step_id,
            owner_member_id=request.member_id,
            input_data=request.args,
            matched_reason="tool_runtime",
            trace_id=trace_id,
        )
        return ToolRunOutcome(result={"skill_run": run.model_dump(mode="json")}, artifacts=[])

    async def _execute_file_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if not request.task_id:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "文件工具必须绑定任务",
                status_code=422,
            )
        name = request.tool_name
        path_arg = str(request.args.get("path") or "outputs/tool-output.txt")
        path = self._artifacts.resolve_task_relative_path(request.task_id, path_arg)
        if name == "file.write":
            if path.exists() and not bool(request.args.get("overwrite")):
                raise AppError(ErrorCode.CONFLICT, "文件已存在，覆盖需要审批", status_code=409)
            content = str(request.args.get("content") or "")
            artifact = await self._artifacts.write_text(
                task_id=request.task_id,
                organization_id=organization_id,
                step_id=request.step_id,
                tool_call_id=tool_call_id,
                display_name=path.name,
                content=content,
                artifact_type="text",
                subdir=path.parent.relative_to(self._artifacts.task_dir(request.task_id)).as_posix(),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result={"uri": artifact.uri}, artifacts=[artifact])
        if name == "file.read":
            if not path.exists() or not path.is_file():
                raise AppError(ErrorCode.NOT_FOUND, "文件不存在", status_code=404)
            return ToolRunOutcome(
                result={"content": str(redact(path.read_text(encoding="utf-8")))},
                artifacts=[],
            )
        if name == "file.list":
            if not path.exists():
                return ToolRunOutcome(result={"items": []}, artifacts=[])
            items = [child.name for child in sorted(path.iterdir())]
            return ToolRunOutcome(result={"items": items}, artifacts=[])
        if name == "file.hash":
            if not path.exists() or not path.is_file():
                raise AppError(ErrorCode.NOT_FOUND, "文件不存在", status_code=404)
            return ToolRunOutcome(
                result={"checksum": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()},
                artifacts=[],
            )
        if name in {"file.copy", "file.move"}:
            if not path.exists() or not path.is_file():
                raise AppError(ErrorCode.NOT_FOUND, "文件不存在", status_code=404)
            destination_arg = str(request.args.get("destination") or "")
            if not destination_arg:
                raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "destination 必填", status_code=422)
            destination = self._artifacts.resolve_task_relative_path(
                request.task_id,
                destination_arg,
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            if name == "file.copy":
                shutil.copy2(path, destination)
            else:
                shutil.move(str(path), str(destination))
            return ToolRunOutcome(
                result={"path": _relative_to_task(destination, request.task_id, self._artifacts)},
                artifacts=[],
            )
        if name == "file.delete":
            if path.exists():
                path.unlink()
            return ToolRunOutcome(result={"deleted": True, "path": path_arg}, artifacts=[])
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "文件工具不存在", status_code=404)

    async def _execute_knowledge_tool(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if request.tool_name == "knowledge.search":
            search_response = await self._knowledge.search(
                KnowledgeSearchRequest(
                    subject_type="member",
                    subject_id=request.member_id,
                    asset_id=request.args.get("asset_id"),
                    query=str(request.args.get("query") or ""),
                    limit=int(request.args.get("limit") or 5),
                ),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=search_response.model_dump(mode="json"), artifacts=[])
        if request.tool_name == "knowledge.get_chunk":
            chunk_id = str(request.args.get("chunk_id") or "")
            if not chunk_id:
                raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "chunk_id 必填", status_code=422)
            chunk = await self._knowledge.get_chunk(
                chunk_id,
                subject_type="member",
                subject_id=request.member_id,
                task_id=request.task_id,
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=chunk.model_dump(mode="json"), artifacts=[])
        if request.tool_name == "knowledge.reindex":
            source_id = str(request.args.get("source_id") or "")
            index_response = await self._knowledge.index_source(source_id, trace_id=trace_id)
            return ToolRunOutcome(result=index_response.model_dump(mode="json"), artifacts=[])
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "知识库工具不存在", status_code=404)

    async def _execute_memory_tool(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if request.tool_name == "memory.search":
            search_response = await self._memory.search(
                MemorySearchApiRequest(
                    query=str(request.args.get("query") or ""),
                    member_id=request.member_id,
                    limit=int(request.args.get("limit") or 5),
                ),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=search_response.model_dump(mode="json"), artifacts=[])
        if request.tool_name == "memory.write_candidate":
            extract_response = await self._memory.extract_from_text(
                str(request.args.get("text") or ""),
                member_id=request.member_id,
                conversation_id=None,
                trace_id=trace_id,
                force=True,
            )
            return ToolRunOutcome(result=extract_response.model_dump(mode="json"), artifacts=[])
        if request.tool_name == "memory.correct":
            extract_response = await self._memory.extract_from_text(
                str(request.args.get("text") or ""),
                member_id=request.member_id,
                conversation_id=None,
                trace_id=trace_id,
                force=True,
            )
            return ToolRunOutcome(result=extract_response.model_dump(mode="json"), artifacts=[])
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "记忆工具不存在", status_code=404)

    async def _execute_asset_tool(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if request.tool_name in {"asset.query", "asset.request_handle"}:
            query_response = await self._asset_broker.query(
                AssetQueryRequest(
                    subject_type="member",
                    subject_id=request.member_id,
                    asset_type=request.args.get("asset_type"),
                    requested_actions=request.args.get("requested_actions") or ["read"],
                    keywords=request.args.get("keywords") or [],
                    task_id=request.task_id,
                ),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=query_response.model_dump(mode="json"), artifacts=[])
        if request.tool_name == "asset.validate_handle":
            handle_id = str(request.args.get("handle_id") or "")
            validate_response = await self._asset_broker.validate_handle(
                handle_id,
                AssetHandleValidateRequest(
                    subject_type="member",
                    subject_id=request.member_id,
                    action=str(request.args.get("action") or "read"),
                    task_id=request.task_id,
                ),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=validate_response.model_dump(mode="json"), artifacts=[])
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "资产工具不存在", status_code=404)

    async def _execute_browser_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        url = str(request.args.get("url") or "")
        if not url.startswith(("http://", "https://")):
            raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "url 必须是 http(s)", status_code=422)
        if request.tool_name == "browser.open":
            return ToolRunOutcome(result={"url": url, "status": "opened"}, artifacts=[])
        if request.tool_name == "browser.snapshot":
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.get(url)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AppError(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    "浏览器快照获取失败",
                    status_code=502,
                    details={"reason": str(redact(str(exc)))},
                ) from exc
            text = response.text[:5000]
            return ToolRunOutcome(
                result={
                    "url": url,
                    "content_preview": str(redact(text)),
                    "untrusted_external_content": True,
                },
                artifacts=[],
            )
        if request.tool_name == "browser.screenshot":
            return await self._browser_screenshot(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                url=url,
                trace_id=trace_id,
            )
        if request.tool_name == "browser.download":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "下载必须绑定任务",
                    status_code=422,
                )
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(url)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                raise AppError(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    "浏览器下载失败",
                    status_code=502,
                    details={"reason": str(redact(str(exc)))},
                ) from exc
            artifact = await self._artifacts.write_bytes(
                task_id=request.task_id,
                organization_id=organization_id,
                step_id=request.step_id,
                tool_call_id=tool_call_id,
                display_name=str(request.args.get("display_name") or "download.bin"),
                content=response.content,
                artifact_type="download",
                content_type=response.headers.get("content-type") or "application/octet-stream",
                subdir="quarantine",
                metadata={"url": url, "untrusted_external_content": True},
                trace_id=trace_id,
            )
            return ToolRunOutcome(
                result={"download": artifact.model_dump(mode="json")},
                artifacts=[artifact],
            )
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "浏览器工具不存在", status_code=404)

    async def _browser_screenshot(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        url: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if not request.task_id:
            raise AppError(ErrorCode.TOOL_PERMISSION_DENIED, "截图必须绑定任务", status_code=422)
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - optional dependency
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "当前环境未安装 Playwright，无法截图",
                status_code=500,
            ) from exc
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**_browser_launch_options())
                try:
                    page = await browser.new_page()
                    await page.goto(url, wait_until="networkidle", timeout=15000)
                    data = await page.screenshot(full_page=True)
                finally:
                    await browser.close()
        except Exception as exc:  # pragma: no cover - requires browser runtime
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "浏览器截图失败",
                status_code=500,
                details={"reason": _redact_browser_failure(str(exc))},
            ) from exc
        artifact = await self._artifacts.write_bytes(
            task_id=request.task_id,
            organization_id=organization_id,
            step_id=request.step_id,
            tool_call_id=tool_call_id,
            display_name="screenshot.png",
            content=data,
            artifact_type="screenshot",
            content_type="image/png",
            subdir="screenshots",
            metadata={"url": url, "untrusted_external_content": True},
            trace_id=trace_id,
        )
        return ToolRunOutcome(
            result={"artifact_id": artifact.artifact_id, "url": url},
            artifacts=[artifact],
        )

    async def _execute_terminal_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if request.tool_name == "terminal.stop":
            return ToolRunOutcome(
                result={
                    "status": "not_running",
                    "message": "当前运行时以同步子进程执行，暂无可停止的后台终端进程。",
                },
                artifacts=[],
            )
        if request.tool_name == "terminal.read_log":
            return await self._read_terminal_log(request)
        if request.tool_name != "terminal.run":
            return ToolRunOutcome(result={"status": "not_running"}, artifacts=[])
        if not request.task_id:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "终端工具必须绑定任务",
                status_code=422,
            )
        command = str(request.args.get("command") or "")
        if request.args.get("cwd"):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "终端工具不接受自定义 cwd，只能在任务工件沙箱中执行",
                status_code=403,
            )
        terminal_policy = (
            self._boundary.classify_terminal_command(command)
            if self._boundary is not None
            else _terminal_command_policy(command)
        )
        if terminal_policy["decision"] == "deny":
            raise AppError(
                ErrorCode.TOOL_OUTPUT_BLOCKED,
                "危险终端命令已被阻断",
                status_code=403,
                details={"reason": terminal_policy["reason"]},
        )
        if self._boundary is None:
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "执行边界服务未初始化，拒绝运行终端命令",
                status_code=500,
            )
        cwd = self._artifacts.task_dir(request.task_id)
        cwd.mkdir(parents=True, exist_ok=True)
        sandbox_profile = await self._boundary.sandbox_profile()
        sandbox_result = await self._boundary.run_terminal_command(
            organization_id=organization_id,
            task_id=request.task_id,
            command=command,
            cwd=cwd,
            timeout_seconds=request.args.get("timeout_seconds"),
            max_output_bytes=request.args.get("max_output_bytes"),
            tool_call_id=tool_call_id,
            trace_id=trace_id,
        )
        output = sandbox_result.output
        redacted_output = str(redact(output))
        dlp_report_id = None
        dlp = await self._boundary.scan_output(
            organization_id=organization_id,
            source_type="terminal_output",
            source_id=tool_call_id,
            scan_target="stdout_stderr",
            value=output,
            tool_call_id=tool_call_id,
            task_id=request.task_id,
            trace_id=trace_id,
        )
        redacted_output = str(dlp.redacted_value)
        dlp_report_id = dlp.report.dlp_report_id
        sandbox_profile_result = sandbox_result.sandbox_profile_result()
        if sandbox_profile is not None:
            sandbox_profile_result["profile_id"] = sandbox_profile.profile_id
        sandbox_metadata = {
            "sandbox": "task_artifact",
            "sandbox_profile": {
                **sandbox_profile_result,
                "cwd": "task_artifact_sandbox",
            },
            "backend_status": sandbox_result.backend_status,
            "fallback_chain": sandbox_result.fallback_chain,
            "degraded_reason": sandbox_result.degraded_reason,
            "resource_usage": sandbox_result.resource_usage,
            "cleanup": sandbox_result.cleanup,
            "env_policy": sandbox_result.env_policy,
            "filesystem_policy": sandbox_result.filesystem_policy,
            "network_policy": sandbox_result.network_policy,
            "output_truncated": sandbox_result.output_truncated,
            "timed_out": sandbox_result.timed_out,
            "command_class": terminal_policy["command_class"],
            "policy_snapshot": terminal_policy,
            "dlp_report_id": dlp_report_id,
            "cwd": "task_artifact_sandbox",
        }
        artifact = await self._artifacts.write_text(
            task_id=request.task_id,
            organization_id=organization_id,
            step_id=request.step_id,
            tool_call_id=tool_call_id,
            display_name="terminal.log",
            content=redacted_output,
            artifact_type="terminal_log",
            subdir="logs",
            metadata=sandbox_metadata,
            trace_id=trace_id,
        )
        await self._update_terminal_policy_snapshot(
            tool_call_id,
            sandbox_result=sandbox_result,
            log_artifact_id=artifact.artifact_id,
            dlp_report_id=dlp_report_id,
        )
        if sandbox_result.timed_out:
            raise AppError(
                ErrorCode.TOOL_TIMEOUT,
                "终端命令超时，沙箱已尝试终止进程树",
                status_code=504,
                details={
                    "sandbox_profile": sandbox_profile_result,
                    "cleanup": sandbox_result.cleanup,
                },
            )
        return ToolRunOutcome(
            result={
                "exit_code": sandbox_result.exit_code,
                "log_artifact_id": artifact.artifact_id,
                "sandbox_profile": sandbox_profile_result,
                "policy_snapshot": terminal_policy,
                "backend_status": sandbox_result.backend_status,
                "fallback_chain": sandbox_result.fallback_chain,
                "degraded_reason": sandbox_result.degraded_reason,
                "output_truncated": sandbox_result.output_truncated,
                "timed_out": sandbox_result.timed_out,
                "resource_usage": sandbox_result.resource_usage,
                "cleanup": sandbox_result.cleanup,
                "dlp_report_id": dlp_report_id,
            },
            artifacts=[artifact],
        )

    async def _update_terminal_policy_snapshot(
        self,
        tool_call_id: str,
        *,
        sandbox_result: Any,
        log_artifact_id: str,
        dlp_report_id: str | None,
    ) -> None:
        row = await self._repo.get_tool_call(tool_call_id)
        if row is None:
            return
        snapshot = dict(row.get("policy_snapshot") or {})
        snapshot["terminal_sandbox_result"] = {
            "backend": sandbox_result.backend,
            "backend_status": sandbox_result.backend_status,
            "fallback_chain": sandbox_result.fallback_chain,
            "degraded_reason": sandbox_result.degraded_reason,
            "timed_out": sandbox_result.timed_out,
            "output_truncated": sandbox_result.output_truncated,
            "resource_usage": sandbox_result.resource_usage,
            "cleanup": sandbox_result.cleanup,
            "log_artifact_id": log_artifact_id,
            "dlp_report_id": dlp_report_id,
        }
        await self._repo.update_tool_call(
            tool_call_id,
            {"policy_snapshot": redact(snapshot), "updated_at": utc_now_iso()},
        )

    async def _read_terminal_log(self, request: ToolExecuteRequest) -> ToolRunOutcome:
        artifact_id = request.args.get("artifact_id")
        if artifact_id:
            artifact, preview = await self._artifacts.read_preview(str(artifact_id))
            if artifact.artifact_type != "terminal_log":
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "指定工件不是终端日志",
                    status_code=403,
                )
            return ToolRunOutcome(
                result={
                    "log_artifact_id": artifact.artifact_id,
                    "content_preview": preview,
                },
                artifacts=[],
            )
        if not request.task_id:
            raise AppError(
                ErrorCode.TOOL_SCHEMA_INVALID,
                "读取终端日志需要 task_id 或 artifact_id",
                status_code=422,
            )
        logs = [
            artifact
            for artifact in await self._repo.list_artifacts(request.task_id)
            if artifact["artifact_type"] == "terminal_log"
        ]
        if not logs:
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "终端日志不存在", status_code=404)
        artifact, preview = await self._artifacts.read_preview(logs[-1]["artifact_id"])
        return ToolRunOutcome(
            result={"log_artifact_id": artifact.artifact_id, "content_preview": preview},
            artifacts=[],
        )

    async def _write_artifact_result(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        content: str,
        display_name: str,
        artifact_type: str,
        trace_id: str | None,
        result_key: str,
        subdir: str = "outputs",
    ) -> ToolRunOutcome:
        if not request.task_id:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "写入工件必须绑定任务",
                status_code=422,
            )
        artifact = await self._artifacts.write_text(
            task_id=request.task_id,
            organization_id=organization_id,
            step_id=request.step_id,
            tool_call_id=tool_call_id,
            display_name=display_name,
            content=content,
            artifact_type=artifact_type,
            subdir=subdir,
            trace_id=trace_id,
        )
        return ToolRunOutcome(
            result={result_key: artifact.model_dump(mode="json")},
            artifacts=[artifact],
        )

    async def _resolve_handles_for_tool(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None,
    ) -> list[dict[str, Any]]:
        action = _asset_action_for_tool(request.tool_name, request.args)
        resolved: list[dict[str, Any]] = []
        for handle_id in _handle_ids_from_args(request.args):
            item = await self._asset_broker.resolve_for_tool(
                handle_id,
                AssetResolveForToolRequest(
                    subject_id=request.member_id,
                    action=action,
                    tool_name=request.tool_name,
                    task_id=request.task_id,
                    conversation_id=None,
                ),
                trace_id=trace_id,
            )
            resolved.append(
                {
                    "handle_id": item.handle_id,
                    "asset_id": item.asset_id,
                    "asset_type": item.asset_type.value,
                    "action": item.action,
                    "has_secret": item.has_secret,
                }
            )
        return resolved

    async def _task_for_request(self, request: ToolExecuteRequest) -> dict[str, Any] | None:
        if not request.task_id:
            return None
        task = await self._repo.get_task(request.task_id)
        if task is None:
            raise AppError(ErrorCode.NOT_FOUND, "任务不存在", status_code=404)
        return task

    def _validate_args(self, tool: ToolDefinition, args: dict[str, Any]) -> None:
        required = tool.input_schema.get("required", [])
        missing = [name for name in required if name not in args]
        if missing:
            raise AppError(
                ErrorCode.TOOL_SCHEMA_INVALID,
                "工具参数缺少必填字段",
                status_code=422,
                details={"missing": missing, "tool_name": tool.tool_name},
            )

    async def _tool_call_record(self, tool_call_id: str, task_id: str | None) -> ToolCallRecord:
        row = await self._repo.get_tool_call(tool_call_id)
        if row is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "工具调用记录无法读取", status_code=500)
        return ToolCallRecord(**row)

    async def _response_from_existing_call(
        self,
        row: dict[str, Any],
    ) -> ToolExecuteResponse:
        approval = None
        if row.get("approval_id"):
            approval_row = await self._repo.get_approval(row["approval_id"])
            approval = ApprovalDetail(**approval_row) if approval_row is not None else None
        artifacts = [
            TaskArtifact(**artifact)
            for artifact in await self._repo.list_artifacts_by_ids(row.get("artifact_ids", []))
        ]
        return ToolExecuteResponse(
            tool_call=ToolCallRecord(**row),
            approval=approval,
            artifacts=artifacts,
            result=row.get("result_redacted", {}),
        )

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        metadata: dict[str, Any],
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            metadata=metadata,
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


def _risk_for(tool: ToolDefinition, args: dict[str, Any]) -> RiskLevel:
    policy = tool.risk_policy
    if tool.tool_name == "file.write" and args.get("overwrite"):
        return RiskLevel(policy.get("overwrite_true", "R3"))
    return RiskLevel(policy.get("default", "R1"))


def _safety_request_for_tool(
    *,
    request: ToolExecuteRequest,
    tool: ToolDefinition,
    risk_level: RiskLevel,
    organization_id: str,
    handle_ids: list[str],
) -> ActionRequest:
    destination = (
        request.args.get("url")
        or request.args.get("destination")
        or request.args.get("command")
        or request.args.get("path")
    )
    return ActionRequest(
        actor_type="member",
        actor_id=request.member_id,
        organization_id=organization_id,
        task_id=request.task_id,
        action_type="tool",
        action=tool.tool_name,
        object_type="tool",
        object_id=tool.tool_name,
        tool_name=tool.tool_name,
        payload_summary=redact(request.args),
        payload=request.args,
        asset_handles=handle_ids,
        destination=str(destination) if destination is not None else None,
        risk_hints=[risk_level.value, tool.source, tool.trust_level],
        untrusted_refs=[{"source": tool.source}] if tool.source in {"mcp", "skill"} else [],
    )


def _risk_order(risk: RiskLevel) -> int:
    return int(risk.value.removeprefix("R"))


def _max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    return left if _risk_order(left) >= _risk_order(right) else right


def _should_defer_to_safety(tool_name: str, reason_codes: list[str]) -> bool:
    if tool_name != "terminal.run":
        return False
    if "terminal_custom_cwd_denied" in reason_codes:
        return False
    return any(
        reason in reason_codes
        for reason in (
            "terminal_destructive_command_denied",
            "terminal_sensitive_path_denied",
            "sensitive_path_denied",
        )
    )


def _handle_ids_from_args(args: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    if args.get("handle_id"):
        ids.append(str(args["handle_id"]))
    for value in args.get("handle_ids") or []:
        ids.append(str(value))
    return ids


def _asset_action_for_tool(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name.startswith("knowledge."):
        return "read_knowledge"
    if tool_name == "asset.validate_handle":
        return str(args.get("action") or "read")
    if tool_name.startswith("account."):
        return "draft_post"
    if tool_name.startswith("hardware."):
        return "query_status"
    return str(args.get("action") or "read")


def _normalize_approval_args(payload: dict[str, Any]) -> dict[str, Any]:
    nested_args = payload.get("args")
    if isinstance(nested_args, dict):
        return dict(nested_args)
    return dict(payload)


def _terminal_command_policy(command: str) -> dict[str, str]:
    lowered = command.lower()
    wrapped = f" {lowered} "
    destructive = [
        "remove-item",
        " rm ",
        "rm -",
        "del /",
        "format ",
        "shutdown",
        "reboot",
        "reg delete",
        "git reset --hard",
        "cipher ",
        "diskpart",
        "mkfs",
        "dd if=",
        "bcdedit",
        "takeown ",
        "icacls ",
    ]
    mutation = [
        " set-item ",
        " new-item ",
        " move-item ",
        " copy-item ",
        "ren ",
        "rename-item",
        "chmod ",
        "chown ",
        "pip install",
        "npm install",
    ]
    sensitive_paths = [
        r"(^|[\\/\s])secrets([\\/\s]|$)",
        r"(^|[\\/\s])\.env(\.local)?([\\/\s]|$)",
        r"master\.key",
        r"local_secrets\.json",
        r"c:\\windows",
        r"\\windows\\system32",
        r"(^|[\s])/(etc|bin|sbin|usr|var|root)(/|\s|$)",
    ]
    if any(re.search(pattern, lowered) for pattern in sensitive_paths):
        return {"decision": "deny", "reason": "sensitive_path", "command_class": "R7"}
    if any(item in wrapped for item in destructive):
        return {"decision": "deny", "reason": "destructive_command", "command_class": "R6"}
    if any(item in wrapped for item in mutation):
        return {"decision": "allow", "reason": "mutation_requires_approval", "command_class": "R5"}
    return {"decision": "allow", "reason": "sandboxed_terminal", "command_class": "R5"}


def _dangerous_command(command: str) -> bool:
    return _terminal_command_policy(command)["decision"] == "deny"


def _relative_to_task(path: Path, task_id: str, store: ArtifactStore) -> str:
    return path.relative_to(store.task_dir(task_id)).as_posix()


BUILTIN_TOOLS: list[dict[str, Any]] = [
    {
        "tool_name": "file.list",
        "display_name": "List files",
        "description": "列出任务工件目录中的文件",
        "input_schema": {"required": ["path"]},
        "output_schema": {},
        "risk_policy": {"default": "R1"},
        "required_handle_types": [],
        "source": "builtin",
        "status": "active",
    },
    {
        "tool_name": "file.read",
        "display_name": "Read file",
        "description": "读取任务工件目录中的文件",
        "input_schema": {"required": ["path"]},
        "output_schema": {},
        "risk_policy": {"default": "R1"},
        "required_handle_types": [],
        "source": "builtin",
        "status": "active",
    },
    {
        "tool_name": "file.write",
        "display_name": "Write file",
        "description": "写入任务工件",
        "input_schema": {"required": ["content"]},
        "output_schema": {},
        "risk_policy": {"default": "R2", "overwrite_true": "R3"},
        "required_handle_types": [],
        "source": "builtin",
        "status": "active",
    },
    {
        "tool_name": "file.copy",
        "display_name": "Copy file",
        "description": "复制任务工件目录中的文件",
        "input_schema": {"required": ["path", "destination"]},
        "output_schema": {},
        "risk_policy": {"default": "R2"},
        "required_handle_types": [],
        "source": "builtin",
        "status": "active",
    },
    {
        "tool_name": "file.move",
        "display_name": "Move file",
        "description": "移动任务工件目录中的文件",
        "input_schema": {"required": ["path", "destination"]},
        "output_schema": {},
        "risk_policy": {"default": "R3"},
        "required_handle_types": [],
        "source": "builtin",
        "status": "active",
    },
    {
        "tool_name": "file.hash",
        "display_name": "Hash file",
        "description": "计算文件 checksum",
        "input_schema": {"required": ["path"]},
        "output_schema": {},
        "risk_policy": {"default": "R1"},
        "required_handle_types": [],
        "source": "builtin",
        "status": "active",
    },
    {
        "tool_name": "file.delete",
        "display_name": "Delete file",
        "description": "删除任务工件目录中的文件",
        "input_schema": {"required": ["path"]},
        "output_schema": {},
        "risk_policy": {"default": "R5"},
        "required_handle_types": [],
        "source": "builtin",
        "status": "active",
    },
    *[
        {
            "tool_name": name,
            "display_name": name,
            "description": f"Builtin tool {name}",
            "input_schema": {},
            "output_schema": {},
            "risk_policy": {"default": risk},
            "required_handle_types": [],
            "source": "builtin",
            "status": "active",
        }
        for name, risk in {
            "knowledge.search": "R1",
            "knowledge.get_chunk": "R1",
            "knowledge.reindex": "R2",
            "memory.search": "R1",
            "memory.write_candidate": "R2",
            "memory.correct": "R2",
            "asset.query": "R1",
            "asset.request_handle": "R1",
            "asset.validate_handle": "R1",
            "browser.open": "R2",
            "browser.snapshot": "R2",
            "browser.screenshot": "R3",
            "browser.download": "R3",
            "terminal.run": "R5",
            "terminal.stop": "R2",
            "terminal.read_log": "R1",
            "account.create_draft_artifact": "R2",
            "hardware.query_status": "R1",
        }.items()
    ],
]
