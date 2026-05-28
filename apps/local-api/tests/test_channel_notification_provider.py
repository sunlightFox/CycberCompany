from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.channels.common import ChannelBindingNotificationProvider


class _FakeChannels:
    def __init__(self) -> None:
        self.text_calls: list[dict[str, Any]] = []

    async def send_channel_text(self, **kwargs: Any) -> SimpleNamespace:
        self.text_calls.append(kwargs)
        return SimpleNamespace(
            status="sent",
            provider_message_id="wechat:test",
            response_summary={},
            error_code=None,
            error_summary=None,
        )


@pytest.mark.asyncio
async def test_channel_notification_provider_allows_voice_text_fallback() -> None:
    channels = _FakeChannels()
    provider = ChannelBindingNotificationProvider(channels, voice_service=SimpleNamespace())

    result = await provider.send(
        channel=SimpleNamespace(provider="wechat", provider_config={"provider_state_ref": "state"}),
        message=SimpleNamespace(
            recipient="wxid-user",
            body_redacted="语音转文字不完整时，先问缺口。",
            metadata={
                "voice_reply": {
                    "requested": True,
                    "should_render": False,
                    "allow_text_fallback": True,
                    "reason": "voice_plan_requested",
                }
            },
        ),
    )

    assert result.status == "sent"
    assert channels.text_calls[0]["text"] == "语音转文字不完整时，先问缺口。"

