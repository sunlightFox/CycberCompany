from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from app.core.errors import AppError
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase38_manifest_v2_preview_and_static_analyzer(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_skill_bundle(tmp_path, bundle_id="phase38-preview")
    response = client.post(
        "/api/skills/preview-install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["blocked"] is False
    assert payload["preview"]["bundle_id"] == "phase38-preview"
    assert payload["governance_preview"]["requires_user_grant"] is True
    assert payload["governance_preview"]["trust_level"] == "restricted"
    assert payload["governance_preview"]["permission_summary"]["tools"] == ["file.write"]
    assert payload["static_analysis"]["status"] in {"passed", "passed_with_warnings"}
    assert payload["source"]["source_uri_hash"].startswith("sha256:")
    assert _payload_leakage_count(payload) == 0


def test_phase38_secret_and_wildcard_permissions_are_blocked(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_skill_bundle(
        tmp_path,
        bundle_id="phase38-blocked",
        tool_name="terminal.run:*",
        extra_manifest="network:\n  allowed_domains: ['*']\n",
        skill_extra="\napi_key = 'sk-phase38-secret'\n",
    )
    response = client.post(
        "/api/skills/preview-install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["blocked"] is True
    reasons = set(payload["static_analysis"]["blocked_reasons"])
    assert {"wildcard_terminal", "hardcoded_api_key"}.issubset(reasons)
    assert "sk-phase38-secret" not in json.dumps(payload, ensure_ascii=False)


def test_phase38_skill_grant_enforcement_and_output_taint(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_skill_bundle(tmp_path, bundle_id="phase38-run")
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
            step_id="phase38-step",
            owner_member_id="mem_xiaoyao",
            input_data={"content": "phase38 safe content"},
        )
    assert missing_grant.value.code == "CAPABILITY_DENIED"

    grant = client.post(
        f"/api/skills/{skill_id}/grants",
        json={
            "subject_id": "mem_xiaoyao",
            "allowed_tools": ["file.write"],
            "denied_actions": ["terminal.run"],
        },
    )
    assert grant.status_code == 200, grant.text

    run = _run_async(
        client,
        registry.skill_plugin_service.run_skill,
        skill_id,
        task_id=task_id,
        step_id="phase38-step",
        owner_member_id="mem_xiaoyao",
        input_data={"content": "phase38 safe content"},
    )
    assert run.status == "completed"
    assert run.policy_snapshot["decision"] == "allow_with_grant"

    taints = client.get(f"/api/skills/{skill_id}/output-taints").json()["items"]
    assert taints
    assert taints[0]["guard_decision"] == "allow_as_untrusted_redacted"
    assert taints[0]["untrusted_external_content"] is True
    assert _payload_leakage_count({"run": run.model_dump(mode="json"), "taints": taints}) == 0


def test_phase38_grant_limits_tool_scope_and_unattended_high_risk(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_skill_bundle(tmp_path, bundle_id="phase38-denied")
    installed = _install_and_enable(client, bundle_dir)
    skill_id = installed["skills"][0]["skill_id"]
    task_id = _create_task(client)
    registry = cast(Any, client.app).state.registry

    client.post(
        f"/api/skills/{skill_id}/grants",
        json={"subject_id": "mem_xiaoyao", "allowed_tools": ["knowledge.search"]},
    )
    with pytest.raises(AppError) as denied:
        _run_async(
            client,
            registry.skill_plugin_service.run_skill,
            skill_id,
            task_id=task_id,
            step_id="phase38-step",
            owner_member_id="mem_xiaoyao",
            input_data={},
        )
    assert denied.value.code == "CAPABILITY_DENIED"

    high_risk_dir = _write_skill_bundle(
        tmp_path,
        bundle_id="phase38-highrisk",
        tool_name="terminal.run",
        tool_risk="R4",
        step_args={"command": "echo phase38"},
    )
    high = _install_and_enable(client, high_risk_dir)
    high_skill = high["skills"][0]["skill_id"]
    client.post(
        f"/api/skills/{high_skill}/grants",
        json={"subject_id": "mem_xiaoyao", "allowed_tools": ["terminal.run"]},
    )
    with pytest.raises(AppError) as unattended:
        _run_async(
            client,
            registry.skill_plugin_service.run_skill,
            high_skill,
            task_id=task_id,
            step_id="phase38-high",
            owner_member_id="mem_xiaoyao",
            input_data={"attendance": "unattended"},
        )
    assert unattended.value.code == "SAFETY_BLOCKED"


def test_phase38_upgrade_rollback_and_eval_bindings(
    client: TestClient,
    tmp_path: Path,
) -> None:
    installed = _install_and_enable(
        client,
        _write_skill_bundle(tmp_path, bundle_id="phase38-version"),
    )
    skill_id = installed["skills"][0]["skill_id"]

    eval_response = client.post(f"/api/skills/{skill_id}/eval")
    assert eval_response.status_code == 200, eval_response.text
    bindings = client.get(f"/api/skills/{skill_id}/eval-bindings").json()["items"]
    assert bindings
    assert bindings[0]["manifest_hash"]

    upgraded = client.post(
        f"/api/skills/{skill_id}/upgrade",
        json={
            "bundle_revision": "2.1.0",
            "display_name": "Phase38 Upgraded",
            "reason": "phase38 test",
        },
    )
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()["skill"]["display_name"] == "Phase38 Upgraded"

    rollback = client.post(
        f"/api/skills/{skill_id}/rollback",
        json={"reason": "phase38 rollback"},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["skill"]["display_name"] == "Phase38 Skill"


def test_phase38_release_contracts_report_diagnostic_and_migration(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase38")
    assert migration_contract["required_migration"] == "026_skill_governance.sql"

    suites = client.get("/api/evals/suites").json()["items"]
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_module = {item["name"]: item for item in contracts}
    registry = cast(Any, client.app).state.registry

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    phase38 = report["summary"]["phase38"]

    assert "suite_phase38_skill_governance" in {item["suite_id"] for item in suites}
    for module in [
        "SkillGovernanceService",
        "SkillPermissionPreview",
        "SkillGrantEnforcement",
        "SkillStaticAnalyzer",
        "SkillVersionRollback",
        "SkillEvalBinding",
        "SkillOutputTaintGuard",
    ]:
        assert by_module[module]["status"] == "implemented"
    assert completed["status"] == "ready_for_release"
    assert phase38["suite_id"] == "suite_phase38_skill_governance"
    assert phase38["registered_cases"] == 10
    assert phase38["tables"]["skill_grants"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase38"]["registered"] is True
    assert any(item["source_type"] == "phase38_skill_governance" for item in evidence)
    assert "phase38" in diagnostic
    assert "phase38_skill_governance" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _write_skill_bundle(
    tmp_path: Path,
    *,
    bundle_id: str,
    tool_name: str = "file.write",
    tool_risk: str = "R2",
    step_args: dict[str, Any] | None = None,
    extra_manifest: str = "",
    skill_extra: str = "",
) -> Path:
    root = tmp_path / bundle_id
    root.mkdir()
    args = step_args or {
        "path": "outputs/phase38-skill.txt",
        "content": "{content}",
    }
    (root / "bundle.yaml").write_text(
        (
            f"id: {bundle_id}\n"
            "bundle_revision: 2.0.0\n"
            "display_name: Phase38 Skill\n"
            "description: Skill governance test bundle\n"
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
            "  unattended_allowed: false\n"
            f"required_tools: [{tool_name}]\n"
            "steps:\n"
            f"  - tool_name: {tool_name}\n"
            f"    args: {json.dumps(args, ensure_ascii=False)}\n"
            f"{extra_manifest}"
        ),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        (
            "# Phase38 Skill\n\n"
            "## 用途\n写入任务工件。\n\n"
            "## 何时使用\n测试 Skill governance。\n\n"
            "## 输入\ncontent。\n\n"
            "## 输出\n任务工件。\n\n"
            "## 步骤\n调用声明工具。\n\n"
            "## 禁止\n不得访问密钥或任意本地路径。\n"
            f"{skill_extra}"
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
    bundle_id = payload["bundle"]["bundle_id"]
    enable = client.post(f"/api/plugins/{bundle_id}/enable", json={})
    assert enable.status_code == 200, enable.text
    return payload


def _create_task(client: TestClient) -> str:
    response = client.post(
        "/api/tasks",
        json={"goal": "phase38 skill task", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["task_id"])


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    async def runner() -> Any:
        return await func(*args, **kwargs)

    return cast(Any, client).portal.call(runner)


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "sk-phase38-secret",
        "token=phase38",
        "cookie=phase38",
        "private_key=phase38",
        "mnemonic=phase38",
        "c:\\users\\administrator\\phase38",
    ]
    return sum(1 for item in forbidden if item in serialized)
