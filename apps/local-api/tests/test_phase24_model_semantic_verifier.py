from __future__ import annotations

import json
from typing import Any, cast

import anyio
from app.services.model_semantic_verifier import ModelAssistedVerifierService
from core_types import ContextDecision, IntentDecision, ModeDecision, SemanticIntentCandidate
from fastapi.testclient import TestClient


def test_phase24_contracts_gap_and_eval_suite(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    suites_first = client.get("/api/evals/suites").json()["items"]
    suites_second = client.get("/api/evals/suites").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    suite_ids = [item["suite_id"] for item in suites_second]

    verifier = by_name["ModelAssistedVerifier"]
    assert verifier["status"] == "implemented_with_fallback"
    assert verifier["implemented"] is True
    assert verifier["details"]["real_model_call"] is False
    assert any(
        item["gap_id"] == "gap_phase24_real_model_semantic_quality_not_enabled"
        and item["status"] == "accepted_risk"
        for item in gaps
    )
    assert "suite_phase24_model_semantic_verifier" in set(suite_ids)
    assert suite_ids.count("suite_phase24_model_semantic_verifier") == 1
    assert len(suites_first) == len(suites_second)


def test_phase24_decision_preview_returns_review_without_persistence(
    client: TestClient,
) -> None:
    before = _table_counts(
        client,
        "semantic_review_requests",
        "semantic_review_suggestions",
        "semantic_review_model_calls",
        "semantic_review_merge_results",
    )
    preview = client.post(
        "/api/brain/decision-preview",
        json={
            "text": "你好，帮我记住我喜欢短回复，顺便删除那个文件",
            "member_id": "mem_xiaoyao",
            "privacy_level": "medium",
        },
    ).json()
    after = _table_counts(
        client,
        "semantic_review_requests",
        "semantic_review_suggestions",
        "semantic_review_model_calls",
        "semantic_review_merge_results",
    )
    review = preview["semantic_review"]

    assert review["fallback_used"] is True
    assert review["fallback_reason"] == "local_model_not_configured"
    assert review["request"]["privacy_policy"] == "local_only"
    assert review["model_call"]["status"] == "skipped"
    assert before == after


def test_phase24_real_turn_persists_review_and_read_only_api(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase24-semantic-review",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "调用 MCP 和技能做一下，顺便删除那个文件"},
        },
    ).json()
    stream_events = _parse_sse(client.get(turn["stream_url"]).text)

    decision = client.get(f"/api/chat/turns/{turn['turn_id']}/brain-decision").json()
    low_confidence = client.get(
        f"/api/chat/turns/{turn['turn_id']}/low-confidence-review"
    ).json()
    review = client.get(f"/api/chat/turns/{turn['turn_id']}/semantic-review").json()
    events = client.get(
        f"/api/chat/turns/{turn['turn_id']}/semantic-review-events"
    ).json()["items"]

    assert decision["semantic_review"]["semantic_review_id"] == review["semantic_review_id"]
    assert low_confidence["semantic_review_id"] == review["semantic_review_id"]
    assert low_confidence["fallback_used"] is True
    assert low_confidence["model_assist_attempted"] is False
    assert review["model_call"]["status"] == "skipped"
    assert review["request"]["capability_boundary_summary"]["mcp_available"] is False
    assert {
        "semantic_review.request",
        "semantic_review.suggestion",
        "semantic_review.model_call",
        "semantic_review.merge",
    }.issubset({item["event_type"] for item in events})
    assert "task.created" not in {event["event"] for event in stream_events}


