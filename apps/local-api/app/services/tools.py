from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote_plus

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
from app.services.asset_broker import AssetBrokerService, ToolResolvedAsset
from app.services.audit import AuditEventService
from app.services.browser_executor import (
    BrowserExecutionRequest,
    BrowserExecutionResult,
    BrowserExecutor,
)
from app.services.checkpoints import rollback_availability_for_tool
from app.services.design_alignment import SafetyDecisionService
from app.services.knowledge import KnowledgeService
from app.services.memory import MemoryService
from app.services.safety_policy import RuntimeSafetyPolicyService, classify_action_category

if TYPE_CHECKING:
    from app.services.browser_sessions import BrowserSessionService
    from app.services.checkpoints import CheckpointService
    from app.services.execution_boundary import ExecutionBoundaryService
    from app.services.mcp import MCPService
    from app.services.media import MediaService
    from app.services.skill_plugin import SkillPluginService

from app.services.office_tools import OfficeToolService
from app.services.tool_asset_runtime import ToolAssetRuntime
from app.services.tool_browser_runtime import ToolBrowserRuntime
from app.services.tool_builtin_runtime import ToolBuiltinRuntime
from app.services.tool_dispatcher import ToolDispatcher
from app.services.tool_memory_runtime import ToolMemoryRuntime
from app.services.tool_mcp_runtime import ToolMcpRuntime
from app.services.tool_safety_bridge import ToolSafetyBridge
from app.services.tool_terminal_runtime import ToolTerminalRuntime


@dataclass(frozen=True)
class ToolRunOutcome:
    result: dict[str, Any]
    artifacts: list[TaskArtifact]


@dataclass(frozen=True)
class HostFilesystemTarget:
    location: str
    path: Path


_BROWSER_EXECUTABLE_PATH_ENV = "CYCBER_BROWSER_EXECUTABLE_PATH"
_BROWSER_CHANNEL_ENV = "CYCBER_BROWSER_CHANNEL"
HOST_FS_DEFAULT_LIMIT = 50
HOST_FS_MAX_LIMIT = 100
HOST_FS_ALLOWED_LOCATIONS = {"desktop", "downloads", "documents", "home", "authorized"}
HOST_FS_SECRET_NAME_RE = re.compile(
    r"(^|[._-])(?:secret|token|password|passwd|pwd|apikey|api_key|private[_-]?key|"
    r"mnemonic|cookie|wallet|master\.key|local_secrets)([._-]|$)"
    r"|(?:\.env(?:\.local)?$|id_rsa$|id_dsa$|id_ecdsa$|id_ed25519$)",
    re.IGNORECASE,
)
HOST_FS_DENIED_PATH_RE = re.compile(
    r"(^|[\\/])(?:windows|program files|program files \(x86\)|programdata|"
    r"\.ssh|\.gnupg|browser profiles?|user data|wallet|secrets?)([\\/]|$)"
    r"|(^|[\\/])(?:google[\\/]chrome|chromium|mozilla[\\/]firefox)([\\/]|$)"
    r"|(^|[\\/])(?:cookies|login data|local state|master\.key|local_secrets\.json)$",
    re.IGNORECASE,
)


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


