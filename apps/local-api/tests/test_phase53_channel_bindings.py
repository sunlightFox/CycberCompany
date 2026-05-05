from __future__ import annotations

import json
from typing import Any, cast

from core_types import RiskLevel
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase53_wechat_mock_binding_asset_channel_and_broker(client: TestClient) -> None:
    started = _start_mock_bind(client)
    bind_session_id = started["bind_session_id"]
    assert started["status"] == "qr_ready"
    assert started["qr"]["data"].startswith("mock-wechat-qr:")

    scanned = client.get(f"/api/channels/bind-sessions/{bind_session_id}")
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["status"] == "scanned"

    confirmed = client.get(f"/api/channels/bind-sessions/{bind_session_id}")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "confirmed"

    finalized = client.post(f"/api/channels/bind-sessions/{bind_session_id}/finalize")
    assert finalized.status_code == 200, finalized.text
    payload = finalized.json()
    assert payload["bind_session"]["status"] == "bound"
    assert payload["asset"]["asset_type"] == "account"
    assert payload["asset"]["metadata"]["asset_subtype"] == "communication_channel"
    assert payload["asset"]["secret_ref"] is None
    assert payload["channel"]["asset_id"] == payload["asset"]["asset_id"]
    assert payload["account"]["channel_id"] == payload["channel"]["channel_id"]
    assert payload["account"]["account_ref_redacted"].startswith("sha256:")
    assert "mock:" not in json.dumps(payload, ensure_ascii=False)

    accounts = client.get("/api/channels/accounts", params={"provider": "wechat_mock"})
    assert accounts.status_code == 200, accounts.text
    assert accounts.json()["items"][0]["channel_account_id"] == payload["account"][
        "channel_account_id"
    ]

    channels = client.get("/api/notification/channels")
    assert channels.status_code == 200, channels.text
    assert payload["channel"]["channel_id"] in {
        item["channel_id"] for item in channels.json()["items"]
    }

    handle_query = client.post(
        "/api/assets/query",
        json={
            "subject_type": "member",
            "subject_id": "mem_xiaoyao",
            "asset_type": "account",
            "requested_actions": ["message_send"],
            "context": {
                "provider": "wechat_mock",
                "asset_subtype": "communication_channel",
                "capability": "approval.reply",
            },
        },
    )
    assert handle_query.status_code == 200, handle_query.text
    handles = handle_query.json()["handles"]
    assert handles
    assert handles[0]["allowed_actions"] == ["message_send"]
    assert "provider_state_ref" not in json.dumps(handles, ensure_ascii=False)


