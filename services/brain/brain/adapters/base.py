from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from brain.adapters.types import CancelToken, ModelChatRequest, ModelChatResult, ModelStreamEvent


class ChatModelClient(Protocol):
    async def stream_chat(
        self,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> AsyncIterator[ModelStreamEvent]:
        ...

    async def complete_chat(
        self,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> ModelChatResult:
        ...
