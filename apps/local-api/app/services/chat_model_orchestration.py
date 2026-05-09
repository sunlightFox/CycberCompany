from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class ChatModelOrchestration:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def run_model_path(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async for event in self._service._run_model_path_impl(*args, **kwargs):
            yield event
