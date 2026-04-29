from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_phase7_supervisor_task_runs_with_explainable_participants(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "请产品和技术共同制定一个上线方案",
            "auto_start": True,
        },
    ).json()
    participants = client.get(f"/api/tasks/{task['task_id']}/participants").json()["items"]
    subtasks = client.get(f"/api/tasks/{task['task_id']}/subtasks").json()["items"]
    replay = client.get(f"/api/tasks/{task['task_id']}/collaboration-replay").json()
    replay_text = json.dumps(replay, ensure_ascii=False)

    assert task["mode"] == "supervisor"
    assert task["status"] == "completed"
    assert task["host_member_id"] == "mem_xiaoyao"
    assert {"mem_xiaoyao", "mem_ningning", "mem_aheng"}.issubset(
        {item["member_id"] for item in participants}
    )
    assert all(item["selection_reason"] for item in participants)
    assert all(item["assigned_member_id"] for item in subtasks)
    assert all(
        "other_members_private_memory" in item["context_scope"]["excluded_context"]
        for item in participants
    )
    assert replay["collaboration_plan"]["host_member_id"] == "mem_xiaoyao"
    assert replay["rounds"]
    assert replay["outputs"]
    assert replay["host_decisions"][0]["source_refs"]
    assert "private_key" not in replay_text
    assert "plain-secret" not in replay_text


def test_phase7_simple_task_stays_workflow(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "写一份普通任务报告", "auto_start": True},
    ).json()

    assert task["mode"] == "workflow"
    assert task["host_member_id"] is None


def test_phase7_unavailable_member_is_not_selected(client: TestClient) -> None:
    client.patch(
        "/api/members/mem_aheng/availability",
        json={"status": "unavailable", "unavailable_reason": "休息"},
    )
    task = client.post(
        "/api/tasks",
        json={
            "goal": "请产品和技术共同制定一个上线方案",
            "auto_start": False,
        },
    ).json()
    plan = client.post(f"/api/tasks/{task['task_id']}/supervisor/plan").json()

    assert plan["participants"]
    assert "mem_aheng" not in {item["member_id"] for item in plan["participants"]}


def test_phase7_member_and_org_skill_policy_apis(client: TestClient) -> None:
    availability = client.get("/api/members/mem_xiaoyao/availability").json()
    member_policy = client.patch(
        "/api/members/mem_xiaoyao/skill-policies",
        json={"allowed_skills": ["coordination"], "denied_skills": ["unsafe_skill"]},
    ).json()
    department_policy = client.patch(
        "/api/departments/dept_product/skill-policies",
        json={"allowed_skills": ["requirement_analysis"]},
    ).json()
    role_policy = client.patch(
        "/api/roles/role_architect/skill-policies",
        json={"allowed_skills": ["architecture_design"]},
    ).json()

    assert availability["status"] == "available"
    assert member_policy["allowed_skills"] == ["coordination"]
    assert department_policy["subject_type"] == "department"
    assert role_policy["subject_type"] == "role"


def test_phase7_remove_participant_skips_pending_subtask(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "请产品和技术共同制定一个上线方案",
            "auto_start": False,
        },
    ).json()
    plan = client.post(f"/api/tasks/{task['task_id']}/supervisor/plan").json()
    participant = next(
        item for item in plan["participants"] if item["participant_type"] != "host"
    )

    removed = client.post(
        f"/api/tasks/{task['task_id']}/participants/{participant['participant_id']}/remove",
        json={"reason": "暂不参与"},
    ).json()
    subtasks = client.get(f"/api/tasks/{task['task_id']}/subtasks").json()["items"]

    assert removed["status"] == "removed"
    assert any(
        item["participant_id"] == participant["participant_id"] and item["status"] == "skipped"
        for item in subtasks
    )


def test_phase7_completed_collaboration_rejects_mutations(client: TestClient) -> None:
    task = client.post(
        "/api/tasks",
        json={
            "goal": "请产品和技术共同制定一个上线方案",
            "auto_start": True,
        },
    ).json()
    participants = client.get(f"/api/tasks/{task['task_id']}/participants").json()["items"]
    participant = next(item for item in participants if item["participant_type"] != "host")
    subtasks = client.get(f"/api/tasks/{task['task_id']}/subtasks").json()["items"]

    remove_response = client.post(
        f"/api/tasks/{task['task_id']}/participants/{participant['participant_id']}/remove",
        json={"reason": "too late"},
    )
    retry_response = client.post(
        f"/api/tasks/{task['task_id']}/subtasks/{subtasks[0]['subtask_id']}/retry"
    )
    skip_response = client.post(
        f"/api/tasks/{task['task_id']}/subtasks/{subtasks[0]['subtask_id']}/skip",
        json={"reason": "too late"},
    )

    assert remove_response.status_code == 409
    assert retry_response.status_code == 409
    assert skip_response.status_code == 409
    assert remove_response.json()["error"]["code"] == "TASK_STATE_INVALID"
