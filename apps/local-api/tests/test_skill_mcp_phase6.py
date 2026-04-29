from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient


def test_phase6_skill_bundle_install_enable_match_and_task_replay(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_bundle(tmp_path)

    install = client.post(
        "/api/skills/install",
        json={
            "source_type": "local_directory",
            "source_uri": str(bundle_dir),
            "requested_by_member_id": "mem_xiaoyao",
        },
    )
    assert install.status_code == 200, install.text
    installed = install.json()
    assert installed["bundle"]["status"] == "installed_disabled"
    assert installed["permission_preview"]["required_tools"][0]["tool_name"] == "file.write"
    skill_id = installed["skills"][0]["skill_id"]

    no_match = client.post(
        "/api/skills/match",
        json={"goal": "请用内容草稿技能写一份发布草稿", "intent": "content_draft"},
    )
    assert no_match.status_code == 200
    assert no_match.json()["items"] == []

    enabled = client.post(
        f"/api/plugins/{installed['bundle']['bundle_id']}/enable",
        json={"actor_member_id": "mem_xiaoyao"},
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "enabled"

    matched = client.post(
        "/api/skills/match",
        json={"goal": "请用内容草稿技能写一份发布草稿", "intent": "content_draft"},
    )
    assert matched.status_code == 200
    assert matched.json()["items"][0]["skill_id"] == skill_id

    task = client.post(
        "/api/tasks",
        json={
            "owner_member_id": "mem_xiaoyao",
            "goal": "使用内容草稿技能生成任务输出",
            "constraints": {"skill_id": skill_id, "skill_input": {"topic": "个人智能体 OS"}},
            "auto_start": True,
        },
    )
    assert task.status_code == 200, task.text
    task_id = task.json()["task_id"]

    replay = client.get(f"/api/tasks/{task_id}/replay")
    assert replay.status_code == 200, replay.text
    replay_json = replay.json()
    assert replay_json["skill_runs"]
    assert replay_json["skill_runs"][0]["status"] == "completed"
    assert replay_json["plugin_events"]

    disabled = client.post(
        f"/api/plugins/{installed['bundle']['bundle_id']}/disable",
        json={"actor_member_id": "mem_xiaoyao", "reason": "test"},
    )
    assert disabled.status_code == 200, disabled.text
    after_disable = client.post(
        "/api/skills/match",
        json={"goal": "请用内容草稿技能写一份发布草稿", "intent": "content_draft"},
    )
    assert after_disable.status_code == 200
    assert after_disable.json()["items"] == []


def test_phase6_invalid_bundle_returns_project_error(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bad_dir = tmp_path / "bad-bundle"
    bad_dir.mkdir()
    response = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bad_dir)},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PLUGIN_VALIDATE_FAILED"


def test_phase6_skill_lifecycle_invariants(client: TestClient, tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    installed = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    ).json()
    bundle_id = installed["bundle"]["bundle_id"]
    skill_id = installed["skills"][0]["skill_id"]

    direct_skill_enable = client.post(
        f"/api/skills/{skill_id}/enable",
        json={"reviewed_by_member_id": "mem_xiaoyao"},
    )
    assert direct_skill_enable.status_code == 409
    assert direct_skill_enable.json()["error"]["code"] == "PLUGIN_DISABLED"

    revoked = client.post(
        f"/api/plugins/{bundle_id}/revoke",
        json={"actor_member_id": "mem_xiaoyao", "reason": "no longer trusted"},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"

    disable_after_revoke = client.post(
        f"/api/plugins/{bundle_id}/disable",
        json={"actor_member_id": "mem_xiaoyao"},
    )
    assert disable_after_revoke.status_code == 409
    assert disable_after_revoke.json()["error"]["code"] == "PLUGIN_REVOKED"

    skill_after_revoke = client.post(
        f"/api/skills/{skill_id}/disable",
        json={"reviewed_by_member_id": "mem_xiaoyao"},
    )
    assert skill_after_revoke.status_code == 409
    assert skill_after_revoke.json()["error"]["code"] == "SKILL_REVOKED"


def test_phase6_skill_md_secret_is_rejected(client: TestClient, tmp_path: Path) -> None:
    bundle_dir = _write_bundle(tmp_path)
    (bundle_dir / "SKILL.md").write_text(
        """
# 泄密 Skill

## 用途
测试。

## 何时使用
测试。

## 输入
文本。

## 输出
文本。

## 步骤
1. 测试。

## 禁止
password=plain-secret
""".strip(),
        encoding="utf-8",
    )
    response = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PLUGIN_VALIDATE_FAILED"


def test_phase6_skill_steps_must_declare_required_tools(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_bundle(tmp_path)
    (bundle_dir / "bundle.yaml").write_text(
        """
id: hidden-tool
version: 0.1.0
display_name: 隐藏工具技能包
entry_skills:
  - hidden_tool
required_tools:
  - file.write
steps:
  - tool_name: terminal.run
    args:
      command: echo hidden
""".strip(),
        encoding="utf-8",
    )
    response = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "PLUGIN_VALIDATE_FAILED"
    assert body["error"]["details"]["tool_name"] == "terminal.run"


def test_phase6_skill_high_risk_step_resumes_after_approval(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_high_risk_bundle(tmp_path)
    installed = client.post(
        "/api/skills/install",
        json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
    ).json()
    bundle_id = installed["bundle"]["bundle_id"]
    skill_id = installed["skills"][0]["skill_id"]
    enabled = client.post(f"/api/plugins/{bundle_id}/enable", json={})
    assert enabled.status_code == 200, enabled.text

    task = client.post(
        "/api/tasks",
        json={
            "owner_member_id": "mem_xiaoyao",
            "goal": "运行高风险技能",
            "constraints": {"skill_id": skill_id},
            "auto_start": True,
        },
    ).json()
    assert task["status"] == "waiting_approval"
    approval_id = task["current_approval_id"]
    assert approval_id

    resumed = client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"reason": "phase6 resume"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "completed"
    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    assert len(replay["skill_runs"]) == 1
    assert any(run["status"] == "completed" for run in replay["skill_runs"])


def test_phase6_mcp_sync_and_tool_runtime_call(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    registry.mcp_service.set_transport_factory(lambda _server: FakeMCPTransport())

    created = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "fake",
            "display_name": "Fake MCP",
            "transport": "stdio",
            "command": "fake-mcp",
            "args": [],
            "env_refs": [],
        },
    )
    assert created.status_code == 200, created.text

    enabled = client.post("/api/mcp/servers/fake/enable")
    assert enabled.status_code == 200, enabled.text

    synced = client.post("/api/mcp/servers/fake/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json()["tools_synced"] == 1
    assert synced.json()["resources_synced"] == 1
    assert synced.json()["prompts_synced"] == 1

    resources = client.get("/api/mcp/servers/fake/resources")
    assert resources.status_code == 200
    assert resources.json()["items"][0]["trust_level"] == "untrusted_external_content"

    prompts = client.get("/api/mcp/servers/fake/prompts")
    assert prompts.status_code == 200
    assert prompts.json()["items"][0]["trust_level"] == "mcp_prompt_template"

    executed = client.post(
        "/api/tools/execute",
        json={
            "member_id": "mem_xiaoyao",
            "tool_name": "mcp.fake.echo",
            "args": {"text": "hello"},
        },
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["tool_call"]["source"] == "mcp"
    assert "hello" in str(executed.json()["result"])


def test_phase6_mcp_resync_disables_stale_capabilities(client: TestClient) -> None:
    registry = cast(Any, client.app).state.registry
    registry.mcp_service.set_transport_factory(
        lambda _server: FakeMCPTransport(
            tool_names=["echo"],
            resource_uris=["fake://old-resource"],
            prompt_names=["draft"],
        )
    )
    created = client.post(
        "/api/mcp/servers",
        json={
            "server_id": "drift",
            "display_name": "Drift MCP",
            "transport": "stdio",
            "command": "fake-mcp",
            "args": [],
            "env_refs": [],
        },
    )
    assert created.status_code == 200, created.text
    assert client.post("/api/mcp/servers/drift/enable").status_code == 200
    first_sync = client.post("/api/mcp/servers/drift/sync")
    assert first_sync.status_code == 200, first_sync.text

    stale_tool = client.get("/api/tools/mcp.drift.echo")
    assert stale_tool.status_code == 200, stale_tool.text
    assert stale_tool.json()["status"] == "active"

    registry.mcp_service.set_transport_factory(
        lambda _server: FakeMCPTransport(
            tool_names=["new_echo"],
            resource_uris=["fake://new-resource"],
            prompt_names=["new_draft"],
        )
    )
    second_sync = client.post("/api/mcp/servers/drift/sync")
    assert second_sync.status_code == 200, second_sync.text

    tools = client.get("/api/mcp/servers/drift/tools").json()["items"]
    statuses = {tool["tool_name"]: tool["status"] for tool in tools}
    assert statuses == {"echo": "disabled", "new_echo": "active"}
    resources = client.get("/api/mcp/servers/drift/resources").json()["items"]
    assert {item["uri"]: item["status"] for item in resources} == {
        "fake://new-resource": "active",
        "fake://old-resource": "disabled",
    }
    prompts = client.get("/api/mcp/servers/drift/prompts").json()["items"]
    assert {item["name"]: item["status"] for item in prompts} == {
        "draft": "disabled",
        "new_draft": "active",
    }

    disabled_tool = client.get("/api/tools/mcp.drift.echo")
    assert disabled_tool.status_code == 200, disabled_tool.text
    assert disabled_tool.json()["status"] == "disabled"
    stale_execute = client.post(
        "/api/tools/execute",
        json={"member_id": "mem_xiaoyao", "tool_name": "mcp.drift.echo", "args": {"text": "x"}},
    )
    assert stale_execute.status_code == 404
    assert stale_execute.json()["error"]["code"] == "TOOL_NOT_FOUND"


def _write_bundle(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "content-draft"
    bundle_dir.mkdir()
    (bundle_dir / "bundle.yaml").write_text(
        """
id: content-draft
version: 0.1.0
display_name: 内容草稿技能包
description: 生成本地 Markdown 草稿。
kind: skill_bundle
author: local
entry_skills:
  - content_draft
triggers:
  intents:
    - content_draft
  keywords:
    - 内容
    - 草稿
required_tools:
  - file.write
permissions:
  fs:
    write:
      - workspace://artifacts/**
risk_policy:
  confirmation_required_for: []
steps:
  - tool_name: file.write
    args:
      path: outputs/skill-result.md
      content: "# {skill_display_name}\\n\\n主题：{topic}"
eval_cases:
  - id: content_draft_basic
    input:
      topic: 个人智能体 OS
    expected:
      contains:
        - 主题
    forbidden:
      text:
        - secret_leak_marker
""".strip(),
        encoding="utf-8",
    )
    (bundle_dir / "SKILL.md").write_text(
        """
# 内容草稿 Skill

## 用途
生成本地草稿。

## 何时使用
用户需要内容或草稿时使用。

## 输入
主题。

## 输出
Markdown 草稿。

## 步骤
1. 生成本地草稿工件。

## 可用工具
file.write。

## 风险规则
不外部发布。

## 失败处理
返回失败原因。

## 禁止
不自动发布，不读取账号明文密码。
""".strip(),
        encoding="utf-8",
    )
    return bundle_dir


def _write_high_risk_bundle(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "cleanup-skill"
    bundle_dir.mkdir()
    (bundle_dir / "bundle.yaml").write_text(
        """
id: cleanup-skill
version: 0.1.0
display_name: 清理技能包
entry_skills:
  - cleanup
triggers:
  keywords:
    - 清理
required_tools:
  - file.delete
risk_policy:
  confirmation_required_for:
    - file.delete
steps:
  - tool_name: file.delete
    args:
      path: outputs/missing.txt
""".strip(),
        encoding="utf-8",
    )
    (bundle_dir / "SKILL.md").write_text(
        """
# 清理 Skill

## 用途
清理任务工件。

## 何时使用
用户确认清理时使用。

## 输入
路径。

## 输出
清理结果。

## 步骤
1. 删除任务工件目录中的文件。

## 禁止
不删除任务工件目录外的文件。
""".strip(),
        encoding="utf-8",
    )
    return bundle_dir


class FakeMCPTransport:
    def __init__(
        self,
        *,
        tool_names: list[str] | None = None,
        resource_uris: list[str] | None = None,
        prompt_names: list[str] | None = None,
    ) -> None:
        self._tool_names = tool_names or ["echo"]
        self._resource_uris = resource_uris or ["fake://resource"]
        self._prompt_names = prompt_names or ["draft"]

    async def start(self) -> None:
        return None

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        return None

    async def close(self) -> None:
        return None

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": "2025-11-25",
                "serverInfo": {"name": "fake", "version": "0.1.0"},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": tool_name,
                        "description": f"Echo text from {tool_name}",
                        "inputSchema": {"type": "object", "required": ["text"]},
                        "annotations": {"readOnlyHint": True},
                    }
                    for tool_name in self._tool_names
                ]
            }
        if method == "resources/list":
            return {
                "resources": [
                    {
                        "uri": uri,
                        "name": "Fake Resource",
                        "description": "A fake external resource",
                        "mimeType": "text/plain",
                    }
                    for uri in self._resource_uris
                ]
            }
        if method == "prompts/list":
            return {
                "prompts": [
                    {
                        "name": name,
                        "description": "A fake prompt template",
                        "arguments": [{"name": "topic", "required": True}],
                    }
                    for name in self._prompt_names
                ]
            }
        if method == "tools/call":
            arguments = (params or {}).get("arguments", {})
            return {"content": [{"type": "text", "text": f"echo:{arguments.get('text')}"}]}
        raise AssertionError(f"unexpected MCP method: {method}")
