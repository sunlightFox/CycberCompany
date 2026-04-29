from __future__ import annotations

from pathlib import Path
from typing import cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_phase20_vector_provider_contracts_and_suite(client: TestClient) -> None:
    status = client.get("/api/vector/status").json()
    providers = client.get("/api/vector/providers").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    external = next(item for item in providers if item["provider_type"] == "external_compatible")

    assert status["provider"] == "local"
    assert status["embedding_model"] == "local_hash_v1"
    assert status["privacy_policy"] == "local_only"
    assert status["allow_cloud"] is False
    assert external["status"] == "disabled"
    assert external["allow_cloud"] is False
    by_module = {item["name"]: item for item in contracts}
    assert by_module["EmbeddingProviderResolver"]["status"] == "implemented"
    assert by_module["MemoryReranker"]["status"] == "implemented"
    assert by_module["KnowledgeReranker"]["status"] == "implemented"
    assert by_module["RetrievalDiagnostics"]["status"] == "implemented"
    assert by_module["ExternalEmbeddingProvider"]["details"]["allow_cloud_default"] is False
    assert any(item["gap_id"] == "gap_external_embedding_provider_disabled" for item in gaps)
    assert "suite_phase20_memory_knowledge_quality" in {item["suite_id"] for item in suites}


def test_phase20_memory_rerank_suppresses_superseded_and_sensitive_items(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    old = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "记住：用户喜欢咖啡"},
    ).json()["memories"][0]
    correction = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "不是咖啡，是茶"},
    ).json()["memories"][0]
    anyio.run(
        registry.memory.insert_memory_item,
        {
            "memory_id": "mem_phase20_sensitive",
            "organization_id": "org_default",
            "member_id": "mem_xiaoyao",
            "user_id": "user_local_owner",
            "layer": "semantic",
            "kind": "credential_note",
            "scope_type": "member",
            "scope_id": "mem_xiaoyao",
            "summary_text": "敏感钱包凭证不得进入模型上下文",
            "payload": {},
            "source": {"type": "manual", "trace_id": "trace_phase20_sensitive"},
            "confidence": 0.9,
            "importance": 0.9,
            "sensitivity": "credential",
            "status": "active",
            "embedding_status": "skipped",
            "metadata": {},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "normalized_summary": "敏感钱包凭证不得进入模型上下文",
            "content_hash": "phase20_sensitive_hash",
        },
    )

    result = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "茶 饮品偏好"},
    ).json()
    diagnostics = client.get(f"/api/retrieval/diagnostics/{result['retrieval_id']}").json()

    assert result["items"][0]["memory_id"] == correction["memory_id"]
    assert result["items"][0]["rerank_score"] is not None
    assert result["items"][0]["selection_confidence"] is not None
    assert "rerank_quality_score" in result["items"][0]["selection_reason"]
    assert old["memory_id"] not in result["selected_memory_ids"]
    reasons = {item["reason"] for item in diagnostics["suppressed_items"]}
    assert "status_superseded" in reasons
    assert "sensitivity_credential" in reasons


def test_phase20_knowledge_rerank_diagnostics_and_release_summary(
    client: TestClient,
    tmp_path: Path,
) -> None:
    note = tmp_path / "phase20.md"
    note.write_text(
        "Phase 20 retrieval quality keeps semantic vector hits distinct from FTS fallback. "
        "It records source trace, rerank score, and untrusted content markers.",
        encoding="utf-8",
    )
    asset = client.post(
        "/api/assets",
        json={
            "asset_type": "knowledge_base",
            "display_name": "Phase 20 Knowledge",
            "sensitivity": "low",
            "config": {"source_type": "file", "root_uri": str(note)},
            "summary_text": "Phase 20 knowledge base",
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
            "display_name": "Phase 20 note",
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
            "query": "retrieval quality semantic vector source trace",
        },
    ).json()
    diagnostics = client.get(f"/api/retrieval/diagnostics/{search['retrieval_id']}").json()
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase20_memory_knowledge_quality"},
    ).json()
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert indexed["chunk_count"] == 1
    assert search["retrieval_id"]
    assert search["items"][0]["retrieval_source"] == "semantic_vector"
    assert search["items"][0]["rerank_score"] is not None
    assert search["items"][0]["untrusted_external_content"] is False
    assert diagnostics["target_type"] == "knowledge"
    assert diagnostics["rerank_runs"]
    assert diagnostics["quality_reports"]
    assert run["status"] == "passed"
    assert run["total_cases"] == 10
    assert completed["status"] == "ready_for_release"
    phase20 = report["summary"]["phase20"]
    assert phase20["registered_cases"] == 10
    assert phase20["failed_results"] == 0
    assert phase20["provider_status"]["local_hash_active"] == 1
