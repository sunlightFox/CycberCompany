# 渠道扩展接入说明

本目录承载 bundled channel extensions。核心层不直接实例化具体平台实现，而是只依赖统一的渠道扩展契约。

## 当前结构

- `wechat_extension.py`
  - providers: `wechat`, `wechat_mock`
- `feishu_extension.py`
  - providers: `feishu`, `feishu_mock`
- `common.py`
  - 渠道通知 provider、默认 `state_dir`、provider variant 归一化等共享适配
- `__init__.py`
  - bundled 扩展清单与统一注册入口

## 一个渠道扩展需要提供什么

每个扩展模块都要导出一个 `ChannelExtensionDefinition`，至少包含：

- `manifest`
  - `id`
  - `providers`
  - `route_mounts`
- `default_state_dir_resolver`
- `config_normalizer`
- `connector_factory`
- `notification_provider_factory`
- `gateway_factory`

## 推荐实现顺序

1. 定义 provider 变体
   - 例如正式渠道和 mock 渠道
2. 在扩展内归一化配置
   - 处理默认 `state_dir`
   - 处理 test-only provider 默认启用策略
   - 优先复用 `common.py` 的共享 helper
3. 创建 connector factory
   - 返回该扩展负责的全部 connector
4. 创建 notification provider factory
   - 返回 `provider -> notification provider` 映射
5. 创建 gateway factory
   - 用 `ChannelRuntimeContext` 注入核心运行时能力
   - 设置 session / stream / approval / ingress bridges
6. 在 `app/channels/__init__.py` 的 `BUILTIN_CHANNEL_EXTENSIONS` 中注册

## 边界要求

- 不在 `app/services/registry.py` 中直接构造平台 connector 或 gateway
- 不在路由层按平台类型 import 具体 gateway service
- mock provider 作为同一扩展下的 provider variant 处理，不回流到核心分支
- 平台专属 inbound / callback / pairing 逻辑可以留在扩展内部，但只能通过标准 gateway 暴露给核心

## 最小模板

```python
from pathlib import Path

from app.core.config import ChannelProviderSection
from app.channels.common import (
    build_channel_notification_providers,
    default_channel_state_dir,
    normalize_channel_provider_variants,
)
from app.services.channel_extensions import (
    ChannelExtensionDefinition,
    ChannelExtensionManifest,
    ChannelRuntimeContext,
)


def _default_state_dir(data_dir: Path, provider: str) -> Path:
    return default_channel_state_dir(data_dir, provider)


def _normalize_configs(
    provider_configs: dict[str, ChannelProviderSection],
    data_dir: Path,
) -> dict[str, ChannelProviderSection]:
    return normalize_channel_provider_variants(
        provider_configs,
        data_dir=data_dir,
        primary_provider="my_channel",
        mock_provider="my_channel_mock",
    )


def _build_connectors(
    provider_configs: dict[str, ChannelProviderSection],
    data_dir: Path,
) -> list[object]:
    ...


def _build_notification_providers(channels, voice_service) -> dict[str, object]:
    return build_channel_notification_providers(
        ("my_channel", "my_channel_mock"),
        channels,
        voice_service=voice_service,
    )


def _build_gateways(context: ChannelRuntimeContext) -> dict[str, object]:
    ...


MY_CHANNEL_EXTENSION = ChannelExtensionDefinition(
    manifest=ChannelExtensionManifest(
        id="my_channel",
        providers=("my_channel", "my_channel_mock"),
        route_mounts=("/api/channels/providers/my_channel/*",),
    ),
    default_state_dir_resolver=_default_state_dir,
    config_normalizer=_normalize_configs,
    connector_factory=_build_connectors,
    notification_provider_factory=_build_notification_providers,
    gateway_factory=_build_gateways,
)
```
