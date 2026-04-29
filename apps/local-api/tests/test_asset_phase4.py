from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_asset_001_account_secret_grant_handle_and_revoke(client: TestClient) -> None:
    asset = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": "小红书账号",
            "provider": "xiaohongshu",
            "sensitivity": "high",
            "secret_value": "token=phase4-secret",
            "config": {
                "platform": "xiaohongshu",
                "username": "local_owner",
                "auth_type": "token",
            },
            "summary_text": "小红书账号，可用于草稿和发布前确认",
            "capabilities": ["read_profile", "draft_post", "publish_post"],
        },
    ).json()
    asset_id = asset["asset_id"]
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    grant_read = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "read_profile",
            "effect": "allow",
        },
    ).json()
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "draft_post",
            "effect": "allow",
        },
    )
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "publish_post",
            "effect": "allow",
        },
    )

    query = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": ["read_profile", "draft_post", "publish_post"],
            "keywords": ["小红书"],
        },
    ).json()
    handle = query["handles"][0]
    validate = client.post(
        f"/api/assets/handles/{handle['handle_id']}/validate",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "action": "read_profile",
        },
    )
    publish_validate = client.post(
        f"/api/assets/handles/{handle['handle_id']}/validate",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "action": "publish_post",
        },
    )
    disabled = client.post(f"/api/assets/{asset_id}/disable").json()
    validate_after_disable = client.post(
        f"/api/assets/handles/{handle['handle_id']}/validate",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "action": "read_profile",
        },
    )
    events = client.get(f"/api/assets/handles/{handle['handle_id']}/events").json()["items"]

    assert "phase4-secret" not in json.dumps(asset, ensure_ascii=False)
    assert "phase4-secret" not in audit_text
    assert asset["has_secret"] is True
    assert asset["secret_ref"] is None
    assert asset["config"] == {
        "platform": "xiaohongshu",
        "username": "local_owner",
        "auth_type": "token",
    }
    assert grant_read["effect"] == "allow"
    assert handle["allowed_actions"] == ["read_profile", "draft_post"]
    assert handle["approval_required_actions"] == ["publish_post"]
    assert validate.status_code == 200
    assert publish_validate.status_code == 409
    assert publish_validate.json()["error"]["code"] == "APPROVAL_REQUIRED"
    assert disabled["status"] == "disabled"
    assert validate_after_disable.status_code == 400
    assert {event["event_type"] for event in events} >= {"issued", "validated", "revoked"}


def test_asset_002_capability_deny_overrides_allow(client: TestClient) -> None:
    asset_id = _create_account_asset(client)
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "read_profile",
            "effect": "allow",
            "priority": 1,
        },
    )
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "read_profile",
            "effect": "deny",
            "priority": 100,
        },
    )

    decision = client.post(
        "/api/capabilities/decide",
        json={
            "subject": {"subject_type": "member", "subject_id": "mem_xiaoyao"},
            "object": {"object_type": "asset", "object_id": asset_id},
            "action": "read_profile",
            "context": {},
        },
    ).json()
    query = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": ["read_profile"],
        },
    )

    assert decision["allowed"] is False
    assert decision["reason"] == "deny_policy_matched"
    assert decision["decision_id"]
    assert query.status_code == 403
    assert query.json()["error"]["code"] == "ASSET_ACCESS_DENIED"


def test_asset_003_knowledge_index_search_and_access_control(
    client: TestClient,
    tmp_path: Path,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    anyio.run(_create_other_member, registry)
    root = tmp_path / "kb"
    root.mkdir()
    note = root / "phase4.md"
    note.write_text("第四阶段资产中心必须通过 Asset Broker 发放句柄。", encoding="utf-8")
    asset = client.post(
        "/api/assets",
        json={
            "asset_type": "knowledge_base",
            "display_name": "项目知识库",
            "sensitivity": "low",
            "config": {"source_type": "folder", "root_uri": str(root)},
            "summary_text": "项目知识库",
            "capabilities": ["read_knowledge", "index_knowledge"],
        },
    ).json()
    asset_id = asset["asset_id"]
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "read_knowledge",
            "effect": "allow",
        },
    )
    source = client.post(
        "/api/knowledge/sources",
        json={
            "asset_id": asset_id,
            "source_type": "markdown",
            "source_uri": str(note),
            "display_name": "阶段四说明",
            "sensitivity": "low",
        },
    ).json()
    indexed = client.post(f"/api/knowledge/sources/{source['source_id']}/index").json()
    search = client.post(
        "/api/knowledge/search",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_id": asset_id,
            "query": "Asset Broker 句柄",
        },
    ).json()
    denied = client.post(
        "/api/knowledge/search",
        json={
            "subject_type": "member",
            "subject_id": "mem_other",
            "asset_id": asset_id,
            "query": "Asset Broker",
        },
    )
    logs = client.get("/api/knowledge/access-logs").json()["items"]

    assert indexed["chunk_count"] == 1
    assert search["items"][0]["asset_id"] == asset_id
    assert "Asset Broker" in search["items"][0]["content_preview"]
    assert search["degraded"] is False
    assert search["items"][0]["retrieval_source"] == "semantic_vector"
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "ASSET_ACCESS_DENIED"
    assert logs


