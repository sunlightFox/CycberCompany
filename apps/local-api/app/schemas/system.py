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


class RuntimeTopologyComponent(ApiModel):
    name: str
    runtime: str
    dependencies: list[str] = Field(default_factory=list)
    status: str = "implemented_with_fallback"
    details: dict[str, Any] = Field(default_factory=dict)


class RuntimeTopologyResponse(ApiModel):
    items: list[RuntimeTopologyComponent] = Field(default_factory=list)


class PhaseReadinessItem(ApiModel):
    status: str = "blocked"
    source_of_truth: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    next_owner_module: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ChatMainlineReadinessResponse(ApiModel):
    phase76_control_plane_version: str
    mainline_path_declared: list[str] = Field(default_factory=list)
    phase_readiness: dict[str, PhaseReadinessItem] = Field(default_factory=dict)
    blocking_gaps: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    runtime_facts: dict[str, Any] = Field(default_factory=dict)


class SessionRuntimeResponse(ApiModel):
    runtime: str
    executor: str
    ingress: str
    route_source: str | None = None
    delegates_to: str | None = None
    maturity: str | None = None
    ownership_mode: str | None = None
    state_machine_owner: str | None = None
    event_source: str | None = None
    business_logic_owner: str | None = None
    public_entrypoints: list[str] = Field(default_factory=list)
    route_selectors: list[str] = Field(default_factory=list)
    running_turn_count: int = 0


class ToolRuntimeResponse(ApiModel):
    runtime: str
    dispatcher: str
    safety_bridge: str
    builtin: dict[str, Any] = Field(default_factory=dict)
    browser: dict[str, Any] = Field(default_factory=dict)
    asset: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)
    terminal: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)
