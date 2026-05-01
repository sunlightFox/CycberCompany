from __future__ import annotations

import json
from typing import cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_memory_001_explicit_remember_writes_active_memory(client: TestClient) -> None:
    response = client.post(
        "/api/memory/extract",
        json={
            "member_id": "mem_xiaoyao",
            "text": "记住：以后开发计划要非常详细，并严格按设计文档推进",
        },
    )
    body = response.json()
    memory = body["memories"][0]

    assert response.status_code == 200
    assert body["blocked"] is False
    assert body["candidates"][0]["decision"] == "auto_written"
    assert memory["status"] == "active"
    assert memory["kind"] == "preference"
    assert memory["source"]["type"] == "manual"
    assert memory["source"]["trace_id"]

    search = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "开发计划 详细"},
    ).json()
    assert search["degraded"] is False
    assert search["items"][0]["memory_id"] == memory["memory_id"]
    assert search["items"][0]["retrieval_source"] == "semantic_vector"
    assert search["items"][0]["embedding_status"] == "indexed"


def test_memory_002_sensitive_text_is_not_written(client: TestClient) -> None:
    response = client.post(
        "/api/memory/extract",
        json={
            "member_id": "mem_xiaoyao",
            "text": "记住：api_key=sk-testsecret000000000000 password=plain",
        },
    )
    body = response.json()
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert response.status_code == 200
    assert body["blocked"] is True
    assert body["memories"] == []
    assert body["candidates"][0]["decision"] == "discarded_sensitive"
    assert "sk-testsecret000000000000" not in audit_text
    assert client.get("/api/memory").json()["items"] == []


def test_memory_003_duplicate_preference_is_not_written_twice(client: TestClient) -> None:
    payload = {
        "member_id": "mem_xiaoyao",
        "text": "记住：以后开发计划要非常详细",
    }
    first = client.post("/api/memory/extract", json=payload).json()
    second = client.post("/api/memory/extract", json=payload).json()
    memories = client.get("/api/memory", params={"status": "active"}).json()["items"]

    assert first["memories"]
    assert second["candidates"][0]["decision"] == "discarded_duplicate"
    assert len(memories) == 1


def test_memory_004_correction_supersedes_old_memory(client: TestClient) -> None:
    old = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "记住：用户喜欢咖啡"},
    ).json()["memories"][0]
    correction = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "不是咖啡，是茶"},
    ).json()["memories"][0]
    old_after = client.get(f"/api/memory/{old['memory_id']}").json()
    relations = client.get(f"/api/memory/{correction['memory_id']}/relations").json()["items"]

    assert correction["kind"] == "correction"
    assert correction["supersedes"] == old["memory_id"]
    assert old_after["status"] == "superseded"
    assert old_after["valid_to"] is not None
    assert relations[0]["relation_type"] == "supersedes"


def test_memory_005_archive_restore_delete_affects_search(client: TestClient) -> None:
    memory = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "记住：以后输出先给结论"},
    ).json()["memories"][0]

    assert client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "先给结论"},
    ).json()["items"]

    archived = client.post(f"/api/memory/{memory['memory_id']}/archive").json()
    after_archive = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "先给结论"},
    ).json()["items"]
    restored = client.post(f"/api/memory/{memory['memory_id']}/restore").json()
    deleted = client.delete(f"/api/memory/{memory['memory_id']}").json()
    after_delete = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "先给结论"},
    ).json()["items"]

    assert archived["status"] == "archived"
    assert after_archive == []
    assert restored["status"] == "active"
    assert deleted["status"] == "deleted"
    assert after_delete == []


