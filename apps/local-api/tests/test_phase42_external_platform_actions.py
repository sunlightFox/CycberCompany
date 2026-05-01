from __future__ import annotations

import json
from typing import Any, cast

from core_types import RiskLevel
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase42_resolver_targets_and_no_account_recovery(client: TestClient) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase42")
    assert migration_contract["required_migration"] == "030_external_platform_actions.sql"

    targets = client.get("/api/external-platform/targets").json()["items"]
    assert any(item["platform_key"] == "fake_platform" for item in targets)

    resolved = _resolve(
        client,
        "帮我在某平台发布一篇文章，内容：Phase42 后端编排验收摘要。",
    )
    intent = resolved["intent"]
    assert intent["status"] == "resolved"
    assert intent["platform_key"] == "fake_platform"
    assert intent["action_type"] == "publish_content"
    assert intent["resolver_evidence"]["platform_from_target_alias"] is True

    candidates = client.post(
        "/api/external-platform/account-candidates",
        json={"intent_id": intent["intent_id"]},
    )
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["status"] == "no_account"

    plan = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent["intent_id"]},
    )
    assert plan.status_code == 200, plan.text
    payload = plan.json()
    assert payload["plan"]["status"] == "awaiting_account"
    assert payload["plan"]["failure_reason"] == "no_account_asset_candidate"
    assert "不会声称已登录或已发布" in payload["message"]
    assert _payload_leakage_count(payload) == 0


def test_phase42_user_message_without_platform_requires_clarification(
    client: TestClient,
) -> None:
    resolved = _resolve(
        client,
        "帮我发一篇文章，内容：没有平台时不能自动猜测。",
    )
    intent = resolved["intent"]
    assert intent["status"] == "clarification_needed"
    assert intent["platform_key"] is None
    assert "platform" in intent["missing_fields"]
    assert "还缺少 platform" in resolved["message"]

    candidates = client.post(
        "/api/external-platform/account-candidates",
        json={"intent_id": intent["intent_id"]},
    )
    assert candidates.status_code == 200, candidates.text
    assert candidates.json()["status"] == "missing_platform"
    assert "还缺少平台信息" in candidates.json()["message"]

    plan = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent["intent_id"]},
    )
    assert plan.status_code == 200, plan.text
    payload = plan.json()
    assert payload["plan"]["status"] == "awaiting_intent_clarification"
    assert payload["plan"]["failure_reason"] == "intent_missing_fields"
    assert payload["plan"]["platform_key"] is None
    assert "缺少关键信息" in payload["message"]
    assert _payload_leakage_count(payload) == 0


def test_phase42_configured_social_platform_without_real_provider_stops_safely(
    client: TestClient,
) -> None:
    target = _create_social_platform_target(client)

    no_account_intent = _resolve(
        client,
        "帮我在小红书发一篇文章，内容：真实社交平台边界测试。",
    )["intent"]
    assert no_account_intent["status"] == "resolved"
    assert no_account_intent["platform_key"] == target["platform_key"]
    assert no_account_intent["resolver_evidence"]["platform_from_target_alias"] is True

    no_account_plan = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": no_account_intent["intent_id"], "execution_mode": "browser"},
    )
    assert no_account_plan.status_code == 200, no_account_plan.text
    no_account_payload = no_account_plan.json()
    assert no_account_payload["plan"]["status"] == "awaiting_account"
    assert no_account_payload["plan"]["failure_reason"] == "no_account_asset_candidate"
    assert "不会声称已登录或已发布" in no_account_payload["message"]

    account = _create_account(
        client,
        display_name="小红书品牌号",
        provider_key=target["platform_key"],
    )
    _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)

    publish_intent = _resolve(
        client,
        "帮我在小红书发一篇文章，内容：真实社交平台边界测试。",
    )["intent"]
    candidates = client.post(
        "/api/external-platform/account-candidates",
        json={"intent_id": publish_intent["intent_id"]},
    ).json()
    assert candidates["status"] == "single_candidate"
    assert candidates["candidates"][0]["display_name"] == "小红书品牌号"
    assert candidates["candidates"][0]["secret_material_visible"] is False

    created = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": publish_intent["intent_id"], "execution_mode": "browser"},
    )
    assert created.status_code == 200, created.text
    plan_payload = created.json()
    plan = plan_payload["plan"]
    assert plan["status"] == "awaiting_approval"
    assert plan["selected_asset_id"] == account["asset_id"]
    assert plan_payload["target"]["display_name"] == "小红书"
    assert plan_payload["approval"]["status"] == "pending"

    pending = client.post(f"/api/external-platform/action-plans/{plan['plan_id']}/execute")
    assert pending.status_code == 200, pending.text
    assert pending.json()["plan"]["status"] == "awaiting_approval"
    assert "不会自动发布" in pending.json()["message"]

    approved = client.post(
        f"/api/approvals/{plan['approval_id']}/approve",
        json={"reason": "phase42 social platform boundary"},
    )
    assert approved.status_code == 200, approved.text

    executed = client.post(f"/api/external-platform/action-plans/{plan['plan_id']}/execute")
    assert executed.status_code == 200, executed.text
    executed_payload = executed.json()
    assert executed_payload["plan"]["status"] == "failed"
    assert executed_payload["plan"]["failure_reason"] == "browser_provider_not_configured"
    assert "未执行外部提交" in executed_payload["message"]
    assert executed_payload["plan"]["evidence"]["provider_registry"] == {
        "provider_key": "browser",
        "execution_modes": ["browser"],
        "real_external_platform_integration": False,
    }
    assert any(item["step_type"] == "provider_boundary" for item in executed_payload["executions"])
    assert _payload_leakage_count(executed_payload) == 0


