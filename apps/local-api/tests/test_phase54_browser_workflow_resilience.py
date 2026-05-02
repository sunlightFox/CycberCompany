from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs

from fastapi.testclient import TestClient


def test_phase54_js_iframe_shadow_modal_tab_mobile_and_replay(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "playwright")
    with _ResilienceSite() as site:
        js_pending = _execute(
            client,
            text=f"填写动态表单 {site.url('/js-delay')}",
            target_url=site.url("/js-delay"),
            action_type="fill_form",
            form_data={"title": "动态标题", "content": "布洛芬库存说明"},
        )
        assert js_pending["execution"]["status"] == "awaiting_approval"
        assert any(step["step_type"] == "wait_for_js" for step in js_pending["steps"])

        iframe_pending = _execute(
            client,
            text=f"填写 iframe 表单 {site.url('/iframe')}",
            target_url=site.url("/iframe"),
            action_type="fill_form",
            form_data={"title": "Frame 标题", "content": "Frame 内容"},
        )
        assert iframe_pending["execution"]["status"] == "awaiting_approval"
        iframe_refs = str(iframe_pending)
        assert "frame_count" in iframe_refs

        shadow_pending = _execute(
            client,
            text=f"填写 shadow 表单 {site.url('/shadow')}",
            target_url=site.url("/shadow"),
            action_type="fill_form",
            form_data={"title": "Shadow 标题", "content": "Shadow 内容"},
        )
        assert shadow_pending["execution"]["status"] == "awaiting_approval"

        modal_pending = _execute(
            client,
            text=f"打开弹层并发草稿 {site.url('/modal')}",
            target_url=site.url("/modal"),
            action_type="fill_form",
            form_data={"title": "弹层标题", "content": "弹层内容"},
        )
        assert modal_pending["execution"]["status"] == "awaiting_approval"
        assert any(step["step_type"] == "open_entry" for step in modal_pending["steps"])

        tab_pending = _execute(
            client,
            text=f"新标签页发草稿 {site.url('/new-tab')}",
            target_url=site.url("/new-tab"),
            action_type="fill_form",
            form_data={"title": "新页标题", "content": "新页内容"},
        )
        assert tab_pending["execution"]["status"] == "awaiting_approval"
        assert "tab_count" in str(tab_pending)

        mobile_pending = _execute(
            client,
            text=f"移动端入口填写 {site.url('/mobile-only')}",
            target_url=site.url("/mobile-only"),
            action_type="fill_form",
            form_data={"title": "移动标题", "content": "移动内容"},
            provider_mode="playwright",
        )
        assert mobile_pending["execution"]["status"] == "awaiting_approval"
        assert mobile_pending["plan"]["metadata"]["mobile_fallback_used"] is True

        replay = client.get(f"/api/browser-workflows/plans/{tab_pending['plan']['plan_id']}/replay")
        assert replay.status_code == 200, replay.text
        replay_payload = replay.json()
        assert replay_payload["redaction_summary"][
            "phase54_frame_tab_console_network_summarized"
        ] is True
        assert _leakage_count(replay_payload) == 0


