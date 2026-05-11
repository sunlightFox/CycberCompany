from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_phase74_runtime_topology_exposes_cleanup_roles_and_growth_policy(
    client: TestClient,
) -> None:
    body = client.get("/api/system/runtime-topology").json()
    items = {item["name"]: item for item in body["items"]}

    assert items["session"]["details"]["cleanup"]["role"] == "runtime_native"
    assert items["session"]["details"]["cleanup"]["allowed_to_grow"] is True

    assert items["tool"]["details"]["cleanup"]["host_files"] == ["tools.py"]
    assert items["tool"]["details"]["cleanup"]["role"] == "runtime_native"

    assert items["skill"]["details"]["cleanup"]["role"] == "compat_shell"
    assert items["skill"]["details"]["cleanup"]["allowed_to_grow"] is False
    assert items["skill"]["details"]["cleanup"]["host_files"] == ["skill_plugin.py"]

    assert items["mcp"]["details"]["cleanup"]["role"] == "compat_shell"
    assert items["mcp"]["details"]["cleanup"]["allowed_to_grow"] is False
    assert "mcp_conversation_bridge" in items["mcp"]["details"]["cleanup"]["delegates_to"]

    assert items["wechat_gateway"]["details"]["cleanup"]["role"] == "compat_shell"
    assert items["wechat_gateway"]["details"]["cleanup"]["allowed_to_grow"] is False
    assert items["feishu_gateway"]["details"]["cleanup"]["host_files"] == ["feishu_gateway.py"]


def test_phase74_helper_components_have_explicit_retention_reason(
    client: TestClient,
) -> None:
    body = client.get("/api/system/runtime-topology").json()
    items = {item["name"]: item for item in body["items"]}

    assert items["chat_context_helper"]["details"]["cleanup"]["role"] == "helper"
    assert items["chat_context_helper"]["details"]["cleanup"]["allowed_to_grow"] is False

    assert items["chat_model_helper"]["details"]["cleanup"]["role"] == "helper"
    assert "chat_model_execution" in items["chat_model_helper"]["details"]["cleanup"]["delegates_to"]

    assert items["chat_memory_helper"]["details"]["cleanup"]["role"] == "helper"
    assert items["chat_memory_helper"]["details"]["cleanup"]["host_files"] == ["chat_memory.py"]

    assert items["chat_response_helper"]["details"]["cleanup"]["role"] == "helper"
    assert items["chat_response_helper"]["details"]["cleanup"]["allowed_to_grow"] is False
    assert "finalize_cleanup" not in items["chat_response_helper"]["details"]
    assert items["chat_response_helper"]["details"]["visible_authority"] == "response_plan_plain_text"


def test_phase74_static_scan_keeps_legacy_execution_symbols_in_compat_hosts_only() -> None:
    output = _rg(
        r"_handle_browser_read_page|_handle_terminal_readonly_command|_execute_browser_tool|_execute_terminal_tool",
        "apps/local-api/app/services",
    )
    lines = [line for line in output.splitlines() if line.strip()]

    assert lines
    allowed_files = {
        "apps/local-api/app/services/chat_turn_execution.py",
        "apps/local-api/app/services/tools.py",
        "apps/local-api/app/services/tool_builtin_runtime.py",
    }
    for line in lines:
        file_part = line.split(":", 1)[0].replace("\\", "/")
        assert file_part in allowed_files


def test_phase74_static_scan_tracks_thin_coordinators_explicitly() -> None:
    output = _rg(
        r"class ChatContextCoordinator|class ChatModelCoordinator|class ChatMemoryCoordinator|class ChatResponseCoordinator",
        "apps/local-api/app/services",
    )
    lines = [line for line in output.splitlines() if line.strip()]

    files = {line.split(":", 1)[0].replace("\\", "/") for line in lines}
    assert files == {
        "apps/local-api/app/services/chat_context.py",
        "apps/local-api/app/services/chat_model.py",
        "apps/local-api/app/services/chat_memory.py",
        "apps/local-api/app/services/chat_response.py",
    }


def test_phase74_chat_py_route_semantics_residue_stays_bounded() -> None:
    output = _rg(r"route_semantics", "apps/local-api/app/services/chat.py")
    lines = [line for line in output.splitlines() if line.strip()]

    assert 1 <= len(lines) <= 8


def _rg(pattern: str, path: str) -> str:
    result = subprocess.run(
        ["rg", "-n", pattern, path],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout
