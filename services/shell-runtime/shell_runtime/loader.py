from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from core_types import MenuItem, ShellConfig, ShellConstraints

from shell_runtime.constants import (
    ASSET_CATEGORIES,
    MENU_KEYS,
    SYSTEM_MENU_KEY,
    SYSTEM_MENU_LABEL,
    SYSTEM_SECTIONS,
)


class ShellRuntimeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ShellRuntime:
    shells_dir: Path
    default_shell_id: str

    def load(self, shell_id: str | None = None) -> ShellConfig:
        target_shell_id = shell_id or self.default_shell_id
        shell_dir = self.shells_dir / target_shell_id
        if not shell_dir.exists():
            raise ShellRuntimeError(f"Shell directory not found: {shell_dir}")

        shell_data = self._read_yaml(shell_dir / "shell.yaml")
        menus_data = self._read_yaml(shell_dir / "menus.yaml")
        terms_data = self._read_yaml(shell_dir / "terms.yaml")
        departments_data = self._read_yaml(shell_dir / "departments.yaml")
        roles_data = self._read_yaml(shell_dir / "roles.yaml")
        member_templates_data = self._read_yaml(shell_dir / "member_templates.yaml")

        self._validate_shell(shell_data)
        try:
            menus = [MenuItem(**item) for item in menus_data.get("menus", [])]
        except Exception as exc:
            raise ShellRuntimeError(f"menus.yaml contains invalid menu fields: {exc}") from exc
        self._validate_menus(menus)
        self._validate_terms(terms_data)
        self._validate_templates(departments_data, roles_data, member_templates_data)

        return ShellConfig(
            shell_id=str(shell_data["id"]),
            display_name=str(shell_data["name"]),
            version=str(shell_data["version"]),
            description=str(shell_data.get("description", "")),
            default_owner_title=str(shell_data["default_owner_title"]),
            menus=menus,
            terms=dict(terms_data.get("terms", {})),
            constraints=ShellConstraints(
                system_menu_label=SYSTEM_MENU_LABEL,
                system_sections=SYSTEM_SECTIONS,
                asset_categories=ASSET_CATEGORIES,
            ),
        )

    def get_label(self, shell_id: str, key: str) -> str:
        shell = self.load(shell_id)
        if key == SYSTEM_MENU_KEY:
            return SYSTEM_MENU_LABEL
        return shell.terms.get(key, key)

    def get_menu(self, shell_id: str) -> list[MenuItem]:
        return self.load(shell_id).menus

    def read_shell_file(self, shell_id: str, filename: str) -> dict[str, Any]:
        return self._read_yaml(self.shells_dir / shell_id / filename)

    def _validate_shell(self, shell_data: dict[str, Any]) -> None:
        self._require_keys(
            shell_data,
            {"id", "name", "version", "default_owner_title"},
            "shell.yaml",
        )

    def _validate_menus(self, menus: list[MenuItem]) -> None:
        if not menus:
            raise ShellRuntimeError("menus.yaml must define at least one menu")
        keys = {menu.key for menu in menus}
        if len(keys) != len(menus):
            raise ShellRuntimeError("Shell menus contain duplicate keys")
        missing = MENU_KEYS - keys
        extra = keys - MENU_KEYS
        if missing:
            raise ShellRuntimeError(f"Shell menus missing keys: {sorted(missing)}")
        if extra:
            raise ShellRuntimeError(f"Shell menus contain unsupported keys: {sorted(extra)}")

        settings_menu = next(menu for menu in menus if menu.key == SYSTEM_MENU_KEY)
        if settings_menu.label != SYSTEM_MENU_LABEL:
            raise ShellRuntimeError("Shell config cannot rename the fixed system menu")
        if settings_menu.route != "/settings":
            raise ShellRuntimeError("Shell config cannot change the fixed system menu route")

    def _validate_terms(self, terms_data: dict[str, Any]) -> None:
        terms = terms_data.get("terms")
        if not isinstance(terms, dict):
            raise ShellRuntimeError("terms.yaml must contain a terms mapping")
        required = {
            "organization",
            "owner",
            "member",
            "department",
            "role",
            "task",
            "asset",
            "skill",
            "memory",
            "audit",
        }
        missing = required - set(terms)
        if missing:
            raise ShellRuntimeError(f"terms.yaml missing keys: {sorted(missing)}")

    def _validate_templates(
        self,
        departments_data: dict[str, Any],
        roles_data: dict[str, Any],
        member_templates_data: dict[str, Any],
    ) -> None:
        departments = departments_data.get("departments")
        roles = roles_data.get("roles")
        members = member_templates_data.get("members")
        if not isinstance(departments, list) or not departments:
            raise ShellRuntimeError("departments.yaml must define departments")
        if not isinstance(roles, list) or not roles:
            raise ShellRuntimeError("roles.yaml must define roles")
        if not isinstance(members, list) or not members:
            raise ShellRuntimeError("member_templates.yaml must define members")

        department_keys = self._validate_unique_keyed_items(
            departments,
            {"key", "display_name"},
            "departments.yaml",
        )
        role_keys = self._validate_unique_keyed_items(
            roles,
            {"key", "display_name", "default_department_key"},
            "roles.yaml",
        )
        for role in roles:
            if role["default_department_key"] not in department_keys:
                raise ShellRuntimeError(
                    f"roles.yaml references unknown department: {role['default_department_key']}"
                )

        member_keys = self._validate_unique_keyed_items(
            members,
            {"key", "name", "role", "department", "persona"},
            "member_templates.yaml",
        )
        default_members = [member for member in members if member.get("default") is True]
        if len(default_members) != 1:
            raise ShellRuntimeError("member_templates.yaml must define exactly one default member")
        if default_members[0]["key"] != "xiaoyao":
            raise ShellRuntimeError("member_templates.yaml default member must be xiaoyao")
        for member in members:
            if member["department"] not in department_keys:
                raise ShellRuntimeError(
                    f"member_templates.yaml references unknown department: {member['department']}"
                )
            if member["role"] not in role_keys:
                raise ShellRuntimeError(
                    f"member_templates.yaml references unknown role: {member['role']}"
                )
        if "xiaoyao" not in member_keys:
            raise ShellRuntimeError(
                "member_templates.yaml must include the default xiaoyao template"
            )

    def _validate_unique_keyed_items(
        self,
        items: list[Any],
        required_keys: set[str],
        filename: str,
    ) -> set[str]:
        keys: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                raise ShellRuntimeError(f"{filename} entries must be mappings")
            self._require_keys(item, required_keys, filename)
            key = str(item["key"])
            if key in keys:
                raise ShellRuntimeError(f"{filename} contains duplicate key: {key}")
            keys.add(key)
        return keys

    def _require_keys(self, data: dict[str, Any], keys: set[str], filename: str) -> None:
        missing = keys - set(data)
        if missing:
            raise ShellRuntimeError(f"{filename} missing keys: {sorted(missing)}")

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise ShellRuntimeError(f"Required shell file not found: {path}")
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise ShellRuntimeError(f"Shell file must contain a YAML mapping: {path}")
        return data
