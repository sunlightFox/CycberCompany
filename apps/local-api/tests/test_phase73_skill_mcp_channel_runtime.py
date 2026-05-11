from __future__ import annotations

from typing import Any, cast

import anyio
from core_types import ChatTurnResponse, RiskLevel
from fastapi.testclient import TestClient

from app.schemas.skills import BundleInstallRequest, SkillMatchRequest


def test_phase73_skill_plugin_service_delegates_to_runtime_layers(
    client: TestClient,
    monkeypatch,
) -> None:
    registry = cast(Any, client.app).state.registry
    service = registry.skill_plugin_service
    captured: list[str] = []

    async def fake_install(request: Any, *, trace_id: str | None = None) -> Any:
        del request, trace_id
        captured.append("install")
        return ("bundle", [], "preview")

    async def fake_list_skills(status: str | None = None) -> Any:
        captured.append(f"list:{status}")
        return []

    async def fake_match(request: Any, *, trace_id: str | None = None) -> Any:
        del request, trace_id
        captured.append("match")
        return []

    async def fake_run(skill_id: str, **kwargs: Any) -> Any:
        del skill_id, kwargs
        captured.append("run")
        return {"skill_run_id": "skr_phase73"}

    async def fake_eval(skill_id: str, *, trace_id: str | None = None) -> Any:
        del skill_id, trace_id
        captured.append("eval")
        return {"eval_run_id": "seval_phase73"}

    monkeypatch.setattr(service._installer, "install_bundle", fake_install)
    monkeypatch.setattr(service._registry, "list_skills", fake_list_skills)
    monkeypatch.setattr(service._runtime, "match", fake_match)
    monkeypatch.setattr(service._runtime, "run", fake_run)
    monkeypatch.setattr(service._eval_runtime, "run_eval", fake_eval)

    assert anyio.run(
        lambda: service.install_bundle(BundleInstallRequest(source_uri="skill://phase73"))
    ) == ("bundle", [], "preview")
    assert anyio.run(service.list_skills, "enabled") == []
    assert anyio.run(lambda: service.match_skills(SkillMatchRequest(goal="phase73 skill match"))) == []
    assert anyio.run(
        lambda: service.run_skill(
            "skill_phase73",
            task_id=None,
            step_id=None,
            owner_member_id="mem_xiaoyao",
            input_data={"goal": "phase73"},
        )
    ) == {"skill_run_id": "skr_phase73"}
    assert anyio.run(lambda: service.run_eval("skill_phase73")) == {"eval_run_id": "seval_phase73"}

    assert captured == ["install", "list:enabled", "match", "run", "eval"]


def test_phase73_mcp_conversation_bridge_exposes_conversation_events_and_approvals(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    created = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase73-mcp-conv",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "帮我规划今天的开发"},
        },
    ).json()
    client.get(created["stream_url"])

    conversations = anyio.run(registry.mcp_service.list_conversations)
    detail = anyio.run(registry.mcp_service.read_conversation, conversation["conversation_id"])
    turn_events = anyio.run(lambda: registry.mcp_service.poll_events(turn_id=created["turn_id"]))

    task = client.post("/api/tasks", json={"goal": "phase73 mcp approval", "auto_start": False}).json()
    approval = anyio.run(
        lambda: registry.approval_service.create_approval(
            task_id=task["task_id"],
            organization_id="org_default",
            requested_action="phase73 action",
            risk_level=RiskLevel.R3,
            summary="phase73 approval",
            payload={"phase": 73},
        )
    )
    approvals = anyio.run(registry.mcp_service.list_approvals, task["task_id"])
    resolved = anyio.run(
        lambda: registry.mcp_service.respond_approval(
            approval_id=approval.approval_id,
            decision="approve",
            actor_member_id="mem_xiaoyao",
            reason="phase73",
        )
    )

    assert any(item["conversation_id"] == conversation["conversation_id"] for item in conversations)
    assert detail["conversation"]["conversation_id"] == conversation["conversation_id"]
    assert detail["messages"]
    assert turn_events
    assert turn_events[0]["source"] == "chat"
    assert any(item["approval_id"] == approval.approval_id for item in approvals)
    assert resolved.status == "approved"


def test_phase73_channel_stream_and_approval_bridges_are_runtime_native(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    bridge = registry.wechat_gateway_service._stream_bridge
    approval_bridge = registry.wechat_gateway_service._approval_bridge

    payload = bridge.deliver_chat_events(
        {
            "message_id": "msg_phase73",
            "turn_id": "turn_phase73",
            "content_text": "fallback text",
            "content": {
                "response_plan": {
                    "plain_text": "final visible text",
                    "summary": "summary text",
                    "action_buttons": [{"code": "approve", "label": "确认"}],
                    "approval_prompt": {"status": "waiting_approval"},
                }
            },
            "voice_metadata": {},
        }
    )
    rendered = approval_bridge.render_pending_action(
        response_plan=payload["response_plan"],
        task_status={"status": "waiting_approval"},
    )

    assert payload["plain_text"] == "final visible text"
    assert rendered["status"] == "waiting_approval"
    assert rendered["action_buttons"]


def test_phase73_channel_session_context_does_not_share_state_across_sessions(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    session_context = registry.wechat_gateway_service._session_context_runtime

    first = session_context.build_inbound(
        provider="wechat",
        session={"session_id": "sess_a", "conversation_id": "conv_a", "member_id": "mem_xiaoyao"},
        channel_message_id="msg_a",
        raw_payload={"sender_label": "A", "thread_ref": "th_a"},
        ui_mode="wechat_chat",
    )
    second = session_context.build_inbound(
        provider="wechat",
        session={"session_id": "sess_b", "conversation_id": "conv_b", "member_id": "mem_xiaoyao"},
        channel_message_id="msg_b",
        raw_payload={"sender_label": "B", "thread_ref": "th_b"},
        ui_mode="wechat_chat",
    )

    assert first["session_id"] == "sess_a"
    assert second["session_id"] == "sess_b"
    assert first["thread_ref"] == "th_a"
    assert second["thread_ref"] == "th_b"
    assert first is not second


def test_phase73_runtime_topology_exposes_skill_mcp_and_channel_bridges(client: TestClient) -> None:
    body = client.get("/api/system/runtime-topology").json()
    items = {item["name"]: item for item in body["items"]}

    assert items["skill"]["details"]["execution"] == "skill_runtime"
    assert items["mcp"]["details"]["conversation_bridge"] == "mcp_conversation_bridge"
    assert items["wechat_gateway"]["details"]["session_context_runtime"] == "channel_session_context"
    assert items["feishu_gateway"]["details"]["stream_bridge"] == "channel_stream_bridge"
