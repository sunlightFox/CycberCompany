from __future__ import annotations

import anyio
from typing import Any, cast

from fastapi.testclient import TestClient


def test_phase70_system_runtime_topology_exposes_runtime_layers(client: TestClient) -> None:
    response = client.get("/api/system/runtime-topology")
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    by_name = {item["name"]: item for item in items}

    assert by_name["session"]["runtime"] == "session_runtime"
    assert by_name["chat_hook_runtime"]["runtime"] == "chat_hook_runtime"
    assert by_name["chat_execution_batches"]["runtime"] == "chat_execution_batches_control_plane"
    assert by_name["channel_ingress"]["runtime"] == "channel_ingress_runtime"
    assert by_name["wechat_gateway"]["runtime"] == "wechat_gateway"
    assert by_name["feishu_gateway"]["runtime"] == "feishu_gateway"
    assert by_name["task"]["runtime"] == "task_runtime"
    assert by_name["tool"]["runtime"] == "tool_runtime"
    assert by_name["browser_workflow"]["runtime"] == "browser_workflow_runtime"
    assert by_name["mcp"]["runtime"] == "mcp_runtime"
    assert by_name["skill"]["runtime"] == "skill_runtime"
    assert by_name["release"]["runtime"] == "release_gate_runtime"
    assert by_name["skill_promotion"]["runtime"] == "skill_promotion_runtime"

    assert by_name["session"]["status"] == "runtime_native"
    assert by_name["chat_hook_runtime"]["status"] == "runtime_native"
    assert by_name["channel_ingress"]["status"] == "runtime_native"
    assert by_name["wechat_gateway"]["status"] == "compat_shell"
    assert by_name["feishu_gateway"]["status"] == "compat_shell"
    assert "turn_execution_manager" in by_name["session"]["dependencies"]
    assert "chat_runtime" in by_name["session"]["dependencies"]
    assert by_name["task"]["details"]["entry_mode"] == "runtime_single_track"
    assert "task_resume_runtime" in by_name["task"]["dependencies"]
    assert "tool_terminal_runtime" in by_name["tool"]["dependencies"]
    assert "terminal_queue_service" in by_name["tool"]["dependencies"]
    assert "browser_session_runtime" in by_name["browser_workflow"]["dependencies"]
    assert "browser_page_state_runtime" in by_name["browser_workflow"]["dependencies"]
    assert "browser_replay_store" in by_name["browser_workflow"]["dependencies"]
    assert by_name["browser_workflow"]["status"] == "runtime_native"
    assert by_name["browser_workflow"]["details"]["observe_act_split"] is True
    assert by_name["tool"]["details"]["terminal"]["maturity"] == "runtime_native"
    assert (
        by_name["tool"]["details"]["terminal"]["execution_mode"]
        == "queued_sandboxed_sync"
    )
    assert by_name["tool"]["details"]["terminal"]["queue_enabled"] is True
    assert by_name["tool"]["details"]["terminal"]["lane_model"] == "in_memory_lanes_v1"
    assert by_name["mcp"]["details"]["policy"]["taint_enforced"] is True
    assert by_name["mcp"]["details"]["conversation_bridge"] == "mcp_conversation_bridge"
    assert "events_poll_wait" in by_name["mcp"]["details"]["bridge_capabilities"]
    assert by_name["skill"]["details"]["installer"] == "skill_installer"
    assert by_name["skill"]["details"]["registry"] == "skill_registry"
    assert by_name["chat_execution_batches"]["details"]["execution_batches_version"] == (
        "phase85.execution_batches_control_plane.v1"
    )
    assert by_name["chat_execution_batches"]["details"]["next_batch"]
    assert isinstance(by_name["chat_execution_batches"]["details"]["covered_batches"], list)
    assert isinstance(by_name["chat_execution_batches"]["details"]["blocked_batches"], list)
    assert by_name["wechat_gateway"]["details"]["stream_bridge"] == "channel_stream_bridge"
    assert by_name["feishu_gateway"]["details"]["approval_bridge"] == "channel_approval_bridge"
    assert "before_tool_call" in by_name["chat_hook_runtime"]["details"]["supported_stages"]
    assert "before_finalize" in by_name["chat_hook_runtime"]["details"]["blocked_stages"]


