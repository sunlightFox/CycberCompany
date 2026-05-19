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
    assert payload["browser_read_page"]["page_state"]["status"] in {"observed", "actionable"}
    assert payload["browser_read_page"]["evidence_refs"]
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


def test_phase59_browser_search_with_citation_executes_without_task(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request, trace_id=None):  # noqa: ANN001,ANN202
        del trace_id
        if request.tool_name == "browser.search":
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "title": "Search Results",
                        "url": "https://example.test/search?q=chat+quality",
                        "http_status": 200,
                        "browser_evidence_id": "bev_test",
                        "content_preview": (
                            "<html><body><li>Chat quality regression report</li>"
                            "<li>Browser evidence summary</li></body></html>"
                        ),
                    },
                    "tool_call": type(
                        "ToolCall",
                        (),
                        {
                            "tool_call_id": "call_search",
                            "risk_level": type("Risk", (), {"value": "R2"})(),
                        },
                    )(),
                },
            )()
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-browser-search",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "请用浏览器搜索 chat quality，并总结结果，必须说明证据来源。",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )
    completed = next(event for event in events if event["event"] == "response.completed")
    payload = completed["payload"]["response_plan"]["structured_payload"]

    assert "task.created" not in {event["event"] for event in events}
    assert "证据来源" in reply
    assert payload["route_semantics"]["route"] == "browser_search_with_citation"
    assert payload["browser_workflow_result"]["status"] == "completed"
    assert payload["evidence_refs"][0]["browser_evidence_id"] == "bev_test"
    assert payload["browser_workflow_result"]["assessment"]["confidence"] == "medium"
    assert payload["browser_research_assessment"]["confidence"] == "medium"


def test_phase59_natural_browser_research_request_bypasses_clarification_and_executes(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request, trace_id=None):  # noqa: ANN001,ANN202
        del trace_id
        if request.tool_name == "browser.search":
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "title": "星海智研 搜索结果",
                        "url": "https://example.test/search?q=%E6%98%9F%E6%B5%B7%E6%99%BA%E7%A0%94",
                        "http_status": 200,
                        "browser_evidence_id": "bev_research",
                        "content_preview": (
                            "<html><body><li>融资进展较快</li>"
                            "<li>中小团队适用</li><li>交付节奏偏慢</li></body></html>"
                        ),
                    },
                    "tool_call": type(
                        "ToolCall",
                        (),
                        {
                            "tool_call_id": "call_research",
                            "risk_level": type("Risk", (), {"value": "R2"})(),
                        },
                    )(),
                },
            )()
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-browser-search-natural",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "请用浏览器搜索 星海智研 这家公司怎么样，整理成整体印象、可能优势、需要留意的风险三部分，并说明证据来源。",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )
    payload = next(
        event for event in events if event["event"] == "response.completed"
    )["payload"]["response_plan"]["structured_payload"]

    assert "你要处理的是哪个对象" not in reply
    assert "证据来源" in reply
    assert payload["route_semantics"]["route"] == "browser_search_with_citation"
    assert payload["browser_workflow_result"]["status"] == "completed"
    assert payload["browser_workflow_result"]["browser_research_plan"]["query"] == "星海智研 这家公司怎么样"
    assert payload["task_status"]["status"] == "not_created"


def test_phase59_browser_search_can_render_popular_explainer_style(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request, trace_id=None):  # noqa: ANN001,ANN202
        del trace_id
        if request.tool_name == "browser.search":
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "title": "海盐加碘 搜索结果",
                        "url": "https://example.test/search?q=iodized+salt",
                        "http_status": 200,
                        "browser_evidence_id": "bev_popular",
                        "content_preview": (
                            "<html><body><li>核心还是补碘，帮助减少碘缺乏带来的健康问题</li>"
                            "<li>并不是越贵越好，关键看是否符合日常食用需求</li>"
                            "<li>如果日常饮食已经很均衡，也要结合地区和个人情况理解</li></body></html>"
                        ),
                    },
                    "tool_call": type(
                        "ToolCall",
                        (),
                        {
                            "tool_call_id": "call_popular",
                            "risk_level": type("Risk", (), {"value": "R2"})(),
                        },
                    )(),
                },
            )()
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-browser-search-popular",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": (
                    "请用浏览器搜索 海盐为什么要加碘，整理成核心结论、常见误区、怎么理解三部分，"
                    "用通俗一点、像科普一样的方式说明，并标注证据来源。"
                ),
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )

    assert "先给一个背景提醒" in reply
    assert "核心结论：" in reply
    assert "常见误区：" in reply
    assert "证据来源" in reply


