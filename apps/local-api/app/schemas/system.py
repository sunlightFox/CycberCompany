from __future__ import annotations

from typing import Any

from core_types import ApiModel
from pydantic import Field


class BootstrapStatus(ApiModel):
    shell_registered: bool
    organization_ready: bool
    default_brain_ready: bool
    default_member_ready: bool
    default_conversation_ready: bool
    welcome_message_ready: bool


class RuntimeContract(ApiModel):
    name: str
    status: str = "not_started"
    implemented: bool = False
    description: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    blocker_level: str = "none"


class RuntimeContractsResponse(ApiModel):
    items: list[RuntimeContract] = Field(default_factory=list)


class DesignGap(ApiModel):
    gap_id: str
    module_name: str
    current_behavior: str
    design_gap: str
    blocker_level: str
    fix_phase: str
    acceptance_tests: list[str] = Field(default_factory=list)
    status: str = "open"
    created_at: str | None = None
    updated_at: str | None = None
    risk_id: str | None = None
    why_accepted: str | None = None
    scope: str | None = None
    mitigation: list[str] = Field(default_factory=list)
    owner_phase: str | None = None
    expires_at: str | None = None
    recheck_trigger: str | None = None
    promotion_rule: str | None = None
    lifecycle_status: str | None = None


class DesignGapsResponse(ApiModel):
    items: list[DesignGap] = Field(default_factory=list)