def _browser_http_error_reason(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout while fetching browser resource"
    reason = str(exc) or exc.__class__.__name__
    return str(redact(reason))


def _html_title(text: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return str(redact(re.sub(r"\s+", " ", match.group(1)).strip()))[:200]


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
        safety_policy_service: RuntimeSafetyPolicyService | None = None,
        execution_boundary_service: ExecutionBoundaryService | None = None,
        browser_session_service: BrowserSessionService | None = None,
        browser_executor: BrowserExecutor | None = None,
        checkpoint_service: CheckpointService | None = None,
        media_service: MediaService | None = None,
        office_tool_service: OfficeToolService | None = None,
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
        self._safety_policy = safety_policy_service
        self._boundary = execution_boundary_service
        self._browser_sessions = browser_session_service
        self._browser_executor = browser_executor or BrowserExecutor()
        self._checkpoints = checkpoint_service
        self._media = media_service
        self._office = office_tool_service
        self._skill_plugin: SkillPluginService | None = None
        self._mcp: MCPService | None = None
        self._dispatcher = ToolDispatcher(self)
        self._safety_bridge = ToolSafetyBridge(self)
        self._terminal_runtime = ToolTerminalRuntime(self)
        self._mcp_runtime = ToolMcpRuntime(self)
        self._builtin_runtime = ToolBuiltinRuntime(self)
        self._browser_runtime = ToolBrowserRuntime()
        self._asset_runtime = ToolAssetRuntime()
        self._memory_runtime = ToolMemoryRuntime()

    def set_extension_services(
        self,
        *,
        skill_plugin_service: SkillPluginService | None = None,
        mcp_service: MCPService | None = None,
    ) -> None:
        self._skill_plugin = skill_plugin_service
        self._mcp = mcp_service

    async def close(self) -> None:
        await self._browser_executor.close()

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
        return await self._dispatcher.execute(request, trace_id=trace_id)

    async def diagnostic(self) -> dict[str, Any]:
        sandbox_status = (
            await self._boundary.sandbox_status() if self._boundary is not None else None
        )
        mcp_runtime = await self._mcp.runtime_diagnostic() if self._mcp is not None else None
        return {
            "runtime": "tool_runtime",
            "dispatcher": "tool_dispatcher",
            "safety_bridge": "tool_safety_bridge",
            "builtin": {"runtime": "tool_builtin_runtime"},
            "browser": self._browser_runtime.diagnostic(),
            "asset": self._asset_runtime.diagnostic(),
            "memory": self._memory_runtime.diagnostic(),
            "terminal": {
                "runtime": "tool_terminal_runtime",
                "backend_profile": sandbox_status,
                "approval_required_for_high_risk": True,
            },
            "mcp": mcp_runtime,
            "extensions": {
                "skill_plugin_configured": self._skill_plugin is not None,
                "mcp_configured": self._mcp is not None,
            },
        }

    def _redact_payload(self, value: Any) -> Any:
        return redact(value)

    async def _approval_if_required(
        self,
        *,
        request: ToolExecuteRequest,
        tool: ToolDefinition,
        tool_call_id: str,
        organization_id: str,
        risk_level: RiskLevel,
        terminal_command_policy: dict[str, Any] | None,
        trace_id: str | None,
    ) -> ApprovalDetail | None:
        return await self._safety_bridge.approval_if_required(
            request=request,
            tool=tool,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            risk_level=risk_level,
            terminal_command_policy=terminal_command_policy,
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
        return await self._execute_builtin_impl(
            request,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            trace_id=trace_id,
        )

    async def _execute_builtin_impl(
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
        if name.startswith(("project.", "runtime.", "host.")):
            return await self._execute_deployment_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("media."):
            return await self._execute_media_tool(request, trace_id=trace_id)
        if name.startswith("office."):
            return await self._execute_office_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
            )
        if name.startswith("account."):
            return await self._execute_account_tool(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                trace_id=trace_id,
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
        result = self._mcp_runtime.normalize_result(result)
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

    async def _execute_account_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if request.tool_name == "account.create_draft_artifact":
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
        if request.tool_name == "account.login":
            return await self._account_login(request, trace_id=trace_id)
        if request.tool_name == "account.publish_post":
            return await self._account_publish_post(request, trace_id=trace_id)
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "账号工具不存在", status_code=404)

    async def _execute_office_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if self._office is None:
            raise AppError(ErrorCode.TOOL_EXECUTION_FAILED, "Office 工具未初始化", status_code=500)
        result, artifacts = await self._office.execute(
            request,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            trace_id=trace_id,
        )
        return ToolRunOutcome(result=result, artifacts=artifacts)

    async def _account_login(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        resolved = await self._resolve_account_secret(
            request,
            action="login",
            trace_id=trace_id,
        )
        login_url = _required_http_url(request.args, "login_url")
        username = _resolved_username(resolved.resolved.resource)
        if not username or not resolved.secret_value:
            raise AppError(
                ErrorCode.ASSET_HANDLE_INVALID,
                "账号资产缺少用户名或密钥",
                status_code=422,
            )
        login_status = await _post_login(
            login_url=login_url,
            username=username,
            password=resolved.secret_value,
            args=request.args,
        )
        return ToolRunOutcome(
            result={
                "status": "authenticated",
                "login": login_status,
                "asset": _resolved_account_summary(resolved.resolved),
                "redaction_summary": {"policy": "trace_service.redact"},
            },
            artifacts=[],
        )

    async def _account_publish_post(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        resolved = await self._resolve_account_secret(
            request,
            action="publish_post",
            trace_id=trace_id,
        )
        login_url = _required_http_url(request.args, "login_url")
        publish_url = _required_http_url(request.args, "publish_url")
        title = str(request.args.get("title") or "").strip()
        body = str(request.args.get("body") or request.args.get("content") or "").strip()
        if not title or not body:
            raise AppError(
                ErrorCode.TOOL_SCHEMA_INVALID,
                "发布文章需要 title 和 body",
                status_code=422,
            )
        username = _resolved_username(resolved.resolved.resource)
        if not username or not resolved.secret_value:
            raise AppError(
                ErrorCode.ASSET_HANDLE_INVALID,
                "账号资产缺少用户名或密钥",
                status_code=422,
            )
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            login_response = await _post_login(
                login_url=login_url,
                username=username,
                password=resolved.secret_value,
                args=request.args,
                client=client,
            )
            publish_response = await client.post(
                publish_url,
                data={
                    str(request.args.get("title_field") or "title"): title,
                    str(request.args.get("body_field") or "body"): body,
                },
            )
        if publish_response.status_code >= 400:
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "账号发布失败",
                status_code=502,
                details={
                    "http_status": publish_response.status_code,
                    "response": _http_response_preview(publish_response),
                },
            )
        publish_payload = _http_response_preview(publish_response)
        return ToolRunOutcome(
            result={
                "status": "published",
                "asset": _resolved_account_summary(resolved.resolved),
                "login": login_response,
                "publish": {
                    "url": str(redact(str(publish_response.url))),
                    "http_status": publish_response.status_code,
                    "response": publish_payload,
                },
                "title": str(redact(title)),
                "body_preview": str(redact(body[:160])),
                "redaction_summary": {"policy": "trace_service.redact"},
                "untrusted_external_content": True,
            },
            artifacts=[],
        )

    async def _resolve_account_secret(
        self,
        request: ToolExecuteRequest,
        *,
        action: str,
        trace_id: str | None,
    ) -> ToolResolvedAsset:
        handle_id = str(request.args.get("handle_id") or "")
        if not handle_id:
            raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "handle_id 必填", status_code=422)
        return await self._asset_broker.resolve_secret_for_tool(
            handle_id,
            AssetResolveForToolRequest(
                subject_id=request.member_id,
                action=action,
                tool_name=request.tool_name,
                task_id=request.task_id,
                conversation_id=None,
                approval_id=request.approval_id,
            ),
            trace_id=trace_id,
        )

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
            checkpoint_service = self._checkpoints
            checkpoint = None
            if checkpoint_service is not None and bool(request.args.get("overwrite")):
                checkpoint = await checkpoint_service.create_checkpoint(
                    task_id=request.task_id,
                    paths=[path_arg],
                    checkpoint_type="pre_mutation",
                    step_id=request.step_id,
                    tool_call_id=tool_call_id,
                    reason="file.write overwrite pre-mutation",
                    metadata={"tool_name": name},
                    trace_id=trace_id,
                )
            content = str(request.args.get("content") or "")
            try:
                artifact = await self._artifacts.write_text(
                    task_id=request.task_id,
                    organization_id=organization_id,
                    step_id=request.step_id,
                    tool_call_id=tool_call_id,
                    display_name=path.name,
                    content=content,
                    artifact_type="text",
                    subdir=path.parent.relative_to(
                        self._artifacts.task_dir(request.task_id)
                    ).as_posix(),
                    trace_id=trace_id,
                )
            finally:
                if checkpoint is not None and checkpoint_service is not None:
                    checkpoint = await checkpoint_service.finalize_checkpoint(
                        checkpoint.checkpoint_id
                    )
            result = {
                "uri": artifact.uri,
                **_checkpoint_result(checkpoint),
            }
            return ToolRunOutcome(result=result, artifacts=[artifact])
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
            checkpoint_service = self._checkpoints
            checkpoint = None
            if checkpoint_service is not None:
                checkpoint = await checkpoint_service.create_checkpoint(
                    task_id=request.task_id,
                    paths=[path_arg, destination_arg],
                    checkpoint_type="pre_mutation",
                    step_id=request.step_id,
                    tool_call_id=tool_call_id,
                    reason=f"{name} pre-mutation",
                    metadata={"tool_name": name},
                    trace_id=trace_id,
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                if name == "file.copy":
                    shutil.copy2(path, destination)
                else:
                    shutil.move(str(path), str(destination))
            finally:
                if checkpoint is not None and checkpoint_service is not None:
                    checkpoint = await checkpoint_service.finalize_checkpoint(
                        checkpoint.checkpoint_id
                    )
            return ToolRunOutcome(
                result={
                    "path": _relative_to_task(destination, request.task_id, self._artifacts),
                    **_checkpoint_result(checkpoint),
                },
                artifacts=[],
            )
        if name == "file.delete":
            checkpoint_service = self._checkpoints
            checkpoint = None
            if checkpoint_service is not None and path.exists():
                checkpoint = await checkpoint_service.create_checkpoint(
                    task_id=request.task_id,
                    paths=[path_arg],
                    checkpoint_type="pre_mutation",
                    step_id=request.step_id,
                    tool_call_id=tool_call_id,
                    reason="file.delete pre-mutation",
                    metadata={"tool_name": name},
                    trace_id=trace_id,
                )
            if path.exists():
                try:
                    path.unlink()
                finally:
                    if checkpoint is not None and checkpoint_service is not None:
                        checkpoint = await checkpoint_service.finalize_checkpoint(
                            checkpoint.checkpoint_id
                        )
            return ToolRunOutcome(
                result={"deleted": True, "path": path_arg, **_checkpoint_result(checkpoint)},
                artifacts=[],
            )
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

    async def _execute_media_tool(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if self._media is None:
            raise AppError(
                ErrorCode.MEDIA_BACKEND_UNAVAILABLE,
                "媒体服务未初始化",
                status_code=503,
            )
        from app.schemas.media import (
            MediaEditPlanCreateRequest,
            MediaExportArtifactRequest,
            MediaExtractAudioRequest,
            MediaExtractFramesRequest,
            MediaImportArtifactRequest,
            MediaProbeRequest,
            MediaSTTRequest,
            MediaSummarizeRequest,
            MediaRenderEditRequest,
            MediaSceneDetectRequest,
            MediaTimelineRequest,
            MediaTTSRequest,
            MediaTranscribeAudioRequest,
        )

        name = request.tool_name
        args = request.args
        if name == "media.import_artifact":
            if request.task_id and not args.get("task_id"):
                args = {**args, "task_id": request.task_id}
            response = await self._media.import_artifact(
                MediaImportArtifactRequest(**args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=response.model_dump(mode="json"), artifacts=[])
        media_id = str(args.get("media_id") or "")
        if name not in {"media.render_edit", "media.tts"} and not media_id:
            raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "media_id 必填", status_code=422)
        media_args = {
            key: value
            for key, value in args.items()
            if key not in {"media_id", "edit_plan_id"}
        }
        if name == "media.probe":
            response = await self._media.probe(
                media_id,
                MediaProbeRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=response.model_dump(mode="json"), artifacts=[])
        if name == "media.extract_frames":
            response = await self._media.extract_frames(
                media_id,
                MediaExtractFramesRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(
                result=response.model_dump(mode="json"),
                artifacts=response.artifacts,
            )
        if name == "media.extract_audio":
            response = await self._media.extract_audio(
                media_id,
                MediaExtractAudioRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(
                result=response.model_dump(mode="json"),
                artifacts=response.artifacts,
            )
        if name == "media.transcribe_audio":
            response = await self._media.transcribe_audio(
                media_id,
                MediaTranscribeAudioRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(
                result=response.model_dump(mode="json"),
                artifacts=response.artifacts,
            )
        if name == "media.stt":
            response = await self._media.stt(
                media_id,
                MediaSTTRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=response.model_dump(mode="json"), artifacts=response.artifacts)
        if name == "media.summarize":
            response = await self._media.summarize(
                media_id,
                MediaSummarizeRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=response.model_dump(mode="json"), artifacts=response.artifacts)
        if name == "media.scene_detect":
            response = await self._media.scene_detect(
                media_id,
                MediaSceneDetectRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=response.model_dump(mode="json"), artifacts=[])
        if name == "media.timeline_summarize":
            response = await self._media.timeline(
                media_id,
                MediaTimelineRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=response.model_dump(mode="json"), artifacts=[])
        if name == "media.plan_edit":
            plan_response = await self._media.create_edit_plan(
                media_id,
                MediaEditPlanCreateRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=plan_response.model_dump(mode="json"), artifacts=[])
        if name == "media.render_edit":
            edit_plan_id = str(args.get("edit_plan_id") or "")
            render_response = await self._media.render_edit(
                edit_plan_id,
                MediaRenderEditRequest(**media_args),
                trace_id=trace_id,
            )
            artifacts = (
                [render_response.artifact] if render_response.artifact is not None else []
            )
            return ToolRunOutcome(
                result=render_response.model_dump(mode="json"),
                artifacts=artifacts,
            )
        if name == "media.export_artifact":
            response = await self._media.export_artifact(
                media_id,
                MediaExportArtifactRequest(**media_args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=response.model_dump(mode="json"), artifacts=[])
        if name == "media.tts":
            response = await self._media.tts(
                MediaTTSRequest(**args),
                trace_id=trace_id,
            )
            return ToolRunOutcome(result=response.model_dump(mode="json"), artifacts=response.artifacts)
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "媒体工具不存在", status_code=404)

    async def _browser_session_context(
        self,
        request: ToolExecuteRequest,
        *,
        trace_id: str | None,
    ) -> dict[str, Any]:
        if self._browser_sessions is None:
            return {}
        handle_id = str(
            request.args.get("session_handle_id")
            or request.args.get("browser_session_handle_id")
            or ""
        )
        if handle_id:
            resolved = await self._asset_broker.resolve_for_tool(
                handle_id,
                AssetResolveForToolRequest(
                    subject_id=request.member_id,
                    action=_asset_action_for_tool(request.tool_name, request.args),
                    tool_name=request.tool_name,
                    task_id=request.task_id,
                    conversation_id=None,
                    approval_id=request.approval_id,
                ),
                trace_id=trace_id,
            )
            resource = resolved.resource if isinstance(resolved.resource, dict) else {}
            raw_config = resource.get("config")
            config = raw_config if isinstance(raw_config, dict) else {}
            context = await self._browser_sessions.validate_session_context(
                browser_profile_id=str(
                    config.get("browser_profile_id") or config.get("profile_id") or ""
                )
                or None,
                browser_session_id=str(config.get("browser_session_id") or "") or None,
                member_id=request.member_id,
                task_id=request.task_id,
                url=str(request.args.get("url") or request.args.get("current_url") or "")
                or None,
            )
            return _merge_browser_page_args({
                **context,
                "asset_handle_id": handle_id,
                "asset_id": resolved.asset_id,
                "asset_summary": resolved.summary,
                "session_handle_resolved": True,
                "cookie_material_exposed": False,
            }, request.args)
        if request.args.get("browser_profile_id") or request.args.get("browser_session_id"):
            context = await self._browser_sessions.validate_session_context(
                browser_profile_id=str(request.args.get("browser_profile_id") or "") or None,
                browser_session_id=str(request.args.get("browser_session_id") or "") or None,
                member_id=request.member_id,
                task_id=request.task_id,
                url=str(request.args.get("url") or request.args.get("current_url") or "")
                or None,
            )
            return _merge_browser_page_args(context, request.args)
        return _merge_browser_page_args({}, request.args)

    async def _resolve_browser_page_url(
        self,
        request: ToolExecuteRequest,
        *,
        action: str,
        session_context: dict[str, Any],
    ) -> str:
        direct_url = str(
            request.args.get("url")
            or request.args.get("current_url")
            or request.args.get("expected_url")
            or ""
        ).strip()
        if direct_url:
            session_context.setdefault("current_url", direct_url)
            return direct_url
        context_url = str(session_context.get("current_url") or "").strip()
        if context_url:
            return context_url
        if request.task_id and self._browser_sessions is not None:
            evidence = await self._latest_browser_page_evidence(request.task_id)
            if evidence is not None:
                evidence_url = str(evidence.get("url") or "").strip()
                if evidence_url:
                    session_context.update(
                        {
                            "current_url": evidence_url,
                            "last_browser_evidence_id": evidence.get("browser_evidence_id"),
                            "last_browser_evidence_action": evidence.get("action"),
                            "last_browser_evidence_status": evidence.get("action_status"),
                            "page_id": evidence.get("page_id") or session_context.get("page_id"),
                        }
                    )
                    return evidence_url
        if action in _BROWSER_PAGE_STATE_ACTIONS:
            raise AppError(
                "BROWSER_SESSION_REQUIRED",
                "请先打开页面，或提供 current_url/browser_session_id 后再执行浏览器交互。",
                status_code=409,
                details={
                    "reason_code": "BROWSER_SESSION_REQUIRED",
                    "recoverable": True,
                    "next_step": "先执行 browser.open，或在参数中提供 current_url。",
                    "action": action,
                },
            )
        return ""

    async def _latest_browser_page_evidence(self, task_id: str) -> dict[str, Any] | None:
        if self._browser_sessions is None:
            return None
        rows = await self._browser_sessions.list_task_evidence(task_id)
        for row in reversed(rows):
            if row.url:
                return row.model_dump(mode="json")
        return None

    async def _ensure_browser_url_allowed(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        url: str,
        action: str,
        session_context: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any]:
        if self._browser_sessions is None:
            if not url.startswith(("http://", "https://")):
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "浏览器 URL 被安全策略阻断",
                    status_code=403,
                    details={"reason_codes": ["browser_session_service_unavailable"]},
                )
            return {
                "allowed": True,
                "url": str(redact(url)),
                "reason_codes": ["browser_url_allowed_without_profile"],
            }
        decision = self._browser_sessions.classify_url(
            url,
            session_context=session_context,
        )
        payload = decision.as_dict()
        if decision.allowed:
            return payload
        await self._browser_sessions.record_evidence(
            task_id=request.task_id,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            action=action,
            action_status="blocked",
            url=decision.redacted_url,
            title=None,
            http_status=None,
            evidence_summary="browser URL blocked by safety policy before navigation",
            network_summary={"request_count": 0, "failed_count": 0},
            console_summary={"error_count": 0, "warning_count": 0},
            redaction_summary={"blocked_before_navigation": True},
            safety_decision=payload,
            session_context=session_context,
            trace_id=trace_id,
        )
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "浏览器 URL 被安全策略阻断",
            status_code=403,
            details={
                "reason_codes": decision.reason_codes,
                "blocked_reason": decision.blocked_reason,
                "url": decision.redacted_url,
            },
        )

    async def _attach_browser_evidence(
        self,
        result: dict[str, Any],
        *,
        request: ToolExecuteRequest,
        tool_call_id: str,
        organization_id: str,
        action: str,
        url: str | None,
        title: str | None,
        http_status: int | None,
        snapshot_preview: str | None,
        safety: dict[str, Any],
        session_context: dict[str, Any],
        trace_id: str | None,
        screenshot_artifact_id: str | None = None,
        download_artifact_id: str | None = None,
        artifact_ids: list[str] | None = None,
    ) -> None:
        if self._browser_sessions is None:
            return
        evidence = await self._browser_sessions.record_evidence(
            task_id=request.task_id,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            action=action,
            action_status=str(result.get("action_status") or "completed"),
            url=url,
            title=title,
            http_status=http_status,
            evidence_summary=str(result.get("evidence_summary") or "browser evidence"),
            snapshot_preview=snapshot_preview,
            screenshot_artifact_id=screenshot_artifact_id,
            download_artifact_id=download_artifact_id,
            artifact_ids=artifact_ids,
            network_summary=dict(
                result.get("network_summary")
                or {
                    "request_count": 1 if url else 0,
                    "failed_count": 1 if result.get("action_status") == "http_error" else 0,
                    "http_status": http_status,
                }
            ),
            console_summary=dict(
                result.get("console_summary") or {"error_count": 0, "warning_count": 0}
            ),
            redaction_summary={
                "session_handle_redacted": True,
                "executor_backend": result.get("backend"),
                "backend_status": result.get("backend_status"),
            },
            safety_decision=safety,
            session_context=session_context,
            trace_id=trace_id,
        )
        result["browser_evidence_id"] = evidence.browser_evidence_id
        result["browser_evidence"] = evidence.model_dump(mode="json")
        result.setdefault("browser_page_state", {})
        page_state = await self._browser_sessions.record_page_state(
            task_id=request.task_id,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            action=action,
            action_status=str(result.get("action_status") or "completed"),
            page_key=_browser_page_key(result, session_context, url),
            current_url=str(result.get("url") or url or "") or None,
            title=title,
            http_status=http_status,
            dom_summary=_browser_dom_summary(result),
            network_summary=dict(
                result.get("network_summary")
                or {
                    "request_count": 1 if url else 0,
                    "failed_count": 1 if result.get("action_status") == "http_error" else 0,
                    "http_status": http_status,
                }
            ),
            console_summary=dict(
                result.get("console_summary") or {"error_count": 0, "warning_count": 0}
            ),
            task_checkpoint=_browser_task_checkpoint(
                request=request,
                tool_call_id=tool_call_id,
                evidence_id=evidence.browser_evidence_id,
                result=result,
            ),
            redaction_summary={
                "session_handle_redacted": True,
                "storage_state_redacted": True,
                "download_path_visible": False,
            },
            session_context=session_context,
            trace_id=trace_id,
            browser_evidence_id=evidence.browser_evidence_id,
        )
        result["browser_page_state"]["page_state_id"] = page_state.page_state_id
        result["browser_page_state"]["page_key"] = page_state.page_key

    async def _execute_browser_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        session_context = await self._browser_session_context(request, trace_id=trace_id)
        if request.tool_name == "browser.search":
            query = str(request.args.get("query") or "").strip()
            if not query:
                raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "query 必填", status_code=422)
            url = str(request.args.get("url") or "").strip() or (
                "https://www.bing.com/search?q=" + quote_plus(query)
            )
            safety = await self._ensure_browser_url_allowed(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                url=url,
                action="search",
                session_context=session_context,
                trace_id=trace_id,
            )
            try:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    response = await client.get(url)
                    response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise AppError(
                    ErrorCode.TOOL_TIMEOUT,
                    "浏览器搜索超时",
                    status_code=504,
                    details={"reason": "timeout while fetching search results"},
                ) from exc
            except httpx.HTTPError as exc:
                raise AppError(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    "浏览器搜索失败",
                    status_code=502,
                    details={"reason": _browser_http_error_reason(exc)},
                ) from exc
            text = response.text[:5000]
            title = _html_title(text)
            result = {
                "query": str(redact(query)),
                "url": str(redact(str(response.url))),
                "title": title,
                "http_status": response.status_code,
                "action_status": "completed",
                "evidence_summary": "browser.search fetched untrusted search content",
                "content_preview": str(redact(text)),
                "snapshot": str(redact(text)),
                "recoverable": False,
                "redaction_summary": {"policy": "trace_service.redact"},
                "retrieval_source": "browser.search",
                "untrusted_external_content": True,
            }
            await self._attach_browser_evidence(
                result,
                request=request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="search",
                url=str(response.url),
                title=title,
                http_status=response.status_code,
                snapshot_preview=text,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
            return ToolRunOutcome(
                result=result,
                artifacts=[],
            )
        action = _browser_action_for_tool(request.tool_name)
        url = await self._resolve_browser_page_url(
            request,
            action=action,
            session_context=session_context,
        )
        safety = await self._ensure_browser_url_allowed(
            request,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            url=url,
            action=action,
            session_context=session_context,
            trace_id=trace_id,
        )
        if request.tool_name == "browser.open":
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="open",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name == "browser.snapshot":
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="snapshot",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name == "browser.wait":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "浏览器等待必须绑定任务",
                    status_code=422,
                )
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="wait",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name in {
            "browser.fill",
            "browser.type",
            "browser.select",
            "browser.check",
        }:
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "浏览器交互必须绑定任务",
                    status_code=422,
                )
            selector = str(request.args.get("selector") or "")
            if not selector:
                raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "selector 必填", status_code=422)
            action = request.tool_name.removeprefix("browser.")
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action=action,
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name in {"browser.click", "browser.submit"}:
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "浏览器交互必须绑定任务",
                    status_code=422,
                )
            selector = str(request.args.get("selector") or "")
            if request.tool_name == "browser.click" and not selector:
                raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "selector 必填", status_code=422)
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="submit" if request.tool_name == "browser.submit" else "click",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name in {"browser.dialog", "browser.tabs", "browser.frame_action"}:
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "浏览器交互必须绑定任务",
                    status_code=422,
                )
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action=request.tool_name.removeprefix("browser."),
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name == "browser.screenshot":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "截图必须绑定任务",
                    status_code=422,
                )
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="screenshot",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name == "browser.vision_snapshot":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "视觉快照必须绑定任务",
                    status_code=422,
                )
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="vision_snapshot",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name in {"browser.console", "browser.network_summary"}:
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "浏览器观测必须绑定任务",
                    status_code=422,
                )
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action=request.tool_name.removeprefix("browser."),
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name == "browser.download":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "下载必须绑定任务",
                    status_code=422,
                )
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="download",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        if request.tool_name == "browser.upload":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "上传必须绑定任务",
                    status_code=422,
                )
            selector = str(request.args.get("selector") or "")
            if not selector:
                raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, "selector 必填", status_code=422)
            if request.args.get("path") or request.args.get("file_path"):
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "上传文件必须来自任务工件或文件资产，不允许任意本地路径",
                    status_code=403,
                )
            file_path = await self._artifact_upload_path(request)
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="upload",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
                file_path=file_path,
            )
        if request.tool_name == "browser.extract":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "浏览器抽取必须绑定任务",
                    status_code=422,
                )
            return await self._run_browser_executor(
                request,
                tool_call_id=tool_call_id,
                organization_id=organization_id,
                action="extract",
                url=url,
                safety=safety,
                session_context=session_context,
                trace_id=trace_id,
            )
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "浏览器工具不存在", status_code=404)

    async def _artifact_upload_path(self, request: ToolExecuteRequest) -> str | None:
        artifact_id = str(request.args.get("artifact_id") or "").strip()
        if not artifact_id:
            if request.args.get("asset_handle_id") or request.args.get("file_asset_handle_id"):
                return None
            raise AppError(
                ErrorCode.TOOL_SCHEMA_INVALID,
                "artifact_id 或文件资产句柄必填",
                status_code=422,
            )
        row = await self._repo.get_artifact(artifact_id)
        if row is None:
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "工件不存在", status_code=404)
        artifact = TaskArtifact(**row)
        if artifact.task_id != request.task_id:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "只能上传当前任务绑定的工件",
                status_code=403,
            )
        return str(self._artifacts.path_for_artifact(artifact))

    async def _run_browser_executor(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        action: str,
        url: str,
        safety: dict[str, Any],
        session_context: dict[str, Any],
        trace_id: str | None,
        file_path: str | None = None,
    ) -> ToolRunOutcome:
        execution = await self._browser_executor.execute(
            BrowserExecutionRequest(
                action=action,
                url=url,
                selector=str(request.args.get("selector") or "") or None,
                value=str(request.args.get("value") or request.args.get("text") or ""),
                timeout_seconds=float(request.args.get("timeout_seconds") or 15),
                context_key=_browser_context_key(request, session_context),
                session_context=session_context,
                display_name=str(request.args.get("display_name") or "") or None,
                file_path=file_path,
                provider_mode=str(request.args.get("provider_mode") or "auto"),
                viewport_profile=str(request.args.get("viewport_profile") or "desktop"),
                target_ref=str(request.args.get("target_ref") or "") or None,
                frame_ref=str(request.args.get("frame_ref") or "") or None,
                tab_ref=str(request.args.get("tab_ref") or "") or None,
                wait_until=str(request.args.get("wait_until") or "") or None,
                wait_for_text=str(request.args.get("wait_for_text") or "") or None,
                wait_for_url=str(request.args.get("wait_for_url") or "") or None,
                action_strategy=str(request.args.get("action_strategy") or "css"),
            )
        )
        result = execution.public_result()
        result["browser_page_state"] = {
            "url_source": _browser_url_source(request.args, session_context),
            "current_url": str(redact(url)),
            "browser_session_id": session_context.get("browser_session_id"),
            "browser_profile_id": session_context.get("browser_profile_id"),
            "page_id": session_context.get("page_id") or request.args.get("page_id"),
            "last_browser_evidence_id": session_context.get("last_browser_evidence_id"),
            "recoverable": False,
        }
        artifacts: list[TaskArtifact] = []
        artifact_ids: list[str] = []
        screenshot_artifact_id: str | None = None
        download_artifact_id: str | None = None
        if action in {"screenshot", "vision_snapshot"} and execution.screenshot_bytes is not None:
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "截图必须绑定任务",
                    status_code=422,
                )
            artifact = await self._artifacts.write_bytes(
                task_id=request.task_id,
                organization_id=organization_id,
                step_id=request.step_id,
                tool_call_id=tool_call_id,
                display_name=execution.filename or (
                    "vision-snapshot.png" if action == "vision_snapshot" else "screenshot.png"
                ),
                content=execution.screenshot_bytes,
                artifact_type="screenshot",
                content_type=execution.content_type or "image/png",
                subdir="screenshots",
                metadata=_browser_artifact_metadata(execution),
                trace_id=trace_id,
            )
            artifacts.append(artifact)
            artifact_ids.append(artifact.artifact_id)
            screenshot_artifact_id = artifact.artifact_id
            result["screenshot"] = artifact.model_dump(mode="json")
            result["artifact"] = artifact.model_dump(mode="json")
            result["artifact_id"] = artifact.artifact_id
        if action == "download" and execution.download_bytes is not None:
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "下载必须绑定任务",
                    status_code=422,
                )
            artifact = await self._artifacts.write_bytes(
                task_id=request.task_id,
                organization_id=organization_id,
                step_id=request.step_id,
                tool_call_id=tool_call_id,
                display_name=execution.filename or "download.bin",
                content=execution.download_bytes,
                artifact_type="download",
                content_type=execution.content_type or "application/octet-stream",
                subdir="quarantine",
                metadata=_browser_artifact_metadata(execution),
                trace_id=trace_id,
            )
            artifacts.append(artifact)
            artifact_ids.append(artifact.artifact_id)
            download_artifact_id = artifact.artifact_id
            result["artifact"] = artifact.model_dump(mode="json")
            result["download"] = artifact.model_dump(mode="json")
        await self._attach_browser_evidence(
            result,
            request=request,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            action=action,
            url=execution.url,
            title=execution.title,
            http_status=execution.http_status,
            snapshot_preview=execution.snapshot,
            screenshot_artifact_id=screenshot_artifact_id,
            download_artifact_id=download_artifact_id,
            artifact_ids=artifact_ids,
            safety=safety,
            session_context=session_context,
            trace_id=trace_id,
        )
        return ToolRunOutcome(result=result, artifacts=artifacts)

    async def _browser_screenshot(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        url: str,
        safety: dict[str, Any],
        session_context: dict[str, Any],
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
            metadata={
                "url": str(redact(url)),
                "untrusted_external_content": True,
                "redaction_summary": {"policy": "trace_service.redact"},
            },
            trace_id=trace_id,
        )
        result = {
            "url": str(redact(url)),
            "title": None,
            "http_status": None,
            "action_status": "completed",
            "evidence_summary": "browser.screenshot captured a task artifact",
            "snapshot": None,
            "screenshot": artifact.model_dump(mode="json"),
            "artifact": artifact.model_dump(mode="json"),
            "artifact_id": artifact.artifact_id,
            "timeout": False,
            "recoverable": False,
            "redaction_summary": {"policy": "trace_service.redact"},
            "untrusted_external_content": True,
        }
        await self._attach_browser_evidence(
            result,
            request=request,
            tool_call_id=tool_call_id,
            organization_id=organization_id,
            action="screenshot",
            url=url,
            title=None,
            http_status=None,
            snapshot_preview=None,
            screenshot_artifact_id=artifact.artifact_id,
            artifact_ids=[artifact.artifact_id],
            safety=safety,
            session_context=session_context,
            trace_id=trace_id,
        )
        return ToolRunOutcome(
            result=result,
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
            "selected_backend": sandbox_result.backend,
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
                "output_preview": redacted_output[:1000],
                "log_artifact_id": artifact.artifact_id,
                "sandbox_profile": sandbox_profile_result,
                "policy_snapshot": terminal_policy,
                "selected_backend": sandbox_result.backend,
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
        snapshot = self._terminal_runtime.enrich_policy_snapshot(
            dict(row.get("policy_snapshot") or {}),
            sandbox_result=sandbox_result,
            log_artifact_id=log_artifact_id,
            dlp_report_id=dlp_report_id,
            approval_id=row.get("approval_id"),
        )
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
                    "status": "completed",
                    "reason_code": "terminal_log_available",
                    "log_artifact_id": artifact.artifact_id,
                    "content_preview": preview,
                    "recoverable": False,
                    "next_step": None,
                },
                artifacts=[],
            )
        if not request.task_id:
            return ToolRunOutcome(
                result={
                    "status": "unavailable",
                    "reason_code": "task_id_required",
                    "log_artifact_id": None,
                    "content_preview": None,
                    "recoverable": True,
                    "next_step": "提供 task_id 或 log artifact_id 后重试。",
                },
                artifacts=[],
            )
        logs = [
            artifact
            for artifact in await self._repo.list_artifacts(request.task_id)
            if artifact["artifact_type"] == "terminal_log"
        ]
        if not logs:
            reason = "terminal_log_missing"
            next_step = "先执行 terminal.run 并通过审批；执行完成后再读取日志。"
            task = await self._repo.get_task(request.task_id)
            task_status = str((task or {}).get("status") or "")
            steps = await self._repo.list_steps(request.task_id)
            terminal_steps = [
                step
                for step in steps
                if str((step.get("input") or {}).get("tool_name") or "") == "terminal.run"
            ]
            latest_terminal_step = terminal_steps[-1] if terminal_steps else None
            approval_status = ""
            if latest_terminal_step and latest_terminal_step.get("approval_id"):
                approval = await self._repo.get_approval(str(latest_terminal_step["approval_id"]))
                approval_status = str((approval or {}).get("status") or "")
            if task_status == "waiting_approval":
                reason = "waiting_approval"
                next_step = "先确认终端命令；确认前不会产生终端日志。"
                if approval_status in {"approved", "edited"}:
                    reason = "approval_resolved_pending_resume"
                    next_step = "审批已通过，终端命令正在恢复执行；稍后再读取日志。"
            elif latest_terminal_step and str(latest_terminal_step.get("status") or "") == "running":
                reason = "executing_after_approval"
                next_step = "终端命令正在执行；执行完成后再读取日志。"
            elif task_status in {"planned", "pending"}:
                reason = "terminal_not_executed"
                next_step = "先启动任务或执行 terminal.run。"
            elif task_status in {"failed", "cancelled", "paused"}:
                reason = f"task_{task_status}"
                next_step = "查看任务回放了解失败/暂停原因，或重新发起命令。"
            elif latest_terminal_step and str(latest_terminal_step.get("status") or "") == "completed":
                reason = "completed_but_log_missing"
                next_step = "终端命令已结束，但日志工件缺失；请查看任务回放和 tool_call 输出。"
            return ToolRunOutcome(
                result={
                    "status": "unavailable",
                    "reason_code": reason,
                    "log_artifact_id": None,
                    "content_preview": None,
                    "recoverable": True,
                    "next_step": next_step,
                    "task_status": task_status or None,
                    "step_status": (
                        str(latest_terminal_step.get("status") or "")
                        if latest_terminal_step
                        else None
                    ),
                    "approval_status": approval_status or None,
                },
                artifacts=[],
            )
        artifact, preview = await self._artifacts.read_preview(logs[-1]["artifact_id"])
        return ToolRunOutcome(
            result={
                "status": "completed",
                "reason_code": "terminal_log_available",
                "log_artifact_id": artifact.artifact_id,
                "content_preview": preview,
                "recoverable": False,
                "next_step": None,
            },
            artifacts=[],
        )

    async def _execute_deployment_tool(
        self,
        request: ToolExecuteRequest,
        *,
        tool_call_id: str,
        organization_id: str,
        trace_id: str | None,
    ) -> ToolRunOutcome:
        if request.tool_name == "host.fs.list":
            return await self._host_fs_list(request)
        if request.tool_name.startswith("host.") and request.tool_name != "host.detect_software":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "本机安装工具必须绑定任务",
                    status_code=422,
                )
            artifact = await self._artifacts.write_text(
                task_id=request.task_id,
                organization_id=organization_id,
                step_id=request.step_id,
                tool_call_id=tool_call_id,
                display_name="host-install-tool.log",
                content=(
                    "host.install_software dry-run only\n"
                    f"payload={redact(request.args)}\n"
                    "real_execution=false\n"
                ),
                artifact_type="host_install_log",
                subdir="logs",
                trace_id=trace_id,
            )
            return ToolRunOutcome(
                result={
                    "status": "installed",
                    "execution_mode": "dry_run",
                    "real_execution": False,
                    "log_artifact_id": artifact.artifact_id,
                    "redaction_summary": {"policy": "trace_service.redact"},
                },
                artifacts=[artifact],
            )
        if request.tool_name == "host.detect_software":
            software = str(request.args.get("software") or request.args.get("name") or "")
            return ToolRunOutcome(
                result={
                    "status": "unknown",
                    "software": str(redact(software)),
                    "version": None,
                    "recoverable": True,
                    "next_step": "创建 host install plan 后进行 dry-run 检测。",
                },
                artifacts=[],
            )
        if request.tool_name == "runtime.ensure":
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "runtime.ensure 必须绑定任务",
                    status_code=422,
                )
            runtime = str(request.args.get("runtime_name") or request.args.get("runtime") or "node")
            version = str(request.args.get("version") or "lts")
            artifact = await self._artifacts.write_text(
                task_id=request.task_id,
                organization_id=organization_id,
                step_id=request.step_id,
                tool_call_id=tool_call_id,
                display_name=f"runtime-{runtime}.log",
                content=(
                    "portable runtime planned\n"
                    f"runtime={redact(runtime)}\n"
                    f"version={redact(version)}\n"
                    "modifies_global_path=false\n"
                ),
                artifact_type="toolchain_log",
                subdir="logs",
                trace_id=trace_id,
            )
            return ToolRunOutcome(
                result={
                    "status": "installed",
                    "runtime_name": str(redact(runtime)),
                    "version": str(redact(version)),
                    "install_mode": "portable",
                    "modifies_global_path": False,
                    "log_artifact_id": artifact.artifact_id,
                },
                artifacts=[artifact],
            )
        if request.tool_name.startswith("project."):
            if not request.task_id:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "项目部署工具必须绑定任务",
                    status_code=422,
                )
            action = request.tool_name.removeprefix("project.")
            project_artifact: TaskArtifact | None = None
            result: dict[str, Any] = {
                "status": "completed",
                "action": action,
                "workspace_policy": "data/workspaces/projects/{workspace_id}",
                "redaction_summary": {"policy": "trace_service.redact"},
            }
            if action in {"clone", "install_deps", "build", "test", "run", "health_check", "stop"}:
                project_artifact = await self._artifacts.write_text(
                    task_id=request.task_id,
                    organization_id=organization_id,
                    step_id=request.step_id,
                    tool_call_id=tool_call_id,
                    display_name=f"project-{action}.log",
                    content=(
                        f"project.{action} completed under managed workflow\n"
                        f"args={redact(request.args)}\n"
                    ),
                    artifact_type="deployment_log",
                    subdir="logs",
                    trace_id=trace_id,
                )
                result["log_artifact_id"] = project_artifact.artifact_id
            if action == "run":
                port = int(request.args.get("port") or request.args.get("preferred_port") or 5173)
                result["endpoint_url"] = f"http://127.0.0.1:{port}"
            if action == "detect_stack":
                result["stack_summary"] = {
                    "stack": str(request.args.get("stack") or "unknown"),
                    "confidence": 0.5,
                    "execution_allowed": False,
                }
            return ToolRunOutcome(
                result=result,
                artifacts=[project_artifact] if project_artifact else [],
            )
        raise AppError(ErrorCode.TOOL_NOT_FOUND, "部署工具不存在", status_code=404)

    async def _host_fs_list(self, request: ToolExecuteRequest) -> ToolRunOutcome:
        target = _resolve_host_fs_target(request.args)
        limit = _host_fs_limit(request.args.get("limit"))
        policy = {
            "risk": "R1",
            "mode": "metadata_only",
            "recursive": False,
            "content_read": False,
            "absolute_paths_returned": False,
            "allowed_locations": sorted(HOST_FS_ALLOWED_LOCATIONS),
        }
        redaction_summary: dict[str, Any] = {
            "hidden_items_skipped": 0,
            "sensitive_names_redacted": 0,
            "access_errors": 0,
        }
        if not target.path.exists():
            return ToolRunOutcome(
                result={
                    "location": target.location,
                    "items": [],
                    "truncated": False,
                    "redaction_summary": redaction_summary,
                    "policy": {**policy, "exists": False},
                },
                artifacts=[],
            )
        if not target.path.is_dir():
            raise AppError(
                ErrorCode.TOOL_SCHEMA_INVALID,
                "host.fs.list 只能列出目录",
                status_code=422,
                details={"location": target.location},
            )
        items: list[dict[str, Any]] = []
        try:
            entries = list(target.path.iterdir())
        except OSError as exc:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "无法读取该目录",
                status_code=403,
                details={"location": target.location, "reason": str(redact(str(exc)))},
            ) from exc
        entries.sort(key=lambda item: (not item.is_dir(), item.name.lower()))
        for entry in entries:
            if len(items) >= limit:
                break
            if _host_fs_hidden(entry):
                redaction_summary["hidden_items_skipped"] += 1
                continue
            try:
                if _host_fs_path_denied(entry.resolve()):
                    redaction_summary["hidden_items_skipped"] += 1
                    continue
                stat = entry.stat()
            except OSError:
                redaction_summary["access_errors"] += 1
                continue
            sensitive_name = _host_fs_sensitive_name(entry.name)
            if sensitive_name:
                redaction_summary["sensitive_names_redacted"] += 1
            kind = "directory" if entry.is_dir() else "file" if entry.is_file() else "other"
            items.append(
                {
                    "name": "[REDACTED_SENSITIVE_NAME]" if sensitive_name else entry.name,
                    "type": kind,
                    "size_bytes": stat.st_size if kind == "file" else None,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                    "redacted": sensitive_name,
                }
            )
        visible_count = len(items)
        skipped = int(redaction_summary["hidden_items_skipped"]) + int(
            redaction_summary["access_errors"]
        )
        truncated = len(entries) > visible_count + skipped
        return ToolRunOutcome(
            result={
                "location": target.location,
                "items": items,
                "truncated": truncated,
                "redaction_summary": redaction_summary,
                "policy": {**policy, "exists": True, "limit": limit},
            },
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
                    approval_id=request.approval_id,
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
    if tool.tool_name == "terminal.run":
        command = str(args.get("command") or "")
        terminal_policy = _terminal_command_policy(command)
        if terminal_policy["decision"] == "deny":
            return RiskLevel("R7")
        if terminal_policy["reason"] == "sandboxed_terminal":
            return RiskLevel("R2")
        return RiskLevel("R3")
    if tool.tool_name == "file.write" and args.get("overwrite"):
        return RiskLevel(policy.get("overwrite_true", "R3"))
    if tool.tool_name == "browser.download" and args.get("workflow_low_risk_download"):
        return RiskLevel("R2")
    return RiskLevel(policy.get("default", "R1"))


def _resolve_host_fs_target(args: dict[str, Any]) -> HostFilesystemTarget:
    raw_location = str(args.get("location") or "").strip().lower()
    location = raw_location or "home"
    if location not in HOST_FS_ALLOWED_LOCATIONS:
        raise AppError(
            ErrorCode.TOOL_SCHEMA_INVALID,
            "不支持的本机目录位置",
            status_code=422,
            details={"allowed_locations": sorted(HOST_FS_ALLOWED_LOCATIONS)},
        )
    allowed = _host_fs_allowed_roots()
    raw_path = str(args.get("path") or "").strip()
    if raw_path:
        if _host_fs_contains_traversal(raw_path):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "路径穿越被拒绝",
                status_code=403,
                details={"reason": "host_fs_path_traversal_denied"},
            )
        candidate = Path(os.path.expandvars(raw_path)).expanduser()
        if not candidate.is_absolute():
            base = allowed.get(location if location != "authorized" else "home")
            candidate = (base or _host_home_dir()) / candidate
        path = candidate.resolve()
        if not any(_path_within(root, path) for root in allowed.values()):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "只能查看预设或已授权目录",
                status_code=403,
                details={"reason": "host_fs_outside_allowed_roots", "location": location},
            )
        resolved_location = _host_fs_location_for_path(path, allowed) or location
    else:
        if location == "authorized":
            raise AppError(
                ErrorCode.TOOL_SCHEMA_INVALID,
                "authorized 位置需要提供 path",
                status_code=422,
            )
        path = allowed[location].resolve()
        resolved_location = location
    if _host_fs_path_denied(path):
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "安全策略拒绝查看该目录",
            status_code=403,
            details={"reason": "host_fs_sensitive_path_denied", "location": resolved_location},
        )
    return HostFilesystemTarget(location=resolved_location, path=path)


