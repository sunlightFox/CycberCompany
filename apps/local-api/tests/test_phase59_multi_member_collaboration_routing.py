from __future__ import annotations

import json

from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase59_route_preview_plan_replay_and_redaction(client: TestClient) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase59")
    assert migration_contract["required_migration"] == (
        "045_multi_member_collaboration_routing_deepening.sql"
    )

    task = _create_supervisor_task(client)
    preview = client.post(
        f"/api/tasks/{task['task_id']}/supervisor/route-preview",
        json={"resource_handle_ids": ["ah_phase59_opaque"]},
    )
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    selected = set(preview_payload["routing_decision"]["selected_member_ids"])

    assert {"mem_xiaoyao", "mem_ningning", "mem_aheng"}.issubset(selected)
    assert preview_payload["routing_decision"]["boundary_summary"]["resource_handle_count"] == 1
    assert preview_payload["context_boundaries"]
    assert all(
        "other_members_private_memory" in item["excluded_context"]
        for item in preview_payload["context_boundaries"]
    )

    plan = client.post(f"/api/tasks/{task['task_id']}/supervisor/plan")
    assert plan.status_code == 200, plan.text
    replay = client.get(f"/api/tasks/{task['task_id']}/collaboration-replay").json()
    task_replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    decisions = client.get(f"/api/tasks/{task['task_id']}/routing-decisions").json()["items"]
    boundaries = client.get(f"/api/tasks/{task['task_id']}/context-boundaries").json()["items"]

    assert any(item["status"] == "previewed" for item in decisions)
    assert any(item["status"] == "planned" for item in decisions)
    assert replay["routing_decisions"]
    assert replay["context_boundaries"]
    assert task_replay["routing_decisions"]
    assert task_replay["context_boundaries"]
    assert len(boundaries) >= len(plan.json()["participants"])

    serialized = json.dumps(
        {
            "preview": preview_payload,
            "replay": replay,
            "task_replay": task_replay,
            "decisions": decisions,
            "boundaries": boundaries,
        },
        ensure_ascii=False,
    )
    assert "C:/Users/Administrator/private" not in serialized
    assert "phase59-secret-token" not in serialized
    assert "phase59-cookie" not in serialized


def test_phase59_handoff_fail_closed_and_release_contracts(client: TestClient) -> None:
    task = _create_supervisor_task(client)
    plan = client.post(f"/api/tasks/{task['task_id']}/supervisor/plan")
    assert plan.status_code == 200, plan.text
    subtasks = client.get(f"/api/tasks/{task['task_id']}/subtasks").json()["items"]
    source_subtask = next(item for item in subtasks if item["assigned_member_id"] == "mem_aheng")

    handoff = client.post(
        f"/api/tasks/{task['task_id']}/subtasks/{source_subtask['subtask_id']}/handoff",
        json={
            "to_member_id": "mem_mobai",
            "reason": "交给运营视角，token=phase59-secret-token cookie=phase59-cookie",
        },
    )
    assert handoff.status_code == 200, handoff.text
    assert handoff.json()["assigned_member_id"] == "mem_mobai"

    records = client.get(f"/api/tasks/{task['task_id']}/handoffs").json()["items"]
    replay = client.get(f"/api/tasks/{task['task_id']}/collaboration-replay").json()
    assert records
    assert records[0]["from_member_id"] == "mem_aheng"
    assert records[0]["to_member_id"] == "mem_mobai"
    assert replay["handoff_records"]
    assert "phase59-secret-token" not in json.dumps(records, ensure_ascii=False)
    assert "phase59-cookie" not in json.dumps(replay, ensure_ascii=False)

    client.patch(
        "/api/members/mem_xiaoqi/availability",
        json={"status": "unavailable", "unavailable_reason": "phase59 regression"},
    )
    blocked = client.post(
        f"/api/tasks/{task['task_id']}/subtasks/{source_subtask['subtask_id']}/handoff",
        json={"to_member_id": "mem_xiaoqi", "reason": "should fail closed"},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PARTICIPANT_UNAVAILABLE"

    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    suites = client.get("/api/evals/suites").json()["items"]
    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]

    assert "suite_phase59_multi_member_collaboration_routing" in {
        item["suite_id"] for item in suites
    }
    for name in [
        "SupervisorRoutingPreview",
        "SupervisorTaskHandoff",
        "CollaborationBoundaryIsolation",
        "CollaborationReplayTraceability",
    ]:
        assert by_name[name]["status"] == "implemented"
    assert completed["status"] == "ready_for_release"
    phase59 = report["summary"]["phase59_multi_member_collaboration_routing"]
    assert phase59["suite_id"] == "suite_phase59_multi_member_collaboration_routing"
    assert phase59["registered_cases"] >= 8
    assert report["summary"]["phase23"]["capability_scores"]["phase59"]["registered"] is True
    assert any(
        item["source_type"] == "phase59_multi_member_collaboration_routing"
        for item in evidence
    )


def _create_supervisor_task(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/tasks",
        json={
            "goal": "请产品和技术共同制定一个上线方案",
            "auto_start": False,
        },
    )
    assert response.status_code == 200, response.text
    task = response.json()
    assert task["mode"] == "supervisor"
    return task
