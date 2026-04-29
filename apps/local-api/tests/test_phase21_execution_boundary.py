from __future__ import annotations

from typing import Any, cast

import anyio
from fastapi.testclient import TestClient


def test_phase21_runtime_contracts_suite_and_policy_api(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    policies = client.get("/api/tools/policies").json()["items"]
    by_module = {item["name"]: item for item in contracts}

    assert by_module["ToolActionPolicyService"]["status"] == "implemented"
    assert by_module["CommandRiskClassifier"]["status"] == "implemented"
    assert by_module["TerminalSandboxProfile"]["status"] == "implemented"
    assert by_module["OutputDLP"]["status"] == "implemented"
    assert by_module["ExecutionBoundaryDiagnostics"]["status"] == "implemented"
    assert by_module["OSLevelSandbox"]["status"] == "implemented_with_fallback"
    assert any(item["gap_id"] == "gap_os_level_sandbox_degraded" for item in gaps)
    assert "suite_phase21_execution_boundary" in {item["suite_id"] for item in suites}
    terminal_policy = next(item for item in policies if item["tool_name"] == "terminal.run")
    assert terminal_policy["requires_task_binding"] is True
    assert terminal_policy["risk_level"] == "R5"


def test_phase21_unknown_tool_and_terminal_policy_denials(client: TestClient) -> None:
    unknown = client.post(
        "/api/tools/execute",
        json={"tool_name": "unknown.run", "args": {"x": 1}},
    )
    no_task = client.post(
        "/api/tools/execute",
        json={"tool_name": "terminal.run", "args": {"command": "echo hello"}},
    )
    task = client.post(
        "/api/tasks",
        json={"goal": "phase21 terminal boundary", "mode_hint": "workflow", "auto_start": False},
    ).json()
    custom_cwd = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "echo hello", "cwd": "C:\\"},
        },
    )
    sensitive_path = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "type C:\\Windows\\System32\\config\\SAM"},
        },
    )

    assert unknown.status_code == 403
    assert unknown.json()["error"]["code"] == "TOOL_PERMISSION_DENIED"
    assert no_task.status_code == 409
    assert custom_cwd.status_code == 403
    assert sensitive_path.status_code == 403
    assert sensitive_path.json()["error"]["code"] in {"SAFETY_BLOCKED", "TOOL_PERMISSION_DENIED"}


