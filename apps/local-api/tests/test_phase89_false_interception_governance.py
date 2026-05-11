from __future__ import annotations

import json
from typing import Any

from app.services import chat as chat_module
from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from fastapi.testclient import TestClient


async def _fake_stream_chat(
    self: Any,
    request: ModelChatRequest,
    cancel_token: CancelToken,
):
    del self, cancel_token
    user_text = str(request.messages[-1]["content"])
    if "对比闲聊、任务、工具三种回复风格的差异" in user_text:
        reply = "闲聊重在接住语气；任务重在目标、步骤、风险；工具回复重在边界、状态和下一步。"
    elif "继续刚才的话题" in user_text:
        reply = "补上验收：首句命中当前目标，证据不足不乱猜，涉及执行先说明边界与下一步。"
    elif "改成只讨论聊天主链路" in user_text:
        reply = "收到，后面只讨论聊天主链路，我会优先围绕回复质量、上下文和执行边界来答。"
    elif "就是这个，继续" in user_text:
        reply = "我先按当前输入继续，但还缺一个明确对象；你补一句目标，我就能继续收敛。"
    else:
        reply = "正常继续主链。"
    yield ModelStreamEvent(event="started")
    yield ModelStreamEvent(event="delta", text=reply)
    yield ModelStreamEvent(event="completed", usage={"output_tokens": len(reply)})


def test_phase89_plain_analysis_and_continuation_return_to_mainline(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", _fake_stream_chat)
    brain_id = _create_local_brain(client)
    client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    conversation_id = _conversation_id(client)

    analysis_created = _create_turn(
        client,
        conversation_id,
        "phase89-analysis",
        "对比闲聊、任务、工具三种回复风格的差异。",
    )
    analysis_events = _parse_sse(client.get(analysis_created["stream_url"]).text)
    analysis_completed = next(
        item for item in analysis_events if item["event"] == "response.completed"
    )
    analysis_text = analysis_completed["payload"]["response_plan"]["plain_text"]
    analysis_presence = analysis_completed["payload"]["response_plan"]["structured_payload"][
        "presence_runtime"
    ]

    continuation_created = _create_turn(
        client,
        conversation_id,
        "phase89-continuation",
        "继续刚才的话题，补上每条原则对应的验收方式。",
    )
    continuation_events = _parse_sse(client.get(continuation_created["stream_url"]).text)
    continuation_completed = next(
        item for item in continuation_events if item["event"] == "response.completed"
    )
    continuation_text = continuation_completed["payload"]["response_plan"]["plain_text"]
    continuation_presence = continuation_completed["payload"]["response_plan"][
        "structured_payload"
    ]["presence_runtime"]
    intent_reason_codes = [
        reason
        for event in continuation_events
        if event["event"] == "intent.detected"
        for reason in event.get("payload", {}).get("reason_codes", [])
    ]

    assert "闲聊" in analysis_text and "任务" in analysis_text and "工具" in analysis_text
    assert "plain_analysis_request" in analysis_presence["heuristic_reason_codes"]["soft_heuristic"]
    assert "验收" in continuation_text
    assert "explicit_continuation" in continuation_presence["heuristic_reason_codes"]["soft_heuristic"]
    assert "pending_clarification_followup" not in intent_reason_codes
    assert "deterministic_execution_state_reply" not in intent_reason_codes


def test_phase89_latest_instruction_override_no_longer_direct_shortcuts(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", _fake_stream_chat)
    brain_id = _create_local_brain(client)
    client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    conversation_id = _conversation_id(client)

    created = _create_turn(
        client,
        conversation_id,
        "phase89-latest",
        "我们先讨论知识库，改成只讨论聊天主链路。",
    )
    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(item for item in events if item["event"] == "response.completed")
    plain_text = completed["payload"]["response_plan"]["plain_text"]
    presence_runtime = completed["payload"]["response_plan"]["structured_payload"][
        "presence_runtime"
    ]
    intent_reason_codes = [
        reason
        for event in events
        if event["event"] == "intent.detected"
        for reason in event.get("payload", {}).get("reason_codes", [])
    ]

    assert "聊天主链路" in plain_text
    assert "这轮生成失败了" not in plain_text
    assert "没有可用模型路由" not in plain_text
    assert "latest_instruction_override" in presence_runtime["heuristic_reason_codes"]["soft_heuristic"]
    assert "latest_instruction_override_direct_reply" not in intent_reason_codes


def test_phase89_pending_clarification_shortcut_requires_real_pending_state(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", _fake_stream_chat)
    brain_id = _create_local_brain(client)
    client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    conversation_id = _conversation_id(client)

    created = _create_turn(client, conversation_id, "phase89-no-pending", "就是这个，继续。")
    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(item for item in events if item["event"] == "response.completed")
    plain_text = completed["payload"]["response_plan"]["plain_text"]
    presence_runtime = completed["payload"]["response_plan"]["structured_payload"][
        "presence_runtime"
    ]
    intent_reason_codes = [
        reason
        for event in events
        if event["event"] == "intent.detected"
        for reason in event.get("payload", {}).get("reason_codes", [])
    ]

    assert "继续" in plain_text or "目标" in plain_text
    assert "clarification_followup_candidate" in presence_runtime["heuristic_reason_codes"]["soft_heuristic"]
    assert "pending_clarification_followup" not in intent_reason_codes


def test_phase89_hard_boundary_and_readiness_release_summary_are_visible(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase89-boundary",
        "请通过浏览器打开 file://C:/Users/Administrator/secret.txt",
    )
    events = _parse_sse(client.get(created["stream_url"]).text)
    reason_codes = [
        reason
        for event in events
        if event["event"] == "intent.detected"
        for reason in event.get("payload", {}).get("reason_codes", [])
    ]
    completed = next(item for item in events if item["event"] == "response.completed")
    readiness = client.get("/api/system/chat-mainline-readiness").json()

    gate = client.post("/api/release-gates", json={}).json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    phase89 = readiness["phase_readiness"]["phase89_false_interception_governance"]
    release_phase89 = report["summary"]["phase89_false_interception_governance"]

    assert "deterministic_boundary_reply" in reason_codes
    assert "file://" in completed["payload"]["response_plan"]["plain_text"]
    assert phase89["details"]["phase89_contract_version"] == "phase89.false_interception_governance.v1"
    assert "wechat_20_summary" in phase89["details"]
    assert "false_boundary_rate" in release_phase89
    assert "natural_continuation_pass_rate" in release_phase89
    assert "runtime_failure_visible_leakage_count" in release_phase89
    assert release_phase89["runtime_failure_visible_leakage_count"] == 0
    assert release_phase89["wechat_20_scenarios_passed"] is True
    assert release_phase89["strict_format_continuity_gate"] == "pass"


def _create_local_brain(client: TestClient) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": "Phase89 local brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "phase89-test-model",
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
