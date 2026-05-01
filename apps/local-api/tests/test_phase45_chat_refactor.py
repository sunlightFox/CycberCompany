from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from app.services.chat_context import ChatContextCoordinator
from app.services.chat_memory import ChatMemoryCoordinator
from app.services.chat_model import ChatModelCoordinator
from app.services.chat_privacy import ChatPrivacyCoordinator
from app.services.chat_quality import ChatQualityPolicy
from app.services.chat_response import ChatResponseCoordinator
from app.services.chat_tasks import ChatTaskCoordinator, ChatTurnOrchestrator
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract

ROOT_DIR = Path(__file__).resolve().parents[3]


def test_phase45_source_cleanup_and_coordinator_units() -> None:
    chat_py = ROOT_DIR / "apps" / "local-api" / "app" / "services" / "chat.py"
    chat_text = chat_py.read_text(encoding="utf-8")
    task_coordinator = ChatTaskCoordinator()
    memory_coordinator = ChatMemoryCoordinator()
    quality = ChatQualityPolicy()

    scheduled = task_coordinator.scheduled_intents.parse("每天上午 9 点帮我整理待办")
    media = task_coordinator.parse_media_task_request("请分析这个 mp4 视频并生成剪辑方案")
    quality_outcome = quality.handle(
        user_text="停，改成只做后端聊天链路验收，给三点。",
        privacy_level="medium",
        sensitivity_hits=[],
        brain_intent="chat",
    )
    quality_payload = (
        quality_outcome.response_plan.model_dump(mode="json")
        if quality_outcome
        else {}
    )

    assert "_phase31_output_guard" not in chat_text
    assert "def _parse_scheduled_task_request" not in chat_text
    assert "def _parse_media_task_request" not in chat_text
    assert "ChatVisibleOutputFilter" not in chat_text
    assert "ChatTaskStatusPresenter" not in chat_text
    assert "context_redaction_summary" not in chat_text
    assert "self._model_coordinator.model_messages" in chat_text
    assert "self._response_coordinator.filter_text" in chat_text
    assert "self._context_coordinator.redaction_summary" in chat_text
    assert scheduled is not None
    assert scheduled.schedule["type"] == "daily"
    assert media is not None
    assert media["source_boundary"] == "task_artifact_only"
    assert memory_coordinator.explicit_forget_boundary("请忘记本批次临时测试回复偏好")
    assert "quality_case" not in json.dumps(quality_payload, ensure_ascii=False)
    assert "chat_quality_policy" in json.dumps(quality_payload, ensure_ascii=False)
    assert "model" in ChatTurnOrchestrator().stage_names()


def test_phase45_model_and_privacy_coordinators_keep_sensitive_context_out() -> None:
    context = SimpleNamespace(
        member=SimpleNamespace(display_name="小幺"),
        persona=None,
        heart=None,
        conversation=SimpleNamespace(
            conversation_id="conv_phase45",
            recent_summary="上一轮提到 token=phase45-summary-secret",
            last_messages=[
                {
                    "author_type": "user",
                    "content_text": "历史 api_key=sk-phase45-history-secret",
                    "model_safe_content_text": "历史 api_key=[REDACTED_API_KEY]",
                }
            ],
        ),
        memories=[],
    )
    model = ChatModelCoordinator()
    privacy = ChatPrivacyCoordinator(model_coordinator=model)
    context_coordinator = ChatContextCoordinator()
    response_coordinator = ChatResponseCoordinator()

    messages = model.model_messages(cast(Any, context), "当前 password=phase45-password-value")
    classified = privacy.classify("token=phase45-token-value")
    response_text, response_filter = response_coordinator.filter_text(
        "trace_id=trc_phase45 token=phase45-visible-secret"
    )
    context_summary = context_coordinator.redaction_summary(
        context,
        sensitivity_hits=classified.sensitivity_hits,
    )
    planner = privacy.planner_context(
        privacy_level=classified.privacy_level,
        allow_cloud=classified.allow_cloud,
        sensitivity_hits=classified.sensitivity_hits,
    )
    serialized = json.dumps({"messages": messages, "planner": planner}, ensure_ascii=False)

    assert "phase45-summary-secret" not in serialized
    assert "sk-phase45-history-secret" not in serialized
    assert "phase45-password-value" not in serialized
    assert "[REDACTED" in serialized
    assert planner["cloud_planner_allowed"] is False
    assert "phase45-visible-secret" not in response_text
    assert response_filter["stream_safe"] is True
    assert context_summary["raw_content_text_used_for_model"] is False


