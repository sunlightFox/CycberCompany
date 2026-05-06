from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from app.core.config import ChannelProviderSection
from app.services.channel_connectors import WechatClawbotConnector


class _SlowWechatClient:
    async def poll_events(self, account_id: str) -> Any:
        assert account_id == "wxid-slow-account"
        await asyncio.sleep(5)
        yield {"event_id": "evt_slow_001"}


class _SlowWechatClientFactory:
    @classmethod
    async def create(cls, **kwargs: Any) -> _SlowWechatClient:
        del kwargs
        return _SlowWechatClient()


def test_wechat_poll_events_uses_bounded_wait_budget(tmp_path: Path) -> None:
    connector = WechatClawbotConnector(
        ChannelProviderSection(
            enabled=True,
            timeout_seconds=10.0,
            poll_interval_seconds=10.0,
        ),
        state_dir=tmp_path,
    )
    connector.set_client_factory(_SlowWechatClientFactory)

    started = time.perf_counter()
    events = asyncio.run(
        connector.poll_events(
            provider_state={"account_id": "wxid-slow-account"},
            limit=20,
        )
    )
    elapsed = time.perf_counter() - started

    assert events == []
    assert elapsed < 3.0
