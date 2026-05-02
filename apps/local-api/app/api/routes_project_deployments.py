from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.project_deployments import (
    DeploymentActionRequest,
    DeploymentLogsResponse,
    HostInstallExecuteRequest,
    HostInstallExecutionResponse,
    HostInstallPlanRequest,
    HostInstallPlanResponse,
    ProjectDeploymentResponse,
    ProjectDeployRequest,
    ProjectWorkspaceCreateRequest,
    ProjectWorkspaceResponse,
    ToolchainEnsureRequest,
    ToolchainListResponse,
    ToolchainResponse,
)
from app.services.registry import ServiceRegistry

workspace_router = APIRouter(prefix="/api/project-workspaces", tags=["project-deployments"])
deployment_router = APIRouter(prefix="/api/project-deployments", tags=["project-deployments"])
toolchain_router = APIRouter(prefix="/api/toolchains", tags=["project-deployments"])
host_install_router = APIRouter(prefix="/api/host-installs", tags=["host-installs"])


@workspace_router.post("", response_model=ProjectWorkspaceResponse)
async def create_project_workspace(
    payload: ProjectWorkspaceCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ProjectWorkspaceResponse:
    workspace = await registry.project_workspace_service.create(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ProjectWorkspaceResponse(**workspace.model_dump(mode="json"))


@workspace_router.get("/{workspace_id}", response_model=ProjectWorkspaceResponse)
async def get_project_workspace(
    workspace_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ProjectWorkspaceResponse:
    workspace = await registry.project_workspace_service.detail(workspace_id)
    return ProjectWorkspaceResponse(**workspace.model_dump(mode="json"))


@deployment_router.post("", response_model=ProjectDeploymentResponse)
async def create_project_deployment(
    payload: ProjectDeployRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ProjectDeploymentResponse:
    deployment = await registry.project_deployment_service.create_plan(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return await _deployment_response(registry, deployment.deployment_id)


@deployment_router.get("/{deployment_id}", response_model=ProjectDeploymentResponse)
async def get_project_deployment(
    deployment_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ProjectDeploymentResponse:
    return await _deployment_response(registry, deployment_id)


@deployment_router.post("/{deployment_id}/approve-plan", response_model=ProjectDeploymentResponse)
async def approve_project_deployment_plan(
    deployment_id: str,
    payload: DeploymentActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ProjectDeploymentResponse:
    deployment = await registry.project_deployment_service.run(
        deployment_id,
        approval_id=payload.approval_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return await _deployment_response(registry, deployment.deployment_id)


@deployment_router.post("/{deployment_id}/run", response_model=ProjectDeploymentResponse)
async def run_project_deployment(
    deployment_id: str,
    payload: DeploymentActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ProjectDeploymentResponse:
    deployment = await registry.project_deployment_service.run(
        deployment_id,
        approval_id=payload.approval_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return await _deployment_response(registry, deployment.deployment_id)


@deployment_router.post("/{deployment_id}/stop", response_model=ProjectDeploymentResponse)
async def stop_project_deployment(
    deployment_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ProjectDeploymentResponse:
    deployment = await registry.project_deployment_service.stop(
        deployment_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return await _deployment_response(registry, deployment.deployment_id)


@deployment_router.get("/{deployment_id}/logs", response_model=DeploymentLogsResponse)
async def get_project_deployment_logs(
    deployment_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> DeploymentLogsResponse:
    return DeploymentLogsResponse(**await registry.project_deployment_service.logs(deployment_id))


@toolchain_router.get("", response_model=ToolchainListResponse)
async def list_toolchains(
    runtime_name: str | None = Query(default=None),
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolchainListResponse:
    return ToolchainListResponse(
        items=await registry.toolchain_service.list(runtime_name=runtime_name)
    )


@toolchain_router.post("/ensure", response_model=ToolchainResponse)
async def ensure_toolchain(
    payload: ToolchainEnsureRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolchainResponse:
    toolchain = await registry.toolchain_service.ensure(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ToolchainResponse(**toolchain.model_dump(mode="json"))


@host_install_router.post("/plan", response_model=HostInstallPlanResponse)
async def create_host_install_plan(
    payload: HostInstallPlanRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> HostInstallPlanResponse:
    plan = await registry.host_install_service.create_plan(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return HostInstallPlanResponse(**plan.model_dump(mode="json"))


@host_install_router.get("/{host_install_plan_id}", response_model=HostInstallPlanResponse)
async def get_host_install_plan(
    host_install_plan_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> HostInstallPlanResponse:
    plan = await registry.host_install_service.detail(host_install_plan_id)
    return HostInstallPlanResponse(**plan.model_dump(mode="json"))


@host_install_router.post(
    "/{host_install_plan_id}/execute",
    response_model=HostInstallExecutionResponse,
)
async def execute_host_install_plan(
    host_install_plan_id: str,
    payload: HostInstallExecuteRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> HostInstallExecutionResponse:
    execution = await registry.host_install_service.execute(
        host_install_plan_id,
        approval_id=payload.approval_id,
        dry_run=payload.dry_run,
        trace_id=getattr(request.state, "trace_id", None),
    )
    plan = await registry.host_install_service.detail(host_install_plan_id)
    return HostInstallExecutionResponse(
        **execution.model_dump(mode="json"),
        plan=plan,
    )


async def _deployment_response(
    registry: ServiceRegistry,
    deployment_id: str,
) -> ProjectDeploymentResponse:
    deployment = await registry.project_deployment_service.detail(deployment_id)
    workspace = await registry.project_workspace_service.detail(deployment.workspace_id)
    process_row = await registry.project_deployments.get_managed_process_for_deployment(
        deployment_id
    )
    port_row = await registry.project_deployments.get_port_lease_for_deployment(deployment_id)
    return ProjectDeploymentResponse(
        **deployment.model_dump(mode="json"),
        workspace=workspace,
        managed_process=process_row,
        port_lease=port_row,
    )
