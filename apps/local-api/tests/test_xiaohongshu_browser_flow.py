from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs

import anyio
from core_types import ExternalPlatformActionPlan, ExternalPlatformAdapterStep, RiskLevel
from fastapi.testclient import TestClient
from app.services.external_platform_adapters import (
    ExternalPlatformAdapterService,
    _final_execution_evidence,
    _final_plan_outcome,
    _challenge_auto_actions,
    _missing_real_xiaohongshu_post_identity,
)


def test_xiaohongshu_http_fallback_does_not_claim_publish_success(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _XiaohongshuSite() as site:
        _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
        )
        _register_comment_adapter(client, post_url=site.url("/notes/note-1"), login_url=site.url("/login"))
        account = _create_account(
            client,
            display_name="小红书测试账号",
            test_whitelist=False,
            real_auto_execute=True,
        )
        for action, risk in [
            ("login", RiskLevel.R2),
            ("publish_content", RiskLevel.R4),
            ("comment_content", RiskLevel.R3),
        ]:
            _grant(client, account["asset_id"], action, risk)

        publish_plan = _create_plan(
            client,
            text="帮我在小红书发布文章，内容：阶段测试发文正文",
            execution_mode="browser",
            publish_text="阶段测试发文正文",
        )
        assert publish_plan["status"] == "ready"
        assert publish_plan["approval_id"] is None
        assert publish_plan["task_id"]

        publish_exec = client.post(
            f"/api/external-platform/action-plans/{publish_plan['plan_id']}/execute-adapter",
            json={"adapter_type": "browser"},
        )
        assert publish_exec.status_code == 200, publish_exec.text
        publish_payload = publish_exec.json()

        assert publish_payload["plan"]["status"] == "degraded"
        assert publish_payload["execution"]["status"] == "degraded"
        assert publish_payload["execution"]["evidence"]["publish_recheck"]["status"] == "missing"
        assert {
            item["step_name"]
            for item in publish_payload["steps"]
            if item["status"] == "completed"
        }.issuperset({"fill_login_username", "fill_login_password", "submit_login", "submit_publish"})
        assert site.submissions == []
        assert site.comments == []


def test_xiaohongshu_missing_visible_proof_stays_degraded(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _XiaohongshuSite(note_page_hides_content=True) as site:
        _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
        )
        account = _create_account(
            client,
            display_name="小红书测试账号",
            test_whitelist=False,
            real_auto_execute=True,
        )
        for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
            _grant(client, account["asset_id"], action, risk)
        plan = _create_plan(
            client,
            text="帮我在小红书发布文章，内容：正文不可见测试",
            execution_mode="browser",
            publish_text="正文不可见测试",
        )
        executed = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={"adapter_type": "browser"},
        )
        assert executed.status_code == 200, executed.text
        payload = executed.json()
    assert payload["execution"]["status"] == "degraded"
    assert payload["execution"]["evidence"]["publish_recheck"]["status"] == "missing"


def test_xiaohongshu_real_flow_requires_playwright_backend(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _XiaohongshuSite() as site:
        _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
            real_platform=True,
            human_resume=True,
        )
        account = _create_account(client, display_name="小红书正式账号", test_whitelist=True)
        for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4), ("comment_content", RiskLevel.R3)]:
            _grant(client, account["asset_id"], action, risk)
        plan = _create_plan(
            client,
            text="帮我在小红书发布文章，内容：真实站点强制 Playwright",
            execution_mode="browser",
            publish_text="真实站点强制 Playwright",
            comment_text="真实评论",
            provider_mode="playwright",
            target_post_url=site.url("/notes/note-1"),
        )
        executed = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={"adapter_type": "browser"},
        )
        assert executed.status_code == 200, executed.text
        payload = executed.json()
    assert payload["execution"]["status"] == "failed"
    assert payload["plan"]["failure_reason"] == "playwright_required"


def test_xiaohongshu_human_resume_path_waits_for_manual_recovery(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _XiaohongshuSite(login_challenge=True) as site:
        _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
            human_resume=True,
        )
        account = _create_account(client, display_name="小红书测试账号", test_whitelist=True)
        for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
            _grant(client, account["asset_id"], action, risk)
        plan = _create_plan(
            client,
            text="帮我在小红书发布文章，内容：挑战测试",
            execution_mode="browser",
            publish_text="挑战测试",
        )
        executed = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={"adapter_type": "browser"},
        )
        assert executed.status_code == 200, executed.text
        payload = executed.json()
    assert payload["execution"]["status"] == "awaiting_human"
    assert payload["next_step"] == "human_resume_real_browser_flow"


