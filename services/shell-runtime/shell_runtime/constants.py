from __future__ import annotations

from core_types import AssetCategory, AssetCategoryItem, MenuItem

MENU_KEYS = {"chat", "members", "organization", "assets", "tasks", "settings"}

SYSTEM_MENU_KEY = "settings"
SYSTEM_MENU_LABEL = "系统管理"

SYSTEM_SECTIONS = [
    MenuItem(key="models", label="模型设置", route="/settings/models", icon="brain", fixed=True),
    MenuItem(key="skills", label="技能包", route="/settings/skills", icon="sparkles", fixed=True),
    MenuItem(key="mcp", label="MCP 服务", route="/settings/mcp", icon="plug", fixed=True),
    MenuItem(key="plugins", label="插件", route="/settings/plugins", icon="package", fixed=True),
    MenuItem(key="memory", label="记忆设置", route="/settings/memory", icon="archive", fixed=True),
    MenuItem(key="safety", label="安全策略", route="/settings/safety", icon="shield", fixed=True),
    MenuItem(key="logs", label="日志与审计", route="/settings/logs", icon="file-text", fixed=True),
    MenuItem(
        key="backup",
        label="备份恢复",
        route="/settings/backup",
        icon="hard-drive",
        fixed=True,
    ),
    MenuItem(key="shell", label="壳与外观", route="/settings/shell", icon="palette", fixed=True),
    MenuItem(
        key="developer",
        label="开发者设置",
        route="/settings/developer",
        icon="terminal",
        fixed=True,
    ),
]

ASSET_CATEGORIES = [
    AssetCategoryItem(key=AssetCategory.BRAIN, label="大脑"),
    AssetCategoryItem(key=AssetCategory.ACCOUNT, label="账号"),
    AssetCategoryItem(key=AssetCategory.WALLET, label="钱包"),
    AssetCategoryItem(key=AssetCategory.HARDWARE, label="硬件"),
    AssetCategoryItem(key=AssetCategory.KNOWLEDGE_BASE, label="知识库"),
]
