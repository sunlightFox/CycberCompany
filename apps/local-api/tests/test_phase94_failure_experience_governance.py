from __future__ import annotations

from typing import Any, cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase94_failure_review_promotes_advisory_memory(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry
    record = anyio.run(
        _record_failure,
        registry,
        {
            "member_id": "mem_xiaoyao",
            "failure_class": "tool_execution_error",
            "summary_text": "读取表格时误报已经完成，实际没有生成结果。",
            "reason_code": "false_completion",
            "conversation_id": "conv_phase94_review",
            "turn_id": "turn_phase94_review",
            "impact_scope": "task_completion",
            "severity": "high",
            "evidence_refs": [{"type": "trace", "trace_id": "tr_phase94_review"}],
            "source_payload": {"tool_name": "office.edit"},
        },
    )

    assert record.review_status == "pending_review"
    assert record.memory_decision == "needs_review"
    assert record.memory_id is None

    reviewed = client.post(
        f"/api/memory/failure-experiences/{record.failure_id}/review",
        json={"action": "approve"},
    ).json()

    assert reviewed["review_status"] == "approved"
    assert reviewed["memory_decision"] == "written"
    assert reviewed["memory_id"]

    memories = client.get("/api/memory", params={"member_id": "mem_xiaoyao", "kind": "failure_advisory"}).json()
    assert any(item["memory_id"] == reviewed["memory_id"] for item in memories["items"])


def test_phase94_recurrence_opens_regression_candidate(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry

    first = anyio.run(
        _record_failure,
        registry,
        {
            "member_id": "mem_xiaoyao",
            "failure_class": "runtime_failure",
            "summary_text": "同一类运行失败第一次出现。",
            "reason_code": "tool_timeout",
            "impact_scope": "chat_runtime",
            "severity": "medium",
            "evidence_refs": [{"type": "turn", "turn_id": "turn_phase94_1"}],
            "source_payload": {"worker": "runtime"},
        },
    )
    second = anyio.run(
        _record_failure,
        registry,
        {
            "member_id": "mem_xiaoyao",
            "failure_class": "runtime_failure",
            "summary_text": "同一类运行失败第二次出现。",
            "reason_code": "tool_timeout",
            "impact_scope": "chat_runtime",
            "severity": "medium",
            "evidence_refs": [{"type": "turn", "turn_id": "turn_phase94_2"}],
            "source_payload": {"worker": "runtime"},
        },
    )

    assert first.recurrence_key == second.recurrence_key
    assert second.recurrence_count >= 2

    candidates = client.get(
        "/api/memory/regression-candidates",
        params={"failure_class": "runtime_failure"},
    ).json()["items"]
    matched = next(item for item in candidates if item["recurrence_key"] == second.recurrence_key)
    assert matched["status"] == "open"
    assert matched["recurrence_count"] >= 2


def test_phase94_readiness_release_and_recall_surface_governance(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry
    approved = anyio.run(
        _create_and_approve_failure,
        registry,
        {
            "member_id": "mem_xiaoyao",
            "failure_class": "tool_execution_error",
            "summary_text": "导出文档时曾经漏写结尾，后续回答要先自检再宣称完成。",
            "reason_code": "false_completion",
            "impact_scope": "delivery_quality",
            "severity": "medium",
            "evidence_refs": [{"type": "trace", "trace_id": "tr_phase94_recall"}],
            "source_payload": {"tool_name": "document.export"},
        },
    )

    advisories = anyio.run(
        _recall_advisories,
        registry,
        "mem_xiaoyao",
        "漏写结尾",
        3,
    )
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase94 = readiness["phase_readiness"]["phase94_failure_experience_governance"]
    created = client.post("/api/release-gates", json={})
    report = client.get(f"/api/release-gates/{created.json()['release_gate_id']}/report").json()

    assert approved.review_status == "approved"
    assert advisories
    assert any(item.failure_id == approved.failure_id for item in advisories)
    assert phase94["details"]["phase94_contract_version"] == "phase94.failure_experience_governance.v1"
    assert phase94["status"] in {"ready", "partial"}
    assert report["summary"]["phase94_failure_experience_governance"]["contract_version"] == "phase94.failure_experience_governance.v1"


async def _create_and_approve_failure(registry: Any, payload: dict[str, Any]) -> Any:
    record = await registry.failure_experience_service.record_failure(**payload)
    return await registry.failure_experience_service.review_failure(record.failure_id, action="approve")


async def _record_failure(registry: Any, payload: dict[str, Any]) -> Any:
    return await registry.failure_experience_service.record_failure(**payload)


async def _recall_advisories(
    registry: Any,
    member_id: str,
    query: str,
    limit: int,
) -> Any:
    return await registry.failure_experience_service.recall_advisories(
        member_id=member_id,
        query=query,
        limit=limit,
    )
