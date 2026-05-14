from __future__ import annotations

from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class ProviderCapabilityProfile(ApiModel):
    provider_ref: str
    provider_type: str = "provider"
    domain: str = "productivity"
    display_name: str
    supported_objects: list[str] = Field(default_factory=list)
    supported_actions: list[str] = Field(default_factory=list)
    risk_actions: list[str] = Field(default_factory=list)
    collaboration_features: list[str] = Field(default_factory=list)
    collaboration_modes: list[str] = Field(default_factory=list)
    authorization_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentSuiteProvider(ProviderCapabilityProfile):
    provider_type: str = "document_suite_provider"


class OfficeTaskRequest(ApiModel):
    domain: str = "productivity"
    request_type: str
    operation: str
    provider_ref: str = "local.office_suite"
    title: str | None = None
    summary: str | None = None
    content: str | None = None
    source_artifact_id: EntityId | None = None
    recipients: list[str] = Field(default_factory=list)
    attendees: list[str] = Field(default_factory=list)
    share_targets: list[str] = Field(default_factory=list)
    scheduled_time: str | None = None
    high_risk_actions: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentChangeSet(ApiModel):
    operation: str
    summary: str
    section_titles: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_artifact_id: EntityId | None = None


class SheetUpdateSummary(ApiModel):
    operation: str
    summary: str
    sheet_names: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_artifact_id: EntityId | None = None


class DeckOutline(ApiModel):
    operation: str
    summary: str
    slide_titles: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    source_artifact_id: EntityId | None = None


class MailDraft(ApiModel):
    operation: str
    subject: str
    summary: str
    recipients: list[str] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)


class CalendarAction(ApiModel):
    operation: str
    title: str
    summary: str
    attendees: list[str] = Field(default_factory=list)
    scheduled_time: str | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)


class ArtifactEvidence(ApiModel):
    evidence_type: str
    summary: str
    source: str = "productivity_domain"
    provider_ref: str
    task_id: EntityId | None = None
    trace_id: EntityId | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalState(ApiModel):
    status: str = "not_required"
    source: str = "productivity_domain"
    provider_ref: str | None = None
    task_id: EntityId | None = None
    trace_id: EntityId | None = None
    approval_id: EntityId | None = None
    requested_action: str | None = None
    risk_level: str | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class Deliverable(ApiModel):
    deliverable_type: str
    title: str
    summary: str
    provider_ref: str
    task_id: EntityId | None = None
    trace_id: EntityId | None = None
    source: str
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    approval_state: ApprovalState = Field(default_factory=ApprovalState)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FinalResult(ApiModel):
    summary: str
    source: str
    provider_ref: str
    task_id: EntityId | None = None
    trace_id: EntityId | None = None
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    approval_state: ApprovalState = Field(default_factory=ApprovalState)
    deliverable: Deliverable | None = None
    artifact_evidence: ArtifactEvidence | None = None
    next_actions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
