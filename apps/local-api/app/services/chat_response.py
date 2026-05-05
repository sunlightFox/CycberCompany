from __future__ import annotations

from typing import Any

from core_types import ResponsePlan

from app.services.chat_safety import ChatVisibleOutputFilter, response_filter_payload
from app.services.chat_visible_guard import visible_text_guard


class ChatResponseCoordinator:
    """Centralizes visible chat output filtering and response-plan text cleanup."""

    def begin_visible_stream(self) -> ChatVisibleOutputFilter:
        return ChatVisibleOutputFilter()

    def filter_text(self, text: str) -> tuple[str, dict[str, Any]]:
        return ChatVisibleOutputFilter.filter_text(text)

    def merge_filter(
        self,
        response_filter: dict[str, Any] | None,
        final_filter: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            **response_filter_payload(response_filter),
            "final_guard": final_filter,
        }

    def visible_text(self, text: str) -> str:
        return visible_text_guard(text)

    def normalize_plan_text(self, plan: ResponsePlan, fallback_text: str) -> dict[str, str]:
        return {
            "summary": self.visible_text(plan.summary or fallback_text),
            "plain_text": self.visible_text(plan.plain_text or fallback_text),
        }
