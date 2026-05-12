from __future__ import annotations

import json
from typing import Any, cast

import anyio
from app.core.errors import AppError
from core_types import ErrorCode
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_phase54_wechat_gateway_full_link import (
    GatewayWechatClient,
    _bind_real_wechat,
    _insert_completed_turn as _insert_wechat_completed_turn,
    _install_fake_wechat,
    _pair_peer,
    _text_event as _wechat_text_event,
)
from test_phase66_feishu_channel import (
    _insert_completed_turn as _insert_feishu_completed_turn,
    _install_fake_feishu,
    _text_event as _feishu_text_event,
)


def test_phase84_local_direct_runtime_and_visibility_acceptance(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[str] = []
    original_execute = client.app.state.registry.tool_runtime.execute

    async def _tracked_execute(request: Any, trace_id: str | None = None) -> Any:
        calls.append(str(request.tool_name))
        return await original_execute(request, trace_id=trace_id)

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", _tracked_execute)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase84-direct-acceptance",
        "请帮我操作桌面窗口，把当前桌面上的记事本窗口最小化，然后告诉我结果。",
    )
    events = _parse_sse(client.get(created["stream_url"]).text)
    event_names = [item["event"] for item in events]
    completed = next(item for item in events if item["event"] == "response.completed")
    plan = completed["payload"]["response_plan"]
    assistant = _assistant_message(client, conversation_id, created["turn_id"])

    assert not calls
    assert "context.ready" in event_names
    assert "response.completed" in event_names
    assert event_names[-1] == "turn.completed"
    assert "tool.completed" not in event_names
    assert "task.created" not in event_names
    assert assistant["content_text"] == plan["plain_text"]
    assert "trace_id" not in plan["plain_text"].lower()
    assert "approval_id" not in plan["plain_text"].lower()
    assert "tool_call_id" not in plan["plain_text"].lower()


def test_phase84_tool_loop_acceptance_and_hook_timeline_visible(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_dispatch(request: Any, trace_id: str | None = None) -> Any:
        del trace_id
        assert request.tool_name == "browser.snapshot"
        return type(
            "ToolResponse",
            (),
            {
                "result": {
                    "title": "Phase84 页面",
                    "url": "https://example.test/phase84",
                    "http_status": 200,
                    "browser_evidence_id": "bev_phase84",
                    "content_preview": "<html><body><h1>Phase84 页面</h1></body></html>",
                    "browser_page_state": {
                        "status": "observed",
                        "page_title": "Phase84 页面",
                        "evidence_refs": [
                            {"type": "browser_evidence", "action": "snapshot", "id": "bev_phase84"}
                        ],
                    },
                },
                "tool_call": type(
                    "ToolCall",
                    (),
                    {
                        "tool_call_id": "call_phase84_browser",
                        "risk_level": type("Risk", (), {"value": "R2"})(),
                        "status": "completed",
                    },
                )(),
                "approval": None,
                "artifacts": [],
            },
        )()

    monkeypatch.setattr(client.app.state.registry.tool_runtime._dispatcher, "execute", fake_dispatch)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase84-tool-loop",
        "帮我看一下这个网页讲了什么：https://example.test/phase84",
    )
    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(item for item in events if item["event"] == "response.completed")
    payload = completed["payload"]["response_plan"]["structured_payload"]
    registry = cast(FastAPI, client.app).state.registry
    timeline = anyio.run(registry.chat.list_run_ledgers, created["turn_id"])
    event_types = {item["event_type"] for item in timeline}

    assert payload["tool_result_context"]["status"] == "completed_with_evidence"
    assert payload["action_status_semantics"]["status"] == "completed_with_evidence"
    assert payload["route_semantics"]["tool_loop"] is True
    assert payload["tool_result_context"]["evidence_refs"]
    assert "hook.before_tool_call" in event_types
    assert "hook.after_tool_call" in event_types
    assert "response.completed" in event_types


