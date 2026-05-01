from __future__ import annotations

import json
from typing import Any, cast

import anyio
from app.services.media import FakeMediaRuntime
from core_types import TaskArtifact
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase43_import_probe_and_derivatives_with_fake_backend(client: TestClient) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase43")
    assert migration_contract["required_migration"] == "031_media_runtime.sql"
    registry = cast(Any, client.app).state.registry
    registry.media_service.set_runtime(FakeMediaRuntime())
    task = _create_task(client)
    artifact = anyio.run(_write_video_artifact, registry, task["task_id"])

    imported = client.post(
        "/api/media/import-artifact",
        json={
            "task_id": task["task_id"],
            "artifact_id": artifact["artifact_id"],
            "metadata": {
                "operator_note": "token=phase43-secret-token",
                "local_path": "c:\\users\\administrator\\phase43\\raw.mp4",
            },
        },
    )
    assert imported.status_code == 200, imported.text
    media = imported.json()["media"]
    assert media["media_type"] == "video"
    assert media["source_artifact_id"] == artifact["artifact_id"]

    probe = client.post(f"/api/media/{media['media_id']}/probe", json={})
    assert probe.status_code == 200, probe.text
    assert probe.json()["status"] == "completed"
    assert probe.json()["media"]["duration_ms"] == 42000

    frames = client.post(
        f"/api/media/{media['media_id']}/extract-frames",
        json={"mode": "interval", "interval_ms": 10000, "max_frames": 2},
    )
    assert frames.status_code == 200, frames.text
    frame_payload = frames.json()
    assert frame_payload["status"] == "completed"
    assert len(frame_payload["derivatives"]) == 2
    assert all(item["derivative_type"] == "frame" for item in frame_payload["derivatives"])

    audio = client.post(
        f"/api/media/{media['media_id']}/extract-audio",
        json={"output_format": "wav"},
    )
    assert audio.status_code == 200, audio.text
    assert audio.json()["derivatives"][0]["derivative_type"] == "audio"

    scene = client.post(
        f"/api/media/{media['media_id']}/scene-detect",
        json={"threshold": 0.3, "max_segments": 3},
    )
    assert scene.status_code == 200, scene.text
    assert scene.json()["analysis"]["analysis_type"] == "scene"

    timeline = client.post(f"/api/media/{media['media_id']}/timeline", json={})
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["analysis"]["analysis_type"] == "timeline"

    derivatives = client.get(f"/api/media/{media['media_id']}/derivatives").json()["items"]
    assert {item["derivative_type"] for item in derivatives} >= {"frame", "audio"}
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    assert replay["media_evidence"]
    media_evidence = replay["media_evidence"][0]
    assert media_evidence["source_boundary"] == "task_artifact_only"
    assert media_evidence["raw_media_content_included"] is False
    assert media_evidence["derivatives"]
    assert media_evidence["analysis"]
    assert _payload_leakage_count(
        {
            "imported": imported.json(),
            "frames": frame_payload,
            "timeline": timeline.json(),
            "replay": replay,
        }
    ) == 0


def test_phase43_backend_unavailable_is_degraded_not_fake_success(client: TestClient) -> None:
    task = _create_task(client)
    registry = cast(Any, client.app).state.registry
    artifact = anyio.run(_write_video_artifact, registry, task["task_id"])
    media = client.post(
        "/api/media/import-artifact",
        json={"task_id": task["task_id"], "artifact_id": artifact["artifact_id"]},
    ).json()["media"]

    probe = client.post(f"/api/media/{media['media_id']}/probe", json={})
    assert probe.status_code == 200, probe.text
    assert probe.json()["status"] in {"completed", "degraded"}
    if probe.json()["status"] == "degraded":
        assert probe.json()["degraded_reason"]
        assert "backend_status" in probe.json()["evidence"]