def test_xiaohongshu_non_whitelist_account_still_requires_approval(
    client: TestClient,
) -> None:
    account = _create_account(client, display_name="小红书正式账号", test_whitelist=False)
    _grant(client, account["asset_id"], "login", RiskLevel.R2)
    _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)
    plan = _create_plan(
        client,
        text="帮我在小红书发布文章，内容：正式账号仍需审批",
        execution_mode="browser",
    )
    assert plan["status"] == "awaiting_approval"
    assert plan["approval_id"]


def test_xiaohongshu_real_whitelist_account_skips_approval_via_explicit_flag(
    client: TestClient,
) -> None:
    account = _create_account(
        client,
        display_name="phase99 real whitelist account",
        test_whitelist=False,
        real_auto_execute=True,
    )
    _grant(client, account["asset_id"], "login", RiskLevel.R2)
    _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)
    plan = _create_plan(
        client,
        text="publish this real xiaohongshu note",
        execution_mode="browser",
        publish_text="phase99 body",
        title="phase99 title",
        comment_text="phase99 first comment",
        provider_mode="playwright",
    )
    assert plan["status"] == "ready"
    assert plan["approval_id"] is None
    assert plan["metadata"]["test_account_approval_bypass"] is True


def test_xiaohongshu_login_verification_fails_closed(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _XiaohongshuSite(login_challenge=True) as site:
        _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
        )
        account = _create_account(client, display_name="小红书测试账号", test_whitelist=True)
        for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
            _grant(client, account["asset_id"], action, risk)
        plan = _create_plan(
            client,
            text="帮我在小红书发布文章，内容：挑战测试",
            execution_mode="browser",
        )
        executed = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/execute-adapter",
            json={"adapter_type": "browser"},
        )
        assert executed.status_code == 200, executed.text
        payload = executed.json()
    assert payload["execution"]["status"] in {"challenge_detected", "awaiting_human"}
    assert payload["plan"]["failure_reason"] == "login_verification_required"


def test_xiaohongshu_challenge_auto_actions_include_low_risk_consent_controls() -> None:
    actions = _challenge_auto_actions({"challenge_detection": {}}, current_url="https://creator.xiaohongshu.com/publish/publish")
    selectors = {item["args"]["selector"] for item in actions}
    assert "button:has-text('同意并继续')" in selectors
    assert "input[type='checkbox'][name*='agree' i]" in selectors


def test_xiaohongshu_auto_remediation_attempt_can_clear_challenge() -> None:
    service = ExternalPlatformAdapterService(
        repo=SimpleNamespace(),
        platform_repo=SimpleNamespace(),
        tool_runtime=SimpleNamespace(),
        approval_service=SimpleNamespace(),
        audit_service=SimpleNamespace(),
        asset_broker=SimpleNamespace(),
        browser_session_service=SimpleNamespace(),
    )
    plan = ExternalPlatformActionPlan(
        plan_id="plan_1",
        intent_id="intent_1",
        organization_id="org_default",
        member_id="mem_xiaoyao",
        platform_key="social_xiaohongshu",
        target_id="target_1",
        action_type="publish_content",
        execution_mode="browser",
        steps=[],
        status="ready",
        risk_level="R4",
        content_summary="publish",
        metadata={"provider_mode": "playwright"},
    )
    step = ExternalPlatformAdapterStep(
        step_id="step_1",
        plan_id=plan.plan_id,
        adapter_id="adapter_1",
        adapter_version_id="version_1",
        step_name="open_publish_entry_session_probe",
        executor="browser",
        tool_name="browser.open",
        input_redacted={"url": "https://creator.xiaohongshu.com/publish/publish"},
    )
    manifest = {"challenge_detection": {"any_text": ["验证", "captcha"]}, "real_site_flow": True}
    adapter = {"adapter_id": "adapter_1", "manifest": manifest, "metadata": {}}
    challenge = {
        "drift_type": "challenge_detected",
        "status": "challenge_detected",
        "reason_code": "adapter_challenge_detected",
        "message": "challenge",
    }
    initial = {"output_redacted": {"url": "https://creator.xiaohongshu.com/publish/publish", "content_preview": "需要验证并同意协议"}}

    calls: list[dict[str, Any]] = []

    async def _fake_execute_browser_remediation_action(**kwargs: Any) -> dict[str, Any]:
        action = kwargs["action"]
        calls.append(action)
        if action["tool_name"] == "browser.snapshot":
            return {
                "tool_name": "browser.snapshot",
                "output_redacted": {
                    "url": "https://creator.xiaohongshu.com/publish/publish?agreed=1",
                    "content_preview": "发布编辑器已加载",
                },
            }
        return {
            "tool_name": action["tool_name"],
            "output_redacted": {"action_status": "completed", "url": action["args"]["url"]},
        }

    service._execute_browser_remediation_action = _fake_execute_browser_remediation_action  # type: ignore[method-assign]
    async def _run() -> dict[str, Any] | None:
        return await service._attempt_challenge_auto_remediation(
            plan=plan,
            adapter=adapter,
            step=step,
            result=initial,
            challenge=challenge,
            trace_id=None,
        )

    remediated = anyio.run(_run)
    assert remediated is not None
    remediation = remediated["output_redacted"]["challenge_auto_remediation"]
    assert remediation["attempted"] is True
    assert remediation["resolved"] is True
    assert calls[0]["tool_name"] in {"browser.check", "browser.click"}


