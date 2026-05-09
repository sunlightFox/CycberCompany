from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class ChatContextAssembly:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def maybe_record_context_compaction(
        self,
        turn: dict[str, Any],
        context: Any,
        context_filter_summary: dict[str, Any],
        root_span_id: str | None,
        emit: Any,
    ) -> AsyncIterator[Any]:
        async for event in self._service._maybe_record_context_compaction_impl(
            turn,
            context,
            context_filter_summary,
            root_span_id,
            emit,
        ):
            yield event
