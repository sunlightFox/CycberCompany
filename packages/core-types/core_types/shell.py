from __future__ import annotations

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import AssetCategory


class MenuItem(ApiModel):
    key: str
    label: str
    route: str
    icon: str
    fixed: bool = False


class AssetCategoryItem(ApiModel):
    key: AssetCategory
    label: str


class ShellConstraints(ApiModel):
    system_menu_label: str = "系统管理"
    system_sections: list[MenuItem] = Field(default_factory=list)
    asset_categories: list[AssetCategoryItem] = Field(default_factory=list)


class ShellConfig(ApiModel):
    shell_id: EntityId
    display_name: str
    version: str
    description: str
    default_owner_title: str
    menus: list[MenuItem]
    terms: dict[str, str]
    constraints: ShellConstraints

