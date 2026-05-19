from __future__ import annotations

from typing import Any

from app.services.memory import MemoryCommandResult
from app.services.chat_turn_input_facts import (
    explicit_preference_recall_query,
    preference_application_request,
    structured_summary_chat_request,
)

FORGET_MARKERS = ("\u5fd8\u8bb0",)
FORGET_SCOPES = (
    "\u8bb0\u5fc6",
    "\u957f\u671f\u8bb0\u5fc6",
    "\u504f\u597d",
    "\u8fd9\u6279\u5185\u5bb9",
)
EXPLICIT_COMMAND_MARKERS = (
    "\u8bb0\u4f4f",
    "\u518d\u8bb0\u4f4f",
    "\u5e2e\u6211\u8bb0\u4f4f",
    "\u8bf7\u8bb0\u4f4f",
    "��ס",
    "���ס",
    "\u4ee5\u540e\u6309\u8fd9\u4e2a",
    "\u4ee5\u540e\u90fd\u6309\u8fd9\u4e2a",
    "\u4fee\u6b63",
    "\u7ea0\u6b63",
    "\u66f4\u65b0",
    "\u6539\u6210",
)
QUERY_MARKERS = (
    "\u8bb0\u5f97",
    "\u8fd8\u8bb0\u5f97",
    "\u4e4b\u524d",
    "\u6211\u521a\u624d\u8ba9\u4f60\u8bb0\u4f4f\u7684",
    "\u590d\u8ff0",
    "\u56de\u5fc6",
    "\u73b0\u5728",
    "\u544a\u8bc9\u6211",
    "\u4ec0\u4e48",
    "\u6309",
)
QUERY_REFERENCES = (
    "FM30-",
    "\u504f\u597d",
    "\u9879\u76ee\u4e8b\u5b9e",
    "\u957f\u671f\u8bb0\u5fc6",
    "\u8bf4\u8fc7",
    "\u8bb0\u4f4f\u4e86\u4ec0\u4e48",
)

STRUCTURE_PREFERENCE_MARKERS = (
    "\u7ed3\u6784\u504f\u597d",
    "\u603b\u7ed3\u504f\u597d",
    "\u4e0d\u8981\u8868\u683c",
    "\u6807\u9898",
    "\u4e24\u6bb5",
    "\u6bb5\u843d",
)


class ChatMemoryCoordinator:
    """Owns direct memory-command boundaries and user-visible memory notices."""

    def allow_direct_command(self, user_text: str, brain_decision: Any | None) -> bool:
        if self.explicit_memory_command(user_text) or self.explicit_memory_query(user_text):
            return True
        if brain_decision is None:
            return True
        if self.explicit_forget_boundary(user_text):
            return True
        if brain_decision.intent.primary_intent not in {
            "memory_update",
            "memory_correction",
        }:
            return False
        return not (
            brain_decision.intent.needs_tool
            or brain_decision.intent.needs_task
            or brain_decision.intent.needs_skill
            or brain_decision.intent.needs_mcp
            or brain_decision.clarification.get("needs_clarification")
        )

    def explicit_forget_boundary(self, user_text: str) -> bool:
        return any(marker in user_text for marker in FORGET_MARKERS) and any(
            marker in user_text for marker in FORGET_SCOPES
        )

    def explicit_memory_command(self, user_text: str) -> bool:
        text = user_text.strip()
        if not text:
            return False
        if self.explicit_memory_query(text):
            return False
        remember_markers = EXPLICIT_COMMAND_MARKERS[:-4]
        correction_markers = EXPLICIT_COMMAND_MARKERS[-4:]
        preference_correction = any(marker in text for marker in correction_markers) and any(
            marker in text for marker in STRUCTURE_PREFERENCE_MARKERS
        )
        return (
            self.explicit_forget_boundary(text)
            or any(marker in text for marker in remember_markers)
            or any(text.startswith(marker) for marker in correction_markers)
            or preference_correction
        )

    def explicit_memory_query(self, user_text: str) -> bool:
        text = user_text.strip()
        if not text:
            return False
        if structured_summary_chat_request(text) or preference_application_request(text):
            return False
        if explicit_preference_recall_query(text):
            return True
        return any(marker in text for marker in QUERY_MARKERS) and any(
            marker in text for marker in QUERY_REFERENCES
        )

    def command_intent(self, result: MemoryCommandResult) -> str:
        if any(item.proposed_kind == "correction" for item in result.candidates) or any(
            item.kind == "correction" for item in result.memories
        ):
            return "memory_correction"
        return "memory_update"

    def command_notice(self, result: MemoryCommandResult) -> str:
        if any(item.proposed_kind == "correction" for item in result.candidates) or any(
            item.kind == "correction" for item in result.memories
        ):
            if any(item.supersedes for item in result.memories):
                return "\u663e\u5f0f\u8bb0\u5fc6\u7ea0\u9519\u5df2\u5904\u7406\uff0c\u65e7\u8bb0\u5fc6\u5df2\u88ab\u65b0\u8bb0\u5fc6\u53d6\u4ee3\u3002"
            return "\u663e\u5f0f\u8bb0\u5fc6\u7ea0\u9519\u5df2\u8bb0\u5f55\uff1b\u6ca1\u6709\u627e\u5230\u53ef\u7cbe\u786e\u53d6\u4ee3\u7684\u65e7\u8bb0\u5fc6\u3002"
        return "\u663e\u5f0f\u8bb0\u5fc6\u547d\u4ee4\u5df2\u5904\u7406\u3002"
