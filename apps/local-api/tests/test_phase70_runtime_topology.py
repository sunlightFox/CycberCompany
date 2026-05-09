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
    assert by_name["channel_ingress"]["runtime"] == "channel_ingress_runtime"
    assert by_name["task"]["runtime"] == "task_runtime"
    assert by_name["tool"]["runtime"] == "tool_runtime"
    assert by_name["mcp"]["runtime"] == "mcp_runtime"
    assert by_name["release"]["runtime"] == "release_gate_runtime"
    assert by_name["skill_promotion"]["runtime"] == "skill_promotion_runtime"

    assert "turn_execution_manager" in by_name["session"]["dependencies"]
    assert "task_resume_runtime" in by_name["task"]["dependencies"]
    assert "tool_terminal_runtime" in by_name["tool"]["dependencies"]
    assert by_name["mcp"]["details"]["policy"]["taint_enforced"] is True


def test_phase70_session_runtime_and_tool_runtime_diagnostics_are_honest(
    client: TestClient,
) -> None:
    session_runtime = client.get("/api/system/session-runtime")
    assert session_runtime.status_code == 200, session_runtime.text
    session_body = session_runtime.json()
    assert session_body["runtime"] == "session_runtime"
    assert session_body["executor"] == "turn_execution_manager"
    assert "scheduled_task_service" in session_body["route_selectors"]

    tool_runtime = client.get("/api/system/tool-runtime")
    assert tool_runtime.status_code == 200, tool_runtime.text
    tool_body = tool_runtime.json()
    assert tool_body["runtime"] == "tool_runtime"
    assert tool_body["dispatcher"] == "tool_dispatcher"
    assert tool_body["safety_bridge"] == "tool_safety_bridge"
    assert tool_body["asset"]["capability_graph_required"] is True
    assert tool_body["memory"]["trace_safe"] is True
    assert tool_body["terminal"]["approval_required_for_high_risk"] is True
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


def _run_async(client: TestClient, awaitable: Any) -> Any:
    portal = getattr(client, "portal", None)
    if portal is not None:
        async def portal_runner() -> Any:
            return await awaitable

        return portal.call(portal_runner)

    async def runner() -> Any:
        return await awaitable

    return anyio.run(runner)