def _host_fs_allowed_roots() -> dict[str, Path]:
    home = _host_home_dir()
    return {
        "desktop": (home / "Desktop").resolve(),
        "downloads": (home / "Downloads").resolve(),
        "documents": (home / "Documents").resolve(),
        "home": home.resolve(),
    }


def _host_home_dir() -> Path:
    for key in ("USERPROFILE", "HOME"):
        value = os.environ.get(key)
        if value:
            return Path(value).expanduser().resolve()
    return Path.home().resolve()


def _host_fs_limit(raw: Any) -> int:
    try:
        value = int(raw) if raw is not None else HOST_FS_DEFAULT_LIMIT
    except (TypeError, ValueError):
        value = HOST_FS_DEFAULT_LIMIT
    return max(1, min(value, HOST_FS_MAX_LIMIT))


def _host_fs_location_for_path(path: Path, roots: dict[str, Path]) -> str | None:
    for location, root in roots.items():
        if _path_within(root, path):
            return location
    return None


def _path_within(root: Path, path: Path) -> bool:
    root = root.resolve()
    path = path.resolve()
    return path == root or root in path.parents


def _host_fs_contains_traversal(value: str) -> bool:
    return any(part == ".." for part in Path(value).parts)


def _host_fs_path_denied(path: Path) -> bool:
    text = str(path.resolve())
    if HOST_FS_DENIED_PATH_RE.search(text):
        return True
    home = _host_home_dir()
    denied: set[Path] = {
        (home / ".ssh").resolve(),
        (home / ".gnupg").resolve(),
        (home / "AppData").resolve(),
        (home / ".config" / "google-chrome").resolve(),
        (home / ".config" / "chromium").resolve(),
        (home / ".mozilla").resolve(),
        (home / "Library" / "Application Support" / "Google" / "Chrome").resolve(),
        (home / "Library" / "Application Support" / "Firefox").resolve(),
    }
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot")
        if system_root:
            denied.add(Path(system_root).resolve())
    else:
        denied.update(Path(value).resolve() for value in ["/etc", "/bin", "/sbin", "/usr", "/var"])
    resolved = path.resolve()
    return any(resolved == item or item in resolved.parents for item in denied)


