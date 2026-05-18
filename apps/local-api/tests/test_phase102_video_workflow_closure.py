from __future__ import annotations

import json
from typing import Any, cast

import anyio
from app.services.media import FakeMediaRuntime
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase102_video_workflow_approval_repair_and_replay(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase102")
    assert migration_contract["required_migration"] == "057_media_video_workflow_closure.sql"
    registry = cast(Any, client.app).state.registry
    registry.media_service.set_runtime(FakeMediaRuntime(fail_render_attempts=1))
    task = _create_task(client, goal="phase102 剪出前 5 秒视频并生成可回放交付结果")
    artifact = anyio.run(_write_video_artifact, registry, task["task_id"])

    imported = client.post(
        "/api/media/import-artifact",
        json={
            "task_id": task["task_id"],
            "artifact_id": artifact["artifact_id"],
            "metadata": {
                "operator_note": "token=phase102-secret-token",
                "local_path": "c:\\users\\administrator\\phase102\\raw.mp4",
            },
        },
    )
    assert imported.status_code == 200, imported.text
    media = imported.json()["media"]

    created = client.post(
        "/api/media/video-workflows",
        json={
            "task_id": task["task_id"],
            "media_id": media["media_id"],
            "goal": "剪出前 5 秒，生成 timeline、scene map 和最终 mp4，不要上传外部平台",
            "workflow_profile": {
                "workflow_type": "video_edit",
                "task_class": "standard",
                "require_render": True,
                "require_export": False,
                "max_frames": 2,
                "max_segments": 3,
                "render_strategy": "copy",
                "provider_capabilities": {
                    "video_generation": False,
                    "generation_provider_status": "not_configured",
                },
            },
        },
    )
    assert created.status_code == 200, created.text
    workflow = created.json()["workflow"]
    assert workflow["status"] == "planned"
    assert workflow["result"]["provider_status"]["video_generation"]["status"] == "degraded"

    pending = client.post(
        f"/api/media/video-workflows/{workflow['workflow_id']}/execute",
        json={},
    )
    assert pending.status_code == 200, pending.text
    pending_payload = pending.json()
    assert pending_payload["workflow"]["status"] == "waiting_approval"
    approval_id = pending_payload["workflow"]["approval_id"]
    assert approval_id
    assert pending_payload["workflow"]["result"]["timeline_summary"]["segment_count"] >= 1
    assert pending_payload["workflow"]["result"]["scene_map"]
    assert pending_payload["workflow"]["result"]["edit_decision_list"]

    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"reason": "phase102 render approval"},
    )
    assert approved.status_code == 200, approved.text

    resumed = client.post(
        f"/api/media/video-workflows/{workflow['workflow_id']}/resume",
        json={"approval_id": approval_id},
    )
    assert resumed.status_code == 200, resumed.text
    completed = resumed.json()
    result = completed["workflow"]["result"]
    assert completed["workflow"]["status"] == "completed"
    assert result["render_output"]["status"] == "rendered"
    assert result["render_output"]["repair_attempted"] is True
    assert result["render_output"]["strategy"] == "safe_reencode"
    assert result["deliverable"] is True

    steps = completed["steps"]
    assert any(step["step_key"] == "render_repair" for step in steps)
    assert any(
        step["step_key"] == "render_output"
        and step["attempt"] == 2
        and step["status"] == "completed"
        for step in steps
    )

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    media_evidence = replay["media_evidence"][0]
    assert media_evidence["video_workflows"]
    final_result = client.get(f"/api/tasks/{task['task_id']}").json()["result"]
    assert final_result["domain"] == "video_workflow"
    assert final_result["video_workflow"]["workflow_id"] == workflow["workflow_id"]
    assert final_result["video_workflow"]["benchmark_summary"]["passed"] >= 1
    assert replay["domain_result"] == final_result
    assert _payload_leakage_count(
        {
            "created": created.json(),
            "pending": pending_payload,
            "completed": completed,
            "replay": replay,
        }
    ) == 0


