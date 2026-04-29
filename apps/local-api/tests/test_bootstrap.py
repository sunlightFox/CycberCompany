from __future__ import annotations

from typing import cast

import anyio
from app.services.bootstrap import DEFAULT_ORGANIZATION_ID
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_boot_001_first_start_creates_default_foundation(client: TestClient) -> None:
    health = client.get("/health")
    organization = client.get("/api/organization/current")
    members = client.get("/api/members")
    conversations = client.get("/api/chat/conversations")

    assert health.status_code == 200
    assert health.headers["x-trace-id"] == health.json()["trace_id"]
    assert health.json()["default_shell"] == "company"
    assert organization.status_code == 200
    assert organization.json()["display_name"] == "我的一人公司"
    assert members.status_code == 200
    member_items = members.json()["items"]
    assert len(member_items) == 5
    member_by_id = {item["member_id"]: item for item in member_items}
    assert member_by_id["mem_xiaoyao"]["display_name"] == "小曜"
    assert member_by_id["mem_xiaoyao"]["default_brain_id"] == "brain_not_configured"
    assert {"mem_aheng", "mem_ningning", "mem_mobai", "mem_xiaoqi"}.issubset(
        set(member_by_id)
    )
    assert conversations.status_code == 200
    assert conversations.json()["items"][0]["primary_member_id"] == "mem_xiaoyao"
    status = client.get("/api/system/bootstrap-status").json()
    assert all(status.values())


def test_boot_002_repeated_start_does_not_duplicate_defaults(
    tmp_path,
    monkeypatch,
) -> None:
    from app.main import create_app

    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as first_client:
        assert first_client.get("/api/members").json()["items"][0]["member_id"] == "mem_xiaoyao"

    with TestClient(create_app()) as second_client:
        members = second_client.get("/api/members").json()["items"]
        conversations = second_client.get("/api/chat/conversations").json()["items"]

    assert len(members) == 5
    assert len(conversations) == 1


def test_boot_003_startup_repairs_missing_welcome_message(client: TestClient) -> None:
    registry = cast(FastAPI, client.app).state.registry

    anyio.run(_delete_welcome_message, registry)
    anyio.run(registry.bootstrap_service.ensure_defaults)
    conversation = client.get("/api/chat/conversations/conv_default_xiaoyao").json()

    assert any(
        message["message_id"] == "msg_welcome_xiaoyao"
        for message in conversation["messages"]
    )


def test_boot_004_bootstrap_does_not_overwrite_existing_organization(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry

    anyio.run(_rename_default_organization, registry)
    anyio.run(registry.bootstrap_service.ensure_defaults)
    organization = client.get("/api/organization/current").json()

    assert organization["display_name"] == "用户自己的组织名"


async def _delete_welcome_message(registry) -> None:
    await registry.db.execute("DELETE FROM messages WHERE message_id = 'msg_welcome_xiaoyao'")


async def _rename_default_organization(registry) -> None:
    await registry.db.execute(
        "UPDATE organizations SET display_name = ? WHERE organization_id = ?",
        ("用户自己的组织名", DEFAULT_ORGANIZATION_ID),
    )
