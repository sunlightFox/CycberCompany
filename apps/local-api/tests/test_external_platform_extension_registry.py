from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from core_types import ErrorCode
from fastapi.testclient import TestClient

from app.external_platforms import (
    BUILTIN_EXTERNAL_PLATFORM_EXTENSIONS,
    register_bundled_external_platform_extensions,
)
from app.services.external_platform_extensions import ExternalPlatformExtensionRegistry


def test_external_platform_extension_registry_discovers_bundled_extensions() -> None:
    registry = ExternalPlatformExtensionRegistry()
    register_bundled_external_platform_extensions(registry)

    inventory = {item["id"]: item for item in registry.inventory()}

    assert set(inventory) == {"fake", "browser", "xiaohongshu"}
    assert inventory["xiaohongshu"]["canonical_aliases"] == ["小红书", "xhs", "rednote"]
    assert "publish_content" in inventory["xiaohongshu"]["action_markers"]
    assert registry.require_platform("fake_platform").manifest.id == "fake"
    assert registry.require_platform("social_xiaohongshu").manifest.id == "xiaohongshu"
    assert registry.require_execution_mode("browser").manifest.id == "browser"
    assert registry.require_execution_mode("mcp_adapter").manifest.id == "browser"
    assert [item.manifest.id for item in BUILTIN_EXTERNAL_PLATFORM_EXTENSIONS] == [
        "fake",
        "browser",
        "xiaohongshu",
    ]


def test_external_platform_extension_registry_rejects_unknown_entries() -> None:
    registry = ExternalPlatformExtensionRegistry()
    register_bundled_external_platform_extensions(registry)

    with pytest.raises(Exception) as platform_exc:
        registry.require_platform("unknown_platform")
    with pytest.raises(Exception) as mode_exc:
        registry.require_execution_mode("unknown_mode")

    assert getattr(platform_exc.value, "code", None) == ErrorCode.CONFIG_ERROR.value
    assert getattr(mode_exc.value, "code", None) == ErrorCode.CONFIG_ERROR.value


def test_external_platform_extension_inventory_visible_via_registry(client: TestClient) -> None:
    service_registry = cast(Any, client.app).state.registry

    providers = client.get("/api/external-platform/providers")
    assert providers.status_code == 200, providers.text
    provider_keys = {item["provider_key"] for item in providers.json()["items"]}

    assert {"fake_provider", "browser", "social_xiaohongshu"}.issubset(provider_keys)
    extension_ids = {
        item["id"] for item in service_registry.external_platform_extension_registry.inventory()
    }
    assert extension_ids == {"fake", "browser", "xiaohongshu"}
    seeded_platforms = {
        item["platform_key"]
        for item in service_registry.external_platform_extension_registry.seeded_targets()
    }
    assert {"fake_platform", "social_xiaohongshu"} <= seeded_platforms


def test_external_platform_extension_boundary_core_stops_hardcoding_platforms() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    registry_source = (root / "services" / "registry.py").read_text(encoding="utf-8")
    action_source = (root / "services" / "external_platform_actions.py").read_text(
        encoding="utf-8"
    )
    adapter_source = (root / "services" / "external_platform_adapters.py").read_text(
        encoding="utf-8"
    )

    assert "default_external_platform_provider_registry" not in registry_source
    assert "default_external_platform_provider_registry" not in action_source
    assert "FAKE_PROVIDER_TARGET" not in action_source
    assert "XIAOHONGSHU_BROWSER_TARGET" not in action_source
    assert "build_provider_registry(runtime_context)" in action_source
    assert "build_adapter_handlers(self)" in adapter_source
