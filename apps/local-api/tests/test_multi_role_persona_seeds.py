from __future__ import annotations

import json
from typing import Any

import pytest
from app.services import chat as chat_module
from brain.adapters import CancelToken, ModelChatRequest, ModelStreamEvent
from fastapi.testclient import TestClient


@pytest.mark.parametrize(
    ("member_id", "profile_id", "summary_hint", "catchphrase", "required_skill"),
    [
        (
            "mem_aheng",
            "direct_professional",
            "架构师",
            "先给方案",
            "architecture_design",
        ),
        (
            "mem_ningning",
            "structured_ux_sensitive",
            "产品经理",
            "先对齐目标",
            "product_design",
        ),
        (
            "mem_xiaoqi",
            "gentle_careful",
            "家庭管家",
            "先别急",
            "daily_planning",
        ),
    ],
)
def test_multi_role_personas_have_distinct_profiles_and_skills(
    client: TestClient,
    member_id: str,
    profile_id: str,
    summary_hint: str,
    catchphrase: str,
    required_skill: str,
) -> None:
    members = {item["member_id"]: item for item in client.get("/api/members").json()["items"]}
    profile = client.get(f"/api/persona/profiles/{profile_id}").json()
    compiled = client.get(f"/api/persona/{member_id}/soul/compiled").json()
    skill_policy = client.get(f"/api/members/{member_id}/skill-policies").json()

    assert member_id in members
    assert members[member_id]["persona_profile_id"] == profile_id
    assert summary_hint in profile["summary"]
    assert summary_hint in compiled["summary"]
    assert compiled["validation_status"] == "valid"
    assert catchphrase in compiled["catchphrases"]
    assert required_skill in skill_policy["allowed_skills"]

    if member_id == "mem_aheng":
        assert profile["default_mode"] == "concise"
        assert profile["tone_policy"]["technical_depth"] >= 0.9
        assert profile["tone_policy"]["directness"] >= 0.85
    elif member_id == "mem_ningning":
        assert profile["tone_policy"]["warmth"] >= 0.7
        assert profile["tone_policy"]["proactiveness"] >= 0.7
        assert any(
            "clarify_user_goal_scenario_and_acceptance_criteria" in item
            for item in compiled["style_principles"]
        )
    elif member_id == "mem_xiaoqi":
        assert profile["tone_policy"]["warmth"] >= 0.88
        assert profile["tone_policy"]["technical_depth"] <= 0.4
        assert any("stabilize_the_users_pace" in item for item in compiled["style_principles"])


@pytest.mark.parametrize(
    ("member_id", "profile_id", "expected_terms"),
    [
        ("mem_aheng", "direct_professional", ["像干脆的架构师一样说话", "先给方案结论"]),
        ("mem_ningning", "structured_ux_sensitive", ["像清晰的产品经理一样沟通", "先对齐目标和用户场景"]),
        ("mem_xiaoqi", "gentle_careful", ["像细心的家庭管家一样接话", "我们一步一步来"]),
    ],
)
def test_multi_role_persona_prompts_include_role_specific_soul(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    member_id: str,
    profile_id: str,
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
        yield ModelStreamEvent(event="delta", text=f"{profile_id} ready")
        yield ModelStreamEvent(event="completed", usage={"output_tokens": 8})

    monkeypatch.setattr(chat_module.OpenAICompatibleClient, "stream_chat", fake_stream)
    brain_id = _create_local_brain(client, member_id)
    bind = client.patch(f"/api/members/{member_id}/default-brain", json={"brain_id": brain_id})
    assert bind.status_code == 200, bind.text

    turn = client.post(
        "/api/chat/turn",
        json={
            "member_id": member_id,
            "session_id": f"{member_id}-persona-role",
            "input": {"type": "text", "text": "先简单介绍一下你会怎么帮我。"},
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


def _create_local_brain(client: TestClient, member_id: str) -> str:
    response = client.post(
        "/api/brains",
        json={
            "display_name": f"{member_id} persona seed brain",
            "provider": "openai_compatible",
            "endpoint": "http://127.0.0.1:65531",
            "model_name": "persona-seed-test-model",
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
