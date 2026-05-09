from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core_types import ChatIngressMetadata, ChatInput, ChatTurnRequest, ClientContext


@dataclass(frozen=True)
class ChannelSessionRoute:
    provider: str
    session_id: str
    conversation_id: str | None
    member_id: str
    ui_mode: str
    text: str
    channel_message_id: str
    raw_payload: dict[str, Any]

    def to_turn_request(self) -> ChatTurnRequest:
        return ChatTurnRequest(
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            member_id=self.member_id,
            input=ChatInput(type="text", text=self.text),
            ingress_metadata=ChatIngressMetadata(
                channel=self.provider,
                channel_message_id=self.channel_message_id,
                queue_policy="immediate",
                raw_payload=self.raw_payload,
            ),
            client_context=ClientContext(
                timezone="Asia/Shanghai",
                locale="zh-CN",
                ui_mode=self.ui_mode,
            ),
        )


class ChannelSessionRouter:
    def route(
        self,
        *,
        provider: str,
        session: dict[str, Any],
        channel_message_id: str,
        text: str,
        raw_payload: dict[str, Any],
        ui_mode: str,
    ) -> ChannelSessionRoute:
        normalized_text = text.strip() or f"收到一条来自 {provider} 的消息。"
        return ChannelSessionRoute(
            provider=provider,
            session_id=str(session["session_id"]),
            conversation_id=session.get("conversation_id"),
            member_id=str(session["member_id"]),
            ui_mode=ui_mode,
            text=normalized_text,
            channel_message_id=channel_message_id,
            raw_payload=raw_payload,
        )
