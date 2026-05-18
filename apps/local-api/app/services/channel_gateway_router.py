from __future__ import annotations

from core_types import ErrorCode

from app.core.errors import AppError


class ChannelGatewayRouter:
    def __init__(self, registry) -> None:  # type: ignore[no-untyped-def]
        self._registry = registry

    def for_provider(self, provider: str):  # type: ignore[no-untyped-def]
        return self._registry.channel_gateway(provider)

    async def for_pairing_request(self, pairing_request_id: str):  # type: ignore[no-untyped-def]
        pairing = await self._registry.channels.get_pairing_request(pairing_request_id)
        if pairing is None:
            raise AppError(ErrorCode.NOT_FOUND, "配对请求不存在", status_code=404)
        return self.for_provider(str(pairing["provider"]))

    async def for_peer(self, peer_id: str):  # type: ignore[no-untyped-def]
        peer = await self._registry.channels.get_peer_session(peer_id)
        if peer is None:
            raise AppError(ErrorCode.NOT_FOUND, "渠道 peer 会话不存在", status_code=404)
        return self.for_provider(str(peer["provider"]))

