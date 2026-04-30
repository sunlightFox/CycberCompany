from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    BrowserEvidence,
    BrowserProfile,
    BrowserProfileEvent,
    BrowserSession,
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


class BrowserProfileUpdateRequest(ApiModel):
    display_name: str | None = Field(default=None, min_length=1)
    status: str | None = None
    sensitivity: str | None = None
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    policy: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    expires_at: str | None = None


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
