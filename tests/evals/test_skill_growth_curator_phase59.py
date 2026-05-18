from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.eval


def _await(callable_obj):
    return anyio.run(callable_obj)


async def _smooth_true() -> bool:
    return True


async def _insert_and_promote(registry: Any) -> tuple[Any, list[Any]]:
    await registry.skill_plugin_service._repo.insert_candidate(_candidate())
    return await registry.skill_plugin_service.promote_candidate(
        "cand.phase59",
        reviewed_by_member_id="mem_xiaoyao",
    )


def test_candidate_promote_auto_enables_and_records_lifecycle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        app = cast(FastAPI, client.app)
        registry = cast(Any, app.state.registry)
        registry.skill_plugin_service._smooth_governance_enabled = _smooth_true
        bundle, _skills = _await(partial(_insert_and_promote, registry))
        assert bundle.status == "enabled"

        lifecycle = client.get("/api/skills/lifecycle").json()["items"]
        assert lifecycle[0]["state"] == "active"
        assert lifecycle[0]["created_by"] == "agent"
        assert lifecycle[0]["provenance"] == "candidate_promote"


def test_skill_run_updates_lifecycle_and_curator_archives_agent_skill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.schemas.tasks import TaskCreateRequest

    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        app = cast(FastAPI, client.app)
        registry = cast(Any, app.state.registry)
        registry.skill_plugin_service._smooth_governance_enabled = _smooth_true
        _bundle, skills = _await(partial(_insert_and_promote, registry))
        skill_id = skills[0].skill_id
        task = _await(
            partial(
                registry.task_engine.create_task,
                TaskCreateRequest(
                    owner_member_id="mem_xiaoyao",
                    goal="run phase59 skill",
                    mode_hint="workflow",
                    auto_start=False,
                ),
            )
        )

        run = _await(
            partial(
                registry.skill_plugin_service.run_skill,
                skill_id,
                task_id=task.task_id,
                step_id=None,
                owner_member_id="mem_xiaoyao",
                input_data={"content": "phase59"},
            ),
        )
        assert run.status == "completed"

        after_run = client.get("/api/skills/lifecycle").json()["items"][0]
        assert after_run["use_count"] == 1
        assert after_run["success_count"] == 1

        client.post(f"/api/skills/{skill_id}/pin", json={"actor_member_id": "mem_xiaoyao"})
        _await(
            partial(
                registry.skill_plugin_service._repo.update_skill_lifecycle,
                skill_id,
                {"updated_at": "2000-01-01T00:00:00Z", "last_used_at": "2000-01-01T00:00:00Z"},
            )
        )
        dry_run = client.post(
            "/api/skills/curator/run",
            json={"stale_after_days": 1, "archive_after_days": 1, "dry_run": False},
        ).json()
        assert dry_run["skipped_pinned_count"] == 1

        client.post(f"/api/skills/{skill_id}/unpin", json={"actor_member_id": "mem_xiaoyao"})
        preview = client.post(
            "/api/skills/curator/run",
            json={"stale_after_days": 1, "archive_after_days": 1, "dry_run": True},
        ).json()
        assert preview["preview_items"]
        assert preview["preview_items"][0]["proposed_action"] == "archive"
        archived = client.post(
            "/api/skills/curator/run",
            json={"stale_after_days": 1, "archive_after_days": 1, "dry_run": False},
        ).json()
        assert archived["archived_count"] >= 1
        item = client.get("/api/skills/lifecycle?include_archived=true").json()["items"][0]
        assert item["state"] == "archived"

        restored = client.post(
            f"/api/skills/{skill_id}/restore",
            json={"actor_member_id": "mem_xiaoyao"},
        )
        assert restored.status_code == 200
        assert restored.json()["items"][0]["state"] == "active"


