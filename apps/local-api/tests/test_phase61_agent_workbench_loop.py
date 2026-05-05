from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase61_turn_reflection_worker_creates_context_file_and_pack(
    client: TestClient,
) -> None:
    registry = client.app.state.registry
    _run_async(
        client,
        _create_completed_turn,
        registry,
        "turn_phase61_workbench_a",
        "msg_phase61_workbench_a",
        "我希望以后回复先给结论再展开，整理报告时按 migration repository service API tests 顺序。",
    )

    queued = client.post("/api/agent-workbench/turns/turn_phase61_workbench_a/reflect")
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "queued"
    assert queued.json()["job"]["status"] == "pending"

    tick = client.post(
        "/api/system/background-workers/tick",
        params={"worker_name": "agent_workbench_reflection_worker"},
    )
    assert tick.status_code == 200, tick.text
    result = tick.json()["results"]["agent_workbench_reflection_worker"]
    assert result["status"] == "healthy"
    assert result["agent_workbench_jobs_processed"] >= 1

    jobs = client.get(
        "/api/agent-workbench/reflection-jobs",
        params={"job_type": "reflect_after_turn"},
    ).json()["items"]
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["turn_id"] == "turn_phase61_workbench_a"

    files = client.get(
        "/api/agent-workbench/context-files",
        params={"member_id": "mem_xiaoyao", "conversation_id": "conv_default_xiaoyao"},
    ).json()["items"]
    assert files
    version = files[0]
    assert version["source_turn_id"] == "turn_phase61_workbench_a"
    assert version["artifact_checksum"].startswith("sha256:")
    assert version["memory_refs"] or version["source_refs"]

    replay = client.get(
        f"/api/agent-workbench/context-files/{version['version_id']}/replay"
    )
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()["replay"]
    assert replay_body["artifact_exists"] is True
    assert replay_body["checksum_matches"] is True

    pack = client.get(
        "/api/agent-workbench/context-packs/latest",
        params={"member_id": "mem_xiaoyao", "conversation_id": "conv_default_xiaoyao"},
    ).json()["pack"]
    assert pack["context_file_refs"][0]["version_id"] == version["version_id"]
    assert pack["memory_refs"] or "稳定记忆" in pack["summary_text"]
    if pack["memory_refs"]:
        memory_ref = pack["memory_refs"][0]
        assert memory_ref["confidence"] is not None
        assert memory_ref["sensitivity"]
        assert memory_ref["selection_reason"]
        assert isinstance(memory_ref["source"], dict)
    if pack["skill_refs"]:
        skill_ref = pack["skill_refs"][0]
        assert skill_ref["source"]
        assert skill_ref["trust_level"]
        assert "requires_asset_broker" in skill_ref
        assert skill_ref["requires_safety"] is True

    workbench = _run_async(
        client,
        registry.agent_workbench_service.latest_workbench_context,
        member_id="mem_xiaoyao",
        conversation_id="conv_default_xiaoyao",
    )
    assert workbench.context_file_version_id == version["version_id"]
    assert workbench.summary

    artifact_path = _artifact_path(registry.config.storage.artifact_dir, version["artifact_uri"])
    serialized = artifact_path.read_text(encoding="utf-8")
    assert "token=" not in serialized.lower()
    assert "cookie" not in serialized.lower()
    assert "private-cookie" not in serialized


def test_phase61_diff_replay_and_growth_evidence_are_redacted(
    client: TestClient,
) -> None:
    registry = client.app.state.registry
    _run_async(
        client,
        _create_completed_turn,
        registry,
        "turn_phase61_workbench_b1",
        "msg_phase61_workbench_b1",
        "以后复盘时保留证据链，但不要暴露 token=phase61-secret 或 C:/Users/Admin/private-cookie.txt。",
    )
    _run_async(
        client,
        _create_completed_turn,
        registry,
        "turn_phase61_workbench_b2",
        "msg_phase61_workbench_b2",
        "继续沿用这个工作台方法，并把 skill 候选保持 pending review。",
    )
    for turn_id in ["turn_phase61_workbench_b1", "turn_phase61_workbench_b2"]:
        response = client.post(
            f"/api/agent-workbench/turns/{turn_id}/reflect",
            json={"mode": "immediate"},
        )
        assert response.status_code == 200, response.text

    files = client.get(
        "/api/agent-workbench/context-files",
        params={"member_id": "mem_xiaoyao", "conversation_id": "conv_default_xiaoyao"},
    ).json()["items"]
    assert len(files) >= 2
    newer, older = files[0], files[1]

    diff = client.get(
        "/api/agent-workbench/context-files/diff",
        params={
            "from_version_id": older["version_id"],
            "to_version_id": newer["version_id"],
        },
    )
    assert diff.status_code == 200, diff.text
    replay = client.get(
        f"/api/agent-workbench/context-files/{newer['version_id']}/replay"
    )
    assert replay.status_code == 200, replay.text

    growth = client.get("/api/skills/growth-candidates", params={"limit": 20})
    assert growth.status_code == 200, growth.text
    assert any(item["experience_id"] for item in growth.json()["items"])

    candidates = client.get("/api/skills/candidates", params={"status": "pending_review"})
    assert candidates.status_code == 200, candidates.text
    assert any(item["status"] == "pending_review" for item in candidates.json()["items"])

    serialized = json.dumps(
        {"diff": diff.json(), "replay": replay.json(), "growth": growth.json()},
        ensure_ascii=False,
    )
    assert "phase61-secret" not in serialized
    assert "private-cookie" not in serialized
    assert "C:/Users" not in serialized


def test_phase61_release_contracts_and_eval_suite(client: TestClient) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase61")
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    suites = client.get("/api/evals/suites").json()["items"]

    assert migration_contract["required_migration"] == "046_agent_workbench_context_files.sql"
    assert "suite_phase61_agent_workbench_loop" in {item["suite_id"] for item in suites}
    for module in [
        "AgentWorkbenchContextPack",
        "ContextFileVersioning",
        "WorkbenchReflectionWorker",
        "MemorySkillContextRoundTrip",
    ]:
        assert by_name[module]["status"] == "implemented"

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase61_agent_workbench_loop"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 7


async def _create_completed_turn(
    registry: Any,
    turn_id: str,
    message_id: str,
    text: str,
) -> None:
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


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    portal = getattr(client, "portal", None)
    if portal is not None:
        async def portal_runner() -> Any:
            return await func(*args, **kwargs)

        return portal.call(portal_runner)

    async def runner() -> Any:
        return await func(*args, **kwargs)

    return anyio.run(runner)


def _artifact_path(root: Path, uri: str) -> Path:
    prefix = "artifact://agent-workbench/"
    assert uri.startswith(prefix)
    return root / "agent-workbench" / Path(*uri.removeprefix(prefix).split("/"))
