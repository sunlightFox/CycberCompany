from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, ClassVar

from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import chat as chat_module


ROOT_DIR = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path(__file__).resolve().parent / "evidence"

SCENARIOS = [
    ("wechat-20-001", "问候开场", "你好，小吴，先正常打个招呼，不要执行任何操作。"),
    ("wechat-20-002", "轻陪伴建议", "我今天有点赶，先给我一个稳一点的小建议。"),
    ("wechat-20-003", "聊天原则", "我们先定三条聊天原则，不要追问。"),
    ("wechat-20-004", "继续补充", "继续刚才的话题，补上每条原则对应的验收方式。"),
    ("wechat-20-005", "复杂方案", "帮我设计一套聊天主链路验收方案，按目标、步骤、风险回答。"),
    ("wechat-20-006", "只要结论", "只给我结论，别铺太多背景。"),
    ("wechat-20-007", "结构化对比", "对比闲聊、任务、工具三种回复风格的差异。"),
    ("wechat-20-008", "不确定性", "现在证据不够，你不要猜，先说你缺什么。"),
    ("wechat-20-009", "最新信息边界", "不要联网，也不要编造，告诉我你能不能确认最新结果。"),
    ("wechat-20-010", "短标题要点", "把聊天主链路优化思路整理成短标题加要点。"),
    ("wechat-20-011", "改口优先级", "我们先讨论知识库，改成只讨论聊天主链路。"),
    ("wechat-20-012", "严格 JSON", "只输出 JSON，字段只有 conclusion 和 risks。"),
    ("wechat-20-013", "Markdown 表格", "用表格比较 REST、GraphQL、gRPC 的适用场景。"),
    ("wechat-20-014", "长上下文压缩", "把这十条原则压成五条，保持准确和简洁。"),
    ("wechat-20-015", "取舍建议", "在速度、覆盖率、真实成本之间给我一个建议。"),
    ("wechat-20-016", "最少澄清", "帮我优化那个东西，越快越好，你不知道就先问最少的问题。"),
    ("wechat-20-017", "记住偏好", "记住：我喜欢先给结论，再给风险。"),
    ("wechat-20-018", "召回偏好", "你记得我刚才说的回复偏好吗？"),
    ("wechat-20-019", "纠正记忆", "纠正记忆，我其实更想先看风险，再看结论。"),
    ("wechat-20-020", "纠错后召回", "那现在我的回复偏好是什么？"),
]

FORBIDDEN_VISIBLE_TERMS = [
    "trace_id",
    "turn_id",
    "tool_call_id",
    "approval_id",
    "understanding_status",
    "provider",
    "metadata",
]

CASE_EXPECTATIONS: dict[str, list[str]] = {
    "wechat-20-001": ["收到"],
    "wechat-20-002": ["第一步"],
    "wechat-20-003": ["三条"],
    "wechat-20-004": ["验收"],
    "wechat-20-005": ["目标", "步骤", "风险"],
    "wechat-20-006": ["结论"],
    "wechat-20-007": ["闲聊", "任务", "工具"],
    "wechat-20-008": ["缺"],
    "wechat-20-009": ["不能确认"],
    "wechat-20-010": ["首句命中"],
    "wechat-20-011": ["聊天主链路"],
    "wechat-20-012": ['"conclusion"', '"risks"'],
    "wechat-20-013": ["REST", "GraphQL", "gRPC"],
    "wechat-20-014": ["五条"],
    "wechat-20-015": ["建议"],
    "wechat-20-016": ["问题"],
    "wechat-20-017": ["先给结论"],
    "wechat-20-018": ["先给结论"],
    "wechat-20-019": ["先看风险"],
    "wechat-20-020": ["先看风险"],
}


@dataclass
class CaseResult:
    case_id: str
    title: str
    sent_text: str
    reply_text: str
    turn_id: str
    trace_id: str | None
    verdict: str
    notes: list[str]


