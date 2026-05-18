from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient


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


def test_phase101_openclaw_skill_preview_install_enable_and_plan(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "openclaw-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        """
---
name: writer
title: OpenClaw Writer
description: Drafts writing prompts
version: 1.2.0
tools:
  - file.write
trigger:
  keywords:
    - writer
    - draft
input_schema:
  type: object
  properties:
    topic:
      type: string
---
# Writer

Use the user's topic and draft an outline.
""".strip(),
        encoding="utf-8",
    )

    preview = client.post(
        "/api/extensions/preview-import",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    assert preview.status_code == 200, preview.text
    preview_json = preview.json()
    assert preview_json["source_format"] == "openclaw_skill_v1"
    assert preview_json["compatibility_status"] == "native"
    assert preview_json["bundle_preview"]["package_kind"] == "skill_only"
    assert preview_json["skills_preview"][0]["runtime_kind"] == "instruction_only"

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    assert install.status_code == 200, install.text
    installed = install.json()
    extension_id = installed["bundle"]["extension_id"]
    assert extension_id == "ext.writer"
    assert installed["bundle"]["source_format"] == "openclaw_skill_v1"
    assert installed["skills"][0]["runtime_kind"] == "instruction_only"

    enabled = client.post(f"/api/extensions/{extension_id}/enable", json={})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["status"] == "enabled"

    plan = client.post(
        f"/api/extensions/{extension_id}/plan-run",
        json={"goal": "draft a writer outline", "intent": "writer"},
    )
    assert plan.status_code == 200, plan.text
    plan_json = plan.json()
    assert plan_json["matches"]
    assert plan_json["runnable"] is False

    compatibility = client.get(f"/api/extensions/{extension_id}/compatibility")
    assert compatibility.status_code == 200, compatibility.text
    assert compatibility.json()["items"]


def test_phase101_openclaw_plugin_registers_and_syncs_mcp_on_enable(
    client: TestClient,
    tmp_path: Path,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.mcp_service.set_transport_factory(
        lambda _server: FakeMCPTransport(tool_names=["browser.navigate"])
    )

    root = tmp_path / "openclaw-plugin"
    skill_root = root / "skills" / "browser"
    skill_root.mkdir(parents=True)
    (root / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "name": "browser-helper",
                "displayName": "Browser Helper",
                "version": "0.4.0",
                "skills": ["skills"],
                "mcpServers": {
                    "browser-fake": {
                        "transport": "stdio",
                        "command": "fake-browser-mcp",
                        "tools": ["browser.navigate"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text(
        """
---
name: browser_helper
title: Browser Helper
description: Uses an MCP browser server
mcp_requirements:
  - server_id: browser-fake
    tool_name: browser.navigate
trigger:
  keywords:
    - browser
    - navigate
---
# Browser Helper

Use the browser MCP capability when available.
""".strip(),
        encoding="utf-8",
    )

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    assert install.status_code == 200, install.text
    installed = install.json()
    extension_id = installed["bundle"]["extension_id"]

    servers = client.get("/api/mcp/servers")
    assert servers.status_code == 200, servers.text
    server = next(item for item in servers.json()["items"] if item["server_id"] == "browser-fake")
    assert server["status"] in {"registered_disabled", "ready"}

    enabled = client.post(f"/api/extensions/{extension_id}/enable", json={})
    assert enabled.status_code == 200, enabled.text

    servers = client.get("/api/mcp/servers")
    server = next(item for item in servers.json()["items"] if item["server_id"] == "browser-fake")
    assert server["status"] == "ready"

    bind = client.post(f"/api/extensions/{extension_id}/bind")
    assert bind.status_code == 200, bind.text
    bind_json = bind.json()
    assert bind_json["bundle"]["binding_status"] in {"ready", "degraded"}
    assert bind_json["snapshots"]