def test_phase21_terminal_approval_boundary_and_output_dlp(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    task = client.post(
        "/api/tasks",
        json={"goal": "phase21 terminal dlp", "mode_hint": "workflow", "auto_start": False},
    ).json()
    payload = {
        "task_id": task["task_id"],
        "tool_name": "terminal.run",
        "args": {"command": "python -c \"print('api_key=sk-phase21secret123')\""},
    }
    first = client.post("/api/tools/execute", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["approval"]["status"] == "pending"

    approval_id = first.json()["approval"]["approval_id"]
    anyio.run(_approve, registry, approval_id)
    second = client.post(
        "/api/tools/execute",
        json={**payload, "approval_id": approval_id},
    )
    assert second.status_code == 200, second.text
    result = second.json()["result"]
    tool_call = second.json()["tool_call"]
    boundary = client.get(f"/api/tools/calls/{tool_call['tool_call_id']}/boundary").json()
    dlp = client.get(f"/api/tools/calls/{tool_call['tool_call_id']}/dlp").json()["items"]
    artifact = client.get(f"/api/artifacts/{result['log_artifact_id']}").json()

    assert tool_call["policy_snapshot"]["boundary_decision_id"]
    assert boundary["sandbox_profile"]["profile_id"] == "task_artifact_policy_guard"
    assert result["sandbox_profile"]["os_sandbox_backend"] in {
        "windows_job_object",
        "policy_guard",
    }
    assert dlp
    assert any(item["redaction_count"] > 0 for item in dlp)
    serialized = str({"result": result, "artifact": artifact, "dlp": dlp})
    assert "sk-phase21secret123" not in serialized
    assert "[REDACTED" in serialized


def test_phase21_mcp_policy_checks_untrusted_content_and_dlp(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    registry.mcp_service.set_transport_factory(lambda _server: Phase21MCPTransport())
    denied = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "phase21_bad",
            "display_name": "Phase21 bad",
            "transport": "stdio",
            "command": "powershell",
            "args": [],
            "env_refs": [],
        },
    )
    inline_env = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "phase21_env",
            "display_name": "Phase21 env",
            "transport": "stdio",
            "command": "fake-mcp",
            "args": [],
            "env_refs": ["API_KEY=plain-secret"],
        },
    )
    created = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "phase21",
            "display_name": "Phase21 MCP",
            "transport": "stdio",
            "command": "fake-mcp",
            "args": [],
            "env_refs": ["secret_ref:mcp_phase21"],
        },
    )
    assert denied.status_code == 422
    assert inline_env.status_code == 422
    assert created.status_code == 200, created.text
    assert client.post("/api/mcp/servers/phase21/enable").status_code == 200
    assert client.post("/api/mcp/servers/phase21/sync").status_code == 200
    resources = client.get("/api/mcp/servers/phase21/resources").json()["items"]
    prompts = client.get("/api/mcp/servers/phase21/prompts").json()["items"]
    executed = client.post(
        "/api/tools/execute",
        json={
            "member_id": "mem_xiaoyao",
            "tool_name": "mcp.phase21.echo",
            "args": {"text": "hello"},
        },
    )
    assert executed.status_code == 200, executed.text
    result = executed.json()["result"]
    tool_call_id = executed.json()["tool_call"]["tool_call_id"]
    dlp = client.get(f"/api/tools/calls/{tool_call_id}/dlp").json()["items"]

    assert resources[0]["trust_level"] == "untrusted_external_content"
    assert prompts[0]["trust_level"] == "mcp_prompt_template"
    assert result["untrusted_external_content"] is True
    assert "sk-phase21mcpsecret123" not in str(result)
    assert any(item["redaction_count"] > 0 for item in dlp)
    assert anyio.run(_mcp_policy_check_count, registry) >= 3


def test_phase21_eval_and_release_report_include_summary(client: TestClient) -> None:
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase21_execution_boundary"},
    ).json()
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert run["status"] == "passed"
    assert run["total_cases"] == 11
    assert completed["status"] == "ready_for_release"
    phase21 = report["summary"]["phase21"]
    assert phase21["registered_cases"] == 11
    assert phase21["failed_results"] == 0
    assert (
        phase21["sandbox_degraded_evidence"][
            "os_level_sandbox_implemented_with_fallback"
        ]
        == 1
    )
    assert phase21["contracts"]["OutputDLP"] == 1


async def _approve(registry: Any, approval_id: str) -> None:
    await registry.approval_service.approve(
        approval_id,
        actor_type="user",
        actor_id="user_local_owner",
        reason="phase21 test approval",
        trace_id=None,
    )


async def _mcp_policy_check_count(registry: Any) -> int:
    row = await registry.db.fetch_one(
        "SELECT COUNT(*) AS count FROM mcp_process_policy_checks"
    )
    return int(row["count"])


class Phase21MCPTransport:
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
                "serverInfo": {"name": "phase21", "version": "0.1.0"},
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
            return {
                "resources": [
                    {
                        "uri": "phase21://resource",
                        "name": "Phase21 Resource",
                        "description": "External resource",
                        "mimeType": "text/plain",
                    }
                ]
            }
        if method == "prompts/list":
            return {
                "prompts": [
                    {
                        "name": "draft",
                        "description": "Prompt suggestion",
                        "arguments": [{"name": "topic", "required": True}],
                    }
                ]
            }
        if method == "tools/call":
            arguments = (params or {}).get("arguments", {})
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"echo:{arguments.get('text')} api_key=sk-phase21mcpsecret123",
                    }
                ]
            }
        raise AssertionError(f"unexpected MCP method: {method}")
