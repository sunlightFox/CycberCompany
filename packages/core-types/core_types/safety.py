from __future__ import annotations

from typing import Any

from pydantic import Field

from core_types.common import ApiModel
from core_types.enums import ApprovalStatus, RiskLevel


class SafetyDecision(ApiModel):
    safety_decision_id: str | None = None
    decision: str = "allow"
    allowed: bool
    risk_level: RiskLevel
    approval_required: bool
    reason: str
    checks: list[str] = Field(default_factory=list)
    redactions: list[str] = Field(default_factory=list)
    required_controls: list[str] = Field(default_factory=list)
    policy_sources: list[str] = Field(default_factory=list)
    trace_refs: list[dict[str, Any]] = Field(default_factory=list)
    payload_summary: dict[str, Any] = Field(default_factory=dict)


class ApprovalSummary(ApiModel):
    approval_id: str
    requested_action: str
    risk_level: RiskLevel
    summary: str
    status: ApprovalStatus
