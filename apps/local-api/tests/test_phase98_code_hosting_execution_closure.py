from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from app.core.errors import AppError
from app.services.chat_intent_router import ChatIntentRouter
from app.services.registry import ServiceRegistry
from core_types import ErrorCode, SkillRunRecord
from fastapi.testclient import TestClient


def test_phase98_router_exposes_code_hosting_routes() -> None:
    for text, route_type in [
        ("请帮我看看 GitHub 仓库现在的 PR 状态。", "code_hosting_readonly_request"),
        ("把这个 GitHub 分支 push 上去并同步远程。", "code_hosting_sync_request"),
        ("帮我在 GitHub 上创建一个 PR。", "code_hosting_pr_request"),
        ("请 review 这个 GitHub PR 并给评论。", "code_hosting_review_request"),
        ("帮我发布一个 GitHub release。", "code_hosting_release_request"),
    ]:
        decision = ChatIntentRouter().decide(text)
        assert decision.route_type == route_type
        assert decision.metadata["forge_provider_type"] == "github"


def test_phase98_task_binds_clawhub_github_skill_and_freezes_binding(
    client: TestClient,
    monkeypatch,
) -> None:
    app = cast(Any, client.app)
    registry = cast(ServiceRegistry, app.state.registry)
    task = client.post(
        "/api/tasks",
        json={
            "goal": "帮我在 GitHub 上创建一个 PR。",
            "auto_start": False,
            "constraints": {
                "code_hosting_request_type": "code_hosting_pr_request",
                "remote_repo_ref": "github.com/acme/demo",
                "base_branch": "main",
                "target_branch": "feature/demo",
            },
        },
    )
    assert task.status_code == 200, task.text
    task_id = task.json()["task_id"]

    bundle = client.get("/api/plugins/clawhub-github-pr-workflow")
    assert bundle.status_code == 200, bundle.text
    assert bundle.json()["status"] == "enabled"

    skill_list = client.get("/api/skills", params={"status": "enabled"})
    assert skill_list.status_code == 200, skill_list.text
    enabled_skill = next(
        item
        for item in skill_list.json()["items"]
        if item["bundle_id"] == "clawhub-github-pr-workflow"
    )
    grants = client.get(f"/api/skills/{enabled_skill['skill_id']}/grants")
    assert grants.status_code == 200, grants.text
    assert any(
        item["subject_id"] == "mem_xiaoyao" and item["status"] == "active"
        for item in grants.json()["items"]
    )

    async def search_empty(**_: Any) -> list[Any]:
        return []

    monkeypatch.setattr(registry.skill_repository_service, "search", search_empty)

    async def fake_run_skill(
        skill_id: str,
        *,
        task_id: str,
        step_id: str,
        owner_member_id: str,
        input_data: dict[str, Any],
        matched_reason: str,
        confidence: float | None,
        approval_id: str | None,
        trace_id: str | None,
    ) -> SkillRunRecord:
        artifact = await registry.artifact_store.write_text(
            task_id=task_id,
            organization_id="org_default",
            step_id=step_id,
            display_name="code-hosting-report.md",
            content="# remote execution\n",
            artifact_type="report",
            trace_id=trace_id,
        )
        return SkillRunRecord(
            skill_run_id="skrun_phase98_freeze",
            organization_id="org_default",
            skill_id=skill_id,
            bundle_id="clawhub-github-pr-workflow",
            task_id=task_id,
            step_id=step_id,
            owner_member_id=owner_member_id,
            status="completed",
            input_redacted=input_data,
            output_redacted={
                "remote_artifacts": [{"type": "pull_request", "ref": "PR-42"}],
                "pr_summary": {"status": "opened", "pr_ref": "PR-42"},
                "commit_summary": {"headline": "prepare pr"},
            },
            matched_reason=matched_reason,
            confidence=confidence,
            approval_id=approval_id,
            artifact_ids=[artifact.artifact_id],
            trace_id=trace_id,
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(registry.skill_plugin_service, "run_skill", fake_run_skill)
    started = client.post(f"/api/tasks/{task_id}/start")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "completed"

    replay = client.get(f"/api/tasks/{task_id}/replay").json()
    assert replay["final_result"]["code_hosting_package_ref"] == "official/github/pr-workflow"
    assert replay["final_result"]["code_hosting_bundle_id"] == "clawhub-github-pr-workflow"
    assert replay["final_result"]["code_hosting_skill_id"] == enabled_skill["skill_id"]
    assert replay["final_result"]["deliverable"] is True


def test_phase98_pr_closure_writes_code_hosting_replay_evidence(
    client: TestClient,
    monkeypatch,
) -> None:
    app = cast(Any, client.app)
    registry = cast(ServiceRegistry, app.state.registry)

    async def fake_run_skill(
        skill_id: str,
        *,
        task_id: str,
        step_id: str,
        owner_member_id: str,
        input_data: dict[str, Any],
        matched_reason: str,
        confidence: float | None,
        approval_id: str | None,
        trace_id: str | None,
    ) -> SkillRunRecord:
        artifact = await registry.artifact_store.write_text(
            task_id=task_id,
            organization_id="org_default",
            step_id=step_id,
            display_name="code-hosting-report.md",
            content="# remote execution\n",
            artifact_type="report",
            trace_id=trace_id,
        )
        return SkillRunRecord(
            skill_run_id="skrun_phase98_pr",
            organization_id="org_default",
            skill_id=skill_id,
            bundle_id="clawhub-github-pr-workflow",
            task_id=task_id,
            step_id=step_id,
            owner_member_id=owner_member_id,
            status="completed",
            input_redacted=input_data,
            output_redacted={
                "remote_artifacts": [{"type": "pull_request", "ref": "PR-108"}],
                "branch_state": {"base_branch": "main", "target_branch": "feature/api"},
                "commit_summary": {"headline": "feat: api update"},
                "pr_summary": {"status": "opened", "pr_ref": "PR-108"},
                "review_outcome": {"status": "requested"},
                "publish_blockers": [],
            },
            matched_reason=matched_reason,
            confidence=confidence,
            approval_id=approval_id,
            artifact_ids=[artifact.artifact_id],
            trace_id=trace_id,
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(registry.skill_plugin_service, "run_skill", fake_run_skill)

    task = client.post(
        "/api/tasks",
        json={
            "goal": "请帮我在 GitHub 上创建 PR 并请求 review。",
            "auto_start": False,
            "constraints": {
                "code_hosting_request_type": "code_hosting_pr_request",
                "repo_request_type": "repo_patch_request",
                "remote_repo_ref": "github.com/acme/demo",
                "base_branch": "main",
                "target_branch": "feature/api",
                "repo_patch_path": "src/demo.txt",
                "repo_patch_content": "phase98\n",
                "verify_read_path": "src/demo.txt",
                "verify_contains_text": "phase98",
            },
        },
    )
    assert task.status_code == 200, task.text

    started = client.post(f"/api/tasks/{task.json()['task_id']}/start")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "completed"

    replay = client.get(f"/api/tasks/{task.json()['task_id']}/replay").json()
    final_result = replay["final_result"]
    assert final_result["code_hosting_request_type"] == "code_hosting_pr_request"
    assert final_result["forge_provider_type"] == "github"
    assert final_result["pr_summary"]["pr_ref"] == "PR-108"
    assert final_result["remote_artifacts"][0]["ref"] == "PR-108"
    assert final_result["files_changed"]
    assert final_result["verification_summary"]["passed"] is True
    assert replay["workflow_evidence"]["code_hosting"]["skill_binding"]["package_ref"] == (
        "official/github/pr-workflow"
    )


def test_phase98_release_waiting_approval_is_not_deliverable(
    client: TestClient,
    monkeypatch,
) -> None:
    app = cast(Any, client.app)
    registry = cast(ServiceRegistry, app.state.registry)

    async def waiting_run_skill(
        skill_id: str,
        *,
        task_id: str,
        step_id: str,
        owner_member_id: str,
        input_data: dict[str, Any],
        matched_reason: str,
        confidence: float | None,
        approval_id: str | None,
        trace_id: str | None,
    ) -> SkillRunRecord:
        return SkillRunRecord(
            skill_run_id="skrun_phase98_release",
            organization_id="org_default",
            skill_id=skill_id,
            bundle_id="clawhub-github-pr-workflow",
            task_id=task_id,
            step_id=step_id,
            owner_member_id=owner_member_id,
            status="waiting_approval",
            input_redacted=input_data,
            output_redacted={"publish_blockers": ["approval_required"]},
            matched_reason=matched_reason,
            confidence=confidence,
            approval_id="apr_phase98_release",
            trace_id=trace_id,
            created_at=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(registry.skill_plugin_service, "run_skill", waiting_run_skill)

    task = client.post(
        "/api/tasks",
        json={
            "goal": "帮我发布一个 GitHub release。",
            "auto_start": False,
            "constraints": {
                "code_hosting_request_type": "code_hosting_release_request",
                "remote_repo_ref": "github.com/acme/demo",
                "release_kind": "minor",
            },
        },
    )
    assert task.status_code == 200, task.text
    started = client.post(f"/api/tasks/{task.json()['task_id']}/start")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "waiting_approval"

    replay = client.get(f"/api/tasks/{task.json()['task_id']}/replay").json()
    assert "approval_required" in replay["final_result"]["publish_blockers"]
    assert replay["final_result"]["deliverable"] is False


def test_phase98_catalog_search_empty_does_not_fallback_to_builtin_skill(
    client: TestClient,
    monkeypatch,
) -> None:
    app = cast(Any, client.app)
    registry = cast(ServiceRegistry, app.state.registry)

    async def search_empty(**_: Any) -> list[Any]:
        return []

    monkeypatch.setattr(registry.skill_repository_service, "search", search_empty)

    task = client.post(
        "/api/tasks",
        json={
            "goal": "帮我在 GitHub 上创建 PR。",
            "auto_start": False,
            "constraints": {
                "code_hosting_request_type": "code_hosting_pr_request",
                "remote_repo_ref": "github.com/acme/demo",
            },
        },
    )
    assert task.status_code == 200, task.text
    started = client.post(f"/api/tasks/{task.json()['task_id']}/start")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "completed"

    replay = client.get(f"/api/tasks/{task.json()['task_id']}/replay").json()
    assert replay["final_result"]["deliverable"] is False
    assert "clawhub_catalog_search_empty" in replay["final_result"]["publish_blockers"]


def test_phase98_grant_failure_is_reported_as_blocker(
    client: TestClient,
    monkeypatch,
) -> None:
    app = cast(Any, client.app)
    registry = cast(ServiceRegistry, app.state.registry)
    async def fail_create_grant(*args: Any, **kwargs: Any) -> Any:
        raise AppError(ErrorCode.CAPABILITY_DENIED, "grant denied", status_code=403)

    monkeypatch.setattr(registry.skill_governance_service, "create_grant", fail_create_grant)

    task = client.post(
        "/api/tasks",
        json={
            "goal": "帮我在 GitHub 上创建 PR。",
            "auto_start": False,
            "constraints": {
                "code_hosting_request_type": "code_hosting_pr_request",
                "remote_repo_ref": "github.com/acme/demo",
            },
        },
    )
    assert task.status_code == 200, task.text
    started = client.post(f"/api/tasks/{task.json()['task_id']}/start")
    assert started.status_code == 200, started.text

    replay = client.get(f"/api/tasks/{task.json()['task_id']}/replay").json()
    assert replay["final_result"]["deliverable"] is False
    assert "CAPABILITY_DENIED" in replay["final_result"]["publish_blockers"]
