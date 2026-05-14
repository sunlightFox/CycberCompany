from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
PYTHONPATH_DIRS = [
    "apps/local-cli",
    "apps/local-api",
    "packages/core-types",
    "services/asset-broker",
    "services/brain",
    "services/capability-graph",
    "services/chat-runtime",
    "services/context-gateway",
    "services/heart",
    "services/memory",
    "services/persona-engine",
    "services/response-composer",
    "services/safety",
    "services/shell-runtime",
    "services/skill-engine",
    "services/task-engine",
    "services/tools",
    "services/trace",
]
for candidate in (APP_ROOT, REPO_ROOT, *(REPO_ROOT / item for item in PYTHONPATH_DIRS)):
    text = str(candidate)
    if text not in sys.path:
        sys.path.insert(0, text)

from app.main import create_app


DEFAULT_XHS_SMOKE_ASSET_ID = "ast_c186829909b54fc2ac616eb150968afd"


def _optional_env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _allowed_domains(*urls: str) -> list[str]:
    domains: list[str] = []
    for url in urls:
        host = urlparse(url).netloc.strip().lower()
        if host and host not in domains:
            domains.append(host)
    return domains


def _register_publish_adapter(client: TestClient, *, login_url: str, publish_url: str) -> dict[str, object]:
    domains = _allowed_domains(login_url, publish_url)
    response = client.post(
        "/api/external-platform/adapters",
        json={
            "platform_key": "social_xiaohongshu",
            "adapter_type": "browser",
            "action_type": "publish_content",
            "display_name": "XHS real smoke adapter",
            "status": "active",
            "allowed_domains": domains,
            "metadata": {
                "real_platform_integration": True,
                "playwright_required": True,
                "human_challenge_resume": False,
                "auto_execute_whitelisted_real_accounts": True,
            },
            "manifest": {
                "allowed_domains": domains,
                "real_site_flow": True,
                "skill_binding": {
                    "repository_id": "clawhub",
                    "package_ref": "community/openclaw/xiaohongshu-publish",
                    "source_policy": "repository_with_fixture_fallback",
                    "capabilities": ["publish_browser", "comment_browser"],
                    "fixture_bundle_id": "clawhub-openclaw-xiaohongshu-publish",
                },
                "login_flow": {
                    "login_url": login_url,
                    "post_login_wait_text": _optional_env(
                        "XHS_SMOKE_POST_LOGIN_WAIT_TEXT",
                        "",
                    ),
                    "post_login_wait_url": _optional_env(
                        "XHS_SMOKE_POST_LOGIN_WAIT_URL",
                        "",
                    ),
                    "selectors": {
                        "username": _optional_env(
                            "XHS_SMOKE_LOGIN_USERNAME_SELECTOR",
                            "input[type='text']",
                        ),
                        "password": _optional_env(
                            "XHS_SMOKE_LOGIN_PASSWORD_SELECTOR",
                            "input[type='password']",
                        ),
                        "submit": _optional_env(
                            "XHS_SMOKE_LOGIN_SUBMIT_SELECTOR",
                            "button[type='submit']",
                        ),
                    },
                },
                "publish_flow": {
                    "start_url": publish_url,
                    "wait_until": _optional_env("XHS_SMOKE_PUBLISH_WAIT_UNTIL", "domcontentloaded"),
                    "publish_success_text": _optional_env("XHS_SMOKE_PUBLISH_SUCCESS_TEXT", ""),
                    "selectors": {
                        "title": _optional_env(
                            "XHS_SMOKE_TITLE_SELECTOR",
                            "input[placeholder*='标题']",
                        ),
                        "body": _optional_env(
                            "XHS_SMOKE_BODY_SELECTOR",
                            "div[contenteditable='true']",
                        ),
                        "submit": _optional_env(
                            "XHS_SMOKE_PUBLISH_SUBMIT_SELECTOR",
                            "button:has-text('发布')",
                        ),
                        "form": _optional_env("XHS_SMOKE_PUBLISH_FORM_SELECTOR", ""),
                        "upload": _optional_env("XHS_SMOKE_UPLOAD_SELECTOR", ""),
                    },
                    "verify": {
                        "expected_url": _optional_env("XHS_SMOKE_POST_URL", ""),
                    },
                },
                "comment_flow": {
                    "start_url": _optional_env("XHS_SMOKE_POST_URL", ""),
                    "target_post_url": _optional_env("XHS_SMOKE_POST_URL", ""),
                    "comment_success_text": _optional_env(
                        "XHS_SMOKE_COMMENT_SUCCESS_TEXT",
                        "",
                    ),
                    "recheck_wait_text": _optional_env("XHS_SMOKE_COMMENT_RECHECK_WAIT_TEXT", ""),
                    "selectors": {
                        "comment_box": _optional_env(
                            "XHS_SMOKE_COMMENT_BOX_SELECTOR",
                            "button:has-text('评论')",
                        ),
                        "comment_input": _optional_env(
                            "XHS_SMOKE_COMMENT_INPUT_SELECTOR",
                            "textarea",
                        ),
                        "comment_submit": _optional_env(
                            "XHS_SMOKE_COMMENT_SUBMIT_SELECTOR",
                            "button:has-text('发送')",
                        ),
                        "comment_form": _optional_env(
                            "XHS_SMOKE_COMMENT_FORM_SELECTOR",
                            "",
                        ),
                    },
                    "verify": {
                        "expected_url": _optional_env("XHS_SMOKE_POST_URL", ""),
                    },
                },
                "challenge_detection": {
                    "any_text": [
                        "captcha",
                        "验证",
                        "验证码",
                        "安全验证",
                        "安全限制",
                        "IP存在风险",
                        "异常",
                    ],
                    "not_logged_in_text": ["登录", "立即登录", "手机号登录"],
                },
            },
        },
    )
    if response.status_code != 200:
        raise SystemExit(response.text)
    return dict(response.json()["adapter"])


