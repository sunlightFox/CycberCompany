from __future__ import annotations

from typing import Any

from response_composer.visible_catalog import (
    apply_conversation_voice,
    catalog_metadata,
    catalog_runtime_texts,
    conversation_voice_strategy,
    opening_copy,
    visible_opening_normalizer,
)


def strip_mechanical_openers(text: str) -> str:
    return visible_opening_normalizer(text)


def voice_catalog_metadata() -> dict[str, Any]:
    return catalog_metadata()


__all__ = [
    "apply_conversation_voice",
    "catalog_runtime_texts",
    "conversation_voice_strategy",
    "opening_copy",
    "strip_mechanical_openers",
    "visible_opening_normalizer",
    "voice_catalog_metadata",
]
