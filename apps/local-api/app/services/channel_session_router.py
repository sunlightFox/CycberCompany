from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core_types import (
    Attachment,
    ChatContentPart,
    ChatContextRef,
    ChatIngressMetadata,
    ChatInput,
    ChatTurnRequest,
    ClientContext,
)


@dataclass(frozen=True)
class ChannelSessionRoute:
    provider: str
    session_id: str
    conversation_id: str | None
    member_id: str
    ui_mode: str
    text: str
    inbound_event_id: str | None
    channel_message_id: str
    raw_payload: dict[str, Any]
    input_type: str = "text"
    content_parts: list[ChatContentPart] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    context_refs: list[ChatContextRef] = field(default_factory=list)
    queue_policy: str = "immediate"
    channel_account_id: str | None = None
    channel_peer_id_redacted: str | None = None
    channel_thread_id: str | None = None
    delivery_mode: str | None = None
    source_timestamp: str | None = None
    dedupe_key: str | None = None

    def to_turn_request(self) -> ChatTurnRequest:
        return ChatTurnRequest(
            session_id=self.session_id,
            conversation_id=self.conversation_id,
            member_id=self.member_id,
            input=ChatInput(
                type="multi_part" if self.input_type == "multi_part" else "text",
                text=self.text,
                content_parts=self.content_parts,
            ),
            attachments=self.attachments,
            context_refs=self.context_refs,
            ingress_metadata=ChatIngressMetadata(
                channel=self.provider,
                inbound_event_id=self.inbound_event_id,
                channel_message_id=self.channel_message_id,
                channel_account_id=self.channel_account_id,
                channel_peer_id_redacted=self.channel_peer_id_redacted,
                channel_thread_id=self.channel_thread_id,
                delivery_mode=self.delivery_mode,
                queue_policy=self.queue_policy,
                dedupe_key=self.dedupe_key,
                source_timestamp=self.source_timestamp,
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
        inbound_event_id: str | None = None,
        channel_message_id: str,
        text: str,
        raw_payload: dict[str, Any],
        ui_mode: str,
        input_type: str = "text",
        content_parts: list[ChatContentPart] | None = None,
        attachments: list[Attachment] | None = None,
        context_refs: list[ChatContextRef] | None = None,
        queue_policy: str = "immediate",
        channel_account_id: str | None = None,
        channel_peer_id_redacted: str | None = None,
        channel_thread_id: str | None = None,
        delivery_mode: str | None = None,
        source_timestamp: str | None = None,
        dedupe_key: str | None = None,
    ) -> ChannelSessionRoute:
        normalized_text = text.strip() or f"收到一条来自 {provider} 的消息。"
        return ChannelSessionRoute(
            provider=provider,
            session_id=str(session["session_id"]),
            conversation_id=session.get("conversation_id"),
            member_id=str(session["member_id"]),
            ui_mode=ui_mode,
            text=normalized_text,
            inbound_event_id=inbound_event_id,
            channel_message_id=channel_message_id,
            raw_payload=raw_payload,
            input_type=input_type,
            content_parts=list(content_parts or []),
            attachments=list(attachments or []),
            context_refs=list(context_refs or []),
            queue_policy=queue_policy,
            channel_account_id=channel_account_id,
            channel_peer_id_redacted=channel_peer_id_redacted,
            channel_thread_id=channel_thread_id,
            delivery_mode=delivery_mode,
            source_timestamp=source_timestamp,
            dedupe_key=dedupe_key,
        )
