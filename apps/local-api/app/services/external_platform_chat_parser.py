from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.external_platform_extensions import ExternalPlatformExtensionRegistry

DEFAULT_GENERIC_PLATFORM_MARKERS: tuple[str, ...] = (
    "平台",
    "账号",
    "账户",
    "外部平台",
    "社交平台",
    "channel",
    "platform",
    "account",
)
DEFAULT_CONTENT_MARKERS: tuple[str, ...] = (
    "内容：",
    "内容:",
    "正文：",
    "正文:",
    "这段内容：",
    "这段内容:",
    "文章：",
    "文章:",
)
DEFAULT_ACTION_MARKERS: dict[str, tuple[str, ...]] = {
    "comment_content": ("评论", "留言", "回复", "comment", "reply"),
    "publish_content": (
        "发布",
        "发一篇文章",
        "发文章",
        "发动态",
        "发帖",
        "发到",
        "同步公告",
        "publish",
        "post",
    ),
    "send_message": ("发消息", "私信", "发送", "send message", "message"),
    "read_status": ("查看", "读取", "查询状态", "read", "status"),
}
QUOTE_PATTERN = re.compile(r"[\"'“”‘’「」『』]([^\"“”‘’「」『』]{3,})[\"'“”‘’「」『』]")


@dataclass(frozen=True)
class ExternalPlatformChatRecognition:
    platform_key: str
    canonical_aliases: tuple[str, ...]
    display_aliases: tuple[str, ...]
    action_markers: dict[str, tuple[str, ...]]
    content_markers: tuple[str, ...]
    generic_platform_markers: tuple[str, ...]


class ExternalPlatformChatParser:
    def __init__(self, registry: ExternalPlatformExtensionRegistry) -> None:
        self._registry = registry
        self._recognition_by_platform: dict[str, ExternalPlatformChatRecognition] = {}
        action_markers = {key: list(values) for key, values in DEFAULT_ACTION_MARKERS.items()}
        content_markers = list(DEFAULT_CONTENT_MARKERS)
        generic_platform_markers = list(DEFAULT_GENERIC_PLATFORM_MARKERS)
        for item in registry.inventory():
            manifest = registry.get(str(item["id"])).manifest
            recognition = ExternalPlatformChatRecognition(
                platform_key=str(manifest.platform_keys[0]) if manifest.platform_keys else "",
                canonical_aliases=tuple(dict.fromkeys(manifest.canonical_aliases)),
                display_aliases=tuple(dict.fromkeys(manifest.display_aliases)),
                action_markers={
                    key: tuple(dict.fromkeys(values))
                    for key, values in manifest.action_markers.items()
                },
                content_markers=tuple(dict.fromkeys(manifest.content_markers)),
                generic_platform_markers=tuple(dict.fromkeys(manifest.generic_platform_markers)),
            )
            if recognition.platform_key:
                self._recognition_by_platform[recognition.platform_key] = recognition
            for key, values in recognition.action_markers.items():
                action_markers.setdefault(key, [])
                action_markers[key].extend(value for value in values if value)
            content_markers.extend(value for value in recognition.content_markers if value)
            generic_platform_markers.extend(
                value for value in recognition.generic_platform_markers if value
            )
        self._all_action_markers = {
            key: tuple(dict.fromkeys(values)) for key, values in action_markers.items()
        }
        self._all_content_markers = tuple(dict.fromkeys(content_markers))
        self._generic_platform_markers = tuple(dict.fromkeys(generic_platform_markers))

    def looks_like_chat_request(self, text: str, targets: list[dict[str, Any]]) -> bool:
        clean = " ".join(str(text or "").strip().split())
        if not clean:
            return False
        action_type, _ = self.detect_action_type(clean)
        if action_type == "unknown":
            return False
        if self.match_target(clean, targets) is not None:
            return True
        lowered = clean.lower()
        return any(
            marker in clean or marker.lower() in lowered
            for marker in self._generic_platform_markers
        )

    def detect_action_type(
        self,
        text: str,
        *,
        platform_key: str | None = None,
    ) -> tuple[str, float]:
        lowered = text.lower()
        marker_map = self._markers_for_platform(platform_key)
        for action_type in ("comment_content", "publish_content", "send_message", "read_status"):
            markers = marker_map.get(action_type, ())
            if any(marker and marker.lower() in lowered for marker in markers):
                return action_type, 0.25
        return "unknown", 0.0

    def extract_content(
        self,
        text: str,
        action_type: str,
        *,
        platform_key: str | None = None,
    ) -> str | None:
        if action_type not in {"publish_content", "comment_content", "send_message"}:
            return None
        recognition = self._recognition_by_platform.get(str(platform_key or ""))
        markers = recognition.content_markers if recognition else self._all_content_markers
        for marker in markers:
            if marker in text:
                value = text.split(marker, 1)[1].strip()
                return value or None
        quoted = QUOTE_PATTERN.findall(text)
        if quoted:
            return quoted[-1].strip()
        return None

    def match_target(self, text: str, targets: list[dict[str, Any]]) -> dict[str, Any] | None:
        lowered = text.lower()
        best_match: dict[str, Any] | None = None
        best_priority = -1
        best_length = -1
        ambiguous = False
        for target in targets:
            match = self._target_match(target, lowered)
            if match is None:
                continue
            priority, alias = match
            alias_length = len(alias)
            if priority > best_priority or (
                priority == best_priority and alias_length > best_length
            ):
                best_priority = priority
                best_length = alias_length
                best_match = {
                    "target_id": target["target_id"],
                    "platform_key": target["platform_key"],
                    "display_name": target["display_name"],
                    "matched_alias": alias,
                }
                ambiguous = False
            elif (
                priority == best_priority
                and alias_length == best_length
                and best_match is not None
                and best_match.get("target_id") != target.get("target_id")
            ):
                ambiguous = True
        if ambiguous:
            return None
        return best_match

    def _target_match(self, target: dict[str, Any], lowered: str) -> tuple[int, str] | None:
        platform_key = str(target.get("platform_key") or "")
        recognition = self._recognition_by_platform.get(platform_key)
        canonical_aliases = list(recognition.canonical_aliases if recognition else ())
        display_aliases = list(recognition.display_aliases if recognition else ())
        target_aliases = [
            str(target.get("display_name") or ""),
            *[str(alias) for alias in target.get("aliases", [])],
        ]
        platform_key_aliases = [platform_key]
        for priority, aliases in (
            (3, [*canonical_aliases, *display_aliases]),
            (2, target_aliases),
            (1, platform_key_aliases),
        ):
            for alias in dict.fromkeys(alias for alias in aliases if alias):
                if alias.lower() in lowered:
                    return priority, alias
        return None

    def _markers_for_platform(self, platform_key: str | None) -> dict[str, tuple[str, ...]]:
        recognition = self._recognition_by_platform.get(str(platform_key or ""))
        if recognition is None:
            return self._all_action_markers
        merged = {key: list(values) for key, values in self._all_action_markers.items()}
        for key, values in recognition.action_markers.items():
            merged.setdefault(key, [])
            merged[key] = list(dict.fromkeys([*values, *merged[key]]))
        return {key: tuple(values) for key, values in merged.items()}
