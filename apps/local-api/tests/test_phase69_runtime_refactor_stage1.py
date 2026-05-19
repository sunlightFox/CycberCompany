from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from chat_runtime import canonical_route_name
from fastapi.testclient import TestClient


def test_phase69_terminal_policy_snapshot_records_real_backend_and_approval_binding(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "phase69 terminal evidence", "auto_start": False},
    ).json()
    first = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": _python_command("print('phase69-terminal')")},
        },
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    approval_id = None
    if first_body.get("approval"):
        approval_id = first_body["approval"]["approval_id"]
        assert client.post(
            f"/api/approvals/{approval_id}/approve",
            json={"reason": "phase69 terminal"},
        ).status_code == 200
        executed = client.post(
            "/api/tools/execute",
            json={
                "task_id": task["task_id"],
                "tool_name": "terminal.run",
                "approval_id": approval_id,
                "args": {"command": _python_command("print('phase69-terminal')")},
            },
        ).json()
    else:
        executed = first_body
    boundary = client.get(
        f"/api/tools/calls/{executed['tool_call']['tool_call_id']}/boundary"
    ).json()
    terminal_result = boundary["tool_call"]["policy_snapshot"]["terminal_sandbox_result"]

    assert executed["result"]["selected_backend"] in {"windows_job_object", "policy_guard"}
    assert terminal_result["selected_backend"] == executed["result"]["selected_backend"]
    assert terminal_result["approval_binding"] == approval_id
    assert terminal_result["fallback_chain"]


def test_phase69_mcp_result_exposes_taint_envelope_for_downstream_runtime(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.mcp_service.set_transport_factory(lambda _server: _Phase69MixedTransport())
    created = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "phase69mcp",
            "display_name": "phase69mcp",
            "transport": "stdio",
            "command": "fake-mcp",
            "args": [],
            "env_refs": ["secret_ref:phase69mcp"],
        },
    )
    assert created.status_code == 200, created.text
    assert client.post("/api/mcp/servers/phase69mcp/enable").status_code == 200
    assert client.post("/api/mcp/servers/phase69mcp/sync").status_code == 200
    executed = client.post(
        "/api/tools/execute",
        json={
            "member_id": "mem_xiaoyao",
            "tool_name": "mcp.phase69mcp.echo",
            "args": {"text": "hello"},
        },
    )
    assert executed.status_code == 200, executed.text
    result = executed.json()["result"]

    assert result["untrusted_external_content"] is True
    assert result["taint"]["untrusted"] is True
    assert result["taint"]["record_id"] == result["taint_record_id"]
    assert result["taint"]["guard_decision"] == result["taint_guard_decision"]


def test_phase69_chat_runtime_becomes_real_runtime_surface(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry

    assert registry.session_runtime._runtime is registry.chat_runtime
    assert registry.chat_service._execution._runner.__self__ is registry.agent_runtime


def test_phase69_runtime_emits_route_taxonomy_for_readonly_shortcuts(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = _fake_home(tmp_path, monkeypatch)
    (home / "Desktop" / "alpha.txt").write_text("alpha content", encoding="utf-8")
    conversation = client.get("/api/chat/conversations").json()["items"][0]

    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase69-route-taxonomy",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "我桌面有哪些文件"},
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    completed = next(event for event in events if event["event"] == "response.completed")
    payload = completed["payload"]["response_plan"]["structured_payload"]

    assert payload["route_semantics"]["route"] == "host_filesystem_list"
    assert (
        payload["route_semantics"]["route_taxonomy"]
        == canonical_route_name("host_filesystem_list")
    )


class _Phase69MixedTransport:
    async def start(self) -> None:
        return None

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        return None

    async def close(self) -> None:
        return None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "phase69", "version": "0.1.0"},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo text",
                        "inputSchema": {"type": "object", "required": ["text"]},
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        if method == "resources/list":
            return {"resources": []}
        if method == "prompts/list":
            return {"prompts": []}
        if method == "tools/call":
            text = str((params or {}).get("arguments", {}).get("text", ""))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"echo:{text} api_key=sk-phase69-secret-123",
                    }
                ]
            }
        raise AssertionError(f"unexpected method: {method}")


def _fake_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    for name in ["Desktop", "Downloads", "Documents"]:
        (home / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


def _python_command(script: str) -> str:
    return f'python -c "{script}"'


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                data = json.loads(current.get("data", "{}"))
                events.append(
                    {
                        "event": data.get("event") or current.get("event"),
                        "payload": data.get("payload", {}),
                    }
                )
                current = {}
            continue
        if line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            current["data"] = f"{current.get('data', '')}{line.split(':', 1)[1].strip()}"
    return events
