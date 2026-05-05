from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase56_experience_consolidation_search_feedback_and_redaction(
    client: TestClient,
) -> None:
    consolidated = client.post(
        "/api/memory/experience/consolidate",
        json={
            "member_id": "mem_xiaoyao",
            "task_id": "task_phase56_success",
            "conversation_id": "conv_phase56_success",
            "outcome": "completed",
            "summary_text": (
                "Phase56 reusable lesson: when preparing release evidence, create "
                "migration, repository, service, API, eval, and focused tests in order."
            ),
            "source": {
                "type": "task_experience",
                "turn_id": "turn_phase56_internal",
                "message_id": "msg_phase56_internal",
                "trace_id": "trace_phase56_internal",
            },
            "steps": [
                {"step_type": "migration", "status": "completed"},
                {"step_type": "service", "status": "completed"},
                {"step_type": "test", "status": "completed"},
            ],
            "evidence": {
                "result": "release evidence ready",
                "token": "phase56-sensitive-marker",
                "local_path": "C:/phase56/private-cookie.txt",
            },
        },
    )
    assert consolidated.status_code == 200, consolidated.text
    body = consolidated.json()
    candidate = body["candidates"][0]
    memory = body["memories"][0]
    experience = body["experience"]

    assert candidate["decision"] == "auto_written"
    assert candidate["score"]["quality_breakdown"].keys() >= {
        "value",
        "clarity",
        "stability",
        "sensitivity",
        "reuse",
        "conflict_risk",
    }
    assert memory["quality_score"] >= 0.55
    assert memory["reuse_score"] > 0
    assert experience["memory_id"] == memory["memory_id"]
    assert experience["decision"] == "auto_written"

    search = client.post(
        "/api/memory/search",
        json={
            "member_id": "mem_xiaoyao",
            "query": "release evidence migration repository service API eval tests",
            "limit": 5,
        },
    )
    assert search.status_code == 200, search.text
    search_body = search.json()
    hit = next(
        item for item in search_body["items"] if item["memory_id"] == memory["memory_id"]
    )
    assert hit["quality_score"] >= 0.55
    assert hit["confidence"] > 0
    assert hit["sensitivity"] in {"low", "medium"}
    assert "rerank_quality_reuse_version" in hit["selection_reason"]
    assert hit["selection_confidence"] is not None
    assert hit["source"]["turn_id"] is None
    assert hit["source"]["message_id"] is None
    assert hit["source"]["trace_id"] is None

    feedback = client.post(
        f"/api/memory/retrievals/{search_body['retrieval_id']}/feedback",
        json={
            "member_id": "mem_xiaoyao",
            "memory_id": memory["memory_id"],
            "task_id": "task_phase56_success",
            "feedback_type": "helpful",
            "rating": 1,
            "source": {"type": "retrieval_feedback", "trace_id": "trace_phase56_feedback"},
            "evidence": {
                "reason": "the memory was reused",
                "cookie": "phase56-feedback-marker",
            },
        },
    )
    assert feedback.status_code == 200, feedback.text
    after_feedback = client.get(f"/api/memory/{memory['memory_id']}").json()
    assert feedback.json()["feedback"]["feedback_type"] == "helpful"
    assert after_feedback["reuse_count"] >= memory["reuse_count"] + 1

    listed_experience = client.get(
        "/api/memory/experience-records",
        params={"member_id": "mem_xiaoyao", "task_id": "task_phase56_success"},
    ).json()["items"]
    serialized = json.dumps(
        {
            "consolidated": body,
            "search": search_body,
            "feedback": feedback.json(),
            "listed_experience": listed_experience,
        },
        ensure_ascii=False,
    )
    assert listed_experience[0]["experience_id"] == experience["experience_id"]
    assert "phase56-sensitive-marker" not in serialized
    assert "phase56-feedback-marker" not in serialized
    assert "private-cookie.txt" not in serialized


def test_phase56_failed_experience_and_conflict_governance(client: TestClient) -> None:
    first = _consolidate_release_lesson(client, "task_phase56_conflict_a", "completed")
    second = _consolidate_release_lesson(
        client,
        "task_phase56_conflict_b",
        "completed",
        summary_suffix=" for rerun validation.",
    )
    failed = _consolidate_release_lesson(client, "task_phase56_failed", "failed")

    assert first["memories"]
    assert second["conflicts"]
    assert second["conflicts"][0]["conflict_type"] in {
        "duplicate_experience",
        "related_experience",
    }
    assert failed["candidates"][0]["decision"] == "needs_review"
    assert failed["memories"] == []
    assert failed["experience"]["outcome"] == "failed"
    assert failed["experience"]["decision"] == "needs_review"

    conflicts = client.get(
        "/api/memory/conflicts",
        params={"member_id": "mem_xiaoyao", "limit": 10},
    )
    assert conflicts.status_code == 200, conflicts.text
    assert any(
        item["candidate_id"] == second["candidates"][0]["candidate_id"]
        for item in conflicts.json()["items"]
    )


def test_phase56_release_contracts_and_eval_suite(client: TestClient) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase56")
    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()

    assert migration_contract["required_migration"] == "041_long_term_memory_experience_loop.sql"
    assert "suite_phase56_long_term_memory_experience_loop" in {
        item["suite_id"] for item in suites
    }
    assert by_name["MemoryExperienceConsolidation"]["status"] == "implemented"
    assert by_name["MemoryConflictGovernance"]["status"] == "implemented"
    assert by_name["MemoryReuseFeedback"]["status"] == "implemented"
    assert completed["status"] == "ready_for_release"
    assert report["summary"]["phase56_long_term_memory_experience_loop"]["suite_id"] == (
        "suite_phase56_long_term_memory_experience_loop"
    )
    assert report["summary"]["phase23"]["capability_scores"]["phase56"]["registered"] is True
    assert any(
        item["source_type"] == "phase56_long_term_memory_experience_loop"
        for item in evidence["items"]
    )


def _consolidate_release_lesson(
    client: TestClient,
    task_id: str,
    outcome: str,
    *,
    summary_suffix: str = ".",
) -> dict[str, Any]:
    response = client.post(
        "/api/memory/experience/consolidate",
        json={
            "member_id": "mem_xiaoyao",
            "task_id": task_id,
            "conversation_id": "conv_phase56_conflict",
            "outcome": outcome,
            "summary_text": (
                "Phase56 conflict lesson: validate memory migration, experience records, "
                "conflict records, reuse feedback, and release evidence together"
                f"{summary_suffix}"
            ),
            "source": {"type": "task_experience", "task_id": task_id},
            "steps": [{"step_type": "service", "status": outcome}],
            "evidence": {"result": outcome},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()
