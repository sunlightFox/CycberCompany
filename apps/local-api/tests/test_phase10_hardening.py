from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase10_runtime_contracts_are_not_overstated(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    statuses = {item["status"] for item in contracts}

    assert {"implemented", "degraded"}.issubset(statuses)
    assert "placeholder" not in statuses
    assert by_name["SafetyService"]["status"] == "implemented"
    assert by_name["MCPConnectionManager"]["status"] == "implemented_with_fallback"
    assert by_name["TerminalRunner"]["status"] == "implemented_with_fallback"
    assert by_name["VectorStore"]["status"] == "implemented"
    assert by_name["SettingsAPI"]["status"] == "implemented"
    assert any(item["gap_id"] == "gap_terminal_os_sandbox" for item in gaps)
    assert any(item["gap_id"] == "gap_release_gate_depth" for item in gaps)


def test_phase10_mcp_allowlist_env_and_unknown_tool_status(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry
    registry.mcp_service.set_transport_factory(lambda _server: UnknownRiskMCPTransport())

    rejected_command = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "unsafe",
            "display_name": "Unsafe MCP",
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
        },
    )
    rejected_env = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "plainenv",
            "display_name": "Plain Env MCP",
            "transport": "stdio",
            "command": "fake-mcp",
            "env_refs": ["API_KEY=plain-secret"],
        },
    )
    accepted = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "phase10",
            "display_name": "Phase10 MCP",
            "transport": "stdio",
            "command": "fake-mcp",
            "env_refs": ["secret_ref:mcp_phase10_env"],
        },
    )
    assert rejected_command.status_code == 422
    assert rejected_env.status_code == 422
    assert accepted.status_code == 200, accepted.text

    assert client.post("/api/mcp/servers/phase10/enable").status_code == 200
    synced = client.post("/api/mcp/servers/phase10/sync")
    assert synced.status_code == 200, synced.text

    tool = client.get("/api/tools/mcp.phase10.mutate").json()
    assert tool["status"] == "disabled"
    execute = client.post(
        "/api/tools/execute",
        json={"tool_name": "mcp.phase10.mutate", "args": {"text": "x"}},
    )
    assert execute.status_code == 404
    assert execute.json()["error"]["code"] == "TOOL_NOT_FOUND"


def test_phase10_terminal_policy_sandbox_and_redacted_logs(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "phase10 terminal hardening", "auto_start": False},
    ).json()
    no_task = client.post(
        "/api/tools/execute",
        json={"tool_name": "terminal.run", "args": {"command": "echo no-task"}},
    )
    first = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "echo api_key=sk-phase10TerminalSecret123"},
        },
    ).json()
    approval_id = first["approval"]["approval_id"]
    client.post(f"/api/approvals/{approval_id}/approve", json={"reason": "test"})
    executed = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "approval_id": approval_id,
            "args": {"command": "echo api_key=sk-phase10TerminalSecret123"},
        },
    ).json()
    artifact = executed["artifacts"][0]
    log = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.read_log",
            "args": {"artifact_id": artifact["artifact_id"]},
        },
    ).json()
    cwd_attempt = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "approval_id": approval_id,
            "args": {"command": "echo cwd", "cwd": "C:\\"},
        },
    )

    assert no_task.status_code == 409
    assert no_task.json()["error"]["code"] == "TOOL_APPROVAL_REQUIRED"
    assert first["tool_call"]["status"] == "approval_required"
    assert artifact["checksum"].startswith("sha256:")
    assert "sk-phase10TerminalSecret123" not in log["result"]["content_preview"]
    assert cwd_attempt.status_code == 403
    assert cwd_attempt.json()["error"]["code"] == "TOOL_PERMISSION_DENIED"


def test_phase10_vector_release_and_repo_hygiene(client: TestClient) -> None:
    vector = client.post(
        "/api/vector/sync-jobs",
        json={"target_type": "knowledge", "target_id": "chunk_phase10"},
    ).json()
    suites = client.get("/api/evals/suites").json()["items"]
    suite_ids = {item["suite_id"] for item in suites}
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    diagnostic = client.post("/api/diagnostics/bundles", json={}).json()
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert vector["provider"] == "local"
    assert vector["status"] == "completed"
    assert "semantic_vector" in vector["payload"]["selection_reason"]
    assert "suite_phase10" in suite_ids
    assert completed["blocker_count"] == 0
    assert report["decision"] == "go"
    assert report["summary"]["phase10"]["runtime_contracts"] >= 1
    assert diagnostic["checksum"].startswith("sha256:")
    assert "data_tmp*/" in gitignore
    assert "Release Gate" in readme
    assert "sk-phase10TerminalSecret123" not in audit_text


def test_phase10_contract_copy_has_no_phase_two_mislabel() -> None:
    paths = [
        Path("services/tools/tools_service/contracts.py"),
        Path("services/skill-engine/skill_engine/contracts.py"),
        Path("services/task-engine/task_engine/contracts.py"),
        Path("services/asset-broker/asset_broker/contracts.py"),
        Path("services/capability-graph/capability_graph/contracts.py"),
        Path("services/context-gateway/context_gateway/contracts.py"),
        Path("services/brain/brain/contracts.py"),
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert "phase two" not in text.lower()
    assert "not_executable_in_phase_two" not in text


class UnknownRiskMCPTransport:
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
                "serverInfo": {"name": "phase10", "version": "0.1.0"},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "mutate",
                        "description": "Unknown mutation-capable tool",
                        "inputSchema": {"type": "object"},
                        "annotations": {},
                    }
                ]
            }
        if method == "resources/list":
            return {"resources": []}
        if method == "prompts/list":
            return {"prompts": []}
        if method == "tools/call":
            return {"content": [{"type": "text", "text": "mutated"}]}
        return {}
