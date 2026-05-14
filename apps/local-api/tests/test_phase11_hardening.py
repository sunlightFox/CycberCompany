from __future__ import annotations

import json
from typing import Any, cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase11_settings_api_reads_updates_and_redacts(client: TestClient) -> None:
    initial = client.get("/api/settings").json()
    updated = client.patch(
        "/api/settings",
        json={
            "model_routing": {
                "reserved_output_tokens": 2048,
                "allow_cloud_fallback": False,
            },
            "safety": {
                "approval_profile": "balanced_personal",
                "chat_visible_redaction": "relaxed",
            },
            "mcp": {"allowed_stdio_commands": ["fake-mcp", "phase11-mcp"]},
            "updated_by_member_id": "mem_xiaoyao",
        },
    )
    rejected = client.patch(
        "/api/settings",
        json={"mcp": {"allowed_stdio_commands": ["api_key=plain-secret"]}},
    )
    fetched = client.get("/api/settings").json()
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)
    contracts = {
        item["name"]: item
        for item in client.get("/api/system/runtime-contracts").json()["items"]
    }
    suite_ids = {
        item["suite_id"] for item in client.get("/api/evals/suites").json()["items"]
    }

    assert initial["source"] in {"config_defaults", "runtime_settings"}
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 1
    assert fetched["settings"]["model_routing"]["reserved_output_tokens"] == 2048
    assert fetched["settings"]["model_routing"]["allow_cloud_fallback"] is False
    assert fetched["settings"]["safety"]["approval_profile"] == "balanced_personal"
    assert fetched["settings"]["safety"]["chat_visible_redaction"] == "relaxed"
    assert "phase11-mcp" in fetched["settings"]["mcp"]["allowed_stdio_commands"]
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "CONFIG_ERROR"
    assert "plain-secret" not in audit_text
    assert contracts["SettingsAPI"]["status"] == "implemented"
    assert contracts["ResponseComposer"]["status"] == "implemented"
    assert "suite_phase11" in suite_ids


def test_phase11_chat_visible_paths_emit_response_plan(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    memory_turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase11-memory",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "记住：以后封版前先看 release gate"},
        },
    ).json()
    memory_events = _parse_sse(client.get(memory_turn["stream_url"]).text)
    completed = next(event for event in memory_events if event["event"] == "response.completed")

    failed_turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase11-no-model",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "你好，今天简单聊两句"},
        },
    ).json()
    failed_events = _parse_sse(client.get(failed_turn["stream_url"]).text)
    failed = failed_events[-1]

    assert completed["payload"]["response_plan"]["memory_notice"] == "显式记忆命令已处理。"
    assert completed["payload"]["response_plan"]["plain_text"]
    assert failed["event"] == "turn.failed"
    assert failed["payload"]["response_plan"]["structured_payload"]["error_code"] == (
        "MODEL_NOT_CONFIGURED"
    )


def test_phase11_agent_loop_records_replay_events(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "research phase11 backend evidence",
            "mode_hint": "agent",
            "auto_start": True,
            "budget_override": {"max_steps": 5, "max_tool_calls": 5},
        },
    ).json()
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    event_types = {event["event_type"] for event in replay["events"]}

    assert task["mode"] == "agent"
    assert task["status"] == "completed"
    assert {"agent.observe", "agent.plan", "agent.act", "agent.evaluate", "agent.stop"}.issubset(
        event_types
    )
    assert replay["final_result"]["stop_reason"] in {"completed", "goal_satisfied"}


def test_phase11_terminal_artifact_has_sandbox_profile(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "phase11 terminal profile", "auto_start": False},
    ).json()
    first = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "echo phase11"},
        },
    ).json()
    approval_id = first.get("approval", {}).get("approval_id")
    if approval_id:
        client.post(f"/api/approvals/{approval_id}/approve", json={"reason": "phase11"})
        executed = client.post(
            "/api/tools/execute",
            json={
                "task_id": task["task_id"],
                "tool_name": "terminal.run",
                "approval_id": approval_id,
                "args": {"command": "echo phase11"},
            },
        ).json()
    else:
        executed = first
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    terminal_artifact = next(
        item for item in replay["artifacts"] if item["artifact_type"] == "terminal_log"
    )

    assert executed["result"]["sandbox_profile"]["type"] == "local_artifact"
    assert executed["result"]["policy_snapshot"]["decision"] in {"allow", "approval_required"}
    sandbox_profile = terminal_artifact["metadata"]["sandbox_profile"]
    assert sandbox_profile["os_sandbox_backend"] in {"windows_job_object", "policy_guard"}
    assert sandbox_profile.get("accepted_risk") in {
        None,
        "os_sandbox_fallback_policy_guard",
        "container_not_enabled",
        "windows_low_integrity_not_enabled",
    }


def test_phase11_balanced_personal_skips_safe_terminal_but_keeps_file_delete_approval(
    client: TestClient,
) -> None:
    patched = client.patch(
        "/api/settings",
        json={
            "safety": {
                "approval_profile": "balanced_personal",
                "chat_visible_redaction": "relaxed",
            },
            "updated_by_member_id": "mem_xiaoyao",
        },
    )
    task = client.post(
        "/api/tasks",
        json={"goal": "phase11 personal safety", "auto_start": False},
    ).json()
    terminal = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "echo phase11-personal"},
        },
    ).json()
    delete = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "file.delete",
            "args": {"path": "outputs/phase11-delete.txt"},
        },
    ).json()

    assert patched.status_code == 200, patched.text
    assert terminal.get("approval") is None
    assert terminal["tool_call"]["status"] == "completed"
    assert delete["approval"]["approval_id"]
    assert delete["tool_call"]["status"] == "approval_required"


def test_phase11_mcp_member_scope_policy_blocks_unauthorized_member(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    registry.mcp_service.set_transport_factory(lambda _server: Phase11MCPTransport())
    created = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "phase11",
            "display_name": "Phase11 MCP",
            "transport": "stdio",
            "command": "fake-mcp",
            "permission": {"allowed_members": ["mem_xiaoyao"]},
            "env_refs": ["secret_ref:mcp_phase11_env"],
        },
    )
    assert created.status_code == 200, created.text
    assert client.post("/api/mcp/servers/phase11/enable").status_code == 200
    assert client.post("/api/mcp/servers/phase11/sync").status_code == 200

    denied = client.post(
        "/api/tools/execute",
        json={
            "member_id": "mem_other",
            "tool_name": "mcp.phase11.echo",
            "args": {"text": "hello"},
        },
    )
    allowed = client.post(
        "/api/tools/execute",
        json={
            "member_id": "mem_xiaoyao",
            "tool_name": "mcp.phase11.echo",
            "args": {"text": "hello"},
        },
    )
    rows = anyio.run(
        registry.db.fetch_all,
        "SELECT policy_snapshot_json FROM mcp_calls ORDER BY created_at DESC LIMIT 1",
    )

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "MCP_TOOL_PERMISSION_DENIED"
    assert allowed.status_code == 200, allowed.text
    snapshot = json.loads(rows[0]["policy_snapshot_json"])
    assert snapshot["mcp_scope"]["allowed"] is True
    assert snapshot["mcp_scope"]["member_id"] == "mem_xiaoyao"


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


class Phase11MCPTransport:
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
                "serverInfo": {"name": "phase11", "version": "0.1.0"},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Read-only echo",
                        "inputSchema": {"type": "object"},
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        if method == "resources/list":
            return {"resources": []}
        if method == "prompts/list":
            return {"prompts": []}
        if method == "tools/call":
            return {"content": [{"type": "text", "text": str(params or {})}]}
        return {}
