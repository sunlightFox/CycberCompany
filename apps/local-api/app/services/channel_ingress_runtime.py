from __future__ import annotations

from typing import Any

from core_types import ChatTurnResponse

from app.services.channel_session_router import ChannelSessionRouter


class ChannelIngressRuntime:
    def __init__(self, *, chat_service: Any, session_runtime: Any) -> None:
        self._chat = chat_service
        self._session_runtime = session_runtime
        self._router = ChannelSessionRouter()

    async def submit_channel_turn(
        self,
        *,
        provider: str,
        session: dict[str, Any],
        channel_message_id: str,
        text: str,
        raw_payload: dict[str, Any],
        ui_mode: str,
    ) -> ChatTurnResponse:
        route = self._router.route(
            provider=provider,
            session=session,
            channel_message_id=channel_message_id,
            text=text,
            raw_payload=raw_payload,
            ui_mode=ui_mode,
        )
        return await self._session_runtime.create_turn(route.to_turn_request())

    async def diagnostic(self) -> dict[str, Any]:
        session_runtime = await self._session_runtime.diagnostic()
        return {
            "providers": ["local", "wechat", "feishu"],
            "router": "channel_session_router",
            "runtime": "channel_ingress_runtime",
            "session_runtime": session_runtime,
        }