def _create_plan(
    client: TestClient,
    *,
    publish_text: str,
    title: str,
    comment_text: str,
    target_post_url: str,
    require_full_comment_flow: bool,
) -> dict[str, object]:
    intent = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": "publish this real xiaohongshu note", "member_id": "mem_xiaoyao"},
    )
    if intent.status_code != 200:
        raise SystemExit(intent.text)
    intent_payload = intent.json()["intent"]
    if not intent_payload.get("platform_key"):
        registry = client.app.state.registry
        import anyio

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
            "publish_text": publish_text,
            "title": title,
            "comment_text": comment_text or None,
            "provider_mode": "playwright",
            "target_post_url": target_post_url or None,
            "require_full_comment_flow": require_full_comment_flow,
        },
    )
    if created.status_code != 200:
        raise SystemExit(created.text)
    return dict(created.json()["plan"])


def _approval_payload() -> dict[str, object]:
    reason = _optional_env(
        "XHS_SMOKE_APPROVAL_REASON",
        "codex real xiaohongshu smoke auto-approval",
    )
    return {
        "actor_type": _optional_env("XHS_SMOKE_APPROVAL_ACTOR_TYPE", "user"),
        "actor_id": _optional_env("XHS_SMOKE_APPROVAL_ACTOR_ID", "user_local_owner"),
        "reason": reason,
    }


def _execute_adapter(
    client: TestClient,
    *,
    plan_id: str,
    approval_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "adapter_type": "browser",
        "provider_mode": "playwright",
    }
    if approval_id:
        payload["approval_id"] = approval_id
    execution = client.post(
        f"/api/external-platform/action-plans/{plan_id}/execute-adapter",
        json=payload,
    )
    if execution.status_code != 200:
        raise SystemExit(execution.text)
    return dict(execution.json())


def _approval_id_from_payload(payload: dict[str, object]) -> str:
    plan = payload.get("plan")
    if isinstance(plan, dict):
        value = str(plan.get("approval_id") or "").strip()
        if value:
            return value
    execution = payload.get("execution")
    if isinstance(execution, dict):
        evidence = execution.get("evidence")
        if isinstance(evidence, dict):
            value = str(evidence.get("approval_id") or "").strip()
            if value:
                return value
    return ""


def _simulate_approval(
    client: TestClient,
    *,
    approval_id: str,
) -> dict[str, object]:
    approved = client.post(
        f"/api/approvals/{approval_id}/approve",
        json=_approval_payload(),
    )
    if approved.status_code != 200:
        raise SystemExit(approved.text)
    return dict(approved.json())