def test_phase99_xiaohongshu_plan_binds_skill_and_writes_draft_artifact(
    client: TestClient,
) -> None:
    _register_publish_adapter(
        client,
        start_url="http://127.0.0.1/publish",
        login_url="http://127.0.0.1/login",
        post_url="http://127.0.0.1/notes/note-1",
        real_platform=True,
    )
    account = _create_account(client, display_name="小红书测试账号", test_whitelist=True)
    for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
        _grant(client, account["asset_id"], action, risk)
    media_artifact_id = anyio.run(_write_text_artifact, client, "phase99-cover.txt", "cover bytes")

    plan = _create_plan(
        client,
        text="post this content to xiaohongshu",
        execution_mode="browser",
        publish_text="phase99 body",
        title="phase99 title",
        tags=["phase99", "launch"],
        media_artifact_ids=[media_artifact_id],
        publish_surface="image_note",
        comment_text="phase99 first comment",
        provider_mode="playwright",
    )

    assert plan["task_id"]
    assert plan["evidence"]["post_draft"]["title"] == "phase99 title"
    assert plan["evidence"]["post_draft"]["media_artifact_ids"] == [media_artifact_id]
    assert plan["evidence"]["publish_candidate"]["media_upload_required"] is True
    assert plan["evidence"]["external_platform_skill"]["ready"] is True
    assert plan["evidence"]["external_platform_skill"]["artifact_ids"]
    assert plan["evidence"]["external_platform_skill"]["workflow_spec"]["publish_flow"]["start_url"]
    assert plan["evidence"]["content_platform_skill"]["ready"] is True
    assert plan["metadata"]["browser_session_handle_id"]
    assert plan["metadata"]["browser_profile_id"]
    assert plan["metadata"]["browser_session_id"]
    assert plan["metadata"]["session_bootstrap_status"] == "created"
    assert plan["metadata"]["login_path"] == "session_reuse"
    assert plan["evidence"]["browser_session"]["bootstrap_status"] == "created"

    artifact_id = plan["evidence"]["external_platform_skill"]["artifact_ids"][0]
    artifact = client.get(f"/api/artifacts/{artifact_id}")
    assert artifact.status_code == 200, artifact.text
    downloaded = client.get(f"/api/artifacts/{artifact_id}/download")
    assert downloaded.status_code == 200, downloaded.text
    content = downloaded.text
    assert "phase99 title" in content
    assert media_artifact_id in content


