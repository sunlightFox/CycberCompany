from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from app.core.errors import AppError
from core_types import RiskLevel
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase48_skill_preflight_uses_capability_and_checkpoint_policy(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_skill_bundle(tmp_path, bundle_id="phase48-capability")
    installed = _install_and_enable(client, bundle_dir)
    skill_id = installed["skills"][0]["skill_id"]
    task_id = _create_task(client)
    registry = cast(Any, client.app).state.registry

    with pytest.raises(AppError) as missing_grant:
        _run_async(
            client,
            registry.skill_plugin_service.run_skill,
            skill_id,
            task_id=task_id,
            step_id="phase48-step",
            owner_member_id="mem_xiaoyao",
            input_data={"content": "phase48 safe content"},
        )
    assert missing_grant.value.code == "CAPABILITY_DENIED"

    grant = client.post(
        f"/api/skills/{skill_id}/grants",
        json={"subject_id": "mem_xiaoyao", "allowed_tools": ["file.write"]},
    )
    assert grant.status_code == 200, grant.text
    edges = _skill_capability_edges(client, skill_id)
    assert any(
        edge["status"] == "active" and edge["source_type"] == "skill_grant"
        for edge in edges
    )

    run = _run_async(
        client,
        registry.skill_plugin_service.run_skill,
        skill_id,
        task_id=task_id,
        step_id="phase48-step",
        owner_member_id="mem_xiaoyao",
        input_data={"content": "phase48 safe content"},
    )
    policy = run.policy_snapshot

    assert run.status == "completed"
    assert policy["governance"] == "phase48"
    assert policy["tool_runtime_boundary"] == "required"
    assert policy["capability_graph"]["fact_source"] == "capability_graph"
    assert policy["capability_graph"]["allowed"] is True
    assert policy["checkpoint_requirements"][0]["tool_name"] == "file.write"
    assert policy["checkpoint_requirements"][0]["checkpoint_required"] is True
    assert _payload_leakage_count({"run": run.model_dump(mode="json")}) == 0

    revoked = client.post(
        f"/api/skills/{skill_id}/revoke",
        json={"actor_member_id": "mem_xiaoyao", "reason": "phase48 revoke"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_capability_edges"] >= 1
    edges_after_revoke = _skill_capability_edges(client, skill_id)
    assert not any(edge["status"] == "active" for edge in edges_after_revoke)


def test_phase48_unattended_skill_requires_passed_eval_binding(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_skill_bundle(
        tmp_path,
        bundle_id="phase48-unattended",
        tool_name="knowledge.search",
        tool_risk="R1",
        step_args={"query": "{content}", "limit": 1},
        unattended_allowed=True,
    )
    installed = _install_and_enable(client, bundle_dir)
    skill_id = installed["skills"][0]["skill_id"]
    task_id = _create_task(client)
    registry = cast(Any, client.app).state.registry

    grant = client.post(
        f"/api/skills/{skill_id}/grants",
        json={"subject_id": "mem_xiaoyao", "allowed_tools": ["knowledge.search"]},
    )
    assert grant.status_code == 200, grant.text
    _set_skill_eval_binding_status(client, skill_id, "failed")

    with pytest.raises(AppError) as missing_eval:
        _run_async(
            client,
            registry.skill_plugin_service.run_skill,
            skill_id,
            task_id=task_id,
            step_id="phase48-unattended",
            owner_member_id="mem_xiaoyao",
            input_data={
                "content": "phase48",
                "execution_context": {"attendance": "unattended"},
            },
        )
    assert missing_eval.value.code == "SAFETY_BLOCKED"
    assert missing_eval.value.details["reason_code"] == "unattended_skill_eval_binding_missing"

    scheduled = client.post(
        "/api/scheduled-tasks",
        json={
            "title": "phase48 scheduled skill",
            "goal": "phase48 scheduled skill run",
            "owner_member_id": "mem_xiaoyao",
            "schedule": {"kind": "once", "run_at": "2026-05-01T00:00:00+00:00"},
            "execution_policy": {"attendance": "unattended"},
            "constraints": {
                "skill_id": skill_id,
                "skill_input": {"content": "phase48 scheduled"},
            },
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    trigger = client.post(
        f"/api/scheduled-tasks/{scheduled.json()['scheduled_task_id']}/trigger",
        json={"scheduled_for": "2026-05-01T00:00:01+00:00"},
    )
    assert trigger.status_code == 200, trigger.text
    assert trigger.json()["status"] == "failed"
    assert trigger.json()["result"]["task_status"] == "failed"
    runs = client.get(
        f"/api/scheduled-tasks/{scheduled.json()['scheduled_task_id']}/runs"
    ).json()["items"]
    assert runs[0]["status"] == "failed"
    assert "linked_task_failed" in runs[0]["failure_reason"]

    eval_response = client.post(f"/api/skills/{skill_id}/eval")
    assert eval_response.status_code == 200, eval_response.text

    run = _run_async(
        client,
        registry.skill_plugin_service.run_skill,
        skill_id,
        task_id=task_id,
        step_id="phase48-unattended",
        owner_member_id="mem_xiaoyao",
        input_data={
            "content": "phase48",
            "execution_context": {"attendance": "unattended"},
        },
    )
    assert run.status == "completed"
    assert run.policy_snapshot["unattended_policy"]["eval_binding_status"] == "passed"
    assert run.policy_snapshot["unattended_policy"]["trust_level"] in {"local", "restricted"}


def test_phase48_notification_pending_action_resume_and_fail_closed(
    client: TestClient,
) -> None:
    first = _create_approval(client, requested_action="browser.download", risk=RiskLevel.R3)
    second = _create_approval(client, requested_action="file.delete", risk=RiskLevel.R5)
    channel_id = _approval_channel(client, first["approval_id"])

    multiple = client.post(
        "/api/notification/inbound",
        json={
            "channel_id": channel_id,
            "sender_ref": "user_local_owner",
            "content": "只允许这一次下载",
        },
    )
    assert multiple.status_code == 200, multiple.text
    multiple_payload = multiple.json()
    assert multiple_payload["binding_status"] == "clarification_required"
    assert multiple_payload["action_result"]["governance_chain"] == "phase48"
    assert multiple_payload["action_result"]["pending_action_binding"] == "clarification_required"
    assert client.get(f"/api/approvals/{first['approval_id']}").json()["status"] == "pending"
    assert client.get(f"/api/approvals/{second['approval_id']}").json()["status"] == "pending"

    _deny(client, second["approval_id"])
    matched = client.post(
        "/api/notification/inbound",
        json={
            "channel_id": channel_id,
            "sender_ref": "user_local_owner",
            "content": "只允许这一次下载 report.csv",
        },
    )
    assert matched.status_code == 200, matched.text
    payload = matched.json()
    assert payload["binding_status"] == "matched"
    assert payload["matched_approval_id"] == first["approval_id"]
    assert payload["action_result"]["pending_action_binding"] == "unique_approval"
    assert payload["action_result"]["task_resume_attempted"] is True
    assert payload["action_result"]["task_resume"]["status"] == "resume_notified"
    assert payload["untrusted_external_content"] is True
    assert client.get(f"/api/approvals/{first['approval_id']}").json()["status"] == "approved"


def test_phase48_checkpoint_rollback_creates_notification_summary(
    client: TestClient,
) -> None:
    task_id = _create_task(client)
    _write_file(client, task_id, "outputs/phase48-rollback.txt", "before")
    checkpoint = client.post(
        f"/api/tasks/{task_id}/checkpoints",
        json={"paths": ["outputs/phase48-rollback.txt"], "reason": "phase48 rollback"},
    )
    assert checkpoint.status_code == 200, checkpoint.text

    _overwrite_file(client, task_id, "outputs/phase48-rollback.txt", "after")
    rollback = client.post(
        f"/api/checkpoints/{checkpoint.json()['checkpoint_id']}/rollback",
        json={"requested_by": "user_local_owner", "reason": "phase48 restore"},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["event"]["status"] == "completed"

    messages = client.get("/api/notification/messages").json()["items"]
    rollback_messages = [
        item for item in messages if item["message_type"] == "checkpoint_rollback_summary"
    ]
    assert rollback_messages
    assert rollback_messages[0]["status"] == "queued"
    assert rollback_messages[0]["metadata"]["governance_chain"] == "phase48"
    assert _payload_leakage_count({"rollback": rollback.json(), "messages": messages}) == 0


def test_phase48_release_contracts_eval_report_and_diagnostic(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase48")
    assert migration_contract["required_migration"] == "031_media_runtime.sql"

    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    for module in [
        "GovernanceClosureMatrix",
        "SkillCapabilityPreflight",
        "SkillGrantCapabilitySync",
        "SkillCheckpointPolicy",
        "UnattendedSkillGovernanceGate",
        "NotificationTaskResumeBridge",
        "RollbackNotificationSummary",
        "CapabilityGraphGovernanceSource",
    ]:
        assert by_name[module]["status"] == "implemented"

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase48_governance_closure"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 10

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]

    phase48 = report["summary"]["phase48"]
    assert completed["status"] == "ready_for_release"
    assert "suite_phase48_governance_closure" in {item["suite_id"] for item in suites}
    assert phase48["suite_id"] == "suite_phase48_governance_closure"
    assert phase48["registered_cases"] == 10
    assert phase48["governance_matrix"]["capability_graph_fact_source"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase48"]["registered"] is True
    assert any(item["source_type"] == "phase48_governance_closure" for item in evidence)
    assert "phase48" in diagnostic
    assert "phase48_governance_closure" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _write_skill_bundle(
    tmp_path: Path,
    *,
    bundle_id: str,
    tool_name: str = "file.write",
    tool_risk: str = "R2",
    step_args: dict[str, Any] | None = None,
    unattended_allowed: bool = False,
) -> Path:
    root = tmp_path / bundle_id
    root.mkdir()
    args = step_args or {
        "path": "outputs/phase48-skill.txt",
        "content": "{content}",
    }
    (root / "bundle.yaml").write_text(
        (
            f"id: {bundle_id}\n"
            "bundle_revision: 2.0.0\n"
            "display_name: Phase48 Skill\n"
            "description: Governance closure test bundle\n"
            "author: local\n"
            "permissions:\n"
            "  tools:\n"
            f"    - name: {tool_name}\n"
            "      actions: [write_task_artifact]\n"
            f"      risk: {tool_risk}\n"
            "  assets: []\n"
            "filesystem:\n"
            "  allowed_roots: ['workspace://artifacts/**']\n"
            "  denied_roots: ['~/.ssh/**', '**/.env']\n"
            "safety:\n"
            f"  unattended_allowed: {str(unattended_allowed).lower()}\n"
            f"required_tools: [{tool_name}]\n"
            "eval_cases: []\n"
            "steps:\n"
            f"  - tool_name: {tool_name}\n"
            f"    args: {json.dumps(args, ensure_ascii=False)}\n"
        ),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        (
            "# Phase48 Skill\n\n"
            "## 用途\n验证治理闭环。\n\n"
            "## 何时使用\n测试 Skill governance closure。\n\n"
            "## 输入\ncontent。\n\n"
            "## 输出\n任务工件或检索结果。\n\n"
            "## 步骤\n调用声明工具。\n\n"
            "## 禁止\n不得访问密钥、任意本地路径或绕过审批。\n"
        ),
        encoding="utf-8",
    )
    return root


def _install_and_enable(client: TestClient, bundle_dir: Path) -> dict[str, Any]:
    install = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert install.status_code == 200, install.text
    payload = install.json()
    enable = client.post(f"/api/plugins/{payload['bundle']['bundle_id']}/enable", json={})
    assert enable.status_code == 200, enable.text
    return payload


def _create_task(client: TestClient) -> str:
    response = client.post(
        "/api/tasks",
        json={"goal": "phase48 governance task", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["task_id"])


def _write_file(client: TestClient, task_id: str, path: str, content: str) -> dict[str, Any]:
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.write",
            "args": {"path": path, "content": content},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["result"])


def _overwrite_file(client: TestClient, task_id: str, path: str, content: str) -> dict[str, Any]:
    approval_id = _request_tool_approval(
        client,
        task_id,
        "file.write",
        {"path": path, "content": content, "overwrite": True},
    )
    _approve(client, approval_id)
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": "file.write",
            "approval_id": approval_id,
            "args": {"path": path, "content": content, "overwrite": True},
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["result"])


def _request_tool_approval(
    client: TestClient,
    task_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> str:
    response = client.post(
        "/api/tools/execute",
        json={
            "task_id": task_id,
            "member_id": "mem_xiaoyao",
            "tool_name": tool_name,
            "args": args,
        },
    )
    assert response.status_code == 200, response.text
    approval = response.json()["approval"]
    assert approval["status"] == "pending"
    return str(approval["approval_id"])


def _approve(client: TestClient, approval_id: str) -> None:
    response = client.post(f"/api/approvals/{approval_id}/approve", json={"reason": "phase48"})
    assert response.status_code == 200, response.text


def _create_approval(
    client: TestClient,
    *,
    requested_action: str,
    risk: RiskLevel,
) -> dict[str, Any]:
    task_id = _create_task(client)

    async def runner() -> Any:
        approval = await cast(Any, client.app).state.registry.approval_service.create_approval(
            task_id=task_id,
            organization_id="org_default",
            requested_action=requested_action,
            risk_level=risk,
            summary=f"需要确认 {requested_action}",
            payload={"action": requested_action, "target": "report.csv"},
            trace_id=None,
        )
        return approval.model_dump(mode="json")

    return dict(cast(Any, client).portal.call(runner))


def _approval_channel(client: TestClient, approval_id: str) -> str:
    messages = client.get("/api/notification/messages").json()["items"]
    message = next(item for item in messages if item.get("approval_id") == approval_id)
    return str(message["channel_id"])


def _deny(client: TestClient, approval_id: str) -> None:
    response = client.post(f"/api/approvals/{approval_id}/deny", json={"reason": "phase48"})
    assert response.status_code == 200, response.text


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    async def runner() -> Any:
        return await func(*args, **kwargs)

    return cast(Any, client).portal.call(runner)


def _set_skill_eval_binding_status(client: TestClient, skill_id: str, status: str) -> None:
    async def runner() -> None:
        await cast(Any, client.app).state.registry.db.execute(
            "UPDATE skill_eval_bindings SET status = ? WHERE skill_id = ?",
            (status, skill_id),
        )

    cast(Any, client).portal.call(runner)


def _skill_capability_edges(client: TestClient, skill_id: str) -> list[dict[str, Any]]:
    async def runner() -> list[dict[str, Any]]:
        edges = await cast(Any, client.app).state.registry.capability_service.list_grants(
            object_type="skill",
            object_id=skill_id,
            limit=50,
        )
        return [edge.model_dump(mode="json") for edge in edges]

    return list(cast(Any, client).portal.call(runner))


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "sk-phase48-secret",
        "token=phase48",
        "cookie=phase48",
        "private_key=phase48",
        "mnemonic=phase48",
        "c:\\users\\administrator\\phase48",
    ]
    return sum(1 for item in forbidden if item in serialized)
