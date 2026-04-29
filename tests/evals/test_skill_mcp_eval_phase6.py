from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.eval


def test_eval_skill_mcp_phase6_extension_safety_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CYCBER_ROOT", str(ROOT_DIR))
    monkeypatch.setenv("CYCBER_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as client:
        app = cast(FastAPI, client.app)
        registry = cast(Any, app.state.registry)
        registry.mcp_service.set_transport_factory(lambda _server: EvalMCPTransport())
        bundle_dir = _write_eval_bundle(tmp_path)

        installed = client.post(
            "/api/skills/install",
            json={"source_type": "local_directory", "source_uri": str(bundle_dir)},
        ).json()
        bundle_id = installed["bundle"]["bundle_id"]
        client.post(f"/api/plugins/{bundle_id}/enable", json={})
        match_before_disable = client.post(
            "/api/skills/match",
            json={"goal": "生成评测草稿", "intent": "eval_draft"},
        ).json()
        client.post(f"/api/plugins/{bundle_id}/disable", json={"reason": "eval"})
        match_after_disable = client.post(
            "/api/skills/match",
            json={"goal": "生成评测草稿", "intent": "eval_draft"},
        ).json()

        client.post(
            "/api/mcp/servers",
            json={
                "server_id": "eval",
                "display_name": "Eval MCP",
                "transport": "stdio",
                "command": "eval-mcp",
            },
        )
        client.post("/api/mcp/servers/eval/enable")
        client.post("/api/mcp/servers/eval/sync")
        resource = client.get("/api/mcp/servers/eval/resources").json()["items"][0]
        prompt = client.get("/api/mcp/servers/eval/prompts").json()["items"][0]
        tool = client.post(
            "/api/tools/execute",
            json={"tool_name": "mcp.eval.echo", "args": {"text": "hello"}},
        ).json()
        audit_text = json.dumps(client.get("/api/audit").json(), ensure_ascii=False)

    metrics = {
        "skill_enabled_match": 1.0 if match_before_disable["items"] else 0.0,
        "plugin_disable_propagates": 1.0 if match_after_disable["items"] == [] else 0.0,
        "mcp_resource_untrusted": 1.0
        if resource["trust_level"] == "untrusted_external_content"
        else 0.0,
        "mcp_prompt_not_system": 1.0 if prompt["trust_level"] == "mcp_prompt_template" else 0.0,
        "mcp_tool_traceable": 1.0 if tool["tool_call"]["source"] == "mcp" else 0.0,
        "secret_isolation": 1.0 if "plain-secret" not in audit_text else 0.0,
    }

    assert all(value == 1.0 for value in metrics.values())


def _write_eval_bundle(tmp_path: Path) -> Path:
    bundle_dir = tmp_path / "eval-draft"
    bundle_dir.mkdir()
    (bundle_dir / "bundle.yaml").write_text(
        """
id: eval-draft
version: 0.1.0
display_name: 评测草稿技能包
entry_skills:
  - eval_draft
triggers:
  intents:
    - eval_draft
  keywords:
    - 评测
    - 草稿
required_tools:
  - file.write
steps:
  - tool_name: file.write
    args:
      path: outputs/eval-skill.md
      content: "# Eval"
""".strip(),
        encoding="utf-8",
    )
    (bundle_dir / "SKILL.md").write_text(
        """
# 评测 Skill

## 用途
生成评测草稿。

## 何时使用
评测 Skill/MCP 链路时使用。

## 输入
文本。

## 输出
Markdown。

## 步骤
1. 写入工件。

## 禁止
不外发，不读取 secret。
""".strip(),
        encoding="utf-8",
    )
    return bundle_dir


class EvalMCPTransport:
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
            return {"protocolVersion": "2025-11-25", "capabilities": {}}
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echo",
                        "inputSchema": {"type": "object", "required": ["text"]},
                        "annotations": {"readOnlyHint": True},
                    }
                ]
            }
        if method == "resources/list":
            return {"resources": [{"uri": "eval://resource", "name": "Eval Resource"}]}
        if method == "prompts/list":
            return {"prompts": [{"name": "eval_prompt", "arguments": []}]}
        if method == "tools/call":
            arguments = (params or {}).get("arguments", {})
            return {"content": [{"type": "text", "text": f"echo:{arguments.get('text')}"}]}
        raise AssertionError(f"unexpected method: {method}")
