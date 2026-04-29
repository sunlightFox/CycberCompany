from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from shell_runtime import ShellRuntime, ShellRuntimeError

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_shell_001_company_shell_loads() -> None:
    shell = ShellRuntime(ROOT_DIR / "shells", "company").load()

    assert shell.shell_id == "company"
    assert shell.display_name == "一人公司"
    assert any(item.key == "chat" for item in shell.menus)
    assert shell.terms["member"] == "员工"


def test_shell_002_system_management_is_fixed() -> None:
    shell = ShellRuntime(ROOT_DIR / "shells", "company").load()
    settings_menu = next(item for item in shell.menus if item.key == "settings")

    assert settings_menu.label == "系统管理"
    assert settings_menu.route == "/settings"
    assert [item.label for item in shell.constraints.system_sections] == [
        "模型设置",
        "技能包",
        "MCP 服务",
        "插件",
        "记忆设置",
        "安全策略",
        "日志与审计",
        "备份恢复",
        "壳与外观",
        "开发者设置",
    ]


def test_shell_003_asset_categories_are_fixed() -> None:
    shell = ShellRuntime(ROOT_DIR / "shells", "company").load()

    assert [item.label for item in shell.constraints.asset_categories] == [
        "大脑",
        "账号",
        "钱包",
        "硬件",
        "知识库",
    ]


def test_shell_004_invalid_menu_key_is_rejected(tmp_path: Path) -> None:
    shell_dir = _copy_company_shell(tmp_path)
    menus_path = shell_dir / "menus.yaml"
    data = yaml.safe_load(menus_path.read_text(encoding="utf-8"))
    data["menus"][0]["key"] = "unsupported"
    menus_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ShellRuntimeError):
        ShellRuntime(tmp_path, "company").load()


def test_shell_005_template_references_are_validated(tmp_path: Path) -> None:
    shell_dir = _copy_company_shell(tmp_path)
    templates_path = shell_dir / "member_templates.yaml"
    data = yaml.safe_load(templates_path.read_text(encoding="utf-8"))
    data["members"][0]["role"] = "missing_role"
    templates_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ShellRuntimeError):
        ShellRuntime(tmp_path, "company").load()


def _copy_company_shell(tmp_path: Path) -> Path:
    source_dir = ROOT_DIR / "shells" / "company"
    target_dir = tmp_path / "company"
    target_dir.mkdir()
    for source in source_dir.glob("*.yaml"):
        (target_dir / source.name).write_text(
            source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    return target_dir