def test_phase70_session_runtime_and_tool_runtime_diagnostics_are_honest(
    client: TestClient,
) -> None:
    session_runtime = client.get("/api/system/session-runtime")
    assert session_runtime.status_code == 200, session_runtime.text
    session_body = session_runtime.json()
    assert session_body["runtime"] == "session_runtime"
    assert session_body["executor"] == "turn_execution_manager"
    assert session_body["route_source"] == "session_runtime"
    assert session_body["delegates_to"] == "chat_runtime"
    assert session_body["maturity"] == "runtime_native"
    assert "create_turn" in session_body["public_entrypoints"]
    assert session_body["route_selectors"] == [
        "session_runtime_entry_contract",
        "chat_runtime_dispatch",
    ]

    registry = cast(Any, client.app).state.registry
    assert registry.session_runtime._runtime is registry.chat_runtime
    assert not hasattr(registry.channel_ingress_runtime, "_chat")
    assert registry.chat_service._execution._runner.__self__ is registry.chat_runtime
    assert (
        registry.chat_service._execution._runner.__func__
        is registry.chat_runtime.run_turn.__func__
    )

    tool_runtime = client.get("/api/system/tool-runtime")
    assert tool_runtime.status_code == 200, tool_runtime.text
    tool_body = tool_runtime.json()
    assert tool_body["runtime"] == "tool_runtime"
    assert tool_body["dispatcher"] == "tool_dispatcher"
    assert tool_body["safety_bridge"] == "tool_safety_bridge"
    assert tool_body["builtin"]["dispatch_mode"] == "builtin_dispatcher_single_track"
    assert tool_body["asset"]["capability_graph_required"] is True
    assert tool_body["memory"]["trace_safe"] is True
    assert tool_body["terminal"]["approval_required_for_high_risk"] is True
    assert tool_body["terminal"]["maturity"] == "runtime_native"
    assert tool_body["terminal"]["execution_mode"] == "queued_sandboxed_sync"
    assert tool_body["terminal"]["queue_enabled"] is True
    assert tool_body["terminal"]["lane_model"] == "in_memory_lanes_v1"
    assert tool_body["terminal"]["snapshot_supported"] is True
    assert tool_body["terminal"]["reset_supported"] is True
    assert "readonly" in tool_body["terminal"]["lanes"]
    assert tool_body["terminal"]["backend_profile"]["active_backend"] in {
        "windows_job_object",
        "policy_guard",
        "container",
        "windows_low_integrity",
        "disabled",
    }
    assert isinstance(tool_body["terminal"]["backend_profile"]["fallback_chain"], list)


