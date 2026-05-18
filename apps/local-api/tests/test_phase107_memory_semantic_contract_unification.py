from __future__ import annotations

from typing import Any, cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase107_search_contract_exposes_semantic_version_and_correction_closure(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    old_id = "mem_phase107_old_pref"
    correction_id = "mem_phase107_correction"
    anyio.run(
        _insert_memory_item,
        registry,
        {
            "memory_id": old_id,
            "organization_id": "org_default",
            "member_id": "mem_xiaoyao",
            "user_id": "user_local_owner",
            "layer": "semantic",
            "kind": "preference",
            "scope_type": "member",
            "scope_id": "mem_xiaoyao",
            "memory_class": "preference",
            "scope_policy": "member_cross_session",
            "summary_text": "以后回复先给结论，再给风险",
            "payload": {"preference": "conclusion_then_risk"},
            "source": {"type": "conversation_turn", "conversation_id": "conv_phase107_a", "channel": "local"},
            "confidence": 0.92,
            "importance": 0.86,
            "sensitivity": "low",
            "durability": "durable",
            "freshness_state": "superseded",
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_to": "2026-01-02T00:00:00+00:00",
            "supersedes": None,
            "superseded_by": correction_id,
            "status": "superseded",
            "quality_score": 0.88,
            "quality_breakdown": {},
            "version_index": 1,
            "conflict_group_id": "grp_phase107_pref",
            "conflict_status": "superseded",
            "reuse_score": 0.0,
            "reuse_count": 0,
            "last_reused_at": None,
            "retention_policy": "persistent",
            "retention_reason": "user_preference",
            "expires_reason": "superseded_by_newer_memory",
            "expires_at": "2026-01-02T00:00:00+00:00",
            "stale_after": None,
            "evidence_strength": 0.75,
            "review_required": False,
            "embedding_status": "skipped",
            "metadata": {"superseded_by": correction_id},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "normalized_summary": "以后回复先给结论再给风险",
            "content_hash": "hash_phase107_old_pref",
        },
    )
    anyio.run(
        _insert_memory_item,
        registry,
        {
            "memory_id": correction_id,
            "organization_id": "org_default",
            "member_id": "mem_xiaoyao",
            "user_id": "user_local_owner",
            "layer": "temporal",
            "kind": "correction",
            "scope_type": "member",
            "scope_id": "mem_xiaoyao",
            "memory_class": "preference",
            "scope_policy": "member_cross_session",
            "summary_text": "用户纠正：回复偏好不是先给结论再给风险，是先看风险再看结论",
            "payload": {"preference": "risk_then_conclusion"},
            "source": {"type": "conversation_turn", "conversation_id": "conv_phase107_b", "channel": "local"},
            "confidence": 0.97,
            "importance": 0.94,
            "sensitivity": "low",
            "durability": "durable",
            "freshness_state": "fresh",
            "valid_from": "2026-01-02T00:00:00+00:00",
            "valid_to": None,
            "supersedes": old_id,
            "superseded_by": None,
            "status": "active",
            "quality_score": 0.93,
            "quality_breakdown": {},
            "version_index": 2,
            "conflict_group_id": "grp_phase107_pref",
            "conflict_status": "resolved",
            "reuse_score": 0.0,
            "reuse_count": 0,
            "last_reused_at": None,
            "retention_policy": "persistent",
            "retention_reason": "user_preference",
            "expires_reason": None,
            "expires_at": None,
            "stale_after": None,
            "evidence_strength": 0.91,
            "review_required": False,
            "embedding_status": "skipped",
            "metadata": {},
            "created_at": "2026-01-02T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "normalized_summary": "用户纠正回复偏好不是先给结论再给风险是先看风险再看结论",
            "content_hash": "hash_phase107_correction",
        },
    )

    search = client.post(
        "/api/memory/search",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase107_c",
            "exclude_conversation_id": "conv_phase107_c",
            "query": "回复偏好 风险 结论",
            "include_cross_session": True,
            "freshness_policy": "exclude_stale",
        },
    ).json()
    old = client.get(f"/api/memory/{old_id}").json()

    assert search["memory_contract_version"] == "phase107.memory_semantic_contract.v1"
    assert search["recall_scope_applied"] == "member_cross_session"
    assert search["items"][0]["memory_id"] == correction_id
    assert search["items"][0]["correction_status"] == "applied"
    assert search["items"][0]["supersedes"] == old_id
    assert old["status"] == "superseded"
    assert old["freshness_state"] == "superseded"
    assert old["superseded_by"] == correction_id
    assert old["correction_status"] is None


