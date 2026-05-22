from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient


class _FakeMailSlurpResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = "{}"
    url = "https://api.mailslurp.com/inboxes"

    def json(self) -> dict[str, Any]:
        return {
            "id": "inbox_test_123",
            "emailAddress": "codex-test@mailslurp.example",
            "name": "codex-auto-inbox",
        }


class _FakeMailSlurpClient:
    calls: list[dict[str, Any]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeMailSlurpClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _FakeMailSlurpResponse:
        self.calls.append({"method": "POST", "url": url, "kwargs": kwargs})
        return _FakeMailSlurpResponse()


def test_mailslurp_email_registration_skill_creates_inbox_via_asset_handle(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    from app.services import tools as tools_module

    _FakeMailSlurpClient.calls.clear()
    monkeypatch.setattr(tools_module.httpx, "AsyncClient", _FakeMailSlurpClient)

    package_ref = "official/email/mailslurp-email-registration"
    search = client.get(
        "/api/skills/catalog/search",
        params={"q": "mailslurp", "repository_id": "clawhub", "limit": 20},
    )
    assert search.status_code == 200, search.text
    assert any(item["package_ref"] == package_ref for item in search.json()["items"])
    zh_search = client.get(
        "/api/skills/catalog/search",
        params={"q": "帮我注册邮箱", "repository_id": "clawhub", "limit": 20},
    )
    assert zh_search.status_code == 200, zh_search.text
    assert any(item["package_ref"] == package_ref for item in zh_search.json()["items"])

    asset = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": "MailSlurp API",
            "provider": "mailslurp",
            "sensitivity": "high",
            "secret_value": "mailslurp-test-api-key",
            "config": {
                "platform": "mailslurp",
                "auth_type": "api_key",
                "username": "mailslurp-api",
            },
            "summary_text": "MailSlurp API key for automated test inbox creation.",
            "capabilities": ["read", "use_api_key"],
        },
    )
    assert asset.status_code == 200, asset.text
    asset_id = asset.json()["asset_id"]

    grant = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "use_api_key",
            "effect": "allow",
        },
    )
    assert grant.status_code == 200, grant.text
    read_grant = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": "read",
            "effect": "allow",
        },
    )
    assert read_grant.status_code == 200, read_grant.text

    query = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": ["read", "use_api_key"],
            "keywords": ["mailslurp"],
        },
    )
    assert query.status_code == 200, query.text
    handle_id = query.json()["handles"][0]["handle_id"]

    root = Path("config/skill-repositories/fixtures/clawhub-mailslurp-email-registration")
    install = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(root.resolve())},
    )
    assert install.status_code == 200, install.text
    payload = install.json()
    bundle_id = payload["bundle"]["bundle_id"]
    skill_id = payload["skills"][0]["skill_id"]

    enable = client.post(f"/api/plugins/{bundle_id}/enable", json={})
    assert enable.status_code == 200, enable.text
    skill_grant = client.post(
        f"/api/skills/{skill_id}/grants",
        json={"subject_id": "mem_xiaoyao", "allowed_tools": ["email_test.create_inbox"]},
    )
    assert skill_grant.status_code == 200, skill_grant.text

    task = client.post(
        "/api/tasks",
        json={"goal": "create a MailSlurp test mailbox", "auto_start": False},
    )
    assert task.status_code == 200, task.text
    task_id = task.json()["task_id"]
    registry = cast(Any, client.app).state.registry

    async def run_skill() -> Any:
        return await registry.skill_plugin_service.run_skill(
            skill_id,
            task_id=task_id,
            step_id="mailslurp-create-inbox",
            owner_member_id="mem_xiaoyao",
            input_data={
                "handle_id": handle_id,
                "name": "codex-auto-inbox",
                "content": "Create one automated inbox for signup verification.",
                "expires_at": "",
            },
        )

    run = cast(Any, client).portal.call(run_skill)
    assert run.status == "completed"
    assert run.artifact_ids
    assert _FakeMailSlurpClient.calls
    call = _FakeMailSlurpClient.calls[0]
    assert call["url"] == "https://api.mailslurp.com/inboxes"
    assert call["kwargs"]["headers"]["x-api-key"] == "mailslurp-test-api-key"

    replay = client.get(f"/api/tasks/{task_id}/replay")
    assert replay.status_code == 200, replay.text
    replay_text = json.dumps(replay.json(), ensure_ascii=False)
    assert "codex-test@mailslurp.example" in replay_text
    assert "mailslurp-test-api-key" not in replay_text
