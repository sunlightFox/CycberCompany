from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from cycber_cli.diagnostics import turn_diagnostics
from cycber_cli.http_client import CycberApiClient
from cycber_cli.output import assistant_delta, persisted_assistant_text
from cycber_cli.state import CliState


@dataclass
class ChatResult:
    created: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)


async def ensure_conversation(client: CycberApiClient, state: CliState) -> None:
    if state.conversation_id and state.member_id:
        return
    conversations = await client.conversations()
    if not conversations:
        raise RuntimeError("没有可用会话；请先启动 local-api 并完成默认数据初始化。")
    state.update_from_conversation(conversations[0])


async def send_message(
    client: CycberApiClient,
    state: CliState,
    message: str,
    *,
    conversation_id: str | None = None,
    member_id: str | None = None,
    session_id: str | None = None,
    stream: bool = True,
    include_diagnostics: bool = False,
) -> ChatResult:
    await ensure_conversation(client, state)
    state.conversation_id = conversation_id or state.conversation_id
    state.member_id = member_id or state.member_id
    state.session_id = session_id or state.session_id or f"cli_{time.strftime('%Y%m%d')}"
    payload = {
        "session_id": state.session_id,
        "conversation_id": state.conversation_id,
        "member_id": state.member_id,
        "input": {"type": "text", "text": message},
        "client_context": {"timezone": "Asia/Shanghai", "locale": "zh-CN", "ui_mode": "cli"},
    }
    created = await client.create_turn(payload)
    turn_id = str(created["turn_id"])
    events: list[dict[str, Any]] = []
    parts: list[str] = []
    if stream:
        async for event in client.stream_turn(turn_id):
            events.append(event)
            delta = assistant_delta(event)
            if delta:
                parts.append(delta)
    else:
        turn_detail = getattr(client, "turn", None)
        if callable(turn_detail):
            deadline = time.time() + 60
            while time.time() < deadline:
                detail = await turn_detail(turn_id)
                if detail.get("status") in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.1)
        events = await client.turn_events(turn_id)
        parts.append(persisted_assistant_text(events))
    diagnostics = await turn_diagnostics(client, turn_id) if include_diagnostics else {}
    state.last_turn_id = turn_id
    state.last_trace_id = str(created.get("trace_id") or "")
    return ChatResult(
        created=created,
        events=events,
        text="".join(parts).strip(),
        artifacts=response_artifacts(events),
        diagnostics=diagnostics,
    )


def response_artifacts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in reversed(events):
        event_type = str(item.get("event_type") or item.get("event") or "")
        if event_type != "response.completed":
            continue
        payload_obj = item.get("payload")
        payload: dict[str, Any] = payload_obj if isinstance(payload_obj, dict) else {}
        nested_obj = payload.get("payload")
        nested: dict[str, Any] = nested_obj if isinstance(nested_obj, dict) else payload
        plan_obj = nested.get("response_plan")
        plan: dict[str, Any] = plan_obj if isinstance(plan_obj, dict) else {}
        refs = plan.get("artifact_refs")
        if isinstance(refs, list):
            return [item for item in refs if isinstance(item, dict)]
    return []
