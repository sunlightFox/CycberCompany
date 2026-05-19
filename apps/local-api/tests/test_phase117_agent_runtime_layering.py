from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient


def test_phase117_runtime_topology_exposes_agent_runtime_and_plane_registries(
    client: TestClient,
) -> None:
    body = client.get("/api/system/runtime-topology").json()
    items = {item["name"]: item for item in body["items"]}

    assert items["agent_runtime"]["details"]["plane"] == "agent_runtime_plane"
    assert items["agent_runtime"]["details"]["owner"] == "agent_runtime"
    assert items["agent_runtime"]["details"]["contract_version"] == "phase117.agent_runtime_owner.v1"
    assert items["chat_runtime"]["details"]["plane"] == "runtime_plane"
    assert items["chat_runtime"]["details"]["execution_owner"] == "agent_runtime"
    assert items["session"]["details"]["plane"] == "session_plane"
    assert items["session"]["details"]["delegates_to"] == "agent_runtime"

    registry = cast(Any, client.app).state.registry
    assert registry.runtime_registry.agent_runtime is registry.agent_runtime
    assert registry.runtime_registry.chat_runtime is registry.chat_runtime
    assert registry.policy_registry.persona_runtime is not None
    assert registry.capability_registry.browser_research_runtime is not None


def test_phase117_turn_execution_plan_is_recorded_on_running_turn(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    registry.chat_service._execution.schedule = lambda *args, **kwargs: None
    created = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": _conversation_id(client),
            "member_id": "mem_xiaoyao",
            "session_id": "sess_phase117_plan",
            "input": {"type": "text", "text": "请用自然的话解释一下网页快照和截图的区别"},
        },
    )
    assert created.status_code == 200, created.text
    turn_id = created.json()["turn_id"]
    _run_async(lambda: registry.agent_runtime.run_turn(turn_id))

    detail = client.get(f"/api/chat/turns/{turn_id}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    experience = dict(payload.get("experience") or {})
    plan = dict(experience.get("turn_execution_plan") or payload.get("turn_execution_plan") or {})

    assert plan["turn_id"] == turn_id
    assert plan["conversation_id"] == payload["conversation_id"]
    assert plan["member_id"] == "mem_xiaoyao"
    assert "response_contract" in plan
    assert "trace_metadata" in plan


def _conversation_id(client: TestClient) -> str:
    response = client.get("/api/chat/conversations")
    assert response.status_code == 200, response.text
    return str(response.json()["items"][0]["conversation_id"])


def _run_async(factory: Any) -> Any:
    import anyio

    with anyio.from_thread.start_blocking_portal() as portal:
        return portal.call(factory)
