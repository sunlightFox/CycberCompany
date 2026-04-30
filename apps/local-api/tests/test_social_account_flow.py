from __future__ import annotations

import contextlib
import json
import socket
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi.testclient import TestClient

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
SOCIAL_PAGE = (FIXTURE_DIR / "social_platform.html").read_text(encoding="utf-8")
ARTICLE_BODY = (
    "This article was posted through Asset Broker, Capability Graph, "
    "Safety, Approval, and Tool Runtime."
)


def test_company_account_login_and_publish_social_platform_flow(client: TestClient) -> None:
    with _social_platform() as platform:
        task = client.post(
            "/api/tasks",
            json={
                "goal": "测试公司账号登录本地社交平台并发布文章",
                "auto_start": False,
                "client_request_id": "social-account-flow",
            },
        ).json()
        task_id = task["task_id"]
        asset = client.post(
            "/api/assets",
            json={
                "asset_type": "account",
                "display_name": "Company Social Account",
                "provider": "local_social",
                "sensitivity": "high",
                "secret_value": platform.password,
                "config": {
                    "platform": "local_social",
                    "username": platform.username,
                    "auth_type": "password",
                },
                "summary_text": "Company social account for local publish-flow tests",
                "capabilities": ["login", "draft_post", "publish_post"],
            },
        ).json()
        asset_id = asset["asset_id"]
        for action, risk in [
            ("login", "R2"),
            ("draft_post", "R2"),
            ("publish_post", "R4"),
        ]:
            client.post(
                "/api/assets/grants",
                json={
                    "subject_type": "member",
                    "subject_id": "mem_xiaoyao",
                    "object_type": "asset",
                    "object_id": asset_id,
                    "action": action,
                    "effect": "allow",
                    "risk_level": risk,
                },
            )

        page = client.post(
            "/api/tools/execute",
            json={
                "task_id": task_id,
                "tool_name": "browser.snapshot",
                "args": {"url": platform.url},
            },
        ).json()
        query = client.post(
            "/api/tools/execute",
            json={
                "task_id": task_id,
                "tool_name": "asset.query",
                "args": {
                    "asset_type": "account",
                    "requested_actions": ["login", "draft_post", "publish_post"],
                    "keywords": ["Company Social"],
                },
            },
        ).json()
        handle = query["result"]["handles"][0]
        handle_id = handle["handle_id"]
        login = client.post(
            "/api/tools/execute",
            json={
                "task_id": task_id,
                "tool_name": "account.login",
                "args": {"handle_id": handle_id, "login_url": platform.login_url},
            },
        ).json()
        draft = client.post(
            "/api/tools/execute",
            json={
                "task_id": task_id,
                "tool_name": "account.create_draft_artifact",
                "args": {
                    "handle_id": handle_id,
                    "draft": "# Product note\nLocal-first agent OS publish-flow test.",
                    "display_name": "company-social-draft.md",
                },
            },
        ).json()
        first_publish = client.post(
            "/api/tools/execute",
            json={
                "task_id": task_id,
                "tool_name": "account.publish_post",
                "args": {
                    "handle_id": handle_id,
                    "login_url": platform.login_url,
                    "publish_url": platform.publish_url,
                    "title": "Company account integration test",
                    "body": ARTICLE_BODY,
                },
            },
        ).json()
        approval_id = first_publish["approval"]["approval_id"]
        client.post(
            f"/api/approvals/{approval_id}/approve",
            json={"reason": "approve local social platform publish test"},
        )
        publish = client.post(
            "/api/tools/execute",
            json={
                "task_id": task_id,
                "tool_name": "account.publish_post",
                "approval_id": approval_id,
                "args": {
                    "handle_id": handle_id,
                    "login_url": platform.login_url,
                    "publish_url": platform.publish_url,
                    "title": "Company account integration test",
                    "body": ARTICLE_BODY,
                },
            },
        ).json()
        replay = client.get(f"/api/tasks/{task_id}/replay").json()
        audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

        assert platform.posts == [
            {
                "post_id": "post_1",
                "author": platform.username,
                "title": "Company account integration test",
                "body": ARTICLE_BODY,
            }
        ]
        assert asset["has_secret"] is True
        assert platform.password not in json.dumps(asset, ensure_ascii=False)
        assert "Local Social Platform Test Page" in page["result"]["title"]
        assert handle["allowed_actions"] == ["login", "draft_post"]
        assert handle["approval_required_actions"] == ["publish_post"]
        assert login["result"]["status"] == "authenticated"
        assert draft["artifacts"][0]["display_name"] == "company-social-draft.md"
        assert first_publish["tool_call"]["status"] == "approval_required"
        assert publish["result"]["status"] == "published"
        assert publish["result"]["publish"]["response"]["post_id"] == "post_1"
        assert any(call["tool_name"] == "asset.query" for call in replay["tool_calls"])
        assert any(call["tool_name"] == "account.publish_post" for call in replay["tool_calls"])
        completed_publish = [
            call
            for call in replay["tool_calls"]
            if call["tool_name"] == "account.publish_post" and call["status"] == "completed"
        ]
        assert completed_publish[-1]["resolved_asset_refs"] == [
            {
                "handle_id": handle_id,
                "asset_id": asset_id,
                "asset_type": "account",
                "action": "publish_post",
                "has_secret": True,
            }
        ]
        assert replay["approvals"][0]["status"] == "approved"
        assert platform.password not in json.dumps(replay, ensure_ascii=False)
        assert platform.password not in audit_text


