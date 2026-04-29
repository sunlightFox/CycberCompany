from __future__ import annotations

from typing import Any

from core_types import ApiModel


class OrganizationSummary(ApiModel):
    organization_id: str
    shell_id: str
    display_name: str
    owner_user_id: str
    owner_title: str
    settings: dict[str, Any]
    created_at: str
    updated_at: str

