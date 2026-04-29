from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import (
    BackupJobStatus,
    BenchmarkRunStatus,
    DiagnosticBundleStatus,
    EvalRunStatus,
    EvidenceType,
    FindingSeverity,
    FindingStatus,
    IntegrityCheckType,
    ReleaseDecision,
    ReleaseGateStatus,
    RestoreJobStatus,
    SecurityAuditStatus,
)


class ReleaseGate(ApiModel):
    release_gate_id: EntityId
    organization_id: EntityId
    status: ReleaseGateStatus
    scope: dict[str, Any] = Field(default_factory=dict)
    required_checks: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    blocker_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    created_by_member_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ReleaseEvidence(ApiModel):
    evidence_id: EntityId
    release_gate_id: EntityId
    evidence_type: EvidenceType | str
    source_type: str
    source_id: EntityId
    checksum: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: datetime


class ReleaseFinding(ApiModel):
    finding_id: EntityId
    release_gate_id: EntityId
    severity: FindingSeverity
    category: str
    title: str
    description: str
    affected_module: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    status: FindingStatus
    owner: str | None = None
    accepted_reason: str | None = None
    accepted_until: datetime | None = None
    verification_run_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class EvalSuite(ApiModel):
    suite_id: EntityId
    name: str
    category: str
    description: str | None = None
    required: bool = True
    threshold: dict[str, Any] = Field(default_factory=dict)
    status: str
    created_at: datetime
    updated_at: datetime


class EvalCase(ApiModel):
    case_id: EntityId
    suite_id: EntityId
    case_key: str
    title: str
    input: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime


class EvalRun(ApiModel):
    eval_run_id: EntityId
    release_gate_id: EntityId | None = None
    suite_id: EntityId | None = None
    status: EvalRunStatus
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None
    trace_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class EvalResult(ApiModel):
    eval_result_id: EntityId
    eval_run_id: EntityId
    suite_id: EntityId
    case_id: EntityId | None = None
    case_key: str
    status: str
    score: float = 0
    expected: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    assertion_summary: str | None = None
    finding_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime


class RedTeamScenario(ApiModel):
    scenario_id: EntityId
    category: str
    title: str
    attack_input: dict[str, Any] = Field(default_factory=dict)
    expected_block: dict[str, Any] = Field(default_factory=dict)
    severity_if_failed: FindingSeverity
    tags: list[str] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime


class SecurityAuditRun(ApiModel):
    audit_run_id: EntityId
    release_gate_id: EntityId | None = None
    status: SecurityAuditStatus
    total_scenarios: int = 0
    passed_scenarios: int = 0
    failed_scenarios: int = 0
    critical_failures: int = 0
    high_failures: int = 0
    result: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class IntegrityCheckRun(ApiModel):
    integrity_run_id: EntityId
    release_gate_id: EntityId | None = None
    check_type: IntegrityCheckType
    status: str
    checked_count: int = 0
    failed_count: int = 0
    threshold: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class BackupJob(ApiModel):
    backup_job_id: EntityId
    organization_id: EntityId
    status: BackupJobStatus
    scope: dict[str, Any] = Field(default_factory=dict)
    output_uri: str | None = None
    manifest: dict[str, Any] = Field(default_factory=dict)
    checksum: str | None = None
    size_bytes: int | None = None
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class RestoreJob(ApiModel):
    restore_job_id: EntityId
    organization_id: EntityId
    backup_job_id: EntityId | None = None
    status: RestoreJobStatus
    input_uri: str
    restore_plan: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    checksum_verified: bool = False
    error_code: str | None = None
    error_summary: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class BenchmarkRun(ApiModel):
    benchmark_run_id: EntityId
    release_gate_id: EntityId | None = None
    benchmark_type: str
    status: BenchmarkRunStatus
    scenario: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    resource_summary: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class DiagnosticBundle(ApiModel):
    bundle_id: EntityId
    organization_id: EntityId
    scope: dict[str, Any] = Field(default_factory=dict)
    redaction_policy: dict[str, Any] = Field(default_factory=dict)
    output_uri: str | None = None
    checksum: str | None = None
    size_bytes: int | None = None
    status: DiagnosticBundleStatus
    created_by_member_id: EntityId | None = None
    created_at: datetime
    completed_at: datetime | None = None


class ReleaseReport(ApiModel):
    report_id: EntityId
    release_gate_id: EntityId
    organization_id: EntityId
    decision: ReleaseDecision
    summary: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    findings_summary: dict[str, Any] = Field(default_factory=dict)
    output_uri: str | None = None
    checksum: str | None = None
    created_at: datetime


class FullHealthResponse(ApiModel):
    status: str
    db: str
    migrations: dict[str, Any] = Field(default_factory=dict)
    trace: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    backup: dict[str, Any] = Field(default_factory=dict)
    tasks: dict[str, Any] = Field(default_factory=dict)
    memory_jobs: dict[str, Any] = Field(default_factory=dict)
    release_gate_readiness: dict[str, Any] = Field(default_factory=dict)
    default_shell: str
    version: str
    trace_id: EntityId | None = None
