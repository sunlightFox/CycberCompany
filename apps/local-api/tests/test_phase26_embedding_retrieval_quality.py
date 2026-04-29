from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient


def test_phase26_contracts_default_provider_and_required_suite(
    client: TestClient,
) -> None:
    status = client.get("/api/vector/status").json()
    providers = client.get("/api/vector/providers").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    suites = client.get("/api/evals/suites").json()["items"]
    by_module = {item["name"]: item for item in contracts}
    external = next(item for item in providers if item["provider_type"] == "external_compatible")
    local_model = next(item for item in providers if item["provider_type"] == "local_model")
    chroma = next(item for item in providers if item["provider_type"] == "chroma")

    assert status["active_provider_id"] == "local_hash_v1"
    assert status["provider"] == "local"
    assert status["embedding_model"] == "local_hash_v1"
    assert status["allow_cloud"] is False
    assert external["status"] == "disabled"
    assert external["allow_cloud"] is False
    assert external["secret_ref_present"] is False
    assert local_model["status"] in {"disabled", "degraded"}
    assert chroma["status"] in {"disabled", "degraded", "active"}
    assert by_module["EmbeddingProviderInterface"]["status"] == "implemented"
    assert by_module["EmbeddingPrivacyRouter"]["status"] == "implemented"
    assert by_module["LocalModelEmbeddingProvider"]["status"] == "implemented"
    assert by_module["ChromaEmbeddingProvider"]["status"] == "implemented"
    assert by_module["ExternalEmbeddingProvider"]["status"] == "implemented_with_fallback"
    assert by_module["VectorReindexer"]["status"] == "implemented"
    assert by_module["RetrievalQualityBenchmark"]["status"] == "implemented"
    assert "suite_phase26_embedding_retrieval_quality" in {
        item["suite_id"] for item in suites
    }


def test_phase26_local_model_degrades_without_model_file(
    client: TestClient,
    tmp_path: Path,
) -> None:
    missing_model = tmp_path / "missing-model.bin"
    updated = client.patch(
        "/api/vector/providers/local_model_default",
        json={
            "status": "active",
            "provider_name": "local_model",
            "embedding_model": "phase26-local-model",
            "embedding_dim": 96,
            "config": {
                "model_path": str(missing_model),
                "model_name": "phase26-local-model",
                "device": "cpu",
                "batch_size": 4,
                "timeout_seconds": 5,
                "max_text_tokens": 512,
            },
        },
    ).json()
    status = client.get("/api/vector/status").json()

    assert updated["status"] == "degraded"
    assert updated["degraded_reason"] == "local_model_not_configured"
    assert updated["health_status"] == "degraded"
    assert status["active_provider_id"] == "local_hash_v1"


def test_phase26_fake_external_provider_semantic_hit_and_privacy_blocks(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    secret_ref, _ = registry.secret_store.put_secret("phase26-fake-provider-key")
    updated = client.patch(
        "/api/vector/providers/external_compatible_default",
        json={
            "status": "active",
            "provider_name": "external_compatible",
            "embedding_model": "phase26-fake-embedding",
            "embedding_dim": 96,
            "privacy_policy": "external_allowed_low_medium",
            "allow_cloud": True,
            "secret_ref": secret_ref,
            "config": {
                "endpoint": "fake://embedding",
                "fake_embedding": True,
                "timeout_seconds": 3,
                "max_text_tokens": 512,
            },
        },
    ).json()

    memory = client.post(
        "/api/memory/extract",
        json={
            "member_id": "mem_xiaoyao",
            "text": "记住：Phase26 用户喜欢咖啡、语义检索质量和本地优先 fallback。",
        },
    ).json()["memories"][0]
    search = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "咖啡 语义 检索 质量 偏好"},
    ).json()
    private_job = client.post(
        "/api/vector/sync-jobs",
        json={
            "target_type": "memory",
            "target_id": "phase26_private",
            "target_provider": "external_compatible_default",
            "privacy_level": "high",
            "payload": {"text": "phase26 private text should stay local"},
        },
    ).json()
    sensitive_job = client.post(
        "/api/vector/sync-jobs",
        json={
            "target_type": "memory",
            "target_id": "phase26_sensitive",
            "target_provider": "external_compatible_default",
            "privacy_level": "medium",
            "payload": {"text": "api_key=phase26-secret-value should never leave local"},
        },
    ).json()

    assert updated["status"] == "active"
    assert updated["secret_ref_present"] is True
    assert search["items"]
    assert search["items"][0]["memory_id"] == memory["memory_id"]
    assert search["items"][0]["provider"] == "external_compatible"
    assert search["items"][0]["embedding_model"] == "phase26-fake-embedding"
    assert "local_hash_v1" in search["items"][0]["fallback_chain"]
    assert private_job["provider"] == "local"
    assert private_job["payload"]["privacy_block_reason"] == "privacy_high_local_only"
    assert private_job["payload"]["fallback_chain"][:2] == [
        "external_compatible_default",
        "local_hash_v1",
    ]
    assert sensitive_job["provider"] == "local"
    assert sensitive_job["payload"]["privacy_block_reason"] == (
        "sensitive_text_external_embedding_blocked"
    )
    assert "phase26-secret-value" not in json.dumps(
        {"private": private_job, "sensitive": sensitive_job},
        ensure_ascii=False,
    )


