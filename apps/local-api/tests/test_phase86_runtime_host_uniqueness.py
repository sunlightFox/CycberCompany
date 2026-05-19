from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient


def test_phase86_readiness_reports_runtime_host_uniqueness_ready(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()

    assert (
        readiness["phase_readiness"]["phase86_runtime_host_uniqueness"]["status"] == "ready"
    )
    assert (
        readiness["runtime_facts"]["phase_docs_present"]["phase86_runtime_host_uniqueness"]
        is True
    )
    assert (
        readiness["runtime_facts"]["phase_tests_present"]["phase86_runtime_host_uniqueness"]
        is True
    )


def test_phase86_topology_uses_phase86_status_names(
    client: TestClient,
) -> None:
    body = client.get("/api/system/runtime-topology").json()
    items = {item["name"]: item for item in body["items"]}

    assert items["chat_service"]["status"] == "compat_shell"
    assert items["chat_service"]["details"]["cleanup"]["role"] == "compat_shell"
    assert items["channel_ingress"]["status"] == "runtime_native"
    assert items["channel_ingress"]["details"]["cleanup"]["role"] == "runtime_native"
    assert items["wechat_gateway"]["status"] == "compat_shell"
    assert items["feishu_gateway"]["status"] == "compat_shell"
    assert items["chat_context_helper"]["status"] == "helper"
    assert items["chat_model_helper"]["status"] == "helper"
    assert items["chat_memory_helper"]["status"] == "helper"
    assert items["chat_response_helper"]["status"] == "helper"


def test_phase86_session_runtime_is_proxy_only_and_chat_runtime_is_exclusive_owner(
    client: TestClient,
) -> None:
    session_runtime = client.get("/api/system/session-runtime").json()
    topology = client.get("/api/system/runtime-topology").json()
    items = {item["name"]: item for item in topology["items"]}

    assert session_runtime["ownership_mode"] == "proxy_only"
    assert session_runtime["state_machine_owner"] == "agent_runtime"
    assert session_runtime["event_source"] == "agent_runtime"
    assert session_runtime["business_logic_owner"] == "agent_runtime"

    assert items["agent_runtime"]["details"]["ownership_mode"] == "exclusive_execution_owner"
    assert items["agent_runtime"]["details"]["turn_execution_owner"] == "agent_runtime"
    assert items["chat_runtime"]["details"]["ownership_mode"] == "compat_facade"
    assert items["chat_runtime"]["details"]["execution_owner"] == "agent_runtime"
    assert items["chat_runtime"]["details"]["state_machine_owner"] == "agent_runtime"
    assert items["chat_runtime"]["details"]["event_source"] == "agent_runtime"
    assert items["chat_runtime"]["details"]["response_finalize_owner"] == "agent_runtime"


def test_phase86_chat_service_keeps_only_public_compat_surface(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    chat_service = registry.chat_service

    public_compat_methods = (
        "create_turn",
        "stream_turn_events",
        "run_turn",
        "recover_incomplete_turns",
        "cancel_turn",
        "retry_turn",
        "placeholder_events",
    )
    for name in public_compat_methods:
        assert hasattr(chat_service, name)

    removed_impl_methods = (
        "_create_turn_impl",
        "_stream_turn_events_impl",
        "_run_turn_impl",
        "_recover_incomplete_turns_impl",
        "_cancel_turn_impl",
        "_retry_turn_impl",
        "_execute_turn",
        "_execute_turn_impl",
        "_run_model_path",
        "_run_model_path_impl",
        "_complete_without_model",
        "_complete_without_model_impl",
    )
    for name in removed_impl_methods:
        assert not hasattr(chat_service, name)
