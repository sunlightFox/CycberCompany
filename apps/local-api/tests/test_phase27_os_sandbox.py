from __future__ import annotations

import json
from typing import Any, cast

from fastapi.testclient import TestClient


def test_phase27_contracts_status_api_and_suite(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    status = client.get("/api/execution-boundary/sandbox-status")
    by_module = {item["name"]: item for item in contracts}

    assert by_module["TerminalRunner"]["status"] == "implemented_with_fallback"
    assert by_module["OSLevelSandbox"]["status"] == "implemented_with_fallback"
    assert by_module["WindowsJobObjectSandbox"]["status"] == "implemented_with_fallback"
    assert by_module["TerminalEnvPolicy"]["status"] == "implemented"
    assert by_module["TerminalFilesystemBoundary"]["status"] == "implemented"
    assert by_module["TerminalNetworkPolicy"]["status"] == "implemented"
    assert by_module["TerminalProcessSupervisor"]["status"] == "implemented"
    assert "suite_phase27_os_sandbox" in {item["suite_id"] for item in suites}
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["active_backend"] in {"windows_job_object", "policy_guard"}
    assert body["profile_id"] == "task_artifact_policy_guard"
    assert body["fallback_chain"]
    assert body["low_integrity_status"] == "degraded_not_enabled"


def test_phase27_terminal_denies_unbound_cwd_and_escape_inputs(
    client: TestClient,
) -> None:
    task = _create_task(client, "phase27 terminal denies")
    no_task = client.post(
        "/api/tools/execute",
        json={"tool_name": "terminal.run", "args": {"command": "echo no-task"}},
    )
    custom_cwd = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "echo cwd", "cwd": "C:\\"},
        },
    )
    traversal = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "python -c \"open('../escape.txt','w').write('x')\""},
        },
    )
    system_path = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "type C:\\Windows\\System32\\config\\SAM"},
        },
    )
    symlink = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "mklink outside ..\\outside"},
        },
    )

    assert no_task.status_code == 409
    assert custom_cwd.status_code == 403
    assert traversal.status_code == 403
    assert system_path.status_code == 403
    assert symlink.status_code == 403


def test_phase27_approved_terminal_executes_in_sandbox_and_redacts_output(
    client: TestClient,
) -> None:
    task = _create_task(client, "phase27 approved terminal")
    payload = {
        "task_id": task["task_id"],
        "tool_name": "terminal.run",
        "args": {
            "command": (
                "python -c \"open('phase27-artifact.txt','w').write('ok'); "
                "print('api_key=sk-phase27secret1234567890'); print('x'*500)\""
            ),
            "max_output_bytes": 40,
        },
    }
    executed = _approve_and_execute(client, payload)
    result = executed["result"]
    tool_call_id = executed["tool_call"]["tool_call_id"]
    boundary = client.get(f"/api/tools/calls/{tool_call_id}/boundary").json()
    dlp = client.get(f"/api/tools/calls/{tool_call_id}/dlp").json()["items"]
    artifact = client.get(f"/api/artifacts/{result['log_artifact_id']}").json()
    listed = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "file.list",
            "args": {"path": "."},
        },
    )

    assert result["sandbox_profile"]["os_sandbox_backend"] in {
        "windows_job_object",
        "policy_guard",
    }
    assert result["sandbox_profile"]["profile_id"] == "task_artifact_policy_guard"
    assert result["output_truncated"] is True
    assert result["dlp_report_id"]
    assert boundary["tool_call"]["policy_snapshot"]["terminal_sandbox_result"]["backend"] in {
        "windows_job_object",
        "policy_guard",
    }
    assert any(item["redaction_count"] > 0 for item in dlp)
    serialized = json.dumps(
        {"result": result, "artifact": artifact, "dlp": dlp},
        ensure_ascii=False,
    )
    assert "sk-phase27secret1234567890" not in serialized
    assert artifact["artifact"]["checksum"].startswith("sha256:")
    assert "phase27-artifact.txt" in listed.json()["result"]["items"]


