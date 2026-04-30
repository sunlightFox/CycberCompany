from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pytest
from app.services import chat as chat_module
from app.services.chat_safety import ChatTaskStatusPresenter, ChatVisibleOutputFilter
from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from core_types import (
    ChatEventType,
    ConversationContext,
    MemberStatus,
    MemberSummary,
    TaskMode,
    TaskStatus,
)
from fastapi.testclient import TestClient


def test_phase35_suite_contracts_release_summary_and_no_new_migration(
    client: TestClient,
) -> None:
    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_module = {item["name"]: item for item in contracts}
    registry = cast(Any, client.app).state.registry

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    phase35 = report["summary"]["phase35"]

    assert _latest_migration() == "025_browser_sessions.sql"
    assert "suite_phase35_chat_safety_state_semantics" in {
        item["suite_id"] for item in suites
    }
    for module in [
        "ChatStreamSafetyFilter",
        "ModelContextRedactionBoundary",
        "ChatTurnAccessPolicy",
        "ChatTaskStatusSemantics",
        "HighPrivacyLocalFirstRouting",
        "ProductionGuardCleanup",
    ]:
        assert by_module[module]["status"] == "implemented"
    assert completed["status"] == "ready_for_release"
    assert phase35["suite_id"] == "suite_phase35_chat_safety_state_semantics"
    assert phase35["registered_cases"] == 8
    assert phase35["stream_final_consistency"]["implemented"] is True
    assert phase35["context_redaction"]["model_safe_boundary"] is True
    assert phase35["production_guard_cleanup"]["phase31_guard_not_in_model_path"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase35"]["registered"] is True
    assert any(item["source_type"] == "phase35_chat_safety_state_semantics" for item in evidence)
    assert "phase35" in diagnostic
    assert "phase35_chat_safety_state_semantics" in diagnostic


def test_phase35_stream_delta_is_filtered_before_sse_and_final_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_stream(
        self: Any,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ):
        assert request.privacy_level == "high"
        yield ModelStreamEvent(event="started")
        yield ModelStreamEvent(event="delta", text="trace_id=tr")
        yield ModelStreamEvent(
            event="delta",
            text=(
                "c_phase35 approval_id=apr_phase35 browser.download R3 "
                "api_key=sk-phase35-secret-value C:\\Users\\Administrator\\secret.txt"
            ),
        )
        yield ModelStreamEvent(event="completed", usage={"output_tokens": 18})

    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", fake_stream)
    brain_id = _create_local_brain(client)
    client.patch("/api/members/mem_xiaoyao/default-brain", json={"brain_id": brain_id})
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase35-stream",
        "这是高隐私问题：api_key=sk-phase35-user-secret。请正常回答。",
    )
    raw_stream = client.get(created["stream_url"]).text
    events = _parse_sse(raw_stream)
    reply = "".join(
        str(event.get("payload", {}).get("text", ""))
        for event in events
        if event.get("event") == "response.delta"
    )
    detail = client.get(f"/api/chat/conversations/{conversation_id}").json()
    assistant = [
        message for message in detail["messages"] if message["turn_id"] == created["turn_id"]
    ][-1]["content_text"]
    persisted = client.get(f"/api/chat/turns/{created['turn_id']}/events").json()["items"]
    delta_payloads = [
        item["payload"]["payload"]
        for item in persisted
        if item["event_type"] == "response.delta"
    ]

    assert reply == assistant
    assert "sk-phase35-secret-value" not in reply
    assert "C:\\Users\\Administrator" not in reply
    assert "trace_id" not in reply.lower()
    assert "approval_id" not in reply.lower()
    assert "browser.download" not in reply.lower()
    assert "r3" not in reply.lower()
    assert all("response_filter" in payload for payload in delta_payloads)


def test_phase35_model_messages_use_redacted_model_safe_context(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    context = SimpleNamespace(
        member=MemberSummary(
            member_id="mem_xiaoyao",
            display_name="小幺",
            status=MemberStatus.NEEDS_CONFIGURATION,
            default_brain_id=None,
        ),
        brain=None,
        persona=None,
        heart=None,
        conversation=ConversationContext(
            conversation_id="conv_phase35",
            recent_summary="上一轮提到 token=secret-phase35-summary",
            last_messages=[
                {
                    "author_type": "user",
                    "content_text": "历史 api_key=sk-phase35-history-secret",
                    "model_safe_content_text": "历史 api_key=[REDACTED_API_KEY]",
                    "redaction_summary": {"applied": True},
                }
            ],
        ),
        memories=[],
    )

    messages = registry.chat_service._model_messages(
        context,
        "当前 password=phase35-password-value",
    )
    serialized = json.dumps(messages, ensure_ascii=False)

    assert "sk-phase35-history-secret" not in serialized
    assert "secret-phase35-summary" not in serialized
    assert "phase35-password-value" not in serialized
    assert "[REDACTED" in serialized


def test_phase35_conversation_write_access_policy_blocks_cross_member(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    denied = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_aheng",
            "session_id": "phase35-access",
            "input": {"type": "text", "text": "跨成员写入测试"},
        },
    )
    allowed = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": "phase35-access-ok",
            "input": {"type": "text", "text": "正常写入测试"},
        },
    )

    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "NOT_FOUND"
    assert "mem_xiaoyao" not in denied.text
    assert allowed.status_code == 200