def test_asset_004_handle_context_reuse_and_mismatch(client: TestClient) -> None:
    asset_id = _create_account_asset(client)
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "read_profile",
            "effect": "allow",
        },
    )

    payload = {
        "subject_type": "member",
        "subject_id": "mem_xiaoyao",
        "asset_type": "account",
        "requested_actions": ["read_profile"],
        "conversation_id": "conv_a",
    }
    first = client.post("/api/assets/query", json=payload).json()["handles"][0]
    second = client.post("/api/assets/query", json=payload).json()["handles"][0]
    wrong_context = client.post(
        f"/api/assets/handles/{first['handle_id']}/validate",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "action": "read_profile",
            "conversation_id": "conv_b",
        },
    )
    correct_context = client.post(
        f"/api/assets/handles/{first['handle_id']}/validate",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "action": "read_profile",
            "conversation_id": "conv_a",
        },
    )
    events = client.get(f"/api/assets/handles/{first['handle_id']}/events").json()["items"]

    assert second["handle_id"] == first["handle_id"]
    assert wrong_context.status_code == 403
    assert wrong_context.json()["error"]["code"] == "ASSET_HANDLE_INVALID"
    assert correct_context.status_code == 200
    assert {event["event_type"] for event in events} >= {"issued", "reused", "validated"}


def test_asset_005_capability_update_revokes_active_handles(client: TestClient) -> None:
    asset_id = _create_account_asset(client)
    grant = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "read_profile",
            "effect": "allow",
        },
    ).json()
    handle = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": ["read_profile"],
        },
    ).json()["handles"][0]

    updated = client.patch(
        f"/api/assets/grants/{grant['edge_id']}",
        json={"effect": "deny", "priority": 100},
    ).json()
    validate_after_update = client.post(
        f"/api/assets/handles/{handle['handle_id']}/validate",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "action": "read_profile",
        },
    )
    query_after_update = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": ["read_profile"],
        },
    )
    events = client.get(f"/api/assets/handles/{handle['handle_id']}/events").json()["items"]
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert updated["effect"] == "deny"
    assert validate_after_update.status_code == 400
    assert validate_after_update.json()["error"]["code"] == "ASSET_HANDLE_INVALID"
    assert query_after_update.status_code == 403
    assert query_after_update.json()["error"]["code"] == "ASSET_ACCESS_DENIED"
    assert any(event["reason"] == "capability_updated" for event in events)
    assert "asset.handle.revoked" in audit_text


def test_asset_006_knowledge_sensitive_content_is_blocked(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "kb"
    root.mkdir()
    note = root / "secret-note.md"
    note.write_text("api_key=phase4-sensitive-key\n项目规则不应被索引。", encoding="utf-8")
    asset = client.post(
        "/api/assets",
        json={
            "asset_type": "knowledge_base",
            "display_name": "敏感知识库",
            "sensitivity": "high",
            "config": {"source_type": "folder", "root_uri": str(root)},
            "summary_text": "敏感知识库",
            "capabilities": ["read_knowledge", "index_knowledge"],
        },
    ).json()
    asset_id = asset["asset_id"]
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "read_knowledge",
            "effect": "allow",
        },
    )
    source = client.post(
        "/api/knowledge/sources",
        json={
            "asset_id": asset_id,
            "source_type": "markdown",
            "source_uri": str(note),
            "display_name": "敏感说明",
            "sensitivity": "high",
        },
    ).json()

    indexed = client.post(f"/api/knowledge/sources/{source['source_id']}/index")
    search = client.post(
        "/api/knowledge/search",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_id": asset_id,
            "query": "项目规则",
        },
    ).json()
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert indexed.status_code == 422
    assert indexed.json()["error"]["code"] == "KNOWLEDGE_INDEX_FAILED"
    assert search["items"] == []
    assert "phase4-sensitive-key" not in audit_text
    assert "knowledge.index.failed" in audit_text


def _create_account_asset(client: TestClient) -> str:
    return client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": "测试账号",
            "provider": "local",
            "sensitivity": "medium",
            "config": {"platform": "test", "username": "owner", "auth_type": "token"},
            "summary_text": "测试账号",
        },
    ).json()["asset_id"]


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
