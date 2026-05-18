from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from app.channels.common import normalize_channel_provider_variants
from app.schemas.channels import FeishuGatewayPollResponse, WechatGatewayPollResponse
from app.core.config import ChannelProviderSection
from app.services.channel_extensions import ChannelExtensionRegistry
from app.services.channel_gateway_router import ChannelGatewayRouter
from core_types import ErrorCode
from fastapi.testclient import TestClient

from app.channels import BUILTIN_CHANNEL_EXTENSIONS, register_bundled_channel_extensions
from app.channels.feishu_extension import FEISHU_CHANNEL_EXTENSION
from app.channels.wechat_extension import WECHAT_CHANNEL_EXTENSION


def test_channel_extension_registry_discovers_bundled_wechat_and_feishu() -> None:
    registry = ChannelExtensionRegistry()
    registry.register(WECHAT_CHANNEL_EXTENSION)
    registry.register(FEISHU_CHANNEL_EXTENSION)

    inventory = {item["id"]: item for item in registry.inventory()}

    assert set(inventory) == {"wechat", "feishu"}
    assert inventory["wechat"]["providers"] == ["wechat", "wechat_mock"]
    assert inventory["feishu"]["providers"] == ["feishu", "feishu_mock"]
    assert registry.require_provider("wechat").manifest.id == "wechat"
    assert registry.require_provider("feishu").manifest.id == "feishu"


def test_register_bundled_channel_extensions_uses_inventory_entrypoint() -> None:
    registry = ChannelExtensionRegistry()

    register_bundled_channel_extensions(registry)

    assert {item["id"] for item in registry.inventory()} == {"wechat", "feishu"}
    assert [definition.manifest.id for definition in BUILTIN_CHANNEL_EXTENSIONS] == [
        "wechat",
        "feishu",
    ]


def test_channel_extension_registry_rejects_unknown_provider() -> None:
    registry = ChannelExtensionRegistry()
    registry.register(WECHAT_CHANNEL_EXTENSION)

    with pytest.raises(Exception) as exc_info:
        registry.require_provider("unknown_provider")

    assert getattr(exc_info.value, "code", None) == ErrorCode.CONFIG_ERROR.value


def test_channel_common_normalizer_applies_default_state_dirs() -> None:
    data_dir = Path("C:/tmp/channel-ext-test")

    normalized = normalize_channel_provider_variants(
        {"wechat": ChannelProviderSection(enabled=True)},
        data_dir=data_dir,
        primary_provider="wechat",
        mock_provider="wechat_mock",
    )

    assert normalized["wechat"].state_dir == data_dir / "channel-providers" / "wechat"
    assert normalized["wechat_mock"].state_dir == data_dir / "channel-providers" / "wechat_mock"
    assert normalized["wechat_mock"].test_only is True


def test_channel_routes_dispatch_via_channel_gateway_registry(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    router = ChannelGatewayRouter(registry)
    original_gateway = registry.channel_gateway_registry.require("wechat")

    class _BrokenGateway:
        async def poll_once(self, **_: Any) -> WechatGatewayPollResponse:
            raise AssertionError("route should not use compatibility field directly")

    class _FakeGateway:
        def runtime_diagnostic(self) -> dict[str, Any]:
            return {"runtime": "fake_wechat_gateway"}

        async def poll_once(self, **_: Any) -> WechatGatewayPollResponse:
            return WechatGatewayPollResponse(status="healthy", processed_events=7)

    registry.wechat_gateway_service = _BrokenGateway()
    registry.channel_gateway_registry.register("wechat", _FakeGateway())
    try:
        response = client.post("/api/channels/providers/wechat/poll-once")
        assert response.status_code == 200, response.text
        assert response.json()["processed_events"] == 7
        assert router.for_provider("wechat").runtime_diagnostic()["runtime"] == "fake_wechat_gateway"
    finally:
        registry.wechat_gateway_service = original_gateway
        registry.channel_gateway_registry.register("wechat", original_gateway)


def test_channel_extension_boundary_core_stops_importing_concrete_channel_classes() -> None:
    root = Path(__file__).resolve().parents[1] / "app"
    registry_source = (root / "services" / "registry.py").read_text(encoding="utf-8")
    route_source = (root / "api" / "routes_channels.py").read_text(encoding="utf-8")

    for forbidden in (
        "WechatClawbotConnector",
        "WechatMockConnector",
        "FeishuOpenPlatformConnector",
        "FeishuMockConnector",
        "WechatChannelGatewayService",
        "FeishuChannelGatewayService",
    ):
        assert forbidden not in registry_source
    assert ".wechat_gateway_service" not in route_source
    assert ".feishu_gateway_service" not in route_source
