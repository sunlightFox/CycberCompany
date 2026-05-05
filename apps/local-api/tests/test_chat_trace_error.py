from __future__ import annotations

import json
from typing import cast

import anyio
from chat_runtime import ChatRuntime
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_chat_001_no_model_turn_uses_phase_two_events_and_persists_messages(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    payload = {
        "session_id": "ses_test",
        "conversation_id": conversation["conversation_id"],
        "member_id": "mem_xiaoyao",
        "input": {"type": "text", "text": "帮我规划今天的开发"},
        "attachments": [],
        "client_context": {"timezone": "Asia/Shanghai", "locale": "zh-CN", "ui_mode": "chat"},
    }

    response = client.post("/api/chat/turn", json=payload)
    body = response.json()
    events = _parse_sse(client.get(body["stream_url"]).text)
    turn_detail = client.get(f"/api/chat/turns/{body['turn_id']}").json()
    persisted_events = client.get(f"/api/chat/turns/{body['turn_id']}/events").json()["items"]
    detail = client.get(f"/api/chat/conversations/{conversation['conversation_id']}").json()

    assert response.status_code == 200
    assert body["status"] == "created"
    assert body["stream_url"] == f"/api/chat/stream/{body['turn_id']}"
    assert [event["event"] for event in events] == [
        "turn.queued",
        "turn.queue_started",
        "content.normalized",
        "turn.started",
        "context.started",
        "context.ready",
        "intent.detected",
        "mode.selected",
        "turn.failed",
    ]
    assert events[-1]["payload"]["code"] == "MODEL_NOT_CONFIGURED"
    assert events[-1]["payload"]["response_plan"]["structured_payload"]["error_code"] == (
        "MODEL_NOT_CONFIGURED"
    )
    assert turn_detail["status"] == "failed"
    assert turn_detail["error_code"] == "MODEL_NOT_CONFIGURED"
    assert [item["event_type"] for item in persisted_events] == [
        event["event"] for event in events
    ]
    assert any(message["content_text"] == "帮我规划今天的开发" for message in detail["messages"])
    assert any(
        message["content"].get("error_code") == "MODEL_NOT_CONFIGURED"
        for message in detail["messages"]
    )
    assert any(
        message["content"].get("response_plan", {}).get("plain_text")
        for message in detail["messages"]
    )
    replayed_events = _parse_sse(client.get(body["stream_url"]).text)
    assert [event["event"] for event in replayed_events] == [event["event"] for event in events]


def test_trace_001_trace_service_writes_and_api_reads_trace(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    response = client.post(
        "/api/chat/turn",
        json={
            "session_id": "ses_trace",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "trace test"},
        },
    )
    body = response.json()
    client.get(body["stream_url"])
    trace_id = body["trace_id"]

    trace = client.get(f"/api/traces/{trace_id}")
    span_types = {span["span_type"] for span in trace.json()["spans"]}

    assert trace.status_code == 200
    assert trace.json()["trace_id"] == trace_id
    assert trace.json()["status"] == "failed"
    assert "chat.turn" in span_types
    assert "context.build" in span_types
    assert "brain.intent" in span_types
    assert "turn.failed" in span_types
    assert any(span["latency_ms"] is not None for span in trace.json()["spans"])
    assert any(span["error_code"] == "MODEL_NOT_CONFIGURED" for span in trace.json()["spans"])


def test_error_001_validation_errors_use_project_error_model(client: TestClient) -> None:
    response = client.post("/api/chat/turn", json={"input": {"type": "text", "text": ""}})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "trace_id" in body["error"]


def test_error_002_not_found_uses_project_error_model(client: TestClient) -> None:
    response = client.get("/api/chat/conversations/missing_conversation")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert "trace_id" in body["error"]


def test_chat_002_runtime_can_emit_failed_event() -> None:
    events = ChatRuntime().failed_events("turn_failed_test", "validation")

    assert events[0].event == "turn.failed"
    assert events[0].payload["reason"] == "validation"


def test_api_001_openapi_can_be_generated(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/system/runtime-contracts" in response.json()["paths"]
    assert "/api/brains" in response.json()["paths"]
    assert "/api/model-routing/preview" in response.json()["paths"]


def test_api_002_placeholder_routes_return_stable_schema(client: TestClient) -> None:
    assets = client.get("/api/assets").json()
    tasks = client.get("/api/tasks").json()
    settings = client.get("/api/settings").json()
    assert assets["items"] == []
    assert tasks["items"] == []
    assert settings["settings"]["model_routing"]["default_route"]
    assert settings["settings"]["mcp"]["default_unknown_tool_status"] in {
        "disabled",
        "approval_required",
    }
    assert settings["trace_id"]


def test_api_003_runtime_contracts_are_exposed(client: TestClient) -> None:
    body = client.get("/api/system/runtime-contracts").json()
    names = {item["name"] for item in body["items"]}

    assert {"ChatRuntime", "ContextGateway", "AssetBroker", "SafetyService"}.issubset(names)


def test_trace_002_startup_trace_contains_foundation_spans(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry

    rows = anyio.run(
        registry.db.fetch_all,
        "SELECT trace_id FROM traces ORDER BY started_at ASC LIMIT 1",
    )
    trace = client.get(f"/api/traces/{rows[0]['trace_id']}").json()
    span_types = {span["span_type"] for span in trace["spans"]}

    assert {
        "app.startup",
        "config.load",
        "db.migration",
        "shell.load",
        "bootstrap.organization",
    }.issubset(span_types)


def test_audit_001_redaction_and_query(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry

    anyio.run(_write_redaction_audit, registry)
    body = client.get("/api/audit").json()
    event = body["items"][0]

    assert event["payload_redacted"]["api_key"] == "[REDACTED]"
    assert event["payload_redacted"]["cookie"] == "[REDACTED]"
    assert event["payload_redacted"]["private_key"] == "[REDACTED]"
    assert event["payload_redacted"]["nested"]["token"] == "[REDACTED]"
    assert event["payload_redacted"]["nested"]["safe"] == "visible"
    assert event["payload_redacted"]["items"][0]["password"] == "[REDACTED]"


def test_brain_001_create_brain_masks_secret_and_preview_routes(client: TestClient) -> None:
    created = client.post(
        "/api/brains",
        json={
            "display_name": "Local test brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:11434",
            "model_name": "test-model",
            "api_key": "placeholder-secret-for-test",
            "is_local": True,
            "context_window": 4096,
        },
    )
    body = created.json()

    assert created.status_code == 200
    assert body["has_api_key"] is True
    assert body["api_key_ref"].startswith("sec_")
    assert "api_key" not in body
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)
    assert "placeholder-secret-for-test" not in audit_text

    preview = client.post(
        "/api/model-routing/preview",
        json={"member_id": "mem_xiaoyao", "text": "解释一下 Context Gateway"},
    ).json()

    assert preview["route"]["primary_brain_id"] == body["brain_id"]
    assert preview["mode"] == "direct"

    binding = client.patch(
        "/api/members/mem_xiaoyao/default-brain",
        json={"brain_id": body["brain_id"]},
    )
    assert binding.status_code == 200
    assert binding.json()["default_brain_id"] == body["brain_id"]


def test_brain_001b_default_brain_binding_rejects_unconfigured_brain(
    client: TestClient,
) -> None:
    binding = client.patch(
        "/api/members/mem_xiaoyao/default-brain",
        json={"brain_id": "brain_not_configured"},
    )

    assert binding.status_code == 409
    assert binding.json()["error"]["code"] == "CONFLICT"


def test_brain_002_cloud_brain_requires_secret(client: TestClient) -> None:
    response = client.post(
        "/api/brains",
        json={
            "display_name": "Cloud test brain",
            "provider": "openai_compatible",
            "endpoint": "https://example.test",
            "model_name": "test-model",
            "is_local": False,
            "allow_cloud": True,
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_brain_003_update_cannot_clear_required_model_config(client: TestClient) -> None:
    created = client.post(
        "/api/brains",
        json={
            "display_name": "Update validation brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:11434",
            "model_name": "test-model",
            "is_local": True,
        },
    ).json()

    clear_endpoint = client.patch(
        f"/api/brains/{created['brain_id']}",
        json={"endpoint": None},
    )
    make_cloud_without_key = client.patch(
        f"/api/brains/{created['brain_id']}",
        json={"is_local": False, "allow_cloud": True, "api_key_ref": None},
    )

    assert clear_endpoint.status_code == 422
    assert make_cloud_without_key.status_code == 422


def test_runtime_001_recover_incomplete_turns_marks_running_failed(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry
    conversation = client.get("/api/chat/conversations").json()["items"][0]

    anyio.run(_create_running_turn, registry, conversation["conversation_id"])
    recovered = anyio.run(registry.chat_service.recover_incomplete_turns)
    turn = client.get("/api/chat/turns/turn_recover_test").json()
    events = client.get("/api/chat/turns/turn_recover_test/events").json()["items"]

    assert recovered == 1
    assert turn["status"] == "failed"
    assert turn["error_code"] == "CHAT_RUNTIME_FAILED"
    assert events[-1]["event_type"] == "turn.failed"


async def _write_redaction_audit(registry) -> None:
    await registry.audit_service.write_event(
        actor_type="system",
        action="test.redaction",
        object_type="audit",
        summary="redaction test",
        payload={
            "api_key": "plain",
            "cookie": "plain",
            "private_key": "plain",
            "nested": {"token": "plain", "safe": "visible"},
            "items": [{"password": "plain"}],
        },
    )


async def _create_running_turn(registry, conversation_id: str) -> None:
    now = "2026-01-01T00:00:00+00:00"
    trace_id = await registry.trace_service.start_trace(
        conversation_id=conversation_id,
        turn_id="turn_recover_test",
    )
    await registry.chat.insert_message(
        message_id="msg_recover_user",
        conversation_id=conversation_id,
        turn_id="turn_recover_test",
        author_type="user",
        author_id="user_local_owner",
        content_type="text",
        content_text="recover me",
        content={"type": "text", "text": "recover me"},
        trace_id=trace_id,
        created_at=now,
    )
    await registry.chat.insert_turn(
        turn_id="turn_recover_test",
        conversation_id=conversation_id,
        member_id="mem_xiaoyao",
        user_message_id="msg_recover_user",
        trace_id=trace_id,
        status="running",
        retry_of_turn_id=None,
        created_at=now,
    )


def _parse_sse(raw: str) -> list[dict]:
    events: list[dict] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