def test_memory_006_chat_explicit_memory_command_emits_events_and_trace(
    client: TestClient,
) -> None:
    conversation = client.get("/api/chat/conversations").json()["items"][0]
    response = client.post(
        "/api/chat/turn",
        json={
            "session_id": "ses_memory",
            "conversation_id": conversation["conversation_id"],
            "member_id": "mem_xiaoyao",
            "input": {"type": "text", "text": "记住：以后开发计划要非常详细"},
        },
    )
    body = response.json()
    events = _parse_sse(client.get(body["stream_url"]).text)
    event_names = [event["event"] for event in events]
    memories = client.get("/api/memory", params={"status": "active"}).json()["items"]
    trace = client.get(f"/api/traces/{body['trace_id']}").json()
    span_types = {span["span_type"] for span in trace["spans"]}

    assert response.status_code == 200
    assert "memory.candidate" in event_names
    assert "memory.written" in event_names
    assert "turn.failed" not in event_names
    visible_reply = "".join(
        event["payload"].get("text", "")
        for event in events
        if event["event"] == "response.delta"
    )
    assert "记好" in visible_reply or "记住了" in visible_reply
    assert "以后开发计划要非常详细" in visible_reply
    assert memories[0]["summary_text"] == "以后开发计划要非常详细"
    assert {
        "memory.extract",
        "memory.write",
        "memory.search",
        "memory.compress",
        "memory.vector.upsert",
    }.issubset(span_types)


def test_memory_007_job_runner_extracts_implicit_memory_and_is_idempotent(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry

    anyio.run(
        _create_completed_turn,
        registry,
        "turn_implicit_pref",
        "msg_implicit_pref",
        "我希望以后回复先给结论再展开",
    )
    anyio.run(registry.memory_service.enqueue_extract_after_turn, "turn_implicit_pref")
    first_processed = anyio.run(registry.memory_service.process_pending_jobs)
    second_processed = anyio.run(registry.memory_service.process_pending_jobs)
    memories = client.get("/api/memory", params={"status": "active"}).json()["items"]
    jobs = anyio.run(_list_completed_extract_jobs, registry)
    jobs_api = client.get(
        "/api/memory/jobs",
        params={"job_type": "extract_after_turn"},
    ).json()["items"]

    assert first_processed in {0, 1}
    assert second_processed == 0
    assert len(memories) == 1
    assert memories[0]["kind"] == "preference"
    assert jobs[0]["attempts"] == 1
    assert jobs_api[0]["turn_id"] == "turn_implicit_pref"
    assert jobs_api[0]["status"] == "completed"
    assert jobs_api[0]["attempts"] == 1


def test_memory_008_review_reject_sensitive_and_member_scope_are_hardened(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry

    review = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "以后都按这个流程整理报告"},
    ).json()
    candidate_id = review["candidates"][0]["candidate_id"]
    rejected = client.post(f"/api/memory/candidates/{candidate_id}/reject")
    approve_after_reject = client.post(f"/api/memory/candidates/{candidate_id}/approve")

    sensitive = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "记住：token=secret-for-test"},
    ).json()["candidates"][0]
    approve_sensitive = client.post(
        f"/api/memory/candidates/{sensitive['candidate_id']}/approve"
    )

    anyio.run(_create_other_member, registry)
    other_memory = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_other", "text": "记住：其他成员喜欢短回答"},
    ).json()["memories"][0]
    own_search = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "短回答"},
    ).json()
    other_search = client.post(
        "/api/memory/search",
        json={"member_id": "mem_other", "query": "短回答"},
    ).json()

    assert review["candidates"][0]["decision"] == "needs_review"
    assert rejected.status_code == 200
    assert approve_after_reject.status_code == 409
    assert approve_sensitive.status_code == 400
    assert own_search["items"] == []
    assert other_search["items"][0]["memory_id"] == other_memory["memory_id"]


def test_memory_009_search_response_explains_ranking_and_filters(
    client: TestClient,
) -> None:
    active = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "记住：这个项目规则是后端优先"},
    ).json()["memories"][0]
    archived = client.post(
        "/api/memory/extract",
        json={"member_id": "mem_xiaoyao", "text": "记住：这个项目规则是旧方案"},
    ).json()["memories"][0]
    client.post(f"/api/memory/{archived['memory_id']}/archive")

    search = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "后端优先 项目规则"},
    ).json()
    filtered = {item["memory_id"]: item["reason"] for item in search["filtered"]}

    assert active["memory_id"] in search["selected_memory_ids"]
    assert search["ranking"]
    assert filtered[archived["memory_id"]] == "status_archived"


