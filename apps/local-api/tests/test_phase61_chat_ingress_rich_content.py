from __future__ import annotations

import json
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase61_text_turn_persists_envelope_queue_and_content_events(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        session_id="phase61-text",
        conversation_id=conversation_id,
        payload={
            "input": {"type": "text", "text": "帮我测试 phase61 文本入口"},
        },
    )
    events = _parse_sse(client.get(created["stream_url"]).text)
    envelope = client.get(f"/api/chat/turns/{created['turn_id']}/envelope").json()
    queue = client.get(f"/api/chat/turns/{created['turn_id']}/queue").json()["item"]
    response_payload = _final_payload(events)

    assert created["envelope_id"] == envelope["envelope_id"]
    assert created["queue_status"] == "queued"
    assert [event["event"] for event in events][:3] == [
        "turn.queued",
        "turn.queue_started",
        "content.normalized",
    ]
    assert envelope["content_parts"][0]["type"] == "text"
    assert envelope["model_safe_text"] == "帮我测试 phase61 文本入口"
    assert queue["status"] in {"completed", "failed"}
    assert (
        response_payload["structured_payload"]["content"]["envelope_id"]
        == envelope["envelope_id"]
    )
    assert response_payload["structured_payload"]["queue"]["queue_id"] == queue["queue_id"]


def test_phase61_multi_part_and_attachments_are_normalized_and_redacted(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        session_id="phase61-rich",
        conversation_id=conversation_id,
        payload={
            "input": {
                "type": "multi_part",
                "content_parts": [
                    {
                        "type": "text",
                        "text": (
                            "请分析这份材料 token=phase61-secret "
                            "C:\\Users\\Administrator\\secret.txt"
                        ),
                    },
                    {
                        "type": "link",
                        "uri": "https://example.com/?token=phase61-secret",
                        "name": "参考链接",
                    },
                    {"type": "task_ref", "ref_id": "tsk_phase61"},
                ],
            },
            "attachments": [
                {
                    "attachment_id": "att_phase61",
                    "name": "secret.txt",
                    "content_type": "text/plain",
                    "uri": "C:\\Users\\Administrator\\secret.txt",
                    "metadata": {"cookie": "phase61-secret"},
                }
            ],
            "context_refs": [
                {"type": "url", "uri": "https://example.com/path?api_key=phase61-secret"}
            ],
            "ingress_metadata": {
                "channel": "local",
                "channel_message_id": "phase61-rich-msg",
                "raw_payload": {"token": "phase61-secret"},
            },
        },
    )
    client.get(created["stream_url"])
    envelope = client.get(f"/api/chat/turns/{created['turn_id']}/envelope").json()
    serialized = json.dumps(envelope, ensure_ascii=False)

    assert {part["type"] for part in envelope["content_parts"]} >= {
        "text",
        "link",
        "task_ref",
        "file",
    }
    assert envelope["context_refs"][0]["type"] == "url"
    assert "phase61-secret" not in serialized
    assert "C:\\Users\\Administrator" not in serialized
    assert "[REDACTED" in serialized