def test_phase84_approval_pending_honesty_and_channel_continuity_smoke(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_dispatch(request: Any, trace_id: str | None = None) -> Any:
        del trace_id
        if request.tool_name == "terminal.run":
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "approval_state": {
                            "status": "required",
                            "approval_id": "apr_phase84_terminal",
                        },
                        "execution_semantics": {
                            "lane": "readonly",
                            "command_class": "readonly",
                        },
                        "retryable": False,
                    },
                    "tool_call": type(
                        "ToolCall",
                        (),
                        {
                            "tool_call_id": "call_phase84_terminal_pending",
                            "risk_level": type("Risk", (), {"value": "R4"})(),
                            "status": "approval_required",
                        },
                    )(),
                    "approval": type(
                        "Approval",
                        (),
                        {
                            "approval_id": "apr_phase84_terminal",
                            "summary": "需要确认后才能执行",
                            "model_dump": lambda self, mode="json": {
                                "approval_id": "apr_phase84_terminal",
                                "status": "required",
                                "summary": "需要确认后才能执行",
                            },
                        },
                    )(),
                    "artifacts": [],
                },
            )()
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_dispatch)
    conversation_id = _conversation_id(client)
    created = _create_turn(client, conversation_id, "phase84-approval", '执行命令: "dir"')
    events = _parse_sse(client.get(created["stream_url"]).text)
    completed = next(item for item in events if item["event"] == "response.completed")
    plan = completed["payload"]["response_plan"]

    assert "tool.completed" not in {event["event"] for event in events}
    assert plan["structured_payload"]["tool_result_context"]["status"] == "waiting_for_approval"
    assert plan["structured_payload"]["action_status_semantics"]["status"] == "waiting_for_approval"
    assert "还没有执行" in plan["plain_text"] or "等待确认" in plan["plain_text"]

    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase84-peer-secret")
    registry = cast(Any, client.app).state.registry
    captured_wechat: list[Any] = []

    async def fake_wechat_submit_channel_turn(**kwargs: Any) -> Any:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        captured_wechat.append(request)
        return await _insert_wechat_completed_turn(
            registry,
            request,
            assistant_text="第八十四阶段微信 smoke 正常。",
            conversation_id=request.conversation_id or "conv_phase84_wechat",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = (
        fake_wechat_submit_channel_turn
    )
    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase84-wechat-1", "wxid-phase84-peer-secret", "第一条"),
    ]
    first = client.post("/api/channels/providers/wechat/poll-once")
    assert first.status_code == 200, first.text
    assert first.json()["chat_turns_created"] == 1

    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase84-wechat-1", "wxid-phase84-peer-secret", "第一条"),
    ]
    duplicate = client.post("/api/channels/providers/wechat/poll-once")
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["duplicate_events"] == 1

    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase84-wechat-2", "wxid-phase84-peer-secret", "第二条"),
    ]
    second = client.post("/api/channels/providers/wechat/poll-once")
    assert second.status_code == 200, second.text
    assert second.json()["chat_turns_created"] == 1
    assert captured_wechat[-1].session_id == captured_wechat[0].session_id
    assert captured_wechat[-1].conversation_id == "conv_phase84_wechat"

    fake_feishu = _install_fake_feishu(client)
    started = client.post(
        "/api/channels/bind-sessions",
        json={
            "provider": "feishu",
            "requested_by_member_id": "mem_xiaoyao",
            "display_name_hint": "飞书机器人",
        },
    )
    assert started.status_code == 200, started.text
    started_payload = started.json()
    assert started_payload["status"] == "qr_ready"
    callback = client.get(
        "/api/channels/inbound/feishu/bind-callback",
        params={
            "state": started_payload["bind_session_id"],
            "code": "phase84-oauth-code",
            "tenant_key": "tenant_phase84_secret",
            "open_id": "ou_phase84_secret",
        },
    )
    assert callback.status_code == 200, callback.text
    finalized = client.post(
        f"/api/channels/bind-sessions/{started_payload['bind_session_id']}/finalize"
    )
    assert finalized.status_code == 200, finalized.text
    binding = finalized.json()["account"]
    captured_feishu: list[Any] = []

    async def fake_feishu_submit_channel_turn(**kwargs: Any) -> Any:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        captured_feishu.append(request)
        return await _insert_feishu_completed_turn(
            registry,
            request,
            assistant_text="第八十四阶段飞书 smoke 正常。",
            conversation_id=request.conversation_id or "conv_phase84_feishu",
        )

    registry.feishu_gateway_service._channel_ingress_runtime.submit_channel_turn = (
        fake_feishu_submit_channel_turn
    )
    fake_feishu.enqueue_event(
        _feishu_text_event("evt-phase84-feishu-unknown", "oc_phase84", "ou_sender", "你好")
    )
    first_feishu = client.post("/api/channels/providers/feishu/poll-once")
    assert first_feishu.status_code == 200, first_feishu.text
    assert first_feishu.json()["created_pairing_requests"] == 1
    pairings = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "feishu", "status": "pending"},
    )
    assert pairings.status_code == 200, pairings.text
    pairing = pairings.json()["items"][0]
    approved = client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaoyao", "reason": "phase84"},
    )
    assert approved.status_code == 200, approved.text

    fake_feishu.enqueue_event(
        _feishu_text_event("evt-phase84-feishu-1", "oc_phase84", "ou_sender", "请回复")
    )
    routed = client.post("/api/channels/providers/feishu/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1
    assert captured_feishu[-1].session_id
    assert binding["channel_account_id"]


def test_phase84_recovery_and_release_summary_acceptance(
    client: TestClient,
    monkeypatch,
) -> None:
    registry = cast(Any, client.app).state.registry
    original_execute = registry.tool_runtime.execute
    calls = {"count": 0}

    async def flaky_execute(request: Any, *, trace_id: str | None = None) -> Any:
        if request.tool_name == "knowledge.search" and calls["count"] == 0:
            calls["count"] += 1
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "temporary tool failure token=phase84-secret",
                status_code=500,
            )
        return await original_execute(request, trace_id=trace_id)

    monkeypatch.setattr(registry.tool_runtime, "execute", flaky_execute)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase84-recovery",
        "请调研 phase84 恢复链路并输出任务报告",
    )
    events = _parse_sse(client.get(created["stream_url"]).text)

    event_names = [event["event"] for event in events]
    assert "turn.recovery_started" in event_names
    assert "turn.recovery_completed" in event_names
    assert "turn.failed" not in event_names

    gate = client.post("/api/release-gates", json={}).json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase84 = readiness["phase_readiness"]["phase84_acceptance_matrix"]
    summary = report["summary"]["chat_mainline_readiness"]

    assert phase84["status"] in {"ready", "partial"}
    assert phase84["details"]["acceptance_matrix_version"] == "phase84.chat_mainline_acceptance_matrix.v1"
    assert "runtime_acceptance" in phase84["details"]["acceptance_groups"]
    assert summary["acceptance_matrix_version"] == "phase84.chat_mainline_acceptance_matrix.v1"
    assert "phase77_runtime_closure" in summary["phase77_to_phase83_statuses"]
    assert "channel_acceptance" in summary["acceptance_groups"]
    assert "phase89_false_interception_governance_status" in summary


