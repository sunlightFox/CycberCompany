from __future__ import annotations

import io
import json
import wave
from typing import Any, cast

import anyio
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase58_media_io_endpoints_tooling_binding_and_redaction(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase58")
    assert migration_contract["required_migration"] == "043_media_multimodal_io_foundation.sql"

    registry = cast(Any, client.app).state.registry
    task = _create_task(client, goal="phase58 multimodal io task")

    audio_media = _import_media(
        client,
        registry,
        task["task_id"],
        display_name="phase58-audio.wav",
        content=_fake_wav_bytes(),
        content_type="audio/wav",
        media_type="audio",
        metadata={"note": "audio source token=phase58-secret-token"},
    )
    image_media = _import_media(
        client,
        registry,
        task["task_id"],
        display_name="phase58-image.png",
        content=_fake_png_bytes(),
        content_type="image/png",
        media_type="image",
        metadata={"note": "image source token=phase58-secret-token"},
    )
    video_media = _import_media(
        client,
        registry,
        task["task_id"],
        display_name="phase58-video.mp4",
        content=b"\x00\x00\x00 ftypisomphase58-video",
        content_type="video/mp4",
        media_type="video",
        metadata={"note": "video source token=phase58-secret-token"},
    )
    document_media = _import_media(
        client,
        registry,
        task["task_id"],
        display_name="phase58-doc.txt",
        content=b"phase58 document body token=phase58-secret-token",
        content_type="text/plain; charset=utf-8",
        media_type="document",
        metadata={"note": "document source token=phase58-secret-token"},
    )

    stt_first = _execute_tool(
        client,
        task["task_id"],
        "media.stt",
        {"media_id": audio_media["media_id"], "provider": "local", "language": "zh-CN"},
    )
    assert stt_first["tool_call"]["status"] in {"completed", "completed_with_warning", "degraded"}
    assert stt_first["result"]["io_records"]
    assert stt_first["result"]["transcripts"]
    stt_io_request_id = stt_first["result"]["evidence"]["io_request_id"]

    stt_second = _execute_tool(
        client,
        task["task_id"],
        "media.stt",
        {"media_id": audio_media["media_id"], "provider": "local", "language": "zh-CN"},
    )
    assert stt_second["result"]["evidence"]["idempotent"] is True
    assert stt_second["result"]["evidence"]["io_request_id"] == stt_io_request_id

    tts = _execute_tool(
        client,
        task["task_id"],
        "media.tts",
        {
            "task_id": task["task_id"],
            "organization_id": "org_default",
            "text": "请播报 token=phase58-secret-token 和 cookie=phase58-cookie",
            "provider": "local",
            "voice": "neutral",
            "output_format": "wav",
            "sensitivity": "medium",
        },
    )
    assert tts["result"]["artifacts"]
    assert tts["result"]["renders"]
    assert "phase58-secret-token" not in json.dumps(tts, ensure_ascii=False)
    assert "phase58-cookie" not in json.dumps(tts, ensure_ascii=False)

    image_summary = _execute_tool(
        client,
        task["task_id"],
        "media.summarize",
        {"media_id": image_media["media_id"], "provider": "local", "summary_type": "image"},
    )
    assert image_summary["result"]["summaries"]
    assert image_summary["result"]["io_records"]

    video_summary = _execute_tool(
        client,
        task["task_id"],
        "media.summarize",
        {"media_id": video_media["media_id"], "provider": "local", "summary_type": "video"},
    )
    assert video_summary["result"]["summaries"]

    document_summary = _execute_tool(
        client,
        task["task_id"],
        "media.summarize",
        {
            "media_id": document_media["media_id"],
            "provider": "local",
            "summary_type": "document",
        },
    )
    assert document_summary["result"]["summaries"]
    assert "phase58-secret-token" not in json.dumps(document_summary, ensure_ascii=False)

    binding = anyio.run(
        _record_chat_binding,
        registry,
        audio_media["media_id"],
        stt_io_request_id,
    )
    assert binding.binding_type == "attachment_understanding"

    io_records = client.get(f"/api/media/{audio_media['media_id']}/io-records")
    assert io_records.status_code == 200, io_records.text
    assert io_records.json()["items"]

    provider_health = client.get("/api/media/providers/health")
    assert provider_health.status_code == 200, provider_health.text
    assert provider_health.json()["items"]

    replay = client.get(f"/api/tasks/{task['task_id']}/replay")
    assert replay.status_code == 200, replay.text
    replay_payload = replay.json()
    assert replay_payload["media_evidence"]
    assert any(item["chat_bindings"] for item in replay_payload["media_evidence"])

    leakage_payload = {
        "stt_first": stt_first,
        "stt_second": stt_second,
        "tts": tts,
        "image_summary": image_summary,
        "video_summary": video_summary,
        "document_summary": document_summary,
        "io_records": io_records.json(),
        "provider_health": provider_health.json(),
        "replay": replay_payload,
    }
    assert _payload_leakage_count(leakage_payload) == 0


def test_phase58_release_contracts_and_suite_registration(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    suites = client.get("/api/evals/suites").json()["items"]

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    diagnostic = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()

    assert "suite_phase58_multimodal_io_foundation" in {item["suite_id"] for item in suites}
    for name in [
        "MediaProviderHealthDiagnostics",
        "MediaSpeechTranscriptPipeline",
        "MediaSpeechRenderPipeline",
        "MediaMultimodalSummaryPipeline",
        "MediaChatBinding",
    ]:
        assert by_name[name]["status"] == "implemented"

    phase58 = report["summary"]["phase58_multimodal_io_foundation"]
    assert phase58["suite_id"] == "suite_phase58_multimodal_io_foundation"
    assert phase58["migration_contract"]["required_migration"] == "043_media_multimodal_io_foundation.sql"
    assert report["summary"]["phase23"]["capability_scores"]["phase58"]["registered"] is True
    assert completed["status"] == "ready_for_release"
    assert any(item["source_type"] == "phase58_multimodal_io_foundation" for item in diagnostic["items"])
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_task(client: TestClient, *, goal: str) -> dict[str, Any]:
    response = client.post("/api/tasks", json={"goal": goal, "auto_start": False})
    assert response.status_code == 200, response.text
    return dict(response.json())


def _import_media(
    client: TestClient,
    registry: Any,
    task_id: str,
    *,
    display_name: str,
    content: bytes,
    content_type: str,
    media_type: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    artifact = anyio.run(
        _write_artifact,
        registry,
        task_id,
        display_name,
        content,
        content_type,
        "file" if media_type != "document" else "text",
        metadata,
    )
    response = client.post(
        "/api/media/import-artifact",
        json={
            "task_id": task_id,
            "artifact_id": artifact.artifact_id,
            "media_type": media_type,
            "display_name": display_name,
            "metadata": metadata,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["media"])


def _execute_tool(
    client: TestClient,
    task_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "tool_name": tool_name,
            "args": args,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _fake_png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00"
        b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _fake_wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


async def _write_artifact(
    registry: Any,
    task_id: str,
    display_name: str,
    content: bytes,
    content_type: str,
    artifact_type: str,
    metadata: dict[str, Any],
) -> Any:
    return await registry.artifact_store.write_bytes(
        task_id=task_id,
        organization_id="org_default",
        display_name=display_name,
        content=content,
        artifact_type=artifact_type,
        content_type=content_type,
        subdir="inputs",
        sensitivity="medium",
        metadata=metadata,
    )


async def _record_chat_binding(
    registry: Any,
    media_id: str,
    io_request_id: str,
) -> Any:
    return await registry.media_service.record_chat_binding(
        media_id=media_id,
        io_request_id=io_request_id,
        channel="wechat",
        conversation_id="conv_phase58",
        turn_id="turn_phase58",
        message_id="msg_phase58",
        channel_event_id="evt_phase58",
        channel_attachment_id="att_phase58",
        binding_type="attachment_understanding",
        status="bound",
        evidence={"note": "chat binding token=phase58-secret-token"},
        trace_id=None,
    )


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "phase58-secret-token",
        "phase58-cookie",
        "c:\\users\\administrator\\phase58",
        "private_key=phase58",
        "cookie=phase58",
        "token=phase58",
    ]
    return sum(1 for item in forbidden if item in serialized)
