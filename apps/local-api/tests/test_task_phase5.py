from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.services.tools import _browser_launch_options, _redact_browser_failure
from fastapi.testclient import TestClient


def test_task_001_workflow_task_creates_replay_and_artifact(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "生成第五阶段任务报告",
            "auto_start": True,
            "client_request_id": "phase5-workflow-1",
        },
    ).json()
    repeated = client.post(
        "/api/tasks",
        json={
            "goal": "生成第五阶段任务报告",
            "auto_start": True,
            "client_request_id": "phase5-workflow-1",
        },
    ).json()
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    artifacts = client.get(f"/api/tasks/{task['task_id']}/artifacts").json()["items"]
    artifact = client.get(f"/api/artifacts/{artifacts[0]['artifact_id']}").json()

    assert task["status"] == "completed"
    assert repeated["task_id"] == task["task_id"]
    assert task["artifact_count"] == 1
    assert replay["steps"]
    assert replay["events"]
    assert replay["artifacts"][0]["checksum"].startswith("sha256:")
    assert artifact["content_preview"]


def test_task_002_terminal_requires_approval_and_deny_does_not_execute(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "执行终端命令",
            "constraints": {"command": "echo should-not-run"},
            "auto_start": True,
        },
    ).json()
    approval_id = task["current_approval_id"]

    denied = client.post(
        f"/api/approvals/{approval_id}/deny",
        json={"reason": "不允许执行"},
    ).json()
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()

    assert task["status"] == "waiting_approval"
    assert approval_id
    assert denied["status"] == "paused"
    assert denied["artifact_count"] == 0
    assert {approval["status"] for approval in replay["approvals"]} == {"denied"}
    assert not replay["artifacts"]


def test_task_003_terminal_approval_executes_and_logs_are_artifacts(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "执行终端命令",
            "constraints": {"command": "echo phase5-ok"},
            "auto_start": True,
        },
    ).json()
    approval_id = task["current_approval_id"]

    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"reason": "允许执行"},
    ).json()
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert approved["status"] == "completed"
    assert approved["artifact_count"] >= 2
    assert any(item["artifact_type"] == "terminal_log" for item in replay["artifacts"])
    assert any(call["tool_name"] == "terminal.run" for call in replay["tool_calls"])
    assert "approval.approved" in audit_text


def test_task_003b_approval_edit_replans_args_and_terminal_log_is_readable(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "执行终端命令",
            "constraints": {"command": "echo phase5-original"},
            "auto_start": True,
        },
    ).json()
    approval_id = task["current_approval_id"]

    edited = client.post(
        f"/api/approvals/{approval_id}/edit",
        json={
            "reason": "改用安全命令",
            "edited_payload": {"command": "echo phase5-edited"},
        },
    ).json()
    read_log = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.read_log",
            "args": {},
        },
    ).json()

    assert edited["status"] == "completed"
    assert "phase5-edited" in read_log["result"]["content_preview"]
    assert "phase5-original" not in read_log["result"]["content_preview"]


def test_task_004_file_tool_blocks_path_escape_and_delete_needs_approval(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "准备文件工具测试", "auto_start": False},
    ).json()
    write = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "file.write",
            "args": {"path": "outputs/sample.txt", "content": "hello"},
        },
    ).json()
    escaped = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "file.read",
            "args": {"path": "../outside.txt"},
        },
    )
    delete = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "file.delete",
            "args": {"path": "outputs/sample.txt"},
        },
    ).json()

    assert write["artifacts"][0]["checksum"].startswith("sha256:")
    assert escaped.status_code == 403
    assert escaped.json()["error"]["code"] == "TOOL_PERMISSION_DENIED"
    assert delete["approval"]["status"] == "pending"
    assert delete["tool_call"]["status"] == "approval_required"


def test_task_004b_tool_idempotency_replays_completed_call(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "准备工具幂等测试", "auto_start": False},
    ).json()
    payload = {
        "task_id": task["task_id"],
        "tool_name": "file.write",
        "idempotency_key": "phase5-file-write-once",
        "args": {"path": "outputs/idempotent.txt", "content": "once"},
    }

    first = client.post("/api/tools/execute", json=payload).json()
    second = client.post("/api/tools/execute", json=payload).json()

    assert second["tool_call"]["tool_call_id"] == first["tool_call"]["tool_call_id"]
    assert second["artifacts"][0]["artifact_id"] == first["artifacts"][0]["artifact_id"]


def test_task_005_tools_registry_and_chat_task_integration(client: TestClient) -> None:
    tools = client.get("/api/tools").json()["items"]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase5-chat",
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "请执行一个文件夹整理任务"},
        },
    ).json()
    stream_text = client.get(f"/api/chat/stream/{turn['turn_id']}").text
    events = client.get(f"/api/chat/turns/{turn['turn_id']}/events").json()["items"]

    assert {"terminal.run", "browser.screenshot", "file.write"}.issubset(
        {item["tool_name"] for item in tools}
    )
    assert "task.created" in stream_text
    assert "task.completed" in stream_text
    assert any(event["event_type"] == "task.created" for event in events)


def test_task_006_browser_launch_options_prefer_configured_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = tmp_path / "chrome.exe"
    browser_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTABLE_PATH", str(browser_path))
    monkeypatch.setenv("CYCBER_BROWSER_CHANNEL", "msedge")

    assert _browser_launch_options() == {"executable_path": str(browser_path)}


def test_task_007_browser_launch_options_use_channel_without_configured_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CYCBER_BROWSER_EXECUTABLE_PATH", raising=False)
    monkeypatch.setenv("CYCBER_BROWSER_CHANNEL", "msedge")

    assert _browser_launch_options() == {"channel": "msedge"}


def test_task_008_browser_failure_redacts_configured_executable_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    browser_path = tmp_path / "chrome.exe"
    browser_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTABLE_PATH", str(browser_path))

    reason = _redact_browser_failure(f"Executable does not exist at {browser_path}")

    assert str(browser_path) not in reason
    assert "[REDACTED_BROWSER_PATH]" in reason
