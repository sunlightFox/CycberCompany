from __future__ import annotations

from typing import Any, cast

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
            "args": {"command": "echo phase69-terminal"},
        },
    ).json()
    approval_id = first["approval"]["approval_id"]
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
            "args": {"command": "echo phase69-terminal"},
        },
    ).json()
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
