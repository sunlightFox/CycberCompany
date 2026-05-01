from __future__ import annotations

from typing import Any

from app.services.chat_safety import context_redaction_summary


class ChatContextCoordinator:
    """Owns model-safe context diagnostics outside the turn orchestrator."""

    def redaction_summary(
        self,
        context: Any,
        *,
        sensitivity_hits: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        return context_redaction_summary(
            context,
            sensitivity_hits=sensitivity_hits,
        )
