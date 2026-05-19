from __future__ import annotations

import json
from typing import Any

import pytest
from app.services import chat as chat_module
from app.services.bootstrap import DIRECT_MEMBER_SEEDS
from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("member_id", "display_name", "profile_id", "summary_hint", "required_skill"),
    [
        ("mem_chenxi", "晨曦", "persona_mem_chenxi", "首席助理", "coordination"),
        ("mem_jihan", "季寒", "persona_mem_jihan", "架构师", "architecture_design"),
        ("mem_suyin", "素音", "persona_mem_suyin", "产品经理", "product_design"),
        ("mem_qiaoqiao", "乔乔", "persona_mem_qiaoqiao", "内容运营", "social_copywriting"),
        ("mem_anan", "安安", "persona_mem_anan", "家庭管家", "daily_planning"),
    ],
)
def test_direct_seeded_agents_bootstrap_with_profiles_and_skills(
    client: TestClient,
    member_id: str,
    display_name: str,
    profile_id: str,
    summary_hint: str,
    required_skill: str,
) -> None:
    members = {item["member_id"]: item for item in client.get("/api/members").json()["items"]}
    profile = client.get(f"/api/persona/profiles/{profile_id}").json()
    compiled = client.get(f"/api/persona/{member_id}/soul/compiled").json()
    skill_policy = client.get(f"/api/members/{member_id}/skill-policies").json()

    assert member_id in members
    assert members[member_id]["display_name"] == display_name
    assert members[member_id]["created_from_template_id"] is None
    assert members[member_id]["persona_profile_id"] == profile_id
    assert summary_hint in profile["summary"]
    assert summary_hint in compiled["summary"]
    assert compiled["validation_status"] == "valid"
    assert required_skill in skill_policy["allowed_skills"]


@pytest.mark.parametrize(
    ("member_id", "expected_terms"),
    [
        ("mem_jihan", ["架构师", "方案结论"]),
        ("mem_suyin", ["产品经理", "用户场景"]),
        ("mem_anan", ["家庭管家"]),
    ],
)
def test_direct_seeded_agents_prompt_follow_persona(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    member_id: str,
    expected_terms: list[str],
) -> None:
    captured_systems: list[str] = []

    async def fake_stream(
        self: Any,
        request: ModelChatRequest,
        cancel_token: CancelToken,
    ):
        del self, cancel_token
        captured_systems.append(
            "\n".join(
                message["content"]
                for message in request.messages
                if message["role"] == "system"
            )
        )
        yield ModelStreamEvent(event="started")
        yield ModelStreamEvent(event="delta", text=f"{member_id} ready")
        yield ModelStreamEvent(event="completed", usage={"output_tokens": 8})

    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", fake_stream)
    brain_id = _create_local_brain(client, member_id)
    bind = client.patch(f"/api/members/{member_id}/default-brain", json={"brain_id": brain_id})
    assert bind.status_code == 200, bind.text

    turn = client.post(
        "/api/chat/turn",
        json={
            "member_id": member_id,
            "session_id": f"{member_id}-direct-seed",
            "input": {"type": "text", "text": "简单介绍一下你会怎么帮我。"},
        },
    ).json()
    stream = client.get(turn["stream_url"])
    events = _parse_sse(stream.text)

    assert stream.status_code == 200, stream.text
    assert captured_systems
    assert "# SOUL" in captured_systems[0]
    assert events
    for term in expected_terms:
        assert term in captured_systems[0]


def test_direct_seeded_agents_registry_matches_bootstrap_constant(client: TestClient) -> None:
    member_ids = {item["member_id"] for item in client.get("/api/members").json()["items"]}

    assert {str(item["member_id"]) for item in DIRECT_MEMBER_SEEDS}.issubset(member_ids)


def _create_local_brain(client: TestClient, member_id: str) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": f"{member_id} direct seed brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "direct-seed-test-model",
            "is_local": True,
            "context_window": 4096,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["brain_id"])


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