def _run_full_smoke(
    client: TestClient,
    *,
    publish_text: str,
    title: str,
    comment_text: str,
    target_post_url: str,
    require_full_comment_flow: bool,
    selected_asset_id: str,
) -> tuple[dict[str, object], str]:
    plan = _create_plan(
        client,
        publish_text=publish_text,
        title=title,
        comment_text=comment_text,
        target_post_url=target_post_url,
        require_full_comment_flow=require_full_comment_flow,
    )
    if selected_asset_id:
        _ensure_selected_account(plan, selected_asset_id=selected_asset_id)
    payload = _execute_adapter(client, plan_id=str(plan["plan_id"]))
    approval_path = "not_required"
    if str(payload.get("next_step") or "") == "approve_or_resume_after_human":
        approval_id = _approval_id_from_payload(payload)
        if not approval_id:
            raise SystemExit("smoke expected approval_id but none was returned")
        _simulate_approval(client, approval_id=approval_id)
        payload = _execute_adapter(client, plan_id=str(plan["plan_id"]), approval_id=approval_id)
        approval_path = "simulated"
    elif str(plan.get("approval_id") or "").strip():
        approval_path = "skipped_or_preapproved"
    return payload, approval_path


def _summary_from_payload(
    payload: dict[str, object],
    *,
    approval_path: str,
) -> dict[str, object]:
    plan = payload.get("plan")
    execution = payload.get("execution")
    plan_data = plan if isinstance(plan, dict) else {}
    execution_data = execution if isinstance(execution, dict) else {}
    evidence = execution_data.get("evidence")
    evidence_data = evidence if isinstance(evidence, dict) else {}
    recovery = evidence_data.get("recovery_evidence")
    recovery_data = recovery if isinstance(recovery, dict) else {}
    return {
        "plan_status": plan_data.get("status"),
        "execution_status": execution_data.get("status"),
        "failure_reason": plan_data.get("failure_reason"),
        "approval_path": approval_path,
        "approval_id": _approval_id_from_payload(payload) or plan_data.get("approval_id"),
        "published_post_url": evidence_data.get("published_post_url"),
        "published_post_id": evidence_data.get("published_post_id"),
        "publish_visible_text_confirmed": evidence_data.get("publish_visible_text_confirmed"),
        "comment_visible_text_confirmed": evidence_data.get("comment_visible_text_confirmed"),
        "publish_and_comment_both_confirmed": evidence_data.get(
            "publish_and_comment_both_confirmed"
        ),
        "recovery_reason_codes": recovery_data.get("reason_codes"),
    }


def _ensure_selected_account(
    plan: dict[str, object],
    *,
    selected_asset_id: str,
) -> None:
    if str(plan.get("selected_asset_id") or "") != selected_asset_id:
        raise SystemExit(
            "smoke selected a different asset than expected: "
            f"{plan.get('selected_asset_id')} != {selected_asset_id}"
        )


def main() -> None:
    login_url = _optional_env("XHS_SMOKE_LOGIN_URL", "https://www.xiaohongshu.com/login")
    publish_url = _optional_env(
        "XHS_SMOKE_PUBLISH_URL",
        "https://creator.xiaohongshu.com/publish/publish",
    )
    target_post_url = _optional_env("XHS_SMOKE_POST_URL", "")
    selected_asset_id = _optional_env("XHS_SMOKE_SELECTED_ASSET_ID", DEFAULT_XHS_SMOKE_ASSET_ID)
    marker = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    title = _optional_env("XHS_SMOKE_TITLE", f"[AUTO-TEST {marker}] XHS smoke")
    body = _optional_env(
        "XHS_SMOKE_BODY",
        f"[AUTO-TEST {marker}] publish body from local-api real smoke",
    )
    comment = _optional_env(
        "XHS_SMOKE_COMMENT",
        f"[AUTO-TEST {marker}] first comment from local-api real smoke",
    )
    require_full_comment_flow = _optional_env(
        "XHS_SMOKE_REQUIRE_FULL_COMMENT_FLOW",
        "true",
    ).lower() in {"1", "true", "yes"}

    with TestClient(create_app()) as client:
        _register_publish_adapter(client, login_url=login_url, publish_url=publish_url)
        payload, approval_path = _run_full_smoke(
            client,
            publish_text=body,
            title=title,
            comment_text=comment,
            target_post_url=target_post_url,
            require_full_comment_flow=require_full_comment_flow,
            selected_asset_id=selected_asset_id,
        )
    summary = _summary_from_payload(payload, approval_path=approval_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    publish_ok = bool(
        summary["plan_status"] == "completed"
        and summary["execution_status"] == "completed"
        and (summary["published_post_url"] or summary["published_post_id"])
        and summary["publish_visible_text_confirmed"]
    )
    comment_ok = bool(summary["publish_and_comment_both_confirmed"])
    if require_full_comment_flow:
        success = comment_ok
    else:
        success = publish_ok
    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
