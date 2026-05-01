from __future__ import annotations

import json
from typing import Any, cast

from core_types import RiskLevel
from fastapi.testclient import TestClient
from phase_contracts import assert_phase_migration_contract


def test_phase40_local_channel_send_and_dlp(client: TestClient) -> None:
    channel = _create_channel(client)
    message = client.post(
        "/api/notification/messages",
        json={
            "channel_id": channel["channel_id"],
            "message_type": "task_completed",
            "recipient": "user_local_owner",
            "subject": "任务完成",
            "body": "报告已经生成，可以查看 artifact 摘要。",
        },
    )
    assert message.status_code == 200, message.text
    payload = message.json()
    assert payload["status"] == "sent"
    assert payload["provider_message_id"].startswith("local:")

    blocked = client.post(
        "/api/notification/messages",
        json={
            "channel_id": channel["channel_id"],
            "message_type": "task_failed",
            "recipient": "user_local_owner",
            "subject": "失败",
            "body": "api_key=sk-phase40-secret-value should not leave",
        },
    )
    assert blocked.status_code == 200, blocked.text
    blocked_payload = blocked.json()
    assert blocked_payload["status"] == "blocked"
    assert blocked_payload["dlp_summary"]["redaction_count"] > 0
    assert "sk-phase40-secret-value" not in json.dumps(blocked_payload, ensure_ascii=False)


def test_phase40_provider_failure_is_not_marked_sent(client: TestClient) -> None:
    channel = _create_channel(client, provider="webhook", secret_value="phase40-webhook-secret")
    response = client.post(
        "/api/notification/messages",
        json={
            "channel_id": channel["channel_id"],
            "message_type": "system_degraded",
            "recipient": "user_local_owner",
            "subject": "Provider test",
            "body": "webhook provider should degrade locally",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "failed"
    assert "phase40-webhook-secret" not in json.dumps(payload, ensure_ascii=False)
    attempts = client.get(
        f"/api/notification/messages/{payload['notification_id']}/attempts"
    ).json()["items"]
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["error_code"] == "provider_disabled"


def test_phase40_inbound_approval_binding_and_fail_closed(client: TestClient) -> None:
    approval = _create_approval(client, requested_action="browser.download", risk=RiskLevel.R3)
    messages = client.get("/api/notification/messages").json()["items"]
    approval_message = next(
        item for item in messages if item.get("approval_id") == approval["approval_id"]
    )
    channel_id = approval_message["channel_id"]

    ambiguous = client.post(
        "/api/notification/inbound",
        json={"channel_id": channel_id, "sender_ref": "user_local_owner", "content": "好的"},
    )
    assert ambiguous.status_code == 200, ambiguous.text
    ambiguous_payload = ambiguous.json()
    assert ambiguous_payload["parsed_intent"] == "approval_ambiguous"
    assert ambiguous_payload["binding_status"] == "clarification_required"

    matched = client.post(
        "/api/notification/inbound",
        json={
            "channel_id": channel_id,
            "sender_ref": "user_local_owner",
            "content": "只允许这一次下载 report.csv",
        },
    )
    assert matched.status_code == 200, matched.text
    matched_payload = matched.json()
    assert matched_payload["parsed_intent"] == "approval_once"
    assert matched_payload["binding_status"] == "matched"
    assert matched_payload["matched_approval_id"] == approval["approval_id"]
    assert matched_payload["untrusted_external_content"] is True

    resolved = client.get(f"/api/approvals/{approval['approval_id']}").json()
    assert resolved["status"] == "approved"


def test_phase40_no_pending_and_multiple_pending_do_not_execute(client: TestClient) -> None:
    channel = _create_channel(client)
    no_pending = client.post(
        "/api/notification/inbound",
        json={
            "channel_id": channel["channel_id"],
            "sender_ref": "user_local_owner",
            "content": "确认",
        },
    )
    assert no_pending.status_code == 200, no_pending.text
    assert no_pending.json()["binding_status"] == "no_pending_action"

    first = _create_approval(client, requested_action="browser.download", risk=RiskLevel.R3)
    second = _create_approval(client, requested_action="file.delete", risk=RiskLevel.R5)
    messages = client.get("/api/notification/messages").json()["items"]
    channel_id = next(
        item for item in messages if item.get("approval_id") == first["approval_id"]
    )["channel_id"]
    multiple = client.post(
        "/api/notification/inbound",
        json={
            "channel_id": channel_id,
            "sender_ref": "user_local_owner",
            "content": "只允许这一次下载",
        },
    )
    assert multiple.status_code == 200, multiple.text
    assert multiple.json()["binding_status"] == "clarification_required"
    assert client.get(f"/api/approvals/{first['approval_id']}").json()["status"] == "pending"
    assert client.get(f"/api/approvals/{second['approval_id']}").json()["status"] == "pending"


def test_phase40_scheduled_task_creates_queued_notification(client: TestClient) -> None:
    scheduled = client.post(
        "/api/scheduled-tasks",
        json={
            "title": "phase40 summary",
            "goal": "每天 09:00 帮我整理通知摘要",
            "owner_member_id": "mem_xiaoyao",
            "schedule": {"kind": "once", "run_at": "2026-05-01T00:00:00+00:00"},
            "execution_policy": {"attendance": "attended", "auto_start": False},
        },
    )
    assert scheduled.status_code == 200, scheduled.text
    trigger = client.post(
        f"/api/scheduled-tasks/{scheduled.json()['scheduled_task_id']}/trigger",
        json={"scheduled_for": "2026-05-01T00:00:00+00:00"},
    )
    assert trigger.status_code == 200, trigger.text
    messages = client.get("/api/notification/messages").json()["items"]
    scheduled_messages = [
        item for item in messages if item["message_type"] == "scheduled_summary"
    ]
    assert scheduled_messages
    assert scheduled_messages[0]["status"] == "queued"


def test_phase40_release_contracts_summary_diagnostic_and_migration(
    client: TestClient,
) -> None:
    migration_contract = assert_phase_migration_contract(client, "phase40")
    assert migration_contract["required_migration"] == "028_notification_gateway.sql"
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}
    assert {
        "NotificationGatewayService",
        "ChannelProviderRuntime",
        "MessageChannelAssetHandle",
        "NotificationOutboundDLP",
        "InboundMessageParser",
        "NotificationPendingActionResolver",
        "NotificationRetryQueue",
        "NotificationTraceAudit",
    }.issubset(by_name)

    run = client.post(
        "/api/evals/runs",
        json={"suite_id": "suite_phase40_notification_gateway"},
    )
    assert run.status_code == 200, run.text
    run_payload = run.json()
    assert run_payload["status"] == "passed"
    assert run_payload["total_cases"] == 10

    gate = client.post("/api/release-gates", json={}).json()
    completed = client.post(f"/api/release-gates/{gate['release_gate_id']}/run").json()
    report = client.get(f"/api/release-gates/{gate['release_gate_id']}/report").json()
    registry = cast(Any, client.app).state.registry
    diagnostic_id = completed["summary"]["diagnostic_bundle_id"]
    diagnostic_path = registry.config.storage.data_dir / "diagnostics" / f"{diagnostic_id}.json"
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))

    phase40 = report["summary"]["phase40"]
    assert completed["status"] == "ready_for_release"
    assert phase40["suite_id"] == "suite_phase40_notification_gateway"
    assert phase40["registered_cases"] == 10
    assert phase40["tables"]["notification_channels"] is True
    assert report["summary"]["phase23"]["capability_scores"]["phase40"]["registered"] is True
    assert "phase40" in diagnostic
    assert "phase40_notification_gateway" in diagnostic
    assert _payload_leakage_count({"report": report, "diagnostic": diagnostic}) == 0


