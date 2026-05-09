from __future__ import annotations

from typing import Any

from core_types import ApiModel
from pydantic import Field


class ModelRouteResolution(ApiModel):
    route_status: str
    brain_id: str | None = None
    failure_code: str | None = None
    retryable: bool = False
    degrade_allowed: bool = False
    privacy_level: str | None = None
    available_brain_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class ModelInvocationFailure(ApiModel):
    code: str
    message: str
    retryable: bool = False
    category: str = "model_invocation"
    metadata: dict[str, Any] = Field(default_factory=dict)


class BrowserWorkflowResult(ApiModel):
    status: str
    visible_summary: str
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    approval_state: dict[str, Any] = Field(default_factory=dict)
    failure_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityBoundaryResult(ApiModel):
    status: str
    capability_namespace: str
    executed: bool = False
    safe_fallbacks: list[str] = Field(default_factory=list)
    failure_code: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)