def test_archive_restore_only_touches_target_skill_in_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        app = cast(FastAPI, client.app)
        registry = cast(Any, app.state.registry)
        now = "2025-01-01T00:00:00Z"
        bundle_id = "bundle.multi"
        _await(
            partial(
                registry.skill_plugin_service._repo.insert_bundle,
                {
                    "bundle_id": bundle_id,
                    "organization_id": "org_default",
                    "display_name": "Multi Bundle",
                    "description": "two skills",
                    "author": "local",
                    "bundle_revision": "1",
                    "source_type": "candidate",
                    "source_uri": "bundle://multi",
                    "package_uri": "bundle://multi",
                    "manifest_hash": "hash",
                    "signature_status": "unsigned",
                    "trust_level": "local",
                    "status": "enabled",
                    "permission_summary": {},
                    "risk_summary": {},
                    "manifest": {"id": bundle_id, "display_name": "Multi Bundle", "required_tools": ["file.write"]},
                    "installed_by_member_id": "mem_xiaoyao",
                    "installed_at": now,
                    "enabled_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            )
        )
        for suffix in ("one", "two"):
            skill_id = f"skill.{suffix}"
            _await(
                partial(
                    registry.skill_plugin_service._repo.insert_skill,
                    {
                        "skill_id": skill_id,
                        "organization_id": "org_default",
                        "bundle_id": bundle_id,
                        "name": skill_id,
                        "display_name": skill_id,
                        "description": skill_id,
                        "entrypoint_path": "SKILL.md",
                        "instructions": "do thing",
                        "trigger": {},
                        "input_schema": {},
                        "output_schema": {},
                        "required_tools": ["file.write"],
                        "required_assets": [],
                        "permission": {},
                        "risk_policy": {"confirmation_required_for": [], "forbidden_actions": []},
                        "eval_summary": {},
                        "steps": [{"tool_name": "file.write", "args": {"path": "a.txt", "content": "x"}}],
                        "status": "enabled",
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            )
            _await(
                partial(
                    registry.skill_plugin_service._initialize_skill_lifecycle,
                    skill_id=skill_id,
                    bundle_id=bundle_id,
                    created_by="agent",
                    provenance="test",
                )
            )

        archived = client.post(
            "/api/skills/skill.one/archive",
            json={"actor_member_id": "mem_xiaoyao", "reason": "test"},
        )
        assert archived.status_code == 200
        archived_skill = _await(partial(registry.skill_plugin_service.get_skill, "skill.one"))
        other_skill = _await(partial(registry.skill_plugin_service.get_skill, "skill.two"))
        bundle = _await(partial(registry.skill_plugin_service.get_bundle, bundle_id))
        assert archived_skill.status == "disabled"
        assert other_skill.status == "enabled"
        assert bundle.status == "enabled"

        restored = client.post(
            "/api/skills/skill.one/restore",
            json={"actor_member_id": "mem_xiaoyao"},
        )
        assert restored.status_code == 200
        restored_skill = _await(partial(registry.skill_plugin_service.get_skill, "skill.one"))
        bundle = _await(partial(registry.skill_plugin_service.get_bundle, bundle_id))
        assert restored_skill.status == "enabled"
        assert bundle.status == "enabled"


def test_restore_blocked_keeps_archived_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        app = cast(FastAPI, client.app)
        registry = cast(Any, app.state.registry)
        registry.skill_plugin_service._smooth_governance_enabled = _smooth_true
        _bundle, skills = _await(partial(_insert_and_promote, registry))
        skill_id = skills[0].skill_id
        archived = client.post(
            f"/api/skills/{skill_id}/archive",
            json={"actor_member_id": "mem_xiaoyao"},
        )
        assert archived.status_code == 200
        _await(
            partial(
                registry.skill_plugin_service._repo.update_bundle,
                "phase59-candidate",
                {
                    "manifest": {
                        "id": "phase59-candidate",
                        "display_name": "Phase59 Candidate",
                        "required_tools": ["wallet.sign_transaction"],
                        "steps": [{"tool_name": "wallet.sign_transaction", "args": {}}],
                    }
                },
            )
        )
        blocked = client.post(
            f"/api/skills/{skill_id}/restore",
            json={"actor_member_id": "mem_xiaoyao"},
        )
        assert blocked.status_code == 409
        item = client.get("/api/skills/lifecycle?include_archived=true").json()["items"][0]
        assert item["state"] == "archived"


def test_skill_candidate_extractor_builds_structured_workflow() -> None:
    from app.services.skill_candidate_extractor import SkillCandidateExtractor
    from core_types.task import TaskDetail, TaskReplay, ToolCallRecord

    replay = TaskReplay(
        task=TaskDetail(
            task_id="task1",
            organization_id="org_default",
            title="doc",
            goal="publish weekly update",
            mode="direct",
            status="completed",
            risk_level="R2",
        ),
        tool_calls=[
            ToolCallRecord(tool_call_id="tc1", tool_name="browser.open", status="completed"),
            ToolCallRecord(tool_call_id="tc2", tool_name="mcp.cms.publish", status="completed"),
            ToolCallRecord(tool_call_id="tc3", tool_name="office.document.write", status="completed"),
        ],
        skill_runs=[{"skill_id": "skill.one", "status": "completed"}],
        retry_plans=[],
        artifacts=[],
    )

    items = SkillCandidateExtractor().extract_from_replay(replay)
    assert items
    assert items[0]["candidate_type"] == "browser_mcp_workflow"
    assert items[0]["steps"]
    assert items[0]["required_tools"]
    assert items[0]["acceptance"]
    assert items[0]["workflow_signature"]
    assert items[0]["primary_tools"]
    assert items[0]["inputs"]
    assert items[0]["outputs"]
    assert items[0]["preconditions"]
    assert items[0]["classification_reason"]


def _candidate() -> dict[str, Any]:
    now = "2025-01-01T00:00:00Z"
    return {
        "candidate_id": "cand.phase59",
        "organization_id": "org_default",
        "source_type": "memory_experience",
        "source_id": "exp.phase59",
        "title": "Phase59 Candidate",
        "description": "A low-risk reusable workflow",
        "draft_manifest": {
            "id": "phase59-candidate",
            "display_name": "Phase59 Candidate",
            "required_tools": ["file.write"],
            "triggers": {"keywords": ["phase59"], "intents": ["draft"]},
            "risk_policy": {"confirmation_required_for": [], "forbidden_actions": []},
        },
        "draft_skill_md": (
            "# Phase59 Candidate\n\n"
            "## 鐢ㄩ€?\nwrite a file\n\n"
            "## 浣曟椂浣跨敤\nwhen needed\n\n"
            "## 杈撳叆\ncontent\n\n"
            "## 杈撳嚭\nfile\n\n"
            "## 姝ラ\n1. write file\n\n"
            "## 绂佹\nno secrets"
        ),
        "proposed_permissions": {"tools": ["file.write"]},
        "proposed_eval_cases": [],
        "status": "pending_review",
        "reviewed_by_member_id": None,
        "review_reason": None,
        "promoted_bundle_id": None,
        "trace_id": "trace_phase59",
        "created_at": now,
        "updated_at": now,
    }