def test_phase45_chat_paths_remain_compatible_after_refactor(client: TestClient) -> None:
    conversation_id = _conversation_id(client)
    quality_turn = _create_turn(
        client,
        conversation_id,
        "phase45-quality",
        "停，改成只做后端聊天链路验收，给三点。",
    )
    quality_events = _parse_sse(client.get(quality_turn["stream_url"]).text)
    quality_reply = _reply_from_events(quality_events)
    quality_detail = client.get(f"/api/chat/turns/{quality_turn['turn_id']}").json()
    scheduled_turn = _create_turn(
        client,
        conversation_id,
        "phase45-scheduled",
        "每天上午 9 点帮我整理一次今天的待办。",
    )
    scheduled_events = _parse_sse(client.get(scheduled_turn["stream_url"]).text)
    scheduled_detail = client.get(f"/api/chat/turns/{scheduled_turn['turn_id']}").json()
    serialized = json.dumps(
        {
            "quality_events": quality_events,
            "quality_detail": quality_detail,
            "scheduled_events": scheduled_events,
            "scheduled_detail": scheduled_detail,
        },
        ensure_ascii=False,
    )

    assert quality_detail["status"] == "completed"
    assert "前一个目标先停掉" in quality_reply
    assert "后端聊天链路验收" in quality_reply
    assert "quality_case" not in serialized
    assert any(event["event"] == "turn.completed" for event in scheduled_events)
    assert scheduled_detail["intent"] == "scheduled_task_request"
    assert "已创建定时任务" in _reply_from_events(scheduled_events)


def test_phase45_release_contracts_eval_report_and_diagnostic(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase45")
    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    run = client.post("/api/evals/runs", json={"suite_id": "suite_phase45_chat_refactor"})
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    phase45 = report["summary"]["phase45"]

    assert migration_contract["required_migration"] == "031_media_runtime.sql"
    assert "suite_phase45_chat_refactor" in {item["suite_id"] for item in suites}
    for name in [
        "ChatTurnOrchestrator",
        "ChatModelCoordinator",
        "ChatTaskCoordinator",
        "ChatContextCoordinator",
        "ChatResponseCoordinator",
        "ChatMemoryCoordinator",
        "ChatPrivacyCoordinator",
        "ChatQualityPolicy",
        "ChatProductionPatchRetirement",
    ]:
        assert by_name[name]["status"] == "implemented"
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 10
    assert completed["status"] == "ready_for_release"
    assert phase45["suite_id"] == "suite_phase45_chat_refactor"
    assert phase45["registered_cases"] == 10
    assert phase45["production_patch_cleanup"]["phase31_guard_removed"] is True
    assert phase45["refactor_boundaries"]["quality_policy_generic_payload"] is True
    assert phase45["refactor_boundaries"]["response_filter_delegated"] is True
    assert phase45["refactor_boundaries"]["context_redaction_delegated"] is True
    assert phase45["refactor_boundaries"]["task_status_presenter_delegated"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase45"]["registered"] is True
    assert any(item["source_type"] == "phase45_chat_refactor" for item in evidence)
    assert "phase45" in diagnostic
    assert "phase45_chat_refactor" in diagnostic


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


def _reply_from_events(events: list[dict[str, Any]]) -> str:
    return "".join(
        str(event.get("payload", {}).get("text", ""))
        for event in events
        if event.get("event") == "response.delta"
    )
