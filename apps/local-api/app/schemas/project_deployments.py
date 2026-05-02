from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    HostInstallExecution,
    HostInstallPlan,
    ManagedProcess,
    PortLease,
    ProjectDeployment,
    ProjectWorkspace,
    ToolchainInstall,
)
from pydantic import Field


class ProjectWorkspaceCreateRequest(ApiModel):
    owner_member_id: EntityId = "mem_xiaoyao"
    task_id: EntityId | None = None
    source_type: str = "github"
    source_uri: str | None = None
    preferred_backend: str = "auto"
    constraints: dict[str, Any] = Field(default_factory=dict)


class ProjectWorkspaceResponse(ProjectWorkspace):
    pass


class ProjectDeployRequest(ApiModel):
    member_id: EntityId = "mem_xiaoyao"
    conversation_id: EntityId | None = None
    source_uri: str
    target: dict[str, Any] = Field(default_factory=lambda: {"mode": "preview"})
    constraints: dict[str, Any] = Field(default_factory=dict)
    task_id: EntityId | None = None


class ProjectDeploymentResponse(ProjectDeployment):
    workspace: ProjectWorkspace | None = None
    managed_process: ManagedProcess | None = None
    port_lease: PortLease | None = None


class DeploymentActionRequest(ApiModel):
    actor_id: EntityId | None = "user_local_owner"
    reason: str | None = None
    approval_id: EntityId | None = None


class DeploymentLogsResponse(ApiModel):
    deployment: ProjectDeployment
    log_artifact_id: EntityId | None = None
    content_preview: str | None = None
    status: str
    reason_code: str | None = None
    recoverable: bool = False
    next_step: str | None = None


class ToolchainEnsureRequest(ApiModel):
    runtime_name: str
    version: str = "lts"
    install_mode: str = "portable"
    source_uri: str | None = None
    checksum: str | None = None
    task_id: EntityId | None = None


class ToolchainListResponse(ApiModel):
    items: list[ToolchainInstall] = Field(default_factory=list)


class ToolchainResponse(ToolchainInstall):
    pass


class HostInstallPlanRequest(ApiModel):
    member_id: EntityId = "mem_xiaoyao"
    conversation_id: EntityId | None = None
    task_id: EntityId | None = None
    requested_software: str
    install_scope: str = "host"
    dry_run: bool = True
    constraints: dict[str, Any] = Field(default_factory=dict)


class HostInstallPlanResponse(HostInstallPlan):
    pass


class HostInstallExecuteRequest(ApiModel):
    approval_id: EntityId | None = None
    actor_id: EntityId | None = "user_local_owner"
    dry_run: bool = True


class HostInstallExecutionResponse(HostInstallExecution):
    plan: HostInstallPlan | None = None