class _Platform:
    def __init__(self, base_url: str) -> None:
        self.url = base_url
        self.login_url = f"{base_url}/login"
        self.publish_url = f"{base_url}/articles"
        self.username = "company_ops"
        self.password = "phase-social-credential"
        self.posts: list[dict[str, str]] = []
        self.sessions: set[str] = set()


@contextlib.contextmanager
def _social_platform():
    port = _free_port()
    platform = _Platform(f"http://127.0.0.1:{port}")
    handler = _handler_for(platform)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield platform
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _handler_for(platform: _Platform):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/" or self.path.startswith("/?"):
                self._send_text(SOCIAL_PAGE)
                return
            if self.path == "/articles":
                self._send_json({"posts": platform.posts})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("content-length", "0"))).decode("utf-8")
            form = {key: values[0] for key, values in parse_qs(body).items()}
            if self.path == "/login":
                self._handle_login(form)
                return
            if self.path == "/articles":
                self._handle_publish(form)
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _handle_login(self, form: dict[str, str]) -> None:
            valid_username = form.get("username") == platform.username
            valid_password = form.get("password") == platform.password
            if not valid_username or not valid_password:
                self._send_json({"status": "denied"}, status=HTTPStatus.UNAUTHORIZED)
                return
            session_id = "session_company_ops"
            platform.sessions.add(session_id)
            self._send_json(
                {"status": "authenticated", "account": platform.username},
                headers={"Set-Cookie": f"session={session_id}; Path=/; HttpOnly"},
            )

        def _handle_publish(self, form: dict[str, str]) -> None:
            cookie = self.headers.get("cookie", "")
            if not any(f"session={session}" in cookie for session in platform.sessions):
                self._send_json({"status": "login_required"}, status=HTTPStatus.UNAUTHORIZED)
                return
            post = {
                "post_id": f"post_{len(platform.posts) + 1}",
                "author": platform.username,
                "title": form.get("title", ""),
                "body": form.get("body", ""),
            }
            platform.posts.append(post)
            self._send_json({"status": "published", **post}, status=HTTPStatus.CREATED)

        def _send_text(self, body: str, *, status: HTTPStatus = HTTPStatus.OK) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_json(
            self,
            body: dict[str, Any],
            *,
            status: HTTPStatus = HTTPStatus.OK,
            headers: dict[str, str] | None = None,
        ) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
