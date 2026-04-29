from __future__ import annotations

import json
from typing import Any, cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase17_suite_contract_gap_and_case_matrix(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]

    by_name = {item["name"]: item for item in contracts}
    phase17_suite = next(
        item for item in suites if item["suite_id"] == "suite_phase17_chat_main_chain"
    )

    assert by_name["ChatMainChainEval"]["status"] == "implemented"
    assert any(item["gap_id"] == "gap_chat_main_chain_eval_local_smoke" for item in gaps)
    assert phase17_suite["category"] == "chat_main_chain_acceptance"
    assert phase17_suite["required"] is True

    registry = cast(FastAPI, client.app).state.registry
    cases = anyio.run(_phase17_cases, registry)
    areas = {case["input"]["capability_area"] for case in cases}
    scenarios = {
        (case["input"]["capability_area"], case["input"]["scenario_type"])
        for case in cases
    }

    assert len(cases) == 39
    assert {
        "casual_chat",
        "complex_dialogue",
        "intent_mode_context",
        "memory_knowledge",
        "persona_heart",
        "workflow_task",
        "agent_loop",
        "tool_runtime",
        "mcp",
        "skill",
        "safety_approval",
        "trace_replay_response",
        "performance_degradation",
    }.issubset(areas)
    for area in areas:
        assert {(area, "allow"), (area, "degraded"), (area, "safety")}.issubset(scenarios)
    sample = cases[0]
    assert {
        "expected_mode",
        "expected_context",
        "expected_safety",
        "expected_response_shape",
        "expected_trace_spans",
        "forbidden_behavior",
        "severity",
        "owner_phase",
    }.issubset(sample["expected"])


def test_phase17_eval_run_passes_with_clean_backend_evidence(client: TestClient) -> None:
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase17_chat_main_chain"},
    ).json()

    assert run["status"] == "passed"
    assert run["total_cases"] == 39
    assert run["failed_cases"] == 0
    assert run["metrics"]["pass_rate"] == 1.0


def test_phase17_response_validator_fails_missing_response_plan(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    anyio.run(_insert_bad_terminal_chat_event, registry)

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase17_chat_main_chain"},
    ).json()

    assert run["status"] == "failed"
    assert run["failed_cases"] >= 1
    assert run["summary"]["failed_cases"] >= 1


def test_phase17_release_report_and_diagnostic_include_summary(
    client: TestClient,
) -> None:
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    diagnostic = client.post(
        "/api/diagnostics/bundles",
        json={"scope": {"phase": "phase17"}},
    ).json()

    phase17 = report["summary"]["phase17"]
    assert completed["status"] == "ready_for_release"
    assert report["decision"] == "go"
    assert phase17["registered_cases"] == 39
    assert phase17["failed_results"] == 0
    assert phase17["zero_tolerance_findings"] == 0
    assert phase17["response_plan_missing"] == 0
    assert phase17["trace_replay_completeness"] >= 0
    assert phase17["contract"] == 1
    assert diagnostic["status"] == "completed"


async def _phase17_cases(registry: Any) -> list[dict[str, Any]]:
    rows = await registry.db.fetch_all(
        """
        SELECT case_key, input_json, expected_json, tags_json
        FROM eval_cases
        WHERE suite_id = 'suite_phase17_chat_main_chain'
        ORDER BY case_key ASC
        """
    )
    return [
        {
            "case_key": row["case_key"],
            "input": json.loads(row["input_json"]),
            "expected": json.loads(row["expected_json"]),
            "tags": json.loads(row["tags_json"]),
        }
        for row in rows
    ]


async def _insert_bad_terminal_chat_event(registry: Any) -> None:
    await registry.db.execute(
        """
        INSERT INTO chat_turns (
          turn_id, conversation_id, member_id, user_message_id, assistant_message_id,
          trace_id, status, intent, mode, privacy_level, route_json, usage_json,
          events_json, error_code, error_message, retry_of_turn_id, cancel_requested,
          created_at, updated_at, ended_at, experience_json, brain_decision_id
        ) VALUES (
          'turn_phase17_bad', 'conv_default_xiaoyao', 'mem_xiaoyao', NULL, NULL,
          'trace_phase17_bad', 'completed', 'chat', 'direct', 'low', '{}', '{}',
          '[]', NULL, NULL, NULL, 0,
          '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00',
          '2026-01-01T00:00:01+00:00', '{}', NULL
        )
        """
    )
    await registry.db.execute(
        """
        INSERT INTO chat_events (
          event_id, turn_id, sequence, event_type, trace_id, payload_json, created_at
        ) VALUES (
          'cevt_phase17_bad', 'turn_phase17_bad', 1, 'response.completed',
          'trace_phase17_bad', ?, '2026-01-01T00:00:01+00:00'
        )
        """,
        (json.dumps({"plain_text": "missing plan"}, ensure_ascii=False),),
    )
