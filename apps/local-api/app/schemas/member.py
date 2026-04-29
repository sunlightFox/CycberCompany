from __future__ import annotations

from core_types import ApiModel, MemberStatus


class MemberListItem(ApiModel):
    member_id: str
    organization_id: str
    department_id: str | None = None
    role_id: str | None = None
    display_name: str
    avatar_uri: str | None = None
    status: MemberStatus
    default_brain_id: str | None = None
    persona_profile_id: str
    created_from_shell_id: str | None = None
    created_from_template_id: str | None = None
    created_at: str
    updated_at: str


class MemberListResponse(ApiModel):
    items: list[MemberListItem]


class MemberDefaultBrainUpdateRequest(ApiModel):
    brain_id: str


class MemberDefaultBrainUpdateResponse(ApiModel):
    member_id: str
    default_brain_id: str
    updated_at: str
