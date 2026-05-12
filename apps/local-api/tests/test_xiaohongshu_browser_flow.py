from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from core_types import RiskLevel
from fastapi.testclient import TestClient


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
        account = _create_account(client, display_name="小红书测试账号", test_whitelist=True)
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
        account = _create_account(client, display_name="小红书测试账号", test_whitelist=True)
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


def _create_plan(
    client: TestClient,
    *,
    text: str,
    execution_mode: str,
    publish_text: str | None = None,
    target_post_url: str | None = None,
    comment_text: str | None = None,
    provider_mode: str | None = None,
) -> dict[str, Any]:
    intent = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": text, "member_id": "mem_xiaoyao"},
    )
    assert intent.status_code == 200, intent.text
    created = client.post(
        "/api/external-platform/action-plans",
        json={
            "intent_id": intent.json()["intent"]["intent_id"],
            "execution_mode": execution_mode,
            "publish_text": publish_text,
            "target_post_url": target_post_url,
            "comment_text": comment_text,
            "provider_mode": provider_mode,
        },
    )
    assert created.status_code == 200, created.text
    return dict(created.json()["plan"])


def _create_account(client: TestClient, *, display_name: str, test_whitelist: bool) -> dict[str, Any]:
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
    post_url: str,
    real_platform: bool = False,
    human_resume: bool = False,
) -> None:
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
            },
            "manifest": {
                "allowed_domains": ["127.0.0.1"],
                "real_site_flow": real_platform,
                "login_flow": {
                    "login_url": login_url,
                    "selectors": {
                        "username": "#username",
                        "password": "#password",
                        "form": "#login-form",
                        "submit": "#login-form",
                    },
                },
                "publish_flow": {
                    "start_url": start_url,
                    "default_title": "阶段测试标题",
                    "selectors": {
                        "title": "#title",
                        "body": "#body",
                        "form": "#publish-form",
                        "submit": "#publish-form",
                    },
                    "target_post_url": post_url,
                    "verify": {"expected_url": post_url},
                },
                "challenge_detection": {"any_text": ["captcha", "验证"], "not_logged_in_text": ["未登录"]},
            },
        },
    )
    assert response.status_code == 200, response.text


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
    def __init__(self, *, login_challenge: bool = False, note_page_hides_content: bool = False) -> None:
        self.login_challenge = login_challenge
        self.note_page_hides_content = note_page_hides_content
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
            if self.path == "/publish":
                self._send_html(
                    """
                    <html><body>
                      <form id="publish-form" method="post" action="/published">
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
