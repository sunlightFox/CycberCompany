from __future__ import annotations

from typing import Any

from core_types import ApiModel, ExecutionBoundaryDiagnostic


class ExecutionBoundaryDiagnosticResponse(ExecutionBoundaryDiagnostic):
    pass


class ExecutionBoundarySandboxStatusResponse(ApiModel):
    active_backend: str
    requested_backend: str
    available_backends: list[dict[str, Any]]
    fallback_reason: str | None = None
    fallback_chain: list[str]
    profile: dict[str, Any] | None = None
    profile_id: str
    limits: dict[str, Any]
    degraded_backend: bool = False
    low_integrity_status: str
    last_diagnostic_summary: dict[str, Any] | None = None
