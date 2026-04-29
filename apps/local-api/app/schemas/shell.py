from __future__ import annotations

from typing import Any

from core_types import ApiModel, ShellConfig, ShellSwitchPreview, ShellTemplateApplication
from pydantic import Field


class ShellListItem(ApiModel):
    shell_id: str
    display_name: str
    version: str
    is_enabled: bool
    created_at: str
    updated_at: str


class ShellListResponse(ApiModel):
    items: list[ShellListItem]


CurrentShellResponse = ShellConfig


class ShellDetailResponse(ShellConfig):
    templates: dict[str, Any] = Field(default_factory=dict)


class ShellSwitchRequest(ApiModel):
    shell_id: str
    actor_member_id: str | None = "mem_xiaoyao"


class ShellSwitchPreviewResponse(ShellSwitchPreview):
    pass


class ShellTemplateListResponse(ApiModel):
    shell_id: str
    templates: dict[str, Any] = Field(default_factory=dict)


class ShellTemplateApplyRequest(ApiModel):
    actor_member_id: str | None = "mem_xiaoyao"


class ShellTemplateApplicationResponse(ShellTemplateApplication):
    pass