def test_phase70_channel_ingress_runtime_routes_simple_channel_turn_through_session_runtime(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry

    captured: dict[str, Any] = {}

    async def _fake_create_turn(request: Any, *, retry_of_turn_id: str | None = None) -> Any:
        captured["request"] = request
        captured["retry_of_turn_id"] = retry_of_turn_id
        return {"turn_id": "turn_phase70"}

    registry.session_runtime.create_turn = _fake_create_turn

    result = _run_async(
        client,
        registry.channel_ingress_runtime.submit_channel_turn(
            provider="wechat",
            session={
                "session_id": "sess_phase70",
                "conversation_id": "conv_phase70",
                "member_id": "mem_xiaoyao",
            },
            channel_message_id="msg_phase70",
            text="  第二阶段入口归一化  ",
            raw_payload={"kind": "text"},
            ui_mode="chat",
        )
    )

    assert result["turn_id"] == "turn_phase70"
    request = captured["request"]
    assert request.session_id == "sess_phase70"
    assert request.conversation_id == "conv_phase70"
    assert request.member_id == "mem_xiaoyao"
    assert request.input.text == "第二阶段入口归一化"
    assert request.ingress_metadata.channel == "wechat"
    assert request.ingress_metadata.channel_message_id == "msg_phase70"


def test_phase70_chat_routes_use_session_runtime_entrypoints(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    captured: list[tuple[str, Any]] = []

    async def _fake_create_turn(request: Any, *, retry_of_turn_id: str | None = None) -> Any:
        captured.append(("create", request))
        return {
            "turn_id": "turn_phase70_route",
            "conversation_id": "conv_phase70_route",
            "message_id": "msg_phase70_route",
            "assistant_message_id": None,
            "task_id": None,
            "trace_id": "trace_phase70_route",
            "status": "created",
            "stream_url": "/api/chat/stream/turn_phase70_route",
            "queue_status": "created",
            "envelope_id": None,
        }

    async def _fake_cancel_turn(turn_id: str) -> Any:
        captured.append(("cancel", turn_id))
        return {
            "turn_id": turn_id,
            "conversation_id": "conv_phase70_route",
            "message_id": "msg_phase70_route",
            "assistant_message_id": None,
            "task_id": None,
            "trace_id": "trace_phase70_route",
            "status": "cancelled",
            "stream_url": f"/api/chat/stream/{turn_id}",
        }

    async def _fake_retry_turn(turn_id: str) -> Any:
        captured.append(("retry", turn_id))
        return {
            "turn_id": "turn_phase70_retry",
            "conversation_id": "conv_phase70_route",
            "message_id": "msg_phase70_route",
            "assistant_message_id": None,
            "task_id": None,
            "trace_id": "trace_phase70_retry",
            "status": "created",
            "stream_url": "/api/chat/stream/turn_phase70_retry",
        }

    registry.session_runtime.create_turn = _fake_create_turn
    registry.session_runtime.cancel_turn = _fake_cancel_turn
    registry.session_runtime.retry_turn = _fake_retry_turn

    created = client.post(
        "/api/chat/turn",
        json={
            "session_id": "sess_phase70_route",
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "phase70 route"},
        },
    )
    assert created.status_code == 200, created.text
    assert captured[0][0] == "create"

    assert client.post("/api/chat/turns/turn_phase70_route/cancel").status_code == 200
    assert client.post("/api/chat/turns/turn_phase70_route/retry").status_code == 200
    assert any(kind == "cancel" for kind, _payload in captured)
    assert any(kind == "retry" for kind, _payload in captured)


def test_phase70_channel_ingress_runtime_routes_rich_turn_payload(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry

    captured: dict[str, Any] = {}

    async def _fake_create_turn(request: Any, *, retry_of_turn_id: str | None = None) -> Any:
        captured["request"] = request
        return {"turn_id": "turn_phase70_rich"}

    registry.session_runtime.create_turn = _fake_create_turn

    result = _run_async(
        client,
        registry.channel_ingress_runtime.submit_channel_turn(
            provider="wechat",
            session={
                "session_id": "sess_phase70_rich",
                "conversation_id": "conv_phase70_rich",
                "member_id": "mem_xiaoyao",
            },
            channel_message_id="msg_phase70_rich",
            text="多模态入口",
            raw_payload={"kind": "rich"},
            ui_mode="wechat_chat",
            input_type="multi_part",
            content_parts=[],
            attachments=[
                {
                    "name": "voice.wav",
                    "content_type": "audio/wav",
                    "uri": "blob://voice.wav",
                    "metadata": {"untrusted_external_content": True},
                }
            ],
            context_refs=[
                {
                    "type": "url",
                    "uri": "https://example.com",
                    "label": "example",
                    "metadata": {},
                }
            ],
        )
    )

    assert result["turn_id"] == "turn_phase70_rich"
    request = captured["request"]
    assert request.input.type == "multi_part"
    assert len(request.attachments) == 1
    assert len(request.context_refs) == 1
    assert request.client_context.ui_mode == "wechat_chat"
    assert request.ingress_metadata.raw_payload["kind"] == "rich"


def test_phase70_task_replay_exposes_skill_candidates(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "phase70 replay skill candidates", "auto_start": False},
    ).json()
    client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "file.write",
            "args": {"path": "a.txt", "content": "one"},
        },
    )
    client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "file.hash",
            "args": {"path": "a.txt"},
        },
    )
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert "skill_candidates" in replay
    assert replay["skill_candidates"]
    assert replay["skill_candidates"][0]["candidate_type"] == "tool_chain"


def _run_async(client: TestClient, awaitable: Any) -> Any:
    portal = getattr(client, "portal", None)
    if portal is not None:
        async def portal_runner() -> Any:
            return await awaitable

        return portal.call(portal_runner)

    async def runner() -> Any:
        return await awaitable

    return anyio.run(runner)
