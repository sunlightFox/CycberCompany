from __future__ import annotations

from typing import Any

from app.services.memory import MemoryCommandResult


class ChatMemoryCoordinator:
    """Owns direct memory-command boundaries and user-visible memory notices."""

    def allow_direct_command(self, user_text: str, brain_decision: Any | None) -> bool:
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
        return "忘记" in user_text and any(
            marker in user_text for marker in ["记忆", "长期记忆", "偏好", "本批次"]
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
                return "显式记忆纠错已处理，旧记忆已被新记忆取代。"
            return "显式记忆纠错已记录；没有找到可精确取代的旧记忆。"
        return "显式记忆命令已处理。"