def test_phase35_task_status_presenter_never_fakes_completion() -> None:
    presenter = ChatTaskStatusPresenter()
    failed = presenter.present(_task(TaskStatus.FAILED))
    paused = presenter.present(_task(TaskStatus.PAUSED))
    planned = presenter.present(_task(TaskStatus.PLANNED))
    completed = presenter.present(_task(TaskStatus.COMPLETED))

    assert failed.event_type == ChatEventType.TASK_FAILED
    assert paused.event_type == ChatEventType.TASK_PAUSED
    assert planned.event_type is None
    assert completed.event_type == ChatEventType.TASK_COMPLETED
    for item in [failed, paused, planned]:
        assert item.task_status["completed"] is False
        assert "处理完成" not in item.text
        assert "尚未完成" in item.text or "没有继续执行" in item.text


def test_phase35_high_privacy_without_local_brain_is_recoverable_block(
    client: TestClient,
) -> None:
    conversation_id = _conversation_id(client)
    created = _create_turn(
        client,
        conversation_id,
        "phase35-privacy",
        "隐私测试：api_key=sk-phase35-privacy-secret",
    )
    events = _parse_sse(client.get(created["stream_url"]).text)
    detail = client.get(f"/api/chat/turns/{created['turn_id']}").json()
    failed = next(event for event in events if event["event"] == "turn.failed")
    serialized = json.dumps({"events": events, "detail": detail}, ensure_ascii=False)

    assert detail["error_code"] == "MODEL_ROUTE_BLOCKED_BY_PRIVACY"
    assert failed["payload"]["response_plan"]["structured_payload"]["recoverable"] is True
    assert "sk-phase35-privacy-secret" not in serialized


def test_phase35_visible_filter_strips_split_internal_terms() -> None:
    filter_ = ChatVisibleOutputFilter()
    chunks = [
        filter_.feed("approval_id=apr"),
        filter_.feed("_phase35 trace_id=trc_phase35 browser.download R3 "),
        filter_.feed("token=phase35-secret C:\\Users\\Administrator\\token.txt"),
        filter_.finish(),
    ]
    visible = "".join(chunks)

    assert "approval_id" not in visible.lower()
    assert "trace_id" not in visible.lower()
    assert "browser.download" not in visible.lower()
    assert "r3" not in visible.lower()
    assert "C:\\Users\\Administrator" not in visible
    assert filter_.summary()["changed_count"] >= 1


def test_phase35_visible_filter_normalizes_negated_stealth_boundary() -> None:
    visible, summary = ChatVisibleOutputFilter.filter_text(
        "我没有真实主观意识；也不会在后台偷偷替你执行工具或登录账号。"
        "关于“后台偷偷运行”，我不会“偷偷”做事。"
    )

    assert "后台偷偷" not in visible
    assert "我会偷偷" not in visible
    assert "不会在后台未经你确认" in visible
    assert "不会未经你确认" in visible
    assert "negated_background_stealth" in summary["blocked_terms"]
    assert "negated_stealth" in summary["blocked_terms"]


def test_phase35_visible_filter_normalizes_realtime_uncertainty() -> None:
    visible, summary = ChatVisibleOutputFilter.filter_text(
        "结论：我不能可靠告诉你今天最新大模型榜单第一名是谁；不浏览或联网时不应编造。"
    )

    assert "无法确认" in visible
    assert "不能可靠告诉" not in visible
    assert "不应编造" in visible
    assert "realtime_uncertainty" in summary["blocked_terms"]


def test_phase35_visible_filter_completes_browser_evidence_terms() -> None:
    filter_ = ChatVisibleOutputFilter()
    chunks = [
        filter_.feed("browser.snapshot 用来记录网页快照，browser.screenshot 用来记录页面截图。"),
        filter_.feed("evidence 和 artifact 应记录可复核依据。"),
        filter_.finish(),
    ]
    visible = "".join(chunks)

    assert "selector" in visible
    assert "网页快照" in visible
    assert "页面截图" in visible
    assert "browser_selector_evidence_hint" in filter_.summary()["blocked_terms"]


def _create_local_brain(client: TestClient) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": "Phase35 local brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "phase35-test-model",
            "is_local": True,
            "context_window": 4096,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["brain_id"])


def _create_turn(
    client: TestClient,
    conversation_id: str,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _conversation_id(client: TestClient) -> str:
    return str(client.get("/api/chat/conversations").json()["items"][0]["conversation_id"])


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _task(status: TaskStatus) -> SimpleNamespace:
    return SimpleNamespace(
        task_id="tsk_phase35",
        title="Phase35 状态测试",
        status=status,
        mode=TaskMode.WORKFLOW,
    )


def _latest_migration() -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    migrations = root / "apps/local-api/app/db/migrations"
    return sorted(path.name for path in migrations.glob("*.sql"))[-1]

