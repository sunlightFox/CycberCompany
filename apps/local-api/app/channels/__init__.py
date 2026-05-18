from __future__ import annotations

from app.channels.feishu_extension import FEISHU_CHANNEL_EXTENSION
from app.channels.wechat_extension import WECHAT_CHANNEL_EXTENSION
from app.services.channel_extensions import (
    ChannelExtensionDefinition,
    ChannelExtensionRegistry,
)

BUILTIN_CHANNEL_EXTENSIONS: tuple[ChannelExtensionDefinition, ...] = (
    WECHAT_CHANNEL_EXTENSION,
    FEISHU_CHANNEL_EXTENSION,
)


def register_bundled_channel_extensions(registry: ChannelExtensionRegistry) -> None:
    for definition in BUILTIN_CHANNEL_EXTENSIONS:
        registry.register(definition)


__all__ = [
    "BUILTIN_CHANNEL_EXTENSIONS",
    "register_bundled_channel_extensions",
]
