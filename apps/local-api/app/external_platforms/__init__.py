from __future__ import annotations

from app.external_platforms.browser_extension import BROWSER_EXTERNAL_PLATFORM_EXTENSION
from app.external_platforms.fake_extension import FAKE_EXTERNAL_PLATFORM_EXTENSION
from app.external_platforms.xiaohongshu_extension import XIAOHONGSHU_EXTERNAL_PLATFORM_EXTENSION
from app.services.external_platform_extensions import (
    ExternalPlatformExtensionDefinition,
    ExternalPlatformExtensionRegistry,
)

BUILTIN_EXTERNAL_PLATFORM_EXTENSIONS: tuple[ExternalPlatformExtensionDefinition, ...] = (
    FAKE_EXTERNAL_PLATFORM_EXTENSION,
    BROWSER_EXTERNAL_PLATFORM_EXTENSION,
    XIAOHONGSHU_EXTERNAL_PLATFORM_EXTENSION,
)


def register_bundled_external_platform_extensions(
    registry: ExternalPlatformExtensionRegistry,
) -> None:
    for definition in BUILTIN_EXTERNAL_PLATFORM_EXTENSIONS:
        registry.register(definition)


__all__ = [
    "BUILTIN_EXTERNAL_PLATFORM_EXTENSIONS",
    "register_bundled_external_platform_extensions",
]
