from __future__ import annotations

import json
from typing import Any, cast
from pathlib import Path

import anyio
from fastapi.testclient import TestClient

from tests.test_phase101_extension_capability_runtime import FakeMCPTransport


def test_phase112_extension_runtime_snapshot_and_disable_sync(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "phase112-python-runtime-plugin"
    root.mkdir()
    (root / "plugin.py").write_text(
        """
def register(context):
    context.register_tool(
        "phase112.search",
        display_name="Phase112 Search",
        description="Phase112 runtime tool",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    context.register_health_check("phase112-runtime", status="ready")
""".strip(),
        encoding="utf-8",
    )
    (root / "plugin.yaml").write_text(
        """
name: phase112-python-runtime-plugin
version: 1.0.0
runtime:
  python_entrypoint: plugin.py
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
    plan = client.post(
        f"/api/extensions/{extension_id}/plan-run",
        json={"goal": "use phase112 search", "intent": "search"},
    ).json()

    assert diagnostics["runtime_snapshot"]["contract_version"] == "phase112.extension_runtime_snapshot.v1"
    assert diagnostics["runtime_snapshot"] == plan["runtime_snapshot"]
    assert diagnostics["runtime_snapshot"]["runtime_sync_state"] == "synced"
    assert diagnostics["runtime_snapshot"]["deliverable_proof"]["final_deliverable"] is True

    tools = client.get("/api/tools").json()["items"]
    tool = next(item for item in tools if item["tool_name"] == "phase112.search")
    assert tool["status"] == "active"

    disabled = client.post(f"/api/extensions/{extension_id}/disable", json={})
    assert disabled.status_code == 200, disabled.text

    diagnostics_after_disable = client.get(f"/api/extensions/{extension_id}/diagnostics").json()
    assert diagnostics_after_disable["runtime_snapshot"]["bundle_status"] == "disabled"
    assert diagnostics_after_disable["runtime_snapshot"]["runtime_sync_state"] == "synced"

    tools_after_disable = client.get("/api/tools").json()["items"]
    disabled_tool = next(item for item in tools_after_disable if item["tool_name"] == "phase112.search")
    assert disabled_tool["status"] == "disabled"


def test_phase112_readiness_and_release_summary_expose_extension_sync_contract(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "phase112-release-plugin"
    root.mkdir()
    (root / "plugin.py").write_text(
        """
def register(context):
    context.register_tool(
        "phase112.release",
        display_name="Phase112 Release",
        description="Phase112 release runtime tool",
    )
    context.register_health_check("phase112-release-runtime", status="ready")
""".strip(),
        encoding="utf-8",
    )
    (root / "plugin.yaml").write_text(
        """
name: phase112-release-plugin
version: 1.0.0
runtime:
  python_entrypoint: plugin.py
""".strip(),
        encoding="utf-8",
    )

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    extension_id = install.json()["bundle"]["extension_id"]
    client.post(f"/api/extensions/{extension_id}/enable", json={})
    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]

    readiness = client.get("/api/system/chat-mainline-readiness").json()
    phase112 = readiness["phase_readiness"]["phase112_extension_runtime_sync_closure"]
    assert phase112["status"] == "ready"
    assert phase112["details"]["phase112_contract_version"] == "phase112.extension_runtime_sync_closure.v1"
    assert phase112["details"]["runtime_snapshot_contract"] == "phase112.extension_runtime_snapshot.v1"

    report = client.get(f"/api/release-gates/{gate_id}/report").json()
    summary = report["summary"]["phase112_extension_runtime_sync_closure"]
    assert summary["status"] == "ready"
    assert summary["contract_version"] == "phase112.extension_runtime_sync_closure.v1"
    assert summary["runtime_snapshot_contract"] == "phase112.extension_runtime_snapshot.v1"
    assert summary["runtime_sync_blocker_cleared"] is True
    assert summary["extension_scorecard"]["final_deliverable_rate"] == 1.0


def test_phase112_extension_runtime_snapshot_flows_into_task_detail_and_phase103(
    client: TestClient,
    tmp_path: Path,
) -> None:
    root = tmp_path / "phase112-task-proof-plugin"
    root.mkdir()
    (root / "plugin.py").write_text(
        """
def register(context):
    context.register_tool(
        "phase112.task",
        display_name="Phase112 Task",
        description="Phase112 task runtime tool",
    )
    context.register_health_check("phase112-task-runtime", status="ready")
""".strip(),
        encoding="utf-8",
    )
    (root / "plugin.yaml").write_text(
        """
name: phase112-task-proof-plugin
version: 1.0.0
runtime:
  python_entrypoint: plugin.py
""".strip(),
        encoding="utf-8",
    )

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    extension_id = install.json()["bundle"]["extension_id"]
    client.post(f"/api/extensions/{extension_id}/enable", json={})
    runtime_snapshot = client.get(f"/api/extensions/{extension_id}/diagnostics").json()["runtime_snapshot"]

    task = client.post(
        "/api/tasks",
        json={
            "goal": "Record extension runtime proof for phase112",
            "office_request": {
                "request_type": "mail",
                "operation": "draft",
                "title": "Phase112 extension proof seed",
                "summary": "Seed task to attach extension runtime proof.",
                "content": "Seed body.",
                "recipients": ["customer@example.com"],
            },
            "auto_start": True,
        },
    ).json()

    registry = cast(Any, client.app).state.registry
    anyio.run(
        registry.tasks.update_task,
        task["task_id"],
        {
            "status": "completed",
            "result": {
                "domain": "extension_ecosystem",
                "summary": "Extension runtime synced and deliverable proof attached.",
                "deliverable": True,
                "extension_id": extension_id,
                "extension_runtime_snapshot": runtime_snapshot,
            },
            "updated_at": task["updated_at"],
        },
    )

    detail = client.get(f"/api/tasks/{task['task_id']}").json()
    assert detail["result"]["domain"] == "extension_ecosystem"
    assert detail["result"]["phase111_deliverable_proof"]["domain"] == "extension_ecosystem"
    assert detail["result"]["phase111_deliverable_proof"]["proof_status"] == "present"
    assert detail["result"]["delivery_status"] == "delivered"
    assert detail["result"]["final_deliverable"] is True

    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    report = client.get(f"/api/release-gates/{gate_id}/report").json()
    extension_scorecard = report["summary"]["phase103_task_closure_gate"]["per_domain_scorecard"][
        "extension_ecosystem"
    ]
    assert extension_scorecard["final_deliverable_rate"] == 1.0
    assert task["task_id"] in extension_scorecard["sample_task_ids"]

    closure_records = anyio.run(
        lambda: registry.release.list_task_closure_records(
            release_gate_id=gate_id,
            domain="extension_ecosystem",
        )
    )
    assert any(record["task_id"] == task["task_id"] for record in closure_records)


def test_phase112_extension_launch_task_auto_attaches_runtime_snapshot_and_phase103(
    client: TestClient,
    tmp_path: Path,
) -> None:
    registry = cast(Any, client.app).state.registry
    registry.mcp_service.set_transport_factory(
        lambda _server: FakeMCPTransport(tool_names=["browser.navigate"])
    )
    root = tmp_path / "phase112-launchable-plugin"
    skill_root = root / "skills" / "draft"
    skill_root.mkdir(parents=True)
    (root / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "name": "phase112-launchable-plugin",
                "displayName": "Phase112 Launchable Plugin",
                "version": "1.0.0",
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
name: extension_draft
title: Extension Draft
description: Produce a local draft through the extension skill.
trigger:
  intents:
    - extension_draft
  keywords:
    - draft
    - runtime
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
      path: outputs/extension-task.md
      content: "# Extension Draft\\n\\nTopic: {topic}"
---
# Extension Draft

Write a draft artifact for the requested topic.
""".strip(),
        encoding="utf-8",
    )

    install = client.post(
        "/api/extensions/install",
        json={"source_type": "local_directory", "source_uri": str(root)},
    )
    assert install.status_code == 200, install.text
    extension_id = install.json()["bundle"]["extension_id"]
    client.post(f"/api/extensions/{extension_id}/enable", json={})

    launched = client.post(
        f"/api/extensions/{extension_id}/tasks",
        json={
            "goal": "Create an extension runtime draft",
            "intent": "extension_draft",
            "skill_input": {"topic": "Phase112 runtime sync"},
            "auto_start": True,
        },
    )
    assert launched.status_code == 200, launched.text
    task = launched.json()
    assert task["status"] == "completed"
    assert task["result"]["domain"] == "extension_ecosystem"
    assert task["result"]["extension_id"] == extension_id
    assert task["result"]["extension_runtime_snapshot"]["contract_version"] == (
        "phase112.extension_runtime_snapshot.v1"
    )
    assert task["result"]["delivery_status"] == "delivered"
    assert task["result"]["final_deliverable"] is True

    replay = client.get(f"/api/tasks/{task['task_id']}/replay").json()
    assert replay["skill_runs"]
    assert replay["skill_runs"][0]["status"] == "completed"
    assert replay["domain_result"]["extension_runtime_snapshot"]["runtime_sync_state"] == "synced"

    gate_id = client.post("/api/release-gates", json={}).json()["release_gate_id"]
    report = client.get(f"/api/release-gates/{gate_id}/report").json()
    extension_scorecard = report["summary"]["phase103_task_closure_gate"]["per_domain_scorecard"][
        "extension_ecosystem"
    ]
    assert extension_scorecard["final_deliverable_rate"] == 1.0
    assert task["task_id"] in extension_scorecard["sample_task_ids"]
