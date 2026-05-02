from __future__ import annotations

import json
from typing import Any

import pytest
from app.services import project_deployments
from fastapi.testclient import TestClient


async def _fake_execute_host_install_step(step: dict[str, Any]) -> dict[str, Any]:
    assert step.get("action") in {"install", "uninstall"}
    assert step.get("target_package_id")
    args = [str(item) for item in list(step.get("args") or [])]
    return {
        "exit_code": 0,
        "command": [str(step.get("executable") or ""), *args],
        "failure_reason": None,
        "stdout_tail": "phase52 fake host software change completed",
        "stderr_tail": "",
        "resolved_package_id": str(step.get("target_package_id") or ""),
    }


def test_phase52_host_install_plan_requires_approval_then_auto_executes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        project_deployments,
        "_execute_host_install_step",
        _fake_execute_host_install_step,
    )

    async def fake_detect_installed_version(package_id: str) -> str:
        assert package_id
        return "fake-installed-version"

    monkeypatch.setattr(
        project_deployments,
        "_detect_installed_version",
        fake_detect_installed_version,
    )
    plan_response = client.post(
        "/api/host-installs/plan",
        json={"requested_software": "jq"},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["status"] == "waiting_approval"
    assert plan["approval_id"]
    assert plan["install_source"]["source_type"] in {"winget", "choco"}
    assert plan["install_source"]["trust"] in {
        "package_manager_search_verified",
        "official_package_manager_dynamic",
    }
    assert plan["install_source"]["match_confidence"] >= 0.86
    assert plan["command_preview"]["executable"] in {"winget", "choco"}
    assert plan["install_source"]["package_id"] == "jq"
    assert plan["impact_summary"]["modifies_global_environment"] is True

    denied = client.post(
        f"/api/host-installs/{plan['host_install_plan_id']}/execute",
        json={"dry_run": True},
    )
    assert denied.status_code == 409

    approved = client.post(
        f"/api/approvals/{plan['approval_id']}/approve",
        json={"reason": "phase52 dry-run"},
    )
    assert approved.status_code == 200, approved.text
    approved_body = approved.json()
    assert approved_body["status"] == "completed"
    assert approved_body["result"]["workflow"] == "host_install"
    assert approved_body["result"]["host_action"] == "install"
    assert approved_body["result"]["status"] == "installed"
    assert approved_body["result"]["host_install_execution_id"]

    detail = client.get(f"/api/host-installs/{plan['host_install_plan_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "installed"

    repeated = client.post(
        f"/api/host-installs/{plan['host_install_plan_id']}/execute",
        json={"approval_id": plan["approval_id"], "dry_run": True},
    )
    assert repeated.status_code == 409


def test_phase52_unknown_or_dangerous_installer_is_manual_only(client: TestClient) -> None:
    unknown_response = client.post(
        "/api/host-installs/plan",
        json={"requested_software": "not-a-real-cycber-tool-zzzz"},
    )
    assert unknown_response.status_code == 200, unknown_response.text
    unknown = unknown_response.json()
    assert unknown["status"] == "manual_only"
    assert unknown["approval_id"] is None
    assert unknown["install_source"]["reason"] == "package_manager_candidate_unavailable"

    plan_response = client.post(
        "/api/host-installs/plan",
        json={"requested_software": "wallet browser extension driver"},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["status"] == "manual_only"
    assert plan["approval_id"] is None
    assert plan["impact_summary"]["manual_only"] is True

    executed = client.post(
        f"/api/host-installs/{plan['host_install_plan_id']}/execute",
        json={"approval_id": "anything", "dry_run": True},
    )
    assert executed.status_code == 403


@pytest.mark.asyncio
async def test_phase52_wechat_install_uses_official_manifest_seed_when_package_search_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_package_candidate(software: str) -> None:
        return None

    monkeypatch.setattr(
        project_deployments,
        "_resolve_host_package_candidate",
        fake_package_candidate,
    )
    monkeypatch.setattr(
        project_deployments.shutil,
        "which",
        lambda name: "powershell" if name == "powershell" else None,
    )

    def fake_manifest(query: str) -> project_deployments.HostPackageCandidate | None:
        if query.lower() != "tencent.wechat.universal":
            return None
        return project_deployments.HostPackageCandidate(
            source_type="winget_manifest",
            package_id="Tencent.WeChat.Universal",
            publisher="Tencent",
            confidence=0.96,
            match_reason="official_winget_manifest_dynamic",
            version="4.1.9.30",
            name="微信",
            installer_url="https://dldir1v6.qq.com/weixin/Universal/Windows/WeChatWin.exe",
            installer_sha256="D" * 64,
            installer_type="nullsoft",
            official_manifest=(
                "https://raw.githubusercontent.com/microsoft/winget-pkgs/master/"
                "manifests/t/Tencent/WeChat/Universal/4.1.9.30/"
                "Tencent.WeChat.Universal.installer.yaml"
            ),
        )

    monkeypatch.setattr(
        project_deployments,
        "_winget_manifest_candidate_for_query",
        fake_manifest,
    )

    source, command, impact, status = await project_deployments._host_install_plan_for("微信")

    assert status == "waiting_approval"
    assert source["source_type"] in {"winget", "official_manifest_installer_fallback"}
    assert source["package_id"] == "Tencent.WeChat.Universal"
    assert source["official_source_assisted"] is True
    assert source["resolved_via"] in {
        "official_winget_manifest",
        "official_source_assisted_winget_manifest",
    }
    assert command["steps"][-1]["target_package_id"] == "Tencent.WeChat.Universal"
    assert impact["package_resolution"]["official_source_assisted"] is True
    assert impact["checksum_verification"] == "sha256"


@pytest.mark.asyncio
async def test_phase52_wecom_install_prefers_enterprise_wechat_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_package_candidate(software: str) -> None:
        return None

    monkeypatch.setattr(
        project_deployments,
        "_resolve_host_package_candidate",
        fake_package_candidate,
    )
    monkeypatch.setattr(
        project_deployments.shutil,
        "which",
        lambda name: "powershell" if name == "powershell" else None,
    )

    def fake_manifest(query: str) -> project_deployments.HostPackageCandidate | None:
        if query != "Tencent.WeCom":
            return None
        return project_deployments.HostPackageCandidate(
            source_type="winget_manifest",
            package_id="Tencent.WeCom",
            publisher="Tencent",
            confidence=0.96,
            match_reason="official_winget_manifest_dynamic",
            version="5.0.8.6009",
            name="企业微信",
            installer_url="https://dldir1v6.qq.com/wework/work_weixin/WeCom_5.0.8.6009.exe",
            installer_sha256="E" * 64,
            installer_type="nullsoft",
            official_manifest=(
                "https://raw.githubusercontent.com/microsoft/winget-pkgs/master/"
                "manifests/t/Tencent/WeCom/5.0.8.6009/Tencent.WeCom.installer.yaml"
            ),
        )

    monkeypatch.setattr(
        project_deployments,
        "_winget_manifest_candidate_for_query",
        fake_manifest,
    )

    source, command, impact, status = await project_deployments._host_install_plan_for(
        "企业微信"
    )

    assert status == "waiting_approval"
    assert source["package_id"] == "Tencent.WeCom"
    assert source["package_id"] != "Tencent.WeChat"
    assert command["steps"][-1]["target_package_id"] == "Tencent.WeCom"
    assert impact["package_resolution"]["target_package_id"] == "Tencent.WeCom"


@pytest.mark.asyncio
async def test_phase52_official_website_candidate_requires_trusted_https_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_package_candidate(software: str) -> None:
        return None

    async def fake_resolver(query: str) -> list[project_deployments.HostSoftwareModelCandidate]:
        return [
            project_deployments.HostSoftwareModelCandidate(
                query="BadChat",
                confidence=0.95,
                publisher_hints=("BadVendor",),
                official_sites=("https://evil.example/badchat",),
                download_pages=("http://trusted.example/badchat.exe",),
                vendor_domains=("trusted.example",),
            )
        ]

    monkeypatch.setattr(
        project_deployments,
        "_resolve_host_package_candidate",
        fake_package_candidate,
    )
    monkeypatch.setattr(
        project_deployments,
        "_winget_manifest_candidate_for_query",
        lambda query: None,
    )
    monkeypatch.setattr(
        project_deployments,
        "_http_response_metadata",
        lambda *args, **kwargs: {
            "url": "https://evil.example/wechat.exe",
            "status": 200,
            "content_type": "application/octet-stream",
            "content_length": 100,
            "body": b"",
            "redirect_chain": ["https://evil.example/wechat.exe"],
        },
    )

    source, command, impact, status = await project_deployments._host_install_plan_for(
        "安装 BadChat",
        model_candidates_provider=fake_resolver,
    )

    assert status == "manual_only"
    assert command == {}
    assert source["source_type"] == "manual_only"
    assert impact["reason_codes"] == ["no_high_confidence_healthy_package_candidate"]


def test_phase52_host_uninstall_approval_auto_executes(
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
        "_install_path_summary",
        lambda package_id, success: "removed_by_package_manager" if success else "not_removed",
    )

    plan_response = client.post(
        "/api/host-installs/plan",
        json={"requested_software": "uninstall QQ"},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["status"] == "waiting_approval"
    assert plan["approval_id"]
    assert plan["command_preview"]["action"] == "uninstall"

    approved = client.post(
        f"/api/approvals/{plan['approval_id']}/approve",
        json={"reason": "phase52 uninstall confirmed"},
    )
    assert approved.status_code == 200, approved.text
    task = approved.json()
    assert task["status"] == "completed"
    assert task["result"]["workflow"] == "host_install"
    assert task["result"]["host_action"] == "uninstall"
    assert task["result"]["status"] == "uninstalled"
    assert task["result"]["exit_code"] == 0
    assert task["result"]["log_artifact_id"]

    detail = client.get(f"/api/host-installs/{plan['host_install_plan_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "uninstalled"


def test_phase52_windows_registry_uninstall_plan_auto_executes(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        project_deployments,
        "_resolve_windows_uninstall_candidate",
        lambda software: project_deployments.WindowsUninstallCandidate(
            display_name="QQ",
            uninstall_string='"C:\\Program Files\\Tencent\\QQNT\\Uninstall.exe"',
            confidence=0.99,
            match_reason="windows_uninstall_registry",
            version="9.9.29",
            publisher="Tencent",
            registry_key="QQ",
        ),
    )
    monkeypatch.setattr(
        project_deployments,
        "_safe_windows_uninstall_executable",
        lambda executable: True,
    )

    async def fake_execute_host_install_step(step: dict[str, Any]) -> dict[str, Any]:
        assert step["step_type"] == "windows_uninstall_registry"
        assert step["action"] == "uninstall"
        assert step["target_display_name"] == "QQ"
        args = [str(item) for item in list(step.get("args") or [])]
        return {
            "exit_code": 0,
            "command": list(step.get("command_redacted") or [str(step.get("executable")), *args]),
            "failure_reason": None,
            "stdout_tail": "removed",
            "stderr_tail": "",
            "resolved_package_id": "QQ",
        }

    versions = iter(["9.9.29", None, None])

    async def fake_detect_installed_version(package_id: str) -> str | None:
        assert package_id == "QQ"
        return next(versions)

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
        "_wait_for_windows_uninstall",
        fake_detect_installed_version,
    )
    monkeypatch.setattr(
        project_deployments,
        "_install_path_summary",
        lambda package_id, success: "removed_by_windows_uninstaller" if success else "not_removed",
    )

    plan_response = client.post(
        "/api/host-installs/plan",
        json={"requested_software": "uninstall QQ"},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["status"] == "waiting_approval"
    assert plan["install_source"]["source_type"] == "windows_uninstall_registry"
    assert plan["install_source"]["display_name"] == "QQ"
    assert plan["command_preview"]["steps"][0]["step_type"] == "windows_uninstall_registry"

    approved = client.post(
        f"/api/approvals/{plan['approval_id']}/approve",
        json={"reason": "phase52 windows registry uninstall confirmed"},
    )
    assert approved.status_code == 200, approved.text
    task = approved.json()
    assert task["status"] == "completed"
    assert task["result"]["workflow"] == "host_install"
    assert task["result"]["host_action"] == "uninstall"
    assert task["result"]["status"] == "uninstalled"

    detail = client.get(f"/api/host-installs/{plan['host_install_plan_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "uninstalled"


def test_phase52_windows_registry_uninstall_is_idempotent_when_absent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        project_deployments,
        "_resolve_windows_uninstall_candidate",
        lambda software: None,
    )
    monkeypatch.setattr(
        project_deployments,
        "_windows_uninstall_lookup_supported",
        lambda: True,
    )

    plan_response = client.post(
        "/api/host-installs/plan",
        json={"requested_software": "uninstall QQ"},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["status"] == "already_absent"
    assert plan["approval_id"] is None
    assert plan["install_source"]["already_absent"] is True
    assert plan["command_preview"]["steps"][0]["step_type"] == "windows_uninstall_absent"

    detail = client.get(f"/api/host-installs/{plan['host_install_plan_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "already_absent"


@pytest.mark.asyncio
async def test_phase52_uninstall_tries_model_candidates_before_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_resolver(query: str) -> list[project_deployments.HostSoftwareModelCandidate]:
        calls.append(query)
        return [
            project_deployments.HostSoftwareModelCandidate(
                query="Weixin",
                display_names=("微信",),
                aliases=("wechat",),
                confidence=0.97,
                reason="common display name",
            )
        ]

    monkeypatch.setattr(
        project_deployments,
        "_resolve_windows_uninstall_candidate",
        lambda software: None,
    )
    monkeypatch.setattr(
        project_deployments,
        "_windows_uninstall_lookup_supported",
        lambda: True,
    )

    source, command, impact, status = await project_deployments._host_install_plan_for(
        "uninstall wechat",
        model_candidates_provider=fake_resolver,
    )

    assert calls == ["wechat"]
    assert status == "already_absent"
    assert source["already_absent"] is True
    assert source["model_assisted"] is True
    assert source["model_candidate_count"] == 1
    assert source["resolved_via"] == "trusted_sources_absence_verified"
    assert command["steps"][0]["step_type"] == "windows_uninstall_absent"
    assert impact["package_resolution"]["model_assisted"] is True
    assert any(
        item["source_type"] == "windows_uninstall_registry_model_candidates"
        for item in source["candidate_attempts"]
    )


@pytest.mark.asyncio
async def test_phase52_model_alias_can_resolve_uninstall_registry_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_queries: list[str] = []

    def fake_windows_candidate(query: str) -> project_deployments.WindowsUninstallCandidate | None:
        seen_queries.append(query)
        if "微信" not in query:
            return None
        return project_deployments.WindowsUninstallCandidate(
            display_name="微信",
            uninstall_string=r'"C:\Program Files\Tencent\Weixin\Uninstall.exe"',
            confidence=0.99,
            match_reason="windows_uninstall_registry",
            version="4.1.9.30",
            publisher="Tencent",
        )

    async def fake_resolver(query: str) -> list[project_deployments.HostSoftwareModelCandidate]:
        assert query == "wechat"
        return [
            project_deployments.HostSoftwareModelCandidate(
                query="WeChat",
                display_names=("微信",),
                aliases=("Weixin",),
                confidence=0.96,
                reason="common Chinese Windows display name",
            )
        ]

    monkeypatch.setattr(
        project_deployments,
        "_resolve_windows_uninstall_candidate",
        fake_windows_candidate,
    )
    monkeypatch.setattr(
        project_deployments,
        "_windows_uninstall_lookup_supported",
        lambda: True,
    )
    monkeypatch.setattr(
        project_deployments,
        "_safe_windows_uninstall_executable",
        lambda executable: True,
    )

    source, command, impact, status = await project_deployments._host_install_plan_for(
        "uninstall wechat",
        model_candidates_provider=fake_resolver,
    )

    assert status == "waiting_approval"
    assert source["source_type"] == "windows_uninstall_registry"
    assert source["display_name"] == "微信"
    assert source["model_assisted"] is True
    assert source["resolved_via"] == "model_assisted_windows_uninstall_registry"
    assert command["steps"][0]["target_display_name"] == "微信"
    assert impact["package_resolution"]["final_match_confidence"] >= 0.9
    assert any("微信" in query for query in seen_queries)


def test_phase52_unsafe_model_candidates_are_ignored() -> None:
    payload = {
        "candidates": [
            {
                "query": "winget uninstall Evil.App --silent",
                "display_names": ["https://example.com/bad.exe", "QQ"],
                "package_ids": ["Tencent.QQ"],
                "aliases": ["powershell Remove-Item"],
                "confidence": 0.98,
            }
        ]
    }
    candidates = project_deployments._parse_host_package_model_candidates(
        json.dumps(payload)
    )

    assert len(candidates) == 1
    terms = project_deployments._model_candidate_lookup_terms(candidates[0])
    assert "Tencent.QQ" in terms
    assert "QQ" in terms
    assert all("http" not in term.lower() for term in terms)
    assert all("powershell" not in term.lower() for term in terms)
    assert all("uninstall" not in term.lower() for term in terms)


def test_phase52_windows_registry_matches_chinese_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        project_deployments,
        "_windows_uninstall_entries",
        lambda query: [
            {
                "DisplayName": "微信",
                "DisplayVersion": "4.1.9.30",
                "Publisher": "Tencent",
                "InstallLocation": r"C:\Program Files\Tencent\Weixin",
                "UninstallString": r'"C:\Program Files\Tencent\Weixin\Uninstall.exe"',
                "QuietUninstallString": "",
                "RegistryKey": "Weixin",
            }
        ],
    )

    candidate = project_deployments._resolve_windows_uninstall_candidate("uninstall 微信")

    assert candidate is not None
    assert candidate.display_name == "微信"
    assert candidate.confidence == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_phase52_official_winget_manifest_id_bootstraps_package_manager(
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
            installer_sha256="A" * 64,
            installer_type="nullsoft",
            official_manifest=(
                "https://raw.githubusercontent.com/microsoft/winget-pkgs/master/"
                "manifests/d/Dynamic/Package/1.2.3/Dynamic.Package.installer.yaml"
            ),
        ),
    )

    source, command, impact, status = await project_deployments._host_install_plan_for(
        "Dynamic.Package"
    )

    assert status == "waiting_approval"
    assert source["source_type"] == "winget"
    assert source["package_id"].lower() == "dynamic.package"
    assert source["trust"] == "official_package_manager_dynamic"
    assert source["official_manifest"].startswith("https://")
    assert impact["risk_level"] == "R6"
    assert impact["bootstrap_required"] is True
    assert impact["bootstrap_package_manager"] == "winget"
    assert impact["checksum_verification"] == "sha256"
    assert command["steps"][0]["step_type"] == "package_manager_bootstrap"
    assert command["steps"][-1]["package_manager"] == "winget"
    assert command["steps"][-1]["target_package_id"].lower() == "dynamic.package"
    assert command["fallback_steps"][0]["step_type"] == "official_manifest_installer"


def test_phase52_jq_uses_chocolatey_approval_plan(client: TestClient) -> None:
    plan_response = client.post(
        "/api/host-installs/plan",
        json={"requested_software": "jq"},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["status"] == "waiting_approval"
    assert plan["approval_id"]
    assert plan["risk_level"] == "R5"
    assert plan["install_source"]["source_type"] == "choco"
    assert plan["install_source"]["package_id"] == "jq"
    assert plan["command_preview"]["executable"] == "choco"
    assert plan["command_preview"]["args"][:2] == ["install", "jq"]
    assert plan["command_preview"]["steps"][0]["target_package_id"] == "jq"


def test_phase52_yq_uses_chocolatey_approval_plan(client: TestClient) -> None:
    plan_response = client.post(
        "/api/host-installs/plan",
        json={"requested_software": "yq"},
    )
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    assert plan["status"] == "waiting_approval"
    assert plan["approval_id"]
    assert plan["risk_level"] == "R5"
    assert plan["install_source"]["source_type"] == "choco"
    assert plan["install_source"]["package_id"] == "yq"
    assert plan["command_preview"]["executable"] == "choco"
    assert plan["command_preview"]["args"][:2] == ["install", "yq"]
    assert plan["command_preview"]["steps"][0]["target_package_id"] == "yq"


@pytest.mark.asyncio
async def test_phase52_model_candidate_can_resolve_unknown_display_name_to_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_resolver(query: str) -> list[project_deployments.HostSoftwareModelCandidate]:
        assert query == "某个中文软件"
        return [
            project_deployments.HostSoftwareModelCandidate(
                query="dynamic package",
                package_id="Dynamic.Package",
                source_type="winget",
                confidence=0.97,
                reason="model suggested official winget id",
            )
        ]

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
            installer_sha256="B" * 64,
            installer_type="nullsoft",
            official_manifest="https://raw.githubusercontent.com/example/manifest.yaml",
        ),
    )
    source, command, impact, status = await project_deployments._host_install_plan_for(
        "某个中文软件",
        model_candidates_provider=fake_resolver,
    )

    assert status == "waiting_approval"
    assert source["source_type"] == "winget"
    assert source["package_id"].lower() == "dynamic.package"
    assert source["trust"] == "official_package_manager_dynamic"
    assert command["steps"][0]["step_type"] == "package_manager_bootstrap"
    assert command["steps"][-1]["target_package_id"].lower() == "dynamic.package"
    assert command["fallback_steps"][0]["sha256"]
    assert impact["checksum_verification"] == "sha256"
    assert impact["bootstrap_required"] is True


def test_phase52_short_package_suffix_can_resolve_choco_candidate() -> None:
    assert project_deployments._host_package_confidence(
        "qq",
        "tencentqq",
        "tencentqq",
        exact=False,
    ) == pytest.approx(0.9)
