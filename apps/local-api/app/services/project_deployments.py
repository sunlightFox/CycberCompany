from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml
from brain.adapters import CancelToken, ModelAdapterError, ModelChatRequest, OpenAICompatibleClient
from brain.contracts import BrainRouteRequest, ModelRouter
from core_types import (
    ErrorCode,
    HostInstallExecution,
    HostInstallPlan,
    ManagedProcess,
    ProjectDeployment,
    ProjectWorkspace,
    RiskLevel,
    TaskMode,
    TaskStatus,
    ToolchainInstall,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.project_deployment_repo import ProjectDeploymentRepository
from app.db.repositories.task_repo import TaskRepository
from app.schemas.project_deployments import (
    HostInstallPlanRequest,
    ProjectDeployRequest,
    ProjectWorkspaceCreateRequest,
    ToolchainEnsureRequest,
)
from app.schemas.tasks import TaskCreateRequest
from app.services.approvals import ApprovalService
from app.services.artifacts import ArtifactStore
from app.services.audit import AuditEventService
from app.services.safety_policy import RuntimeSafetyPolicyService
from app.services.tasks import TaskEngine

ORG_DEFAULT = "org_default"
WORKSPACE_STATUSES = {"created", "cloning", "ready", "archived", "failed"}
DEPLOYMENT_TERMINAL_STATUSES = {"healthy", "failed", "stopped", "cancelled"}
HOST_INSTALL_DENY_MARKERS = {
    "driver",
    "kernel",
    "wallet",
    "crack",
    "keygen",
    "外挂",
    "破解",
    "驱动",
    "内核",
    "钱包",
    "浏览器扩展",
    "关闭杀毒",
    "绕过uac",
    "绕过 UAC",
}
_MANAGED_PROCESS_HANDLES: dict[str, asyncio.subprocess.Process] = {}
_HOST_PACKAGE_CACHE: dict[str, tuple[float, HostPackageCandidate | None]] = {}
_HOST_PACKAGE_CACHE_TTL_SECONDS = 300.0
_WINGET_MANIFEST_CACHE: dict[str, tuple[float, HostPackageCandidate | None]] = {}
_WINGET_BOOTSTRAP_URL = "https://aka.ms/getwinget"
_WINGET_OFFICIAL_DOC_URL = "https://learn.microsoft.com/en-us/windows/package-manager/winget/"
_WINGET_MANIFEST_REPO_RAW = "https://raw.githubusercontent.com/microsoft/winget-pkgs/master"
_WINGET_MANIFEST_REPO_API = "https://api.github.com/repos/microsoft/winget-pkgs/contents"
_WINGET_GITHUB_RELEASE_URL = (
    "https://github.com/microsoft/winget-cli/releases/latest/download/"
    "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle"
)
_WINGET_GITHUB_DEPENDENCIES_URL = (
    "https://github.com/microsoft/winget-cli/releases/latest/download/"
    "DesktopAppInstaller_Dependencies.zip"
)
_WINGET_GITHUB_LICENSE_URL = (
    "https://github.com/microsoft/winget-cli/releases/latest/download/"
    "e53e159d00e04f729cc2180cffd1c02e_License1.xml"
)
_OFFICIAL_SOURCE_CACHE: dict[str, tuple[float, HostPackageCandidate | None]] = {}
_OFFICIAL_SOURCE_CACHE_TTL_SECONDS = 300.0
_OFFICIAL_DOWNLOAD_EXTENSIONS = {
    ".exe",
    ".msi",
    ".msix",
    ".msixbundle",
    ".appx",
    ".appxbundle",
}
_KNOWN_OFFICIAL_SOURCE_HINTS = {
    "企业微信": {
        "queries": ("企业微信", "WeCom", "WeChat Work", "WXWork"),
        "publisher_hints": ("Tencent",),
        "official_sites": ("https://work.weixin.qq.com/",),
        "download_pages": ("https://work.weixin.qq.com/#indexDownload",),
        "vendor_domains": (
            "qq.com",
            "work.weixin.qq.com",
            "dldir1v6.qq.com",
            "tencent.com",
        ),
        "package_ids": ("Tencent.WeCom",),
    },
    "wecom": {
        "queries": ("WeCom", "企业微信", "WeChat Work", "WXWork"),
        "publisher_hints": ("Tencent",),
        "official_sites": ("https://work.weixin.qq.com/",),
        "download_pages": ("https://work.weixin.qq.com/#indexDownload",),
        "vendor_domains": (
            "qq.com",
            "work.weixin.qq.com",
            "dldir1v6.qq.com",
            "tencent.com",
        ),
        "package_ids": ("Tencent.WeCom",),
    },
    "wechat work": {
        "queries": ("WeChat Work", "WeCom", "企业微信", "WXWork"),
        "publisher_hints": ("Tencent",),
        "official_sites": ("https://work.weixin.qq.com/",),
        "download_pages": ("https://work.weixin.qq.com/#indexDownload",),
        "vendor_domains": (
            "qq.com",
            "work.weixin.qq.com",
            "dldir1v6.qq.com",
            "tencent.com",
        ),
        "package_ids": ("Tencent.WeCom",),
    },
    "wechat": {
        "queries": ("WeChat", "Weixin", "微信"),
        "publisher_hints": ("Tencent",),
        "official_sites": ("https://weixin.qq.com/", "https://pc.weixin.qq.com/"),
        "download_pages": (
            "https://pc.weixin.qq.com/",
            "https://weixin.qq.com/cgi-bin/readtemplate?lang=en_US&t=weixin_faq_list&head=true",
        ),
        "vendor_domains": (
            "qq.com",
            "weixin.qq.com",
            "pc.weixin.qq.com",
            "dldir1v6.qq.com",
            "tencent.com",
        ),
        "package_ids": ("Tencent.WeChat", "Tencent.WeChat.Universal", "Tencent.Weixin"),
    },
    "weixin": {
        "queries": ("WeChat", "Weixin", "微信"),
        "publisher_hints": ("Tencent",),
        "official_sites": ("https://weixin.qq.com/", "https://pc.weixin.qq.com/"),
        "download_pages": (
            "https://pc.weixin.qq.com/",
            "https://weixin.qq.com/cgi-bin/readtemplate?lang=en_US&t=weixin_faq_list&head=true",
        ),
        "vendor_domains": (
            "qq.com",
            "weixin.qq.com",
            "pc.weixin.qq.com",
            "dldir1v6.qq.com",
            "tencent.com",
        ),
        "package_ids": ("Tencent.WeChat", "Tencent.WeChat.Universal", "Tencent.Weixin"),
    },
    "微信": {
        "queries": ("WeChat", "Weixin", "微信"),
        "publisher_hints": ("Tencent",),
        "official_sites": ("https://weixin.qq.com/", "https://pc.weixin.qq.com/"),
        "download_pages": (
            "https://pc.weixin.qq.com/",
            "https://weixin.qq.com/cgi-bin/readtemplate?lang=en_US&t=weixin_faq_list&head=true",
        ),
        "vendor_domains": (
            "qq.com",
            "weixin.qq.com",
            "pc.weixin.qq.com",
            "dldir1v6.qq.com",
            "tencent.com",
        ),
        "package_ids": ("Tencent.WeChat", "Tencent.WeChat.Universal", "Tencent.Weixin"),
    },
    "qq": {
        "queries": ("QQ", "Tencent QQ"),
        "publisher_hints": ("Tencent",),
        "official_sites": ("https://im.qq.com/",),
        "download_pages": ("https://im.qq.com/pcqq/index.shtml",),
        "vendor_domains": ("qq.com", "im.qq.com", "dldir1.qq.com", "tencent.com"),
        "package_ids": ("Tencent.QQ",),
    },
    "vs code": {
        "queries": ("VS Code", "Visual Studio Code", "Code"),
        "publisher_hints": ("Microsoft",),
        "official_sites": ("https://code.visualstudio.com/",),
        "download_pages": ("https://code.visualstudio.com/Download",),
        "vendor_domains": ("code.visualstudio.com", "visualstudio.com", "microsoft.com"),
        "package_ids": ("Microsoft.VisualStudioCode", "Microsoft.VisualStudioCode.User"),
    },
    "visual studio code": {
        "queries": ("Visual Studio Code", "VS Code", "Code"),
        "publisher_hints": ("Microsoft",),
        "official_sites": ("https://code.visualstudio.com/",),
        "download_pages": ("https://code.visualstudio.com/Download",),
        "vendor_domains": ("code.visualstudio.com", "visualstudio.com", "microsoft.com"),
        "package_ids": ("Microsoft.VisualStudioCode", "Microsoft.VisualStudioCode.User"),
    },
    "vscode": {
        "queries": ("VS Code", "Visual Studio Code", "Code"),
        "publisher_hints": ("Microsoft",),
        "official_sites": ("https://code.visualstudio.com/",),
        "download_pages": ("https://code.visualstudio.com/Download",),
        "vendor_domains": ("code.visualstudio.com", "visualstudio.com", "microsoft.com"),
        "package_ids": ("Microsoft.VisualStudioCode", "Microsoft.VisualStudioCode.User"),
    },
}


@dataclass(frozen=True)
class BackendSelection:
    selected_backend: str
    backend_candidates: list[dict[str, Any]]
    degraded_reason: str | None = None
    blocked_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_backend": self.selected_backend,
            "backend_candidates": self.backend_candidates,
            "degraded_reason": self.degraded_reason,
            "blocked_reason": self.blocked_reason,
        }


class ExecutionBackendSelector:
    def select(
        self,
        preferred_backend: str = "auto",
        *,
        host_install: bool = False,
    ) -> BackendSelection:
        if host_install:
            return BackendSelection(
                selected_backend="host_executor",
                backend_candidates=[
                    _candidate(
                        "host_executor",
                        self._has_package_manager(),
                        "host install explicit",
                    ),
                    _candidate("manual_only", True, "manual fallback"),
                ],
            )
        candidates = [
            _candidate("container", _has_executable("docker"), "docker"),
            _candidate("wsl", _has_executable("wsl"), "wsl"),
            _candidate("local_workspace", True, "policy guarded fallback"),
        ]
        if preferred_backend not in {"", "auto"}:
            match = next(
                (
                    item
                    for item in candidates
                    if item["backend_type"] == preferred_backend
                ),
                None,
            )
            if match and match["available"]:
                return BackendSelection(
                    selected_backend=preferred_backend,
                    backend_candidates=candidates,
                )
        selected = next(item for item in candidates if item["available"])
        degraded = (
            "local_workspace_policy_guard"
            if selected["backend_type"] == "local_workspace"
            else None
        )
        return BackendSelection(
            selected_backend=str(selected["backend_type"]),
            backend_candidates=candidates,
            degraded_reason=degraded,
        )

    def _has_package_manager(self) -> bool:
        return any(_has_executable(name) for name in ("winget", "choco", "msiexec"))


class StackDetectorService:
    def detect(self, workspace_root: Path) -> dict[str, Any]:
        _ensure_within(workspace_root, workspace_root)
        package_json = workspace_root / "package.json"
        pyproject = workspace_root / "pyproject.toml"
        requirements = workspace_root / "requirements.txt"
        go_mod = workspace_root / "go.mod"
        cargo = workspace_root / "Cargo.toml"
        index_html = workspace_root / "index.html"
        if package_json.exists():
            manifest = _load_json_file(package_json)
            scripts = dict(manifest.get("scripts") or {}) if isinstance(manifest, dict) else {}
            package_manager = _node_package_manager(workspace_root)
            risky_scripts = _risky_scripts(scripts)
            return {
                "stack": "node",
                "package_manager": package_manager,
                "scripts": redact(scripts),
                "install_command": f"{package_manager} install",
                "build_command": _script_command(package_manager, scripts, "build"),
                "run_command": _script_command(package_manager, scripts, "dev")
                or _script_command(package_manager, scripts, "start"),
                "test_command": _script_command(package_manager, scripts, "test"),
                "risky_scripts": risky_scripts,
                "confidence": 0.95,
            }
        if pyproject.exists() or requirements.exists():
            run_command = _python_run_command(workspace_root)
            return {
                "stack": "python",
                "package_manager": "pip",
                "install_command": "pip install -r requirements.txt"
                if requirements.exists()
                else "pip install -e .",
                "run_command": run_command,
                "build_command": None,
                "risky_scripts": [],
                "confidence": 0.8,
            }
        if go_mod.exists():
            return {
                "stack": "go",
                "run_command": "go run .",
                "confidence": 0.7,
                "risky_scripts": [],
            }
        if cargo.exists():
            return {
                "stack": "rust",
                "run_command": "cargo run",
                "confidence": 0.7,
                "risky_scripts": [],
            }
        if index_html.exists():
            return {
                "stack": "static",
                "run_command": "python -m http.server",
                "confidence": 0.75,
                "risky_scripts": [],
            }
        return {
            "stack": "unknown",
            "confidence": 0.2,
            "risky_scripts": [],
            "execution_allowed": False,
            "reason": "unknown_stack_requires_clarification",
        }