def _host_fs_hidden(path: Path) -> bool:
    return path.name.startswith(".") or path.name.lower() in {
        "ntuser.dat",
        "desktop.ini",
        "thumbs.db",
    }


def _host_fs_sensitive_name(name: str) -> bool:
    return bool(HOST_FS_SECRET_NAME_RE.search(name))


def _checkpoint_result(checkpoint: Any | None) -> dict[str, Any]:
    if checkpoint is None:
        return {
            "checkpoint_id": None,
            "rollback_available": False,
        }
    return {
        "checkpoint_id": checkpoint.checkpoint_id,
        "rollback_available": checkpoint.restorable,
        "checkpoint_status": checkpoint.status,
    }


def _required_http_url(args: dict[str, Any], key: str) -> str:
    url = str(args.get(key) or "").strip()
    if not url:
        raise AppError(ErrorCode.TOOL_SCHEMA_INVALID, f"{key} 必填", status_code=422)
    if url.lower().startswith("file:"):
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "file URL 被安全策略阻断",
            status_code=403,
            details={"reason": "account_file_url_denied", "field": key},
        )
    if not url.startswith(("http://", "https://")):
        raise AppError(
            ErrorCode.TOOL_SCHEMA_INVALID,
            f"{key} 必须是 http(s)",
            status_code=422,
        )
    return url