def test_phase99_real_xiaohongshu_compile_includes_media_upload_steps(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _XiaohongshuSite() as site:
        _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
            real_platform=True,
            human_resume=True,
        )
        account = _create_account(client, display_name="小红书测试账号", test_whitelist=True)
        for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
            _grant(client, account["asset_id"], action, risk)
        media_artifact_id = anyio.run(_write_text_artifact, client, "phase99-image.txt", "image bytes")
        plan = _create_plan(
            client,
            text="publish this image note to xiaohongshu",
            execution_mode="browser",
            publish_text="phase99 image body",
            title="phase99 image title",
            media_artifact_ids=[media_artifact_id],
            comment_text="phase99 image comment",
            provider_mode="playwright",
            target_post_url=site.url("/notes/note-1"),
        )
        compiled = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/compile",
            json={"adapter_type": "browser"},
        )
        assert compiled.status_code == 200, compiled.text
        step_names = [item["step_name"] for item in compiled.json()["steps"]]
        assert "open_login_page" in step_names
        assert "detect_existing_session_state" not in step_names
        assert "verify_publish_editor_loaded" in step_names
        assert "upload_media_1" in step_names
        assert "verify_media_upload_1" in step_names
        assert "submit_comment" not in step_names


def test_phase99_real_xiaohongshu_compile_uses_dedicated_comment_submit_selector(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "http_fallback")
    with _XiaohongshuSite() as site:
        _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=site.url("/notes/note-1"),
            real_platform=True,
            human_resume=True,
        )
        account = _create_account(client, display_name="phase99 selector account", test_whitelist=True)
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
            require_full_comment_flow=True,
        )
        compiled = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/compile",
            json={"adapter_type": "browser"},
        )
        assert compiled.status_code == 200, compiled.text
        steps = compiled.json()["steps"]
        submit_comment = next(item for item in steps if item["step_name"] == "submit_comment")
        fill_comment = next(item for item in steps if item["step_name"] == "fill_comment_content")
        open_comment = next(item for item in steps if item["step_name"] == "open_comment_box")
        assert open_comment["input_redacted"]["selector"] == "#comment-box"
        assert fill_comment["input_redacted"]["selector"] == "#comment-input"
        assert submit_comment["input_redacted"]["selector"] == "#comment-submit"


def test_phase99_real_xiaohongshu_compile_threads_captured_post_url_into_comment_steps(
    client: TestClient,
) -> None:
    with _XiaohongshuSite() as site:
        _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=None,
            real_platform=True,
            human_resume=True,
        )
        account = _create_account(client, display_name="phase99 threaded url account", test_whitelist=True)
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
            require_full_comment_flow=True,
        )
        compiled = client.post(
            f"/api/external-platform/action-plans/{plan['plan_id']}/compile",
            json={"adapter_type": "browser"},
        )
    assert compiled.status_code == 200, compiled.text
    steps_by_name = {
        item["step_name"]: item.get("input_redacted") or {}
        for item in compiled.json()["steps"]
    }
    for name in (
        "reopen_post_for_publish_recheck",
        "assert_post_content_visible",
        "open_comment_box",
        "fill_comment_content",
        "submit_comment",
        "reopen_post_for_comment_recheck",
        "assert_comment_visible",
    ):
        assert steps_by_name[name]["url"] == "__published_post_url__"
    assert steps_by_name["capture_post_url_or_post_id"]["capture_post_identity"] is True