class ProjectWorkspaceService:
    def __init__(
        self,
        *,
        repo: ProjectDeploymentRepository,
        member_repo: MemberRepository,
        data_dir: Path,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._members = member_repo
        self._root = data_dir / "workspaces" / "projects"
        self._trace = trace_service
        self._audit = audit_service
        self._backend_selector = ExecutionBackendSelector()

    async def create(
        self,
        request: ProjectWorkspaceCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> ProjectWorkspace:
        member = await self._members.get_member(request.owner_member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        _validate_source_uri(request.source_uri)
        selection = self._backend_selector.select(request.preferred_backend)
        workspace_id = new_id("pws")
        root = (self._root / workspace_id).resolve()
        _ensure_within(self._root.resolve(), root)
        root.mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        data = {
            "workspace_id": workspace_id,
            "organization_id": ORG_DEFAULT,
            "task_id": request.task_id,
            "owner_member_id": request.owner_member_id,
            "source_type": request.source_type,
            "source_uri": str(redact(request.source_uri)) if request.source_uri else None,
            "root_uri": f"workspace://projects/{workspace_id}",
            "backend_type": selection.selected_backend,
            "status": "created",
            "stack_summary": {},
            "policy_snapshot": {
                "workspace_root": "data/workspaces/projects/{workspace_id}",
                "filesystem_policy": "workspace_root_only",
                "backend_selection": selection.as_dict(),
                "constraints": redact(request.constraints),
            },
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_workspace(data)
        return await self.detail(workspace_id)

    async def detail(self, workspace_id: str) -> ProjectWorkspace:
        row = await self._repo.get_workspace(workspace_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "项目工作区不存在", status_code=404)
        return ProjectWorkspace(**row)

    def path_for(self, workspace: ProjectWorkspace) -> Path:
        if not workspace.root_uri.startswith("workspace://projects/"):
            raise AppError(ErrorCode.VALIDATION_ERROR, "不支持的工作区 URI", status_code=422)
        workspace_id = workspace.root_uri.removeprefix("workspace://projects/")
        path = (self._root / workspace_id).resolve()
        _ensure_within(self._root.resolve(), path)
        return path


class ToolchainService:
    def __init__(
        self,
        *,
        repo: ProjectDeploymentRepository,
        data_dir: Path,
    ) -> None:
        self._repo = repo
        self._root = data_dir / "toolchains"

    async def ensure(
        self,
        request: ToolchainEnsureRequest,
        *,
        trace_id: str | None = None,
    ) -> ToolchainInstall:
        runtime = _safe_key(request.runtime_name)
        version = _safe_key(request.version)
        mode = _safe_key(request.install_mode or "portable")
        if mode != "portable":
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "默认只支持 portable toolchain；全局安装请创建 host install plan",
                status_code=422,
            )
        root = (self._root / runtime / version).resolve()
        _ensure_within(self._root.resolve(), root)
        root.mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        existing = await self._repo.get_toolchain(
            runtime_name=runtime,
            version=version,
            install_mode=mode,
        )
        toolchain_id = existing["toolchain_id"] if existing else new_id("tc")
        source_uri = request.source_uri or _default_toolchain_source(runtime, version)
        data = {
            "toolchain_id": toolchain_id,
            "organization_id": ORG_DEFAULT,
            "runtime_name": runtime,
            "version": version,
            "install_mode": mode,
            "root_uri": f"toolchain://{runtime}/{version}",
            "source_uri": str(redact(source_uri)),
            "checksum": request.checksum or "sha256:deferred",
            "status": "installed" if (root / ".installed").exists() else "planned",
            "policy_snapshot": {
                "path_policy": "data/toolchains_only",
                "modifies_global_path": False,
                "download_requires_approval": True,
                "task_id": request.task_id,
            },
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        marker = root / ".installed"
        marker.write_text("planned portable toolchain placeholder\n", encoding="utf-8")
        data["status"] = "installed"
        await self._repo.insert_toolchain(data)
        row = await self._repo.get_toolchain(
            runtime_name=runtime,
            version=version,
            install_mode=mode,
        )
        return ToolchainInstall(**(row or data))

    async def list(self, runtime_name: str | None = None) -> list[ToolchainInstall]:
        rows = await self._repo.list_toolchains(runtime_name=runtime_name)
        return [ToolchainInstall(**row) for row in rows]


class ProjectDeploymentService:
    def __init__(
        self,
        *,
        repo: ProjectDeploymentRepository,
        workspace_service: ProjectWorkspaceService,
        toolchain_service: ToolchainService,
        task_engine: TaskEngine,
        task_repo: TaskRepository,
        approval_service: ApprovalService,
        artifact_store: ArtifactStore,
        data_dir: Path,
        trace_service: TraceService,
        audit_service: AuditEventService,
        safety_policy_service: RuntimeSafetyPolicyService | None = None,
    ) -> None:
        self._repo = repo
        self._workspaces = workspace_service
        self._toolchains = toolchain_service
        self._task_engine = task_engine
        self._tasks = task_repo
        self._approvals = approval_service
        self._artifacts = artifact_store
        self._data_dir = data_dir
        self._trace = trace_service
        self._audit = audit_service
        self._safety_policy = safety_policy_service
        self._stack_detector = StackDetectorService()
        self._backend_selector = ExecutionBackendSelector()

    async def create_plan(
        self,
        request: ProjectDeployRequest,
        *,
        trace_id: str | None = None,
    ) -> ProjectDeployment:
        _validate_source_uri(request.source_uri)
        task_id = request.task_id
        if task_id is None:
            task = await self._task_engine.create_task(
                TaskCreateRequest(
                    conversation_id=request.conversation_id,
                    owner_member_id=request.member_id,
                    goal=f"部署项目 {request.source_uri}",
                    mode_hint=TaskMode.AGENT,
                    constraints={
                        "phase52": "project_deployment",
                        "source_uri": request.source_uri,
                        "target": request.target,
                    },
                    planner_context={
                        "phase52": {
                            "workflow": "project_deployment",
                            "source_uri": str(redact(request.source_uri)),
                        }
                    },
                    auto_start=False,
                ),
                trace_id=trace_id,
            )
            task_id = task.task_id
        workspace = await self._workspaces.create(
            ProjectWorkspaceCreateRequest(
                owner_member_id=request.member_id,
                task_id=task_id,
                source_type="github" if "github.com" in request.source_uri.lower() else "git_https",
                source_uri=request.source_uri,
                preferred_backend=str(request.target.get("preferred_backend") or "auto"),
                constraints=request.constraints,
            ),
            trace_id=trace_id,
        )
        selection = self._backend_selector.select(
            str(request.target.get("preferred_backend") or "auto")
        )
        plan = _deployment_plan(
            source_uri=request.source_uri,
            workspace=workspace,
            backend=selection,
            constraints=request.constraints,
        )
        approval_binding_hash = _deployment_binding_hash(plan)
        approval_required = True
        if self._safety_policy is not None:
            policy = await self._safety_policy.get_policy(organization_id=ORG_DEFAULT)
            approval_required = not policy.should_skip_approval(
                action="project.deployment.run",
                risk_level=RiskLevel.R4,
                action_category="project_deployment",
                payload={
                    "source_uri": request.source_uri,
                    "backend_type": selection.selected_backend,
                    "preferred_port": plan.get("preferred_port"),
                },
            )
        now = utc_now_iso()
        deployment_id = new_id("dep")
        await self._repo.insert_deployment(
            {
                "deployment_id": deployment_id,
                "organization_id": ORG_DEFAULT,
                "workspace_id": workspace.workspace_id,
                "task_id": task_id,
                "status": "waiting_approval" if approval_required else "planned",
                "backend_type": selection.selected_backend,
                "plan": plan,
                "endpoint": {},
                "health": {"status": "not_started"},
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        if approval_required:
            approval = await self._approvals.create_approval(
                task_id=task_id,
                organization_id=ORG_DEFAULT,
                requested_action="project.deployment.run",
                risk_level=RiskLevel.R4,
                summary=f"需要确认部署项目 {redact(request.source_uri)}",
                payload={
                    "deployment_id": deployment_id,
                    "workspace_id": workspace.workspace_id,
                    "source_uri": redact(request.source_uri),
                    "backend_type": selection.selected_backend,
                    "preferred_port": plan.get("preferred_port"),
                    "approval_binding_hash": approval_binding_hash,
                },
                trace_id=trace_id,
            )
            plan["approval_strategy"] = {
                **dict(plan.get("approval_strategy") or {}),
                "approval_id": approval.approval_id,
                "approval_binding_hash": approval_binding_hash,
                "status": "required",
            }
        else:
            plan["approval_strategy"] = {
                **dict(plan.get("approval_strategy") or {}),
                "approval_id": None,
                "approval_binding_hash": approval_binding_hash,
                "status": "not_required_balanced_personal",
            }
        await self._repo.update_deployment(
            deployment_id,
            {"plan": plan, "updated_at": utc_now_iso()},
        )
        return await self.detail(deployment_id)

    async def detail(self, deployment_id: str) -> ProjectDeployment:
        row = await self._repo.get_deployment(deployment_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "项目部署不存在", status_code=404)
        return ProjectDeployment(**row)

    async def run(
        self,
        deployment_id: str,
        *,
        approval_id: str | None = None,
        trace_id: str | None = None,
    ) -> ProjectDeployment:
        deployment = await self.detail(deployment_id)
        if deployment.status in DEPLOYMENT_TERMINAL_STATUSES:
            return deployment
        approval_strategy = dict(deployment.plan.get("approval_strategy") or {})
        approval_required = str(approval_strategy.get("status") or "") == "required"
        if approval_id is None:
            if not approval_required:
                await self._repo.update_deployment(
                    deployment_id,
                    {
                        "status": "planned",
                        "health": {"status": "not_started"},
                        "updated_at": utc_now_iso(),
                    },
                )
            else:
                await self._repo.update_deployment(
                    deployment_id,
                    {
                        "status": "waiting_approval",
                        "health": {"status": "waiting_approval"},
                        "updated_at": utc_now_iso(),
                    },
                )
                return await self.detail(deployment_id)
        if approval_id is not None and approval_required:
            await self._verify_deployment_approval(deployment, approval_id)
        workspace = await self._workspaces.detail(deployment.workspace_id)
        workspace_path = self._workspaces.path_for(workspace)
        await self._repo.update_deployment(
            deployment_id,
            {"status": "running", "current_step_key": "clone", "updated_at": utc_now_iso()},
        )
        log_lines: list[str] = []
        try:
            source_uri = str(deployment.plan.get("source_uri") or workspace.source_uri or "")
            await self._clone_or_prepare_source(source_uri, workspace_path, log_lines)
            stack = self._stack_detector.detect(workspace_path)
            await self._repo.update_workspace(
                workspace.workspace_id,
                {
                    "status": "ready",
                    "stack_summary": stack,
                    "updated_at": utc_now_iso(),
                },
            )
            log_lines.append(f"stack={stack.get('stack')} backend={deployment.backend_type}")
            if stack.get("stack") == "unknown":
                raise AppError(
                    ErrorCode.TASK_PLAN_FAILED,
                    "未知项目栈，已停止执行并等待补充启动方式",
                    status_code=422,
                    details={"reason_code": "unknown_stack_requires_clarification"},
                )
            if stack.get("stack") == "node":
                await self._toolchains.ensure(
                    ToolchainEnsureRequest(
                        runtime_name="node",
                        version="lts",
                        task_id=deployment.task_id,
                    ),
                    trace_id=trace_id,
                )
            port = await self._lease_port(
                deployment,
                preferred=deployment.plan.get("preferred_port"),
            )
            endpoint_url = f"http://127.0.0.1:{port}"
            command, install_summary, build_summary = await self._prepare_runtime(
                stack,
                workspace_path,
                port,
                log_lines,
            )
            process_handle, startup_log = await self._start_preview_process(
                command,
                workspace_path,
                port=port,
                endpoint_url=endpoint_url,
            )
            log_lines.extend(startup_log)
            health = await _wait_for_http(endpoint_url)
            if not health["ok"]:
                await _terminate_process(process_handle)
                raise AppError(
                    ErrorCode.TASK_PLAN_FAILED,
                    "项目预览服务启动后健康检查失败",
                    status_code=422,
                    details=health,
                )
            log_lines.extend(
                [
                    f"install_deps={install_summary}",
                    f"build={build_summary}",
                    f"run=process_started endpoint={endpoint_url}",
                    f"health_check=passed status={health.get('status')}",
                ]
            )
            artifact = await self._artifacts.write_text(
                task_id=deployment.task_id,
                organization_id=ORG_DEFAULT,
                display_name="deployment.log",
                content="\n".join(log_lines),
                artifact_type="deployment_log",
                subdir="logs",
                metadata={
                    "deployment_id": deployment_id,
                    "workspace_id": workspace.workspace_id,
                    "backend_type": deployment.backend_type,
                },
                trace_id=trace_id,
            )
            process = await self._create_process(
                deployment,
                workspace=workspace,
                port=port,
                endpoint_url=endpoint_url,
                log_artifact_id=artifact.artifact_id,
                command=" ".join(command),
                process_handle=process_handle,
                trace_id=trace_id,
            )
            await self._repo.update_deployment(
                deployment_id,
                {
                    "status": "healthy",
                    "current_step_key": "health_check",
                    "endpoint": {
                        "url": endpoint_url,
                        "port": port,
                        "managed_process_id": process.managed_process_id,
                    },
                    "health": {
                        "status": "passed",
                        "log_artifact_id": artifact.artifact_id,
                        "degraded_isolation": deployment.backend_type == "local_workspace",
                    },
                    "updated_at": utc_now_iso(),
                },
            )
            return await self.detail(deployment_id)
        except Exception as exc:
            artifact = await self._artifacts.write_text(
                task_id=deployment.task_id,
                organization_id=ORG_DEFAULT,
                display_name="deployment-failure.log",
                content="\n".join([*log_lines, f"failure={redact(str(exc))}"]),
                artifact_type="deployment_log",
                subdir="logs",
                metadata={"deployment_id": deployment_id, "status": "failed"},
                trace_id=trace_id,
            )
            await self._repo.update_deployment(
                deployment_id,
                {
                    "status": "failed",
                    "failure_reason": str(redact(str(exc))),
                    "health": {
                        "status": "failed",
                        "log_artifact_id": artifact.artifact_id,
                        "recoverable": True,
                    },
                    "updated_at": utc_now_iso(),
                },
            )
            port_row = await self._repo.get_port_lease_for_deployment(deployment_id)
            if port_row is not None and port_row.get("status") == "active":
                await self._repo.update_port_lease(
                    port_row["port_lease_id"],
                    {"status": "released", "updated_at": utc_now_iso()},
                )
            return await self.detail(deployment_id)

    async def stop(self, deployment_id: str, *, trace_id: str | None = None) -> ProjectDeployment:
        await self.detail(deployment_id)
        process_row = await self._repo.get_managed_process_for_deployment(deployment_id)
        if process_row is not None:
            process_id = str(process_row["managed_process_id"])
            process_handle = _MANAGED_PROCESS_HANDLES.pop(process_id, None)
            if process_handle is not None:
                await _terminate_process(process_handle)
            await self._repo.update_managed_process(
                process_id,
                {"status": "stopped", "stopped_at": utc_now_iso(), "updated_at": utc_now_iso()},
            )
        port_row = await self._repo.get_port_lease_for_deployment(deployment_id)
        if port_row is not None:
            await self._repo.update_port_lease(
                port_row["port_lease_id"],
                {"status": "released", "updated_at": utc_now_iso()},
            )
        await self._repo.update_deployment(
            deployment_id,
            {
                "status": "stopped",
                "health": {"status": "stopped"},
                "updated_at": utc_now_iso(),
            },
        )
        return await self.detail(deployment_id)

    async def logs(self, deployment_id: str) -> dict[str, Any]:
        deployment = await self.detail(deployment_id)
        process = await self._repo.get_managed_process_for_deployment(deployment_id)
        log_artifact_id = None
        if process is not None:
            log_artifact_id = process.get("log_artifact_id")
        if not log_artifact_id:
            log_artifact_id = deployment.health.get("log_artifact_id")
        if not log_artifact_id:
            return {
                "deployment": deployment,
                "log_artifact_id": None,
                "content_preview": None,
                "status": "unavailable",
                "reason_code": "deployment_log_missing",
                "recoverable": True,
                "next_step": "先运行部署或查看失败步骤。",
            }
        _artifact, preview = await self._artifacts.read_preview(str(log_artifact_id))
        return {
            "deployment": deployment,
            "log_artifact_id": log_artifact_id,
            "content_preview": preview,
            "status": "completed",
            "reason_code": "deployment_log_available",
            "recoverable": False,
            "next_step": None,
        }

    async def _clone_or_prepare_source(
        self,
        source_uri: str,
        workspace_path: Path,
        log_lines: list[str],
    ) -> None:
        parsed = urlparse(source_uri)
        if parsed.scheme in {"", "file"}:
            raise AppError(ErrorCode.TOOL_PERMISSION_DENIED, "拒绝本地路径部署源", status_code=403)
        if source_uri.startswith("fixture://"):
            fixture_name = source_uri.removeprefix("fixture://")
            _write_fixture_project(workspace_path, fixture_name)
            log_lines.append(f"fixture={fixture_name}")
            return
        if "github.com" in parsed.netloc.lower() or source_uri.endswith(".git"):
            if any(workspace_path.iterdir()):
                shutil.rmtree(workspace_path)
                workspace_path.mkdir(parents=True, exist_ok=True)
            git = shutil.which("git")
            if git is None:
                raise AppError(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    "本机缺少 git，无法 clone 公开仓库",
                    status_code=503,
                )
            completed = await _run_command(
                [git, "clone", "--depth", "1", source_uri, str(workspace_path)],
                cwd=workspace_path.parent,
                timeout=180,
            )
            if completed["exit_code"] != 0:
                raise AppError(
                    ErrorCode.TASK_PLAN_FAILED,
                    "公开仓库 clone 失败",
                    status_code=422,
                    details={"stderr": completed["stderr_tail"]},
                )
            log_lines.append("clone=completed")
            return
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "只支持公开 GitHub/HTTPS git 或 fixture 项目源",
            status_code=422,
        )

    async def _prepare_runtime(
        self,
        stack: dict[str, Any],
        workspace_path: Path,
        port: int,
        log_lines: list[str],
    ) -> tuple[list[str], str, str]:
        stack_name = str(stack.get("stack") or "")
        if stack_name == "node":
            if _node_static_preview_ok(workspace_path):
                python = sys.executable
                return [
                    python,
                    "-m",
                    "http.server",
                    str(port),
                    "--bind",
                    "127.0.0.1",
                ], "skipped_static_node", "skipped"
            npm = shutil.which(str(stack.get("package_manager") or "npm"))
            if npm is None:
                raise AppError(
                    ErrorCode.TOOL_EXECUTION_FAILED,
                    "本机缺少 npm，无法启动 Node 项目",
                    status_code=503,
                )
            install = await _run_command([npm, "install"], cwd=workspace_path, timeout=180)
            log_lines.append(f"npm_install_exit={install['exit_code']}")
            if install["exit_code"] != 0:
                raise AppError(
                    ErrorCode.TASK_PLAN_FAILED,
                    "Node 依赖安装失败",
                    status_code=422,
                    details={"stderr": install["stderr_tail"]},
                )
            run_command = str(stack.get("run_command") or "")
            if "start" in run_command:
                command = [npm, "run", "start"]
            else:
                command = [
                    npm,
                    "run",
                    "dev",
                    "--",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ]
            return command, "completed", "skipped"
        python = sys.executable
        if stack_name == "python":
            return [
                python,
                "-m",
                "http.server",
                str(port),
                "--bind",
                "127.0.0.1",
            ], "skipped_preview_mode", "skipped"
        if stack_name == "static":
            return [
                python,
                "-m",
                "http.server",
                str(port),
                "--bind",
                "127.0.0.1",
            ], "skipped_static", "skipped"
        if stack_name in {"go", "rust"}:
            raise AppError(
                ErrorCode.TASK_PLAN_FAILED,
                f"{stack_name} 项目真实运行暂未接入",
                status_code=422,
            )
        raise AppError(
            ErrorCode.TASK_PLAN_FAILED,
            "未知项目栈，无法准备运行时",
            status_code=422,
        )

    async def _start_preview_process(
        self,
        command: list[str],
        workspace_path: Path,
        *,
        port: int,
        endpoint_url: str,
    ) -> tuple[asyncio.subprocess.Process, list[str]]:
        env = {**os.environ, "PORT": str(port)}
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workspace_path),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return process, [
            f"process_pid={process.pid}",
            f"process_command={redact(' '.join(command))}",
            f"process_endpoint={endpoint_url}",
        ]

    async def _lease_port(self, deployment: ProjectDeployment, *, preferred: Any) -> int:
        port = int(preferred or 5173)
        for candidate in range(port, port + 100):
            if (
                await self._repo.get_active_port_lease(candidate) is None
                and _port_available(candidate)
            ):
                now = utc_now_iso()
                await self._repo.insert_port_lease(
                    {
                        "port_lease_id": new_id("port"),
                        "organization_id": ORG_DEFAULT,
                        "task_id": deployment.task_id,
                        "deployment_id": deployment.deployment_id,
                        "port": candidate,
                        "protocol": "http",
                        "status": "active",
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                return candidate
        raise AppError(ErrorCode.CONFLICT, "没有可用本地端口", status_code=409)

    async def _verify_deployment_approval(
        self,
        deployment: ProjectDeployment,
        approval_id: str,
    ) -> None:
        approval_strategy = dict(deployment.plan.get("approval_strategy") or {})
        expected_approval_id = str(approval_strategy.get("approval_id") or "")
        if approval_id != expected_approval_id:
            raise AppError(
                ErrorCode.TOOL_APPROVAL_REQUIRED,
                "项目部署必须先通过绑定审批",
                status_code=409,
            )
        approval = await self._approvals.get(approval_id)
        if approval.status not in {"approved", "edited"}:
            raise AppError(
                ErrorCode.TOOL_APPROVAL_REQUIRED,
                "项目部署审批尚未通过",
                status_code=409,
            )
        expected_hash = str(approval_strategy.get("approval_binding_hash") or "")
        actual_hash = str((approval.payload_redacted or {}).get("approval_binding_hash") or "")
        if expected_hash and actual_hash != expected_hash:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "项目部署审批绑定已变化，拒绝执行",
                status_code=403,
            )

    async def _create_process(
        self,
        deployment: ProjectDeployment,
        *,
        workspace: ProjectWorkspace,
        port: int,
        endpoint_url: str,
        log_artifact_id: str,
        command: str,
        process_handle: asyncio.subprocess.Process | None = None,
        trace_id: str | None,
    ) -> ManagedProcess:
        now = utc_now_iso()
        managed_process_id = new_id("mpr")
        data = {
            "managed_process_id": managed_process_id,
            "organization_id": ORG_DEFAULT,
            "deployment_id": deployment.deployment_id,
            "task_id": deployment.task_id,
            "workspace_id": workspace.workspace_id,
            "process_kind": "preview_server",
            "command_redacted": {"command": str(redact(command)), "cwd": "project_workspace"},
            "backend_type": deployment.backend_type,
            "status": "running",
            "port": port,
            "endpoint_url": endpoint_url,
            "log_artifact_id": log_artifact_id,
            "started_at": now,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_managed_process(data)
        if process_handle is not None:
            _MANAGED_PROCESS_HANDLES[managed_process_id] = process_handle
        row = await self._repo.get_managed_process_for_deployment(deployment.deployment_id)
        return ManagedProcess(**(row or data))


class HostInstallService:
    def __init__(
        self,
        *,
        repo: ProjectDeploymentRepository,
        task_engine: TaskEngine,
        task_repo: TaskRepository,
        approval_service: ApprovalService,
        artifact_store: ArtifactStore,
        trace_service: TraceService,
        audit_service: AuditEventService,
        brain_repo: Any | None = None,
        model_routing_service: Any | None = None,
        secret_store: Any | None = None,
        safety_policy_service: RuntimeSafetyPolicyService | None = None,
    ) -> None:
        self._repo = repo
        self._task_engine = task_engine
        self._task_repo = task_repo
        self._approvals = approval_service
        self._artifacts = artifact_store
        self._trace = trace_service
        self._audit = audit_service
        self._brain_repo = brain_repo
        self._model_routing = model_routing_service
        self._secrets = secret_store
        self._safety_policy = safety_policy_service
        self._model_router = ModelRouter()

    async def create_plan(
        self,
        request: HostInstallPlanRequest,
        *,
        trace_id: str | None = None,
    ) -> HostInstallPlan:
        software = request.requested_software.strip()
        if not software:
            raise AppError(ErrorCode.VALIDATION_ERROR, "requested_software 必填", status_code=422)
        action = _host_software_action(software)
        action_label = "卸载" if action == "uninstall" else "安装"
        task_id = request.task_id
        if task_id is None:
            task = await self._task_engine.create_task(
                TaskCreateRequest(
                    conversation_id=request.conversation_id,
                    owner_member_id=request.member_id,
                    goal=f"{action_label}本机软件 {_host_install_visible_software_name(software)}",
                    mode_hint=TaskMode.AGENT,
                    constraints={
                        "phase52": "host_install",
                        "requested_software": software,
                        "action": action,
                    },
                    planner_context={"phase52": {"workflow": "host_install", "action": action}},
                    auto_start=False,
                ),
                trace_id=trace_id,
            )
            task_id = task.task_id
        source, command, impact, status = await self._host_install_plan_for(
            software,
            trace_id=trace_id,
            task_id=task_id,
        )
        now = utc_now_iso()
        risk = str(impact.get("risk_level") or ("R6" if status == "manual_only" else "R5"))
        host_install_plan_id = new_id("hip")
        await self._repo.insert_host_plan(
            {
                "host_install_plan_id": host_install_plan_id,
                "organization_id": ORG_DEFAULT,
                "task_id": task_id,
                "requested_software": str(redact(_host_install_visible_software_name(software))),
                "install_source": source,
                "command_preview": command,
                "impact_summary": impact,
                "risk_level": risk,
                "status": status,
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        if status == "waiting_approval":
            approval = await self._approvals.create_approval(
                task_id=task_id,
                organization_id=ORG_DEFAULT,
                requested_action=(
                    "host.uninstall_software"
                    if action == "uninstall"
                    else "host.install_software"
                ),
                risk_level=RiskLevel(risk),
                summary=(
                    f"需要确认{action_label}本机软件 "
                    f"{redact(_host_install_visible_software_name(software))}"
                ),
                payload={
                    "host_install_plan_id": host_install_plan_id,
                    "requested_software": redact(_host_install_visible_software_name(software)),
                    "host_action": action,
                    "install_source": source,
                    "command_preview": command,
                    "impact_summary": impact,
                    "approval_binding_hash": _binding_hash(source, command, impact),
                },
                trace_id=trace_id,
            )
            await self._repo.update_host_plan(
                host_install_plan_id,
                {
                    "approval_id": approval.approval_id,
                    "updated_at": utc_now_iso(),
                },
            )
        return await self.detail(host_install_plan_id)

    async def detail(self, host_install_plan_id: str) -> HostInstallPlan:
        row = await self._repo.get_host_plan(host_install_plan_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "安装计划不存在", status_code=404)
        return HostInstallPlan(**row)

    async def execute_for_approval(
        self,
        approval_id: str,
        *,
        dry_run: bool = False,
        trace_id: str | None = None,
    ) -> HostInstallExecution | None:
        approval = await self._approvals.get(approval_id)
        if approval.requested_action not in {
            "host.install_software",
            "host.uninstall_software",
        }:
            return None
        payload = dict(approval.payload_redacted or {})
        host_install_plan_id = str(payload.get("host_install_plan_id") or "").strip()
        if not host_install_plan_id:
            row = await self._repo.get_host_plan_by_approval_id(approval_id)
            host_install_plan_id = str((row or {}).get("host_install_plan_id") or "").strip()
        if not host_install_plan_id:
            raise AppError(
                ErrorCode.TOOL_APPROVAL_REQUIRED,
                "审批未绑定本机软件安装计划，拒绝执行",
                status_code=409,
            )
        return await self.execute(
            host_install_plan_id,
            approval_id=approval_id,
            dry_run=dry_run,
            trace_id=trace_id,
        )

    async def execute(
        self,
        host_install_plan_id: str,
        *,
        approval_id: str | None,
        dry_run: bool = True,
        trace_id: str | None = None,
    ) -> HostInstallExecution:
        plan = await self.detail(host_install_plan_id)
        if plan.status == "manual_only":
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "该软件安装需要人工处理，拒绝自动执行",
                status_code=403,
            )
        if plan.status in {"installed", "uninstalled"}:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "本机软件变更已执行，拒绝重复执行",
                status_code=409,
            )
        if not approval_id or approval_id != plan.approval_id:
            raise AppError(
                ErrorCode.TOOL_APPROVAL_REQUIRED,
                "本机安装必须先通过绑定审批",
                status_code=409,
            )
        approval = await self._approvals.get(approval_id)
        if approval.status not in {"approved", "edited"}:
            raise AppError(ErrorCode.TOOL_APPROVAL_REQUIRED, "审批尚未通过", status_code=409)
        expected = _binding_hash(plan.install_source, plan.command_preview, plan.impact_summary)
        actual = str((approval.payload_redacted or {}).get("approval_binding_hash") or "")
        if actual != expected:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "审批绑定已变化，拒绝执行",
                status_code=403,
            )
        execution: dict[str, Any]
        if dry_run:
            execution = {
                "exit_code": 0,
                "version_detected": "dry-run",
                "install_path_summary": "not_modified_dry_run",
                "failure_reason": None,
                "log": (
                    "host install dry-run\n"
                    f"software={redact(plan.requested_software)}\n"
                    f"command={redact(plan.command_preview)}\n"
                    f"steps={redact(_host_command_steps(plan.command_preview))}\n"
                    "real_execution=false\n"
                ),
            }
        else:
            execution = await self._execute_host_command(plan, trace_id=trace_id)
        artifact = await self._artifacts.write_text(
            task_id=plan.task_id,
            organization_id=ORG_DEFAULT,
            display_name="host-install.log",
            content=str(execution["log"]),
            artifact_type="host_install_log",
            subdir="logs",
            metadata={"host_install_plan_id": host_install_plan_id, "dry_run": dry_run},
            trace_id=trace_id,
        )
        now = utc_now_iso()
        success = execution["exit_code"] == 0
        action = _host_install_plan_action(plan)
        success_status = "uninstalled" if action == "uninstall" else "installed"
        status = success_status if success else "failed"
        data = {
            "host_install_execution_id": new_id("hie"),
            "organization_id": ORG_DEFAULT,
            "host_install_plan_id": host_install_plan_id,
            "task_id": plan.task_id,
            "status": status,
            "exit_code": execution["exit_code"],
            "log_artifact_id": artifact.artifact_id,
            "version_detected": execution["version_detected"],
            "install_path_summary": execution["install_path_summary"],
            "failure_reason": execution["failure_reason"],
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_host_execution(data)
        await self._repo.update_host_plan(
            host_install_plan_id,
            {"status": status, "updated_at": utc_now_iso()},
        )
        await self._sync_task_after_execution(plan, data, dry_run=dry_run, trace_id=trace_id)
        return HostInstallExecution(**data)

    async def _sync_task_after_execution(
        self,
        plan: HostInstallPlan,
        execution: dict[str, Any],
        *,
        dry_run: bool,
        trace_id: str | None,
    ) -> None:
        success = execution["status"] in {"installed", "uninstalled"}
        action = _host_install_plan_action(plan)
        result = {
            "workflow": "host_install",
            "host_action": action,
            "host_install_plan_id": plan.host_install_plan_id,
            "host_install_execution_id": execution["host_install_execution_id"],
            "status": execution["status"],
            "exit_code": execution.get("exit_code"),
            "log_artifact_id": execution.get("log_artifact_id"),
            "version_detected": execution.get("version_detected"),
            "install_path_summary": execution.get("install_path_summary"),
            "dry_run": dry_run,
        }
        now = utc_now_iso()
        await self._task_repo.update_task(
            plan.task_id,
            {
                "status": TaskStatus.COMPLETED.value if success else TaskStatus.FAILED.value,
                "result": result,
                "failure_reason": None if success else execution.get("failure_reason"),
                "current_approval_id": None,
                "updated_at": now,
            },
        )
        await self._task_repo.update_job_by_idempotency(
            f"task.run:{plan.task_id}",
            {
                "status": TaskStatus.COMPLETED.value if success else TaskStatus.FAILED.value,
                "locked_by": None,
                "locked_at": None,
                "error_code": None if success else ErrorCode.TASK_STEP_FAILED.value,
                "error_summary": None if success else execution.get("failure_reason"),
                "updated_at": now,
            },
        )
        event_type = "host_install.completed" if success else "host_install.failed"
        await self._task_repo.insert_event(
            {
                "event_id": new_id("tevt"),
                "organization_id": ORG_DEFAULT,
                "task_id": plan.task_id,
                "event_type": event_type,
                "payload": result,
                "payload_redacted": redact(result),
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        await self._audit.write_event(
            actor_type="system",
            action=event_type,
            object_type="host_install_plan",
            object_id=plan.host_install_plan_id,
            summary=(
                "本机软件卸载已完成"
                if execution["status"] == "uninstalled"
                else "本机软件安装已完成"
                if success
                else "本机软件变更失败"
            ),
            risk_level=RiskLevel.R2 if success else RiskLevel.R3,
            payload={
                "host_install_plan_id": plan.host_install_plan_id,
                "host_action": action,
                "status": execution["status"],
            },
            trace_id=trace_id,
        )

    async def _host_install_plan_for(
        self,
        software: str,
        *,
        trace_id: str | None,
        task_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
        return await _host_install_plan_for(
            software,
            model_candidates_provider=lambda query: self._model_package_candidates(
                query,
                trace_id=trace_id,
                task_id=task_id,
            ),
        )

    async def _model_package_candidates(
        self,
        query: str,
        *,
        trace_id: str | None,
        task_id: str,
    ) -> list[HostSoftwareModelCandidate]:
        if not trace_id:
            return []
        if not all((self._brain_repo, self._model_routing, self._secrets)):
            return []
        brain_repo = self._brain_repo
        model_routing = self._model_routing
        secret_store = self._secrets
        assert brain_repo is not None
        assert model_routing is not None
        assert secret_store is not None
        span_id = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MODEL_CALL,
            name="resolve host software package candidates",
            input_data={"query": redact(query)},
            metadata={"purpose": "host_install_package_resolution"},
        )
        try:
            config = await model_routing.get_config()
            brains = await brain_repo.list_routable_brains()
            route = self._model_router.select_route(
                BrainRouteRequest(
                    text=f"Resolve package manager IDs for host software: {query}",
                    privacy_level="medium",
                    estimated_input_tokens=512,
                    available_brains=brains,
                    model_routing_config=config,
                    requires_tool_calling=False,
                )
            )
            if route is None:
                await self._trace.end_span(
                    span_id,
                    status=TraceSpanStatus.FAILED,
                    output_data={"reason": "no_routable_model"},
                )
                return []
            brain = await brain_repo.get_brain(route.primary_brain_id)
            if brain is None:
                await self._trace.end_span(
                    span_id,
                    status=TraceSpanStatus.FAILED,
                    output_data={"reason": "selected_brain_missing"},
                )
                return []
            client = OpenAICompatibleClient(
                str(brain["endpoint"]),
                secret_store.get_secret(brain.get("api_key_ref")),
            )
            result = await client.complete_chat(
                ModelChatRequest(
                    model=str(brain["model_name"]),
                    messages=_host_package_model_messages(query),
                    temperature=0.0,
                    max_output_tokens=1024,
                    top_p=0.2,
                    timeout_seconds=min(int(brain.get("timeout_seconds") or 180), 30),
                    stream=False,
                    trace_id=trace_id,
                    turn_id=f"task:{task_id}",
                    route_id=f"host-install-package-resolution:{brain['brain_id']}",
                    privacy_level="medium",
                    retry_count=0,
                    metadata={"purpose": "host_install_package_resolution"},
                ),
                CancelToken(),
            )
            candidates = _parse_host_package_model_candidates(result.text)
            await self._trace.end_span(
                span_id,
                output_data={
                    "finish_reason": result.finish_reason,
                    "usage": result.usage,
                    "candidate_count": len(candidates),
                    "brain_id": brain["brain_id"],
                },
            )
            return candidates
        except (ModelAdapterError, json.JSONDecodeError, TypeError, ValueError) as exc:
            await self._trace.end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error": str(redact(str(exc)))[:240]},
            )
            return []

    async def _execute_host_command(
        self,
        plan: HostInstallPlan,
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        steps = _host_command_steps(plan.command_preview)
        if not steps:
            return {
                "exit_code": 1,
                "version_detected": None,
                "install_path_summary": "not_executed",
                "failure_reason": "empty_host_install_command",
                "log": "host install failed: empty command\n",
            }
        log_lines = [
            "host install real execution",
            f"software={redact(plan.requested_software)}",
            f"steps={redact(steps)}",
        ]
        resolved_package_id = _host_install_target_package_id(plan)
        final_exit_code = 0
        failure_reason: str | None = None
        for index, step in enumerate(steps, start=1):
            phase = await self._begin_host_install_phase(
                trace_id=trace_id,
                phase="primary",
                plan=plan,
                index=index,
                step=step,
            )
            try:
                version_before = None
                if step.get("step_type") == "windows_uninstall_registry":
                    version_before = await _detect_installed_version(
                        str(
                            step.get("target_display_name")
                            or step.get("target_package_id")
                            or ""
                        )
                    )
                result = await _execute_host_install_step(step)
                final_exit_code = int(result.get("exit_code") or 0)
                if result.get("resolved_package_id"):
                    resolved_package_id = str(result["resolved_package_id"])
                if step.get("step_type") == "windows_uninstall_registry":
                    target_display_name = str(
                        step.get("target_display_name") or step.get("target_package_id") or ""
                    )
                    version_after = await _wait_for_windows_uninstall(target_display_name)
                    result["version_before"] = version_before
                    result["version_after"] = version_after
                    result["uninstall_verified"] = version_after is None
                    if version_after is None:
                        final_exit_code = 0
                        result["exit_code"] = 0
                        result["failure_reason"] = None
                    elif final_exit_code == 0:
                        final_exit_code = 1
                        result["exit_code"] = 1
                        result["failure_reason"] = (
                            "windows_uninstall_registry_entry_still_present"
                        )
                timing = await self._end_host_install_phase(
                    phase,
                    status=(
                        TraceSpanStatus.COMPLETED
                        if final_exit_code == 0
                        else TraceSpanStatus.FAILED
                    ),
                    output_data={
                        "exit_code": final_exit_code,
                        "failure_reason": result.get("failure_reason"),
                        "resolved_package_id": resolved_package_id,
                    },
                )
                result.update(timing)
            except Exception as exc:
                await self._end_host_install_phase(
                    phase,
                    status=TraceSpanStatus.FAILED,
                    output_data={"error": str(redact(str(exc)))[:240]},
                )
                raise
            log_lines.extend(
                _host_install_step_log_lines("step", index, step, result, final_exit_code)
            )
            if final_exit_code != 0:
                failure_reason = str(result.get("failure_reason") or "host install failed")
                break
        if final_exit_code != 0:
            fallback_steps = _host_fallback_steps(plan.command_preview)
            if fallback_steps:
                log_lines.append("primary_install_failed_trying_approved_fallback=true")
                final_exit_code = 0
                failure_reason = None
                for fallback_index, step in enumerate(fallback_steps, start=1):
                    phase = await self._begin_host_install_phase(
                        trace_id=trace_id,
                        phase="fallback",
                        plan=plan,
                        index=fallback_index,
                        step=step,
                    )
                    try:
                        result = await _execute_host_install_step(step)
                        final_exit_code = int(result.get("exit_code") or 0)
                        if result.get("resolved_package_id"):
                            resolved_package_id = str(result["resolved_package_id"])
                        timing = await self._end_host_install_phase(
                            phase,
                            status=(
                                TraceSpanStatus.COMPLETED
                                if final_exit_code == 0
                                else TraceSpanStatus.FAILED
                            ),
                            output_data={
                                "exit_code": final_exit_code,
                                "failure_reason": result.get("failure_reason"),
                                "resolved_package_id": resolved_package_id,
                            },
                        )
                        result.update(timing)
                    except Exception as exc:
                        await self._end_host_install_phase(
                            phase,
                            status=TraceSpanStatus.FAILED,
                            output_data={"error": str(redact(str(exc)))[:240]},
                        )
                        raise
                    log_lines.extend(
                        _host_install_step_log_lines(
                            "fallback_step",
                            fallback_index,
                            step,
                            result,
                            final_exit_code,
                        )
                    )
                    if final_exit_code != 0:
                        failure_reason = str(result.get("failure_reason") or "host install failed")
                        break
        package_id = resolved_package_id or _host_install_target_query(plan)
        success = final_exit_code == 0
        action = _host_install_plan_action(plan)
        source = plan.install_source
        detect_terms = [
            package_id,
            _host_install_target_query(plan),
            str(source.get("name") or ""),
            str(source.get("publisher") or ""),
            str(plan.requested_software or ""),
        ]
        detect_phase = await self._begin_host_install_phase(
            trace_id=trace_id,
            phase="detect",
            plan=plan,
            target_package_id=package_id,
        )
        try:
            if action == "install":
                version = await _detect_installed_version_for_terms(
                    detect_terms,
                    package_id=package_id,
                )
            else:
                version = await _detect_installed_version(package_id)
            detect_timing = await self._end_host_install_phase(
                detect_phase,
                output_data={
                    "version_detected": version,
                    "target_package_id": package_id,
                    "term_count": len(_clean_detect_terms(detect_terms)),
                },
            )
        except Exception as exc:
            await self._end_host_install_phase(
                detect_phase,
                status=TraceSpanStatus.FAILED,
                output_data={"error": str(redact(str(exc)))[:240]},
            )
            raise
        log_lines.extend(
            [
                "detect.phase=detect",
                f"detect.started_at={detect_timing['started_at']}",
                f"detect.ended_at={detect_timing['ended_at']}",
                f"detect.duration_ms={detect_timing['duration_ms']}",
                f"detect.term_count={len(_clean_detect_terms(detect_terms))}",
            ]
        )
        if success and action == "install" and not version:
            success = False
            final_exit_code = 1
            failure_reason = (
                "official_installer_finished_but_install_not_verified"
                if _plan_uses_official_website_only(plan)
                else "package_manager_finished_but_install_not_verified"
            )
        install_path_summary = (
            "not_installed"
            if success and action == "uninstall"
            else _install_path_summary(package_id, success)
        )
        log_lines.extend(
            [
                f"exit_code={final_exit_code}",
                f"resolved_package_id={redact(package_id)}",
                f"version_detected={version}",
            ]
        )
        log = "\n".join(log_lines)
        return {
            "exit_code": final_exit_code,
            "version_detected": version,
            "install_path_summary": install_path_summary,
            "failure_reason": None if success else failure_reason or "host install failed",
            "log": log,
        }

    async def _begin_host_install_phase(
        self,
        *,
        trace_id: str | None,
        phase: str,
        plan: HostInstallPlan,
        index: int | None = None,
        step: dict[str, Any] | None = None,
        target_package_id: str | None = None,
    ) -> dict[str, Any]:
        started_at = utc_now_iso()
        step_key = str((step or {}).get("step_key") or (step or {}).get("step_type") or "")
        target = str(
            target_package_id
            or (step or {}).get("target_package_id")
            or _host_install_target_package_id(plan)
            or ""
        )
        span_id = None
        if trace_id:
            span_id = await self._trace.start_span(
                trace_id,
                span_type="host_install.phase",
                name=f"host install {phase}",
                input_data={
                    "phase": phase,
                    "step_index": index,
                    "step_key": step_key,
                    "step_type": (step or {}).get("step_type"),
                    "target_package_id": target,
                    "host_install_plan_id": plan.host_install_plan_id,
                },
                metadata={
                    "workflow": "host_install",
                    "host_action": _host_install_plan_action(plan),
                    "phase": phase,
                },
            )
        return {
            "phase": phase,
            "step_index": index,
            "step_key": step_key,
            "target_package_id": target,
            "started_at": started_at,
            "started_monotonic": time.monotonic(),
            "span_id": span_id,
        }

    async def _end_host_install_phase(
        self,
        phase_state: dict[str, Any],
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ended_at = utc_now_iso()
        duration_ms = max(
            0,
            int((time.monotonic() - float(phase_state.get("started_monotonic") or 0.0)) * 1000),
        )
        timing = {
            "phase": phase_state.get("phase"),
            "started_at": phase_state.get("started_at"),
            "ended_at": ended_at,
            "duration_ms": duration_ms,
        }
        span_id = phase_state.get("span_id")
        if span_id:
            await self._trace.end_span(
                str(span_id),
                status=status,
                output_data={**timing, **(output_data or {})},
            )
        return timing


def _candidate(backend_type: str, available: bool, reason: str) -> dict[str, Any]:
    return {"backend_type": backend_type, "available": available, "reason": reason}


def _has_executable(name: str) -> bool:
    return shutil.which(name) is not None


def _validate_source_uri(value: str | None) -> None:
    if not value:
        return
    lowered = value.lower().strip()
    if lowered.startswith(("file:", "\\\\", "c:\\", "c:/", "/", "../")):
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "拒绝本地路径或路径逃逸部署源",
            status_code=403,
        )
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http", "fixture"}:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "部署源必须是 HTTPS/GitHub 或 fixture",
            status_code=422,
        )


def _ensure_within(root: Path, path: Path) -> None:
    root = root.resolve()
    path = path.resolve()
    if root not in [path, *path.parents]:
        raise AppError(ErrorCode.TOOL_PERMISSION_DENIED, "路径不能逃逸受控目录", status_code=403)


def _safe_key(value: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    if not key:
        raise AppError(ErrorCode.VALIDATION_ERROR, "名称不合法", status_code=422)
    return key[:80]


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


async def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        await _terminate_process(process)
        stdout_bytes, stderr_bytes = await process.communicate()
        return {
            "exit_code": -1,
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": "timeout",
            "stdout_tail": _tail(stdout_bytes.decode("utf-8", errors="replace")),
            "stderr_tail": "timeout",
        }
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return {
        "exit_code": process.returncode,
        "stdout": str(redact(stdout)),
        "stderr": str(redact(stderr)),
        "stdout_tail": _tail(str(redact(stdout))),
        "stderr_tail": _tail(str(redact(stderr))),
    }


async def _wait_for_http(endpoint_url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout
    last_error = ""
    while asyncio.get_running_loop().time() < deadline:
        result = await asyncio.to_thread(_http_get_once, endpoint_url)
        if result["ok"]:
            return result
        last_error = str(result.get("error") or result)
        await asyncio.sleep(0.25)
    return {"ok": False, "error": last_error or "timeout"}


def _http_get_once(endpoint_url: str) -> dict[str, Any]:
    try:
        request = urllib.request.Request(endpoint_url, headers={"User-Agent": "phase52-health"})
        with urllib.request.urlopen(request, timeout=2) as response:
            return {"ok": 200 <= response.status < 400, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt" and process.pid:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=10)
        except (FileNotFoundError, ProcessLookupError, OSError):
            pass
        except TimeoutError:
            pass
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _tail(text: str, count: int = 12) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-count:])


def _node_static_preview_ok(root: Path) -> bool:
    package_json = _load_json_file(root / "package.json")
    deps = {
        **dict(package_json.get("dependencies") or {}),
        **dict(package_json.get("devDependencies") or {}),
    }
    return (root / "index.html").exists() and not deps


def _python_run_command(root: Path) -> str | None:
    common_files = ["app.py", "main.py", "wsgi.py"]
    for filename in common_files:
        if (root / filename).exists():
            return f"python {filename}"
    return "python -m http.server"


def _node_package_manager(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    return "npm"


def _script_command(package_manager: str, scripts: dict[str, Any], name: str) -> str | None:
    if name not in scripts:
        return None
    return f"{package_manager} run {name}"


def _risky_scripts(scripts: dict[str, Any]) -> list[dict[str, Any]]:
    risky: list[dict[str, Any]] = []
    for name, command in scripts.items():
        text = f"{name} {command}".lower()
        shell_markers = (
            "postinstall",
            "prepare",
            "install.sh",
            "curl",
            "wget",
            "powershell",
        )
        if any(marker in text for marker in shell_markers):
            risky.append(
                {
                    "script": name,
                    "risk_level": "R5",
                    "reason": "install_script_or_network_shell",
                }
            )
    return risky


def _deployment_plan(
    *,
    source_uri: str,
    workspace: ProjectWorkspace,
    backend: BackendSelection,
    constraints: dict[str, Any],
) -> dict[str, Any]:
    steps = [
        ("clone", "project.clone", "R3"),
        ("detect_stack", "project.detect_stack", "R1"),
        ("ensure_toolchain", "runtime.ensure", "R3"),
        ("install_deps", "project.install_deps", "R4"),
        ("build", "project.build", "R3"),
        ("run", "project.run", "R4"),
        ("health_check", "project.health_check", "R2"),
    ]
    return {
        "source_uri": str(redact(source_uri)),
        "workspace_id": workspace.workspace_id,
        "backend_type": backend.selected_backend,
        "backend_selection": backend.as_dict(),
        "risk_level": "R4" if backend.selected_backend != "local_workspace" else "R5",
        "preferred_port": int(constraints.get("preferred_port") or 5173),
        "steps": [
            {"step_key": key, "tool": tool, "risk_level": risk, "status": "planned"}
            for key, tool, risk in steps
        ],
        "approval_strategy": {
            "required_before_execution": True,
            "approval_binds": [
                "source_uri",
                "backend_type",
                "workspace_root",
                "network_policy",
                "install_commands",
                "port_range",
            ],
        },
        "degraded_isolation": backend.selected_backend == "local_workspace",
    }


def _deployment_binding_hash(plan: dict[str, Any]) -> str:
    approval_fields = {
        "source_uri": plan.get("source_uri"),
        "workspace_id": plan.get("workspace_id"),
        "backend_type": plan.get("backend_type"),
        "preferred_port": plan.get("preferred_port"),
        "degraded_isolation": plan.get("degraded_isolation"),
        "steps": [
            {
                "step_key": step.get("step_key"),
                "tool": step.get("tool"),
                "risk_level": step.get("risk_level"),
            }
            for step in list(plan.get("steps") or [])
            if isinstance(step, dict)
        ],
    }
    return _binding_hash(approval_fields)


def _write_fixture_project(root: Path, fixture_name: str) -> None:
    safe_name = _safe_key(fixture_name or "node-static")
    if safe_name in {"node", "node-static", "vite", "react"}:
        (root / "package.json").write_text(
            json.dumps(
                {
                    "scripts": {
                        "dev": "vite --host 127.0.0.1",
                        "build": "vite build",
                        "test": "echo ok",
                    },
                    "dependencies": {},
                    "devDependencies": {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (root / "index.html").write_text("<div id=\"app\">phase52</div>\n", encoding="utf-8")
        return
    if safe_name == "static":
        (root / "index.html").write_text("<h1>phase52 static</h1>\n", encoding="utf-8")
        return
    (root / "README.md").write_text("# unknown fixture\n", encoding="utf-8")


def _default_toolchain_source(runtime: str, version: str) -> str:
    if runtime == "node":
        return f"https://nodejs.org/dist/{version}/"
    if runtime == "python":
        return f"https://www.python.org/downloads/release/{version}/"
    if runtime == "ffmpeg":
        return "https://ffmpeg.org/download.html"
    return f"toolchain://manifest/{runtime}/{version}"


@dataclass(frozen=True)
class HostPackageCandidate:
    source_type: str
    package_id: str
    publisher: str
    confidence: float
    match_reason: str
    version: str | None = None
    name: str | None = None
    installer_url: str | None = None
    installer_sha256: str | None = None
    installer_type: str | None = None
    installer_switches: dict[str, Any] | None = None
    official_manifest: str | None = None
    official_page: str | None = None
    official_source_verification: dict[str, Any] | None = None


@dataclass(frozen=True)
class WindowsUninstallCandidate:
    display_name: str
    uninstall_string: str
    confidence: float
    match_reason: str
    version: str | None = None
    publisher: str | None = None
    quiet_uninstall_string: str | None = None
    install_location: str | None = None
    display_icon: str | None = None
    registry_key: str | None = None


@dataclass(frozen=True)
class PackageManagerBootstrapPlan:
    manager: str
    source: dict[str, Any]
    steps: list[dict[str, Any]]
    impact: dict[str, Any]


@dataclass(frozen=True)
class HostSoftwareModelCandidate:
    query: str
    package_id: str | None = None
    source_type: str | None = None
    confidence: float = 0.0
    reason: str | None = None
    queries: tuple[str, ...] = ()
    display_names: tuple[str, ...] = ()
    package_ids: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    publisher_hints: tuple[str, ...] = ()
    official_sites: tuple[str, ...] = ()
    download_pages: tuple[str, ...] = ()
    vendor_domains: tuple[str, ...] = ()


async def _host_install_plan_for(
    software: str,
    *,
    model_candidates_provider: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    uninstall = _host_software_action(software) == "uninstall"
    normalized_query = _normalize_software_query(software)
    resolution_attempts: list[dict[str, Any]] = []
    model_candidates: list[HostSoftwareModelCandidate] | None = None

    async def model_candidates_once(query: str) -> list[HostSoftwareModelCandidate]:
        nonlocal model_candidates
        if model_candidates is None:
            if model_candidates_provider is None:
                model_candidates = []
            else:
                model_candidates = await model_candidates_provider(query)
        return model_candidates

    def finish(
        result: tuple[dict[str, Any], dict[str, Any], dict[str, Any], str],
        *,
        resolved_via: str,
        final_match_confidence: float | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
        return _with_resolution_metadata(
            result,
            resolved_via=resolved_via,
            candidate_attempts=resolution_attempts,
            model_candidates=model_candidates or [],
            final_match_confidence=final_match_confidence,
        )

    lowered = _normalize_software_query(software).lower()
    if any(marker.lower() in lowered for marker in HOST_INSTALL_DENY_MARKERS):
        return (
            {"source_type": "manual_only", "reason": "blocked_high_risk_software"},
            {},
            {
                "manual_only": True,
                "host_action": "uninstall" if uninstall else "install",
                "reason_codes": ["driver_wallet_extension_or_security_bypass_blocked"],
            },
            "manual_only",
        )
    if uninstall:
        windows_candidate = await asyncio.to_thread(_resolve_windows_uninstall_candidate, software)
        resolution_attempts.append(
            {
                "source_type": "windows_uninstall_registry",
                "query": normalized_query,
                "status": "matched" if windows_candidate is not None else "no_match",
                "match_confidence": windows_candidate.confidence
                if windows_candidate is not None
                else 0.0,
            }
        )
        windows_plan = (
            _windows_uninstall_registry_plan(windows_candidate)
            if windows_candidate is not None
            else None
        )
        if windows_plan is not None:
            assert windows_candidate is not None
            return finish(
                windows_plan,
                resolved_via="windows_uninstall_registry",
                final_match_confidence=windows_candidate.confidence,
            )
        if windows_candidate is not None:
            return finish(
                (
                    {
                        "source_type": "manual_only",
                        "reason": "windows_uninstall_command_unavailable",
                        "display_name": windows_candidate.display_name,
                    },
                    {},
                    {
                        "manual_only": True,
                        "host_action": "uninstall",
                        "reason_codes": ["windows_uninstall_command_unavailable"],
                        "safe_next_step": (
                            "系统已登记该软件，但卸载入口不可安全自动执行，"
                            "请人工卸载或修复卸载入口后重试。"
                        ),
                    },
                    "manual_only",
                ),
                resolved_via="windows_uninstall_registry_manual_only",
                final_match_confidence=windows_candidate.confidence,
            )
        installed_candidate = await _resolve_installed_host_package_candidate(software)
        resolution_attempts.append(
            {
                "source_type": "package_manager_installed_list",
                "query": normalized_query,
                "status": "matched" if installed_candidate is not None else "no_match",
                "match_confidence": installed_candidate.confidence
                if installed_candidate is not None
                else 0.0,
            }
        )
        if installed_candidate is not None:
            return finish(
                _package_manager_install_plan(installed_candidate, uninstall=True),
                resolved_via="package_manager_installed_list",
                final_match_confidence=installed_candidate.confidence,
            )
    package_search_allowed = not uninstall or not _windows_uninstall_lookup_supported()
    candidate = (
        await _resolve_host_package_candidate(software)
        if package_search_allowed
        else None
    )
    resolution_attempts.append(
        {
            "source_type": "package_manager_search",
            "query": normalized_query,
            "status": "matched" if candidate is not None else "no_match",
            "match_confidence": candidate.confidence if candidate is not None else 0.0,
        }
    )
    if candidate is None:
        model_candidates = await model_candidates_once(normalized_query)
        resolution_attempts.append(
            {
                "source_type": "model_candidate_expansion",
                "query": normalized_query,
                "status": "matched" if model_candidates else "no_match",
                "candidate_count": len(model_candidates),
            }
        )
        if uninstall:
            windows_candidate = await _resolve_windows_uninstall_via_model_candidates(
                software,
                model_candidates=model_candidates,
            )
            resolution_attempts.append(
                {
                    "source_type": "windows_uninstall_registry_model_candidates",
                    "query": normalized_query,
                    "status": "matched" if windows_candidate is not None else "no_match",
                    "match_confidence": windows_candidate.confidence
                    if windows_candidate is not None
                    else 0.0,
                }
            )
            windows_plan = (
                _windows_uninstall_registry_plan(windows_candidate)
                if windows_candidate is not None
                else None
            )
            if windows_plan is not None and windows_candidate is not None:
                return finish(
                    windows_plan,
                    resolved_via="model_assisted_windows_uninstall_registry",
                    final_match_confidence=windows_candidate.confidence,
                )
            installed_candidate = await _resolve_installed_host_package_via_model_candidates(
                model_candidates
            )
            resolution_attempts.append(
                {
                    "source_type": "package_manager_installed_list_model_candidates",
                    "query": normalized_query,
                    "status": "matched" if installed_candidate is not None else "no_match",
                    "match_confidence": installed_candidate.confidence
                    if installed_candidate is not None
                    else 0.0,
                }
            )
            if installed_candidate is not None:
                return finish(
                    _package_manager_install_plan(installed_candidate, uninstall=True),
                    resolved_via="model_assisted_package_manager_installed_list",
                    final_match_confidence=installed_candidate.confidence,
                )
        candidate = (
            await _resolve_host_package_via_model_candidates(
                software,
                model_candidates_provider=model_candidates_once,
            )
            if package_search_allowed
            else None
        )
        resolution_attempts.append(
            {
                "source_type": "package_manager_search_model_candidates",
                "query": normalized_query,
                "status": "matched" if candidate is not None else "no_match",
                "match_confidence": candidate.confidence if candidate is not None else 0.0,
            }
        )
    manifest_candidate = None
    if not uninstall:
        manifest_candidate = await _resolve_winget_manifest_candidate(
            software,
            model_candidates_provider=model_candidates_once,
        )
        if manifest_candidate is None:
            manifest_candidate = await asyncio.to_thread(
                _official_source_assisted_manifest_lookup,
                software,
            )
        resolution_attempts.append(
            {
                "source_type": "official_winget_manifest",
                "query": normalized_query,
                "status": "matched" if manifest_candidate is not None else "no_match",
                "match_confidence": manifest_candidate.confidence
                if manifest_candidate is not None
                else 0.0,
            }
        )
    if (
        candidate is not None
        and manifest_candidate is not None
        and _official_manifest_has_installer(manifest_candidate)
        and manifest_candidate.confidence >= candidate.confidence
    ):
        return finish(
            _official_manifest_installer_fallback_plan(
                manifest_candidate,
                source_type="official_manifest_installer_primary",
                bootstrap_skipped_reason="official_manifest_installer_preferred",
                preferred_over_source={
                    "source_type": candidate.source_type,
                    "package_id": candidate.package_id,
                    "match_confidence": candidate.confidence,
                    "match_reason": candidate.match_reason,
                },
            ),
            resolved_via="official_winget_manifest_installer_primary",
            final_match_confidence=manifest_candidate.confidence,
        )
    if candidate is None and manifest_candidate is not None:
        return finish(
            _winget_manifest_package_manager_plan(manifest_candidate),
            resolved_via="official_winget_manifest",
            final_match_confidence=manifest_candidate.confidence,
        )
    if candidate is None:
        official_candidate = (
            await _resolve_official_website_candidate(
                software,
                model_candidates_provider=model_candidates_once,
            )
            if not uninstall
            else None
        )
        resolution_attempts.append(
            {
                "source_type": "official_website_verified_source",
                "query": normalized_query,
                "status": "matched" if official_candidate is not None else "no_match",
                "match_confidence": official_candidate.confidence
                if official_candidate is not None
                else 0.0,
                "verification": official_candidate.official_source_verification
                if official_candidate is not None
                else None,
            }
        )
        if official_candidate is not None:
            if official_candidate.source_type == "winget_manifest":
                return finish(
                    _winget_manifest_package_manager_plan(official_candidate),
                    resolved_via="official_source_assisted_winget_manifest",
                    final_match_confidence=official_candidate.confidence,
                )
            return finish(
                _official_website_installer_plan(official_candidate),
                resolved_via="official_website_verified_source",
                final_match_confidence=official_candidate.confidence,
            )
        if uninstall and _windows_uninstall_lookup_supported():
            return finish(
                _windows_uninstall_already_absent_plan(software),
                resolved_via="trusted_sources_absence_verified",
            )
        return finish(
            (
                {
                    "source_type": "manual_only",
                    "reason": "package_manager_candidate_unavailable",
                },
                {},
                {
                    "manual_only": True,
                    "host_action": "uninstall" if uninstall else "install",
                    "reason_codes": ["no_high_confidence_healthy_package_candidate"],
                    "safe_next_step": (
                        "请提供可信包管理器 ID、官网安装源，或修复当前包管理器环境后重试。"
                    ),
                },
                "manual_only",
            ),
            resolved_via="no_high_confidence_candidate",
        )
    return finish(
        _package_manager_install_plan(
            candidate,
            uninstall=uninstall,
            fallback_candidate=manifest_candidate,
        ),
        resolved_via=(
            "model_assisted_package_manager_search"
            if model_candidates
            else "package_manager_search"
        ),
        final_match_confidence=candidate.confidence,
    )


def _windows_uninstall_registry_plan(
    candidate: WindowsUninstallCandidate,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str] | None:
    step = _windows_uninstall_registry_step(candidate)
    if step is None:
        return None
    display_name = candidate.display_name
    source = {
        "source_type": "windows_uninstall_registry",
        "package_id": display_name,
        "display_name": display_name,
        "publisher": candidate.publisher or "Windows installed app registry",
        "trust": "installed_app_registry",
        "version": candidate.version,
        "match_confidence": candidate.confidence,
        "match_reason": candidate.match_reason,
        "registry_key": candidate.registry_key,
        "quiet_uninstall_available": bool(candidate.quiet_uninstall_string),
    }
    command = _command_preview_from_steps([step])
    command.update(
        {
            "executable": step["executable"],
            "args": list(step["args"]),
            "cwd": step.get("cwd", "host_default"),
            "env_policy": step.get("env_policy", "minimal"),
            "action": "uninstall",
        }
    )
    impact = {
        "host_action": "uninstall",
        "modifies_global_environment": True,
        "may_require_admin": True,
        "writes_system_locations": True,
        "modifies_path_or_registry": True,
        "rollback": f"重新从可信来源安装 {display_name}",
        "real_execution_default": False,
        "preserve_preexisting_install": False,
        "package_resolution": {
            "source_type": "windows_uninstall_registry",
            "match_confidence": candidate.confidence,
            "match_reason": candidate.match_reason,
        },
        "installed_app": {
            "display_name": display_name,
            "version": candidate.version,
            "publisher": candidate.publisher,
            "install_location": candidate.install_location,
        },
        "may_show_vendor_uninstaller": not bool(candidate.quiet_uninstall_string),
    }
    return source, command, impact, "waiting_approval"


def _with_resolution_metadata(
    result: tuple[dict[str, Any], dict[str, Any], dict[str, Any], str],
    *,
    resolved_via: str,
    candidate_attempts: list[dict[str, Any]],
    model_candidates: list[HostSoftwareModelCandidate],
    final_match_confidence: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    source, command, impact, status = result
    source = dict(source)
    command = dict(command)
    impact = dict(impact)
    source.setdefault("resolved_via", resolved_via)
    source["candidate_attempts"] = [dict(item) for item in candidate_attempts]
    source["model_assisted"] = bool(model_candidates)
    source["model_candidate_count"] = len(model_candidates)
    official_verification = source.get("official_source_verification")
    source["official_source_assisted"] = bool(
        official_verification
        or str(source.get("source_type") or "").startswith("official_manifest")
        or str(source.get("source_type") or "") == "official_winget_manifest"
        or str(source.get("source_type") or "").startswith("official_website")
        or "official_winget_manifest" in resolved_via
        or "official_source_assisted" in resolved_via
        or "official_source_assisted" in str(source.get("match_reason") or "")
    )
    if final_match_confidence is not None:
        source["final_match_confidence"] = final_match_confidence
    resolution = dict(impact.get("package_resolution") or {})
    resolution.setdefault("resolved_via", resolved_via)
    resolution["candidate_attempts"] = [dict(item) for item in candidate_attempts]
    resolution["model_assisted"] = bool(model_candidates)
    resolution["model_candidate_count"] = len(model_candidates)
    resolution["official_source_assisted"] = bool(source["official_source_assisted"])
    if isinstance(official_verification, dict) and official_verification:
        resolution["official_source_verification"] = dict(official_verification)
    if final_match_confidence is not None:
        resolution["final_match_confidence"] = final_match_confidence
    impact["package_resolution"] = resolution
    return source, command, impact, status


def _windows_uninstall_already_absent_plan(
    software: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    display_name = _host_install_visible_software_name(software)
    step = {
        "step_key": f"windows_uninstall_absent_{_safe_step_key(display_name)}",
        "step_type": "windows_uninstall_absent",
        "action": "uninstall",
        "executable": "__already_absent__",
        "args": [],
        "cwd": "host_default",
        "env_policy": "minimal",
        "target_package_id": display_name,
        "target_display_name": display_name,
        "timeout_seconds": 0,
    }
    source = {
        "source_type": "windows_uninstall_registry",
        "package_id": display_name,
        "display_name": display_name,
        "publisher": "Windows installed app registry",
        "trust": "installed_app_registry_absence_verified",
        "version": None,
        "match_confidence": 0.99,
        "match_reason": "windows_uninstall_registry_absent",
        "already_absent": True,
    }
    command = _command_preview_from_steps([step])
    command.update(
        {
            "executable": step["executable"],
            "args": [],
            "cwd": step["cwd"],
            "env_policy": step["env_policy"],
            "action": "uninstall",
        }
    )
    impact = {
        "host_action": "uninstall",
        "modifies_global_environment": False,
        "may_require_admin": False,
        "writes_system_locations": False,
        "modifies_path_or_registry": False,
        "rollback": f"{display_name} 当前未检测到，无需回滚。",
        "real_execution_default": False,
        "preserve_preexisting_install": False,
        "package_resolution": {
            "source_type": "windows_uninstall_registry",
            "match_confidence": 0.99,
            "match_reason": "windows_uninstall_registry_absent",
        },
        "already_absent": True,
    }
    return source, command, impact, "already_absent"


def _package_manager_install_plan(
    candidate: HostPackageCandidate,
    *,
    uninstall: bool,
    fallback_candidate: HostPackageCandidate | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    package_id = candidate.package_id
    action = "uninstall" if uninstall else "install"
    source: dict[str, Any]
    if candidate.source_type == "choco":
        install_step = _choco_install_step(package_id, action=action)
        source = {
            "source_type": "choco",
            "package_id": package_id,
            "publisher": candidate.publisher,
            "trust": "package_manager_search_verified",
            "version": candidate.version,
            "match_confidence": candidate.confidence,
            "match_reason": candidate.match_reason,
        }
        command = _command_preview_from_steps([install_step])
        command.update(
            {
                "executable": install_step["executable"],
                "args": list(install_step["args"]),
                "cwd": install_step.get("cwd", "host_default"),
                "env_policy": install_step.get("env_policy", "minimal"),
                "action": install_step["action"],
            }
        )
        if fallback_candidate is not None:
            command["fallback_steps"] = [_official_manifest_installer_step(fallback_candidate)]
        impact = {
            "host_action": action,
            "modifies_global_environment": True,
            "may_require_admin": True,
            "writes_system_locations": True,
            "modifies_path_or_registry": True,
            "rollback": _host_action_rollback("choco", package_id, action),
            "real_execution_default": False,
            "preserve_preexisting_install": True,
            "package_resolution": {
                "source_type": "choco",
                "match_confidence": candidate.confidence,
                "match_reason": candidate.match_reason,
            },
        }
        if fallback_candidate is not None:
            source["fallback_source"] = _official_manifest_source(fallback_candidate)
            impact["fallback_package_resolution"] = {
                "source_type": "official_winget_manifest",
                "target_package_id": fallback_candidate.package_id,
                "match_confidence": fallback_candidate.confidence,
                "match_reason": fallback_candidate.match_reason,
            }
            impact["checksum_verification"] = "sha256"
            impact["risk_level"] = "R6"
        return source, command, impact, "waiting_approval"
    source = {
        "source_type": "winget",
        "package_id": package_id,
        "publisher": candidate.publisher,
        "trust": "package_manager_search_verified",
        "version": candidate.version,
        "match_confidence": candidate.confidence,
        "match_reason": candidate.match_reason,
    }
    winget_action = "uninstall" if uninstall else "install"
    install_step = _winget_install_step(package_id, action=winget_action)
    command = _command_preview_from_steps([install_step])
    command.update(
        {
            "executable": install_step["executable"],
            "args": list(install_step["args"]),
            "cwd": install_step.get("cwd", "host_default"),
            "env_policy": install_step.get("env_policy", "minimal"),
            "action": install_step["action"],
        }
    )
    if fallback_candidate is not None:
        command["fallback_steps"] = [_official_manifest_installer_step(fallback_candidate)]
    impact = {
        "host_action": action,
        "modifies_global_environment": True,
        "may_require_admin": True,
        "writes_system_locations": True,
        "modifies_path_or_registry": True,
        "rollback": _host_action_rollback("winget", package_id, action),
        "real_execution_default": False,
        "package_resolution": {
            "source_type": "winget",
            "match_confidence": candidate.confidence,
            "match_reason": candidate.match_reason,
        },
    }
    if fallback_candidate is not None:
        source["fallback_source"] = _official_manifest_source(fallback_candidate)
        impact["fallback_package_resolution"] = {
            "source_type": "official_winget_manifest",
            "target_package_id": fallback_candidate.package_id,
            "match_confidence": fallback_candidate.confidence,
            "match_reason": fallback_candidate.match_reason,
        }
        impact["checksum_verification"] = "sha256"
        impact["risk_level"] = "R6"
    return source, command, impact, "waiting_approval"


def _host_software_action(software: str) -> str:
    lowered = software.lower()
    if any(marker in lowered for marker in ("uninstall", "卸载", "移除")):
        return "uninstall"
    return "install"


def _host_install_visible_software_name(software: str) -> str:
    clean = software.strip()
    for marker in (
        "卸载",
        "移除",
        "安装",
        "装一下",
        "到这台电脑",
        "到我的电脑",
        "这台电脑",
        "我的电脑",
        "到电脑",
        "全局",
        "本机",
    ):
        clean = clean.replace(marker, " ")
    clean = re.sub(r"(?i)\b(?:uninstall|install)\b", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" ，。,.")
    return clean or software.strip()


def _host_action_rollback(package_manager: str, package_id: str, action: str) -> str:
    if package_manager == "choco":
        if action == "uninstall":
            return f"choco install {package_id} -y --no-progress"
        return f"choco uninstall {package_id} -y --no-progress"
    if action == "uninstall":
        return f"winget install --id {package_id} --source winget"
    return f"winget uninstall --id {package_id}"


def _winget_manifest_package_manager_plan(
    package: HostPackageCandidate,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    winget_available = shutil.which("winget") is not None
    if not winget_available and _official_manifest_has_installer(package):
        return _official_manifest_installer_fallback_plan(
            package,
            bootstrap_skipped_reason="official_manifest_installer_available",
        )
    bootstrap = _winget_bootstrap_plan() if not winget_available else None
    if bootstrap is None and not winget_available:
        return _official_manifest_installer_fallback_plan(package)
    install_step = _winget_install_step(package.package_id, action="install")
    steps = [*bootstrap.steps, install_step] if bootstrap is not None else [install_step]
    command = _command_preview_from_steps(steps)
    command.update(
        {
            "executable": install_step["executable"],
            "args": list(install_step["args"]),
            "cwd": install_step.get("cwd", "host_default"),
            "env_policy": install_step.get("env_policy", "minimal"),
            "action": install_step["action"],
            "fallback_steps": [_official_manifest_installer_step(package)],
        }
    )
    source = {
        "source_type": "winget",
        "package_id": package.package_id,
        "publisher": package.publisher,
        "trust": "official_package_manager_dynamic",
        "version": package.version,
        "match_confidence": package.confidence,
        "match_reason": package.match_reason,
        "resolved_via": "official_winget_manifest",
        "official_manifest": package.official_manifest,
        "fallback_source": _official_manifest_source(package),
    }
    if bootstrap is not None:
        source["package_manager_bootstrap"] = bootstrap.source
    impact = {
        "modifies_global_environment": True,
        "may_require_admin": True,
        "writes_system_locations": True,
        "modifies_path_or_registry": True,
        "rollback": f"winget uninstall --id {package.package_id}",
        "real_execution_default": False,
        "preserve_preexisting_install": True,
        "risk_level": "R6" if bootstrap is not None else "R5",
        "package_resolution": {
            "source_type": "official_winget_manifest",
            "target_package_id": package.package_id,
            "match_confidence": package.confidence,
            "match_reason": package.match_reason,
        },
        "fallback_package_resolution": {
            "source_type": "official_winget_manifest",
            "target_package_id": package.package_id,
            "match_confidence": package.confidence,
            "match_reason": package.match_reason,
        },
        "checksum_verification": "sha256",
    }
    if bootstrap is not None:
        impact.update(bootstrap.impact)
        impact["bootstrap_required"] = True
    return source, command, impact, "waiting_approval"


def _official_manifest_has_installer(package: HostPackageCandidate) -> bool:
    return bool(package.installer_url and package.installer_sha256)


def _command_preview_from_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    first = steps[0] if steps else {}
    return {
        "executable": first.get("executable"),
        "args": list(first.get("args") or []),
        "cwd": first.get("cwd", "host_default"),
        "env_policy": first.get("env_policy", "minimal"),
        "action": first.get("action") or "install",
        "steps": steps,
    }


def _official_manifest_installer_fallback_plan(
    package: HostPackageCandidate,
    *,
    source_type: str = "official_manifest_installer_fallback",
    bootstrap_skipped_reason: str = "no_supported_package_manager_bootstrap",
    preferred_over_source: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    step = _official_manifest_installer_step(package)
    source = {
        **_official_manifest_source(package),
        "source_type": source_type,
    }
    if preferred_over_source is not None:
        source["preferred_over_source"] = dict(preferred_over_source)
    command = _command_preview_from_steps([step])
    impact = {
        "host_action": "install",
        "modifies_global_environment": True,
        "may_require_admin": True,
        "writes_system_locations": True,
        "modifies_path_or_registry": True,
        "rollback": (
            f"winget uninstall --id {package.package_id} 或通过系统应用卸载"
            f" {package.name or package.package_id}"
        ),
        "real_execution_default": False,
        "preserve_preexisting_install": True,
        "risk_level": "R6",
        "package_resolution": {
            "source_type": "official_winget_manifest",
            "target_package_id": package.package_id,
            "match_confidence": package.confidence,
            "match_reason": package.match_reason,
        },
        "bootstrap_required": False,
        "bootstrap_skipped_reason": bootstrap_skipped_reason,
        "checksum_verification": "sha256",
    }
    if preferred_over_source is not None:
        impact["preferred_over_package_resolution"] = dict(preferred_over_source)
    return source, command, impact, "waiting_approval"


def _official_website_installer_plan(
    package: HostPackageCandidate,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    step = _official_website_installer_step(package)
    source = _official_website_source(package)
    command = _command_preview_from_steps([step])
    command.update(
        {
            "executable": step["executable"],
            "args": list(step["args"]),
            "cwd": step.get("cwd", "host_default"),
            "env_policy": step.get("env_policy", "minimal"),
            "action": "install",
        }
    )
    verification = dict(package.official_source_verification or {})
    checksum_status = str(verification.get("checksum_status") or "unavailable")
    impact = {
        "host_action": "install",
        "modifies_global_environment": True,
        "may_require_admin": True,
        "writes_system_locations": True,
        "modifies_path_or_registry": True,
        "rollback": _official_website_rollback(package),
        "real_execution_default": False,
        "preserve_preexisting_install": True,
        "risk_level": "R6",
        "package_resolution": {
            "source_type": "official_website_verified_source",
            "target_package_id": package.package_id,
            "match_confidence": package.confidence,
            "match_reason": package.match_reason,
            "official_source_verification": verification,
            "official_source_assisted": True,
        },
        "official_source_verification": verification,
        "checksum_verification": checksum_status,
    }
    return source, command, impact, "waiting_approval"


def _official_manifest_source(package: HostPackageCandidate) -> dict[str, Any]:
    return {
        "source_type": "official_winget_manifest",
        "package_manager": "winget_manifest",
        "package_id": package.package_id,
        "publisher": package.publisher,
        "target_source_type": "official_installer_from_winget_manifest",
        "trust": "official_manifest_with_sha256",
        "version": package.version,
        "match_confidence": package.confidence,
        "match_reason": package.match_reason,
        "official_manifest": package.official_manifest,
        "official_docs": _WINGET_OFFICIAL_DOC_URL,
        "installer_url": package.installer_url,
        "installer_sha256": package.installer_sha256,
    }


def _official_website_source(package: HostPackageCandidate) -> dict[str, Any]:
    return {
        "source_type": "official_website_installer",
        "package_id": package.package_id,
        "publisher": package.publisher,
        "target_source_type": "official_installer_from_vendor_site",
        "trust": "official_website_verified",
        "version": package.version,
        "name": package.name,
        "match_confidence": package.confidence,
        "match_reason": package.match_reason,
        "official_page": package.official_page,
        "installer_url": package.installer_url,
        "installer_sha256": package.installer_sha256,
        "official_source_verification": dict(package.official_source_verification or {}),
    }


def _official_website_rollback(package: HostPackageCandidate) -> str:
    display_name = package.name or package.package_id
    if package.package_id.startswith("Tencent."):
        return f"winget uninstall --id {package.package_id} 或通过系统应用卸载 {display_name}"
    return f"通过系统应用卸载 {display_name}"


def _official_manifest_installer_step(package: HostPackageCandidate) -> dict[str, Any]:
    powershell = _powershell_executable() or "powershell"
    installer_url = package.installer_url
    installer_sha256 = package.installer_sha256
    if not installer_url or not installer_sha256:
        raise AppError(
            ErrorCode.TASK_PLAN_FAILED,
            "缺少官方 manifest 安装器元数据",
            status_code=422,
        )
    installer_filename = _installer_filename(package.package_id, installer_url)
    silent_args = _silent_args_for_manifest_candidate(package)
    script = (
        "$ErrorActionPreference='Stop'; "
        "$progressPreference='silentlyContinue'; "
        "$work=Join-Path $env:TEMP 'cycber-host-install'; "
        "New-Item -ItemType Directory -Force -Path $work | Out-Null; "
        f"$installer=Join-Path $work '{installer_filename}'; "
        "try { "
        f"Invoke-WebRequest -Uri '{installer_url}' -OutFile $installer -UseBasicParsing; "
        "$hash=(Get-FileHash -Algorithm SHA256 -Path $installer).Hash.ToUpperInvariant(); "
        f"if ($hash -ne '{installer_sha256}') "
        "{ throw ('installer sha256 mismatch: ' + $hash) }; "
        f"$process = Start-Process -FilePath $installer -ArgumentList '{silent_args}' "
        "-Wait -PassThru; "
        "exit $process.ExitCode "
        "} finally { "
        "if (Test-Path $installer) { "
        "Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue "
        "} "
        "}"
    )
    return {
        "step_key": f"official_manifest_install_{_safe_step_key(package.package_id)}",
        "step_type": "official_manifest_installer",
        "package_manager": "winget_manifest",
        "action": "install",
        "executable": powershell,
        "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        "cwd": "host_default",
        "env_policy": "minimal",
        "target_package_id": package.package_id,
        "official_manifest": package.official_manifest,
        "official_source": installer_url,
        "sha256": installer_sha256,
        "timeout_seconds": 1800,
        "fallback_for": "package_manager_install_failed",
    }


def _official_website_installer_step(package: HostPackageCandidate) -> dict[str, Any]:
    powershell = _powershell_executable() or "powershell"
    installer_url = package.installer_url
    if not installer_url:
        raise AppError(
            ErrorCode.TASK_PLAN_FAILED,
            "缺少官网安装器下载地址",
            status_code=422,
        )
    installer_filename = _installer_filename(package.package_id, installer_url)
    expected_hash = str(package.installer_sha256 or "").upper()
    silent_args = _silent_args_for_manifest_candidate(package)
    hash_check = ""
    if expected_hash:
        hash_check = (
            "$hash=(Get-FileHash -Algorithm SHA256 -Path $installer).Hash.ToUpperInvariant(); "
            f"if ($hash -ne '{expected_hash}') "
            "{ throw ('installer sha256 mismatch: ' + $hash) }; "
        )
    signature_check = (
        "$signature = Get-AuthenticodeSignature -FilePath $installer; "
        "$sigStatus = [string]$signature.Status; "
        "if ($sigStatus -notin @('Valid','NotSigned','UnknownError')) { "
        "throw ('installer signature rejected: ' + $sigStatus) "
        "}; "
    )
    script = (
        "$ErrorActionPreference='Stop'; "
        "$progressPreference='silentlyContinue'; "
        "$work=Join-Path $env:TEMP 'cycber-host-install'; "
        "New-Item -ItemType Directory -Force -Path $work | Out-Null; "
        f"$installer=Join-Path $work '{installer_filename}'; "
        "try { "
        f"Invoke-WebRequest -Uri '{installer_url}' -OutFile $installer -UseBasicParsing; "
        f"{hash_check}"
        f"{signature_check}"
        f"$process = Start-Process -FilePath $installer -ArgumentList '{silent_args}' "
        "-Wait -PassThru; "
        "exit $process.ExitCode "
        "} finally { "
        "if (Test-Path $installer) { "
        "Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue "
        "} "
        "}"
    )
    return {
        "step_key": f"official_website_install_{_safe_step_key(package.package_id)}",
        "step_type": "official_website_installer",
        "action": "install",
        "executable": powershell,
        "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        "cwd": "host_default",
        "env_policy": "minimal",
        "target_package_id": package.package_id,
        "official_page": package.official_page,
        "official_source": installer_url,
        "sha256": package.installer_sha256,
        "signature_policy": "verify_after_download",
        "timeout_seconds": 1800,
    }


def _winget_install_step(package_id: str, *, action: str = "install") -> dict[str, Any]:
    executable = "winget"
    if shutil.which("winget") is None:
        executable = _powershell_executable() or "winget"
        script_command = (
            "$ErrorActionPreference='Stop'; "
            "$winget = Get-Command winget -ErrorAction SilentlyContinue; "
            "if (-not $winget) { "
            "  $candidate = Get-ChildItem -Path 'C:\\Program Files\\WindowsApps' "
            "-Recurse -Filter winget.exe -ErrorAction SilentlyContinue | "
            "    Sort-Object LastWriteTime -Descending | Select-Object -First 1; "
            "  if ($candidate) { $winget = $candidate.FullName } "
            "}; "
            "if (-not $winget) { throw 'winget not found after bootstrap' }; "
            "$wingetPath = if ($winget -is [string]) { $winget } else { $winget.Source }; "
            "& $wingetPath "
            f"{action} --id {package_id} --source winget "
            "--accept-package-agreements --accept-source-agreements "
            f"{'--silent --disable-interactivity' if action == 'install' else ''}"
        )
        return {
            "step_key": f"winget_{action}_{_safe_step_key(package_id)}",
            "step_type": "package_manager_install",
            "package_manager": "winget",
            "action": action,
            "executable": executable,
            "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script_command],
            "cwd": "host_default",
            "env_policy": "minimal",
            "target_package_id": package_id,
            "timeout_seconds": 1800,
        }
    args = [
        action,
        "--id",
        package_id,
        "--source",
        "winget",
        "--accept-package-agreements",
        "--accept-source-agreements",
    ]
    if action == "install":
        args.extend(["--silent", "--disable-interactivity"])
    return {
        "step_key": f"winget_{action}_{_safe_step_key(package_id)}",
        "step_type": "package_manager_install",
        "package_manager": "winget",
        "action": action,
        "executable": executable,
        "args": args,
        "cwd": "host_default",
        "env_policy": "minimal",
        "target_package_id": package_id,
        "timeout_seconds": 1800,
    }


def _winget_verify_step() -> dict[str, Any]:
    if shutil.which("winget"):
        return {
            "step_key": "verify_winget_available",
            "step_type": "verify_package_manager",
            "package_manager": "winget",
            "action": "verify",
            "executable": "winget",
            "args": ["--version"],
            "cwd": "host_default",
            "env_policy": "minimal",
            "timeout_seconds": 60,
        }
    powershell = _powershell_executable() or "powershell"
    script_command = (
        "$ErrorActionPreference='Stop'; "
        "$winget = Get-Command winget -ErrorAction SilentlyContinue; "
        "if (-not $winget) { "
        "  $candidate = Get-ChildItem -Path 'C:\\Program Files\\WindowsApps' "
        "-Recurse -Filter winget.exe -ErrorAction SilentlyContinue | "
        "    Sort-Object LastWriteTime -Descending | Select-Object -First 1; "
        "  if ($candidate) { $winget = $candidate.FullName } "
        "}; "
        "if (-not $winget) { throw 'winget not found after bootstrap' }; "
        "$wingetPath = if ($winget -is [string]) { $winget } else { $winget.Source }; "
        "& $wingetPath --version"
    )
    return {
        "step_key": "verify_winget_available",
        "step_type": "verify_package_manager",
        "package_manager": "winget",
        "action": "verify",
        "executable": powershell,
        "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script_command],
        "cwd": "host_default",
        "env_policy": "minimal",
        "timeout_seconds": 60,
    }


def _winget_bootstrap_plan() -> PackageManagerBootstrapPlan | None:
    if os.name != "nt":
        return None
    powershell = _powershell_executable()
    if powershell is None:
        return None
    install_script = _winget_bootstrap_script()
    return PackageManagerBootstrapPlan(
        manager="winget",
        source={
            "source_type": "official_package_manager_bootstrap",
            "package_manager": "winget",
            "publisher": "Microsoft",
            "download_url": _WINGET_BOOTSTRAP_URL,
            "fallback_download_url": _WINGET_GITHUB_RELEASE_URL,
            "fallback_dependencies_url": _WINGET_GITHUB_DEPENDENCIES_URL,
            "fallback_license_url": _WINGET_GITHUB_LICENSE_URL,
            "official_docs": _WINGET_OFFICIAL_DOC_URL,
            "installer_type": "msixbundle",
            "trust": "official_vendor_url",
        },
        steps=[
            {
                "step_key": "bootstrap_winget_official_app_installer",
                "step_type": "package_manager_bootstrap",
                "package_manager": "winget",
                "action": "bootstrap_package_manager",
                "executable": powershell,
                "args": [
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    install_script,
                ],
                "cwd": "host_default",
                "env_policy": "minimal",
                "official_source": _WINGET_BOOTSTRAP_URL,
                "official_fallback_sources": [
                    _WINGET_GITHUB_RELEASE_URL,
                    _WINGET_GITHUB_DEPENDENCIES_URL,
                    _WINGET_GITHUB_LICENSE_URL,
                ],
                "official_docs": _WINGET_OFFICIAL_DOC_URL,
                "timeout_seconds": 600,
            },
            _winget_verify_step(),
        ],
        impact={
            "bootstrap_package_manager": "winget",
            "bootstrap_source": _WINGET_BOOTSTRAP_URL,
            "bootstrap_fallback_sources": [
                _WINGET_GITHUB_RELEASE_URL,
                _WINGET_GITHUB_DEPENDENCIES_URL,
                _WINGET_GITHUB_LICENSE_URL,
            ],
            "bootstrap_official_docs": _WINGET_OFFICIAL_DOC_URL,
            "installs_package_manager": True,
        },
    )


def _choco_install_step(package_id: str, *, action: str = "install") -> dict[str, Any]:
    return {
        "step_key": f"choco_{action}_{_safe_step_key(package_id)}",
        "step_type": "package_manager_install",
        "package_manager": "choco",
        "action": action,
        "executable": "choco",
        "args": [action, package_id, "-y", "--no-progress", "--limit-output"],
        "cwd": "host_default",
        "env_policy": "minimal",
        "target_package_id": package_id,
        "timeout_seconds": 1800,
    }


def _windows_uninstall_registry_step(
    candidate: WindowsUninstallCandidate,
) -> dict[str, Any] | None:
    command_text = candidate.quiet_uninstall_string or candidate.uninstall_string
    command = _parse_windows_uninstall_command(command_text)
    if command is None:
        return None
    executable, args = command
    silent_args_added = False
    if not candidate.quiet_uninstall_string:
        args, silent_args_added = _add_best_effort_silent_uninstall_args(executable, args)
    command_redacted = [executable, *args]
    return {
        "step_key": f"windows_uninstall_{_safe_step_key(candidate.display_name)}",
        "step_type": "windows_uninstall_registry",
        "action": "uninstall",
        "executable": executable,
        "args": args,
        "cwd": "host_default",
        "env_policy": "minimal",
        "target_package_id": candidate.display_name,
        "target_display_name": candidate.display_name,
        "registry_key": candidate.registry_key,
        "quiet_uninstall_available": bool(candidate.quiet_uninstall_string),
        "silent_args_added": silent_args_added,
        "command_redacted": command_redacted,
        "timeout_seconds": 1800,
    }


def _winget_bootstrap_script() -> str:
    return (
        "$ErrorActionPreference='Stop'; "
        "$progressPreference='silentlyContinue'; "
        "$work=Join-Path $env:TEMP 'cycber-winget-bootstrap'; "
        "New-Item -ItemType Directory -Force -Path $work | Out-Null; "
        "$bundle=Join-Path $work 'Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle'; "
        "$deps=Join-Path $work 'DesktopAppInstaller_Dependencies.zip'; "
        "$license=Join-Path $work 'License.xml'; "
        "$depDir=Join-Path $work 'dependencies'; "
        "$installed=$false; "
        "try { "
        "  Install-PackageProvider -Name NuGet -Force -Scope CurrentUser | Out-Null; "
        "  Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery "
        "-Scope CurrentUser; "
        "  Repair-WinGetPackageManager -Latest -AllUsers; "
        "  $installed=$true; "
        "} catch { Write-Host ('Microsoft.WinGet.Client repair failed: ' "
        "+ $_.Exception.Message) }; "
        "if (-not $installed) { "
        f"  try {{ Invoke-WebRequest -Uri '{_WINGET_BOOTSTRAP_URL}' "
        "-OutFile $bundle -UseBasicParsing } "
        "catch { "
        "    Write-Host ('aka.ms bootstrap download failed: ' + $_.Exception.Message); "
        f"    Invoke-WebRequest -Uri '{_WINGET_GITHUB_RELEASE_URL}' "
        "-OutFile $bundle -UseBasicParsing; "
        "  }; "
        f"  Invoke-WebRequest -Uri '{_WINGET_GITHUB_DEPENDENCIES_URL}' "
        "-OutFile $deps -UseBasicParsing; "
        f"  Invoke-WebRequest -Uri '{_WINGET_GITHUB_LICENSE_URL}' "
        "-OutFile $license -UseBasicParsing; "
        "  if (Test-Path $depDir) { Remove-Item -Recurse -Force $depDir }; "
        "  Expand-Archive -Path $deps -DestinationPath $depDir -Force; "
        "  $dependencyPackages = Get-ChildItem -Path $depDir -Recurse "
        "-Include '*.appx','*.msix' | "
        "    Where-Object { $_.Name -match 'x64|neutral' } | "
        "Select-Object -ExpandProperty FullName; "
        "  foreach ($dependency in $dependencyPackages) { "
        "    try { Add-AppxPackage -Path $dependency -ErrorAction Stop } "
        "    catch { Write-Host ('dependency already installed or unsupported: ' "
        "+ $_.Exception.Message) } "
        "  }; "
        "  try { Add-AppxProvisionedPackage -Online -PackagePath $bundle "
        "-LicensePath $license -ErrorAction Stop | Out-Null } "
        "  catch { Write-Host ('provisioned package install failed: ' + $_.Exception.Message) }; "
        "  Add-AppxPackage -Path $bundle; "
        "}; "
        "$wingetCommand = Get-Command winget -ErrorAction SilentlyContinue; "
        "if (-not $wingetCommand) { "
        "  $candidate = Get-ChildItem -Path 'C:\\Program Files\\WindowsApps' "
        "-Recurse -Filter winget.exe -ErrorAction SilentlyContinue | "
        "    Sort-Object LastWriteTime -Descending | Select-Object -First 1; "
        "  if ($candidate) { $env:PATH = $candidate.Directory.FullName + ';' + $env:PATH } "
        "}; "
        "winget --version"
    )


def _powershell_executable() -> str | None:
    for name in ("pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            return name
    return None


async def _resolve_host_package_candidate(software: str) -> HostPackageCandidate | None:
    normalized = _normalize_software_query(software)
    if not normalized:
        return None
    cached = _HOST_PACKAGE_CACHE.get(normalized)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _HOST_PACKAGE_CACHE_TTL_SECONDS:
        return cached[1]
    candidates: list[HostPackageCandidate] = []
    if shutil.which("winget"):
        candidates.extend(await _winget_candidates(normalized))
    if shutil.which("choco"):
        candidates.extend(await _choco_candidates(normalized))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item.confidence,
            1 if item.source_type == "winget" else 0,
            -len(item.package_id),
        ),
        reverse=True,
    )
    best = candidates[0]
    if best.confidence < 0.9:
        _HOST_PACKAGE_CACHE[normalized] = (now, None)
        return None
    _HOST_PACKAGE_CACHE[normalized] = (now, best)
    return best


async def _resolve_host_package_via_model_candidates(
    software: str,
    *,
    model_candidates_provider: Any | None,
) -> HostPackageCandidate | None:
    if model_candidates_provider is None:
        return None
    model_candidates = await model_candidates_provider(_normalize_software_query(software))
    for model_candidate in model_candidates[:5]:
        if model_candidate.confidence < 0.65:
            continue
        for query in _model_candidate_lookup_terms(model_candidate):
            candidate = await _resolve_host_package_candidate(query)
            if candidate is None:
                continue
            if model_candidate.source_type in {None, "", candidate.source_type}:
                return _model_adjusted_package_candidate(candidate, model_candidate)
    return None


async def _resolve_windows_uninstall_via_model_candidates(
    software: str,
    *,
    model_candidates: list[HostSoftwareModelCandidate],
) -> WindowsUninstallCandidate | None:
    candidates: list[WindowsUninstallCandidate] = []
    for model_candidate in model_candidates[:8]:
        if model_candidate.confidence < 0.65:
            continue
        for query in _model_candidate_lookup_terms(model_candidate):
            candidate = await asyncio.to_thread(_resolve_windows_uninstall_candidate, query)
            if candidate is None:
                continue
            candidates.append(_model_adjusted_windows_candidate(candidate, model_candidate))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item.confidence,
            bool(item.quiet_uninstall_string),
            -len(item.display_name),
        ),
        reverse=True,
    )
    return candidates[0] if candidates[0].confidence >= 0.9 else None


async def _resolve_installed_host_package_via_model_candidates(
    model_candidates: list[HostSoftwareModelCandidate],
) -> HostPackageCandidate | None:
    candidates: list[HostPackageCandidate] = []
    for model_candidate in model_candidates[:8]:
        if model_candidate.confidence < 0.65:
            continue
        for query in _model_candidate_lookup_terms(model_candidate):
            candidate = await _resolve_installed_host_package_candidate(query)
            if candidate is None:
                continue
            if model_candidate.source_type in {None, "", candidate.source_type}:
                candidates.append(_model_adjusted_package_candidate(candidate, model_candidate))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item.confidence,
            1 if item.source_type == "winget" else 0,
            -len(item.package_id),
        ),
        reverse=True,
    )
    return candidates[0] if candidates[0].confidence >= 0.9 else None


async def _resolve_installed_host_package_candidate(software: str) -> HostPackageCandidate | None:
    query = _normalize_software_query(software)
    if not query:
        return None
    candidates: list[HostPackageCandidate] = []
    if shutil.which("winget"):
        candidates.extend(await _winget_installed_candidates(query))
    if shutil.which("choco"):
        candidates.extend(await _choco_installed_candidates(query))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item.confidence,
            1 if item.source_type == "winget" else 0,
            -len(item.package_id),
        ),
        reverse=True,
    )
    best = candidates[0]
    return best if best.confidence >= 0.9 else None


async def _resolve_winget_manifest_candidate(
    software: str,
    *,
    model_candidates_provider: Any | None,
) -> HostPackageCandidate | None:
    queries = _candidate_queries_for_manifest_lookup(software)
    if model_candidates_provider is not None:
        for item in await model_candidates_provider(_normalize_software_query(software)):
            queries.extend(_model_candidate_lookup_terms(item))
    seen: set[str] = set()
    for query in queries:
        key = _manifest_lookup_key(query)
        if not key or key in seen:
            continue
        seen.add(key)
        cached = _WINGET_MANIFEST_CACHE.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _HOST_PACKAGE_CACHE_TTL_SECONDS:
            if cached[1] is not None:
                return cached[1]
            continue
        candidate = await asyncio.to_thread(_winget_manifest_candidate_for_query, query)
        _WINGET_MANIFEST_CACHE[key] = (now, candidate)
        if candidate is not None:
            return candidate
    return None


async def _winget_candidates(query: str) -> list[HostPackageCandidate]:
    exact = await _run_command(
        ["winget", "search", "--query", query, "--exact", "--source", "winget"],
        cwd=Path.cwd(),
        timeout=60,
    )
    rows = _parse_winget_search_rows(str(exact.get("stdout") or ""))
    candidates = [
        HostPackageCandidate(
            source_type="winget",
            package_id=row["id"],
            publisher=_publisher_from_winget_id(row["id"]),
            confidence=_host_package_confidence(query, row["id"], row["name"], exact=True),
            match_reason="winget_exact_search",
            version=row.get("version"),
            name=row.get("name"),
        )
        for row in rows
        if row.get("id")
    ]
    if candidates:
        return candidates
    broad = await _run_command(
        ["winget", "search", "--query", query, "--source", "winget"],
        cwd=Path.cwd(),
        timeout=60,
    )
    rows = _parse_winget_search_rows(str(broad.get("stdout") or ""))
    return [
        HostPackageCandidate(
            source_type="winget",
            package_id=row["id"],
            publisher=_publisher_from_winget_id(row["id"]),
            confidence=_host_package_confidence(query, row["id"], row["name"], exact=False),
            match_reason="winget_search",
            version=row.get("version"),
            name=row.get("name"),
        )
        for row in rows[:8]
        if row.get("id")
    ]


async def _choco_candidates(query: str) -> list[HostPackageCandidate]:
    exact = await _run_command(
        ["choco", "search", query, "--exact", "--limit-output"],
        cwd=Path.cwd(),
        timeout=60,
    )
    rows = _parse_choco_search_rows(str(exact.get("stdout") or ""))
    candidates = [
        HostPackageCandidate(
            source_type="choco",
            package_id=row["id"],
            publisher="Chocolatey community package",
            confidence=_host_package_confidence(query, row["id"], row["id"], exact=True),
            match_reason="choco_exact_search",
            version=row.get("version"),
            name=row.get("name"),
        )
        for row in rows
        if row.get("id")
    ]
    if candidates:
        return candidates
    broad = await _run_command(
        ["choco", "search", query, "--limit-output"],
        cwd=Path.cwd(),
        timeout=60,
    )
    rows = _parse_choco_search_rows(str(broad.get("stdout") or ""))
    candidates = [
        HostPackageCandidate(
            source_type="choco",
            package_id=row["id"],
            publisher="Chocolatey community package",
            confidence=_host_package_confidence(query, row["id"], row["id"], exact=False),
            match_reason="choco_search",
            version=row.get("version"),
            name=row.get("name"),
        )
        for row in rows[:8]
        if row.get("id")
    ]
    return [
        candidate
        for candidate in candidates
        if candidate.confidence >= 0.9
        and await _choco_package_appears_healthy(candidate.package_id)
    ]


async def _winget_installed_candidates(query: str) -> list[HostPackageCandidate]:
    exact = await _run_command(
        ["winget", "list", "--query", query, "--exact"],
        cwd=Path.cwd(),
        timeout=60,
    )
    rows = _parse_winget_search_rows(str(exact.get("stdout") or ""))
    candidates = [
        HostPackageCandidate(
            source_type="winget",
            package_id=row["id"],
            publisher=_publisher_from_winget_id(row["id"]),
            confidence=_host_package_confidence(query, row["id"], row["name"], exact=True),
            match_reason="winget_installed_exact_list",
            version=row.get("version"),
            name=row.get("name"),
        )
        for row in rows
        if row.get("id")
    ]
    if candidates:
        return candidates
    broad = await _run_command(
        ["winget", "list", "--query", query],
        cwd=Path.cwd(),
        timeout=60,
    )
    rows = _parse_winget_search_rows(str(broad.get("stdout") or ""))
    return [
        HostPackageCandidate(
            source_type="winget",
            package_id=row["id"],
            publisher=_publisher_from_winget_id(row["id"]),
            confidence=_host_package_confidence(query, row["id"], row["name"], exact=False),
            match_reason="winget_installed_list",
            version=row.get("version"),
            name=row.get("name"),
        )
        for row in rows[:8]
        if row.get("id")
    ]


async def _choco_installed_candidates(query: str) -> list[HostPackageCandidate]:
    exact = await _run_command(
        ["choco", "list", query, "--exact", "--limit-output"],
        cwd=Path.cwd(),
        timeout=60,
    )
    rows = _parse_choco_search_rows(str(exact.get("stdout") or ""))
    candidates = [
        HostPackageCandidate(
            source_type="choco",
            package_id=row["id"],
            publisher="Chocolatey community package",
            confidence=_host_package_confidence(query, row["id"], row["id"], exact=True),
            match_reason="choco_installed_exact_list",
            version=row.get("version"),
            name=row.get("name"),
        )
        for row in rows
        if row.get("id")
    ]
    if candidates:
        return candidates
    broad = await _run_command(
        ["choco", "list", query, "--limit-output"],
        cwd=Path.cwd(),
        timeout=60,
    )
    rows = _parse_choco_search_rows(str(broad.get("stdout") or ""))
    return [
        HostPackageCandidate(
            source_type="choco",
            package_id=row["id"],
            publisher="Chocolatey community package",
            confidence=_host_package_confidence(query, row["id"], row["id"], exact=False),
            match_reason="choco_installed_list",
            version=row.get("version"),
            name=row.get("name"),
        )
        for row in rows[:8]
        if row.get("id")
    ]


def _normalize_software_query(software: str) -> str:
    lowered = software.lower()
    for marker in (
        "uninstall",
        "install",
        "卸载",
        "移除",
        "安装",
        "装一下",
        "到这台电脑",
        "到我的电脑",
        "这台电脑",
        "我的电脑",
        "到电脑",
        "全局",
        "本机",
    ):
        lowered = lowered.replace(marker, " ")
    query = re.sub(r"\s+", " ", lowered).strip(" ，。,.")
    return query[:80]


def _candidate_queries_for_manifest_lookup(software: str) -> list[str]:
    normalized = _normalize_software_query(software)
    visible = _host_install_visible_software_name(software)
    queries = [visible, normalized]
    if "." in visible and re.search(r"[a-zA-Z]", visible):
        queries.insert(0, visible)
    if "." in normalized and re.search(r"[a-zA-Z]", normalized):
        queries.insert(0, normalized)
    visible_ascii_tokens = [
        token
        for token in re.split(r"[^a-zA-Z0-9.]+", visible)
        if token and len(token) >= 2
    ]
    ascii_tokens = [
        token
        for token in re.split(r"[^a-zA-Z0-9.]+", normalized)
        if token and len(token) >= 2
    ]
    queries.extend(visible_ascii_tokens)
    queries.extend(ascii_tokens)
    return [query for query in dict.fromkeys(queries) if query]


def _winget_manifest_candidate_for_query(query: str) -> HostPackageCandidate | None:
    package_id = query.strip()
    if "." not in package_id:
        return None
    manifest_root = _winget_manifest_root_for_package_id(package_id)
    if manifest_root is None:
        return None
    versions = _github_contents_json(manifest_root["api_path"])
    version_items = [
        item
        for item in versions
        if isinstance(item, dict)
        and item.get("type") == "dir"
        and _looks_like_version(str(item.get("name") or ""))
    ]
    if not version_items:
        return None
    version = sorted(
        (str(item["name"]) for item in version_items),
        key=_version_sort_key,
        reverse=True,
    )[0]
    filename = f"{package_id}.installer.yaml"
    manifest_path = f"{manifest_root['raw_path']}/{version}/{filename}"
    manifest_url = f"{_WINGET_MANIFEST_REPO_RAW}/{manifest_path}"
    manifest_text = _http_text(manifest_url)
    if manifest_text is None:
        return None
    try:
        manifest = yaml.safe_load(manifest_text) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(manifest, dict):
        return None
    installer = _select_manifest_installer(manifest)
    if installer is None:
        return None
    installer_url = str(installer.get("InstallerUrl") or "")
    installer_sha256 = str(installer.get("InstallerSha256") or "").upper()
    if not installer_url.startswith("https://") or not re.fullmatch(
        r"[A-F0-9]{64}",
        installer_sha256,
    ):
        return None
    return HostPackageCandidate(
        source_type="winget_manifest",
        package_id=str(manifest.get("PackageIdentifier") or package_id),
        publisher=_publisher_from_winget_id(package_id),
        confidence=0.96,
        match_reason="official_winget_manifest_dynamic",
        version=str(manifest.get("PackageVersion") or version),
        name=str(manifest.get("PackageIdentifier") or package_id),
        installer_url=installer_url,
        installer_sha256=installer_sha256,
        installer_type=str(installer.get("InstallerType") or manifest.get("InstallerType") or ""),
        installer_switches=dict(
            installer.get("InstallerSwitches") or manifest.get("InstallerSwitches") or {}
        ),
        official_manifest=manifest_url,
        official_source_verification={
            "source": "winget_manifest",
            "manifest_url": manifest_url,
            "download_url": installer_url,
            "checksum_status": "sha256",
            "domain_status": "manifest_trusted",
        },
    )


async def _resolve_official_website_candidate(
    software: str,
    *,
    model_candidates_provider: Any | None,
) -> HostPackageCandidate | None:
    normalized = _normalize_software_query(software)
    if not normalized:
        return None
    static_candidate = _static_official_website_candidate(normalized)
    if static_candidate is not None:
        return static_candidate
    cached = _OFFICIAL_SOURCE_CACHE.get(normalized)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _OFFICIAL_SOURCE_CACHE_TTL_SECONDS:
        return cached[1]
    model_candidates = (
        await model_candidates_provider(normalized)
        if model_candidates_provider is not None
        else []
    )
    candidates = _official_source_candidates_for_query(normalized, model_candidates)
    for candidate in candidates[:10]:
        if candidate.confidence < 0.7:
            continue
        resolved = await asyncio.to_thread(
            _official_website_candidate_for_model_candidate,
            normalized,
            candidate,
        )
        if resolved is not None:
            _OFFICIAL_SOURCE_CACHE[normalized] = (now, resolved)
            return resolved
    _OFFICIAL_SOURCE_CACHE[normalized] = (now, None)
    return None


def _static_official_website_candidate(normalized_query: str) -> HostPackageCandidate | None:
    lowered = normalized_query.lower()
    if any(key in lowered for key in ("vs code", "visual studio code", "vscode")):
        return HostPackageCandidate(
            source_type="official_website",
            package_id="Microsoft.VisualStudioCode",
            publisher="Microsoft",
            confidence=0.95,
            match_reason="static_official_website_verified",
            version=None,
            name="Visual Studio Code",
            installer_url="https://update.code.visualstudio.com/latest/win32-x64-user/stable",
            installer_sha256=None,
            installer_type="inno",
            official_page="https://code.visualstudio.com/Download",
            official_source_verification={
                "source": "official_website",
                "official_page": "https://code.visualstudio.com/Download",
                "download_url": "https://update.code.visualstudio.com/latest/win32-x64-user/stable",
                "checksum_status": "unavailable",
                "domain_status": "vendor_domain_match",
            },
        )
    return None


def _official_source_candidates_for_query(
    query: str,
    model_candidates: list[HostSoftwareModelCandidate],
) -> list[HostSoftwareModelCandidate]:
    candidates = list(model_candidates)
    for key, hint in sorted(
        _KNOWN_OFFICIAL_SOURCE_HINTS.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        if key.lower() in query.lower():
            candidates.append(
                HostSoftwareModelCandidate(
                    query=str(hint["queries"][0]),
                    package_id=str(hint["package_ids"][0]),
                    source_type="winget",
                    confidence=0.92,
                    reason="known_official_source_hint",
                    queries=tuple(str(item) for item in hint["queries"]),
                    package_ids=tuple(str(item) for item in hint["package_ids"]),
                    aliases=tuple(str(item) for item in hint["queries"]),
                    publisher_hints=tuple(str(item) for item in hint["publisher_hints"]),
                    official_sites=tuple(str(item) for item in hint["official_sites"]),
                    download_pages=tuple(str(item) for item in hint["download_pages"]),
                    vendor_domains=tuple(str(item) for item in hint["vendor_domains"]),
                )
            )
    deduped: list[HostSoftwareModelCandidate] = []
    seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
    for item in sorted(candidates, key=lambda candidate: candidate.confidence, reverse=True):
        candidate_key = (
            item.query.lower(),
            tuple(value.lower() for value in item.download_pages),
            tuple(value.lower() for value in item.vendor_domains),
        )
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        deduped.append(item)
    return deduped


def _official_website_candidate_for_model_candidate(
    requested_query: str,
    candidate: HostSoftwareModelCandidate,
) -> HostPackageCandidate | None:
    manifest_candidate = _official_manifest_from_candidate_terms(candidate)
    if manifest_candidate is not None:
        return manifest_candidate
    candidate_domains = _candidate_vendor_domains(candidate)
    pages = _candidate_official_pages(candidate)
    if not pages or not candidate_domains:
        return None
    lookup_terms = _official_source_match_terms(requested_query, candidate)
    for page_url in pages:
        direct_download = (
            _verify_official_download_url(page_url, candidate_domains)
            if _looks_like_installer_url(page_url)
            else None
        )
        if direct_download is not None:
            return _official_website_package_candidate(
                candidate,
                page_url,
                direct_download,
                "trusted_direct_installer_url",
            )
        page_result = _fetch_official_page(page_url, candidate_domains)
        if page_result is None:
            continue
        download_url = _select_official_download_url(
            page_result["url"],
            page_result["body"],
            candidate_domains,
            lookup_terms,
        )
        if download_url is None and _looks_like_installer_url(page_result["url"]):
            download_url = page_result["url"]
        if download_url is None:
            continue
        download_result = _verify_official_download_url(download_url, candidate_domains)
        if download_result is None:
            continue
        return _official_website_package_candidate(
            candidate,
            page_result["url"],
            download_result,
            "matched_vendor_domain",
        )
    return None


def _official_website_package_candidate(
    candidate: HostSoftwareModelCandidate,
    official_page: str,
    download_result: dict[str, Any],
    domain_status: str,
) -> HostPackageCandidate:
    download_url = str(download_result["url"])
    package_id = (
        candidate.package_id
        or (candidate.package_ids[0] if candidate.package_ids else None)
        or _package_id_from_official_download(candidate, download_url)
    )
    if not package_id:
        package_id = _safe_step_key(candidate.query).replace("_", ".")
    publisher = (
        candidate.publisher_hints[0]
        if candidate.publisher_hints
        else _publisher_from_domain(urlparse(download_url).hostname or "")
    )
    verification = {
        "source": "official_website",
        "official_page": official_page,
        "download_url": download_url,
        "domain_status": domain_status,
        "content_type": download_result.get("content_type"),
        "content_length": download_result.get("content_length"),
        "checksum_status": "unavailable",
        "signature_status": "verify_after_download",
        "redirect_chain": download_result.get("redirect_chain") or [download_url],
    }
    return HostPackageCandidate(
        source_type="official_website",
        package_id=package_id,
        publisher=publisher,
        confidence=min(candidate.confidence, 0.9),
        match_reason="model_assisted_official_website_verified",
        version=None,
        name=candidate.display_names[0] if candidate.display_names else candidate.query,
        installer_url=download_url,
        installer_sha256=None,
        installer_type=_installer_type_from_url(download_url),
        installer_switches={},
        official_page=official_page,
        official_source_verification=verification,
    )


def _official_source_assisted_manifest_lookup(software: str) -> HostPackageCandidate | None:
    normalized = _normalize_software_query(software)
    for candidate in _official_source_candidates_for_query(normalized, []):
        resolved = _official_manifest_from_candidate_terms(candidate)
        if resolved is not None:
            return resolved
    return None


def _official_manifest_from_candidate_terms(
    candidate: HostSoftwareModelCandidate,
) -> HostPackageCandidate | None:
    if candidate.confidence < 0.65:
        return None
    for term in _model_candidate_lookup_terms(candidate):
        if "." not in term:
            continue
        resolved = _winget_manifest_candidate_for_query(term)
        if resolved is None:
            continue
        return HostPackageCandidate(
            source_type=resolved.source_type,
            package_id=resolved.package_id,
            publisher=resolved.publisher,
            confidence=min(resolved.confidence, candidate.confidence, 0.97),
            match_reason=f"official_source_assisted_{resolved.match_reason}",
            version=resolved.version,
            name=resolved.name,
            installer_url=resolved.installer_url,
            installer_sha256=resolved.installer_sha256,
            installer_type=resolved.installer_type,
            installer_switches=resolved.installer_switches,
            official_manifest=resolved.official_manifest,
            official_page=resolved.official_page,
            official_source_verification=resolved.official_source_verification,
        )
    return None


def _candidate_vendor_domains(candidate: HostSoftwareModelCandidate) -> list[str]:
    domains = [
        _safe_model_domain(item)
        for item in candidate.vendor_domains
        if _safe_model_domain(item)
    ]
    if domains:
        return [item for item in dict.fromkeys(domains) if item]
    for url in (*candidate.official_sites, *candidate.download_pages):
        parsed = urlparse(url)
        domain = _safe_model_domain(parsed.hostname or "")
        if domain:
            domains.append(domain)
    return [item for item in dict.fromkeys(domains) if item]


def _candidate_official_pages(candidate: HostSoftwareModelCandidate) -> list[str]:
    urls = [
        _safe_model_url(item)
        for item in (*candidate.download_pages, *candidate.official_sites)
        if _safe_model_url(item)
    ]
    return [item for item in dict.fromkeys(urls) if item]


def _official_source_match_terms(
    requested_query: str,
    candidate: HostSoftwareModelCandidate,
) -> list[str]:
    raw = [
        requested_query,
        candidate.query,
        candidate.package_id or "",
        *candidate.package_ids,
        *candidate.queries,
        *candidate.display_names,
        *candidate.aliases,
        *candidate.publisher_hints,
    ]
    terms = [
        _package_match_key(value)
        for value in raw
        if value and len(str(value).strip()) >= 2
    ]
    return [item for item in dict.fromkeys(terms) if item]


def _fetch_official_page(
    url: str,
    vendor_domains: list[str],
) -> dict[str, Any] | None:
    if not _trusted_https_url(url, vendor_domains):
        return None
    response = _http_response_metadata(url, method="GET", max_bytes=512_000)
    if response is None or response["status"] >= 400:
        return None
    final_url = str(response["url"])
    if not _trusted_https_url(final_url, vendor_domains):
        return None
    content_type = str(response.get("content_type") or "").lower()
    body = response.get("body") or b""
    if _looks_like_installer_url(final_url) or "text/html" in content_type:
        return {
            "url": final_url,
            "body": body.decode("utf-8", errors="replace"),
            "content_type": content_type,
        }
    return None


def _select_official_download_url(
    page_url: str,
    body: str,
    vendor_domains: list[str],
    match_terms: list[str],
) -> str | None:
    if _looks_like_installer_url(page_url):
        return page_url
    hrefs = re.findall(r"""(?i)href\s*=\s*["']([^"']+)["']""", body)
    candidates: list[tuple[int, str]] = []
    for href in hrefs[:300]:
        absolute = urljoin(page_url, href.strip())
        if not _looks_like_installer_url(absolute):
            continue
        if not _trusted_https_url(absolute, vendor_domains):
            continue
        score = _download_url_score(absolute, match_terms)
        if score <= 0:
            continue
        candidates.append((score, absolute))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -len(item[1])), reverse=True)
    return candidates[0][1]


def _verify_official_download_url(
    url: str,
    vendor_domains: list[str],
) -> dict[str, Any] | None:
    if not _trusted_https_url(url, vendor_domains) or not _looks_like_installer_url(url):
        return None
    response = _http_response_metadata(url, method="HEAD", max_bytes=0)
    if response is None or response["status"] >= 400 or response["status"] in {405, 403}:
        response = _http_response_metadata(url, method="GET", max_bytes=4096)
    if response is None or response["status"] >= 400:
        return None
    final_url = str(response["url"])
    if (
        not _trusted_https_url(final_url, vendor_domains)
        or not _looks_like_installer_url(final_url)
    ):
        return None
    content_type = str(response.get("content_type") or "").lower()
    content_length = int(response.get("content_length") or 0)
    if content_type and not any(
        marker in content_type
        for marker in (
            "application/",
            "binary",
            "octet-stream",
            "x-msdownload",
            "x-msi",
            "x-msdos-program",
        )
    ):
        return None
    return {
        "url": final_url,
        "content_type": content_type,
        "content_length": content_length,
        "redirect_chain": response.get("redirect_chain") or [url, final_url],
    }


def _winget_manifest_root_for_package_id(package_id: str) -> dict[str, str] | None:
    parts = [part for part in package_id.split(".") if part]
    if len(parts) < 2:
        return None
    first = parts[0]
    if not first or not first[0].isalnum():
        return None
    path_parts = [first[0].lower(), *parts]
    raw_path = "manifests/" + "/".join(path_parts)
    api_path = raw_path
    return {"raw_path": raw_path, "api_path": api_path}


def _github_contents_json(path: str) -> list[dict[str, Any]]:
    url = f"{_WINGET_MANIFEST_REPO_API}/{path}?ref=master"
    text = _http_text(url)
    if text is None:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _http_text(url: str) -> str | None:
    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "CycberHostInstallResolver",
                "Accept": "application/vnd.github+json, text/plain, */*",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


def _http_response_metadata(
    url: str,
    *,
    method: str,
    max_bytes: int,
) -> dict[str, Any] | None:
    redirects: list[str] = [url]

    class _TrackingRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            req: Any,
            fp: Any,
            code: int,
            msg: str,
            headers: Any,
            newurl: str,
        ) -> Any:
            redirects.append(newurl)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(_TrackingRedirectHandler)
    try:
        request = urllib.request.Request(
            url,
            method=method,
            headers={
                "User-Agent": "CycberHostInstallResolver",
                "Accept": "text/html,application/octet-stream,application/x-msdownload,*/*",
            },
        )
        with opener.open(request, timeout=20) as response:
            body = response.read(max_bytes) if max_bytes > 0 else b""
            final_url = response.geturl()
            if not redirects or redirects[-1] != final_url:
                redirects.append(final_url)
            return {
                "url": final_url,
                "status": int(getattr(response, "status", 200) or 200),
                "content_type": response.headers.get("Content-Type", ""),
                "content_length": int(response.headers.get("Content-Length") or 0),
                "body": body,
                "redirect_chain": redirects,
            }
    except urllib.error.HTTPError as exc:
        return {
            "url": str(getattr(exc, "url", url) or url),
            "status": int(exc.code or 0),
            "content_type": exc.headers.get("Content-Type", "") if exc.headers else "",
            "content_length": int(exc.headers.get("Content-Length") or 0) if exc.headers else 0,
            "body": b"",
            "redirect_chain": redirects,
        }
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return None


def _select_manifest_installer(manifest: dict[str, Any]) -> dict[str, Any] | None:
    installers = manifest.get("Installers")
    if not isinstance(installers, list):
        return None
    usable = [item for item in installers if isinstance(item, dict)]
    if not usable:
        return None
    preferred_arches = {"x64", "neutral", "x86"}
    for arch in preferred_arches:
        for item in usable:
            if str(item.get("Architecture") or "").lower() == arch:
                return item
    return usable[0]


def _looks_like_version(value: str) -> bool:
    return bool(re.search(r"\d", value))


def _version_sort_key(value: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for part in re.split(r"([0-9]+)", value):
        if not part:
            continue
        parts.append(int(part) if part.isdigit() else part.lower())
    return tuple(parts)


def _manifest_lookup_key(value: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "", value.lower())


def _trusted_https_url(url: str, vendor_domains: list[str]) -> bool:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        return False
    host = (parsed.hostname or "").strip(".").lower()
    if not host or _host_is_private_or_local(host):
        return False
    return any(_domain_matches(host, domain) for domain in vendor_domains)


def _host_is_private_or_local(host: str) -> bool:
    if host in {"localhost", "local"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _domain_matches(host: str, domain: str) -> bool:
    clean_domain = _safe_model_domain(domain)
    if not clean_domain:
        return False
    clean_host = host.strip(".").lower()
    return clean_host == clean_domain or clean_host.endswith(f".{clean_domain}")


def _safe_model_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "://" in text:
        text = urlparse(text).hostname or ""
    text = text.strip(".")
    if not re.fullmatch(r"[a-z0-9.-]{3,253}", text):
        return ""
    if ".." in text or text.startswith("-") or text.endswith("-"):
        return ""
    return text


def _safe_model_url(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\r\n\t]+", "", text)
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return ""
    if _host_is_private_or_local(parsed.hostname.lower()):
        return ""
    if any(marker in text.lower() for marker in ("`", "$(", "<", ">", "|", ";")):
        return ""
    return text[:500]


def _looks_like_installer_url(url: str) -> bool:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix in _OFFICIAL_DOWNLOAD_EXTENSIONS:
        return True
    lowered = parsed.path.lower()
    return any(lowered.endswith(f"{ext}/") for ext in _OFFICIAL_DOWNLOAD_EXTENSIONS)


def _download_url_score(url: str, match_terms: list[str]) -> int:
    lowered = url.lower()
    score = 1 if _looks_like_installer_url(url) else 0
    for marker in ("windows", "win", "pc", "desktop", "setup", "install"):
        if marker in lowered:
            score += 1
    url_key = _package_match_key(lowered)
    for term in match_terms:
        if term and term in url_key:
            score += 2
    return score


def _package_id_from_official_download(
    candidate: HostSoftwareModelCandidate,
    download_url: str,
) -> str:
    publisher = (
        re.sub(r"[^A-Za-z0-9]+", "", candidate.publisher_hints[0])
        if candidate.publisher_hints
        else _publisher_from_domain(urlparse(download_url).hostname or "")
    )
    name = re.sub(r"[^A-Za-z0-9]+", "", candidate.query or Path(urlparse(download_url).path).stem)
    publisher = publisher or "Official"
    name = name or "Installer"
    return f"{publisher}.{name}"[:120]


def _publisher_from_domain(domain: str) -> str:
    parts = [
        part
        for part in domain.lower().split(".")
        if part and part not in {"com", "cn", "net", "org"}
    ]
    if not parts:
        return "Official vendor"
    return re.sub(r"[^A-Za-z0-9]+", "", parts[-1].title()) or "Official vendor"


def _installer_type_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix == ".msi":
        return "msi"
    if suffix in {".msix", ".msixbundle", ".appx", ".appxbundle"}:
        return "msix"
    return "exe"


def _installer_filename(package_id: str, installer_url: str) -> str:
    suffix = Path(urlparse(installer_url).path).suffix or ".exe"
    return f"{_safe_step_key(package_id)}{suffix}"


def _silent_args_for_manifest_candidate(candidate: HostPackageCandidate) -> str:
    switches = dict(candidate.installer_switches or {})
    silent = str(switches.get("Silent") or switches.get("SilentWithProgress") or "").strip()
    if silent:
        return silent
    installer_type = str(candidate.installer_type or "").lower()
    if installer_type in {"nullsoft", "nsis"}:
        return "/S"
    if installer_type in {"inno", "inno setup"}:
        return "/VERYSILENT /NORESTART"
    if installer_type in {"wix", "msi"}:
        return "/quiet /norestart"
    return "/S"


def _safe_step_key(value: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return key[:80] or "package"


def _host_package_confidence(
    query: str,
    package_id: str,
    name: str,
    *,
    exact: bool,
) -> float:
    normalized_query = _package_match_key(query)
    normalized_id = _package_match_key(package_id)
    normalized_name = _package_match_key(name)
    if not normalized_query:
        return 0.0
    if normalized_query in {normalized_id, normalized_name}:
        return 0.99 if exact else 0.95
    if (
        len(normalized_query) >= 2
        and (normalized_id.endswith(normalized_query) or normalized_name.endswith(normalized_query))
    ):
        return 0.93 if exact else 0.9
    id_parts = set(_package_match_parts(package_id))
    name_parts = set(_package_match_parts(name))
    if normalized_query in id_parts or normalized_query in name_parts:
        return 0.94 if exact else 0.9
    if normalized_query in normalized_id or normalized_query in normalized_name:
        return 0.86 if exact else 0.78
    return 0.0


def _host_package_model_messages(query: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You resolve user-facing software names to Windows package manager search "
                "and installed application lookup candidates. Return strict minified JSON only, "
                "with no markdown and no explanation. Return at most 3 candidates. "
                "You may include official vendor HTTPS home/download pages and vendor domains "
                "as candidates only; never include shell commands, executable paths, flags, "
                "or installer arguments. "
                "Schema: {\"candidates\":[{\"query\":\"string\","
                "\"queries\":[\"string\"],\"display_names\":[\"string\"],"
                "\"package_ids\":[\"string\"],\"aliases\":[\"string\"],"
                "\"publisher_hints\":[\"string\"],"
                "\"official_sites\":[\"https://vendor.example\"],"
                "\"download_pages\":[\"https://vendor.example/download\"],"
                "\"vendor_domains\":[\"vendor.example\"],"
                "\"package_id\":\"optional string\",\"source_type\":\"winget|choco|any\","
                "\"confidence\":0.0,\"reason\":\"short\"}]}. Prefer official winget IDs "
                "when confident, common installed display names for Windows uninstall lookup, "
                "official vendor pages for install fallback, and short aliases otherwise. "
                "If unsure, return {\"candidates\":[]}."
            ),
        },
        {
            "role": "user",
            "content": f"Software requested by user: {query}",
        },
    ]


def _parse_host_package_model_candidates(text: str) -> list[HostSoftwareModelCandidate]:
    payload = _json_object_from_text(text)
    raw_candidates = payload.get("candidates") if isinstance(payload, dict) else None
    if not isinstance(raw_candidates, list):
        return []
    candidates: list[HostSoftwareModelCandidate] = []
    for raw in raw_candidates[:8]:
        if not isinstance(raw, dict):
            continue
        query = _safe_model_candidate_text(raw.get("query") or raw.get("name") or "")
        package_id = _safe_model_candidate_text(raw.get("package_id") or "")
        queries = _safe_model_candidate_list(raw.get("queries"))
        display_names = _safe_model_candidate_list(raw.get("display_names"))
        package_ids = _safe_model_candidate_list(raw.get("package_ids"))
        aliases = _safe_model_candidate_list(raw.get("aliases"))
        publisher_hints = _safe_model_candidate_list(raw.get("publisher_hints"))
        official_sites = _safe_model_url_list(raw.get("official_sites"))
        download_pages = _safe_model_url_list(raw.get("download_pages"))
        vendor_domains = _safe_model_domain_list(raw.get("vendor_domains"))
        source_type = str(raw.get("source_type") or "any").lower()
        if source_type not in {"winget", "choco", "any"}:
            source_type = "any"
        try:
            confidence = float(raw.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        if not any((
            query,
            package_id,
            queries,
            display_names,
            package_ids,
            aliases,
            official_sites,
            download_pages,
            vendor_domains,
        )):
            continue
        candidates.append(
            HostSoftwareModelCandidate(
                query=query or package_id,
                package_id=package_id or None,
                source_type=None if source_type == "any" else source_type,
                confidence=max(0.0, min(confidence, 1.0)),
                reason=_safe_model_candidate_text(raw.get("reason") or "")[:120] or None,
                queries=tuple(queries),
                display_names=tuple(display_names),
                package_ids=tuple(package_ids),
                aliases=tuple(aliases),
                publisher_hints=tuple(publisher_hints),
                official_sites=tuple(official_sites),
                download_pages=tuple(download_pages),
                vendor_domains=tuple(vendor_domains),
            )
        )
    return sorted(candidates, key=lambda item: item.confidence, reverse=True)


def _json_object_from_text(text: str) -> dict[str, Any]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            return {}
        data = json.loads(match.group(0))
    return data if isinstance(data, dict) else {}


def _safe_model_candidate_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"(?i)\b(?:https?|file)://\S+", " ", text)
    text = re.sub(r"(?i)\b(?:winget|choco|powershell|cmd|msiexec|sudo)\b", " ", text)
    text = re.sub(r"(?i)\b(?:install|uninstall|remove-item|remove|delete)\b", " ", text)
    text = re.sub(r"(?i)(?:^|\s)(?:--?|/)[a-z0-9][\w-]*", " ", text)
    text = re.sub(r"[;&|<>`$\\\\/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:120]


def _safe_model_candidate_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [_safe_model_candidate_text(item) for item in value[:8]]
    return [item for item in dict.fromkeys(cleaned) if item]


def _safe_model_url_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [_safe_model_url(item) for item in value[:8]]
    return [item for item in dict.fromkeys(cleaned) if item]


def _safe_model_domain_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [_safe_model_domain(item) for item in value[:8]]
    return [item for item in dict.fromkeys(cleaned) if item]


def _model_candidate_lookup_terms(candidate: HostSoftwareModelCandidate) -> list[str]:
    values = [
        candidate.package_id,
        *candidate.package_ids,
        candidate.query,
        *candidate.queries,
        *candidate.display_names,
        *candidate.aliases,
    ]
    terms = [
        _safe_model_candidate_text(value)
        for value in values
        if value and _safe_model_lookup_term(value)
    ]
    return [item for item in dict.fromkeys(terms) if item][:12]


def _safe_model_lookup_term(value: Any) -> bool:
    text = str(value or "").strip()
    if not text or len(text) > 120:
        return False
    lowered = text.lower()
    blocked = (
        "http://",
        "https://",
        "file://",
        "powershell",
        "cmd.exe",
        "&&",
        "||",
        ";",
        "`",
        "$(",
        "<",
        ">",
    )
    return not any(marker in lowered for marker in blocked)


def _model_adjusted_package_candidate(
    candidate: HostPackageCandidate,
    model_candidate: HostSoftwareModelCandidate,
) -> HostPackageCandidate:
    confidence = min(candidate.confidence, model_candidate.confidence, 0.97)
    return HostPackageCandidate(
        source_type=candidate.source_type,
        package_id=candidate.package_id,
        publisher=candidate.publisher,
        confidence=confidence,
        match_reason=f"model_assisted_{candidate.match_reason}",
        version=candidate.version,
        name=candidate.name,
        installer_url=candidate.installer_url,
        installer_sha256=candidate.installer_sha256,
        installer_type=candidate.installer_type,
        installer_switches=candidate.installer_switches,
        official_manifest=candidate.official_manifest,
        official_page=candidate.official_page,
        official_source_verification=candidate.official_source_verification,
    )


def _model_adjusted_windows_candidate(
    candidate: WindowsUninstallCandidate,
    model_candidate: HostSoftwareModelCandidate,
) -> WindowsUninstallCandidate:
    confidence = min(candidate.confidence, model_candidate.confidence, 0.97)
    return WindowsUninstallCandidate(
        display_name=candidate.display_name,
        uninstall_string=candidate.uninstall_string,
        confidence=confidence,
        match_reason=f"model_assisted_{candidate.match_reason}",
        version=candidate.version,
        publisher=candidate.publisher,
        quiet_uninstall_string=candidate.quiet_uninstall_string,
        install_location=candidate.install_location,
        display_icon=candidate.display_icon,
        registry_key=candidate.registry_key,
    )


async def _choco_package_appears_healthy(package_id: str) -> bool:
    info = await _run_command(
        ["choco", "info", package_id, "--limit-output"],
        cwd=Path.cwd(),
        timeout=60,
    )
    text = f"{info.get('stdout') or ''}\n{info.get('stderr') or ''}".lower()
    if int(info.get("exit_code") or 0) != 0:
        return False
    unhealthy_markers = (
        "possibly broken",
        "likely broken",
        "testing status: failing",
        "package testing status: failing",
    )
    return not any(marker in text for marker in unhealthy_markers)


def _parse_choco_search_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean or "|" not in clean:
            continue
        package_id, version = clean.split("|", 1)
        package_id = package_id.strip()
        if package_id:
            rows.append({"id": package_id, "version": version.strip(), "name": package_id})
    return rows


def _parse_winget_search_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith(("-", "Name ", "名称 ")):
            continue
        parts = re.split(r"\s{2,}", clean)
        if len(parts) < 2:
            continue
        name, package_id = parts[0].strip(), parts[1].strip()
        if "." not in package_id and package_id.lower() not in {_package_match_key(package_id)}:
            continue
        version = parts[2].strip() if len(parts) > 2 else None
        rows.append({"name": name, "id": package_id, "version": version or ""})
    return rows


def _package_match_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _package_match_parts(value: str) -> list[str]:
    return [
        _package_match_key(part)
        for part in re.split(r"[^a-zA-Z0-9]+", value)
        if _package_match_key(part)
    ]


def _publisher_from_winget_id(package_id: str) -> str:
    first = package_id.split(".", 1)[0].strip()
    return first or "unknown"


def _host_command(command_preview: dict[str, Any]) -> list[str]:
    executable = str(command_preview.get("executable") or "")
    args = [str(item) for item in list(command_preview.get("args") or [])]
    return [executable, *args] if executable else []


def _host_command_steps(command_preview: dict[str, Any]) -> list[dict[str, Any]]:
    raw_steps = command_preview.get("steps")
    if isinstance(raw_steps, list) and raw_steps:
        return [dict(step) for step in raw_steps if isinstance(step, dict)]
    command = _host_command(command_preview)
    if not command:
        return []
    return [
        {
            "step_key": "host_install_command",
            "step_type": "package_manager_install",
            "action": command_preview.get("action") or "install",
            "executable": command[0],
            "args": command[1:],
            "cwd": command_preview.get("cwd", "host_default"),
            "env_policy": command_preview.get("env_policy", "minimal"),
            "timeout_seconds": 1800,
        }
    ]


def _host_fallback_steps(command_preview: dict[str, Any]) -> list[dict[str, Any]]:
    raw_steps = command_preview.get("fallback_steps")
    if isinstance(raw_steps, list):
        return [dict(step) for step in raw_steps if isinstance(step, dict)]
    return []


def _host_install_step_log_lines(
    prefix: str,
    index: int,
    step: dict[str, Any],
    result: dict[str, Any],
    exit_code: int,
) -> list[str]:
    return [
        f"{prefix}[{index}].phase={result.get('phase') or ''}",
        f"{prefix}[{index}].started_at={result.get('started_at') or ''}",
        f"{prefix}[{index}].ended_at={result.get('ended_at') or ''}",
        f"{prefix}[{index}].duration_ms={result.get('duration_ms')}",
        f"{prefix}[{index}].key={step.get('step_key') or step.get('step_type') or index}",
        f"{prefix}[{index}].command={redact(result.get('command') or [])}",
        f"{prefix}[{index}].exit_code={exit_code}",
        f"{prefix}[{index}].stdout_tail={result.get('stdout_tail') or ''}",
        f"{prefix}[{index}].stderr_tail={result.get('stderr_tail') or ''}",
        f"{prefix}[{index}].uninstall_verified={result.get('uninstall_verified')}",
    ]


async def _execute_host_install_step(step: dict[str, Any]) -> dict[str, Any]:
    if step.get("step_type") == "windows_uninstall_absent":
        target = str(step.get("target_display_name") or step.get("target_package_id") or "")
        return {
            "exit_code": 0,
            "command": [],
            "failure_reason": None,
            "stdout_tail": f"{target} already absent",
            "stderr_tail": "",
            "resolved_package_id": target,
            "uninstall_verified": True,
        }
    executable = str(step.get("executable") or "").strip()
    args = [str(item) for item in list(step.get("args") or [])]
    command = [executable, *args] if executable else []
    command_for_log = list(step.get("command_redacted") or command)
    if not command:
        return {
            "exit_code": 1,
            "command": [],
            "failure_reason": "empty_host_install_step",
            "stdout_tail": "",
            "stderr_tail": "empty_host_install_step",
        }
    if shutil.which(executable) is None and not Path(executable).exists():
        return {
            "exit_code": 127,
            "command": command_for_log,
            "failure_reason": f"{executable} not found",
            "stdout_tail": "",
            "stderr_tail": f"{executable} not found",
        }
    timeout = int(step.get("timeout_seconds") or 1800)
    completed = await _run_command(command, cwd=Path.cwd(), timeout=timeout)
    exit_code = int(completed.get("exit_code") or 0)
    failure_reason = (
        None if exit_code == 0 else completed.get("stderr_tail") or "host install failed"
    )
    return {
        "exit_code": exit_code,
        "command": command_for_log,
        "failure_reason": failure_reason,
        "stdout_tail": completed.get("stdout_tail") or "",
        "stderr_tail": completed.get("stderr_tail") or "",
        "resolved_package_id": step.get("target_package_id"),
    }


def _host_install_target_package_id(plan: HostInstallPlan) -> str:
    source = plan.install_source
    package_id = str(source.get("display_name") or source.get("package_id") or "")
    if package_id:
        return package_id
    package_resolution = plan.impact_summary.get("package_resolution") or {}
    if isinstance(package_resolution, dict):
        return str(package_resolution.get("target_package_id") or "")
    return ""


def _host_install_target_query(plan: HostInstallPlan) -> str:
    return _normalize_software_query(plan.requested_software)


def _host_install_plan_action(plan: HostInstallPlan) -> str:
    action = str(plan.impact_summary.get("host_action") or plan.command_preview.get("action") or "")
    return "uninstall" if action == "uninstall" else "install"


def _plan_uses_official_website_only(plan: HostInstallPlan) -> bool:
    source_type = str(plan.install_source.get("source_type") or "")
    if source_type == "official_website_installer":
        return True
    steps = _host_command_steps(plan.command_preview)
    return any(str(step.get("step_type") or "") == "official_website_installer" for step in steps)


def _resolve_windows_uninstall_candidate(software: str) -> WindowsUninstallCandidate | None:
    if os.name != "nt":
        return None
    query = _normalize_software_query(software)
    if not query:
        return None
    rows = _windows_uninstall_entries(query)
    candidates: list[WindowsUninstallCandidate] = []
    for row in rows:
        display_name = str(row.get("DisplayName") or "").strip()
        uninstall_string = str(row.get("UninstallString") or "").strip()
        if not display_name or not uninstall_string:
            continue
        confidence = _windows_uninstall_match_confidence(query, display_name)
        if confidence < 0.9:
            continue
        candidates.append(
            WindowsUninstallCandidate(
                display_name=display_name,
                uninstall_string=uninstall_string,
                confidence=confidence,
                match_reason="windows_uninstall_registry",
                version=str(row.get("DisplayVersion") or "").strip() or None,
                publisher=str(row.get("Publisher") or "").strip() or None,
                quiet_uninstall_string=str(row.get("QuietUninstallString") or "").strip() or None,
                install_location=str(row.get("InstallLocation") or "").strip() or None,
                display_icon=str(row.get("DisplayIcon") or "").strip() or None,
                registry_key=str(row.get("RegistryKey") or "").strip() or None,
            )
        )
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            item.confidence,
            bool(item.quiet_uninstall_string),
            -len(item.display_name),
        ),
        reverse=True,
    )
    return candidates[0]


async def _detect_installed_version(package_id: str) -> str | None:
    return await _detect_installed_version_for_terms(
        _display_name_terms_for_package(package_id),
        package_id=package_id,
    )


async def _detect_installed_version_for_terms(
    terms: list[str],
    *,
    package_id: str | None = None,
) -> str | None:
    clean_terms = _clean_detect_terms([package_id or "", *terms])
    version = await asyncio.to_thread(_windows_uninstall_version_for_terms, clean_terms)
    if version:
        return version
    package_id = str(package_id or (clean_terms[0] if clean_terms else "")).strip()
    for term in clean_terms:
        version = await _detect_direct_executable_version(term)
        if version:
            return version
    version = await _detect_package_manager_installed_version(package_id)
    if version:
        return version
    return None


async def _detect_installed_version_without_registry(package_id: str) -> str | None:
    version = await _detect_direct_executable_version(package_id)
    if version:
        return version
    return await _detect_package_manager_installed_version(package_id)


async def _detect_direct_executable_version(term: str) -> str | None:
    executable = _safe_direct_executable_name(term)
    if executable and shutil.which(executable):
        completed = await _run_command([executable, "--version"], cwd=Path.cwd(), timeout=15)
        if completed["exit_code"] == 0:
            return _tail(str(completed["stdout"]).strip(), count=1) or None
    return None


async def _detect_package_manager_installed_version(package_id: str) -> str | None:
    if package_id and shutil.which("choco"):
        completed = await _run_command(
            ["choco", "list", package_id, "--exact", "--limit-output"],
            cwd=Path.cwd(),
            timeout=30,
        )
        text = str(completed["stdout"]).strip()
        return text or None
    if package_id and shutil.which("winget"):
        completed = await _run_command(
            ["winget", "list", "--id", package_id, "--source", "winget"],
            cwd=Path.cwd(),
            timeout=30,
        )
        if int(completed.get("exit_code") or 0) == 0 and package_id.lower() in str(
            completed.get("stdout") or ""
        ).lower():
            return _tail(str(completed.get("stdout") or ""), count=3) or "installed_by_winget"
    return None


def _clean_detect_terms(terms: list[str]) -> list[str]:
    candidates: list[str] = []
    for term in terms:
        raw = str(term or "").strip()
        if not raw:
            continue
        candidates.extend(_display_name_terms_for_package(raw))
        candidates.append(_host_install_visible_software_name(raw))
    return list(dict.fromkeys(term for term in candidates if term))


async def _wait_for_windows_uninstall(display_name: str, *, timeout: float = 120.0) -> str | None:
    deadline = time.monotonic() + timeout
    version = await _detect_installed_version(display_name)
    while version is not None and time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        version = await _detect_installed_version(display_name)
    return version


def _safe_direct_executable_name(package_id: str) -> str | None:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", package_id):
        return package_id.rsplit(".", 1)[-1]
    return None


def _display_name_terms_for_package(package_id: str) -> list[str]:
    terms = [package_id]
    if "." in package_id:
        terms.extend(part for part in package_id.split(".") if len(part) >= 2)
        terms.append(package_id.rsplit(".", 1)[-1])
    return list(dict.fromkeys(term for term in terms if term))


def _windows_uninstall_version(display_name: str) -> str | None:
    row = _windows_uninstall_entry(display_name)
    version = str(row.get("DisplayVersion") or "").strip() if row else ""
    return str(redact(version)) if version else None


def _windows_uninstall_version_for_terms(display_names: list[str]) -> str | None:
    rows = _windows_uninstall_entries_for_terms(display_names)
    for row in rows:
        version = str(row.get("DisplayVersion") or "").strip()
        if version:
            return str(redact(version))
    return None


def _windows_uninstall_entry(display_name: str) -> dict[str, str] | None:
    rows = _windows_uninstall_entries(display_name)
    return rows[0] if rows else None


def _windows_uninstall_lookup_supported() -> bool:
    return os.name == "nt" and _powershell_executable() is not None


def _windows_uninstall_match_confidence(query: str, display_name: str) -> float:
    normalized_query = _package_match_key(query)
    normalized_display = _package_match_key(display_name)
    if normalized_query and normalized_display:
        return _host_package_confidence(
            query,
            display_name,
            display_name,
            exact=normalized_query == normalized_display,
        )
    query_text = re.sub(r"\s+", "", query.lower())
    display_text = re.sub(r"\s+", "", display_name.lower())
    if not query_text or not display_text:
        return 0.0
    if query_text == display_text:
        return 0.99
    if display_text.endswith(query_text):
        return 0.93
    if query_text in display_text:
        return 0.9
    return 0.0


def _windows_uninstall_entries(display_name: str) -> list[dict[str, str]]:
    return _windows_uninstall_entries_for_terms([display_name])


def _windows_uninstall_entries_for_terms(display_names: list[str]) -> list[dict[str, str]]:
    if not _windows_uninstall_lookup_supported():
        return []
    terms = list(dict.fromkeys(str(item).strip() for item in display_names if str(item).strip()))
    if not terms:
        return []
    roots = (
        r"HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
        r"HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*",
    )
    powershell = _powershell_executable() or "powershell"
    terms_json = json.dumps(terms, ensure_ascii=False)
    escaped_terms_json = terms_json.replace("'", "''")
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        (
            f"$terms = '{escaped_terms_json}' | ConvertFrom-Json; "
            "$items=@(); "
            + " ".join(
                f"$items += Get-ItemProperty '{root}' -ErrorAction SilentlyContinue;"
                for root in roots
            )
            + " $match=$items | Where-Object { "
            "$name=[string]$_.DisplayName; "
            "$name -and ($terms | Where-Object { "
            "$term=[string]$_; $name -eq $term -or $name -like ('*' + $term + '*') "
            "}) "
            "}; "
            "if ($match) { $match | Select-Object DisplayName,DisplayVersion,"
            "Publisher,InstallLocation,DisplayIcon,UninstallString,QuietUninstallString,"
            "@{Name='RegistryKey';Expression={$_.PSChildName}} | ConvertTo-Json -Compress }"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    text = completed.stdout.strip()
    if completed.returncode != 0 or not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    rows: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            rows.append({str(key): str(value or "") for key, value in item.items()})
    return rows


def _parse_windows_uninstall_command(command_text: str) -> tuple[str, list[str]] | None:
    text = command_text.strip()
    if not text or any(marker in text for marker in ("\n", "\r")):
        return None
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        return None
    if not parts:
        return None
    executable = parts[0].strip()
    args = [part.strip() for part in parts[1:] if part.strip()]
    if not _safe_windows_uninstall_executable(executable):
        return None
    return executable, args


def _safe_windows_uninstall_executable(executable: str) -> bool:
    lower = executable.lower()
    if lower in {"msiexec", "msiexec.exe"}:
        return True
    if lower.endswith("\\msiexec.exe"):
        return True
    path = Path(executable)
    if path.suffix.lower() != ".exe":
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    if not resolved.exists():
        return False
    allowed_roots = [
        os.environ.get("ProgramFiles", ""),
        os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    resolved_text = str(resolved).lower()
    return any(
        root and resolved_text.startswith(str(Path(root).resolve()).lower())
        for root in allowed_roots
    )


def _add_best_effort_silent_uninstall_args(
    executable: str,
    args: list[str],
) -> tuple[list[str], bool]:
    lower_args = {arg.lower() for arg in args}
    lower_exe = executable.lower()
    if lower_exe.endswith("msiexec.exe") or lower_exe == "msiexec":
        new_args = [
            f"/x{arg[2:]}" if arg.lower().startswith("/i") and len(arg) > 2 else arg
            for arg in args
        ]
        new_args = ["/x" if arg.lower() == "/i" else arg for arg in new_args]
        added = False
        if not any(arg in lower_args for arg in {"/qn", "/quiet", "/passive"}):
            new_args.append("/qn")
            added = True
        if "/norestart" not in lower_args:
            new_args.append("/norestart")
            added = True
        return new_args, added
    if any(arg.lower() in {"/s", "/silent", "/verysilent", "--silent", "-silent"} for arg in args):
        return args, False
    return [*args, "/S"], True


def _path_needs_powershell(executable: str) -> bool:
    return bool(
        Path(executable).is_absolute()
        and (" " in executable or shutil.which(executable) is None)
    )


def _powershell_single_quoted(value: str) -> str:
    return value.replace("'", "''")


def _install_path_summary(package_id: str, success: bool) -> str:
    if not success:
        return "not_installed"
    executable = _safe_direct_executable_name(package_id)
    if executable:
        path = shutil.which(executable)
        if path:
            return str(redact(path))
    if shutil.which("choco"):
        path = _choco_install_location(package_id)
        if path:
            return str(redact(path))
    for display_name in _display_name_terms_for_package(package_id):
        entry = _windows_uninstall_entry(display_name)
        if not entry:
            continue
        for key in ("DisplayIcon", "InstallLocation"):
            value = str(entry.get(key) or "").strip().strip('"')
            if value:
                return str(redact(value))
    return "installed_by_package_manager"


def _choco_install_location(package_id: str) -> str | None:
    if not package_id or not shutil.which("choco"):
        return None
    try:
        completed = subprocess.run(
            ["choco", "info", package_id, "--limit-output"],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    text = completed.stdout.strip()
    if text:
        return f"installed_by_choco:{package_id}"
    return None


def _binding_hash(*items: Any) -> str:
    raw = json.dumps(items, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
