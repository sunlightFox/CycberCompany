from __future__ import annotations

import json
import time
from typing import Any, cast

import pytest
from app.services import project_deployments
from fastapi.testclient import TestClient


def test_phase52_chat_creates_project_deployment_plan(client: TestClient) -> None:
    body = _turn(
        client,
        "phase52-deploy",
        "帮我部署 fixture://node-static 这个 GitHub 项目，跑起来给我地址。",
    )
    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    text = _reply_from_events(events)
    assert "受控项目部署计划" in text
    assert "不会修改系统全局环境" in text
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    assert payload["task_status"]["task_id"]
    assert payload["deployment_plan"]["workspace_id"]
    assert payload["workspace_boundary"]["filesystem_policy"] == (
        "data/workspaces/projects/{workspace_id}"
    )


def test_phase52_chat_only_explain_does_not_create_task(client: TestClient) -> None:
    body = _turn(
        client,
        "phase52-direct",
        "只解释如何部署 GitHub 项目，不要执行，不要创建任务。",
    )
    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    assert payload["route_semantics"]["task_created"] is False


def test_phase52_chat_host_install_generates_approval_plan(client: TestClient) -> None:
    body = _turn(client, "phase52-host-install", "帮我安装 VS Code 到这台电脑。")
    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    text = _reply_from_events(events)
    assert "安装" in text
    assert "确认" in text
    assert any(marker in text for marker in ["还没", "点头", "开工", "动手"])
    assert "受控链路" not in text
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    assert payload["task_status"]["task_id"]
    assert payload["host_install_plan"]["status"] == "waiting_approval"
    assert payload["approval_binding"]["status"] == "required"
    assert payload["reply_option_items"]


