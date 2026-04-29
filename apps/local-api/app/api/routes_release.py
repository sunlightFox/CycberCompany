from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.release import (
    BackupJobCreateRequest,
    BackupJobResponse,
    BenchmarkRunCreateRequest,
    BenchmarkRunResponse,
    DiagnosticBundleCreateRequest,
    DiagnosticBundleResponse,
    EvalRunCreateRequest,
    EvalRunResponse,
    EvalSuiteListResponse,
    ReleaseEvidenceListResponse,
    ReleaseFindingListResponse,
    ReleaseGateCreateRequest,
    ReleaseGateListResponse,
    ReleaseGateResponse,
    ReleaseReportResponse,
    RestoreJobCreateRequest,
    RestoreJobResponse,
    SecurityAuditRunCreateRequest,
    SecurityAuditRunResponse,
)
from app.services.registry import ServiceRegistry

release_router = APIRouter(prefix="/api/release-gates", tags=["release"])
eval_router = APIRouter(prefix="/api/evals", tags=["evals"])
security_router = APIRouter(prefix="/api/security", tags=["security"])
backup_router = APIRouter(prefix="/api/backup", tags=["backup"])
restore_router = APIRouter(prefix="/api/restore", tags=["restore"])
benchmark_router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])
diagnostic_router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@release_router.post("", response_model=ReleaseGateResponse)
async def create_release_gate(
    payload: ReleaseGateCreateRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> ReleaseGateResponse:
    return ReleaseGateResponse(
        **(
            await registry.release_gate_service.create_gate(
                organization_id=payload.organization_id,
                scope=payload.scope,
                required_checks=payload.required_checks,
                created_by_member_id=payload.created_by_member_id,
            )
        ).model_dump(mode="json")
    )


@release_router.get("", response_model=ReleaseGateListResponse)
async def list_release_gates(
    organization_id: str = Query(default="org_default"),
    registry: ServiceRegistry = Depends(get_registry),
) -> ReleaseGateListResponse:
    return ReleaseGateListResponse(
        items=await registry.release_gate_service.list_gates(organization_id)
    )


@release_router.get("/{release_gate_id}", response_model=ReleaseGateResponse)
async def get_release_gate(
    release_gate_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ReleaseGateResponse:
    return ReleaseGateResponse(
        **(await registry.release_gate_service.get_gate(release_gate_id)).model_dump(mode="json")
    )


@release_router.post("/{release_gate_id}/run", response_model=ReleaseGateResponse)
async def run_release_gate(
    release_gate_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ReleaseGateResponse:
    return ReleaseGateResponse(
        **(
            await registry.release_gate_service.run_gate(
                release_gate_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@release_router.get("/{release_gate_id}/evidence", response_model=ReleaseEvidenceListResponse)
async def release_gate_evidence(
    release_gate_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ReleaseEvidenceListResponse:
    return ReleaseEvidenceListResponse(
        items=await registry.release_gate_service.list_evidence(release_gate_id)
    )


@release_router.get("/{release_gate_id}/findings", response_model=ReleaseFindingListResponse)
async def release_gate_findings(
    release_gate_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ReleaseFindingListResponse:
    return ReleaseFindingListResponse(
        items=await registry.release_gate_service.list_findings(release_gate_id)
    )


@release_router.get("/{release_gate_id}/report", response_model=ReleaseReportResponse)
async def release_gate_report(
    release_gate_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ReleaseReportResponse:
    return ReleaseReportResponse(
        **(await registry.release_gate_service.get_report(release_gate_id)).model_dump(mode="json")
    )


@eval_router.get("/suites", response_model=EvalSuiteListResponse)
async def eval_suites(registry: ServiceRegistry = Depends(get_registry)) -> EvalSuiteListResponse:
    return EvalSuiteListResponse(items=await registry.release_gate_service.list_eval_suites())


@eval_router.post("/runs", response_model=EvalRunResponse)
async def create_eval_run(
    payload: EvalRunCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> EvalRunResponse:
    return EvalRunResponse(
        **(
            await registry.release_gate_service.run_eval(
                release_gate_id=payload.release_gate_id,
                suite_id=payload.suite_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@eval_router.get("/runs/{eval_run_id}", response_model=EvalRunResponse)
async def get_eval_run(
    eval_run_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> EvalRunResponse:
    return EvalRunResponse(
        **(await registry.release_gate_service.get_eval_run(eval_run_id)).model_dump(mode="json")
    )


@security_router.post("/audit-runs", response_model=SecurityAuditRunResponse)
async def create_security_audit_run(
    payload: SecurityAuditRunCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> SecurityAuditRunResponse:
    return SecurityAuditRunResponse(
        **(
            await registry.release_gate_service.run_security_audit(
                release_gate_id=payload.release_gate_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@security_router.get("/audit-runs/{audit_run_id}", response_model=SecurityAuditRunResponse)
async def get_security_audit_run(
    audit_run_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SecurityAuditRunResponse:
    return SecurityAuditRunResponse(
        **(
            await registry.release_gate_service.get_security_audit_run(audit_run_id)
        ).model_dump(mode="json")
    )


@backup_router.post("/jobs", response_model=BackupJobResponse)
async def create_backup_job(
    payload: BackupJobCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BackupJobResponse:
    return BackupJobResponse(
        **(
            await registry.release_gate_service.create_backup(
                organization_id=payload.organization_id,
                scope=payload.scope,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@backup_router.get("/jobs/{backup_job_id}", response_model=BackupJobResponse)
async def get_backup_job(
    backup_job_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BackupJobResponse:
    return BackupJobResponse(
        **(await registry.release_gate_service.get_backup(backup_job_id)).model_dump(mode="json")
    )


@restore_router.post("/jobs", response_model=RestoreJobResponse)
async def create_restore_job(
    payload: RestoreJobCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> RestoreJobResponse:
    return RestoreJobResponse(
        **(
            await registry.release_gate_service.create_restore(
                organization_id=payload.organization_id,
                backup_job_id=payload.backup_job_id,
                input_uri=payload.input_uri,
                restore_plan=payload.restore_plan,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@restore_router.get("/jobs/{restore_job_id}", response_model=RestoreJobResponse)
async def get_restore_job(
    restore_job_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> RestoreJobResponse:
    return RestoreJobResponse(
        **(await registry.release_gate_service.get_restore(restore_job_id)).model_dump(mode="json")
    )


@benchmark_router.post("/runs", response_model=BenchmarkRunResponse)
async def create_benchmark_run(
    payload: BenchmarkRunCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BenchmarkRunResponse:
    return BenchmarkRunResponse(
        **(
            await registry.release_gate_service.run_benchmark(
                release_gate_id=payload.release_gate_id,
                benchmark_type=payload.benchmark_type,
                scenario=payload.scenario,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@benchmark_router.get("/runs/{benchmark_run_id}", response_model=BenchmarkRunResponse)
async def get_benchmark_run(
    benchmark_run_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BenchmarkRunResponse:
    return BenchmarkRunResponse(
        **(
            await registry.release_gate_service.get_benchmark(benchmark_run_id)
        ).model_dump(mode="json")
    )


@diagnostic_router.post("/bundles", response_model=DiagnosticBundleResponse)
async def create_diagnostic_bundle(
    payload: DiagnosticBundleCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> DiagnosticBundleResponse:
    return DiagnosticBundleResponse(
        **(
            await registry.release_gate_service.create_diagnostic_bundle(
                organization_id=payload.organization_id,
                scope=payload.scope,
                redaction_policy=payload.redaction_policy,
                created_by_member_id=payload.created_by_member_id,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@diagnostic_router.get("/bundles/{bundle_id}", response_model=DiagnosticBundleResponse)
async def get_diagnostic_bundle(
    bundle_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> DiagnosticBundleResponse:
    return DiagnosticBundleResponse(
        **(await registry.release_gate_service.get_diagnostic(bundle_id)).model_dump(mode="json")
    )
