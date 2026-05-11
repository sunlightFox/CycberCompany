from __future__ import annotations

import json
from typing import Any

from app.services import chat as chat_module
from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from fastapi.testclient import TestClient


async def _phase90_stream_chat(
    self: Any,
    request: ModelChatRequest,
    cancel_token: CancelToken,
):
    del self, cancel_token
    prompt = str(request.messages[-1]["content"])
    if "用表格比较 REST" in prompt:
        assert "只输出 JSON" not in prompt
        reply = (
            "| 方案 | 适用场景 |\n"
            "| --- | --- |\n"
            "| REST | 通用 CRUD、团队协作成本低 |\n"
            "| GraphQL | 前端按需取数 |\n"
            "| gRPC | 内部高性能服务调用 |"
        )
    elif "只输出 JSON" in prompt:
        reply = '{"conclusion":"聊天质量要先稳住自然度和边界一致性","risks":["回复模板腔偏重"]}'
    else:
        reply = "phase90"
    yield ModelStreamEvent(event="started")
    yield ModelStreamEvent(event="delta", text=reply)
    yield ModelStreamEvent(event="completed", usage={"output_tokens": len(reply)})


def test_phase90_readiness_and_release_summary_expose_removal_gate_truth(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase90 = readiness["phase_readiness"]["phase90_compat_cleanup_release_gate"]

    gate = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    report = client.get(f"/api/release-gates/{gate}/report").json()["summary"]
    phase90_release = report["phase90_compat_cleanup_release_gate"]

    assert phase90["status"] == "ready"
    assert phase90["details"]["phase90_contract_version"] == "phase90.compat_cleanup_release_gate.v1"
    assert phase90["details"]["minimum_suite"]
    assert phase90_release["status"] == "ready"
    assert phase90_release["minimum_suite_passed"] is True
    assert phase90_release["strict_format_continuity_gate"] == "pass"
    assert phase90_release["visible_leakage_gate"] is True
    assert phase90_release["no_turn_gate"] is True
    assert phase90_release["duplicate_inbound_gate"] is True
    assert phase90_release["wrong_conversation_reuse_gate"] is True
    assert all(phase90_release["removal_gate_status_by_component"].values())


def test_phase90_runtime_topology_marks_retained_shells_and_removed_internal_compat(
    client: TestClient,
) -> None:
    items = {
        item["name"]: item for item in client.get("/api/system/runtime-topology").json()["items"]
    }
    for name in [
        "chat_service",
        "wechat_gateway",
        "feishu_gateway",
        "chat_response_helper",
        "chat_model_helper",
        "chat_context_helper",
    ]:
        cleanup = items[name]["details"]["cleanup"]
        assert cleanup["public_shell_retained"] is True
        assert cleanup["internal_compat_removed"] is True
        assert cleanup["retained_only_for_api_or_fixture_contract"] is True


def test_phase90_chat_service_facade_retained_without_phase89_compat_methods(
    client: TestClient,
) -> None:
    service = client.app.state.registry.chat_service

    assert hasattr(service, "create_turn")
    assert hasattr(service, "stream_turn_events")
    assert hasattr(service, "run_turn")
    assert hasattr(service, "recover_incomplete_turns")
    assert hasattr(service, "cancel_turn")
    assert hasattr(service, "retry_turn")
    assert hasattr(service, "placeholder_events")
    assert not hasattr(service, "_deterministic_execution_state_reply_text")
    assert not hasattr(service, "_deterministic_latest_instruction_reply_text")
    assert not hasattr(service, "_maybe_handle_pending_clarification_followup")


def test_phase90_format_sensitive_request_does_not_inherit_prior_json_shape(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", _phase90_stream_chat)
    brain_id = _create_local_brain(client)
    client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    conversation_id = _conversation_id(client)

    created_json = _create_turn(
        client,
        conversation_id,
        "phase90-json",
        "只输出 JSON，字段只有 conclusion 和 risks。",
    )
    json_events = _parse_sse(client.get(created_json["stream_url"]).text)
    json_completed = next(item for item in json_events if item["event"] == "response.completed")
    assert json_completed["payload"]["response_plan"]["plain_text"].startswith("{")

    created_table = _create_turn(
        client,
        conversation_id,
        "phase90-table",
        "用表格比较 REST、GraphQL、gRPC 的适用场景。",
    )
    table_events = _parse_sse(client.get(created_table["stream_url"]).text)
    table_completed = next(
        item for item in table_events if item["event"] == "response.completed"
    )
    plain_text = table_completed["payload"]["response_plan"]["plain_text"]

    assert "| REST |" in plain_text
    assert plain_text.startswith("| 方案 |")


def _create_local_brain(client: TestClient) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": "Phase90 local brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "phase90-test-model",
            "is_local": True,
            "context_window": 4096,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["brain_id"])


def _conversation_id(client: TestClient) -> str:
    response = client.get("/api/chat/conversations")
    assert response.status_code == 200, response.text
    return str(response.json()["items"][0]["conversation_id"])


def _create_turn(
    client: TestClient,
    conversation_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/chat/turn",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": conversation_id,
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