def test_phase61_dedupe_returns_existing_turn_without_duplicate_execution(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    payload = {
        "input": {"type": "text", "text": "phase61 dedupe"},
        "ingress_metadata": {
            "channel": "wechat",
            "channel_message_id": "same-message-id",
        },
    }
    first = _create_turn(
        client,
        session_id="phase61-dedupe",
        conversation_id=conversation_id,
        payload=payload,
    )
    second = _create_turn(
        client,
        session_id="phase61-dedupe",
        conversation_id=conversation_id,
        payload=payload,
    )

    assert second["turn_id"] == first["turn_id"]
    assert second["status"] == "superseded"
    assert second["queue_status"] == "superseded"


def test_phase61_collect_debounce_merges_consecutive_channel_messages(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    first = _create_turn(
        client,
        session_id="phase61-collect",
        conversation_id=conversation_id,
        payload={
            "input": {"type": "text", "text": "phase61 collect 第一段"},
            "ingress_metadata": {
                "channel": "wechat",
                "channel_message_id": "phase61-collect-1",
                "queue_policy": "collect",
                "debounce_ms": 200,
            },
        },
    )
    second = _create_turn(
        client,
        session_id="phase61-collect",
        conversation_id=conversation_id,
        payload={
            "input": {"type": "text", "text": "phase61 collect 第二段"},
            "ingress_metadata": {
                "channel": "wechat",
                "channel_message_id": "phase61-collect-2",
                "queue_policy": "collect",
                "debounce_ms": 200,
            },
        },
    )
    events = _parse_sse(client.get(first["stream_url"]).text)
    envelope = client.get(f"/api/chat/turns/{first['turn_id']}/envelope").json()
    conversation = client.get(f"/api/chat/conversations/{conversation_id}").json()
    user_messages = [
        message
        for message in conversation["messages"]
        if message["turn_id"] == first["turn_id"] and message["author_type"] == "user"
    ]

    assert second["turn_id"] == first["turn_id"]
    assert second["queue_status"] == "superseded"
    assert envelope["normalized_summary"]["debounce_collected"] is True
    assert envelope["normalized_summary"]["collected_message_count"] == 2
    assert "phase61 collect 第一段" in envelope["model_safe_text"]
    assert "phase61 collect 第二段" in envelope["model_safe_text"]
    assert len(user_messages) == 1
    assert user_messages[0]["content"]["normalized_summary"]["collected_message_count"] == 2
    assert [event["event"] for event in events].count("turn.queue_started") == 1


def test_phase61_same_session_queue_claim_allows_only_one_running_turn(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    conversation_id = _conversation_id(client)
    first = _create_turn(
        client,
        session_id="phase61-claim",
        conversation_id=conversation_id,
        payload={"input": {"type": "text", "text": "phase61 claim first"}},
    )
    second = _create_turn(
        client,
        session_id="phase61-claim",
        conversation_id=conversation_id,
        payload={"input": {"type": "text", "text": "phase61 claim second"}},
    )

    async def force_created_queued() -> tuple[bool, bool]:
        await registry.chat.update_turn(first["turn_id"], status="created", updated_at="2026-01-01")
        await registry.chat.update_turn(
            second["turn_id"],
            status="created",
            updated_at="2026-01-01",
        )
        await registry.chat.update_queue_item(
            first["turn_id"],
            status="queued",
            updated_at="2026-01-01",
        )
        await registry.chat.update_queue_item(
            second["turn_id"],
            status="queued",
            updated_at="2026-01-01",
        )
        first_claim = await registry.chat.claim_turn_for_session(
            first["turn_id"],
            session_id="phase61-claim",
            locked_by="test",
            locked_until="2026-01-01T00:05:00+00:00",
            updated_at="2026-01-01",
        )
        second_claim = await registry.chat.claim_turn_for_session(
            second["turn_id"],
            session_id="phase61-claim",
            locked_by="test",
            locked_until="2026-01-01T00:05:00+00:00",
            updated_at="2026-01-01",
        )
        return first_claim, second_claim

    portal = getattr(client, "portal", None)
    assert portal is not None
    first_claim, second_claim = portal.call(force_created_queued)
    first_queue = client.get(f"/api/chat/turns/{first['turn_id']}/queue").json()["item"]
    second_queue = client.get(f"/api/chat/turns/{second['turn_id']}/queue").json()["item"]

    assert first_claim is True
    assert second_claim is False
    assert first_queue["status"] == "running"
    assert second_queue["status"] == "queued"


def test_phase61_recovery_attempts_are_stage_aware_for_model_config_failure(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        session_id="phase61-model-config",
        conversation_id=conversation_id,
        payload={"input": {"type": "text", "text": "phase61 model fallback diagnostics"}},
    )
    client.get(created["stream_url"])
    recovery = client.get(f"/api/chat/turns/{created['turn_id']}/recovery").json()["items"]

    assert recovery
    assert recovery[-1]["recovery_stage"] == "model"
    assert recovery[-1]["error_signature"].startswith("sha256:")
    assert recovery[-1]["recovery_action"] in {"ask_user_for_missing_input", "stop_unrecoverable"}


def test_phase61_context_compaction_records_events_and_recovery_attempt(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    long_text = "phase61 context compaction " + ("长上下文 " * 900)
    created = _create_turn(
        client,
        session_id="phase61-compaction",
        conversation_id=conversation_id,
        payload={"input": {"type": "text", "text": long_text}},
    )
    events = _parse_sse(client.get(created["stream_url"]).text)
    compactions = client.get(f"/api/chat/turns/{created['turn_id']}/compactions").json()["items"]
    recovery = client.get(f"/api/chat/turns/{created['turn_id']}/recovery").json()["items"]

    assert "context.compaction_started" in [event["event"] for event in events]
    assert "context.compaction_completed" in [event["event"] for event in events]
    assert compactions
    assert compactions[0]["token_estimate_before"] >= compactions[0]["token_estimate_after"]
    assert any(
        item["recovery_stage"] == "context"
        and item["recovery_action"] == "rebuild_minimal_context"
        for item in recovery
    )


def _conversation_id(client: TestClient) -> str:
    return client.get("/api/chat/conversations").json()["items"][0]["conversation_id"]


def _create_turn(
    client: TestClient,
    *,
    session_id: str,
    conversation_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "member_id": "mem_xiaoyao",
        **payload,
    }
    response = client.post("/api/chat/turn", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _final_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event["event"] in {"response.completed", "turn.failed"}:
            return event["payload"].get("response_plan") or {}
    return {}
