from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class ProjectWorkspace(ApiModel):
    workspace_id: EntityId
    organization_id: EntityId
    task_id: EntityId | None = None
    owner_member_id: EntityId
    source_type: str
    source_uri: str | None = None
    root_uri: str
    backend_type: str
    status: str
    stack_summary: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class ProjectDeployment(ApiModel):
    deployment_id: EntityId
    organization_id: EntityId
    workspace_id: EntityId
    task_id: EntityId
    status: str
    backend_type: str
    plan: dict[str, Any] = Field(default_factory=dict)
    current_step_key: str | None = None
    endpoint: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class ToolchainInstall(ApiModel):
    toolchain_id: EntityId
    organization_id: EntityId
    runtime_name: str
    version: str
    install_mode: str
    root_uri: str
    source_uri: str | None = None
    checksum: str | None = None
    status: str
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class HostInstallPlan(ApiModel):
    host_install_plan_id: EntityId
    organization_id: EntityId
    task_id: EntityId
    requested_software: str
    install_source: dict[str, Any] = Field(default_factory=dict)
    command_preview: dict[str, Any] = Field(default_factory=dict)
    impact_summary: dict[str, Any] = Field(default_factory=dict)
    risk_level: str
    status: str
    approval_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class HostInstallExecution(ApiModel):
    host_install_execution_id: EntityId
    organization_id: EntityId
    host_install_plan_id: EntityId
    task_id: EntityId
    status: str
    exit_code: int | None = None
    log_artifact_id: EntityId | None = None
    version_detected: str | None = None
    install_path_summary: str | None = None
    failure_reason: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class ManagedProcess(ApiModel):
    managed_process_id: EntityId
    organization_id: EntityId
    deployment_id: EntityId | None = None
    task_id: EntityId
    workspace_id: EntityId | None = None
    process_kind: str
    command_redacted: dict[str, Any] = Field(default_factory=dict)
    backend_type: str
    status: str
    port: int | None = None
    endpoint_url: str | None = None
    log_artifact_id: EntityId | None = None
    started_at: datetime | None = None
    stopped_at: datetime | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class PortLease(ApiModel):
    port_lease_id: EntityId
    organization_id: EntityId
    task_id: EntityId | None = None
    deployment_id: EntityId | None = None
    port: int
    protocol: str
    status: str
    leased_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
