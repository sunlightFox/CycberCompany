from __future__ import annotations

from pathlib import Path

from app.core.config import ChannelProviderSection
from app.services.channel_connectors import FeishuMockConnector, FeishuOpenPlatformConnector
from app.services.channel_extensions import (
    ChannelExtensionDefinition,
    ChannelExtensionManifest,
    ChannelRuntimeContext,
)
from app.services.feishu_gateway import FeishuChannelGatewayService

from .common import (
    build_channel_notification_providers,
    default_channel_state_dir,
    normalize_channel_provider_variants,
)


def _normalize_configs(
    provider_configs: dict[str, ChannelProviderSection],
    data_dir: Path,
) -> dict[str, ChannelProviderSection]:
    return normalize_channel_provider_variants(
        provider_configs,
        data_dir=data_dir,
        primary_provider="feishu",
        mock_provider="feishu_mock",
    )


def _default_state_dir(data_dir: Path, provider: str) -> Path:
    return default_channel_state_dir(data_dir, provider)


def _build_connectors(
    provider_configs: dict[str, ChannelProviderSection],
    data_dir: Path,
) -> list[object]:
    configs = _normalize_configs(provider_configs, data_dir)
    return [
        FeishuOpenPlatformConnector(configs["feishu"], state_dir=configs["feishu"].state_dir or _default_state_dir(data_dir, "feishu")),
        FeishuMockConnector(configs["feishu_mock"]),
    ]


def _build_notification_providers(channels, voice_service) -> dict[str, object]:
    return build_channel_notification_providers(
        ("feishu", "feishu_mock"),
        channels,
        voice_service=voice_service,
    )


def _build_gateways(context: ChannelRuntimeContext) -> dict[str, FeishuChannelGatewayService]:
    configs = _normalize_configs(context.provider_configs, context.data_dir)
    gateway = FeishuChannelGatewayService(
        repo=context.channel_repo,
        chat_repo=context.chat_repo,
        chat_service=context.chat_service,
        notifications=context.notification_gateway_service,
        connectors=context.channel_connector_registry,
        secret_store=context.secret_store,
        data_dir=context.data_dir,
        trace_service=context.trace_service,
        audit_service=context.audit_service,
        config=configs["feishu"],
    )
    gateway.set_channel_bridges(
        session_context=context.channel_session_context,
        stream_bridge=context.channel_stream_bridge,
        approval_bridge=context.channel_approval_bridge,
    )
    gateway.set_channel_session_semantics_runtime(context.channel_session_semantics)
    gateway.set_channel_ingress_runtime(context.channel_ingress_runtime)
    return {"feishu": gateway}


FEISHU_CHANNEL_EXTENSION = ChannelExtensionDefinition(
    manifest=ChannelExtensionManifest(
        id="feishu",
        providers=("feishu", "feishu_mock"),
        route_mounts=(
            "/api/channels/inbound/feishu",
            "/api/channels/inbound/feishu/bind-callback",
            "/api/channels/providers/feishu/*",
        ),
    ),
    default_state_dir_resolver=_default_state_dir,
    config_normalizer=_normalize_configs,
    connector_factory=_build_connectors,
    notification_provider_factory=_build_notification_providers,
    gateway_factory=_build_gateways,
)