def test_phase84_active_run_followup_merges_into_existing_turn(
    client: TestClient,
    monkeypatch,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    monkeypatch.setattr(registry.chat_service._execution, "schedule", lambda *args, **kwargs: None)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase84-steering-followup",
        "先给我整理一个执行方案",
    )
    _mark_turn_running(client, created["turn_id"])

    followup = _create_turn(
        client,
        conversation_id,
        "phase84-steering-followup",
        "另外，再加一句：保留原步骤顺序。",
    )
    assert followup["turn_id"] == created["turn_id"]
    assert followup["status"] == "steering_applied"

    envelope = client.get(f"/api/chat/turns/{created['turn_id']}/envelope").json()
    queue = client.get(f"/api/chat/turns/{created['turn_id']}/queue").json()["item"]
    events = client.get(f"/api/chat/turns/{created['turn_id']}/events").json()["items"]

    assert "保留原步骤顺序" in envelope["model_safe_text"]
    assert envelope["ingress_metadata"]["queue_policy"] == "followup"
    assert queue["queue_policy"] == "followup"
    assert queue["steering_diagnostics"]["control_intent"] == "followup_append"
    assert "turn.steering_detected" in {item["event_type"] for item in events}


def test_phase84_active_run_steer_supersedes_old_turn_and_queues_new_turn(
    client: TestClient,
    monkeypatch,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    monkeypatch.setattr(registry.chat_service._execution, "schedule", lambda *args, **kwargs: None)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase84-steering-replace",
        "先写一版详细说明",
    )
    _mark_turn_running(client, created["turn_id"])

    supersede = _create_turn(
        client,
        conversation_id,
        "phase84-steering-replace",
        "停，改成给我一个三步摘要。",
    )

    assert supersede["turn_id"] != created["turn_id"]
    assert supersede["queue_status"] == "queued"

    original = client.get(f"/api/chat/turns/{created['turn_id']}").json()
    original_events = client.get(f"/api/chat/turns/{created['turn_id']}/events").json()["items"]
    replacement_queue = client.get(f"/api/chat/turns/{supersede['turn_id']}/queue").json()["item"]

    assert original["cancel_requested"] is True
    assert "turn.superseded" in {item["event_type"] for item in original_events}
    assert replacement_queue["queue_policy"] == "steer"
    assert replacement_queue["steering_diagnostics"]["target_turn_id"] == created["turn_id"]


