from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient


def test_phase58_host_fs_list_returns_metadata_only(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = _fake_home(tmp_path, monkeypatch)
    desktop = home / "Desktop"
    (desktop / "alpha.txt").write_text("secret file content should not leak", encoding="utf-8")
    (desktop / "Project").mkdir()
    (desktop / ".hidden").write_text("hidden", encoding="utf-8")
    (desktop / "api_token.txt").write_text("token=hidden", encoding="utf-8")

    response = client.post(
        "/api/tools/execute",
        json={
            "member_id": "mem_xiaoyao",
            "tool_name": "host.fs.list",
            "args": {"location": "desktop", "limit": 20},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    result = body["result"]
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["location"] == "desktop"
    assert body.get("approval") is None
    assert {item["name"] for item in result["items"]} >= {"alpha.txt", "Project"}
    assert "secret file content" not in serialized
    assert str(home) not in serialized
    assert ".hidden" not in serialized
    assert result["redaction_summary"]["sensitive_names_redacted"] == 1
    assert "[REDACTED_SENSITIVE_NAME]" in serialized


def test_phase58_host_fs_list_rejects_traversal_and_sensitive_path(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = _fake_home(tmp_path, monkeypatch)
    (home / ".ssh").mkdir()

    traversal = client.post(
        "/api/tools/execute",
        json={
            "member_id": "mem_xiaoyao",
            "tool_name": "host.fs.list",
            "args": {"location": "desktop", "path": "../.ssh"},
        },
    )
    assert traversal.status_code == 403, traversal.text
    assert traversal.json()["error"]["details"]["reason"] == "host_fs_path_traversal_denied"

    sensitive = client.post(
        "/api/tools/execute",
        json={
            "member_id": "mem_xiaoyao",
            "tool_name": "host.fs.list",
            "args": {"location": "authorized", "path": str(home / ".ssh")},
        },
    )
    assert sensitive.status_code == 403, sensitive.text
    assert sensitive.json()["error"]["details"]["reason"] == "host_fs_sensitive_path_denied"


def test_phase58_chat_desktop_files_executes_readonly_tool_without_clarification(
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
            "session_id": "phase58-host-fs-chat",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "我桌面有哪些文件"},
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )
    event_names = {event["event"] for event in events}
    completed = next(event for event in events if event["event"] == "response.completed")
    payload = completed["payload"]["response_plan"]["structured_payload"]

    assert "tool.completed" in event_names
    assert "task.created" not in event_names
    assert "alpha.txt" in reply
    assert "目标文件或范围" not in reply
    assert "备份" not in reply
    assert payload["route_semantics"]["route"] == "host_filesystem_list"
    assert payload["host_filesystem_list"]["items"][0]["name"] == "alpha.txt"


def _fake_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    for name in ["Desktop", "Downloads", "Documents"]:
        (home / name).mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


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