def _create_channel(
    client: TestClient,
    *,
    provider: str = "local_mock",
    secret_value: str | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/notification/channels",
        json={
            "provider": provider,
            "display_name": f"phase40 {provider}",
            "channel_type": "local_inbox" if provider == "local_mock" else provider,
            "sensitivity": "medium",
            "secret_value": secret_value,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["asset_id"]
    assert "secret" not in json.dumps(payload.get("provider_config", {}), ensure_ascii=False)
    return dict(payload)


def _create_task(client: TestClient) -> str:
    response = client.post(
        "/api/tasks",
        json={"goal": "phase40 notification approval task", "auto_start": False},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["task_id"])


def _create_approval(
    client: TestClient,
    *,
    requested_action: str,
    risk: RiskLevel,
) -> dict[str, Any]:
    task_id = _create_task(client)

    async def runner() -> Any:
        approval = await cast(Any, client.app).state.registry.approval_service.create_approval(
            task_id=task_id,
            organization_id="org_default",
            requested_action=requested_action,
            risk_level=risk,
            summary=f"需要确认 {requested_action}",
            payload={"action": requested_action, "target": "report.csv"},
            trace_id=None,
        )
        return approval.model_dump(mode="json")

    return dict(cast(Any, client).portal.call(runner))


def _payload_leakage_count(payload: Any) -> int:
    serialized = json.dumps(payload, ensure_ascii=False).lower()
    forbidden = [
        "sk-phase40-secret-value",
        "phase40-webhook-secret",
        "token=phase40",
        "cookie=phase40",
        "private_key=phase40",
        "mnemonic=phase40",
        "c:\\users\\administrator\\phase40",
    ]
    return sum(1 for item in forbidden if item in serialized)
