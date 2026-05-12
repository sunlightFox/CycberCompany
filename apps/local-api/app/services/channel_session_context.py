from __future__ import annotations

from typing import Any

from trace_service import redact


class ChannelSessionContext:
    def build_inbound(
        self,
        *,
        provider: str,
        session: dict[str, Any],
        channel_message_id: str,
        raw_payload: dict[str, Any],
        ui_mode: str,
        semantics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        semantics_payload = dict(semantics or {})
        return {
            "provider": provider,
            "inbound_event_id": semantics_payload.get("inbound_event_id")
            or raw_payload.get("inbound_event_id")
            or raw_payload.get("channel_event_id"),
            "session_id": session.get("session_id"),
            "conversation_id": session.get("conversation_id"),
            "member_id": session.get("member_id"),
            "channel_account_id": semantics_payload.get("channel_account_id")
            or raw_payload.get("channel_account_id")
            or session.get("channel_account_id"),
            "channel_peer_session_id": session.get("channel_peer_session_id"),
            "channel_peer_id_redacted": semantics_payload.get("channel_peer_id_redacted")
            or raw_payload.get("peer_ref_redacted")
            or raw_payload.get("channel_peer_id_redacted")
            or session.get("peer_ref_redacted"),
            "channel_message_id": channel_message_id,
            "channel_thread_id": semantics_payload.get("channel_thread_id")
            or raw_payload.get("thread_ref")
            or raw_payload.get("thread_id"),
            "delivery_mode": semantics_payload.get("delivery_mode")
            or raw_payload.get("delivery_mode"),
            "source_timestamp": semantics_payload.get("source_timestamp")
            or raw_payload.get("source_timestamp")
            or raw_payload.get("received_at"),
            "dedupe_key": semantics_payload.get("dedupe_key") or raw_payload.get("dedupe_key"),
            "sender_label": raw_payload.get("sender_label") or raw_payload.get("display_name"),
            "recipient": raw_payload.get("recipient"),
            "thread_ref": raw_payload.get("thread_ref") or raw_payload.get("thread_id"),
            "ui_mode": ui_mode,
            "steering": dict(semantics_payload.get("steering") or raw_payload.get("steering") or {}),
            "raw_payload_redacted": redact(raw_payload),
        }

    def build_outbound(
        self,
        *,
        provider: str,
        session: dict[str, Any],
        binding: dict[str, Any],
        message: dict[str, Any],
        semantics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        semantics_payload = dict(semantics or {})
        return {
            "provider": provider,
            "inbound_event_id": semantics_payload.get("inbound_event_id")
            or binding.get("channel_event_id"),
            "channel_account_id": session.get("channel_account_id"),
            "channel_peer_session_id": session.get("channel_peer_session_id"),
            "channel_id": session.get("channel_id"),
            "channel_peer_id_redacted": session.get("peer_ref_redacted"),
            "channel_thread_id": semantics_payload.get("channel_thread_id")
            or session.get("thread_ref"),
            "delivery_mode": semantics_payload.get("delivery_mode"),
            "thread_ref": session.get("thread_ref") or binding.get("channel_event_id"),
            "turn_id": binding.get("turn_id"),
            "message_id": message.get("message_id"),
            "steering": dict(binding.get("steering") or session.get("steering") or {}),
            "voice_reply": dict(message.get("voice_metadata") or {}),
        }