def test_phase59_browser_search_adds_timeliness_reminder_for_latest_queries(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request, trace_id=None):  # noqa: ANN001,ANN202
        del trace_id
        if request.tool_name == "browser.search":
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "title": "最新门诊安排 搜索结果",
                        "url": "https://example.test/search?q=latest+clinic+schedule",
                        "http_status": 200,
                        "browser_evidence_id": "bev_latest",
                        "content_preview": (
                            "<html><body><li>本周门诊安排有调整</li>"
                            "<li>部分科室周末停诊</li><li>建议出发前再次确认公告</li></body></html>"
                        ),
                    },
                    "tool_call": type(
                        "ToolCall",
                        (),
                        {
                            "tool_call_id": "call_latest",
                            "risk_level": type("Risk", (), {"value": "R2"})(),
                        },
                    )(),
                },
            )()
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-browser-search-latest",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "请用浏览器搜索 最新门诊安排，整理成主要变化、需要注意、出发前确认三部分，并说明证据来源。",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )

    assert "时效提醒" in reply
    assert "最新的官方页面或公告" in reply


def test_phase59_browser_search_adds_conflict_note_when_sources_diverge(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request, trace_id=None):  # noqa: ANN001,ANN202
        del trace_id
        if request.tool_name == "browser.search":
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "title": "办事指南 搜索结果",
                        "url": "https://example.test/search?q=guide+conflict",
                        "http_status": 200,
                        "browser_evidence_id": "bev_conflict",
                        "content_preview": (
                            "<html><body><li>有的资料写现场取号即可</li>"
                            "<li>也有资料写必须先线上预约</li>"
                            "<li>不同来源提醒以当地窗口最新通知为准</li></body></html>"
                        ),
                    },
                    "tool_call": type(
                        "ToolCall",
                        (),
                        {
                            "tool_call_id": "call_conflict",
                            "risk_level": type("Risk", (), {"value": "R2"})(),
                        },
                    )(),
                },
            )()
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-browser-search-conflict",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "请用浏览器搜索 某地居住证续签预约要求，整理成现有说法、可能差异、怎么处理三部分，并说明证据来源。",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )

    assert "说法不完全一致" in reply
    assert "以权威来源为准" in reply


def test_phase59_browser_search_marks_official_source_as_higher_confidence(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request, trace_id=None):  # noqa: ANN001,ANN202
        del trace_id
        if request.tool_name == "browser.search":
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "title": "某市政务服务网 公告",
                        "url": "https://service.example.gov.cn/notice",
                        "http_status": 200,
                        "browser_evidence_id": "bev_official",
                        "content_preview": (
                            "<html><body><li>申请入口已经统一迁移到政务服务网</li>"
                            "<li>现场办理时需携带身份证原件</li>"
                            "<li>工作时间以窗口公告为准</li></body></html>"
                        ),
                    },
                    "tool_call": type(
                        "ToolCall",
                        (),
                        {
                            "tool_call_id": "call_official",
                            "risk_level": type("Risk", (), {"value": "R2"})(),
                        },
                    )(),
                },
            )()
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-browser-search-official",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "请用浏览器搜索 某市落户办理入口，整理成入口、材料、办理提醒三部分，并说明证据来源。",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )

    assert "可信度：较高，可优先参考当前结果" in reply
    assert "偏官方口径" in reply


def test_phase59_browser_search_marks_community_source_as_preliminary(
    client: TestClient,
    monkeypatch,
) -> None:
    async def fake_execute(request, trace_id=None):  # noqa: ANN001,ANN202
        del trace_id
        if request.tool_name == "browser.search":
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "title": "论坛经验帖",
                        "url": "https://forum.example.com/thread/123",
                        "http_status": 200,
                        "browser_evidence_id": "bev_community",
                        "content_preview": (
                            "<html><body><li>楼主说可以现场加号</li>"
                            "<li>回帖里也有人说被要求先预约</li>"
                            "<li>更多像个人经验整理</li></body></html>"
                        ),
                    },
                    "tool_call": type(
                        "ToolCall",
                        (),
                        {
                            "tool_call_id": "call_community",
                            "risk_level": type("Risk", (), {"value": "R2"})(),
                        },
                    )(),
                },
            )()
        raise AssertionError(f"unexpected tool {request.tool_name}")

    monkeypatch.setattr(client.app.state.registry.tool_runtime, "execute", fake_execute)
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-browser-search-community",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "请用浏览器搜索 某医院挂号经验，整理成现有说法、风险点、如何核对三部分，并说明证据来源。",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )

    assert "可信度：中等偏谨慎，适合先当线索再交叉核对" in reply
    assert "整理页或社区口径" in reply


def test_phase59_desktop_native_request_returns_structured_boundary(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase59-desktop-boundary",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {
                "type": "text",
                "text": "请帮我操作桌面窗口，把当前桌面上的记事本窗口最小化，然后告诉我结果。",
            },
        },
    ).json()
    events = _parse_sse(client.get(turn["stream_url"]).text)
    reply = "".join(
        event["payload"].get("text", "") for event in events if event["event"] == "response.delta"
    )
    completed = next(event for event in events if event["event"] == "response.completed")
    payload = completed["payload"]["response_plan"]["structured_payload"]

    assert "没有执行" in reply
    assert "desktop.*" in reply
    assert payload["capability_boundary"]["status"] == "capability_not_supported"
    assert payload["capability_boundary"]["executed"] is False
    assert payload["route_semantics"]["route"] == "desktop_native_request"


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