def test_phase42_configured_social_platform_multiple_accounts_asks_user(
    client: TestClient,
) -> None:
    target = _create_social_platform_target(client)
    ops = _create_account(
        client,
        display_name="小红书运营号",
        provider_key=target["platform_key"],
    )
    brand = _create_account(
        client,
        display_name="小红书品牌号",
        provider_key=target["platform_key"],
    )
    for account in [ops, brand]:
        _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)

    intent = _resolve(
        client,
        "请在小红书发布文章，内容：多账号真实使用边界测试。",
    )["intent"]
    created = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent["intent_id"], "execution_mode": "browser"},
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["plan"]["status"] == "awaiting_clarification"
    assert payload["plan"]["selected_asset_id"] is None
    assert len(payload["candidates"]) == 2
    assert "需要你先选择一个账号" in payload["message"]

    clarified = client.post(
        f"/api/external-platform/action-plans/{payload['plan']['plan_id']}/clarify",
        json={"text": "用小红书品牌号"},
    )
    assert clarified.status_code == 200, clarified.text
    clarified_payload = clarified.json()
    assert clarified_payload["plan"]["status"] == "awaiting_approval"
    assert clarified_payload["plan"]["selected_asset_id"] == brand["asset_id"]
    assert clarified_payload["approval"]["status"] == "pending"
    assert _payload_leakage_count(clarified_payload) == 0


def test_phase42_single_account_approval_and_fake_provider_execution(
    client: TestClient,
) -> None:
    account = _create_account(client, display_name="运营账号 A")
    _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)

    intent = _resolve(
        client,
        "请用某平台发布文章，内容：今天的公告已经完成后端验收。",
    )["intent"]
    candidates = client.post(
        "/api/external-platform/account-candidates",
        json={"intent_id": intent["intent_id"]},
    ).json()
    assert candidates["status"] == "single_candidate"
    assert candidates["candidates"][0]["asset_id"] == account["asset_id"]
    assert candidates["candidates"][0]["secret_material_visible"] is False

    created = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent["intent_id"]},
    )
    assert created.status_code == 200, created.text
    plan_payload = created.json()
    plan = plan_payload["plan"]
    assert plan["status"] == "awaiting_approval"
    assert plan["selected_asset_id"] == account["asset_id"]
    assert plan["approval_id"]
    assert plan["task_id"]
    assert plan_payload["approval"]["status"] == "pending"
    assert "phase42-secret-token" not in json.dumps(plan_payload, ensure_ascii=False)

    pending_execute = client.post(f"/api/external-platform/action-plans/{plan['plan_id']}/execute")
    assert pending_execute.status_code == 200, pending_execute.text
    assert pending_execute.json()["plan"]["status"] == "awaiting_approval"
    assert "等待审批" in pending_execute.json()["message"]

    approved = client.post(
        f"/api/approvals/{plan['approval_id']}/approve",
        json={"reason": "phase42 controlled publish"},
    )
    assert approved.status_code == 200, approved.text

    executed = client.post(f"/api/external-platform/action-plans/{plan['plan_id']}/execute")
    assert executed.status_code == 200, executed.text
    executed_payload = executed.json()
    assert executed_payload["plan"]["status"] == "completed"
    assert executed_payload["executions"]
    assert any(item["step_type"] == "submit_publish" for item in executed_payload["executions"])
    assert executed_payload["plan"]["evidence"]["provider_result"]["executor"] == "fake_provider"
    assert _payload_leakage_count(executed_payload) == 0


def test_phase42_multiple_accounts_require_clarification_and_can_continue(
    client: TestClient,
) -> None:
    ops = _create_account(client, display_name="运营账号 A")
    brand = _create_account(client, display_name="品牌账号 B")
    for account in [ops, brand]:
        _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)

    intent = _resolve(
        client,
        "帮我在某平台发动态，内容：多账号澄清链路验证。",
    )["intent"]
    created = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent["intent_id"]},
    )
    assert created.status_code == 200, created.text
    plan = created.json()["plan"]
    assert plan["status"] == "awaiting_clarification"
    assert plan["selected_asset_id"] is None
    assert len(created.json()["candidates"]) == 2

    clarified = client.post(
        f"/api/external-platform/action-plans/{plan['plan_id']}/clarify",
        json={"text": "用品牌账号 B"},
    )
    assert clarified.status_code == 200, clarified.text
    clarified_payload = clarified.json()
    assert clarified_payload["plan"]["status"] == "awaiting_approval"
    assert clarified_payload["plan"]["selected_asset_id"] == brand["asset_id"]
    assert clarified_payload["approval"]["status"] == "pending"
    assert _payload_leakage_count(clarified_payload) == 0