def test_phase26_reindex_shadow_success_and_failure_keep_old_index(
    client: TestClient,
    tmp_path: Path,
) -> None:
    registry = cast(Any, client.app).state.registry
    secret_ref, _ = registry.secret_store.put_secret("phase26-reindex-fake-key")
    client.patch(
        "/api/vector/providers/external_compatible_default",
        json={
            "status": "active",
            "provider_name": "external_compatible",
            "embedding_model": "phase26-reindex-fake",
            "embedding_dim": 96,
            "allow_cloud": True,
            "secret_ref": secret_ref,
            "config": {"endpoint": "fake://embedding", "fake_embedding": True},
        },
    )
    sync = client.post(
        "/api/vector/sync-jobs",
        json={
            "target_type": "memory",
            "target_id": "phase26_reindex_source",
            "collection_name": "memory_phase26_reindex",
            "payload": {"text": "Phase26 reindex source text for rollback validation"},
        },
    ).json()
    success = client.post(
        "/api/vector/sync-jobs",
        json={
            "job_type": "reindex",
            "target_type": "memory",
            "collection_name": "memory_phase26_reindex",
            "source_provider": "local_hash_v1",
            "target_provider": "external_compatible_default",
            "strategy": "shadow_index",
            "dry_run": True,
            "payload": {"text": "Phase26 dry-run reindex"},
        },
    ).json()
    missing_model = tmp_path / "missing-reindex-model.bin"
    client.patch(
        "/api/vector/providers/local_model_default",
        json={
            "status": "active",
            "embedding_model": "phase26-missing-reindex-model",
            "embedding_dim": 96,
            "config": {"model_path": str(missing_model)},
        },
    )
    failed = client.post(
        "/api/vector/sync-jobs",
        json={
            "job_type": "reindex",
            "target_type": "memory",
            "collection_name": "memory_phase26_reindex",
            "source_provider": "local_hash_v1",
            "target_provider": "local_model_default",
            "strategy": "validate_before_switch",
            "payload": {"text": "Phase26 failed reindex should not switch"},
        },
    ).json()

    assert sync["status"] == "completed"
    assert success["status"] == "completed"
    assert success["payload"]["reindex_progress"]["rollback_available"] is True
    assert success["payload"]["target_provider"] == "external_compatible_default"
    assert failed["status"] == "failed"
    assert failed["degraded_reason"] in {
        "local_model_not_configured",
        "target_provider_unavailable",
    }
    assert failed["payload"]["target_provider"] == "local_model_default"
    assert client.get("/api/vector/status").json()["status"] == "implemented"


def test_phase26_knowledge_retrieval_and_release_phase23_aggregation(
    client: TestClient,
    tmp_path: Path,
) -> None:
    note = tmp_path / "phase26.md"
    note.write_text(
        "Phase 26 knowledge retrieval validates provider confidence, section recall, "
        "semantic vector hits, and explicit FTS fallback evidence.",
        encoding="utf-8",
    )
    asset = client.post(
        "/api/assets",
        json={
            "asset_type": "knowledge_base",
            "display_name": "Phase 26 Knowledge",
            "sensitivity": "low",
            "config": {"source_type": "file", "root_uri": str(note)},
            "summary_text": "Phase 26 knowledge base",
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
            "display_name": "Phase 26 note",
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
            "query": "provider confidence section recall semantic vector",
        },
    ).json()
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase26_embedding_retrieval_quality"},
    ).json()
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()

    assert indexed["chunk_count"] == 1
    assert search["items"][0]["retrieval_source"] == "semantic_vector"
    assert search["items"][0]["fallback_chain"]
    assert search["items"][0]["selection_confidence"] is not None
    assert run["status"] == "passed"
    assert run["total_cases"] == 10
    assert completed["status"] == "ready_for_release"
    assert report["summary"]["phase26"]["suite_id"] == (
        "suite_phase26_embedding_retrieval_quality"
    )
    assert report["summary"]["phase26"]["registered_cases"] == 10
    assert report["summary"]["phase26"]["leakage_count"] == 0
    assert report["summary"]["phase23"]["capability_scores"]["phase26"]["registered"] is True
