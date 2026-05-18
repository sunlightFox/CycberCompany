from __future__ import annotations

from typing import Any, cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.session_context import SessionContextCuratorService


def test_phase92_cross_session_preference_recall_and_correction_override(
    client: TestClient,
) -> None:
    client.post(
        "/api/memory/extract",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase92_a",
            "text": "记住：以后回复先给结论，再给风险",
        },
    )
    search = client.post(
        "/api/memory/search",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase92_b",
            "exclude_conversation_id": "conv_phase92_b",
            "query": "我的回复偏好是什么",
            "include_cross_session": True,
            "memory_classes": ["preference"],
            "durability_filter": ["durable"],
            "freshness_policy": "exclude_stale",
        },
    ).json()

    assert search["recall_scope_applied"] == "member_cross_session"
    assert search["items"]
    assert search["items"][0]["cross_session"] is True
    assert search["items"][0]["memory_class"] == "preference"
    assert search["items"][0]["durability"] == "durable"
    assert search["items"][0]["freshness_state"] == "fresh"

    old_memory_id = search["items"][0]["memory_id"]
    client.post(
        "/api/memory/extract",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase92_b",
            "text": "纠正记忆：回复偏好不是先给结论，再给风险，是先看风险，再看结论",
        },
    )
    after_correction = client.post(
        "/api/memory/search",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase92_c",
            "exclude_conversation_id": "conv_phase92_c",
            "query": "先看风险 再看结论 回复偏好",
            "include_cross_session": True,
            "memory_classes": ["preference"],
            "durability_filter": ["durable"],
            "freshness_policy": "exclude_stale",
        },
    ).json()

    assert after_correction["items"][0]["memory_id"] != old_memory_id
    assert "风险" in after_correction["items"][0]["summary_text"]
    filtered = {item["memory_id"]: item["reason"] for item in after_correction["filtered"]}
    assert filtered[old_memory_id] == "status_superseded"


def test_phase92_stale_and_transient_memory_do_not_pollute_default_recall(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    anyio.run(_insert_memory_item, registry, {
        "memory_id": "mem_phase92_stale",
        "organization_id": "org_default",
        "member_id": "mem_xiaoyao",
        "user_id": "user_local_owner",
        "layer": "session",
        "kind": "semantic_note",
        "scope_type": "member",
        "scope_id": "mem_xiaoyao",
        "memory_class": "transient_working_state",
        "scope_policy": "member_cross_session",
        "summary_text": "这是一条不该长期污染召回的临时说明",
        "payload": {"note": "temporary"},
        "source": {"type": "conversation_turn", "conversation_id": "conv_phase92_tmp", "channel": "local"},
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
        "conflict_group_id": "grp_mem_phase92_stale",
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
        "normalized_summary": "这是一条不该长期污染召回的临时说明",
        "content_hash": "hash_phase92_stale",
    })

    search = client.post(
        "/api/memory/search",
        json={
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_phase92_live",
            "query": "临时说明",
            "include_cross_session": True,
            "freshness_policy": "exclude_stale",
        },
    ).json()
    filtered = {item["memory_id"]: item["reason"] for item in search["filtered"]}

    assert "mem_phase92_stale" not in search["selected_memory_ids"]
    assert filtered["mem_phase92_stale"] == "stale"


def test_phase92_session_context_override_drops_cross_session_preference_priority() -> None:
    service = SessionContextCuratorService()
    context = service.curate(
        presence_state=type(
            "Presence",
            (),
            {
                "identity_state": {"display_name": "助手"},
                "relationship_state": {"user_pressure": "low"},
                "conversation_state": {
                    "latest_instruction_override": True,
                    "user_goal": "按最新要求来",
                    "active_topic": "旧偏好",
                    "continuity_mode": "override",
                },
                "action_state": {"pending_approval": False, "running_task": False},
                "interaction_posture": "collaborative",
            },
        )(),
        user_profile={},
        latest_continuity={},
        recent_messages=[],
        memory_candidates=[
            {
                "memory_id": "mem_cross_pref",
                "memory_class": "preference",
                "durability": "durable",
                "freshness_state": "fresh",
                "cross_session": True,
                "summary_text": "回复先给结论",
                "evidence_strength": 0.9,
                "selection_confidence": 0.9,
            },
            {
                "memory_id": "mem_fact",
                "memory_class": "fact",
                "durability": "durable",
                "freshness_state": "fresh",
                "cross_session": False,
                "summary_text": "当前话题是最新改口",
                "evidence_strength": 0.8,
                "selection_confidence": 0.8,
            },
        ],
    )

    assert context.latest_instruction_override is True
    assert all(item["memory_id"] != "mem_cross_pref" for item in context.relevant_memory_items)
    assert "优先服从这轮显式要求" in context.stable_user_profile_block


def test_phase92_readiness_release_and_topology_expose_memory_governance(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase92 = readiness["phase_readiness"]["phase92_long_term_memory_recall_governance"]
    topology = client.get("/api/system/runtime-topology").json()
    registry = cast(FastAPI, client.app).state.registry
    summary = anyio.run(registry.release_gate_service.chat_mainline_signal_summary)

    assert phase92["details"]["phase92_contract_version"] == "phase92.long_term_memory_recall.v1"
    assert "preference" in phase92["details"]["canonical_memory_classes"]
    memory_component = next(item for item in topology["items"] if item["name"] == "memory_service")
    assert memory_component["details"]["memory_contract_version"] == "phase92.long_term_memory_recall.v1"
    assert summary["phase92_contract_version"] == "phase92.long_term_memory_recall.v1"
    assert summary["phase92_long_term_memory_recall_governance_status"] in {"ready", "partial"}


async def _insert_memory_item(registry: Any, payload: dict[str, Any]) -> None:
    await registry.memory_service._repo.insert_memory_item(payload)
