from __future__ import annotations

from typing import Any, cast

import anyio
from app.core.time import new_id, utc_now_iso
from app.services.voice import EdgeVoiceProvider, HailuoVoiceProvider
from fastapi.testclient import TestClient


def test_voice_reply_runtime_routes_edge_and_hailuo_and_persists_audio_fields(
    client: TestClient,
    monkeypatch,
) -> None:
    registry = cast(Any, client.app).state.registry
    default_profiles = client.get("/api/voice/profiles").json()["items"]
    assert any(item["provider"] == "edge" for item in default_profiles)
    edge_profile = _create_voice_profile(
        client,
        display_name="Edge Default",
        provider="edge",
        provider_voice_id="zh-CN-XiaoxiaoNeural",
    )
    _create_voice_binding(client, edge_profile["voice_profile_id"])

    async def _fake_edge_render(self, request):  # type: ignore[no-untyped-def]
        return b"edge-audio", {"backend": "edge", "voice": request.voice_profile["provider_voice_id"]}

    monkeypatch.setattr(EdgeVoiceProvider, "render", _fake_edge_render)

    edge_trace_id = anyio.run(_start_trace, registry)
    edge_result = anyio.run(
        _render_voice_reply,
        registry,
        edge_trace_id,
        "请用声音回复我。",
        "先说结论，可以。然后再补一句说明，别太快。",
        {},
        "R1",
    )
    anyio.run(_attach_voice_message, registry, edge_result.render_job["render_job_id"], edge_trace_id)
    anyio.run(_end_trace, registry, edge_trace_id)

    assert edge_result.voice_reply["requested"] is True
    assert edge_result.voice_reply["should_render"] is True
    assert edge_result.voice_reply["provider"] == "edge"
    assert edge_result.voice_reply["voice_profile_id"] == edge_profile["voice_profile_id"]
    assert edge_result.voice_reply["audio_uri"].startswith("voice://")
    assert edge_result.render_job["status"] == "completed"
    assert edge_result.render_job["output_content_type"] == "audio/wav"

    hailuo_profile = _create_voice_profile(
        client,
        display_name="Hailuo Mandarin",
        provider="hailuo_ai",
        provider_voice_id="hailuo_voice_01",
        output_format="mp3",
        config={"endpoint": "https://example.invalid/tts"},
        secret="hailuo-secret",
    )
    assert hailuo_profile["has_secret"] is True
    assert "secret_ref" not in hailuo_profile
    _create_voice_binding(client, hailuo_profile["voice_profile_id"])

    async def _fake_hailuo_render(self, request):  # type: ignore[no-untyped-def]
        return b"hailuo-audio", {
            "backend": "hailuo_ai",
            "voice": request.voice_profile["provider_voice_id"],
        }

    monkeypatch.setattr(HailuoVoiceProvider, "render", _fake_hailuo_render)

    hailuo_trace_id = anyio.run(_start_trace, registry)
    hailuo_result = anyio.run(
        _render_voice_reply,
        registry,
        hailuo_trace_id,
        "请用声音回复我。",
        "这次换个更慢一点的节奏，先给结论，再补说明。",
        {},
        "R1",
    )
    anyio.run(
        _attach_voice_message,
        registry,
        hailuo_result.render_job["render_job_id"],
        hailuo_trace_id,
    )
    anyio.run(_end_trace, registry, hailuo_trace_id)

    assert hailuo_result.voice_reply["provider"] == "hailuo_ai"
    assert hailuo_result.voice_reply["output_format"] == "mp3"
    assert hailuo_result.render_job["output_content_type"] == "audio/mpeg"
    assert hailuo_result.voice_reply["voice_style_plan"]["segments"]
    assert hailuo_result.voice_reply["voice_style_plan"]["voice_name"] == "Hailuo Mandarin"

    preview = client.post(
        "/api/voice/render-preview",
        json={
            "member_id": "mem_xiaoyao",
            "text": "请用声音回复我。",
            "voice_profile_id": hailuo_profile["voice_profile_id"],
            "response_plan": {"structured_payload": {"voice_reply": {"requested": True}}},
        },
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["voice_reply"]["voice_profile_id"] == hailuo_profile["voice_profile_id"]
    assert preview_body["voice_reply"]["provider"] == "hailuo_ai"

    message_id = new_id("msg")
    anyio.run(_insert_voice_message, registry, message_id, edge_trace_id, edge_result)
    persisted = anyio.run(registry.chat.get_message, message_id)
    assert persisted is not None
    assert persisted["audio_uri"] == edge_result.voice_reply["audio_uri"]
    assert persisted["voice_metadata"]["provider"] == "edge"
    assert persisted["voice_metadata"]["voice_style_plan"]["segments"]

    trace = client.get(f"/api/traces/{edge_trace_id}").json()
    span_types = [span["span_type"] for span in trace["spans"]]
    assert "voice.render" in span_types
    assert "voice.attach" in span_types
    assert all("secret" not in str(span).lower() for span in trace["spans"])


def test_voice_reply_runtime_blocks_high_risk_audio_and_degrades(
    client: TestClient,
    monkeypatch,
) -> None:
    registry = cast(Any, client.app).state.registry
    profile = _create_voice_profile(
        client,
        display_name="Guarded Voice",
        provider="edge",
        provider_voice_id="zh-CN-YunxiNeural",
    )
    _create_voice_binding(client, profile["voice_profile_id"])

    async def _fail_render(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("should not render high-risk voice")

    monkeypatch.setattr(EdgeVoiceProvider, "render", _fail_render)

    trace_id = anyio.run(_start_trace, registry)
    result = anyio.run(
        _render_voice_reply,
        registry,
        trace_id,
        "请用声音回复我。",
        "这条内容不应该真的被播出来。",
        {"safety_notice": "high risk"},
        "R5",
    )
    anyio.run(_end_trace, registry, trace_id)

    assert result.voice_reply["requested"] is True
    assert result.voice_reply["should_render"] is False
    assert result.voice_reply["reason"] == "high_risk_voice_blocked"
    assert result.render_job == {}


async def _start_trace(registry):  # type: ignore[no-untyped-def]
    return await registry.trace_service.start_trace()


async def _end_trace(registry, trace_id: str) -> None:  # type: ignore[no-untyped-def]
    await registry.trace_service.end_trace(trace_id)


async def _render_voice_reply(
    registry,
    trace_id: str,
    user_text: str,
    assistant_text: str,
    response_plan: dict[str, Any],
    risk_level: str,
):  # type: ignore[no-untyped-def]
    return await registry.voice_service.render_voice_reply(
        turn={
            "organization_id": "org_default",
            "member_id": "mem_xiaoyao",
            "conversation_id": "conv_default_xiaoyao",
            "turn_id": new_id("turn"),
        },
        user_text=user_text,
        assistant_text=assistant_text,
        response_plan=response_plan,
        persona={
            "default_mode": "warm",
            "tone_policy": {
                "conciseness": 0.88,
                "warmth": 0.42,
                "directness": 0.75,
            },
            "style_principles": ["先给结论", "语气自然"],
        },
        heart={
            "preferred_pace": "slow_and_clear",
            "mood": "steady",
        },
        risk_level=risk_level,
        trace_id=trace_id,
    )


async def _attach_voice_message(registry, render_job_id: str, trace_id: str) -> None:  # type: ignore[no-untyped-def]
    await registry.voice_service.attach_message(
        render_job_id=render_job_id,
        message_id=new_id("msg"),
        trace_id=trace_id,
    )


async def _insert_voice_message(
    registry,
    message_id: str,
    trace_id: str,
    result,
):  # type: ignore[no-untyped-def]
    await registry.chat.insert_message(
        message_id=message_id,
        conversation_id="conv_default_xiaoyao",
        turn_id=None,
        author_type="assistant",
        author_id="mem_xiaoyao",
        content_type="audio",
        content_text="先说结论，可以。然后再补一句说明，别太快。",
        content={
            "type": "audio",
            "text": "先说结论，可以。然后再补一句说明，别太快。",
            "voice_reply": result.voice_reply,
        },
        trace_id=trace_id,
        voice_profile_id=result.voice_reply["voice_profile_id"],
        voice_render_job_id=result.render_job["render_job_id"],
        audio_uri=result.voice_reply["audio_uri"],
        audio_content_type=result.voice_reply["audio_content_type"],
        voice_metadata=result.voice_reply,
        created_at=utc_now_iso(),
    )


def _create_voice_profile(
    client: TestClient,
    *,
    display_name: str,
    provider: str,
    provider_voice_id: str,
    output_format: str = "wav",
    config: dict[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/voice/profiles",
        json={
            "display_name": display_name,
            "provider": provider,
            "provider_voice_id": provider_voice_id,
            "output_format": output_format,
            "config": config or {},
            "secret": secret,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create_voice_binding(client: TestClient, voice_profile_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/voice/bindings",
        json={
            "member_id": "mem_xiaoyao",
            "voice_profile_id": voice_profile_id,
            "binding_scope": "default",
            "reply_mode": "explicit_request_only",
            "priority": 10,
            "status": "active",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()
