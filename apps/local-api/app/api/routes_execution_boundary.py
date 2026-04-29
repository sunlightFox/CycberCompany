from __future__ import annotations

from core_types import ErrorCode
from fastapi import APIRouter, Depends

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.schemas.execution_boundary import (
    ExecutionBoundaryDiagnosticResponse,
    ExecutionBoundarySandboxStatusResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/execution-boundary", tags=["execution-boundary"])


@router.get(
    "/sandbox-status",
    response_model=ExecutionBoundarySandboxStatusResponse,
)
async def execution_boundary_sandbox_status(
    registry: ServiceRegistry = Depends(get_registry),
) -> ExecutionBoundarySandboxStatusResponse:
    return ExecutionBoundarySandboxStatusResponse(
        **await registry.execution_boundary_service.sandbox_status()
    )


@router.get(
    "/diagnostics/{diagnostic_id}",
    response_model=ExecutionBoundaryDiagnosticResponse,
)
async def execution_boundary_diagnostic(
    diagnostic_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ExecutionBoundaryDiagnosticResponse:
    diagnostic = await registry.execution_boundary_service.get_diagnostic(diagnostic_id)
    if diagnostic is None:
        raise AppError(ErrorCode.NOT_FOUND, "执行边界诊断不存在", status_code=404)
    return ExecutionBoundaryDiagnosticResponse(**diagnostic.model_dump(mode="json"))
