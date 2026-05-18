from __future__ import annotations

import anyio
from core_types import ExternalPlatformActionPlan, RiskLevel
from fastapi.testclient import TestClient

from app.services.external_platform_adapters import (
    _final_execution_evidence,
    _final_plan_outcome,
)
from tests.test_xiaohongshu_browser_flow import (
    _XiaohongshuSite,
    _create_account,
    _create_plan,
    _grant,
    _register_publish_adapter,
)


def test_phase99_content_platform_visible_proof_marks_deliverable(client: TestClient) -> None:
    with _XiaohongshuSite() as site:
        adapter = _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
            real_platform=True,
            human_resume=True,
        )
        account = _create_account(
            client,
            display_name="phase99 closure content platform ok",
            test_whitelist=True,
        )
        for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
            _grant(client, account["asset_id"], action, risk)
        plan = _create_plan(
            client,
            text="publish this real xiaohongshu note",
            execution_mode="browser",
            publish_text="phase99 body",
            title="phase99 title",
            comment_text="phase99 first comment",
            provider_mode="playwright",
            target_post_url=site.url("/notes/note-1"),
        )
        plan_model = ExternalPlatformActionPlan(
            **anyio.run(client.app.state.registry.external_platform.get_plan, plan["plan_id"])
        )

    final_evidence = _final_execution_evidence(
        plan=plan_model,
        adapter=adapter,
        evidence_items=[
            {
                "step_name": "capture_post_url_or_post_id",
                "verification": {
                    "published_post_url": "https://example.com/note-1",
                    "published_post_id": "note-1",
                },
            },
            {
                "step_name": "assert_post_content_visible",
                "verification": {
                    "publish_visible_text_confirmed": True,
                    "visible_excerpt": "phase99 body",
                },
            },
        ],
        completed_step_ids=["step_capture", "step_publish_recheck"],
    )
    final_status, failure_reason, next_step = _final_plan_outcome(
        plan=plan_model,
        adapter=adapter,
        final_evidence=final_evidence,
    )

    assert final_status == "completed"
    assert failure_reason is None
    assert next_step is None
    assert final_evidence["publish_visible_text_confirmed"] is True
    assert final_evidence["browser_execution_summary"]["verification_outcome"] == "confirmed"


def test_phase99_content_platform_missing_visible_proof_is_not_deliverable(
    client: TestClient,
) -> None:
    with _XiaohongshuSite() as site:
        adapter = _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
            real_platform=True,
            human_resume=True,
        )
        account = _create_account(
            client,
            display_name="phase99 closure content platform partial",
            test_whitelist=True,
        )
        for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
            _grant(client, account["asset_id"], action, risk)
        plan = _create_plan(
            client,
            text="publish this real xiaohongshu note",
            execution_mode="browser",
            publish_text="phase99 body",
            title="phase99 title",
            comment_text="phase99 first comment",
            provider_mode="playwright",
            target_post_url=site.url("/notes/note-1"),
        )
        plan_model = ExternalPlatformActionPlan(
            **anyio.run(client.app.state.registry.external_platform.get_plan, plan["plan_id"])
        )

    final_evidence = _final_execution_evidence(
        plan=plan_model,
        adapter=adapter,
        evidence_items=[
            {
                "step_name": "capture_post_url_or_post_id",
                "verification": {
                    "published_post_url": "https://example.com/note-1",
                    "published_post_id": "note-1",
                },
            }
        ],
        completed_step_ids=["step_capture"],
    )
    final_status, failure_reason, next_step = _final_plan_outcome(
        plan=plan_model,
        adapter=adapter,
        final_evidence=final_evidence,
    )

    assert final_status == "awaiting_human"
    assert failure_reason in {
        "publish_recheck_missing",
        "comment_recheck_missing",
        "publish_visible_text_missing",
    }
    assert next_step == "human_resume_real_browser_flow"
    assert final_evidence["publish_visible_text_confirmed"] is False
