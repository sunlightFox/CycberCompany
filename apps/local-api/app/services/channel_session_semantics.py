from __future__ import annotations

import hashlib
from typing import Any


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


class ChannelSessionSemanticsRuntime:
    _DELIVERY_MODES = ("dm", "group", "channel", "thread", "system")

    def resolve_inbound(
        self,
        *,
        provider: str,
        channel_account_id: str | None,
        channel_message_id: str,
        raw_payload: dict[str, Any],
        queue_policy: str = "immediate",
        fallback_peer_ref_redacted: str | None = None,
        fallback_thread_id: str | None = None,
        fallback_delivery_mode: str | None = None,
        fallback_source_timestamp: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(raw_payload or {})
        delivery_mode = self._delivery_mode(payload, fallback_delivery_mode)
        thread_id = self._thread_id(payload, fallback_thread_id)
        peer_ref_redacted = str(
            payload.get("peer_ref_redacted")
            or payload.get("channel_peer_id_redacted")
            or fallback_peer_ref_redacted
            or "sha256:unknown-peer"
        )
        source_timestamp = str(
            payload.get("source_timestamp")
            or payload.get("received_at")
            or payload.get("provider_received_at")
            or fallback_source_timestamp
            or ""
        ).strip() or None
        account_id = str(channel_account_id or payload.get("channel_account_id") or "").strip() or None
        session_peer_ref_redacted = self._session_peer_ref_redacted(
            provider=provider,
            channel_account_id=account_id,
            delivery_mode=delivery_mode,
            peer_ref_redacted=peer_ref_redacted,
            thread_id=thread_id,
        )
        dedupe_key = str(
            payload.get("dedupe_key")
            or self._dedupe_key(
                provider=provider,
                channel_account_id=account_id,
                delivery_mode=delivery_mode,
                peer_ref_redacted=peer_ref_redacted,
                thread_id=thread_id,
                channel_message_id=channel_message_id,
            )
        )
        return {
            "provider": provider,
            "channel_account_id": account_id,
            "channel_peer_id_redacted": peer_ref_redacted,
            "channel_thread_id": thread_id,
            "delivery_mode": delivery_mode,
            "queue_policy": str(queue_policy or "immediate"),
            "source_timestamp": source_timestamp,
            "dedupe_key": dedupe_key,
            "session_peer_ref_redacted": session_peer_ref_redacted,
            "conversation_binding_mode": "same_channel_only",
            "cross_channel_reuse_allowed": False,
            "session_lifecycle": "active",
            "session_semantics": {
                "delivery_mode": delivery_mode,
                "thread_ref": thread_id,
                "peer_scope": self._peer_scope(delivery_mode),
                "conversation_binding_mode": "same_channel_only",
                "cross_channel_reuse_allowed": False,
            },
        }

    def merge_policy_snapshot(
        self,
        policy_snapshot: dict[str, Any] | None,
        semantics: dict[str, Any],
        *,
        lifecycle_state: str = "active",
    ) -> dict[str, Any]:
        snapshot = dict(policy_snapshot or {})
        snapshot["session_lifecycle"] = lifecycle_state
        snapshot["session_semantics"] = dict(semantics.get("session_semantics") or {})
        snapshot["conversation_binding_mode"] = semantics.get(
            "conversation_binding_mode", "same_channel_only"
        )
        snapshot["cross_channel_reuse_allowed"] = bool(
            semantics.get("cross_channel_reuse_allowed", False)
        )
        return snapshot

    def runtime_diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "channel_session_semantics",
            "maturity": "runtime_native",
            "supports_delivery_modes": list(self._DELIVERY_MODES),
            "supports_thread_isolation": True,
            "supports_peer_session_rollover": True,
            "dedupe_source": "channel_account_peer_thread_message",
            "cross_channel_reuse_default": False,
        }

    def _delivery_mode(
        self,
        payload: dict[str, Any],
        fallback_delivery_mode: str | None,
    ) -> str:
        explicit = str(
            payload.get("delivery_mode")
            or fallback_delivery_mode
            or ""
        ).strip()
        if explicit in self._DELIVERY_MODES:
            return explicit
        chat_type = str(payload.get("chat_type") or payload.get("peer_type") or "").lower()
        if payload.get("thread_ref") or payload.get("thread_id"):
            return "thread"
        if chat_type in {"group", "room"}:
            return "group"
        if chat_type in {"channel", "topic"}:
            return "channel"
        if chat_type in {"system", "webhook", "worker"}:
            return "system"
        return "dm"

    def _thread_id(
        self,
        payload: dict[str, Any],
        fallback_thread_id: str | None,
    ) -> str | None:
        value = str(
            payload.get("thread_ref")
            or payload.get("thread_id")
            or payload.get("topic_id")
            or fallback_thread_id
            or ""
        ).strip()
        return value or None

    def _session_peer_ref_redacted(
        self,
        *,
        provider: str,
        channel_account_id: str | None,
        delivery_mode: str,
        peer_ref_redacted: str,
        thread_id: str | None,
    ) -> str:
        if delivery_mode == "dm" and not thread_id:
            return peer_ref_redacted
        scope = "|".join(
            [
                provider,
                str(channel_account_id or "unknown-account"),
                delivery_mode,
                peer_ref_redacted,
                str(thread_id or ""),
            ]
        )
        return _hash(scope)

    def _dedupe_key(
        self,
        *,
        provider: str,
        channel_account_id: str | None,
        delivery_mode: str,
        peer_ref_redacted: str,
        thread_id: str | None,
        channel_message_id: str,
    ) -> str:
        return _hash(
            "|".join(
                [
                    provider,
                    str(channel_account_id or "unknown-account"),
                    delivery_mode,
                    peer_ref_redacted,
                    str(thread_id or ""),
                    channel_message_id,
                ]
            )
        )

    def _peer_scope(self, delivery_mode: str) -> str:
        if delivery_mode == "thread":
            return "thread"
        if delivery_mode in {"group", "channel"}:
            return "container"
        if delivery_mode == "system":
            return "system"
        return "peer"
