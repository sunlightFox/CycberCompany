from __future__ import annotations

from pathlib import Path

from app.core.config import ChannelProviderSection
from app.services.channel_connectors import WechatClawbotConnector, WechatMockConnector
from app.services.channel_extensions import (
    ChannelExtensionDefinition,
    ChannelExtensionManifest,
    ChannelRuntimeContext,
)
from app.services.wechat_gateway import WechatChannelGatewayService

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
        primary_provider="wechat",
        mock_provider="wechat_mock",
    )


def _default_state_dir(data_dir: Path, provider: str) -> Path:
    return default_channel_state_dir(data_dir, provider)


def _build_connectors(
    provider_configs: dict[str, ChannelProviderSection],
    data_dir: Path,
) -> list[object]:
    configs = _normalize_configs(provider_configs, data_dir)
    return [
        WechatClawbotConnector(configs["wechat"], state_dir=configs["wechat"].state_dir or _default_state_dir(data_dir, "wechat")),
        WechatMockConnector(configs["wechat_mock"]),
    ]


def _build_notification_providers(channels, voice_service) -> dict[str, object]:
    return build_channel_notification_providers(
        ("wechat", "wechat_mock"),
        channels,
        voice_service=voice_service,
    )


def _build_gateways(context: ChannelRuntimeContext) -> dict[str, WechatChannelGatewayService]:
    configs = _normalize_configs(context.provider_configs, context.data_dir)
    gateway = WechatChannelGatewayService(
        repo=context.channel_repo,
        chat_repo=context.chat_repo,
        chat_service=context.chat_service,
        notifications=context.notification_gateway_service,
        connectors=context.channel_connector_registry,
        artifact_store=context.artifact_store,
        secret_store=context.secret_store,
        media_repo=context.media_repo,
        data_dir=context.data_dir,
        trace_service=context.trace_service,
        audit_service=context.audit_service,
        config=configs["wechat"],
        multimodal_understanding=context.multimodal_understanding,
    )
    gateway.set_channel_bridges(
        session_context=context.channel_session_context,
        stream_bridge=context.channel_stream_bridge,
        approval_bridge=context.channel_approval_bridge,
    )
    gateway.set_channel_session_semantics_runtime(context.channel_session_semantics)
    gateway.set_channel_ingress_runtime(context.channel_ingress_runtime)
    return {"wechat": gateway}


WECHAT_CHANNEL_EXTENSION = ChannelExtensionDefinition(
    manifest=ChannelExtensionManifest(
        id="wechat",
        providers=("wechat", "wechat_mock"),
        route_mounts=(
            "/api/channels/inbound/wechat",
            "/api/channels/providers/wechat/*",
            "/api/channels/pairing-requests/*",
            "/api/channels/peers/*",
        ),
    ),
    default_state_dir_resolver=_default_state_dir,
    config_normalizer=_normalize_configs,
    connector_factory=_build_connectors,
    notification_provider_factory=_build_notification_providers,
    gateway_factory=_build_gateways,
)
