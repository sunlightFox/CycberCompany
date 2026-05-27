from __future__ import annotations

from pathlib import Path
from typing import cast

import anyio
from app.services.bootstrap import (
    DEFAULT_BRAIN_ID,
    DEFAULT_CODEX_API_KEY_REF,
    DEFAULT_CODEX_CONTEXT_WINDOW,
    DEFAULT_CODEX_DISPLAY_NAME,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
    DEFAULT_CODEX_TEXT_VERBOSITY,
    DEFAULT_MEMBER_VOICE_IDS,
    DEFAULT_ORGANIZATION_ID,
    DIRECT_MEMBER_SEEDS,
    _read_codex_runtime_config,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_boot_001_first_start_creates_default_foundation(client: TestClient) -> None:
    health = client.get("/health")
    organization = client.get("/api/organization/current")
    members = client.get("/api/members")
    brains = client.get("/api/brains")
    conversations = client.get("/api/chat/conversations")

    assert health.status_code == 200
    assert health.headers["x-trace-id"] == health.json()["trace_id"]
    assert health.json()["default_shell"] == "company"
    assert organization.status_code == 200
    assert organization.json()["display_name"] == "我的一人公司"
    assert members.status_code == 200
    assert brains.status_code == 200
    brain_by_id = {item["brain_id"]: item for item in brains.json()["items"]}
    default_brain = brain_by_id[DEFAULT_BRAIN_ID]
    assert default_brain["display_name"] == DEFAULT_CODEX_DISPLAY_NAME
    assert default_brain["provider"] == "openai_compatible"
    assert default_brain["endpoint"] == "http://127.0.0.1:8317/v1"
    assert default_brain["model_name"] == DEFAULT_CODEX_MODEL
    assert default_brain["api_key_ref"] == DEFAULT_CODEX_API_KEY_REF
    assert default_brain["status"] == "configured"
    assert default_brain["is_local"] is False
    assert default_brain["allow_cloud"] is True
    assert default_brain["protocol_family"] == "responses"
    assert default_brain["request_format"] == "responses"
    assert default_brain["response_format"] == "openai_responses"
    assert default_brain["context_window"] == DEFAULT_CODEX_CONTEXT_WINDOW
    assert default_brain["privacy_policy"]["codex_provider"] == "custom"
    assert default_brain["privacy_policy"]["codex_wire_api"] == "responses"
    assert default_brain["privacy_policy"]["requires_openai_auth"] is True
    assert default_brain["privacy_policy"]["disable_response_storage"] is True
    assert default_brain["privacy_policy"]["approvals_reviewer"] == "user"
    assert default_brain["privacy_policy"]["reasoning_effort"] == DEFAULT_CODEX_REASONING_EFFORT
    assert default_brain["privacy_policy"]["text_verbosity"] == DEFAULT_CODEX_TEXT_VERBOSITY
    member_items = members.json()["items"]
    assert len(member_items) == 11
    member_by_id = {item["member_id"]: item for item in member_items}
    assert member_by_id["mem_xiaoyao"]["display_name"] == "小曜"
    assert member_by_id["mem_xiaoyao"]["default_brain_id"] == "brain_not_configured"
    assert member_by_id["mem_xiaowu"]["display_name"] == "小吴"
    assert member_by_id["mem_xiaowu"]["default_brain_id"] == "brain_not_configured"
    assert member_by_id["mem_xiaowu"]["created_from_template_id"] is None
    assert {"mem_aheng", "mem_ningning", "mem_mobai", "mem_xiaoqi"}.issubset(
        set(member_by_id)
    )
    for seed in DIRECT_MEMBER_SEEDS:
        assert member_by_id[str(seed["member_id"])]["display_name"] == seed["display_name"]
        assert member_by_id[str(seed["member_id"])]["created_from_template_id"] is None
    assert conversations.status_code == 200
    assert conversations.json()["items"][0]["primary_member_id"] == "mem_xiaoyao"
    member_voice_ids: dict[str, str] = {}
    for member in member_items:
        bindings = client.get(f"/api/voice/members/{member['member_id']}/bindings")
        assert bindings.status_code == 200
        items = bindings.json()["items"]
        assert len(items) == 1
        binding = items[0]
        assert binding["provider"] == "edge"
        assert binding["voice_profile_id"].startswith("vpr_member_")
        member_voice_ids[member["member_id"]] = binding["provider_voice_id"]
    assert len(set(member_voice_ids.values())) == len(member_voice_ids)
    assert member_voice_ids["mem_xiaoyao"] == DEFAULT_MEMBER_VOICE_IDS["xiaoyao"]
    assert member_voice_ids["mem_xiaowu"] == DEFAULT_MEMBER_VOICE_IDS["xiaowu"]
    assert member_voice_ids["mem_chenxi"] == DEFAULT_MEMBER_VOICE_IDS["chenxi"]
    assert member_voice_ids["mem_jihan"] == DEFAULT_MEMBER_VOICE_IDS["jihan"]
    assert member_voice_ids["mem_suyin"] == DEFAULT_MEMBER_VOICE_IDS["suyin"]
    assert member_voice_ids["mem_qiaoqiao"] == DEFAULT_MEMBER_VOICE_IDS["qiaoqiao"]
    assert member_voice_ids["mem_anan"] == DEFAULT_MEMBER_VOICE_IDS["anan"]
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

    assert len(members) == 11
    assert [item["member_id"] for item in members].count("mem_xiaowu") == 1
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


def test_boot_004_managed_default_brain_prefers_codex_auth_key_ref(
    tmp_path,
    monkeypatch,
) -> None:
    from app.main import create_app

    root_dir = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("CYCBER_ROOT", str(root_dir))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-current-env")
    with TestClient(create_app()) as test_client:
        registry = cast(FastAPI, test_client.app).state.registry
        anyio.run(_set_default_brain_api_key_ref, registry, "sec_stale_local")
        anyio.run(registry.bootstrap_service.ensure_defaults)
        brain = anyio.run(_default_brain_row, registry)

    assert brain["api_key_ref"] == DEFAULT_CODEX_API_KEY_REF
    assert brain["status"] == "configured"


def test_boot_004_real_model_wire_api_env_can_select_responses(
    tmp_path,
    monkeypatch,
) -> None:
    from app.main import create_app

    root_dir = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("CYCBER_ROOT", str(root_dir))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CYCBER_REAL_MODEL_ENDPOINT", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("CYCBER_REAL_MODEL_WIRE_API", "responses")
    monkeypatch.setenv("CYCBER_REAL_MODEL_API_KEY_REF", "codex-auth://OPENAI_API_KEY")
    monkeypatch.setenv("CYCBER_REAL_MODEL_NAME", "gpt-5.4-mini")
    with TestClient(create_app()) as test_client:
        brain = test_client.get("/api/brains").json()["items"][0]

    assert brain["model_name"] == "gpt-5.4-mini"
    assert brain["protocol_family"] == "responses"
    assert brain["request_format"] == "responses"
    assert brain["response_format"] == "openai_responses"
    assert brain["api_key_ref"] == "codex-auth://OPENAI_API_KEY"
    assert brain["privacy_policy"]["codex_wire_api"] == "responses"


def test_boot_004_managed_default_brain_allows_model_override(
    tmp_path,
    monkeypatch,
) -> None:
    from app.main import create_app

    root_dir = Path(__file__).resolve().parents[3]
    monkeypatch.setenv("CYCBER_ROOT", str(root_dir))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CYCBER_REAL_MODEL_MODEL", "gpt-5.4-mini")
    with TestClient(create_app()) as test_client:
        registry = cast(FastAPI, test_client.app).state.registry
        brain = anyio.run(_default_brain_row, registry)

    assert brain["model_name"] == "gpt-5.4-mini"


def test_boot_004_managed_default_brain_reads_codex_config(
    tmp_path,
    monkeypatch,
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        """
model_provider = "custom"
model = "gpt-5.5"
model_reasoning_effort = "high"

[model_providers.custom]
wire_api = "responses"
requires_openai_auth = true
base_url = "http://127.0.0.1:8317/v1"
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.services.bootstrap.Path.home", lambda: tmp_path)

    runtime = _read_codex_runtime_config()

    assert runtime["codex_provider"] == "custom"
    assert runtime["endpoint"] == "http://127.0.0.1:8317/v1"
    assert runtime["model"] == "gpt-5.5"
    assert runtime["wire_api"] == "responses"
    assert runtime["requires_openai_auth"] is True
    assert runtime["api_key_ref"] == DEFAULT_CODEX_API_KEY_REF


def test_boot_004_bootstrap_does_not_overwrite_existing_organization(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry

    anyio.run(_rename_default_organization, registry)
    anyio.run(registry.bootstrap_service.ensure_defaults)
    organization = client.get("/api/organization/current").json()

    assert organization["display_name"] == "用户自己的组织名"


def test_boot_005_bootstrap_does_not_overwrite_member_voice_binding(
    client: TestClient,
) -> None:
    registry = cast(FastAPI, client.app).state.registry
    profile = client.post(
        "/api/voice/profiles",
        json={
            "display_name": "小曜用户自定义声音",
            "provider": "edge",
            "provider_voice_id": "zh-CN-YunxiNeural",
            "output_format": "wav",
        },
    )
    assert profile.status_code == 200, profile.text
    profile_id = profile.json()["voice_profile_id"]
    binding = client.post(
        "/api/voice/bindings",
        json={
            "member_id": "mem_xiaoyao",
            "voice_profile_id": profile_id,
            "binding_scope": "default",
            "reply_mode": "explicit_request_only",
            "priority": 1000,
            "status": "active",
        },
    )
    assert binding.status_code == 200, binding.text

    anyio.run(registry.bootstrap_service.ensure_defaults)
    bindings = client.get("/api/voice/members/mem_xiaoyao/bindings").json()["items"]

    assert len(bindings) == 1
    assert bindings[0]["voice_profile_id"] == profile_id
    assert bindings[0]["provider_voice_id"] == "zh-CN-YunxiNeural"


async def _delete_welcome_message(registry) -> None:
    await registry.db.execute("DELETE FROM messages WHERE message_id = 'msg_welcome_xiaoyao'")


async def _set_default_brain_api_key_ref(registry, api_key_ref: str) -> None:
    await registry.db.execute(
        """
        UPDATE brains
        SET api_key_ref = ?, status = 'healthy', display_name = 'User Renamed Default'
        WHERE brain_id = ?
        """,
        (api_key_ref, DEFAULT_BRAIN_ID),
    )


async def _default_brain_row(registry) -> dict:
    row = await registry.db.fetch_one(
        "SELECT api_key_ref, model_name, status FROM brains WHERE brain_id = ?",
        (DEFAULT_BRAIN_ID,),
    )
    return dict(row)


async def _rename_default_organization(registry) -> None:
    await registry.db.execute(
        "UPDATE organizations SET display_name = ? WHERE organization_id = ?",
        ("用户自己的组织名", DEFAULT_ORGANIZATION_ID),
    )
