from __future__ import annotations

from app.external_platforms.browser_extension import BrowserExternalPlatformProvider
from app.services.external_platform_extensions import (
    ExternalPlatformExtensionDefinition,
    ExternalPlatformExtensionManifest,
    ExternalPlatformRuntimeContext,
)
from app.services.external_platform_providers import ExternalPlatformProvider

XIAOHONGSHU_BROWSER_TARGET = {
    "target_id": "ept_social_xiaohongshu",
    "platform_key": "social_xiaohongshu",
    "display_name": "小红书",
    "aliases": ["小红书", "xhs", "rednote"],
    "supported_actions": ["publish_content", "comment_content", "read_status"],
    "required_asset_types": ["account"],
    "execution_modes": ["browser"],
    "risk_defaults": {
        "publish_content": "R4",
        "comment_content": "R3",
        "read_status": "R1",
    },
    "metadata": {
        "seeded_for": "phase_xiaohongshu_browser_flow",
        "real_provider": True,
        "provider_registry_owned": True,
        "real_external_platform_integration": True,
    },
}


def _build_providers(_context: ExternalPlatformRuntimeContext) -> list[ExternalPlatformProvider]:
    provider = BrowserExternalPlatformProvider(
        provider_key="social_xiaohongshu",
        display_name="小红书 browser boundary",
    )
    return [provider]


XIAOHONGSHU_EXTERNAL_PLATFORM_EXTENSION = ExternalPlatformExtensionDefinition(
    manifest=ExternalPlatformExtensionManifest(
        id="xiaohongshu",
        platform_keys=("social_xiaohongshu",),
        execution_modes=(),
        seeded_targets=(XIAOHONGSHU_BROWSER_TARGET,),
        display_aliases=("小红书",),
        canonical_aliases=("小红书", "xhs", "rednote"),
        action_markers={
            "publish_content": ("发布", "发帖", "发文章", "发动态", "发布内容"),
            "comment_content": ("评论", "回复", "留言"),
            "read_status": ("查看", "读取", "查询状态"),
        },
        content_markers=("正文：", "正文:", "内容：", "内容:", "文章：", "文章:"),
        generic_platform_markers=("平台", "账号", "社交平台", "小红书"),
    ),
    provider_factory=_build_providers,
)