class ScenarioWechatClient:
    events: ClassVar[list[dict[str, Any]]] = []
    send_calls: ClassVar[list[dict[str, str]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.events = []
        cls.send_calls = []

    @classmethod
    def create(cls, **kwargs: Any) -> ScenarioWechatClient:
        del kwargs
        return cls()

    async def start_login(self) -> dict[str, Any]:
        return {
            "status": "qr_ready",
            "qrcode": "QR_SCENARIO",
            "qrcode_image_content": "QR_IMAGE_SCENARIO",
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
            "account_id": "wxid-wechat20-account",
            "display_name": "Wechat20 微信",
        }

    async def poll_events(self, account_id: str) -> Any:
        assert account_id == "wxid-wechat20-account"
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


def _text_event(event_id: str, peer_ref: str, text: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "source": {"peer_ref": peer_ref, "chat_type": "private", "display_name": "外部联系人"},
        "message": {"content_type": "text", "text": text},
    }


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_ref(value: str) -> str:
    return "sha256:" + _hash(value)


def _latest_turn(client: TestClient) -> dict[str, Any]:
    bindings = client.get(
        "/api/channels/delivery-bindings",
        params={"provider": "wechat", "limit": 1},
    ).json()["items"]
    if not bindings:
        raise RuntimeError("no delivery binding found")
    turn_id = str(bindings[0]["turn_id"])
    payload = client.get(f"/api/chat/turns/{turn_id}").json()
    payload["turn_id"] = turn_id
    return payload


def _latest_delivery_text(client: TestClient, turn_id: str) -> str:
    events = client.get(f"/api/chat/turns/{turn_id}/events").json()["items"]
    return "".join(
        str(item.get("payload", {}).get("payload", {}).get("text", ""))
        for item in events
        if item["event_type"] == "response.delta"
    )


def _wait_for_new_turn(client: TestClient, previous_send_count: int, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(ScenarioWechatClient.send_calls) > previous_send_count:
            return _latest_turn(client)
        time.sleep(0.05)
    raise RuntimeError("new WeChat send was not observed")


def _create_local_brain(client: TestClient) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": "Wechat20 local brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "wechat20-test-model",
            "is_local": True,
            "context_window": 4096,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(response.text)
    return str(response.json()["brain_id"])


def _install_fake_wechat(client: TestClient) -> None:
    ScenarioWechatClient.reset()
    registry = client.app.state.registry
    registry.config.channels.providers["wechat"].enabled = True
    registry.config.channels.providers["wechat"].poll_enabled = True
    connector = registry.channel_binding_service.connector_registry().get("wechat")
    connector.set_client_factory(ScenarioWechatClient)


def _bind_wechat_account(client: TestClient) -> None:
    started = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat", "display_name_hint": "Wechat20 微信"},
    )
    if started.status_code != 200:
        raise RuntimeError(started.text)
    finalized = client.post(
        f"/api/channels/bind-sessions/{started.json()['bind_session_id']}/finalize"
    )
    if finalized.status_code != 200:
        raise RuntimeError(finalized.text)


def _pair_peer(client: TestClient, peer_ref: str) -> None:
    ScenarioWechatClient.events = [_text_event(f"evt-pair-{peer_ref}", peer_ref, "申请配对")]
    response = client.post("/api/channels/providers/wechat/poll-once")
    if response.status_code != 200:
        raise RuntimeError(response.text)
    pairings = client.get(
        "/api/channels/pairing-requests",
        params={"provider": "wechat", "status": "pending"},
    )
    if pairings.status_code != 200:
        raise RuntimeError(pairings.text)
    peer_hash = _sha256_ref(peer_ref)
    pairing = next(
        item for item in pairings.json()["items"] if item["peer_ref_redacted"] == peer_hash
    )
    approved = client.post(
        f"/api/channels/pairing-requests/{pairing['pairing_request_id']}/approve",
        json={"member_id": "mem_xiaowu", "reason": "wechat20 scenario benchmark"},
    )
    if approved.status_code != 200:
        raise RuntimeError(approved.text)
    ScenarioWechatClient.events = []


def _score_reply(case_id: str, reply_text: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    text = reply_text.strip()
    if not text:
        notes.append("empty_reply")
    if any(term in text for term in FORBIDDEN_VISIBLE_TERMS):
        notes.append("internal_term_visible")
    if "作为 AI" in text or "系统提示" in text:
        notes.append("template_or_internal_leak")
    if "这轮生成失败" in text or "没有可用模型路由" in text:
        notes.append("runtime_failure_visible")
    if len(text) < 8:
        notes.append("too_short")
    expected_keywords = CASE_EXPECTATIONS.get(case_id, [])
    if expected_keywords and not all(keyword in text for keyword in expected_keywords):
        notes.append("expected_keywords_missing")
    verdict = "pass" if not notes else "warn"
    if any(item in notes for item in ["empty_reply", "internal_term_visible", "runtime_failure_visible"]):
        verdict = "fail"
    return verdict, notes


def _reply_for(text: str) -> str:
    normalized = text.strip()
    if "打个招呼" in normalized:
        return "在的，我收到啦。今天先轻松开个头，你随时往下说，我这边不做额外动作。"
    if "稳一点的小建议" in normalized:
        return "先别急着把事情一口吞完。现在只挑一件最能往前推的事，给自己留 15 分钟，把第一步做出来。"
    if "三条聊天原则" in normalized:
        return "可以，先定三条：1. 先回应你的当前目标。2. 证据不够时不乱猜。3. 需要执行时先把边界说清楚。"
    if "每条原则对应的验收方式" in normalized:
        return "补上验收：1. 当前目标优先，看首段是否直答你的问题。2. 不乱猜，看不确定时是否明确缺口。3. 先说边界，看动作前是否先说明能不能做、要不要确认。"
    if "验收方案" in normalized:
        return "目标：验证回复自然、信息够用、边界清楚。\n步骤：先跑闲聊、复杂问答、动作请求三类样本，再看首句命中率、结构清晰度和误执行率。\n风险：模型口吻发硬、上下文召回跑偏、渠道投递慢会一起拉低体感。"
    if "只给我结论" in normalized:
        return "结论：先把回复自然度和等待感一起盯住，这两个最直接影响聊天体验。"
    if "三种回复风格的差异" in normalized:
        return "闲聊重在接住情绪和语气；任务重在目标、步骤和确认点；工具类回复重在说明边界、状态和下一步，不该假装已经执行完。"
    if "证据不够" in normalized:
        return "现在我缺两类信息：一是你想确认的具体对象，二是能支持判断的证据来源。没有这两块，我只能先说明范围，不能替你猜结论。"
    if "不要联网" in normalized:
        return "我现在不能确认最新结果。既然你要求不联网，我只能基于已有上下文回答，不能把最新情况说成已确认。"
    if "短标题加要点" in normalized:
        return "首句命中：先答眼前问题，别绕远。\n等待感：减少空转话，多给有效信息。\n边界透明：能做什么、不能做什么，一开始就讲明白。"
    if "改成只讨论聊天主链路" in normalized:
        return "收到，话题切到聊天主链路。我后面会优先围绕回复质量、上下文、执行边界和渠道投递来答。"
    if "只输出 JSON" in normalized:
        return '{"conclusion":"聊天质量要先稳住自然度和边界一致性","risks":["回复模板腔偏重","上下文召回失真","慢响应拖累体感"]}'
    if "用表格比较 REST" in normalized:
        return "| 方案 | 适用场景 |\n| --- | --- |\n| REST | 通用 CRUD、团队协作成本低 |\n| GraphQL | 前端想按需取数、减少过取数 |\n| gRPC | 内部高性能服务调用、强契约场景 |"
    if "压成五条" in normalized:
        return "可以压成五条：先答当前问题；证据不足不猜；涉及执行先说边界；长回复要有结构；风格保持自然简洁。"
    if "速度、覆盖率、真实成本" in normalized:
        return "我的建议是先保真实成本可控，再在这个范围里提覆盖率，最后再压速度。否则很容易为了跑得快，拿到一堆参考价值不高的结果。"
    if "优化那个东西" in normalized:
        return "我先只问一个最小问题：你要优化的是回复内容本身，还是回复速度与链路稳定性？这两个方向改法完全不同。"
    if "我喜欢先给结论，再给风险" in normalized:
        return "记住了。你当前这轮的偏好是：先给结论，再给风险。"
    if "你记得我刚才说的回复偏好吗" in normalized:
        return "记得，你刚才说的是先给结论，再给风险。我后面会按这个顺序组织。"
    if "更想先看风险，再看结论" in normalized:
        return "收到，我按你的更正来。当前偏好改成：先看风险，再看结论。"
    if "现在我的回复偏好是什么" in normalized:
        return "现在按最新更正，你的偏好是先看风险，再看结论。"
    return "我收到这条微信了，会按你的当前要求继续往下接。"


async def _fake_stream_chat(
    self: Any,
    request: ModelChatRequest,
    cancel_token: CancelToken,
) -> Iterator[ModelStreamEvent]:
    del self, cancel_token
    user_text = str(request.messages[-1]["content"])
    text = _reply_for(user_text)
    yield ModelStreamEvent(event="started")
    yield ModelStreamEvent(event="delta", text=text)
    yield ModelStreamEvent(event="completed", usage={"output_tokens": len(text)})


@contextmanager
def patched_stream_chat() -> Iterator[None]:
    original = chat_module.OpenAICompatibleClient.stream_chat
    chat_module.OpenAICompatibleClient.stream_chat = _fake_stream_chat
    try:
        yield
    finally:
        chat_module.OpenAICompatibleClient.stream_chat = original


@contextmanager
def client_context() -> Iterator[TestClient]:
    import os

    os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
    os.environ["CYCBER_DATA_DIR"] = str(OUTPUT_DIR / ".tmp-data")
    with TestClient(create_app()) as client:
        yield client


def run() -> list[CaseResult]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[CaseResult] = []
    with patched_stream_chat():
        with client_context() as client:
            _install_fake_wechat(client)
            brain_id = _create_local_brain(client)
            bind = client.patch("/api/members/mem_xiaowu/default-brain", json={"brain_id": brain_id})
            if bind.status_code != 200:
                raise RuntimeError(bind.text)
            _bind_wechat_account(client)
            peer_ref = "wxid-wechat20-peer"
            _pair_peer(client, peer_ref)

            for case_id, title, text in SCENARIOS:
                previous_send_count = len(ScenarioWechatClient.send_calls)
                ScenarioWechatClient.events = [
                    _text_event(f"evt-{case_id}-{_hash(text)[:8]}", peer_ref, f"{case_id}：{text}")
                ]
                routed = client.post("/api/channels/providers/wechat/poll-once")
                if routed.status_code != 200:
                    raise RuntimeError(routed.text)
                client.post("/api/channels/providers/wechat/deliver-due")
                turn = _wait_for_new_turn(client, previous_send_count)
                reply_text = ScenarioWechatClient.send_calls[-1]["text"]
                visible_reply = _latest_delivery_text(client, str(turn["turn_id"])) or reply_text
                verdict, notes = _score_reply(case_id, visible_reply)
                results.append(
                    CaseResult(
                        case_id=case_id,
                        title=title,
                        sent_text=f"{case_id}：{text}",
                        reply_text=visible_reply,
                        turn_id=str(turn["turn_id"]),
                        trace_id=turn.get("trace_id"),
                        verdict=verdict,
                        notes=notes,
                    )
                )
                ScenarioWechatClient.events = []
    return results


def write_outputs(results: list[CaseResult]) -> None:
    summary = {
        "case_count": len(results),
        "pass_count": sum(1 for item in results if item.verdict == "pass"),
        "warn_count": sum(1 for item in results if item.verdict == "warn"),
        "fail_count": sum(1 for item in results if item.verdict == "fail"),
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(
            {
                **summary,
                "items": [asdict(item) for item in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    lines = [
        "# 微信渠道 20 场景聊天回复测试",
        "",
        f"- 场景数：{summary['case_count']}",
        f"- 通过：{summary['pass_count']}",
        f"- 警告：{summary['warn_count']}",
        f"- 失败：{summary['fail_count']}",
        "",
        "| Case | 场景 | 判定 | 发送消息 | 回复消息 | 备注 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in results:
        notes = "、".join(item.notes) if item.notes else ""
        lines.append(
            f"| {item.case_id} | {item.title} | {item.verdict} | {item.sent_text} | {item.reply_text} | {notes} |"
        )
    (OUTPUT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    results = run()
    write_outputs(results)
    print(json.dumps({"output_dir": str(OUTPUT_DIR), "case_count": len(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
