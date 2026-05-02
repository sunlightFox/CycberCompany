from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from fastapi.testclient import TestClient


def test_phase59_browser_read_page_executes_snapshot_without_task(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _TestSite() as site:
        conversation = client.get("/api/chat/conversations").json()["items"][0]
        turn = client.post(
            "/api/chat/turn",
            json={
                "session_id": "phase59-browser-read",
                "conversation_id": conversation["conversation_id"],
                "member_id": "mem_xiaoyao",
                "input": {
                    "type": "text",
                    "text": f"帮我看一下这网站有什么内容，{site.url('/page')}",
                },
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
    assert "我无法访问外部链接" not in reply
    assert "浏览网页" not in reply
    assert "Phase59 测试网页" in reply
    assert "只读网页能力正在工作" in reply
    assert payload["route_semantics"]["route"] == "browser_read_page"
    assert payload["route_semantics"]["tool_name"] == "browser.snapshot"
    assert payload["browser_read_page"]["title"] == "Phase59 测试网页"
    assert payload["task_status"]["status"] == "not_created"


def test_phase59_browser_read_page_blocks_metadata_url(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-browser-read-blocked",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "看看这个网页讲什么 http://169.254.169.254/latest/meta-data",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )
    event_names = {event["event"] for event in events}

    assert "tool.completed" not in event_names
    assert "task.created" not in event_names
    assert "安全策略已拒绝访问" in reply or "metadata 或私网敏感地址" in reply


class _TestSite:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _TestHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _TestSite:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        address = self._server.server_address
        host = address[0]
        port = address[1]
        assert isinstance(host, str)
        assert isinstance(port, int)
        return f"http://{host}:{port}{path}"


class _TestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = (
            "<html><head><title>Phase59 测试网页</title></head>"
            "<body><h1>Phase59 测试网页</h1>"
            "<p>这个页面说明只读网页能力正在工作。</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


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