def test_phase99_real_xiaohongshu_adapter_requires_comment_flow_manifest(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/external-platform/adapters",
        json={
            "platform_key": "social_xiaohongshu",
            "adapter_type": "browser",
            "action_type": "publish_content",
            "display_name": "Broken XHS real adapter",
            "status": "active",
            "allowed_domains": ["www.xiaohongshu.com"],
            "metadata": {
                "real_platform_integration": True,
                "playwright_required": True,
            },
            "manifest": {
                "allowed_domains": ["www.xiaohongshu.com"],
                "real_site_flow": True,
                "login_flow": {
                    "login_url": "https://www.xiaohongshu.com/login",
                    "selectors": {
                        "username": "#username",
                        "password": "#password",
                        "submit": "#submit",
                    },
                },
                "publish_flow": {
                    "start_url": "https://creator.xiaohongshu.com/publish/publish",
                    "selectors": {
                        "title": "#title",
                        "body": "#body",
                        "submit": "#submit",
                    },
                },
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["validation"]["valid"] is True
    issues = payload["validation"]["issues"]
    assert not any(item["code"] == "comment_flow_missing" for item in issues)


def test_phase99_real_xiaohongshu_plan_requires_comment_text(
    client: TestClient,
) -> None:
    account = _create_account(client, display_name="phase99 xhs account", test_whitelist=True)
    for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
        _grant(client, account["asset_id"], action, risk)
    intent = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": "publish this image note to xiaohongshu", "member_id": "mem_xiaoyao"},
    )
    assert intent.status_code == 200, intent.text
    intent_payload = intent.json()["intent"]
    if not intent_payload.get("platform_key"):
        registry = client.app.state.registry
        anyio.run(
            registry.external_platform.update_intent,
            intent_payload["intent_id"],
            {
                "platform_key": "social_xiaohongshu",
                "action_type": "publish_content",
                "status": "resolved",
                "missing_fields": [],
                "confidence": 0.99,
            },
        )
    created = client.post(
        "/api/external-platform/action-plans",
        json={
            "intent_id": intent_payload["intent_id"],
            "execution_mode": "browser",
            "publish_text": "phase99 image body",
            "title": "phase99 image title",
            "media_artifact_ids": ["art_phase99"],
            "publish_surface": "image_note",
            "provider_mode": "playwright",
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["plan"]["status"] == "ready"
    assert payload["plan"]["failure_reason"] is None


def test_phase99_real_xiaohongshu_full_comment_mode_requires_comment_text(
    client: TestClient,
) -> None:
    account = _create_account(client, display_name="phase99 xhs full-flow account", test_whitelist=True)
    for action, risk in [("login", RiskLevel.R2), ("publish_content", RiskLevel.R4)]:
        _grant(client, account["asset_id"], action, risk)
    intent = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": "publish this image note to xiaohongshu", "member_id": "mem_xiaoyao"},
    )
    assert intent.status_code == 200, intent.text
    intent_payload = intent.json()["intent"]
    if not intent_payload.get("platform_key"):
        registry = client.app.state.registry
        anyio.run(
            registry.external_platform.update_intent,
            intent_payload["intent_id"],
            {
                "platform_key": "social_xiaohongshu",
                "action_type": "publish_content",
                "status": "resolved",
                "missing_fields": [],
                "confidence": 0.99,
            },
        )
    created = client.post(
        "/api/external-platform/action-plans",
        json={
            "intent_id": intent_payload["intent_id"],
            "execution_mode": "browser",
            "publish_text": "phase99 image body",
            "title": "phase99 image title",
            "media_artifact_ids": ["art_phase99"],
            "publish_surface": "image_note",
            "provider_mode": "playwright",
            "require_full_comment_flow": True,
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["plan"]["status"] == "awaiting_clarification"
    assert payload["plan"]["failure_reason"] == "comment_text_required"
    assert payload["next_step"] == "ask_user_for_missing_fields"


def test_phase99_real_xiaohongshu_completion_requires_publish_and_comment_confirmation(
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
        account = _create_account(client, display_name="phase99 real account", test_whitelist=True)
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
            require_full_comment_flow=True,
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
                    "published_post_url": site.url("/notes/note-1"),
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
            {
                "step_name": "assert_comment_visible",
                "verification": {
                    "comment_visible_text_confirmed": True,
                    "visible_excerpt": "phase99 first comment",
                },
            },
        ],
        completed_step_ids=[
            "step_capture",
            "step_publish_recheck",
            "step_submit_comment",
            "step_comment_recheck",
        ],
    )
    final_status, failure_reason, next_step = _final_plan_outcome(
        plan=plan_model,
        adapter=adapter,
        final_evidence=final_evidence,
    )
    assert final_status == "completed"
    assert failure_reason is None
    assert next_step is None
    assert final_evidence["publish_and_comment_both_confirmed"] is True
    assert final_evidence["verification_evidence"]["url_identity_confirmation"]["status"] == "confirmed"
    assert final_evidence["browser_execution_summary"]["verification_outcome"] == "confirmed"


def test_phase99_real_xiaohongshu_missing_comment_confirmation_waits_for_human(
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
        account = _create_account(client, display_name="phase99 partial account", test_whitelist=True)
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
            require_full_comment_flow=True,
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
                    "published_post_url": site.url("/notes/note-1"),
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
            {
                "step_name": "submit_comment",
            },
        ],
        completed_step_ids=["step_capture", "step_publish_recheck", "step_submit_comment"],
    )
    final_status, failure_reason, next_step = _final_plan_outcome(
        plan=plan_model,
        adapter=adapter,
        final_evidence=final_evidence,
    )
    assert final_status == "awaiting_human"
    assert failure_reason == "comment_recheck_missing"
    assert next_step == "human_resume_real_browser_flow"
    assert final_evidence["comment_visible_text_confirmed"] is False
    assert final_evidence["verification_evidence"]["recovery_evidence"]["reason_codes"][0] == "comment_recheck_missing"
    assert final_evidence["browser_execution_summary"]["human_intervention_required"] is True


def test_phase99_real_xiaohongshu_publish_confirmation_is_enough_by_default(
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
        account = _create_account(client, display_name="phase99 publish-only account", test_whitelist=True)
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
                    "published_post_url": site.url("/notes/note-1"),
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
    assert final_evidence["verification_evidence"]["visible_text_confirmation"]["publish"]["status"] == "confirmed"


def test_phase99_real_xiaohongshu_missing_post_identity_waits_for_human(
    client: TestClient,
) -> None:
    with _XiaohongshuSite() as site:
        adapter = _register_publish_adapter(
            client,
            start_url=site.url("/publish"),
            login_url=site.url("/login"),
            post_url=None,
            real_platform=True,
            human_resume=True,
        )
        account = _create_account(client, display_name="phase99 identity account", test_whitelist=True)
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
        )
        plan_model = ExternalPlatformActionPlan(
            **anyio.run(client.app.state.registry.external_platform.get_plan, plan["plan_id"])
        )
    identity_problem = _missing_real_xiaohongshu_post_identity(
        plan=plan_model,
        adapter=adapter,
        step=SimpleNamespace(step_name="capture_post_url_or_post_id"),
        result={"verification": {}, "output_redacted": {"url": site.url("/published"), "text": "publish complete"}},
    )
    assert identity_problem is not None
    assert identity_problem["reason_code"] == "published_post_identity_missing"


def _create_plan(
    client: TestClient,
    *,
    text: str,
    execution_mode: str,
    publish_text: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    media_artifact_ids: list[str] | None = None,
    publish_surface: str | None = None,
    target_post_url: str | None = None,
    comment_text: str | None = None,
    provider_mode: str | None = None,
    require_full_comment_flow: bool = False,
) -> dict[str, Any]:
    intent = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": text, "member_id": "mem_xiaoyao"},
    )
    assert intent.status_code == 200, intent.text
    intent_payload = intent.json()["intent"]
    if not intent_payload.get("platform_key") and "xiaohongshu" in text.lower():
        registry = client.app.state.registry
        anyio.run(
            registry.external_platform.update_intent,
            intent_payload["intent_id"],
            {
                "platform_key": "social_xiaohongshu",
                "action_type": "publish_content",
                "status": "resolved",
                "missing_fields": [],
                "confidence": 0.99,
            },
        )
    created = client.post(
        "/api/external-platform/action-plans",
        json={
            "intent_id": intent_payload["intent_id"],
            "execution_mode": execution_mode,
            "publish_text": publish_text,
            "title": title,
            "tags": tags or [],
            "media_artifact_ids": media_artifact_ids or [],
            "publish_surface": publish_surface,
            "target_post_url": target_post_url,
            "comment_text": comment_text,
            "provider_mode": provider_mode,
            "require_full_comment_flow": require_full_comment_flow,
        },
    )
    assert created.status_code == 200, created.text
    return dict(created.json()["plan"])


def _create_account(
    client: TestClient,
    *,
    display_name: str,
    test_whitelist: bool,
    real_auto_execute: bool = False,
) -> dict[str, Any]:
    response = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": display_name,
            "provider": "social_xiaohongshu",
            "sensitivity": "high",
            "config": {
                "platform": "social_xiaohongshu",
                "username": "xhs_test_user",
                "auth_type": "password",
            },
            "secret_value": "xhs-test-password",
            "owner_scope_type": "member",
            "owner_scope_id": "mem_xiaoyao",
            "visibility": "private",
            "risk_level": "R4",
            "summary_text": f"{display_name} account",
            "capabilities": ["login", "publish_content", "publish_post", "comment_content"],
            "metadata": {
                "platform": "social_xiaohongshu",
                "test_account_auto_approve_external_actions": test_whitelist,
                "auto_execute_whitelisted_real_accounts": real_auto_execute,
                "real_platform_auto_execute_whitelist": (
                    ["social_xiaohongshu"] if real_auto_execute else []
                ),
            },
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _grant(client: TestClient, asset_id: str, action: str, risk: RiskLevel) -> dict[str, Any]:
    response = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": action,
            "effect": "allow",
            "risk_level": risk.value,
            "source_type": "xhs_test",
            "source_id": asset_id,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _register_publish_adapter(
    client: TestClient,
    *,
    start_url: str,
    login_url: str,
    post_url: str | None,
    real_platform: bool = False,
    human_resume: bool = False,
) -> dict[str, Any]:
    publish_flow: dict[str, Any] = {
        "start_url": start_url,
        "default_title": "阶段测试标题",
        "selectors": {
            "upload": "#upload",
            "title": "#title",
            "body": "#body",
            "form": "#publish-form",
            "submit": "#publish-form",
        },
    }
    comment_flow: dict[str, Any] = {
        "start_url": post_url or start_url,
        "selectors": {
            "comment_box": "#comment-box",
            "comment_input": "#comment-input",
            "comment_submit": "#comment-submit",
            "comment_form": "#comment-form",
        },
        "verify": {"expected_url": post_url} if post_url else {},
        "comment_success_text": "comment success",
        "recheck_wait_text": "comment",
    }
    if post_url:
        publish_flow["target_post_url"] = post_url
        publish_flow["verify"] = {"expected_url": post_url}
    response = client.post(
        "/api/external-platform/adapters",
        json={
            "platform_key": "social_xiaohongshu",
            "adapter_type": "browser",
            "action_type": "publish_content",
            "display_name": "XHS publish adapter",
            "status": "active",
            "allowed_domains": ["127.0.0.1"],
            "metadata": {
                "real_platform_integration": real_platform,
                "playwright_required": real_platform,
                "human_challenge_resume": human_resume,
                "auto_execute_whitelisted_real_accounts": real_platform,
            },
            "manifest": {
                "allowed_domains": ["127.0.0.1"],
                "real_site_flow": real_platform,
                "skill_binding": {
                    "repository_id": "clawhub",
                    "package_ref": "community/openclaw/xiaohongshu-publish",
                    "source_policy": "repository_with_fixture_fallback",
                    "capabilities": ["publish_browser", "comment_browser"],
                    "fixture_bundle_id": "clawhub-openclaw-xiaohongshu-publish",
                },
                "login_flow": {
                    "login_url": login_url,
                    "selectors": {
                        "username": "#username",
                        "password": "#password",
                        "form": "#login-form",
                        "submit": "#login-form",
                    },
                },
                "publish_flow": publish_flow,
                "comment_flow": comment_flow,
                "challenge_detection": {"any_text": ["captcha", "验证"], "not_logged_in_text": ["未登录"]},
            },
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json()["adapter"])


def _register_comment_adapter(client: TestClient, *, post_url: str, login_url: str) -> None:
    response = client.post(
        "/api/external-platform/adapters",
        json={
            "platform_key": "social_xiaohongshu",
            "adapter_type": "browser",
            "action_type": "comment_content",
            "display_name": "XHS comment adapter",
            "status": "active",
            "allowed_domains": ["127.0.0.1"],
            "manifest": {
                "allowed_domains": ["127.0.0.1"],
                "login_flow": {
                    "login_url": login_url,
                    "selectors": {
                        "username": "#username",
                        "password": "#password",
                        "form": "#login-form",
                        "submit": "#login-form",
                    },
                },
                "comment_flow": {
                    "start_url": post_url,
                    "selectors": {
                        "comment_box": "#comment-box",
                        "comment_input": "#comment",
                        "form": "#comment-form",
                        "submit": "#comment-form",
                    },
                    "verify": {"expected_url": post_url},
                },
                "challenge_detection": {"any_text": ["captcha", "验证"], "not_logged_in_text": ["未登录"]},
            },
        },
    )
    assert response.status_code == 200, response.text
class _XiaohongshuSite:
    def __init__(
        self,
        *,
        login_challenge: bool = False,
        note_page_hides_content: bool = False,
        consent_challenge: bool = False,
    ) -> None:
        self.login_challenge = login_challenge
        self.note_page_hides_content = note_page_hides_content
        self.consent_challenge = consent_challenge
        self.logins: list[dict[str, str]] = []
        self.submissions: list[dict[str, str]] = []
        self.comments: list[dict[str, str]] = []

    def __enter__(self) -> "_XiaohongshuSite":
        handler = _handler_for(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._server.server_port}{path}"


def _handler_for(site: _XiaohongshuSite) -> type[BaseHTTPRequestHandler]:
    class _XhsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/login":
                if site.login_challenge:
                    self._send_html("<html><body>captcha verification required</body></html>")
                    return
                self._send_html(
                    """
                    <html><body>
                      <form id="login-form" method="post" action="/login">
                        <input id="username" name="username" value="">
                        <input id="password" name="password" value="">
                        <button id="login-submit" type="submit">login</button>
                      </form>
                    </body></html>
                    """
                )
                return
            if self.path.startswith("/publish"):
                if site.consent_challenge and "agreed=1" not in self.path:
                    self._send_html(
                        """
                        <html><body>
                          <section id="consent-gate">
                            <p>安全验证，请先同意协议</p>
                            <form id="consent-form" method="get" action="/publish">
                              <label><input id="agree-box" type="checkbox" name="agreed" value="1">同意协议</label>
                              <button id="agree-submit" type="submit">同意并继续</button>
                            </form>
                          </section>
                        </body></html>
                        """
                    )
                    return
                self._send_html(
                    """
                    <html><body>
                      <form id="publish-form" method="post" action="/published">
                        <input id="upload" name="upload" type="file">
                        <input id="title" name="title" value="">
                        <textarea id="body" name="body"></textarea>
                        <button id="publish-submit" type="submit">publish</button>
                      </form>
                    </body></html>
                    """
                )
                return
            if self.path == "/notes/note-1":
                latest_body = site.submissions[-1]["body"] if site.submissions else ""
                comments_html = "".join(
                    f"<li class='comment-item'>{item['comment']}</li>" for item in site.comments
                )
                visible_body = "" if site.note_page_hides_content else latest_body
                self._send_html(
                    f"""
                    <html><body>
                      <article id="note-body">{visible_body}</article>
                      <button id="comment-box" type="button">comment</button>
                      <form id="comment-form" method="post" action="/notes/note-1/commented">
                        <textarea id="comment" name="comment"></textarea>
                        <button id="comment-submit" type="submit">send</button>
                      </form>
                      <ul id="comments">{comments_html}</ul>
                    </body></html>
                    """
                )
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or "0")
            data = parse_qs(self.rfile.read(length).decode("utf-8"))
            if self.path == "/login":
                site.logins.append(
                    {
                        "username": data.get("username", [""])[0],
                        "password": data.get("password", [""])[0],
                    }
                )
                self._send_html("<html><body>login ok</body></html>")
                return
            if self.path == "/published":
                site.submissions.append(
                    {
                        "title": data.get("title", [""])[0],
                        "body": data.get("body", [""])[0],
                        "post_url": self.server_base + "/notes/note-1",
                    }
                )
                self._send_html("<html><body>published post_id=note-1</body></html>")
                return
            if self.path == "/notes/note-1/commented":
                site.comments.append(
                    {"post_id": "note-1", "comment": data.get("comment", [""])[0]}
                )
                self._send_html("<html><body>comment success</body></html>")
                return
            self.send_response(404)
            self.end_headers()

        @property
        def server_base(self) -> str:
            return f"http://127.0.0.1:{self.server.server_port}"

        def _send_html(self, html: str) -> None:
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return _XhsHandler


async def _write_text_artifact(client: TestClient, display_name: str, content: str) -> str:
    registry = client.app.state.registry
    task_response = client.post(
        "/api/tasks",
        json={
            "conversation_id": "conv_phase99_artifacts",
            "owner_member_id": "mem_xiaoyao",
            "goal": "phase99 artifact helper",
            "resource_handle_ids": [],
        },
    )
    assert task_response.status_code == 200, task_response.text
    task = task_response.json()
    artifact = await registry.artifact_store.write_text(
        task_id=task["task_id"],
        organization_id="org_default",
        display_name=display_name,
        content=content,
    )
    return artifact.artifact_id
