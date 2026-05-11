from __future__ import annotations

from typing import Any, cast

from chat_runtime import ChatRuntime
from fastapi.testclient import TestClient


def test_phase77_session_runtime_still_delegates_to_chat_runtime(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry

    session_diag = _run_async(registry.session_runtime.diagnostic)

    assert session_diag["delegates_to"] == "chat_runtime"
    assert registry.session_runtime._runtime is registry.chat_runtime
    assert registry.chat_service._execution._runner.__self__ is registry.chat_runtime
    assert (
        registry.chat_service._execution._runner.__func__
        is registry.chat_runtime.run_turn.__func__
    )


def test_phase77_runtime_topology_exposes_chat_runtime_and_chat_service_roles(
    client: TestClient,
) -> None:
    body = client.get("/api/system/runtime-topology").json()
    items = {item["name"]: item for item in body["items"]}

    assert items["chat_runtime"]["runtime"] == "chat_runtime"
    assert items["chat_runtime"]["status"] == "runtime_native"
    assert items["chat_runtime"]["details"]["cleanup"]["role"] == "runtime_native"
    assert items["chat_runtime"]["details"]["cleanup"]["allowed_to_grow"] is True

    assert items["chat_service"]["runtime"] == "chat_service"
    assert items["chat_service"]["status"] == "compat_shell"
    assert items["chat_service"]["details"]["cleanup"]["role"] == "compat_shell"
    assert items["chat_service"]["details"]["cleanup"]["allowed_to_grow"] is False
    assert items["chat_service"]["details"]["cleanup"]["delegates_to"] == ["chat_runtime"]


def test_phase77_chat_service_no_longer_exposes_runtime_host_methods(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    chat_service = registry.chat_service

    compat_public_methods = (
        "create_turn",
        "stream_turn_events",
        "run_turn",
        "recover_incomplete_turns",
        "cancel_turn",
        "retry_turn",
        "placeholder_events",
    )
    for name in compat_public_methods:
        assert hasattr(chat_service, name)

    removed_runtime_methods = (
        "_execute_turn",
        "_execute_turn_impl",
        "_run_model_path",
        "_run_model_path_impl",
        "_complete_without_model",
        "_complete_without_model_impl",
    )
    for name in removed_runtime_methods:
        assert not hasattr(chat_service, name)

    assert not hasattr(ChatRuntime, "__getattr__")


def test_phase77_readiness_uses_runtime_closure_rules(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase77 = readiness["phase_readiness"]["phase77_runtime_closure"]

    assert phase77["status"] == "ready"
    assert phase77["next_owner_module"] == "services/chat-runtime/chat_runtime/runtime.py"
    assert "services/chat-runtime/chat_runtime/runtime.py" in phase77["source_of_truth"]
    assert "chat_service_still_exposes_runtime_host_methods" not in phase77["blocking_reasons"]


def _run_async(fn: Any) -> Any:
    import asyncio

    return asyncio.run(fn())