def test_phase102_degraded_provider_and_release_suite(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    for name in [
        "VideoWorkflowProfile",
        "VideoWorkflowClosure",
        "VideoWorkflowRenderRepair",
    ]:
        assert by_name[name]["status"] == "implemented"

    run = client.post("/api/evals/runs", json={"suite_id": "suite_phase102_video_workflow_closure"})
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 7

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))

    phase102 = report["summary"]["phase102_video_workflow_closure"]
    assert phase102["suite_id"] == "suite_phase102_video_workflow_closure"
    assert phase102["registered_cases"] == 7
    assert phase102["video_workflow_matrix"]["generation_provider_degraded"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase102"]["registered"] is True
    assert "phase102_video_workflow_closure" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def test_phase102_task_engine_executes_video_workflow_after_artifact_binding(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.media_service.set_runtime(FakeMediaRuntime(fail_render_attempts=0))
    task = _create_task(client, goal="phase102 通过任务引擎剪出前 5 秒视频并生成交付结果")
    artifact = anyio.run(_write_video_artifact, registry, task["task_id"])
    imported = client.post(
        "/api/media/import-artifact",
        json={"task_id": task["task_id"], "artifact_id": artifact["artifact_id"]},
    )
    assert imported.status_code == 200, imported.text

    started = client.post(f"/api/tasks/{task['task_id']}/start")
    assert started.status_code == 200, started.text
    waiting = client.get(f"/api/tasks/{task['task_id']}").json()
    assert waiting["status"] == "waiting_approval"
    step = client.get(f"/api/tasks/{task['task_id']}/replay").json()["steps"][0]
    assert step["step_type"] == "video_workflow"
    assert step["approval_id"]
    assert step["output"]["workflow_id"]

    approval_id = step["approval_id"]
    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"reason": "phase102 task-engine render approval"},
    )
    assert approved.status_code == 200, approved.text
    resumed = approved.json()
    assert resumed["status"] == "completed"
    assert resumed["result"]["domain"] == "video_workflow"
    assert resumed["result"]["deliverable"] is True
    assert resumed["result"]["video_workflow"]["deliverable"] is True
    assert resumed["result"]["video_workflow"]["approval_state"]["status"] == "resolved"

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    assert replay["domain"] == "video_workflow"
    assert replay["domain_result"]["video_workflow"]["deliverable"] is True
    workflow_replay = replay["media_evidence"][0]["video_workflows"][0]
    assert workflow_replay["workflow"]["status"] == "completed"
    assert any(
        item["scenario_key"] == "render_approval_repair" and item["status"] == "passed"
        for item in workflow_replay["benchmarks"]
    )


def _create_task(client: TestClient, *, goal: str) -> dict[str, Any]:
    response = client.post(
        "/api/tasks",
        json={
            "goal": goal,
            "auto_start": False,
            "planner_context": {
                "intent": "video_workflow_request",
                "phase": "phase102",
                "video_workflow_profile": {"workflow_type": "video_edit"},
            },
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _write_video_artifact(registry: Any, task_id: str) -> dict[str, Any]:
    artifact = await registry.artifact_store.write_bytes(
        task_id=task_id,
        organization_id="org_default",
        display_name="phase102-video.mp4",
        content=b"\x00\x00\x00 ftypisomphase102-video token=phase102-secret-token",
        artifact_type="video",
        content_type="video/mp4",
        subdir="inputs",
        sensitivity="medium",
        metadata={
            "fixture": "phase102",
            "local_path": "c:\\users\\administrator\\phase102\\video.mp4",
        },
    )
    return artifact.model_dump(mode="json")


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "phase102-secret-token",
        "token=phase102",
        "cookie=phase102",
        "private_key=phase102",
        "mnemonic=phase102",
        "c:\\users\\administrator\\phase102",
    ]
    return sum(1 for item in forbidden if item in serialized)
