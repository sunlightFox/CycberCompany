from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase15_vector_status_and_sync_job_use_local_provider(
    client: TestClient,
) -> None:
    status = client.get("/api/vector/status").json()
    sync = client.post(
        "/api/vector/sync-jobs",
        json={
            "target_type": "memory",
            "target_id": "mem_phase15_status",
            "collection_name": "memory_org_default",
            "payload": {"summary_text": "Phase 15 local semantic vector smoke"},
        },
    ).json()
    after = client.get("/api/vector/status").json()

    assert status["provider"] == "local"
    assert status["status"] == "implemented"
    assert status["available"] is True
    assert status["embedding_model"] == "local_hash_v1"
    assert sync["provider"] == "local"
    assert sync["status"] == "completed"
    assert sync["vector_ref_ids"]
    assert after["local_embedding_count"] >= status["local_embedding_count"] + 1


def test_phase15_memory_semantic_search_and_supersede_filter(
    client: TestClient,
) -> None:
    old = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "记住：用户喜欢咖啡"},
    ).json()["memories"][0]
    correction = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "不是咖啡，是茶"},
    ).json()["memories"][0]
    tea = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "茶 饮品偏好"},
    ).json()
    coffee = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "咖啡"},
    ).json()
    old_after = client.get(f"/api/memory/{old['memory_id']}").json()

    assert correction["embedding_status"] == "indexed"
    assert old_after["status"] == "superseded"
    assert tea["items"][0]["memory_id"] == correction["memory_id"]
    assert tea["items"][0]["retrieval_source"] == "semantic_vector"
    assert "semantic_vector" in tea["items"][0]["selection_reason"]
    assert old["memory_id"] not in coffee["selected_memory_ids"]


def test_phase15_knowledge_semantic_search_and_release_summary(
    client: TestClient,
    tmp_path: Path,
) -> None:
    note = tmp_path / "phase15.md"
    note.write_text(
        "Phase 15 upgrades knowledge retrieval with local semantic vectors "
        "and explicit FTS fallback.",
        encoding="utf-8",
    )
    asset = client.post(
        "/api/assets",
        json={
            "asset_type": "knowledge_base",
            "display_name": "Phase 15 Knowledge",
            "sensitivity": "low",
            "config": {"source_type": "file", "root_uri": str(note)},
            "summary_text": "Phase 15 knowledge base",
            "capabilities": ["read_knowledge", "index_knowledge"],
        },
    ).json()
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset["asset_id"],
            "action": "read_knowledge",
            "effect": "allow",
        },
    )
    source = client.post(
        "/api/knowledge/sources",
        json={
            "asset_id": asset["asset_id"],
            "source_type": "markdown",
            "source_uri": str(note),
            "display_name": "Phase 15 note",
            "sensitivity": "low",
        },
    ).json()
    indexed = client.post(f"/api/knowledge/sources/{source['source_id']}/index").json()
    search = client.post(
        "/api/knowledge/search",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_id": asset["asset_id"],
            "query": "semantic vectors FTS fallback",
        },
    ).json()
    client.get("/api/system/runtime-contracts")
    suites = client.get("/api/evals/suites").json()["items"]
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    registry = cast(FastAPI, client.app).state.registry

    assert indexed["chunk_count"] == 1
    assert search["degraded"] is False
    assert search["items"][0]["retrieval_source"] == "semantic_vector"
    assert "semantic_vector" in search["items"][0]["selection_reason"]
    assert "suite_phase15_memory_knowledge_semantic" in {
        item["suite_id"] for item in suites
    }
    assert completed["blocker_count"] == 0
    assert report["summary"]["phase15"]["provider"] == "local"
    assert report["summary"]["phase15"]["local_vector_embeddings"] >= 1
    assert registry.vector_service.provider_name == "local"
