from __future__ import annotations

import json
from typing import Any, cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase82_chat_turn_memory_write_populates_unified_source_and_ledgers(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    created = client.post(
        "/api/chat/turn",
        json={
            "session_id": "ses_phase82_memory",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "记住：以后开发先补 migration 再补 service"},
        },
    )
    assert created.status_code == 200, created.text
    turn = created.json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    event_names = [item["event"] for item in events]
    memories = client.get("/api/memory", params={"status": "active"}).json()["items"]
    written = next(
        item for item in memories if item["summary_text"] == "以后开发先补 migration 再补 service"
    )

    registry = cast(FastAPI, client.app).state.registry
    turn_ledger = anyio.run(registry.chat.get_turn_ledger, turn["turn_id"])
    run_ledgers = anyio.run(registry.chat.list_run_ledgers, turn["turn_id"])

    assert "memory.written" in event_names
    assert written["source"]["type"] == "conversation_turn"
    assert written["source"]["conversation_id"] == conversation["conversation_id"]
    assert written["source"]["turn_id"] == turn["turn_id"]
    assert written["source"]["message_id"] == turn["message_id"]
    assert written["source"]["trace_id"] == turn["trace_id"]
    assert written["source"]["channel"] == "local"
    assert written["source"]["captured_at"]

    assert turn_ledger is not None
    assert turn_ledger["turn_id"] == turn["turn_id"]
    assert turn_ledger["trace_id"] == turn["trace_id"]
    assert turn_ledger["status"] == "completed"
    assert turn_ledger["conversation_id"] == conversation["conversation_id"]
    assert turn_ledger["started_at"] is not None
    assert turn_ledger["ended_at"] is not None

    stages = {item["stage"] for item in run_ledgers}
    assert {"turn_accept", "turn_execution", "response_finalize", "memory_write"}.issubset(
        stages
    )
    assert any(item["event_type"] == "memory.written" for item in run_ledgers)


def test_phase82_memory_source_chain_keeps_supersede_history(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    chat_turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "ses_phase82_correction",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "记住：用户喜欢咖啡"},
        },
    ).json()
    client.get(chat_turn["stream_url"])
    chat_memories = client.get("/api/memory", params={"status": "active"}).json()["items"]
    chat_memory = next(item for item in chat_memories if item["source"]["turn_id"] == chat_turn["turn_id"])

    old = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "记住：用户喜欢可乐"},
    ).json()["memories"][0]
    correction = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "不是可乐，是茶"},
    ).json()["memories"][0]
    old_after = client.get(f"/api/memory/{old['memory_id']}").json()

    registry = cast(FastAPI, client.app).state.registry
    source_chain = anyio.run(
        registry.chat_run_ledger_service.memory_source_chain,
        chat_memory["memory_id"],
    )

    assert correction["supersedes"] == old["memory_id"]
    assert old_after["status"] == "superseded"
    assert source_chain["memory"]["memory_id"] == chat_memory["memory_id"]
    assert any(item["event_type"] == "memory.written" for item in source_chain["source_chain"])


def test_phase82_task_replay_exposes_unified_memory_source_contract(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "生成第八十二阶段回放检查", "auto_start": True},
    ).json()
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert replay["memory_writes"]
    source = replay["memory_writes"][0]["source"]
    assert source["type"] == "task_result"
    assert source["conversation_id"]
    assert source["task_id"] == task["task_id"]
    assert source["trace_id"]
    assert source["captured_at"]


def test_phase82_hook_execution_is_visible_in_run_ledger_timeline(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    created = client.post(
        "/api/chat/turn",
        json={
            "session_id": "ses_phase82_hook_ledger",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "记住：hook ledger 也要进时间线"},
        },
    )
    assert created.status_code == 200, created.text
    turn = created.json()
    client.get(turn["stream_url"])

    registry = cast(FastAPI, client.app).state.registry
    anyio.run(
        registry.chat_hook_runtime.run_before_finalize,
        {
            "trace_id": turn["trace_id"],
            "conversation_id": conversation["conversation_id"],
            "turn_id": turn["turn_id"],
            "member_id": "mem_xiaoyao",
            "session_id": "ses_phase82_hook_ledger",
            "channel": "local",
            "payload": {
                "plain_text": "trace_id=trc_demo",
                "summary": "trace_id=trc_demo",
                "response_plan": {
                    "plain_text": "trace_id=trc_demo",
                    "summary": "trace_id=trc_demo",
                },
            },
        },
    )
    run_ledgers = anyio.run(registry.chat.list_run_ledgers, turn["turn_id"])
    hook_entries = [item for item in run_ledgers if item["event_type"] == "hook.before_finalize"]

    assert hook_entries
    assert hook_entries[-1]["stage"] == "hook:before_finalize"
    assert hook_entries[-1]["status"] == "rewritten"
    assert hook_entries[-1]["payload"]["hook_name"] == "builtin.before_finalize.visible_guard"


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