def test_phase84_interrupt_clears_pending_approval_and_records_resume_slot(
    client: TestClient,
    monkeypatch,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    monkeypatch.setattr(registry.chat_service._execution, "schedule", lambda *args, **kwargs: None)
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase84-steering-interrupt",
        "继续执行安装前检查",
    )
    _mark_turn_running(client, created["turn_id"])
    _seed_pending_approval_state(client, conversation_id, created["turn_id"], "phase84-steering-interrupt")

    paused = _create_turn(
        client,
        conversation_id,
        "phase84-steering-interrupt",
        "先别做了，暂停一下。",
    )

    assert paused["turn_id"] == created["turn_id"]
    assert paused["status"] == "cancel_requested"

    state = client.get(f"/api/chat/conversations/{conversation_id}/working-state").json()
    events = client.get(f"/api/chat/turns/{created['turn_id']}/events").json()["items"]

    assert state["pending_approval_action"] == {}
    assert state["pending_execution_resume"]["payload"]["control_intent"] == "pause_current"
    assert "turn.paused" in {item["event_type"] for item in events}


def _create_turn(
    client: TestClient,
    conversation_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _conversation_id(client: TestClient) -> str:
    return str(client.get("/api/chat/conversations").json()["items"][0]["conversation_id"])


def _assistant_message(
    client: TestClient,
    conversation_id: str,
    turn_id: str,
) -> dict[str, Any]:
    detail = client.get(f"/api/chat/conversations/{conversation_id}").json()
    messages = detail["messages"]
    assistant_messages = [
        message
        for message in messages
        if message.get("turn_id") == turn_id and message.get("author_type") == "assistant"
    ]
    assert assistant_messages
    return assistant_messages[-1]


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    portal = client.portal
    assert portal is not None
    if args or kwargs:
        return portal.call(lambda: func(*args, **kwargs))
    return portal.call(func)


def _mark_turn_running(client: TestClient, turn_id: str) -> None:
    registry = cast(FastAPI, client.app).state.registry
    _run_async(
        client,
        registry.chat.update_queue_item,
        turn_id,
        status="running",
        updated_at="2026-01-01T00:00:00+00:00",
        started_at="2026-01-01T00:00:00+00:00",
    )
    _run_async(
        client,
        registry.chat.update_turn,
        turn_id,
        status="running",
        updated_at="2026-01-01T00:00:00+00:00",
    )


def _seed_pending_approval_state(
    client: TestClient,
    conversation_id: str,
    turn_id: str,
    session_id: str,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    _run_async(
        client,
        registry.chat.upsert_working_state,
        {
            "conversation_id": conversation_id,
            "organization_id": "org_default",
            "active_topic": "phase84 approval",
            "user_goal": "等待审批",
            "known_constraints": [],
            "decisions_made": [],
            "open_questions": [],
            "candidate_actions": [],
            "referenced_artifacts": [],
            "last_response_summary": "waiting approval",
            "pending_confirmation": {},
            "pending_clarification": {},
            "pending_approval_action": {
                "kind": "approval",
                "session_id": session_id,
                "source_turn_id": turn_id,
                "status": "active",
                "payload": {"approval_id": "apr_phase84", "actions": [{"approval_id": "apr_phase84"}]},
            },
            "pending_execution_resume": {},
            "session_id": session_id,
            "source_turn_id": turn_id,
            "source_message_fingerprint": "sha256:phase84",
            "confidence": 0.8,
            "status": "active",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        },
    )
