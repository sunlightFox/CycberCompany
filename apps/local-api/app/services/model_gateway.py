from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from brain.adapters import CancelToken, ModelChatRequest, ModelChatResult, ModelStreamEvent

from app.services.secrets import SecretStore


class ModelProtocolGateway:
    def __init__(self, *, secret_store: SecretStore, client_cls: type[Any]) -> None:
        self._secrets = secret_store
        self._client_cls = client_cls

    def build_client(self, brain: dict[str, Any]) -> Any:
        endpoint = str(brain["endpoint"])
        api_key = self._secrets.get_secret(brain.get("api_key_ref"))
        kwargs = {
            "protocol_family": str(brain.get("protocol_family") or ""),
            "request_format": str(brain.get("request_format") or ""),
            "response_format": str(brain.get("response_format") or ""),
            "supports_stream": brain.get("supports_stream"),
        }
        try:
            return self._client_cls(endpoint, api_key, **kwargs)
        except TypeError:
            return self._client_cls(endpoint, api_key)

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
