from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from core_types import ErrorCode

from app.core.config import ChannelProviderSection
from app.core.errors import AppError


class ChannelGatewayProtocol(Protocol):
    def runtime_diagnostic(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ChannelExtensionManifest:
    id: str
    kind: str = "channel"
    providers: tuple[str, ...] = ()
    bundled: bool = True
    source: str = "bundled"
    test_only: bool = False
    config_schema: dict[str, Any] = field(default_factory=dict)
    route_mounts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChannelRuntimeContext:
    provider_configs: dict[str, ChannelProviderSection]
    channel_repo: Any
    chat_repo: Any
    media_repo: Any | None
    chat_service: Any
    notification_gateway_service: Any
    channel_binding_service: Any
    channel_connector_registry: Any
    artifact_store: Any
    secret_store: Any
    trace_service: Any
    audit_service: Any
    session_runtime: Any
    channel_session_semantics: Any
    channel_session_context: Any
    channel_stream_bridge: Any
    channel_approval_bridge: Any
    channel_ingress_runtime: Any
    data_dir: Path
    multimodal_understanding: Any | None = None


@dataclass(frozen=True)
class ChannelExtensionDefinition:
    manifest: ChannelExtensionManifest
    default_state_dir_resolver: Callable[[Path, str], Path]
    config_normalizer: Callable[[dict[str, ChannelProviderSection], Path], dict[str, ChannelProviderSection]]
    connector_factory: Callable[[dict[str, ChannelProviderSection], Path], list[Any]]
    notification_provider_factory: Callable[[Any, Any], dict[str, Any]]
    gateway_factory: Callable[[ChannelRuntimeContext], dict[str, ChannelGatewayProtocol]]


class ChannelExtensionRegistry:
    def __init__(self) -> None:
        self._extensions: dict[str, ChannelExtensionDefinition] = {}
        self._provider_to_extension: dict[str, str] = {}

    def register(self, definition: ChannelExtensionDefinition) -> None:
        manifest = definition.manifest
        if manifest.kind != "channel":
            raise ValueError(f"Unsupported channel extension kind: {manifest.kind}")
        self._extensions[manifest.id] = definition
        for provider in manifest.providers:
            self._provider_to_extension[provider] = manifest.id

    def get(self, extension_id: str) -> ChannelExtensionDefinition:
        return self._extensions[extension_id]

    def extension_for_provider(self, provider: str) -> ChannelExtensionDefinition | None:
        extension_id = self._provider_to_extension.get(provider)
        if extension_id is None:
            return None
        return self._extensions.get(extension_id)

    def require_provider(self, provider: str) -> ChannelExtensionDefinition:
        extension = self.extension_for_provider(provider)
        if extension is None:
            raise AppError(
                ErrorCode.CONFIG_ERROR,
                f"Unknown channel provider '{provider}'",
                status_code=500,
                details={"provider": provider},
            )
        return extension

    def inventory(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for definition in self._extensions.values():
            items.append(
                {
                    "id": definition.manifest.id,
                    "kind": definition.manifest.kind,
                    "providers": list(definition.manifest.providers),
                    "bundled": definition.manifest.bundled,
                    "source": definition.manifest.source,
                    "test_only": definition.manifest.test_only,
                    "route_mounts": list(definition.manifest.route_mounts),
                }
            )
        return items

    def normalized_provider_configs(
        self,
        provider_configs: dict[str, ChannelProviderSection],
        data_dir: Path,
    ) -> dict[str, ChannelProviderSection]:
        normalized = dict(provider_configs)
        for definition in self._extensions.values():
            normalized = definition.config_normalizer(normalized, data_dir)
        return normalized

    def build_connectors(
        self,
        provider_configs: dict[str, ChannelProviderSection],
        data_dir: Path,
    ) -> list[Any]:
        connectors: list[Any] = []
        for definition in self._extensions.values():
            connectors.extend(definition.connector_factory(provider_configs, data_dir))
        return connectors

    def build_notification_providers(self, channels: Any, voice_service: Any) -> dict[str, Any]:
        providers: dict[str, Any] = {}
        for definition in self._extensions.values():
            providers.update(definition.notification_provider_factory(channels, voice_service))
        return providers

    def build_gateways(self, context: ChannelRuntimeContext) -> dict[str, ChannelGatewayProtocol]:
        gateways: dict[str, ChannelGatewayProtocol] = {}
        for definition in self._extensions.values():
            gateways.update(definition.gateway_factory(context))
        return gateways


class ChannelGatewayRegistry:
    def __init__(self) -> None:
        self._gateways: dict[str, ChannelGatewayProtocol] = {}

    def register(self, provider: str, gateway: ChannelGatewayProtocol) -> None:
        self._gateways[provider] = gateway

    def get(self, provider: str) -> ChannelGatewayProtocol | None:
        return self._gateways.get(provider)

    def require(self, provider: str) -> ChannelGatewayProtocol:
        gateway = self.get(provider)
        if gateway is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                f"Channel gateway '{provider}' is unavailable",
                status_code=404,
                details={"provider": provider},
            )
        return gateway

    def providers(self) -> list[str]:
        return sorted(self._gateways)
