from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient


def test_smooth_settings_are_default_and_mutable(client: TestClient) -> None:
    initial = client.get("/api/settings")
    assert initial.status_code == 200, initial.text
    safety = initial.json()["settings"]["safety"]
    assert safety["governance_mode"] == "smooth"
    assert safety["chat_visible_redaction"] == "relaxed"

    updated = client.patch(
        "/api/settings",
        json={
            "safety": {"governance_mode": "balanced"},
            "updated_by_member_id": "mem_xiaoyao",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["settings"]["safety"]["governance_mode"] == "balanced"


def test_smooth_skill_soft_findings_install_and_auto_enable(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        bundle_id="smooth-soft-skill",
        skill_extra="\ntoken: example\ncookie: example\npassword: example\n",
    )

    preview = client.post(
        "/api/skills/preview-install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert preview.status_code == 200, preview.text
    analysis = preview.json()["static_analysis"]
    assert analysis["status"] == "passed_with_warnings"
    assert set(analysis["blocked_reasons"]) == set()
    assert {"hardcoded_token", "hardcoded_cookie", "hardcoded_password"}.issubset(
        set(analysis["warnings"])
    )

    installed = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert installed.status_code == 200, installed.text
    payload = installed.json()
    assert payload["bundle"]["status"] == "enabled"
    assert payload["skills"][0]["status"] == "enabled"
    assert payload["bundle"]["risk_summary"]["static_analysis"]["status"] == "passed_with_warnings"


def test_smooth_skill_hardline_findings_still_block(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        bundle_id="smooth-hardline-skill",
        skill_extra="\nprivate_key: plain\nmnemonic: seed words\n",
    )

    preview = client.post(
        "/api/skills/preview-install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert preview.status_code == 200, preview.text
    reasons = set(preview.json()["static_analysis"]["blocked_reasons"])
    assert {"hardcoded_private_key", "hardcoded_mnemonic"}.issubset(reasons)

    installed = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert installed.status_code == 422
    assert installed.json()["error"]["code"] == "PLUGIN_VALIDATE_FAILED"


def test_smooth_eval_security_failure_warns_but_enables(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_bundle(
        tmp_path,
        bundle_id="smooth-eval-warning",
        skill_extra="\nforbidden_eval_marker\n",
        extra_manifest=(
            "eval_cases:\n"
            "  - id: soft-security\n"
            "    forbidden:\n"
            "      text: ['forbidden_eval_marker']\n"
        ),
    )

    installed = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert installed.status_code == 200, installed.text
    payload = installed.json()
    bundle_id = payload["bundle"]["bundle_id"]
    skill_id = payload["skills"][0]["skill_id"]
    assert payload["bundle"]["status"] == "enabled"
    assert payload["skills"][0]["status"] == "enabled"

    registry = cast(Any, client.app).state.registry
    _run_async(
        client,
        registry.skill_plugin_service._repo.insert_eval_case,
        {
            "eval_case_id": "sevalcase_smooth_warning",
            "organization_id": "org_default",
            "skill_id": skill_id,
            "bundle_id": bundle_id,
            "case_key": "soft-security",
            "input": {},
            "expected": {},
            "forbidden": {"text": ["forbidden_eval_marker"]},
            "risk_assertions": {},
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    disabled = client.post(f"/api/plugins/{bundle_id}/disable", json={})
    assert disabled.status_code == 200, disabled.text
    enabled = client.post(f"/api/plugins/{bundle_id}/enable", json={})
    assert enabled.status_code == 200, enabled.text

    events = client.get(f"/api/plugins/{bundle_id}/events")
    assert events.status_code == 200, events.text
    assert any(item["event_type"] == "plugin.eval_warning" for item in events.json()["items"])


def test_smooth_auto_approval_keeps_hardline_runtime_blocks(client: TestClient) -> None:
    client.patch(
        "/api/settings",
        json={
            "safety": {"governance_mode": "smooth"},
            "updated_by_member_id": "mem_xiaoyao",
        },
    )
    download = client.post(
        "/api/safety/evaluate",
        json={
            "actor_id": "mem_xiaoyao",
            "action_type": "tool",
            "action": "browser.download",
            "object_type": "browser",
            "tool_name": "browser.download",
            "payload": {"url": "http://127.0.0.1:8080/report.csv"},
        },
    ).json()
    submit = client.post(
        "/api/safety/evaluate",
        json={
            "actor_id": "mem_xiaoyao",
            "action_type": "tool",
            "action": "browser.submit",
            "object_type": "browser",
            "tool_name": "browser.submit",
            "payload": {"url": "https://example.test/contact", "form": {"name": "Ada"}},
        },
    ).json()
    delete = client.post(
        "/api/safety/evaluate",
        json={
            "actor_id": "mem_xiaoyao",
            "action_type": "tool",
            "action": "file.delete",
            "object_type": "tool",
            "tool_name": "file.delete",
            "payload": {"path": "outputs/smooth-delete.txt"},
        },
    ).json()

    task = client.post(
        "/api/tasks",
        json={"goal": "smooth hardline runtime", "auto_start": False},
    ).json()
    destructive = client.post(
        "/api/tools/execute",
        json={
            "task_id": task["task_id"],
            "tool_name": "terminal.run",
            "args": {"command": "rm -rf /"},
        },
    )

    assert download["decision"] == "allow"
    assert submit["decision"] == "allow"
    assert delete["approval_required"] is True
    assert destructive.status_code == 403


def test_repository_refresh_is_idempotent_with_unversioned_packages(
    client: TestClient,
) -> None:
    first = client.post("/api/skills/repositories/clawhub/refresh")
    assert first.status_code == 200, first.text
    assert first.json()["indexed_count"] >= 1

    second = client.post("/api/skills/repositories/clawhub/refresh")
    assert second.status_code == 200, second.text
    assert second.json()["indexed_count"] == first.json()["indexed_count"]


def _write_bundle(
    tmp_path: Path,
    *,
    bundle_id: str,
    skill_extra: str = "",
    extra_manifest: str = "",
) -> Path:
    root = tmp_path / bundle_id
    root.mkdir()
    (root / "bundle.yaml").write_text(
        (
            f"id: {bundle_id}\n"
            "bundle_revision: 2.0.0\n"
            "display_name: Smooth Governance Skill\n"
            "description: Experience-first governance test bundle\n"
            "author: local\n"
            "permissions:\n"
            "  tools:\n"
            "    - name: file.write\n"
            "      actions: [write_task_artifact]\n"
            "      risk: R2\n"
            "  assets: []\n"
            "filesystem:\n"
            "  allowed_roots: ['workspace://artifacts/**']\n"
            "  denied_roots: []\n"
            "safety:\n"
            "  unattended_allowed: false\n"
            "required_tools: [file.write]\n"
            "steps:\n"
            "  - tool_name: file.write\n"
            f"    args: {json.dumps({'path': 'outputs/smooth.txt', 'content': '{content}'})}\n"
            f"{extra_manifest}"
        ),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        (
            "# Smooth Governance Skill\n\n"
            "## 用途\n写入任务工件。\n\n"
            "## 何时使用\n测试 experience-first governance。\n\n"
            "## 输入\ncontent。\n\n"
            "## 输出\n任务工件。\n\n"
            "## 步骤\n调用声明工具。\n\n"
            "## 禁止\n不要访问真实密钥或外部账户。\n"
            f"{skill_extra}"
        ),
        encoding="utf-8",
    )
    return root


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    async def runner() -> Any:
        return await func(*args, **kwargs)

    return cast(Any, client).portal.call(runner)
