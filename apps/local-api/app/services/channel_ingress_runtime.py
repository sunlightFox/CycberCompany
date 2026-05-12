from __future__ import annotations

from typing import Any

from core_types import Attachment, ChatContentPart, ChatContextRef, ChatTurnResponse

from app.services.channel_session_router import ChannelSessionRouter
from app.services.chat_steering import ChatSteeringCoordinator


class ChannelIngressRuntime:
    def __init__(
        self,
        *,
        session_runtime: Any,
        channel_session_semantics: Any | None = None,
        chat_hook_runtime: Any | None = None,
    ) -> None:
        self._session_runtime = session_runtime
        self._channel_session_semantics = channel_session_semantics
        self._chat_hook_runtime = chat_hook_runtime
        self._router = ChannelSessionRouter()
        self._steering = ChatSteeringCoordinator()

    async def submit_channel_turn(
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
        steering: dict[str, Any] | None = None,
    ) -> ChatTurnResponse:
        if self._chat_hook_runtime is not None:
            hook_result = await self._chat_hook_runtime.run_before_ingress(
                {
                    "trace_id": None,
                    "conversation_id": session.get("conversation_id"),
                    "turn_id": None,
                    "member_id": session.get("member_id"),
                    "session_id": session.get("session_id"),
                    "channel": provider,
                    "payload": {
                        "provider": provider,
                        "inbound_event_id": inbound_event_id,
                        "channel_message_id": channel_message_id,
                        "raw_payload": raw_payload,
                        "queue_policy": queue_policy,
                        "channel_account_id": channel_account_id,
                        "channel_peer_id_redacted": channel_peer_id_redacted,
                        "channel_thread_id": channel_thread_id,
                        "delivery_mode": delivery_mode,
                        "source_timestamp": source_timestamp,
                        "dedupe_key": dedupe_key,
                    },
                }
            )
            rewritten = dict(hook_result.get("rewritten_payload") or {})
            raw_payload = dict(rewritten.get("raw_payload") or raw_payload)
            queue_policy = str(rewritten.get("queue_policy") or queue_policy)
            channel_account_id = rewritten.get("channel_account_id", channel_account_id)
            channel_peer_id_redacted = rewritten.get(
                "channel_peer_id_redacted",
                channel_peer_id_redacted,
            )
            channel_thread_id = rewritten.get("channel_thread_id", channel_thread_id)
            delivery_mode = rewritten.get("delivery_mode", delivery_mode)
            source_timestamp = rewritten.get("source_timestamp", source_timestamp)
            dedupe_key = rewritten.get("dedupe_key", dedupe_key)
            steering = dict(rewritten.get("steering") or steering or {})
        source_channel_semantics = {
            "provider": provider,
            "delivery_mode": delivery_mode,
            "channel_thread_id": channel_thread_id,
            "channel_account_id": channel_account_id,
        }
        steering_payload = dict(steering or {})
        if "source_channel_semantics" not in steering_payload:
            steering_payload["source_channel_semantics"] = source_channel_semantics
        if queue_policy == "immediate":
            decision = self._steering.decide(
                user_text=text,
                queue_policy=queue_policy,
                active_turn=None,
                working_state={},
                explicit_steering=steering_payload,
            )
            if decision.queue_policy in {"followup", "steer", "interrupt"}:
                queue_policy = decision.queue_policy
                steering_payload = decision.metadata(
                    source_channel_semantics=source_channel_semantics
                )
        route = self._router.route(
            provider=provider,
            session=session,
            channel_message_id=channel_message_id,
            inbound_event_id=inbound_event_id,
            text=text,
            raw_payload=raw_payload,
            ui_mode=ui_mode,
            input_type=input_type,
            content_parts=content_parts,
            attachments=attachments,
            context_refs=context_refs,
            queue_policy=queue_policy,
            channel_account_id=channel_account_id,
            channel_peer_id_redacted=channel_peer_id_redacted,
            channel_thread_id=channel_thread_id,
            delivery_mode=delivery_mode,
            source_timestamp=source_timestamp,
            dedupe_key=dedupe_key,
            steering=steering_payload,
        )
        return await self._session_runtime.create_turn(route.to_turn_request())

    async def diagnostic(self) -> dict[str, Any]:
        session_runtime = await self._session_runtime.diagnostic()
        return {
            "providers": ["local", "wechat", "feishu"],
            "router": "channel_session_router",
            "runtime": "channel_ingress_runtime",
            "supports": ["text", "multi_part", "attachments", "context_refs"],
            "session_semantics_runtime": (
                "channel_session_semantics"
                if self._channel_session_semantics is not None
                else None
            ),
            "session_runtime": session_runtime,
            "hook_runtime": (
                "chat_hook_runtime" if self._chat_hook_runtime is not None else None
            ),
        }
