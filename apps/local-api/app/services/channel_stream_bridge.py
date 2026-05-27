from __future__ import annotations

from typing import Any


class ChannelStreamBridge:
    def final_text_details(self, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content")
        if isinstance(content, dict):
            response_plan = content.get("response_plan")
            if isinstance(response_plan, dict):
                structured_payload = response_plan.get("structured_payload")
                structured_payload = (
                    structured_payload if isinstance(structured_payload, dict) else {}
                )
                plain_text = str(response_plan.get("plain_text") or "").strip()
                if plain_text:
                    return {
                        "plain_text": plain_text,
                        "source": "response_plan_plain_text",
                        "fallback_used": False,
                        "user_text": str(structured_payload.get("current_user_text") or ""),
                    }
        return {
            "plain_text": str(message.get("content_text") or ""),
            "source": "content_text_fallback",
            "fallback_used": True,
            "user_text": "",
        }

    def final_plain_text(self, message: dict[str, Any]) -> str:
        return str(self.final_text_details(message).get("plain_text") or "")

    def deliver_chat_events(self, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content") if isinstance(message.get("content"), dict) else {}
        structured_payload = dict(content.get("response_plan", {}).get("structured_payload") or {})
        steering = dict(structured_payload.get("steering") or content.get("steering") or {})
        final_text = self.final_text_details(message)
        return {
            "plain_text": str(final_text.get("plain_text") or ""),
            "message_id": message.get("message_id"),
            "turn_id": message.get("turn_id"),
            "final_text_source": final_text.get("source"),
            "fallback_used": bool(final_text.get("fallback_used")),
            "response_plan": dict(content.get("response_plan") or {}),
            "steering": steering,
            "voice_reply": dict(message.get("voice_metadata") or {}),
        }
