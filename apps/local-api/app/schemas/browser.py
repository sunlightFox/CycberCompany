from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    BrowserEvidence,
    BrowserPageState,
    BrowserProfile,
    BrowserProfileEvent,
    BrowserSession,
    BrowserSessionHealthProbe,
    EntityId,
)
from pydantic import Field


class BrowserProfileCreateRequest(ApiModel):
    display_name: str = Field(min_length=1)
    profile_type: str = "task_isolated"
    storage_backend: str = "local_encrypted"
    sensitivity: str = "medium"
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by_member_id: EntityId | None = "mem_xiaoyao"
    expires_at: str | None = None
    execution_backend: str = "playwright_ephemeral"
    cdp_endpoint: str | None = None
    browser_family: str | None = None
    browser_profile_name: str | None = None
    identity_binding_status: str = "unbound"
    login_capture_mode: str = "manual_handoff"


class BrowserProfileUpdateRequest(ApiModel):
    display_name: str | None = Field(default=None, min_length=1)
    status: str | None = None
    sensitivity: str | None = None
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    policy: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    expires_at: str | None = None
    execution_backend: str | None = None
    cdp_endpoint: str | None = None
    browser_family: str | None = None
    browser_profile_name: str | None = None
    identity_binding_status: str | None = None
    login_capture_mode: str | None = None


class BrowserProfileActionRequest(ApiModel):
    reason: str | None = None


class BrowserSessionCreateRequest(ApiModel):
    asset_id: EntityId | None = None
    login_domain: str = Field(min_length=1)
    auth_type: str = "cookie_session"
    sensitivity: str = "high"
    session_metadata: dict[str, Any] = Field(default_factory=dict)
    secret_ref: EntityId | None = None
    created_by_member_id: EntityId | None = "mem_xiaoyao"
    expires_at: str | None = None
    reuse_policy: dict[str, Any] = Field(default_factory=dict)
    execution_backend: str = "playwright_ephemeral"
    identity_source: str | None = None
    cdp_endpoint: str | None = None
    browser_family: str | None = None
    browser_profile_name: str | None = None
    identity_binding_status: str = "unbound"
    login_capture_mode: str = "manual_handoff"


class BrowserProfileBindLocalCdpRequest(ApiModel):
    cdp_endpoint: str = Field(min_length=1)
    browser_family: str = "edge"
    browser_profile_name: str | None = None
    identity_source: str = "local_edge_cdp"


class BrowserProfileBootstrapLoginRequest(ApiModel):
    login_domain: str = Field(min_length=1)
    login_url: str = Field(min_length=1)
    asset_id: EntityId | None = None
    auth_type: str = "cookie_session"
    sensitivity: str = "high"
    session_metadata: dict[str, Any] = Field(default_factory=dict)
    created_by_member_id: EntityId | None = "mem_xiaoyao"
    expires_at: str | None = None
    reuse_policy: dict[str, Any] = Field(default_factory=dict)
    browser_family: str = "edge"
    browser_profile_name: str | None = None
    cdp_endpoint: str | None = None
    login_capture_mode: str = "manual_handoff"


class BrowserSessionHealthCheckRequest(ApiModel):
    probe_type: str = "manual"
    provider_status: str | None = None
    observed_status: str | None = None
    failure_reason: str | None = None
    recovery_hint: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class BrowserSessionLoginProbeRequest(BrowserSessionHealthCheckRequest):
    pass


class BrowserSessionRestoreContextRequest(ApiModel):
    task_id: EntityId | None = None
    member_id: EntityId | None = None
    page_key: str | None = None
    current_url: str | None = None
    requested_action: str | None = None


class BrowserSessionHealthProbeResponse(BrowserSessionHealthProbe):
    pass


class BrowserPageStateResponse(BrowserPageState):
    pass


class BrowserPageStateListResponse(ApiModel):
    items: list[BrowserPageState] = Field(default_factory=list)


class BrowserSessionHealthCheckResponse(ApiModel):
    browser_session: BrowserSession
    browser_profile: BrowserProfile
    probe: BrowserSessionHealthProbe


class BrowserSessionRestoreContextResponse(ApiModel):
    browser_session: BrowserSession
    browser_profile: BrowserProfile
    context: dict[str, Any] = Field(default_factory=dict)


class BrowserProfileResponse(BrowserProfile):
    pass


class BrowserProfileListResponse(ApiModel):
    items: list[BrowserProfile] = Field(default_factory=list)


class BrowserSessionResponse(BrowserSession):
    pass


class BrowserSessionListResponse(ApiModel):
    items: list[BrowserSession] = Field(default_factory=list)


class BrowserEvidenceResponse(BrowserEvidence):
    pass


class BrowserEvidenceListResponse(ApiModel):
    items: list[BrowserEvidence] = Field(default_factory=list)


class BrowserProfileEventListResponse(ApiModel):
    items: list[BrowserProfileEvent] = Field(default_factory=list)