def test_phase54_challenge_resume_candidate_and_release_contracts(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("CYCBER_BROWSER_EXECUTOR", "playwright")
    with _ResilienceSite() as site:
        challenge_plan = _plan(
            client,
            text=f"处理挑战后填写 {site.url('/challenge')}",
            target_url=site.url("/challenge"),
            action_type="fill_form",
            form_data={"title": "恢复标题", "content": "恢复内容"},
        )
        challenged = client.post(
            f"/api/browser-workflows/plans/{challenge_plan['plan_id']}/execute",
            json={"provider_mode": "playwright"},
        )
        assert challenged.status_code == 200, challenged.text
        assert challenged.json()["execution"]["status"] == "challenge_detected"

        resumed = client.post(
            f"/api/browser-workflows/plans/{challenge_plan['plan_id']}/resume-after-human",
            json={
                "human_resolution": {"current_url": site.url("/resolved")},
                "provider_mode": "playwright",
            },
        )
        assert resumed.status_code == 200, resumed.text
        resumed_payload = resumed.json()
        assert resumed_payload["execution"]["status"] == "awaiting_approval"
        assert resumed_payload["plan"]["status"] == "awaiting_approval"

        approval_id = resumed_payload["plan"]["approval_id"]
        client.post(f"/api/approvals/{approval_id}/approve", json={"reason": "phase54"})
        completed = client.post(
            f"/api/browser-workflows/plans/{challenge_plan['plan_id']}/resume-after-human",
            json={"approval_id": approval_id, "provider_mode": "playwright"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["execution"]["status"] == "completed"
        assert completed.json()["candidate"]["manifest"]["phase"] == (
            "phase54_browser_workflow_resilience"
        )

        providers = client.post(
            "/api/tools/execute",
            json={
                "task_id": completed.json()["plan"]["task_id"],
                "member_id": "mem_xiaoyao",
                "tool_name": "browser.snapshot",
                "args": {
                    "url": site.url("/resolved"),
                    "provider_mode": "remote_cdp",
                },
            },
        )
        assert providers.status_code == 200, providers.text
        assert providers.json()["result"]["backend"] == "remote_cdp"
        assert providers.json()["result"]["backend_status"] == "unavailable"

    suites = client.get("/api/evals/suites")
    assert suites.status_code == 200, suites.text
    assert "suite_phase54_browser_workflow_resilience" in {
        item["suite_id"] for item in suites.json()["items"]
    }
    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase54_browser_workflow_resilience"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["total_cases"] == 14


def _execute(
    client: TestClient,
    *,
    text: str,
    target_url: str,
    action_type: str,
    form_data: dict[str, Any],
    provider_mode: str = "playwright",
) -> dict[str, Any]:
    plan = _plan(
        client,
        text=text,
        target_url=target_url,
        action_type=action_type,
        form_data=form_data,
    )
    response = client.post(
        f"/api/browser-workflows/plans/{plan['plan_id']}/execute",
        json={"provider_mode": provider_mode, "max_steps": 10},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _plan(
    client: TestClient,
    *,
    text: str,
    target_url: str,
    action_type: str,
    form_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    intent = client.post(
        "/api/browser-workflows/intents/resolve",
        json={
            "text": text,
            "member_id": "mem_xiaoyao",
            "target_url": target_url,
            "action_type": action_type,
        },
    )
    assert intent.status_code == 200, intent.text
    created = client.post(
        "/api/browser-workflows/plans",
        json={
            "intent_id": intent.json()["intent"]["intent_id"],
            "form_data": form_data or {},
        },
    )
    assert created.status_code == 200, created.text
    return created.json()["plan"]


class _ResilienceSite:
    def __enter__(self) -> _ResilienceSite:
        self.submissions: list[dict[str, str]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self._server.server_port}{path}"

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        site = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/js-delay":
                    return self._html(
                        """
                        <main id="app">loading</main>
                        <script>
                        setTimeout(() => {
                          document.querySelector('#app').innerHTML = `
                            <form id="draft" method="post" action="/submit">
                              <input id="title" name="title" placeholder="标题" />
                              <textarea id="content" name="content" placeholder="内容"></textarea>
                              <button id="submit" type="submit">提交</button>
                            </form>`;
                        }, 250);
                        console.log('phase54 dynamic token=secret');
                        </script>
                        """
                    )
                if self.path == "/iframe":
                    return self._html('<iframe id="editor-frame" src="/frame-editor"></iframe>')
                if self.path == "/frame-editor":
                    return self._html(_form_html())
                if self.path == "/shadow":
                    return self._html(
                        """
                        <form id="draft" method="post" action="/submit">
                          <div id="shadow-host"></div>
                          <button id="submit" type="submit">提交</button>
                        </form>
                        <script>
                        const root = document
                          .querySelector('#shadow-host')
                          .attachShadow({mode:'open'});
                        root.innerHTML = `
                          <input id="title" name="title" placeholder="标题" />
                          <textarea id="content" name="content" placeholder="内容"></textarea>`;
                        </script>
                        """
                    )
                if self.path == "/modal":
                    return self._html(
                        """
                        <button id="compose" onclick="location.href='/modal/editor'">
                          发布
                        </button>
                        <section id="drawer"></section>
                        """
                    )
                if self.path == "/modal/editor":
                    return self._html(_form_html())
                if self.path == "/new-tab":
                    return self._html('<a id="compose" target="_blank" href="/tab-editor">发布</a>')
                if self.path == "/tab-editor":
                    return self._html(_form_html())
                if self.path == "/mobile-only":
                    ua = self.headers.get("User-Agent", "")
                    if "Mobile" not in ua:
                        return self._html("<main><p>桌面端暂无入口</p></main>")
                    return self._html(_form_html())
                if self.path == "/challenge":
                    return self._html("<h1>验证码 二次验证 风控</h1>")
                if self.path == "/resolved":
                    return self._html(_form_html())
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                data = parse_qs(self.rfile.read(length).decode("utf-8"))
                item = {key: values[-1] for key, values in data.items()}
                if self.path == "/submit":
                    site.submissions.append(item)
                    return self._html("<h1>ok</h1>")
                self.send_error(404)

            def _html(self, body: str) -> None:
                data = f"<html><body>{body}</body></html>".encode()
                self._bytes(data, "text/html; charset=utf-8")

            def _bytes(self, data: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args: object) -> None:
                return

        return Handler


def _form_html() -> str:
    return """
    <form id="draft" method="post" action="/submit">
      <label for="title">标题</label>
      <input id="title" name="title" />
      <label for="content">内容</label>
      <textarea id="content" name="content"></textarea>
      <button id="submit" type="submit">提交</button>
    </form>
    """


def _leakage_count(payload: Any) -> int:
    text = json.dumps(payload, ensure_ascii=False).lower()
    return sum(
        token in text
        for token in [
            "cookie:",
            "set-cookie",
            "authorization:",
            '"password":',
            "private_key",
            "c:\\users\\administrator",
        ]
    )
