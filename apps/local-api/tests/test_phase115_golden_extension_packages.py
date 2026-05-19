from pathlib import Path

import pytest
from fastapi.testclient import TestClient


_PHASE115_GOLDEN_FIXTURES = [
    (
        "clawhub-xiaohongshu-content-platform",
        "content_platform",
        {
            "platform_display_name": "Xiaohongshu",
            "request_type": "content_platform_publish_request",
            "action_type": "publish_content",
            "account_display_name": "Brand Account",
            "publish_surface": "text_note",
            "selected_asset_id": "asset_demo",
            "selected_handle_id": "handle_demo",
            "title": "Demo Title",
            "body": "Demo Body",
            "tags": ["demo", "release"],
            "comment_text": "first comment",
            "media_artifact_ids": ["art_demo_1"],
        },
    ),
    (
        "clawhub-github-pr-workflow",
        "code_hosting",
        {
            "code_hosting_request_type": "code_hosting_pr_request",
            "remote_repo_ref": "github.com/example/repo",
            "base_branch": "main",
            "target_branch": "feature/demo",
            "pr_ref": "PR-1",
            "review_action": "comment",
            "release_kind": "draft",
        },
    ),
    (
        "clawhub-email-draft",
        "email_draft",
        {
            "content": "Follow up with the customer about timeline and next step.",
        },
    ),
]


@pytest.mark.parametrize(("bundle_id", "intent", "skill_input"), _PHASE115_GOLDEN_FIXTURES)
def test_phase115_golden_fixture_supports_full_extension_loop(
    client: TestClient,
    bundle_id: str,
    intent: str,
    skill_input: dict[str, object],
) -> None:
    root = Path("config/skill-repositories/fixtures") / bundle_id
    preview = client.post(
        "/api/extensions/preview-import",
        json={"source_type": "local_directory", "source_uri": str(root.resolve())},
    )
    assert preview.status_code == 200, preview.text
    preview_json = preview.json()
    assert preview_json["extension_id"] == f"ext.{bundle_id}"
    assert preview_json["bundle_preview"]["package_kind"] == "legacy_bundle"

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root.resolve())},
    )
    assert install.status_code == 200, install.text
    extension_id = install.json()["bundle"]["extension_id"]

    enabled = client.post(f"/api/extensions/{extension_id}/enable", json={})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "enabled"

    diagnostics = client.get(f"/api/extensions/{extension_id}/diagnostics").json()
    assert diagnostics["package_contract"]["contract_version"] == "phase115.golden_extension_package.v1"
    assert diagnostics["package_contract"]["golden_package"] is True
    assert diagnostics["summary"]["missing_bindings"] == []
    assert diagnostics["summary"]["runtime_sync_missing"] is False
    assert diagnostics["summary"]["external_runtime_required"] is False
    assert diagnostics["runtime_snapshot"]["runtime_sync_state"] == "synced"
    assert diagnostics["runtime_snapshot"]["deliverable_proof"]["final_deliverable"] is True

    plan = client.post(
        f"/api/extensions/{extension_id}/plan-run",
        json={"goal": f"run {bundle_id}", "intent": intent},
    )
    assert plan.status_code == 200, plan.text
    plan_json = plan.json()
    assert plan_json["runnable"] is True
    assert plan_json["runnable_state"] == "ready"
    assert plan_json["package_contract"] == diagnostics["package_contract"]
    assert plan_json["runtime_snapshot"] == diagnostics["runtime_snapshot"]

    launched = client.post(
        f"/api/extensions/{extension_id}/tasks",
        json={
            "goal": f"Deliver {bundle_id}",
            "intent": intent,
            "skill_input": skill_input,
            "auto_start": True,
        },
    )
    assert launched.status_code == 200, launched.text
    task = launched.json()
    assert task["status"] == "completed"
    assert task["result"]["domain"] == "extension_ecosystem"
    assert task["result"]["extension_id"] == extension_id
    assert task["result"]["extension_package_contract"]["bundle_id"] == bundle_id
    assert task["result"]["phase111_deliverable_proof"]["proof_status"] == "present"
    assert task["result"]["delivery_status"] == "delivered"
    assert task["result"]["final_deliverable"] is True

    disabled = client.post(
        f"/api/extensions/{extension_id}/disable",
        json={"reason": "phase115 regression"},
    )
    assert disabled.status_code == 200, disabled.text

    diagnostics_after_disable = client.get(f"/api/extensions/{extension_id}/diagnostics").json()
    assert diagnostics_after_disable["runtime_snapshot"]["bundle_status"] == "disabled"
    assert diagnostics_after_disable["runtime_snapshot"]["runtime_sync_state"] == "synced"


def test_phase115_readiness_and_release_summary_expose_golden_package_inventory(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase115 = readiness["phase_readiness"]["phase115_golden_extension_packages"]
    assert phase115["details"]["phase115_contract_version"] == "phase115.golden_extension_packages.v1"
    assert len(phase115["details"]["golden_package_inventory"]) == 3
    assert all(item["importable"] is True for item in phase115["details"]["golden_package_inventory"])

    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    report = client.get(f"/api/release-gates/{gate_id}/report").json()
    summary = report["summary"]["phase115_golden_extension_packages"]
    assert summary["contract_version"] == "phase115.golden_extension_packages.v1"
    assert summary["package_contract_version"] == "phase115.golden_extension_package.v1"
    assert len(summary["golden_package_inventory"]) == 3
    assert summary["inventory_coverage"]["inventory_count"] == 3
    assert len(summary["per_package_lifecycle_status"]) == 3

    dashboard = client.get("/api/system/maturity-dashboard").json()
    extension_dimension = next(
        item for item in dashboard["dimensions"] if item["key"] == "extension"
    )
    assert extension_dimension["contract_version"] == "phase115.golden_extension_packages.v1"
    assert extension_dimension["upstream_phase_keys"] == ["phase115_golden_extension_packages"]
