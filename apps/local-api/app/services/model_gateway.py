from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from brain.adapters import CancelToken, ModelChatRequest, ModelChatResult, ModelStreamEvent

from app.services.model_provider_registry import ModelProviderRegistry
from app.services.secrets import SecretStore


class ModelProtocolGateway:
    def __init__(self, *, secret_store: SecretStore, client_cls: type[Any]) -> None:
        self._providers = ModelProviderRegistry(
            secret_store=secret_store,
            client_cls=client_cls,
        )

    def build_client(self, brain: dict[str, Any]) -> Any:
        return self._providers.build_client(brain)

    def capability_summary(self, brain: dict[str, Any]) -> dict[str, Any]:
        return self._providers.capability_summary(brain)

    async def complete_chat(
        self,
        brain: dict[str, Any],
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> ModelChatResult:
        client = self.build_client(brain)
        return await client.complete_chat(request, cancel_token)

    async def stream_chat(
        self,
        brain: dict[str, Any],
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ) -> AsyncIterator[ModelStreamEvent]:
        client = self.build_client(brain)
        async for item in client.stream_chat(request, cancel_token):
            yield item
