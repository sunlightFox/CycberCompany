from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def test_phase9_runtime_contracts_and_design_gaps_are_honest(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    gaps = client.get("/api/system/design-gaps").json()["items"]
    by_name = {item["name"]: item for item in contracts}

    assert by_name["SafetyService"]["status"] == "implemented"
    assert by_name["SafetyService"]["implemented"] is True
    assert by_name["HeartService"]["status"] == "implemented"
    assert by_name["PersonaEngine"]["status"] == "implemented"
    assert by_name["VectorStore"]["status"] == "implemented"
    assert by_name["SettingsAPI"]["status"] == "implemented"
    assert any(item["module_name"] == "VectorStore" for item in gaps)


def test_phase9_safety_decisions_cover_allow_approval_and_deny(
    client: TestClient,
) -> None:
    allowed = client.post(
        "/api/safety/evaluate",
        json={
            "actor_id": "mem_xiaoyao",
            "action_type": "tool",
            "action": "file.read",
            "object_type": "tool",
            "tool_name": "file.read",
            "risk_hints": ["R1"],
        },
    ).json()
    approval = client.post(
        "/api/safety/evaluate",
        json={
            "actor_id": "mem_xiaoyao",
            "action_type": "tool",
            "action": "terminal.run",
            "object_type": "tool",
            "tool_name": "terminal.run",
            "payload": {"command": "echo safe"},
            "risk_hints": ["R5"],
        },
    ).json()
    denied = client.post(
        "/api/safety/evaluate",
        json={
            "actor_id": "mem_xiaoyao",
            "action_type": "tool",
            "action": "upload",
            "object_type": "external",
            "destination": "https://example.test",
            "payload": {"api_key": "sk-phase9ShouldRedact123456"},
        },
    ).json()
    fetched = client.get(f"/api/safety/decisions/{denied['safety_decision_id']}").json()
    audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    assert allowed["decision"] == "allow"
    assert approval["decision"] == "approval_required"
    assert approval["risk_level"] == "R5"
    assert denied["decision"] == "deny"
    assert denied["allowed"] is False
    assert "api_key" in denied["redactions"]
    assert fetched["safety_decision_id"] == denied["safety_decision_id"]
    assert "sk-phase9ShouldRedact123456" not in audit_text


def test_phase9_tool_runtime_records_safety_and_blocks_danger(
    client: TestClient,
) -> None:
    task = client.post(
        "/api/tasks",
        json={"goal": "phase9 tool safety", "auto_start": False},
    ).json()
    write = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "file.write",
            "args": {"path": "outputs/phase9.txt", "content": "ok"},
        },
    ).json()
    dangerous = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "Remove-Item -Recurse data/secrets"},
        },
    )

    assert write["tool_call"]["safety_decision_id"]
    assert write["tool_call"]["safety_decision"]["decision"] == "allow"
    assert write["tool_call"]["policy_snapshot"]["policy_sources"]
    assert dangerous.status_code == 403
    assert dangerous.json()["error"]["code"] == "SAFETY_BLOCKED"


def test_phase9_asset_resolve_for_tool_returns_minimal_resource(
    client: TestClient,
) -> None:
    asset = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": "Phase9 Account",
            "provider": "local",
            "secret_value": "token=phase9-secret",
            "config": {"platform": "test", "username": "owner", "auth_type": "token"},
            "summary_text": "phase9 account",
        },
    ).json()
    client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset["asset_id"],
            "action": "read_profile",
            "effect": "allow",
        },
    )
    handle = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": ["read_profile"],
            "keywords": ["Phase9"],
        },
    ).json()["handles"][0]
    resolved = client.post(
        f"/api/assets/handles/{handle['handle_id']}/resolve-for-tool",
        json={
            "subject_id": "mem_xiaoyao",
            "action": "read_profile",
            "tool_name": "account.create_draft_artifact",
        },
    ).json()
    text = json.dumps(resolved, ensure_ascii=False)

    assert resolved["handle_id"] == handle["handle_id"]
    assert resolved["has_secret"] is True
    assert resolved["resource"]["config"]["username"] == "owner"
    assert "phase9-secret" not in text
    assert "secret_ref" not in text


def test_phase9_persona_heart_and_vector_contracts(client: TestClient) -> None:
    profiles = client.get("/api/persona/profiles").json()["items"]
    profile_id = profiles[0]["persona_profile_id"]
    updated = client.patch(
        f"/api/persona/profiles/{profile_id}",
        json={"summary": "Calm, exact, and brief."},
    ).json()
    heart = client.get("/api/heart/state/mem_xiaoyao").json()
    vector = client.post(
        "/api/vector/sync-jobs",
        json={"target_type": "memory", "target_id": "mem_test"},
    ).json()
    fetched = client.get(f"/api/vector/sync-jobs/{vector['job_id']}").json()

    assert updated["summary"] == "Calm, exact, and brief."
    assert heart["member_id"] == "mem_xiaoyao"
    assert vector["provider"] == "local"
    assert vector["status"] == "completed"
    assert fetched["job_id"] == vector["job_id"]


def test_phase9_company_shell_copy_lives_in_shell_template() -> None:
    bootstrap_source = Path("apps/local-api/app/services/bootstrap.py").read_text(
        encoding="utf-8"
    )
    shell_source = Path("shells/company/shell.yaml").read_text(encoding="utf-8")

    for forbidden in ("老板", "公司", "一人公司"):
        assert forbidden not in bootstrap_source
        assert forbidden in shell_source
