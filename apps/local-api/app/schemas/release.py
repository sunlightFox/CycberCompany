from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    BackupJob,
    BenchmarkRun,
    DiagnosticBundle,
    EntityId,
    EvalRun,
    EvalSuite,
    FullHealthResponse,
    ReleaseEvidence,
    ReleaseFinding,
    ReleaseGate,
    ReleaseReport,
    RestoreJob,
    SecurityAuditRun,
)
from pydantic import Field


class ReleaseGateCreateRequest(ApiModel):
    organization_id: EntityId = "org_default"
    scope: dict[str, Any] = Field(default_factory=dict)
    required_checks: list[str] = Field(default_factory=list)
    created_by_member_id: EntityId | None = "mem_xiaoyao"


class ReleaseGateListResponse(ApiModel):
    items: list[ReleaseGate] = Field(default_factory=list)


class ReleaseEvidenceListResponse(ApiModel):
    items: list[ReleaseEvidence] = Field(default_factory=list)


class ReleaseFindingListResponse(ApiModel):
    items: list[ReleaseFinding] = Field(default_factory=list)


class EvalSuiteListResponse(ApiModel):
    items: list[EvalSuite] = Field(default_factory=list)


class EvalRunCreateRequest(ApiModel):
    release_gate_id: EntityId | None = None
    suite_id: EntityId | None = None


class SecurityAuditRunCreateRequest(ApiModel):
    release_gate_id: EntityId | None = None


class BackupJobCreateRequest(ApiModel):
    organization_id: EntityId = "org_default"
    scope: dict[str, Any] = Field(default_factory=dict)


class RestoreJobCreateRequest(ApiModel):
    organization_id: EntityId = "org_default"
    backup_job_id: EntityId | None = None
    input_uri: str | None = None
    restore_plan: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRunCreateRequest(ApiModel):
    release_gate_id: EntityId | None = None
    benchmark_type: str = "smoke"
    scenario: dict[str, Any] = Field(default_factory=dict)


class DiagnosticBundleCreateRequest(ApiModel):
    organization_id: EntityId = "org_default"
    scope: dict[str, Any] = Field(default_factory=dict)
    redaction_policy: dict[str, Any] = Field(default_factory=dict)
    created_by_member_id: EntityId | None = "mem_xiaoyao"


class ReleaseGateResponse(ReleaseGate):
    pass


class EvalRunResponse(EvalRun):
    pass


class SecurityAuditRunResponse(SecurityAuditRun):
    pass


class BackupJobResponse(BackupJob):
    pass


class RestoreJobResponse(RestoreJob):
    pass


class BenchmarkRunResponse(BenchmarkRun):
    pass


class DiagnosticBundleResponse(DiagnosticBundle):
    pass


class ReleaseReportResponse(ReleaseReport):
    pass


class FullHealthApiResponse(FullHealthResponse):
    pass