def test_phase53_bind_expire_cancel_duplicate_finalize_and_revoke(
    client: TestClient,
) -> None:
    cancel_session = _start_mock_bind(client)["bind_session_id"]
    cancelled = client.post(f"/api/channels/bind-sessions/{cancel_session}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    finalize_cancelled = client.post(f"/api/channels/bind-sessions/{cancel_session}/finalize")
    assert finalize_cancelled.status_code == 409, finalize_cancelled.text

    expired_session = _start_mock_bind(client)["bind_session_id"]

    async def expire_session() -> None:
        registry = cast(Any, client.app).state.registry
        await registry.channels.update_bind_session(
            expired_session,
            {
                "expires_at": "2020-01-01T00:00:00+00:00",
                "updated_at": "2020-01-01T00:00:00+00:00",
            },
        )

    cast(Any, client).portal.call(expire_session)
    expired = client.get(f"/api/channels/bind-sessions/{expired_session}")
    assert expired.status_code == 200, expired.text
    assert expired.json()["status"] == "expired"
    assert client.post(f"/api/channels/bind-sessions/{expired_session}/finalize").status_code == 409

    bind_session_id = _start_mock_bind(client)["bind_session_id"]
    first = client.post(f"/api/channels/bind-sessions/{bind_session_id}/finalize")
    assert first.status_code == 200, first.text
    duplicate = client.post(f"/api/channels/bind-sessions/{bind_session_id}/finalize")
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["asset"]["asset_id"] == first.json()["asset"]["asset_id"]

    channel_id = first.json()["channel"]["channel_id"]
    revoked = client.post(f"/api/channels/{channel_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "revoked"
    revoked_accounts = client.get("/api/channels/accounts", params={"status": "revoked"})
    assert revoked_accounts.status_code == 200, revoked_accounts.text
    assert channel_id in {item["channel_id"] for item in revoked_accounts.json()["items"]}


def test_phase53_wechat_inbound_approval_and_fail_closed_cases(
    client: TestClient,
) -> None:
    binding = _bind_mock_channel(client)
    approval = _create_approval(
        client,
        requested_action="browser.download",
        payload={"action": "browser.download", "target": "report.csv"},
    )
    _create_approval_notification(client, binding["channel_id"], approval["approval_id"])

    matched = _post_wechat_inbound(
        client,
        binding["channel_account_id"],
        "只允许这一次下载 report.csv",
        provider_event_id="evt-secret-download",
    )
    assert matched.status_code == 200, matched.text
    matched_payload = matched.json()
    assert matched_payload["status"] == "received"
    assert matched_payload["notification_inbound"]["binding_status"] == "matched"
    assert matched_payload["notification_inbound"]["untrusted_external_content"] is True
    assert matched_payload["event"]["provider_event_id_redacted"].startswith("sha256:")
    serialized = json.dumps(matched_payload, ensure_ascii=False)
    assert "evt-secret-download" not in serialized
    assert "wxid-secret-peer" not in serialized
    assert client.get(f"/api/approvals/{approval['approval_id']}").json()["status"] == "approved"

    ambiguous_approval = _create_approval(
        client,
        requested_action="file.delete",
        payload={"action": "file.delete", "target": "report.csv"},
        risk=RiskLevel.R5,
    )
    _create_approval_notification(
        client,
        binding["channel_id"],
        ambiguous_approval["approval_id"],
    )
    ambiguous = _post_wechat_inbound(client, binding["channel_account_id"], "好的")
    assert ambiguous.status_code == 200, ambiguous.text
    assert ambiguous.json()["notification_inbound"]["binding_status"] == (
        "clarification_required"
    )
    assert client.get(f"/api/approvals/{ambiguous_approval['approval_id']}").json()[
        "status"
    ] == "pending"


def test_phase53_wechat_inbound_group_unpaired_multi_and_no_pending_fail_closed(
    client: TestClient,
) -> None:
    binding = _bind_mock_channel(client)

    no_pending = _post_wechat_inbound(client, binding["channel_account_id"], "确认")
    assert no_pending.status_code == 200, no_pending.text
    assert no_pending.json()["notification_inbound"]["binding_status"] == "no_pending_action"

    approval = _create_approval(
        client,
        requested_action="browser.download",
        payload={"action": "browser.download", "target": "report.csv"},
    )
    _create_approval_notification(client, binding["channel_id"], approval["approval_id"])

    group = _post_wechat_inbound(
        client,
        binding["channel_account_id"],
        "只允许这一次下载 report.csv",
        provider_event_id="evt-phase53-group",
        source={"chat_type": "group", "peer_ref": "room-secret-id"},
    )
    assert group.status_code == 200, group.text
    assert group.json()["status"] == "rejected_or_ignored"
    assert group.json()["notification_inbound"] is None
    assert client.get(f"/api/approvals/{approval['approval_id']}").json()["status"] == "pending"

    unpaired = _post_wechat_inbound(
        client,
        binding["channel_account_id"],
        "只允许这一次下载 report.csv",
        provider_event_id="evt-phase53-unpaired",
        source={
            "chat_type": "private",
            "peer_ref": "wxid-unpaired-secret",
            "pairing_status": "unpaired",
        },
    )
    assert unpaired.status_code == 200, unpaired.text
    assert unpaired.json()["status"] == "rejected_or_ignored"
    assert unpaired.json()["notification_inbound"] is None

    second = _create_approval(
        client,
        requested_action="file.delete",
        payload={"action": "file.delete", "target": "report.csv"},
        risk=RiskLevel.R5,
    )
    _create_approval_notification(client, binding["channel_id"], second["approval_id"])
    multiple = _post_wechat_inbound(
        client,
        binding["channel_account_id"],
        "只允许这一次下载 report.csv",
        provider_event_id="evt-phase53-multiple",
        source={"chat_type": "private", "peer_ref": "wxid-secret-peer-2"},
    )
    assert multiple.status_code == 200, multiple.text
    assert multiple.json()["notification_inbound"]["binding_status"] == (
        "clarification_required"
    )
    assert client.get(f"/api/approvals/{approval['approval_id']}").json()["status"] == "pending"
    assert client.get(f"/api/approvals/{second['approval_id']}").json()["status"] == "pending"


def test_phase54_wechat_inbound_reply_requires_object_and_can_cancel(
    client: TestClient,
) -> None:
    binding = _bind_mock_channel(client)
    approval = _create_approval(
        client,
        requested_action="browser.download",
        payload={"action": "browser.download", "target": "report.csv"},
        risk=RiskLevel.R3,
    )
    _create_approval_notification(client, binding["channel_id"], approval["approval_id"])

    vague = _post_wechat_inbound(
        client,
        binding["channel_account_id"],
        "本次允许下载",
        provider_event_id="evt-phase54-vague-allow",
    )
    assert vague.status_code == 200, vague.text
    vague_payload = vague.json()["notification_inbound"]
    assert vague_payload["parsed_intent"] == "approval_once"
    assert vague_payload["binding_status"] == "clarification_required"
    assert (
        vague_payload["action_result"]["reason"]
        == "high_risk_requires_explicit_action_object"
    )
    assert client.get(f"/api/approvals/{approval['approval_id']}").json()["status"] == "pending"

    cancelled = _post_wechat_inbound(
        client,
        binding["channel_account_id"],
        "取消",
        provider_event_id="evt-phase54-cancel",
    )
    assert cancelled.status_code == 200, cancelled.text
    cancelled_payload = cancelled.json()["notification_inbound"]
    assert cancelled_payload["parsed_intent"] == "approval_deny"
    assert cancelled_payload["binding_status"] == "matched"
    assert cancelled_payload["action_result"]["status"] == "denied"
    assert client.get(f"/api/approvals/{approval['approval_id']}").json()["status"] == "denied"


def test_phase54_wechat_direct_inbound_dedupes_provider_event(
    client: TestClient,
) -> None:
    binding = _bind_mock_channel(client)
    first = _post_wechat_inbound(
        client,
        binding["channel_account_id"],
        "确认",
        provider_event_id="evt-phase54-direct-dup",
    )
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["status"] == "received"
    assert first_payload["notification_inbound"]["binding_status"] == "no_pending_action"

    duplicate = _post_wechat_inbound(
        client,
        binding["channel_account_id"],
        "确认",
        provider_event_id="evt-phase54-direct-dup",
    )
    assert duplicate.status_code == 200, duplicate.text
    duplicate_payload = duplicate.json()
    assert duplicate_payload["status"] == "duplicate"
    assert duplicate_payload["notification_inbound"] is None
    provider_event_ref = first_payload["event"]["provider_event_id_redacted"]
    events = client.get(
        "/api/channels/events",
        params={"provider": "wechat_mock", "limit": 20},
    )
    assert events.status_code == 200, events.text
    matching = [
        item
        for item in events.json()["items"]
        if item["provider_event_id_redacted"] == provider_event_ref
    ]
    assert len(matching) == 1


def test_phase53_release_identity_migration_and_contracts(client: TestClient) -> None:
    migration = assert_phase_migration_contract(client, "phase53")
    assert migration["required_migration"] == "036_channel_bindings_wechat.sql"
    assert set(migration["required_tables"]) == {
        "channel_bind_sessions",
        "channel_accounts",
        "channel_peers",
        "channel_events",
    }

    contracts = client.get("/api/system/runtime-contracts")
    assert contracts.status_code == 200, contracts.text
    contract_names = {item["name"] for item in contracts.json()["items"]}
    assert {
        "WechatClawbotConnector",
        "WechatChannelBindingService",
        "WechatChannelNotificationBridge",
        "WechatInboundApprovalResolver",
        "WechatChannelPeerPolicy",
        "WechatChannelRedactionAudit",
    }.issubset(contract_names)
    assert "AutonomousBrowserWorkflowPlanner" not in contract_names

    suites = client.get("/api/evals/suites")
    assert suites.status_code == 200, suites.text
    suite_ids = {item["suite_id"] for item in suites.json()["items"]}
    assert "suite_phase53_channel_bindings_wechat" in suite_ids
    assert "suite_phase53_autonomous_browser_workflows" not in suite_ids

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase53_channel_bindings_wechat"},
    )
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "passed"
    assert run.json()["total_cases"] == 11


def _start_mock_bind(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/channels/bind-sessions",
        json={"provider": "wechat_mock", "display_name_hint": "测试微信"},
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _bind_mock_channel(client: TestClient) -> dict[str, Any]:
    bind_session_id = _start_mock_bind(client)["bind_session_id"]
    finalized = client.post(f"/api/channels/bind-sessions/{bind_session_id}/finalize")
    assert finalized.status_code == 200, finalized.text
    payload = finalized.json()
    return {
        "channel_id": payload["channel"]["channel_id"],
        "channel_account_id": payload["account"]["channel_account_id"],
    }


def _create_approval(
    client: TestClient,
    *,
    requested_action: str,
    payload: dict[str, Any],
    risk: RiskLevel = RiskLevel.R3,
) -> dict[str, Any]:
    task = client.post(
        "/api/tasks",
        json={"goal": f"phase53 approval {requested_action}", "auto_start": False},
    )
    assert task.status_code == 200, task.text

    async def runner() -> dict[str, Any]:
        registry = cast(Any, client.app).state.registry
        approval = await registry.approval_service.create_approval(
            task_id=task.json()["task_id"],
            organization_id="org_default",
            requested_action=requested_action,
            risk_level=risk,
            summary=f"需要确认 {requested_action}",
            payload=payload,
            trace_id=None,
        )
        return dict(approval.model_dump(mode="json"))

    return cast(dict[str, Any], cast(Any, client).portal.call(runner))


def _create_approval_notification(
    client: TestClient,
    channel_id: str,
    approval_id: str,
) -> dict[str, Any]:
    response = client.post(
        "/api/notification/messages",
        json={
            "channel_id": channel_id,
            "message_type": "approval_required",
            "recipient": "wxid-phase53-peer",
            "subject": "审批确认",
            "body": "请确认本次操作。",
            "approval_id": approval_id,
            "send_immediately": False,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def _post_wechat_inbound(
    client: TestClient,
    channel_account_id: str,
    content: str,
    *,
    provider_event_id: str = "evt-phase53",
    source: dict[str, Any] | None = None,
) -> Any:
    return client.post(
        "/api/channels/inbound/wechat",
        json={
            "provider": "wechat_mock",
            "channel_account_id": channel_account_id,
            "provider_event_id": provider_event_id,
            "source": source or {"chat_type": "private", "peer_ref": "wxid-secret-peer"},
            "message": {"content_type": "text", "content_text": content},
            "raw_event": {
                "token": "raw-token-should-not-leak",
                "cookie": "raw-cookie-should-not-leak",
            },
        },
    )
