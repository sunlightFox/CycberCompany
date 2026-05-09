from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


class ChatResponseFinalize:
    def __init__(self, service: Any) -> None:
        self._service = service

    async def complete_without_model(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async for event in self._service._complete_without_model_impl(*args, **kwargs):
            yield event