def test_phase107_correction_with_context_and_punctuation_supersedes_previous_memory(
    client: TestClient,
) -> None:
    old = client.post(
        "/api/memory/extract",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase107_real_a",
            "text": "记住：以后回复先给结论，再给风险",
        },
    ).json()["memories"][0]
    correction = client.post(
        "/api/memory/extract",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase107_real_b",
            "text": "纠正记忆：回复偏好不是先给结论再给风险，而是先看风险再看结论",
        },
    ).json()["memories"][0]
    old_after = client.get(f"/api/memory/{old['memory_id']}").json()

    assert correction["kind"] == "correction"
    assert correction["supersedes"] == old["memory_id"]
    assert correction["correction_status"] == "applied"
    assert old_after["status"] == "superseded"
    assert old_after["freshness_state"] == "superseded"
    assert old_after["superseded_by"] == correction["memory_id"]


def test_phase107_filtered_entries_keep_state_explanations_for_stale_memories(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    anyio.run(
        _insert_memory_item,
        registry,
        {
            "memory_id": "mem_phase107_stale",
            "organization_id": "org_default",
            "member_id": "mem_xiaoyao",
            "user_id": "user_local_owner",
            "layer": "session",
            "kind": "semantic_note",
            "scope_type": "member",
            "scope_id": "mem_xiaoyao",
            "memory_class": "transient_working_state",
            "scope_policy": "member_cross_session",
            "summary_text": "这是不该被长期默认召回的临时说明",
            "payload": {"note": "temporary"},
            "source": {"type": "conversation_turn", "conversation_id": "conv_phase107_tmp", "channel": "local"},
            "confidence": 0.6,
            "importance": 0.4,
            "sensitivity": "low",
            "durability": "transient",
            "freshness_state": "stale",
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_to": None,
            "supersedes": None,
            "superseded_by": None,
            "status": "active",
            "quality_score": 0.5,
            "quality_breakdown": {},
            "version_index": 1,
            "conflict_group_id": "grp_mem_phase107_stale",
            "conflict_status": "clear",
            "reuse_score": 0.0,
            "reuse_count": 0,
            "last_reused_at": None,
            "retention_policy": "standard",
            "retention_reason": None,
            "expires_reason": None,
            "expires_at": None,
            "stale_after": "2026-01-01T00:00:00+00:00",
            "evidence_strength": 0.2,
            "review_required": False,
            "embedding_status": "skipped",
            "metadata": {},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "normalized_summary": "这是不该被长期默认召回的临时说明",
            "content_hash": "hash_phase107_stale",
        },
    )

    search = client.post(
        "/api/memory/search",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase107_live",
            "query": "临时说明",
            "include_cross_session": True,
            "freshness_policy": "exclude_stale",
        },
    ).json()
    filtered = {item["memory_id"]: item for item in search["filtered"]}

    assert search["memory_contract_version"] == "phase107.memory_semantic_contract.v1"
    assert filtered["mem_phase107_stale"]["reason"] == "stale"
    assert filtered["mem_phase107_stale"]["status"] == "active"
    assert filtered["mem_phase107_stale"]["freshness_state"] == "stale"
    assert filtered["mem_phase107_stale"]["durability"] == "transient"


def test_phase107_readiness_release_and_topology_expose_semantic_contract(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase107 = readiness["phase_readiness"]["phase107_memory_semantic_contract_unification"]
    topology = client.get("/api/system/runtime-topology").json()
    gate = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    report = client.get(f"/api/release-gates/{gate}/report").json()["summary"]
    phase107_release = report["phase107_memory_semantic_contract_unification"]
    registry = cast(FastAPI, client.app).state.registry
    summary = anyio.run(registry.release_gate_service.chat_mainline_signal_summary)

    assert phase107["status"] == "ready"
    assert phase107["details"]["phase107_contract_version"] == "phase107.memory_semantic_contract.v1"
    memory_component = next(item for item in topology["items"] if item["name"] == "memory_service")
    assert (
        memory_component["details"]["memory_semantic_contract_version"]
        == "phase107.memory_semantic_contract.v1"
    )
    assert phase107_release["contract_version"] == "phase107.memory_semantic_contract.v1"
    assert "correction_status" in phase107_release["status_fields"]
    assert summary["phase107_contract_version"] == "phase107.memory_semantic_contract.v1"
    assert summary["phase107_memory_semantic_contract_unification_status"] == "ready"


async def _insert_memory_item(registry: Any, payload: dict[str, Any]) -> None:
    await registry.memory_service._repo.insert_memory_item(payload)
