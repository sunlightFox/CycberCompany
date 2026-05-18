from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from core_types import ErrorCode

from app.core.errors import AppError
from app.services.external_platform_providers import (
    ExternalPlatformProvider,
    ExternalPlatformProviderRegistry,
    ProviderInfo,
)


class ExternalPlatformAdapterHandlerProtocol(Protocol):
    async def compile_plan(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any: ...

    async def execute_adapter(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any: ...

    async def discover_adapter(
        self,
        plan_id: str,
        *,
        trace_id: str | None = None,
    ) -> Any: ...

    async def resume_after_human(
        self,
        plan_id: str,
        request: Any | None = None,
        *,
        trace_id: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class ExternalPlatformExtensionManifest:
    id: str
    kind: str = "external_platform"
    platform_keys: tuple[str, ...] = ()
    execution_modes: tuple[str, ...] = ()
    seeded_targets: tuple[dict[str, Any], ...] = ()
    display_aliases: tuple[str, ...] = ()
    canonical_aliases: tuple[str, ...] = ()
    action_markers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    content_markers: tuple[str, ...] = ()
    generic_platform_markers: tuple[str, ...] = ()
    bundled: bool = True
    source: str = "bundled"
    test_only: bool = False


@dataclass(frozen=True)
class ExternalPlatformRuntimeContext:
    external_platform_repo: Any
    external_platform_adapter_repo: Any
    asset_repo: Any
    asset_service: Any
    asset_broker: Any
    capability_service: Any
    browser_session_service: Any
    artifact_store: Any
    task_engine: Any
    approval_service: Any
    tool_runtime: Any
    trace_service: Any
    audit_service: Any
    safety_policy_service: Any | None = None
    skill_plugin_service: Any | None = None
    skill_governance_service: Any | None = None
    skill_repository_service: Any | None = None


@dataclass(frozen=True)
class ExternalPlatformExtensionDefinition:
    manifest: ExternalPlatformExtensionManifest
    provider_factory: Callable[[ExternalPlatformRuntimeContext], list[ExternalPlatformProvider]]
    adapter_handler_factory: Callable[[Any], ExternalPlatformAdapterHandlerProtocol] | None = None


class ExternalPlatformExtensionRegistry:
    def __init__(self) -> None:
        self._extensions: dict[str, ExternalPlatformExtensionDefinition] = {}
        self._platform_to_extension: dict[str, str] = {}
        self._execution_mode_to_extension: dict[str, str] = {}

    def register(self, definition: ExternalPlatformExtensionDefinition) -> None:
        manifest = definition.manifest
        if manifest.kind != "external_platform":
            raise ValueError(f"Unsupported external platform extension kind: {manifest.kind}")
        self._extensions[manifest.id] = definition
        for key in manifest.platform_keys:
            self._platform_to_extension[key] = manifest.id
        for mode in manifest.execution_modes:
            self._execution_mode_to_extension[mode] = manifest.id

    def get(self, extension_id: str) -> ExternalPlatformExtensionDefinition:
        return self._extensions[extension_id]

    def extension_for_platform(self, platform_key: str) -> ExternalPlatformExtensionDefinition | None:
        extension_id = self._platform_to_extension.get(platform_key)
        if extension_id is None:
            return None
        return self._extensions.get(extension_id)

    def extension_for_execution_mode(
        self,
        execution_mode: str,
    ) -> ExternalPlatformExtensionDefinition | None:
        extension_id = self._execution_mode_to_extension.get(execution_mode)
        if extension_id is None:
            return None
        return self._extensions.get(extension_id)

    def require_platform(self, platform_key: str) -> ExternalPlatformExtensionDefinition:
        extension = self.extension_for_platform(platform_key)
        if extension is None:
            raise AppError(
                ErrorCode.CONFIG_ERROR,
                f"Unknown external platform '{platform_key}'",
                status_code=500,
                details={"platform_key": platform_key},
            )
        return extension

    def require_execution_mode(self, execution_mode: str) -> ExternalPlatformExtensionDefinition:
        extension = self.extension_for_execution_mode(execution_mode)
        if extension is None:
            raise AppError(
                ErrorCode.CONFIG_ERROR,
                f"Unknown external platform execution mode '{execution_mode}'",
                status_code=500,
                details={"execution_mode": execution_mode},
            )
        return extension

    def inventory(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for definition in self._extensions.values():
            items.append(
                {
                    "id": definition.manifest.id,
                    "kind": definition.manifest.kind,
                    "platform_keys": list(definition.manifest.platform_keys),
                    "execution_modes": list(definition.manifest.execution_modes),
                    "seeded_targets": [dict(item) for item in definition.manifest.seeded_targets],
                    "display_aliases": list(definition.manifest.display_aliases),
                    "canonical_aliases": list(definition.manifest.canonical_aliases),
                    "action_markers": {
                        key: list(values)
                        for key, values in definition.manifest.action_markers.items()
                    },
                    "content_markers": list(definition.manifest.content_markers),
                    "generic_platform_markers": list(
                        definition.manifest.generic_platform_markers
                    ),
                    "bundled": definition.manifest.bundled,
                    "source": definition.manifest.source,
                    "test_only": definition.manifest.test_only,
                }
            )
        return items

    def seeded_targets(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for definition in self._extensions.values():
            items.extend(dict(item) for item in definition.manifest.seeded_targets)
        return items

    def build_provider_registry(
        self,
        context: ExternalPlatformRuntimeContext,
    ) -> ExternalPlatformProviderRegistry:
        registry = ExternalPlatformProviderRegistry()
        for definition in self._extensions.values():
            for provider in definition.provider_factory(context):
                registry.register(provider)
        return registry

    def build_adapter_handlers(
        self,
        service: Any,
    ) -> dict[str, ExternalPlatformAdapterHandlerProtocol]:
        handlers: dict[str, ExternalPlatformAdapterHandlerProtocol] = {}
        for definition in self._extensions.values():
            if definition.adapter_handler_factory is None:
                continue
            handler = definition.adapter_handler_factory(service)
            for mode in definition.manifest.execution_modes:
                handlers[mode] = handler
        return handlers

    def list_provider_info(
        self,
        context: ExternalPlatformRuntimeContext,
    ) -> list[ProviderInfo]:
        return self.build_provider_registry(context).list()