def test_phase52_chat_host_install_plan_error_stays_in_safe_boundary(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = cast(Any, client.app).state.registry

    async def broken_create_plan(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError("package resolver unavailable")

    monkeypatch.setattr(registry.host_install_service, "create_plan", broken_create_plan)

    body = _turn(client, "phase52-host-install-plan-error", "帮我安装 7-Zip。")

    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    assert payload["route_semantics"]["route"] == "host_software_install_request"
    assert payload["route_semantics"]["task_created"] is False
    assert payload["task_status"]["status"] == "blocked_by_boundary"
    assert "host_install_plan_error" in payload


def test_phase52_chat_host_uninstall_generates_approval_or_manual_plan(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve_host_package_candidate(
        software: str,
    ) -> project_deployments.HostPackageCandidate:
        assert software == "uninstall QQ"
        return project_deployments.HostPackageCandidate(
            source_type="winget",
            package_id="Tencent.QQ",
            publisher="Tencent",
            confidence=0.96,
            match_reason="test_uninstall_candidate",
            version="1.0.0",
            name="QQ",
        )

    monkeypatch.setattr(
        project_deployments,
        "_resolve_windows_uninstall_candidate",
        lambda software: None,
    )
    monkeypatch.setattr(
        project_deployments,
        "_windows_uninstall_lookup_supported",
        lambda: False,
    )
    monkeypatch.setattr(
        project_deployments,
        "_resolve_host_package_candidate",
        fake_resolve_host_package_candidate,
    )
    body = _turn(client, "phase52-host-uninstall", "帮我卸载 QQ。")
    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    text = _reply_from_events(events)
    assert "卸载" in text
    assert "确认" in text
    assert any(marker in text for marker in ["还没", "点头", "开工", "动手"])
    assert "受控链路" not in text
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    plan = payload["host_install_plan"]
    assert plan["status"] == "waiting_approval"
    assert plan["approval_id"]
    assert plan["requested_software"] == "QQ"
    assert plan["command_preview"]["action"] == "uninstall"
    assert plan["command_preview"]["steps"][-1]["action"] == "uninstall"
    assert plan["command_preview"]["steps"][-1]["target_package_id"] == "Tencent.QQ"
    assert plan["impact_summary"]["host_action"] == "uninstall"
    assert payload["approval_binding"]["status"] == "required"
    assert payload["approval_binding"]["host_action"] == "uninstall"
    natural = payload["natural_interaction"]
    assert natural["pending_confirmation"]["actions"][0]["approval_id"] == plan["approval_id"]
    assert natural["pending_confirmation"]["actions"][0]["action_type"] == (
        "host.uninstall_software"
    )
    assert natural["reply_option_items"][0]["code"] == "once"


def test_phase52_chat_host_uninstall_confirm_auto_executes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve_host_package_candidate(
        software: str,
    ) -> project_deployments.HostPackageCandidate:
        assert software == "uninstall QQ"
        return project_deployments.HostPackageCandidate(
            source_type="winget",
            package_id="Tencent.QQ",
            publisher="Tencent",
            confidence=0.96,
            match_reason="test_uninstall_candidate",
            version="1.0.0",
            name="QQ",
        )

    monkeypatch.setattr(
        project_deployments,
        "_resolve_windows_uninstall_candidate",
        lambda software: None,
    )
    monkeypatch.setattr(
        project_deployments,
        "_windows_uninstall_lookup_supported",
        lambda: False,
    )

    async def fake_execute_host_install_step(step: dict[str, Any]) -> dict[str, Any]:
        assert step["action"] == "uninstall"
        assert step["target_package_id"] == "Tencent.QQ"
        args = [str(item) for item in list(step.get("args") or [])]
        return {
            "exit_code": 0,
            "command": [str(step.get("executable") or ""), *args],
            "failure_reason": None,
            "stdout_tail": "removed",
            "stderr_tail": "",
            "resolved_package_id": "Tencent.QQ",
        }

    monkeypatch.setattr(
        project_deployments,
        "_resolve_host_package_candidate",
        fake_resolve_host_package_candidate,
    )
    monkeypatch.setattr(
        project_deployments,
        "_execute_host_install_step",
        fake_execute_host_install_step,
    )

    async def fake_detect_installed_version(package_id: str) -> None:
        assert package_id == "Tencent.QQ"
        return None

    monkeypatch.setattr(
        project_deployments,
        "_detect_installed_version",
        fake_detect_installed_version,
    )
    monkeypatch.setattr(
        project_deployments,
        "_detect_installed_version_for_terms",
        lambda terms, package_id=None: fake_detect_installed_version(str(package_id or "")),
    )
    monkeypatch.setattr(
        project_deployments,
        "_install_path_summary",
        lambda package_id, success: "removed_by_package_manager" if success else "not_removed",
    )

    body = _turn(client, "phase52-host-uninstall-confirm", "帮我卸载 QQ。")
    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    plan_id = payload["host_install_plan"]["host_install_plan_id"]
    state = client.get(f"/api/chat/conversations/{body['conversation_id']}/working-state")
    assert state.status_code == 200, state.text
    pending = state.json()["pending_confirmation"]
    assert pending["actions"][0]["approval_id"] == payload["host_install_plan"]["approval_id"]

    confirmed = _turn(
        client,
        "phase52-host-uninstall-confirm",
        "只允许这一次",
        conversation_id=body["conversation_id"],
    )
    assert confirmed["status"] == "completed"
    confirmed_events = _events(client, confirmed["turn_id"])
    text = _reply_from_events(confirmed_events)
    assert "确认" in text
    assert "卸载 QQ" in text
    assert "完成" in text
    assert "approval_id" not in text
    assert "trace_id" not in text
    confirmed_payload = _completed_payload(confirmed_events)["response_plan"]["structured_payload"]
    assert confirmed_payload["natural_interaction"]["status"] == "approved"
    assert confirmed_payload["natural_interaction"]["clear_pending"] is True

    detail = client.get(f"/api/host-installs/{plan_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "uninstalled"

    followup = _turn(
        client,
        "phase52-host-uninstall-confirm",
        "你现在是已经卸完了，还是还在等什么证据？",
        conversation_id=body["conversation_id"],
    )
    assert followup["status"] == "completed"
    followup_text = _reply_from_events(_events(client, followup["turn_id"]))
    assert "已经完成" in followup_text
    assert "等额外证据" in followup_text
    assert "卸载 QQ" in followup_text
    assert "已经开始推进" not in followup_text


def test_phase52_chat_host_install_confirm_requires_post_install_verification(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve_host_package_candidate(
        software: str,
    ) -> project_deployments.HostPackageCandidate:
        assert software in {"TestApp", "install TestApp"}
        return project_deployments.HostPackageCandidate(
            source_type="winget",
            package_id="Vendor.TestApp",
            publisher="Vendor",
            confidence=0.96,
            match_reason="test_install_candidate",
            version="1.0.0",
            name="TestApp",
        )

    async def fake_execute_host_install_step(step: dict[str, Any]) -> dict[str, Any]:
        assert step["action"] == "install"
        return {
            "exit_code": 0,
            "command": [str(step.get("executable") or ""), *list(step.get("args") or [])],
            "failure_reason": None,
            "stdout_tail": "command reported success",
            "stderr_tail": "",
            "resolved_package_id": "Vendor.TestApp",
        }

    async def fake_detect_installed_version(package_id: str) -> None:
        assert package_id.lower() in {"vendor.testapp", "testapp", "vendor"}
        return None

    monkeypatch.setattr(
        project_deployments,
        "_resolve_host_package_candidate",
        fake_resolve_host_package_candidate,
    )
    monkeypatch.setattr(
        project_deployments,
        "_execute_host_install_step",
        fake_execute_host_install_step,
    )
    monkeypatch.setattr(
        project_deployments,
        "_detect_installed_version",
        fake_detect_installed_version,
    )
    monkeypatch.setattr(
        project_deployments,
        "_detect_installed_version_for_terms",
        lambda terms, package_id=None: fake_detect_installed_version(str(package_id or "")),
    )

    body = _turn(client, "phase52-host-install-verify-fails", "帮我安装 TestApp。")
    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    plan_id = payload["host_install_plan"]["host_install_plan_id"]

    confirmed = _turn(
        client,
        "phase52-host-install-verify-fails",
        "只允许这一次",
        conversation_id=body["conversation_id"],
    )
    assert confirmed["status"] == "completed"
    confirmed_events = _events(client, confirmed["turn_id"])
    text = _reply_from_events(confirmed_events)
    assert "没有顺利完成" in text
    assert "完成，跑完啦" not in text
    confirmed_payload = _completed_payload(confirmed_events)["response_plan"]["structured_payload"]
    assert confirmed_payload["natural_interaction"]["status"] == "approved"
    assert confirmed_payload["natural_interaction"]["clear_pending"] is True

    detail = client.get(f"/api/host-installs/{plan_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "failed"


def test_phase52_chat_manifest_id_install_generates_official_plan(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        project_deployments.shutil,
        "which",
        lambda name: "powershell" if name == "powershell" else None,
    )
    monkeypatch.setattr(
        project_deployments,
        "_winget_manifest_candidate_for_query",
        lambda query: project_deployments.HostPackageCandidate(
            source_type="winget_manifest",
            package_id=query,
            publisher="DynamicPublisher",
            confidence=0.96,
            match_reason="official_winget_manifest_dynamic",
            version="1.2.3",
            name=query,
            installer_url="https://example.com/installer.exe",
            installer_sha256="C" * 64,
            installer_type="nullsoft",
            official_manifest="https://raw.githubusercontent.com/example/manifest.yaml",
        ),
    )
    body = _turn(
        client,
        "phase52-host-install-manifest-id",
        "帮我安装 Dynamic.Package。",
    )
    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    text = _reply_from_events(events)
    assert "安装" in text
    assert "确认" in text
    assert any(marker in text for marker in ["还没", "点头", "开工", "动手"])
    assert "受控链路" not in text
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    plan = payload["host_install_plan"]
    assert plan["status"] == "waiting_approval"
    assert plan["approval_id"]
    assert plan["install_source"]["source_type"] == "official_manifest_installer_fallback"
    assert plan["install_source"]["resolved_via"] == "official_winget_manifest"
    assert plan["install_source"]["package_id"].lower() == "dynamic.package"
    assert plan["install_source"]["installer_sha256"]
    assert plan["impact_summary"]["bootstrap_required"] is False
    assert plan["impact_summary"]["bootstrap_skipped_reason"] == (
        "official_manifest_installer_available"
    )
    assert not any(
        step["step_type"] == "package_manager_bootstrap"
        for step in plan["command_preview"]["steps"]
    )
    assert plan["command_preview"]["steps"][0]["step_type"] == "official_manifest_installer"
    assert plan["command_preview"]["steps"][-1]["target_package_id"].lower() == "dynamic.package"


def test_phase52_chat_qq_prefers_official_manifest_over_choco(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve_host_package_candidate(
        software: str,
    ) -> project_deployments.HostPackageCandidate:
        assert software == "QQ"
        return project_deployments.HostPackageCandidate(
            source_type="choco",
            package_id="tencentqq",
            publisher="Chocolatey community package",
            confidence=0.90,
            match_reason="choco_exact_search",
            version="9.9.9",
            name="tencentqq",
        )

    async def fake_manifest_candidate(
        *_: Any, **__: Any
    ) -> project_deployments.HostPackageCandidate:
        return project_deployments.HostPackageCandidate(
            source_type="winget_manifest",
            package_id="Tencent.QQ",
            publisher="Tencent",
            confidence=0.92,
            match_reason="official_winget_manifest_dynamic",
            version="9.9.19",
            name="QQ",
            installer_url="https://dldir1.qq.com/qqfile/qq/QQNT/Windows/QQ.exe",
            installer_sha256="F" * 64,
            installer_type="nullsoft",
            official_manifest="https://raw.githubusercontent.com/example/Tencent.QQ.installer.yaml",
        )

    monkeypatch.setattr(
        project_deployments,
        "_resolve_host_package_candidate",
        fake_resolve_host_package_candidate,
    )
    monkeypatch.setattr(
        project_deployments,
        "_resolve_winget_manifest_candidate",
        fake_manifest_candidate,
    )
    monkeypatch.setattr(
        project_deployments,
        "_official_source_assisted_manifest_lookup",
        lambda software: None,
    )
    body = _turn(client, "phase52-host-install-qq-official", "帮我安装 QQ。")
    assert body["status"] == "completed"
    events = _events(client, body["turn_id"])
    payload = _completed_payload(events)["response_plan"]["structured_payload"]
    plan = payload["host_install_plan"]
    assert plan["install_source"]["source_type"] == "official_manifest_installer_primary"
    assert plan["install_source"]["resolved_via"] == "official_winget_manifest_installer_primary"
    assert plan["install_source"]["package_id"] == "Tencent.QQ"
    assert plan["install_source"]["preferred_over_source"]["package_id"] == "tencentqq"
    steps = plan["command_preview"]["steps"]
    assert steps[0]["step_type"] == "official_manifest_installer"
    assert steps[0]["target_package_id"] == "Tencent.QQ"
    assert all(step["step_key"] != "choco_install_tencentqq" for step in steps)


def test_phase52_release_eval_and_diagnostic_summary(client: TestClient) -> None:
    suites = client.get("/api/evals/suites")
    assert suites.status_code == 200, suites.text
    ids = {item["suite_id"] for item in suites.json()["items"]}
    assert "suite_phase52_chat_deploy_install" in ids

    run = client.post("/api/evals/runs", json={"suite_id": "suite_phase52_chat_deploy_install"})
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run")
    assert completed.status_code == 200, completed.text
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report")
    assert report.status_code == 200, report.text
    summary = report.json()["summary"]
    assert "phase52" in summary
    assert summary["phase52"]["suite_id"] == "suite_phase52_chat_deploy_install"
    assert "phase52" in summary["phase23"]["capability_scores"]

    diagnostic = client.post("/api/diagnostics/bundles", json={})
    assert diagnostic.status_code == 200, diagnostic.text
    registry = cast(Any, client.app).state.registry
    diagnostic_path = (
        registry.config.storage.data_dir
        / "diagnostics"
        / f"{diagnostic.json()['bundle_id']}.json"
    )
    diag_summary = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert "phase52_chat_deploy_host_install" in diag_summary


def test_phase52_chat_host_uninstall_repeat_request_still_targets_qq(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolve_host_package_candidate(
        software: str,
    ) -> project_deployments.HostPackageCandidate:
        assert software == "uninstall QQ"
        return project_deployments.HostPackageCandidate(
            source_type="winget",
            package_id="Tencent.QQ",
            publisher="Tencent",
            confidence=0.96,
            match_reason="test_uninstall_candidate",
            version="1.0.0",
            name="QQ",
        )

    monkeypatch.setattr(
        project_deployments,
        "_resolve_windows_uninstall_candidate",
        lambda software: None,
    )
    monkeypatch.setattr(
        project_deployments,
        "_windows_uninstall_lookup_supported",
        lambda: False,
    )
    monkeypatch.setattr(
        project_deployments,
        "_resolve_host_package_candidate",
        fake_resolve_host_package_candidate,
    )

    first = _turn(client, "phase52-host-uninstall-repeat", "帮我卸载 QQ。")
    assert first["status"] == "completed"

    second = _turn(
        client,
        "phase52-host-uninstall-repeat",
        "再帮我卸载 QQ。",
        conversation_id=first["conversation_id"],
    )
    assert second["status"] == "completed"
    payload = _completed_payload(_events(client, second["turn_id"]))["response_plan"][
        "structured_payload"
    ]
    assert payload["host_install_plan"]["requested_software"] == "QQ"


def _turn(
    client: TestClient,
    session_id: str,
    text: str,
    *,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    created = client.post(
        "/api/chat/turn",
        json={
            "member_id": "mem_xiaoyao",
            "session_id": session_id,
            "conversation_id": conversation_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert created.status_code == 200, created.text
    turn_id = created.json()["turn_id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        detail = client.get(f"/api/chat/turns/{turn_id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"turn {turn_id} did not finish")


def _events(client: TestClient, turn_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/chat/turns/{turn_id}/events")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _reply_from_events(events: list[dict[str, Any]]) -> str:
    return "".join(
        str(item.get("payload", {}).get("payload", {}).get("text", ""))
        for item in events
        if item["event_type"] == "response.delta"
    )


def _completed_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    completed = next(item for item in events if item["event_type"] == "response.completed")
    return dict(completed["payload"]["payload"])
