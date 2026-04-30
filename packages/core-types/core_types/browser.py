from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class BrowserProfile(ApiModel):
    browser_profile_id: EntityId
    organization_id: EntityId
    display_name: str
    profile_type: str = "task_isolated"
    storage_backend: str = "local_encrypted"
    status: str
    sensitivity: str = "medium"
    allowed_domains: list[str] = Field(default_factory=list)
    blocked_domains: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by_member_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None
    cleared_at: datetime | None = None
    expires_at: datetime | None = None


class BrowserSession(ApiModel):
    browser_session_id: EntityId
    organization_id: EntityId
    browser_profile_id: EntityId
    asset_id: EntityId | None = None
    login_domain: str
    auth_type: str = "cookie_session"
    status: str
    sensitivity: str = "high"
    session_metadata: dict[str, Any] = Field(default_factory=dict)
    secret_ref: EntityId | None = None
    created_by_member_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class BrowserProfileEvent(ApiModel):
    event_id: EntityId
    organization_id: EntityId
    browser_profile_id: EntityId
    browser_session_id: EntityId | None = None
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime


class BrowserEvidence(ApiModel):
    browser_evidence_id: EntityId
    organization_id: EntityId
    task_id: EntityId | None = None
    tool_call_id: EntityId | None = None
    browser_profile_id: EntityId | None = None
    browser_session_id: EntityId | None = None
    action: str
    action_status: str
    url: str | None = None
    title: str | None = None
    http_status: int | None = None
    evidence_summary: str
    snapshot_preview: str | None = None
    screenshot_artifact_id: EntityId | None = None
    download_artifact_id: EntityId | None = None
    artifact_ids: list[EntityId] = Field(default_factory=list)
    network_summary: dict[str, Any] = Field(default_factory=dict)
    console_summary: dict[str, Any] = Field(default_factory=dict)
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    safety_decision: dict[str, Any] = Field(default_factory=dict)
    untrusted_external_content: bool = True
    trace_id: EntityId | None = None
    created_at: datetime


class BrowserNetworkEvent(ApiModel):
    network_event_id: EntityId
    browser_evidence_id: EntityId
    organization_id: EntityId
    request_url: str
    method: str = "GET"
    status_code: int | None = None
    resource_type: str | None = None
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class BrowserConsoleEvent(ApiModel):
    console_event_id: EntityId
    browser_evidence_id: EntityId
    organization_id: EntityId
    level: str
    message_preview: str
    redaction_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