def test_phase24_fake_adapter_schema_timeout_and_risk_guard(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry

    guarded = anyio.run(
        _run_fake_review,
        registry.trace_service,
        _FakeAdapter(
            {
                "suggested_primary_intent": "task_request",
                "suggested_mode": "direct",
                "risk_notes": ["safe_to_execute"],
                "confidence": 0.9,
                "reason_summary": "looks safe",
            }
        ),
    )
    invalid = anyio.run(_run_fake_review, registry.trace_service, _FakeAdapter("{not-json"))
    timeout = anyio.run(
        _run_fake_review,
        registry.trace_service,
        _TimeoutAdapter(),
    )

    assert guarded.result.fallback_used is False
    assert guarded.result.risk_guard_applied is True
    assert guarded.mode.mode == "ask_clarification"
    assert invalid.result.fallback_used is True
    assert invalid.result.fallback_reason == "schema_invalid"
    assert invalid.result.schema_valid is False
    assert timeout.result.fallback_used is True
    assert timeout.result.fallback_reason == "model_timeout"


def test_phase24_release_report_and_phase23_aggregation(client: TestClient) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    turn = client.post(
        "/api/chat/turn",
        json={
            "session_id": "phase24-release",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "？？？"},
        },
    ).json()
    client.get(turn["stream_url"])
    gate = client.post("/api/release-gates", json={}).json()
    client.post(f"/api/release-gates/{gate['release_gate_id']}/run")
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    phase24 = report["summary"]["phase24"]
    phase23 = report["summary"]["phase23"]

    assert report["decision"] == "go"
    assert phase24["suite_id"] == "suite_phase24_model_semantic_verifier"
    assert phase24["registered_cases"] >= 10
    assert phase24["review_requests"] >= 1
    assert phase24["fallback_count"] >= 1
    assert phase24["leakage_count"] == 0
    assert phase23["eval_status"]["registered_suites"] >= 8
    assert phase23["capability_scores"]["phase24"]["registered"] is True
    assert "phase24-secret-value" not in json.dumps(report, ensure_ascii=False)


async def _run_fake_review(trace_service: Any, adapter: Any) -> Any:
    service = ModelAssistedVerifierService(
        trace_service=trace_service,
        adapter=adapter,
        allow_cloud=False,
    )
    return await service.review_and_merge(
        text="帮我转账",
        member_id="mem_xiaoyao",
        conversation_id="conv_phase24_fake",
        turn_id="turn_phase24_fake",
        brain_decision_id="bd_phase24_fake",
        intent=IntentDecision(
            primary_intent="task_request",
            risk_signals=["high_risk_financial_or_signature"],
            needs_tool=True,
            needs_task=True,
            confidence=0.52,
            reason_codes=["high_risk_financial_or_signature"],
        ),
        mode=ModeDecision(
            mode="ask_clarification",
            submode="blocks_execution",
            requires_approval_before_execute=True,
            fallback_mode="direct",
            confidence=0.52,
            reason_codes=["high_risk_without_confirmation"],
        ),
        context=ContextDecision(
            include_memory=False,
            selection_reason=["current_input"],
        ),
        semantic=SemanticIntentCandidate(
            semantic_candidate_id="sem_phase24_fake",
            member_id="mem_xiaoyao",
            primary_intent="task_request",
            risk_intents=["high_risk_financial_or_signature"],
            conflicts=["high_risk_vs_ambiguous_destination"],
            confidence=0.5,
        ),
        dialogue_state=None,
        clarification={
            "needs_clarification": True,
            "reason": "high_risk_without_confirmation",
            "clarification_type": "missing_destination",
            "blocking_level": "blocks_execution",
            "questions": ["要使用哪个账户或钱包？"],
            "safe_partial_answer_allowed": False,
        },
        capability_snapshot={"skill": {"available": False}, "mcp_runtime": {"available": False}},
        privacy_level="medium",
        trigger_reasons=["high_risk_missing_destination"],
        trace_id=None,
        root_span_id=None,
    )


class _FakeAdapter:
    name = "local_fake"

    def __init__(self, payload: dict[str, Any] | str) -> None:
        self._payload = payload

    def available(self, request: Any) -> bool:
        del request
        return True

    async def complete(self, request: Any) -> dict[str, Any] | str:
        del request
        return self._payload


class _TimeoutAdapter:
    name = "local_fake"

    def available(self, request: Any) -> bool:
        del request
        return True

    async def complete(self, request: Any) -> dict[str, Any] | str:
        del request
        raise TimeoutError


def _table_counts(client: TestClient, *tables: str) -> dict[str, int]:
    registry = cast(Any, client.app).state.registry
    result: dict[str, int] = {}
    for table in tables:
        row = anyio.run(registry.db.fetch_one, f"SELECT COUNT(*) AS count FROM {table}")
        result[table] = int(row["count"])
    return result


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