def test_phase42_approval_deny_cancels_and_sensitive_content_blocks(
    client: TestClient,
) -> None:
    account = _create_account(client, display_name="安全账号")
    _grant(client, account["asset_id"], "publish_content", RiskLevel.R4)

    intent = _resolve(
        client,
        "帮我在某平台发布，内容：这次会取消，不应该真的提交。",
    )["intent"]
    created = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": intent["intent_id"]},
    ).json()
    denied = client.post(
        f"/api/approvals/{created['plan']['approval_id']}/deny",
        json={"reason": "phase42 deny"},
    )
    assert denied.status_code == 200, denied.text
    executed = client.post(
        f"/api/external-platform/action-plans/{created['plan']['plan_id']}/execute"
    )
    assert executed.status_code == 200, executed.text
    payload = executed.json()
    assert payload["plan"]["status"] == "cancelled"
    assert payload["executions"] == []

    sensitive = _resolve(
        client,
        "帮我在某平台发布，内容：token=phase42-raw-secret password=phase42-pass",
    )["intent"]
    blocked = client.post(
        "/api/external-platform/action-plans",
        json={"intent_id": sensitive["intent_id"]},
    )
    assert blocked.status_code == 200, blocked.text
    blocked_payload = blocked.json()
    assert blocked_payload["plan"]["status"] == "blocked"
    assert blocked_payload["plan"]["failure_reason"] == "sensitive_content_blocked"
    assert _payload_leakage_count(blocked_payload) == 0


def test_phase42_release_contracts_eval_report_and_diagnostic(
    client: TestClient,
) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    for name in [
        "ExternalPlatformActionResolver",
        "PlatformTargetRegistry",
        "AccountAssetCandidateResolver",
        "ExternalPlatformActionOrchestrator",
        "ExternalPlatformFakeProvider",
        "ExternalPlatformApprovalBinding",
        "ExternalPlatformTraceEvidence",
    ]:
        assert by_name[name]["status"] == "implemented"

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase42_external_platform_actions"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 10

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    evidence = client.get(f"/api/release-gates/{gate['release_gate_id']}/evidence").json()[
        "items"
    ]

    phase42 = report["summary"]["phase42"]
    assert completed["status"] == "ready_for_release"
    assert phase42["suite_id"] == "suite_phase42_external_platform_actions"
    assert phase42["registered_cases"] == 10
    assert phase42["tables"]["external_platform_targets"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase42"]["registered"] is True
    assert any(item["source_type"] == "phase42_external_platform_actions" for item in evidence)
    assert "phase42" in diagnostic
    assert "phase42_external_platform_actions" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _resolve(client: TestClient, text: str) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/intents/resolve",
        json={"text": text, "member_id": "mem_xiaoyao"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_social_platform_target(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/external-platform/targets",
        json={
            "platform_key": "social_xiaohongshu",
            "display_name": "小红书",
            "aliases": ["小红书", "xhs", "rednote"],
            "supported_actions": ["publish_content", "read_status"],
            "required_asset_types": ["account"],
            "execution_modes": ["browser"],
            "risk_defaults": {"publish_content": "R4", "read_status": "R1"},
            "metadata": {
                "test_social_platform_target": True,
                "real_external_platform_integration": False,
                "provider_required_before_real_publish": True,
            },
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _create_account(
    client: TestClient,
    *,
    display_name: str,
    provider_key: str = "fake_platform",
) -> dict[str, Any]:
    response = client.post(
        "/api/assets",
        json={
            "asset_type": "account",
            "display_name": display_name,
            "provider": provider_key,
            "sensitivity": "high",
            "config": {
                "platform": provider_key,
                "username": display_name,
                "auth_type": "token",
            },
            "secret_value": "token=phase42-secret-token",
            "owner_scope_type": "member",
            "owner_scope_id": "mem_xiaoyao",
            "visibility": "private",
            "risk_level": "R4",
            "summary_text": f"{display_name} external platform account",
            "capabilities": ["login", "publish_content", "publish_post"],
            "metadata": {"platform": "fake_platform", "label": display_name},
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["secret_ref"] is None
    assert "phase42-secret-token" not in json.dumps(payload, ensure_ascii=False)
    return dict(payload)


def _grant(
    client: TestClient,
    asset_id: str,
    action: str,
    risk: RiskLevel,
) -> dict[str, Any]:
    response = client.post(
        "/api/assets/grants",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "object_type": "asset",
            "object_id": asset_id,
            "action": action,
            "effect": "allow",
            "risk_level": risk.value,
            "source_type": "phase42_test",
            "source_id": asset_id,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "phase42-secret-token",
        "phase42-raw-secret",
        "phase42-pass",
        "token=phase42",
        "cookie=phase42",
        "private_key=phase42",
        "mnemonic=phase42",
        "c:\\users\\administrator\\phase42",
    ]
    return sum(1 for item in forbidden if item in serialized)