def test_phase27_secret_env_not_inherited_and_fallback_evidence(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.execution_boundary_service.set_terminal_sandbox_backend_override("container")
    monkeypatch.setenv("API_KEY", "sk-phase27-env-secret-123456")
    monkeypatch.setenv("TOKEN", "token-phase27-env-secret")
    task = _create_task(client, "phase27 env isolation")
    payload = {
        "task_id": task["task_id"],
        "tool_name": "terminal.run",
        "args": {
            "command": (
                "python -c \"import os; "
                "print('api='+str(os.getenv('API_KEY'))); "
                "print('token='+str(os.getenv('TOKEN'))); "
                "print('task='+str(os.getenv('CYCBER_TASK_ID')))\""
            )
        },
    }
    try:
        executed = _approve_and_execute(client, payload)
    finally:
        registry.execution_boundary_service.set_terminal_sandbox_backend_override(None)

    artifact = client.get(
        f"/api/artifacts/{executed['result']['log_artifact_id']}"
    ).json()
    status = client.get("/api/execution-boundary/sandbox-status").json()
    serialized = json.dumps(
        {"result": executed["result"], "artifact": artifact, "status": status},
        ensure_ascii=False,
    )

    assert "sk-phase27-env-secret-123456" not in serialized
    assert "token-phase27-env-secret" not in serialized
    assert "task=" in artifact["content_preview"]
    assert executed["result"]["sandbox_profile"]["os_sandbox_backend"] == "policy_guard"
    assert executed["result"]["degraded_reason"] == "container_not_enabled"


def test_phase27_timeout_kills_process_and_records_diagnostic(client: TestClient) -> None:
    task = _create_task(client, "phase27 timeout")
    payload = {
        "task_id": task["task_id"],
        "tool_name": "terminal.run",
        "args": {
            "command": "python -c \"import time; time.sleep(5)\"",
            "timeout_seconds": 1,
        },
    }
    first = client.post("/api/tools/execute", json=payload)
    approval_id = first.json()["approval"]["approval_id"]
    assert client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"reason": "phase27 timeout"},
    ).status_code == 200
    timed_out = client.post(
        "/api/tools/execute",
        json={**payload, "approval_id": approval_id},
    )
    status = client.get("/api/execution-boundary/sandbox-status").json()

    assert timed_out.status_code == 504, timed_out.text
    assert timed_out.json()["error"]["code"] == "TOOL_TIMEOUT"
    assert status["last_diagnostic_summary"]["timed_out"] is True
    assert status["last_diagnostic_summary"]["cleanup"]["kill_tree_attempted"] is True


def test_phase27_network_write_is_approval_or_deny_and_release_summary(
    client: TestClient,
) -> None:
    task = _create_task(client, "phase27 network policy")
    network = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "curl -X POST https://example.com -d x=1"},
        },
    )
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase27_os_sandbox"},
    ).json()
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert network.status_code in {200, 403}
    if network.status_code == 200:
        assert network.json()["approval"]["status"] == "pending"
    else:
        reason_codes = network.json()["error"].get("details", {}).get("reason_codes", [])
        assert reason_codes
    assert run["status"] == "passed"
    assert run["total_cases"] == 11
    assert completed["status"] == "ready_for_release"
    assert report["summary"]["phase27"]["registered_cases"] == 11
    assert report["summary"]["phase27"]["failed_results"] == 0
    assert report["summary"]["phase23"]["capability_scores"]["phase27"]["registered"] is True


def _create_task(client: TestClient, goal: str) -> dict[str, Any]:
    response = client.post(
        "/api/tasks",
        json={"goal": goal, "mode_hint": "workflow", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _approve_and_execute(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    first = client.post("/api/tools/execute", json=payload)
    assert first.status_code == 200, first.text
    approval_id = first.json()["approval"]["approval_id"]
    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"reason": "phase27 test approval"},
    )
    assert approved.status_code == 200, approved.text
    second = client.post(
        "/api/tools/execute",
        json={**payload, "approval_id": approval_id},
    )
    assert second.status_code == 200, second.text
    return second.json()
