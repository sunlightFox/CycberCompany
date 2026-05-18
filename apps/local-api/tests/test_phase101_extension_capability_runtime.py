from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.services.extension_runtime import (
    ExtensionRuntimeActivationContext,
    ExtensionRuntimeDriverRegistry,
)


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

    async def notify(self, method: str, params: dict[str, object] | None = None) -> None:
        return None

    async def close(self) -> None:
        return None

    async def request(
        self,
        method: str,
        params: dict[str, object] | None = None,
    ) -> dict[str, object]:
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
            text = arguments.get("text") if isinstance(arguments, dict) else None
            return {"content": [{"type": "text", "text": f"echo:{text}"}]}
        raise AssertionError(f"unexpected MCP method: {method}")


def test_openclaw_package_json_compat_and_smoke_check_preview(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "openclaw-node-plugin"
    skill_root = root / "skills" / "writer"
    skill_root.mkdir(parents=True)
    (root / "dist").mkdir()
    (root / "dist" / "index.js").write_text("export default {};\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "@demo/openclaw-node-plugin",
                "version": "0.2.0",
                "main": "dist/index.js",
                "openclaw": {
                    "compat": {"pluginApi": "^1.0.0"},
                    "build": {
                        "openclawVersion": "1.4.0",
                        "pluginSdkVersion": "1.4.0",
                    },
                    "install": {"minHostVersion": "1.0.0"},
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": "node-writer",
                "displayName": "Node Writer",
                "skills": ["skills"],
                "contracts": {"tools": ["file.write"]},
                "configSchema": {
                    "type": "object",
                    "required": ["apiKey"],
                    "properties": {"apiKey": {"type": "string"}},
                },
                "configUiHints": {"apiKey": {"sensitive": True, "label": "API key"}},
            }
        ),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text(
        """
---
name: writer
tools:
  - file.write
---
# Writer
""".strip(),
        encoding="utf-8",
    )

    response = client.post(
        "/api/extensions/preview-import",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    snapshot = payload["bundle_preview"]["canonical_snapshot"]
    assert snapshot["runtime_compatibility"] == "external_runtime"
    assert snapshot["config_requirements"][0]["key"] == "apiKey"
    assert snapshot["secret_requirements"][0]["key"] == "apiKey"
    assert snapshot["manifest"]["_package_json_openclaw"]["plugin_api_range"] == "^1.0.0"


def test_extension_diagnostics_and_plan_run_include_runtime_state(
    client: TestClient,
    tmp_path: Path,
) -> None:
    registry = client.app.state.registry
    registry.mcp_service.set_transport_factory(
        lambda _server: FakeMCPTransport(tool_names=["browser.navigate"])
    )

    root = tmp_path / "openclaw-mcp-plugin"
    skill_root = root / "skills" / "browser"
    skill_root.mkdir(parents=True)
    (root / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": "browser-helper",
                "displayName": "Browser Helper",
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
mcp_requirements:
  - server_id: browser-fake
    tool_name: browser.navigate
trigger:
  keywords:
    - browser
---
# Browser Helper
""".strip(),
        encoding="utf-8",
    )

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    assert install.status_code == 200, install.text
    extension_id = install.json()["bundle"]["extension_id"]

    diagnostics = client.get(f"/api/extensions/{extension_id}/diagnostics")
    assert diagnostics.status_code == 200, diagnostics.text
    diagnostics_json = diagnostics.json()
    assert diagnostics_json["summary"]["runtime_compatibility"] == "mcp_compatible"
    assert any(item["contribution_type"] == "mcp" for item in diagnostics_json["contributions"])
    assert isinstance(diagnostics_json["next_actions"], list)

    plan = client.post(
        f"/api/extensions/{extension_id}/plan-run",
        json={"goal": "open browser", "intent": "browser"},
    )
    assert plan.status_code == 200, plan.text
    plan_json = plan.json()
    assert plan_json["runnable"] is False
    assert plan_json["runnable_state"] in {"needs_binding", "external_runtime_required"}
    assert isinstance(plan_json["missing_bindings"], list)
    assert plan_json["selected_capabilities"]


def test_hermes_plugin_yaml_imports_native_python_contribution(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "hermes-tavily"
    root.mkdir()
    (root / "plugin.yaml").write_text(
        """
name: web-tavily
version: 1.0.0
description: Tavily search provider
kind: backend
required_env:
  - TAVILY_API_KEY
install_hint: pip install tavily-python
provides_web_providers:
  - tavily
""".strip(),
        encoding="utf-8",
    )

    response = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    extension_id = payload["bundle"]["extension_id"]
    snapshot = payload["bundle"]["canonical_snapshot"]
    assert snapshot["source_format"] == "hermes_plugin_v1"
    assert snapshot["runtime_compatibility"] == "native_python"
    assert snapshot["env_requirements"][0]["name"] == "TAVILY_API_KEY"

    diagnostics = client.get(f"/api/extensions/{extension_id}/diagnostics")
    assert diagnostics.status_code == 200, diagnostics.text
    assert diagnostics.json()["contributions"][0]["runtime_kind"] == "python"


def test_native_python_runtime_register_context_activates_contributions(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "python-runtime-plugin"
    root.mkdir()
    (root / "plugin.py").write_text(
        """
def register(context):
    context.register_tool(
        "demo.search",
        display_name="Demo Search",
        description="Demo provider search",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    context.register_external_platform("demo-web", metadata={"mode": "search"})
    context.register_health_check("demo-runtime", status="ready")
""".strip(),
        encoding="utf-8",
    )
    (root / "plugin.yaml").write_text(
        """
name: python-runtime-plugin
version: 1.0.0
runtime:
  python_entrypoint: plugin.py
provides_web_providers:
  - demo-web
""".strip(),
        encoding="utf-8",
    )

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    assert install.status_code == 200, install.text
    extension_id = install.json()["bundle"]["extension_id"]

    enabled = client.post(f"/api/extensions/{extension_id}/enable", json={})
    assert enabled.status_code == 200, enabled.text

    diagnostics = client.get(f"/api/extensions/{extension_id}/diagnostics").json()
    assert diagnostics["health"]["status"] == "ready"
    assert any(
        item["contribution_type"] == "tool" and item["name"] == "demo.search"
        for item in diagnostics["contributions"]
    )

    tools = client.get("/api/tools").json()["items"]
    demo_tool = next(item for item in tools if item["tool_name"] == "demo.search")
    assert demo_tool["source"] == "extension_python"
    assert demo_tool["status"] == "active"


def test_native_python_runtime_error_is_reported_as_health_contribution(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "broken-python-runtime-plugin"
    root.mkdir()
    (root / "plugin.yaml").write_text(
        """
name: broken-python-runtime-plugin
version: 1.0.0
runtime:
  python_entrypoint: missing.py
""".strip(),
        encoding="utf-8",
    )

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    assert install.status_code == 200, install.text
    extension_id = install.json()["bundle"]["extension_id"]

    enabled = client.post(f"/api/extensions/{extension_id}/enable", json={})
    assert enabled.status_code == 200, enabled.text

    diagnostics = client.get(f"/api/extensions/{extension_id}/diagnostics").json()
    assert diagnostics["health"]["status"] == "error"
    loader = next(
        item
        for item in diagnostics["contributions"]
        if item["contribution_type"] == "health_check" and item["name"] == "python_loader"
    )
    assert loader["status"] == "blocked"
    assert loader["evidence"]["driver_id"] == "python_inprocess"
    assert "missing.py" in loader["details"]["errors"][0]


def test_openclaw_node_bridge_is_structured_without_process_start(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "openclaw-bridge-plugin"
    skill_root = root / "skills" / "bridge"
    skill_root.mkdir(parents=True)
    (root / "dist").mkdir()
    (root / "dist" / "index.js").write_text("export default {};\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "@demo/openclaw-bridge-plugin",
                "version": "0.3.0",
                "main": "dist/index.js",
                "scripts": {"openclaw:start": "node dist/index.js"},
                "openclaw": {
                    "compat": {"pluginApi": "^1.0.0"},
                    "build": {"openclawVersion": "1.4.0"},
                    "bridge": {
                        "health_check": {"kind": "stdio_ping"},
                        "env_refs": ["DEMO_TOKEN"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": "bridge-plugin",
                "displayName": "Bridge Plugin",
                "skills": ["skills"],
            }
        ),
        encoding="utf-8",
    )
    (skill_root / "SKILL.md").write_text("# Bridge\n", encoding="utf-8")

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    assert install.status_code == 200, install.text
    extension_id = install.json()["bundle"]["extension_id"]

    diagnostics = client.get(f"/api/extensions/{extension_id}/diagnostics")
    assert diagnostics.status_code == 200, diagnostics.text
    payload = diagnostics.json()
    bridge = next(
        item
        for item in payload["contributions"]
        if item["contribution_type"] == "route"
    )
    assert bridge["runtime_kind"] == "external_runtime"
    assert bridge["details"]["start_command"] == "node dist/index.js"
    assert payload["health"]["status"] == "external_runtime_required"
    assert any(item["kind"] == "start_external_runtime" for item in payload["next_actions"])


def test_runtime_driver_registry_selects_python_and_node_in_order(tmp_path: Path) -> None:
    context = ExtensionRuntimeActivationContext(
        extension_id="ext_demo",
        organization_id="org_default",
        bundle_id="bundle_demo",
        source_root=tmp_path,
        package={},
        canonical={
            "runtime_compatibility": "native_python",
            "manifest": {"runtime": {"python_entrypoint": "plugin.py"}},
            "runtime_contributions": [
                {
                    "contribution_id": "extcontrib.bundle_demo.bridge.external",
                    "contribution_type": "route",
                    "runtime_kind": "external_runtime",
                    "name": "external_runtime_bridge",
                    "details": {"start_command": "node dist/index.js"},
                }
            ],
        },
    )

    registry = ExtensionRuntimeDriverRegistry()

    assert [driver.driver_id for driver in registry.drivers_for(context)] == [
        "python_inprocess",
        "node_bridge",
    ]
    results = registry.activate_all(context)
    assert [result.driver_id for result in results] == ["python_inprocess", "node_bridge"]
    assert results[0].status == "blocked"
    assert results[1].status == "external_runtime_required"
    assert results[1].contributions[0].status == "external_runtime_required"
    assert results[1].contributions[0].evidence["process_started"] is False
