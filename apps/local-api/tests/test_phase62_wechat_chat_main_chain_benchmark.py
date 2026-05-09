from __future__ import annotations

import json
import hashlib
import importlib.util
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar, cast

from app.services import chat as chat_module
from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from fastapi.testclient import TestClient

OFFICE_DEPS_AVAILABLE = all(
    importlib.util.find_spec(name) is not None for name in ["docx", "openpyxl", "pptx"]
)


def test_phase62_wechat_chat_main_chain_benchmark_reports_quality_latency_and_coverage(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    _install_fake_wechat(client, Phase62WechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase62-peer")
    _prepare_fake_home(tmp_path, monkeypatch)

    with _Phase62Site() as site:
        turns = [
            "你好，简单打个招呼。",
            "记住：我喜欢先给结论。",
            "我刚才喜欢什么？",
            "你是真人吗？",
            "我桌面有哪些文件",
            f"看一下这个网站 {site.url('/page')}",
            "执行命令 echo phase62",
        ]
        if OFFICE_DEPS_AVAILABLE:
            _install_office_skill(client)
            turns.append(
                "Office Skill 生成 Word 项目周报，内容包括本周完成接口评审，"
                "风险是上线窗口紧，下一步要补自动化测试。"
            )

        for index, text in enumerate(turns, start=1):
            previous_send_count = len(Phase62WechatClient.send_calls)
            Phase62WechatClient.events = [
                _text_event(f"evt-phase62-{index}", "wxid-phase62-peer", text)
            ]
            routed = client.post("/api/channels/providers/wechat/poll-once")
            assert routed.status_code == 200, routed.text
            assert routed.json()["chat_turns_created"] == 1, routed.text
            client.post("/api/channels/providers/wechat/deliver-due")
            _wait_for_new_send(previous_send_count)

    benchmark = client.post(
        "/api/benchmarks/runs",
        json={
            "benchmark_type": "wechat_chat_main_chain",
            "scenario": {"turn_limit": 50, "require_real_wechat": False},
        },
    )
    assert benchmark.status_code == 200, benchmark.text
    payload = benchmark.json()
    metrics = payload["metrics"]
    summary = payload["scenario"]["report"]

    assert payload["benchmark_type"] == "wechat_chat_main_chain"
    assert metrics["turn_count"] >= len(turns)
    assert metrics["coverage_rate"] > 0.5
    assert (
        metrics["avg_first_token_latency_ms"] is None
        or metrics["avg_first_token_latency_ms"] >= 0
    )
    assert metrics["avg_turn_latency_ms"] is None or metrics["avg_turn_latency_ms"] >= 0
    assert (
        metrics["avg_wechat_delivery_latency_ms"] is None
        or metrics["avg_wechat_delivery_latency_ms"] >= 0
    )
    assert summary["turns"]
    assert "direct" in summary["coverage"]
    assert "persona" in summary["coverage"]
    assert "browser" in summary["coverage"]
    assert "terminal" in summary["coverage"]
    assert "wechat_delivery" in summary["coverage"]
    if OFFICE_DEPS_AVAILABLE:
        assert summary["coverage"]["skill"] is True
    assert summary["optimization_focus"]


def test_phase62_wechat_terminal_readonly_command_executes_without_approval(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, Phase62WechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase62-peer")
    previous_send_count = len(Phase62WechatClient.send_calls)
    Phase62WechatClient.events = [
        _text_event("evt-terminal", "wxid-phase62-peer", "执行命令 echo phase62-terminal")
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1, routed.text
    client.post("/api/channels/providers/wechat/deliver-due")
    _wait_for_new_send(previous_send_count)
    assert "phase62-terminal" in Phase62WechatClient.send_calls[-1]["text"]

    turns = client.get("/api/chat/conversations").json()["items"][0]["conversation_id"]
    conversation = client.get(f"/api/chat/conversations/{turns}").json()
    terminal_turn = next(
        item for item in conversation["messages"] if "phase62-terminal" in item["content_text"]
    )
    trace_id = terminal_turn["trace_id"]
    trace = client.get(f"/api/traces/{trace_id}")
    assert trace.status_code == 200, trace.text
    assert any(
        span["span_type"] == "tool.call"
        and span.get("metadata", {}).get("tool_name") == "terminal.run"
        for span in trace.json()["spans"]
    )
    text = _latest_reply(client, terminal_turn["turn_id"])
    assert "phase62-terminal" in text
    assert "审批" not in text
    assert "未执行" not in text


def test_phase62_wechat_complex_model_reply_delivers_single_revised_message(
    client: TestClient,
    monkeypatch,
) -> None:
    _install_fake_wechat(client, Phase62WechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase62-continuation-peer")
    brain_id = _create_local_brain(client)
    bound = client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    assert bound.status_code == 200, bound.text
    calls: list[list[dict[str, str]]] = []

    async def fake_stream_chat(
        self: Any,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ):
        del self, cancel_token
        calls.append(request.messages)
        if len(calls) == 1:
            text = "trace_id=trc_phase62。先看办公 AI 场景，后面我再补。"
        else:
            text = (
                "📘 结论：办公 AI 场景要同时看质量和耗时，先把等待感降下来，"
                "再把回复写得更像真实助手。\n"
                "📌 下一步：记录每轮耗时、低质标签和修复建议；对慢点按模型、"
                "上下文、工具和投递分开处理。"
            )
        yield ModelStreamEvent(event="started")
        yield ModelStreamEvent(event="delta", text=text)
        yield ModelStreamEvent(event="completed", usage={"output_tokens": len(text)})

    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", fake_stream_chat)
    previous_send_count = len(Phase62WechatClient.send_calls)
    Phase62WechatClient.events = [
        _text_event(
            "evt-phase62-continuation",
            "wxid-phase62-continuation-peer",
            "帮我分析网上用户最关心的办公 AI 场景，重点看聊天质量、耗时和优化闭环。",
        )
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    assert routed.json()["chat_turns_created"] == 1
    _wait_for_new_send(previous_send_count)

    sent = Phase62WechatClient.send_calls[-1]["text"]
    assert "办公 AI 场景" in sent
    assert "trace_id" not in sent
    assert len(calls) in {1, 2}
    bindings = client.get(
        "/api/channels/delivery-bindings",
        params={"provider": "wechat", "limit": 5},
    ).json()["items"]
    binding = bindings[0]
    turn_id = binding["turn_id"]
    same_turn_bindings = client.get(
        "/api/channels/delivery-bindings",
        params={"provider": "wechat", "turn_id": turn_id},
    ).json()["items"]
    events = client.get(f"/api/chat/turns/{turn_id}/events").json()["items"]
    response_deltas = [item for item in events if item["event_type"] == "response.delta"]
    response_completed = next(
        item for item in events if item["event_type"] == "response.completed"
    )
    continuation = (
        response_completed["payload"]["payload"]["response_plan"]["structured_payload"].get(
            "continuation"
        )
        or {}
    )

    assert len(same_turn_bindings) == 1
    assert len(response_deltas) == 1
    assert response_deltas[0]["payload"]["payload"]["text"] == sent
    if len(calls) == 2:
        assert continuation.get("enabled") is True
        assert continuation.get("iterations") == 1
        assert continuation.get("used_revision") is True
        assert continuation.get("quality_verdict") in {"good", "revise"}
        assert isinstance(continuation.get("quality_tags") or [], list)
        assert isinstance(continuation.get("diagnostics") or {}, dict)
        assert continuation.get("initial_latency_ms") is not None
        assert continuation.get("total_latency_ms") is not None
        assert continuation.get("latency_budget_ms") == 20_000
    else:
        assert continuation == {}


def test_phase62_local_and_wechat_plain_chat_keep_same_boundary_stance(
    client: TestClient,
    monkeypatch,
) -> None:
    _install_fake_wechat(client, Phase62WechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase62-equivalence-peer")
    brain_id = _create_local_brain(client)
    bound = client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    assert bound.status_code == 200, bound.text

    async def fake_stream_chat(
        self: Any,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ):
        del self, cancel_token
        user_text = request.messages[-1]["content"]
        if "不要调用工具" in user_text:
            text = (
                "结论先说：普通聊天主链要让当前消息优先，"
                "不要把审批口吻、任务状态和系统腔混进正文。"
            )
        else:
            text = "先按当前问题直接回答，再按需要补最近上下文。"
        yield ModelStreamEvent(event="started")
        yield ModelStreamEvent(event="delta", text=text)
        yield ModelStreamEvent(event="completed", usage={"output_tokens": len(text)})

    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", fake_stream_chat)

    local_turn = client.post(
        "/api/chat/turn",
        json={
            "member_id": "mem_xiaoyao",
            "session_id": "phase62-local-equivalence",
            "input": {
                "type": "text",
                "text": "解释普通聊天主链为什么要更干净，不要调用工具，也不要执行操作。",
            },
        },
    )
    assert local_turn.status_code == 200, local_turn.text
    local_stream = client.get(local_turn.json()["stream_url"])
    local_reply = _parse_local_sse(local_stream.text)

    previous_send_count = len(Phase62WechatClient.send_calls)
    Phase62WechatClient.events = [
        _text_event(
            "evt-phase62-equivalence",
            "wxid-phase62-equivalence-peer",
            "解释普通聊天主链为什么要更干净，不要调用工具，也不要执行操作。",
        )
    ]
    routed = client.post("/api/channels/providers/wechat/poll-once")
    assert routed.status_code == 200, routed.text
    client.post("/api/channels/providers/wechat/deliver-due")
    _wait_for_new_send(previous_send_count)
    wechat_reply = Phase62WechatClient.send_calls[-1]["text"]

    for reply in (local_reply, wechat_reply):
        assert "当前消息优先" in reply
        assert "审批口吻" in reply
        assert "任务状态" in reply
        assert "没有待确认" not in reply
        assert "需要审批" not in reply
        assert "已执行" not in reply


def _install_fake_wechat(client: TestClient, factory: type[Phase62WechatClient]) -> None:
    factory.reset()
    registry = cast(Any, client.app).state.registry
    registry.config.channels.providers["wechat"].enabled = True
    registry.config.channels.providers["wechat"].poll_enabled = True
    connector = registry.channel_binding_service.connector_registry().get("wechat")
    cast(Any, connector).set_client_factory(factory)


def _bind_real_wechat(client: TestClient) -> None:
    started = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": "Phase62 微信"},
    )
    assert started.status_code == 200, started.text
    finalized = client.post(
        f"/api/channels/bind-sessions/{started.json()['bind_session_id']}/finalize"
    )
    assert finalized.status_code == 200, finalized.text


def _pair_peer(client: TestClient, peer_ref: str) -> None:
    Phase62WechatClient.events = [_text_event(f"evt-pair-{peer_ref}", peer_ref, "申请配对")]
    response = client.post("/api/channels/providers/wechat/poll-once")
    assert response.status_code == 200, response.text
    assert response.json()["created_pairing_requests"] == 1, response.text
    pairings = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    )
    assert pairings.status_code == 200, pairings.text
    peer_hash = _sha256_ref(peer_ref)
    pairing = next(
        item for item in pairings.json()["items"] if item["peer_ref_redacted"] == peer_hash
    )
    approved = client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaoyao", "reason": "phase62"},
    )
    assert approved.status_code == 200, approved.text
    Phase62WechatClient.events = []


def _install_office_skill(client: TestClient) -> None:
    installed = client.post(
        "/api/skills/install",
        json={"source_type": "repository_ref", "source_uri": "clawhub:official/office/word-report"},
    )
    assert installed.status_code == 200, installed.text
    bundle_id = installed.json()["bundle"]["bundle_id"]
    skill_id = installed.json()["skills"][0]["skill_id"]
    enabled = client.post(
        f"/api/plugins/{bundle_id}/enable",
        json={"actor_member_id": "mem_xiaoyao"},
    )
    assert enabled.status_code == 200, enabled.text
    grant = client.post(
        f"/api/skills/{skill_id}/grants",
        json={"allowed_tools": ["office.word.generate"]},
    )
    assert grant.status_code == 200, grant.text


def _create_local_brain(client: TestClient) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": "Phase62 continuation brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "phase62-continuation-model",
            "is_local": True,
            "context_window": 4096,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["brain_id"])


def _prepare_fake_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    desktop = home / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    (desktop / "alpha.txt").write_text("alpha content", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))


def _wait_for_new_send(previous_count: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(Phase62WechatClient.send_calls) > previous_count:
            return
        time.sleep(0.05)
    raise AssertionError("new WeChat send was not observed")


def _sha256_ref(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _latest_reply(client: TestClient, turn_id: str) -> str:
    events = client.get(f"/api/chat/turns/{turn_id}/events").json()["items"]
    return "".join(
        str(item.get("payload", {}).get("payload", {}).get("text", ""))
        for item in events
        if item["event_type"] == "response.delta"
    )


def _parse_local_sse(raw: str) -> str:
    chunks: list[str] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data: "):
                continue
            payload = cast(dict[str, Any], json.loads(line[6:]))
            if payload.get("event") == "response.delta":
                chunks.append(str(payload.get("payload", {}).get("text", "")))
    return "".join(chunks)


def _text_event(event_id: str, peer_ref: str, text: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "source": {"peer_ref": peer_ref, "chat_type": "private", "display_name": "外部联系人"},
        "message": {"content_type": "text", "text": text},
    }


class Phase62WechatClient:
    events: ClassVar[list[dict[str, Any]]] = []
    send_calls: ClassVar[list[dict[str, str]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.events = []
        cls.send_calls = []

    @classmethod
    def create(cls, **kwargs: Any) -> Phase62WechatClient:
        del kwargs
        return cls()

    async def start_login(self) -> dict[str, Any]:
        return {
            "status": "qr_ready",
            "qrcode": "QR_PHASE62",
            "qrcode_image_content": "QR_IMAGE_PHASE62",
            "expires_at": "2030-01-01T00:00:00+00:00",
        }

    async def wait_for_login(
        self,
        qrcode: str,
        timeout: float | int | None = None,
    ) -> dict[str, Any]:
        del qrcode, timeout
        return {
            "status": "confirmed",
            "account_id": "wxid-phase62-account",
            "display_name": "Phase62 微信",
        }

    async def poll_events(self, account_id: str) -> Any:
        assert account_id == "wxid-phase62-account"
        for event in list(self.__class__.events):
            yield event

    async def send_text(self, *, account_id: str, user_id: str, text: str) -> dict[str, Any]:
        self.__class__.send_calls.append(
            {"account_id": account_id, "user_id": user_id, "text": text}
        )
        return {"message_id": f"msg-{len(self.__class__.send_calls)}"}

    async def download_media(self, *, account_id: str, media_id: str) -> bytes:
        del account_id, media_id
        return b""


class _Phase62Site:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Phase62Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> _Phase62Site:
        self._thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        host = self._server.server_address[0]
        port = self._server.server_address[1]
        return f"http://{host}:{port}{path}"


class _Phase62Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = (
            "<html><head><title>Phase62 页面</title></head>"
            "<body><h1>Phase62 页面</h1><p>这是微信聊天基线浏览器只读验证页。</p></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args