def test_memory_010_asset_scoped_memory_requires_asset_broker_filter(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    anyio.run(_insert_asset_scoped_memory, registry)

    default_search = client.post(
        "/api/memory/search",
        json={"member_id": "mem_xiaoyao", "query": "知识库凭证"},
    ).json()
    authorized_search = client.post(
        "/api/memory/search",
        json={
            "member_id": "mem_xiaoyao",
            "query": "知识库凭证",
            "include_asset_scoped": True,
            "asset_scope_ids": ["asset_docs"],
        },
    ).json()
    filtered = {item["memory_id"]: item["reason"] for item in default_search["filtered"]}

    assert default_search["items"] == []
    assert filtered["mem_asset_docs"] == "asset_scope_requires_broker"
    assert authorized_search["items"][0]["memory_id"] == "mem_asset_docs"


async def _create_completed_turn(registry, turn_id: str, message_id: str, text: str) -> None:
    conversation_id = "conv_default_xiaoyao"
    trace_id = await registry.trace_service.start_trace(
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    now = "2026-01-01T00:00:00+00:00"
    await registry.chat.insert_message(
        message_id=message_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        author_type="user",
        author_id="user_local_owner",
        content_type="text",
        content_text=text,
        content={"type": "text", "text": text},
        trace_id=trace_id,
        created_at=now,
    )
    await registry.chat.insert_turn(
        turn_id=turn_id,
        conversation_id=conversation_id,
        member_id="mem_xiaoyao",
        user_message_id=message_id,
        trace_id=trace_id,
        status="completed",
        retry_of_turn_id=None,
        created_at=now,
    )


async def _create_other_member(registry) -> None:
    now = "2026-01-01T00:00:00+00:00"
    await registry.db.execute(
        """
        INSERT INTO members (
          member_id, organization_id, department_id, role_id, display_name, avatar_uri,
          status, default_brain_id, persona_profile_id, heart_profile_json,
          memory_policy_json, created_from_shell_id, created_from_template_id,
          metadata_json, created_at, updated_at
        ) VALUES (?, ?, NULL, NULL, ?, NULL, 'online', NULL, ?, '{}', '{}', NULL, NULL, '{}', ?, ?)
        ON CONFLICT(member_id) DO NOTHING
        """,
        ("mem_other", "org_default", "其他成员", "persona_other", now, now),
    )


async def _insert_asset_scoped_memory(registry) -> None:
    now = "2026-01-01T00:00:00+00:00"
    trace_id = await registry.trace_service.start_trace(
        conversation_id="conv_default_xiaoyao",
        turn_id="turn_asset_memory",
    )
    await registry.memory.insert_memory_item(
        {
            "memory_id": "mem_asset_docs",
            "organization_id": "org_default",
            "member_id": "mem_xiaoyao",
            "user_id": "user_local_owner",
            "layer": "asset",
            "kind": "knowledge_fact",
            "scope_type": "asset",
            "scope_id": "asset_docs",
            "summary_text": "知识库凭证使用规则只能通过 Asset Broker 获取",
            "payload": {"fact": "知识库凭证使用规则只能通过 Asset Broker 获取"},
            "source": {
                "type": "manual",
                "conversation_id": None,
                "turn_id": None,
                "message_id": None,
                "trace_id": trace_id,
            },
            "confidence": 0.8,
            "importance": 0.8,
            "sensitivity": "low",
            "valid_from": now,
            "valid_to": None,
            "supersedes": None,
            "status": "active",
            "review_required": False,
            "embedding_status": "skipped",
            "metadata": {"asset_scope": True},
            "created_at": now,
            "updated_at": now,
            "normalized_summary": "知识库凭证使用规则只能通过assetbroker获取",
            "content_hash": "hash_asset_docs",
        }
    )


async def _list_completed_extract_jobs(registry) -> list[dict]:
    return await registry.memory.list_jobs(
        status="completed",
        job_type="extract_after_turn",
    )


def _parse_sse(raw: str) -> list[dict]:
    events: list[dict] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