def test_phase43_source_artifact_checksum_mismatch_is_fail_closed(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    registry.media_service.set_runtime(FakeMediaRuntime())
    task = _create_task(client)
    artifact = anyio.run(_write_video_artifact, registry, task["task_id"])
    media = client.post(
        "/api/media/import-artifact",
        json={"task_id": task["task_id"], "artifact_id": artifact["artifact_id"]},
    ).json()["media"]

    source_path = registry.artifact_store.path_for_artifact(TaskArtifact(**artifact))
    source_path.write_bytes(b"tampered phase43 media source token=phase43-secret-token")

    probe = client.post(f"/api/media/{media['media_id']}/probe", json={})
    assert probe.status_code == 409, probe.text
    payload = probe.json()
    assert payload["error"]["code"] == "MEDIA_PLAN_INVALID"
    assert payload["error"]["details"]["media_id"] == media["media_id"]
    assert payload["error"]["details"]["source_artifact_id"] == artifact["artifact_id"]
    assert payload["error"]["details"]["expected_checksum"] == media["checksum"]
    assert payload["error"]["details"]["actual_checksum"].startswith("sha256:")
    assert _payload_leakage_count(payload) == 0


def test_phase43_render_requires_approval_and_invalid_edl_is_rejected(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.media_service.set_runtime(FakeMediaRuntime())
    task = _create_task(client)
    artifact = anyio.run(_write_video_artifact, registry, task["task_id"])
    media = client.post(
        "/api/media/import-artifact",
        json={"task_id": task["task_id"], "artifact_id": artifact["artifact_id"]},
    ).json()["media"]
    client.post(f"/api/media/{media['media_id']}/probe", json={})

    invalid = client.post(
        f"/api/media/{media['media_id']}/edit-plans",
        json={
            "goal": "非法剪辑",
            "operations": [{"type": "trim", "source_start_ms": 10000, "source_end_ms": 1000}],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "MEDIA_PLAN_INVALID"

    plan = client.post(
        f"/api/media/{media['media_id']}/edit-plans",
        json={
            "goal": "剪出前 5 秒，不要上传外部平台",
            "operations": [{"type": "trim", "source_start_ms": 0, "source_end_ms": 5000}],
        },
    )
    assert plan.status_code == 200, plan.text
    edit_plan = plan.json()["edit_plan"]
    assert edit_plan["status"] == "planned"

    first_render = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "media.render_edit",
            "args": {"edit_plan_id": edit_plan["edit_plan_id"]},
        },
    )
    assert first_render.status_code == 200, first_render.text
    pending = first_render.json()
    assert pending["tool_call"]["status"] == "approval_required"
    assert pending["approval"]["risk_level"] == "R3"

    approved = client.post(
        f"/api/approvals/{pending['approval']['approval_id']}/approve",
        json={"reason": "phase43 render approval"},
    )
    assert approved.status_code == 200, approved.text
    rendered = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "media.render_edit",
            "approval_id": pending["approval"]["approval_id"],
            "args": {"edit_plan_id": edit_plan["edit_plan_id"]},
        },
    )
    assert rendered.status_code == 200, rendered.text
    rendered_payload = rendered.json()
    assert rendered_payload["tool_call"]["status"] == "completed"
    assert rendered_payload["artifacts"][0]["artifact_type"] == "video"
    assert rendered_payload["result"]["edit_plan"]["status"] == "rendered"
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    assert any(call["tool_name"] == "media.render_edit" for call in replay["tool_calls"])
    assert _payload_leakage_count({"rendered": rendered_payload, "replay": replay}) == 0


def test_phase43_tool_import_rejects_cross_task_artifact(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    task_a = _create_task(client)
    task_b = _create_task(client, goal="phase43 other task")
    artifact = anyio.run(_write_video_artifact, registry, task_a["task_id"])
    denied = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_b["task_id"],
            "tool_name": "media.import_artifact",
            "args": {"task_id": task_b["task_id"], "artifact_id": artifact["artifact_id"]},
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "TOOL_PERMISSION_DENIED"


def test_phase43_release_contracts_eval_report_and_diagnostic(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    for name in [
        "MediaArtifactRegistry",
        "MediaRuntimeBackend",
        "MediaProbeTool",
        "MediaTimelineAnalysis",
        "MediaEditPlanService",
        "MediaRenderApprovalBinding",
        "MediaReplayEvidence",
    ]:
        assert by_name[name]["status"] == "implemented"

    run = client.post("/api/evals/runs", json={"suite_id": "suite_phase43_media_runtime"})
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 9

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))

    phase43 = report["summary"]["phase43"]
    assert phase43["suite_id"] == "suite_phase43_media_runtime"
    assert phase43["registered_cases"] == 9
    assert phase43["tables"]["media_assets"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase43"]["registered"] is True
    assert "phase43" in diagnostic
    assert "phase43_media_runtime" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_task(client: TestClient, *, goal: str = "phase43 media task") -> dict[str, Any]:
    response = client.post("/api/tasks", json={"goal": goal, "auto_start": False})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _write_video_artifact(registry: Any, task_id: str) -> dict[str, Any]:
    artifact = await registry.artifact_store.write_bytes(
        task_id=task_id,
        organization_id="org_default",
        display_name="phase43-video.mp4",
        content=b"\x00\x00\x00 ftypisomphase43-video token=phase43-secret-token",
        artifact_type="video",
        content_type="video/mp4",
        subdir="inputs",
        sensitivity="medium",
        metadata={
            "fixture": "phase43",
            "local_path": "c:\\users\\administrator\\phase43\\video.mp4",
        },
    )
    return artifact.model_dump(mode="json")


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "phase43-secret-token",
        "token=phase43",
        "cookie=phase43",
        "private_key=phase43",
        "mnemonic=phase43",
        "c:\\users\\administrator\\phase43",
    ]
    return sum(1 for item in forbidden if item in serialized)