def _resolved_username(resource: dict[str, Any]) -> str:
    raw_config = resource.get("config")
    config = cast(dict[str, Any], raw_config) if isinstance(raw_config, dict) else {}
    return str(config.get("username") or "").strip()


def _resolved_account_summary(resolved: Any) -> dict[str, Any]:
    resource = resolved.resource if isinstance(resolved.resource, dict) else {}
    raw_config = resource.get("config")
    config = cast(dict[str, Any], raw_config) if isinstance(raw_config, dict) else {}
    return {
        "handle_id": resolved.handle_id,
        "asset_id": resolved.asset_id,
        "asset_type": resolved.asset_type.value,
        "platform": config.get("platform"),
        "username": config.get("username"),
        "has_secret": resolved.has_secret,
    }


async def _post_login(
    *,
    login_url: str,
    username: str,
    password: str,
    args: dict[str, Any],
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    data = {
        str(args.get("username_field") or "username"): username,
        str(args.get("password_field") or "password"): password,
    }
    if client is None:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as owned_client:
            response = await owned_client.post(login_url, data=data)
    else:
        response = await client.post(login_url, data=data)
    if response.status_code >= 400:
        raise AppError(
            ErrorCode.TOOL_EXECUTION_FAILED,
            "账号登录失败",
            status_code=502,
            details={
                "http_status": response.status_code,
                "response": _http_response_preview(response),
            },
        )
    return {
        "url": str(redact(str(response.url))),
        "http_status": response.status_code,
        "response": _http_response_preview(response),
    }


def _http_response_preview(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            return dict(redact(payload))
        if payload is not None:
            return {"json": redact(payload)}
    return {
        "content_type": content_type,
        "text_preview": str(redact(response.text[:500])),
    }


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
    payload_summary = redact(request.args)
    payload = request.args
    if tool.tool_name == "host.fs.list":
        payload_summary = {
            "location": request.args.get("location"),
            "path_supplied": bool(request.args.get("path")),
            "limit": request.args.get("limit"),
        }
        payload = dict(payload_summary)
        destination = request.args.get("location") or "host_allowed_location"
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
        payload_summary=payload_summary,
        payload=payload,
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
    if args.get("session_handle_id"):
        ids.append(str(args["session_handle_id"]))
    if args.get("browser_session_handle_id"):
        ids.append(str(args["browser_session_handle_id"]))
    for value in args.get("handle_ids") or []:
        ids.append(str(value))
    return list(dict.fromkeys(ids))


def _asset_action_for_tool(tool_name: str, args: dict[str, Any]) -> str:
    if tool_name.startswith("knowledge."):
        return "read_knowledge"
    if tool_name == "asset.validate_handle":
        return str(args.get("action") or "read")
    if tool_name == "account.login":
        return "login"
    if tool_name == "account.publish_post":
        return "publish_post"
    if tool_name.startswith("account."):
        return "draft_post"
    if tool_name == "browser.download":
        return "download"
    if tool_name == "browser.upload":
        return "upload"
    if tool_name == "browser.extract":
        return "read"
    if tool_name in {"browser.screenshot", "browser.vision_snapshot"}:
        return "capture"
    if tool_name in {"browser.console", "browser.network_summary", "browser.wait"}:
        return "read"
    if tool_name in {"browser.dialog", "browser.tabs", "browser.frame_action"}:
        return str(args.get("action") or "interact")
    if tool_name in {
        "browser.fill",
        "browser.type",
        "browser.select",
        "browser.check",
        "browser.click",
        "browser.submit",
    }:
        return str(args.get("action") or "interact")
    if tool_name.startswith("browser."):
        return str(args.get("action") or "read")
    if tool_name.startswith("hardware."):
        return "query_status"
    return str(args.get("action") or "read")


def _browser_action_for_tool(tool_name: str) -> str:
    return tool_name.removeprefix("browser.")


_BROWSER_PAGE_STATE_ACTIONS = {
    "open",
    "snapshot",
    "screenshot",
    "vision_snapshot",
    "fill",
    "type",
    "select",
    "check",
    "click",
    "submit",
    "wait",
    "dialog",
    "tabs",
    "frame_action",
    "console",
    "network_summary",
    "download",
    "upload",
    "extract",
}


def _merge_browser_page_args(
    session_context: dict[str, Any],
    args: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(session_context)
    for key in ("browser_session_id", "browser_profile_id", "page_id", "current_url"):
        value = args.get(key)
        if value:
            merged[key] = str(value)
    return merged


def _browser_url_source(args: dict[str, Any], session_context: dict[str, Any]) -> str:
    if args.get("url"):
        return "args.url"
    if args.get("current_url"):
        return "args.current_url"
    if args.get("expected_url"):
        return "args.expected_url"
    if session_context.get("last_browser_evidence_id"):
        return "last_browser_evidence"
    if session_context.get("current_url"):
        return "session_context"
    return "missing"


def _browser_context_key(
    request: ToolExecuteRequest,
    session_context: dict[str, Any],
) -> str:
    profile_id = session_context.get("browser_profile_id") or "no_profile"
    session_id = session_context.get("browser_session_id") or "no_session"
    task_id = request.task_id or "no_task"
    return f"{profile_id}:{session_id}:{task_id}"


def _browser_artifact_metadata(execution: BrowserExecutionResult) -> dict[str, Any]:
    return {
        "url": str(redact(execution.url)),
        "http_status": execution.http_status,
        "browser_backend": execution.backend,
        "backend_status": execution.backend_status,
        "fallback_chain": execution.fallback_chain,
        "degraded_reason": execution.degraded_reason,
        "untrusted_external_content": True,
        "redaction_summary": {
            "policy": "trace_service.redact",
            "storage_state_redacted": True,
            "session_material_visible": False,
        },
    }


def _browser_page_key(
    result: dict[str, Any],
    session_context: dict[str, Any],
    url: str,
) -> str:
    value = (
        session_context.get("page_id")
        or result.get("browser_page_state", {}).get("page_id")
        or result.get("page_id")
        or url
        or "browser_page"
    )
    return str(redact(str(value)))[:200]


def _browser_dom_summary(result: dict[str, Any]) -> dict[str, Any]:
    snapshot = str(result.get("snapshot") or result.get("content_preview") or "")
    payload = {
        "has_snapshot": bool(snapshot),
        "snapshot_preview": str(redact(snapshot))[:500] if snapshot else None,
        "snapshot_hash": (
            "sha256:" + hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
            if snapshot
            else None
        ),
    }
    if result.get("selector"):
        payload["selector"] = str(redact(str(result["selector"])))
    if result.get("interaction"):
        payload["interaction"] = redact(result["interaction"])
    return payload


def _browser_task_checkpoint(
    *,
    request: ToolExecuteRequest,
    tool_call_id: str,
    evidence_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "task_id": request.task_id,
        "tool_call_id": tool_call_id,
        "browser_evidence_id": evidence_id,
        "action": request.tool_name,
        "action_status": result.get("action_status"),
        "recoverable": bool(result.get("recoverable")),
        "browser_backend": result.get("backend"),
        "backend_status": result.get("backend_status"),
        "fallback_chain": result.get("fallback_chain"),
    }


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


def _public_memory_search_tool_call(record: ToolCallRecord) -> ToolCallRecord:
    return record.model_copy(
        update={
            "trace_id": None,
            "safety_decision": _strip_internal_trace_fields(record.safety_decision),
            "result_redacted": _strip_internal_trace_fields(record.result_redacted),
        }
    )


def _sanitize_tool_request_for_execution(request: ToolExecuteRequest) -> ToolExecuteRequest:
    if request.tool_name != "media.tts":
        return request
    text = request.args.get("text")
    if not isinstance(text, str):
        return request
    redacted_text = str(redact(text))
    if redacted_text == text:
        return request
    metadata = request.args.get("metadata")
    args = {
        **request.args,
        "text": "[REDACTED_CONTENT]",
        "metadata": {
            **(metadata if isinstance(metadata, dict) else {}),
            "source_text_redacted_before_safety": True,
        },
    }
    return request.model_copy(update={"args": args})


def _strip_internal_trace_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_internal_trace_fields(item)
            for key, item in value.items()
            if key not in {"trace_id", "turn_id", "message_id"}
        }
    if isinstance(value, list):
        return [_strip_internal_trace_fields(item) for item in value]
    return value


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
    {
        "tool_name": "host.fs.list",
        "display_name": "List host files",
        "description": "只读列出本机预设或授权目录中的文件元数据",
        "input_schema": {"required": ["location"]},
        "output_schema": {},
        "risk_policy": {"default": "R1"},
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
            "browser.search": "R2",
            "browser.snapshot": "R2",
            "browser.wait": "R2",
            "browser.fill": "R2",
            "browser.type": "R2",
            "browser.select": "R2",
            "browser.check": "R2",
            "browser.click": "R2",
            "browser.dialog": "R2",
            "browser.tabs": "R2",
            "browser.frame_action": "R2",
            "browser.submit": "R5",
            "browser.screenshot": "R3",
            "browser.vision_snapshot": "R3",
            "browser.download": "R3",
            "browser.upload": "R5",
            "browser.extract": "R2",
            "browser.console": "R2",
            "browser.network_summary": "R2",
            "desktop.window.list": "R1",
            "desktop.window.focus": "R4",
            "desktop.window.minimize": "R4",
            "desktop.window.maximize": "R4",
            "terminal.run": "R5",
            "terminal.stop": "R2",
            "terminal.read_log": "R1",
            "project.create_workspace": "R2",
            "project.clone": "R3",
            "project.detect_stack": "R1",
            "runtime.ensure": "R3",
            "project.install_deps": "R4",
            "project.build": "R3",
            "project.test": "R3",
            "project.run": "R4",
            "project.health_check": "R2",
            "project.read_logs": "R1",
            "project.stop": "R3",
            "host.detect_software": "R2",
            "host.install_software": "R5",
            "account.login": "R2",
            "account.create_draft_artifact": "R2",
            "account.publish_post": "R4",
            "media.import_artifact": "R2",
            "media.probe": "R1",
            "media.extract_frames": "R2",
            "media.extract_audio": "R2",
            "media.transcribe_audio": "R2",
            "media.stt": "R2",
            "media.tts": "R2",
            "media.summarize": "R2",
            "media.scene_detect": "R2",
            "media.timeline_summarize": "R2",
            "media.plan_edit": "R2",
            "media.render_edit": "R3",
            "media.export_artifact": "R3",
            "office.word.generate": "R2",
            "office.word.edit": "R2",
            "office.excel.generate": "R2",
            "office.excel.edit": "R2",
            "office.ppt.generate": "R2",
            "office.ppt.edit": "R2",
            "hardware.query_status": "R1",
        }.items()
    ],
]
