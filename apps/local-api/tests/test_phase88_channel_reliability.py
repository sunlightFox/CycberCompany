from __future__ import annotations

from typing import Any, cast

from app.services.channel_reliability import (
    build_correlation,
    no_turn_payload,
    orphan_turn_payload,
    summarize_records,
    wrong_reuse_payload,
)
from fastapi.testclient import TestClient

from test_phase54_wechat_gateway_full_link import (
    GatewayWechatClient,
    _bind_real_wechat,
    _insert_completed_turn,
    _install_fake_wechat,
    _pair_peer,
    _text_event as _wechat_text_event,
)


def test_phase88_helper_payloads_use_canonical_taxonomy() -> None:
    correlation = build_correlation(
        inbound_event_id="chevt_phase88",
        provider="wechat",
        channel_account_id="chacc_phase88",
        channel_message_id="msg_phase88",
        conversation_id="conv_phase88",
        turn_id="turn_phase88",
    )

    no_turn = no_turn_payload(correlation=correlation, reason_code="turn_not_created")
    orphan = orphan_turn_payload(
        correlation=correlation,
        reason_code="turn_completed_but_delivery_binding_missing",
        turn_id="turn_phase88",
        queue_status="queued",
    )
    wrong_reuse = wrong_reuse_payload(
        correlation=correlation,
        conflicting_session_id="chps_conflict_phase88",
    )

    assert no_turn["taxonomy"] == ["no_turn"]
    assert no_turn["failure_reason_codes"] == ["turn_not_created"]
    assert orphan["taxonomy"] == ["orphan_turn"]
    assert orphan["turn_formation"]["queue_status"] == "queued"
    assert wrong_reuse["taxonomy"] == ["wrong_conversation_reuse"]
    assert wrong_reuse["failure_reason_codes"] == ["session_binding_mismatch"]


def test_phase88_summary_counts_failure_reasons() -> None:
    correlation = build_correlation(provider="wechat")
    summary = summarize_records(
        "wechat",
        [
            no_turn_payload(
                correlation=correlation,
                reason_code="pairing_rejected_or_missing",
            ),
            no_turn_payload(
                correlation=correlation,
                reason_code="ingress_policy_blocked",
            ),
        ],
    )

    assert summary["taxonomy_counts"]["no_turn"] == 2
    assert summary["failure_reason_counts"]["pairing_rejected_or_missing"] == 1
    assert summary["failure_reason_counts"]["ingress_policy_blocked"] == 1


def test_phase88_ingress_context_and_metadata_carry_inbound_event_id(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    context = registry.wechat_gateway_service._session_context_runtime.build_inbound(
        provider="wechat",
        session={
            "session_id": "sess_phase88",
            "conversation_id": "conv_phase88",
            "member_id": "mem_xiaoyao",
        },
        channel_message_id="msg_phase88",
        raw_payload={"channel_event_id": "chevt_phase88"},
        ui_mode="wechat_chat",
        semantics={"inbound_event_id": "chevt_phase88"},
    )

    captured: dict[str, Any] = {}

    async def fake_create_turn(request: Any, *, retry_of_turn_id: str | None = None) -> Any:
        captured["request"] = request
        captured["retry_of_turn_id"] = retry_of_turn_id
        return {"turn_id": "turn_phase88"}

    registry.session_runtime.create_turn = fake_create_turn

    result = _run_async(
        client,
        lambda: registry.channel_ingress_runtime.submit_channel_turn(
            provider="wechat",
            session={
                "session_id": "sess_phase88",
                "conversation_id": "conv_phase88",
                "member_id": "mem_xiaoyao",
            },
            inbound_event_id="chevt_phase88",
            channel_message_id="msg_phase88",
            text="phase88",
            raw_payload={"channel_event_id": "chevt_phase88"},
            ui_mode="wechat_chat",
        ),
    )

    assert result["turn_id"] == "turn_phase88"
    assert context["inbound_event_id"] == "chevt_phase88"
    assert captured["request"].ingress_metadata.inbound_event_id == "chevt_phase88"


def test_phase88_wechat_wrong_conversation_reuse_and_duplicate_are_visible(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase88-peer-a")
    _pair_peer(client, "wxid-phase88-peer-b")
    registry = cast(Any, client.app).state.registry

    async def fake_submit_channel_turn(**kwargs: Any) -> Any:
        request = registry.channel_ingress_runtime._router.route(**kwargs).to_turn_request()
        return await _insert_completed_turn(
            registry,
            request,
            assistant_text="phase88 微信可靠性链路已接通。",
            conversation_id=request.conversation_id or "conv_phase88_reuse",
        )

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = (
        fake_submit_channel_turn
    )

    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase88-a-1", "wxid-phase88-peer-a", "第一条"),
    ]
    first = client.post("/api/channels/providers/wechat/poll-once")
    assert first.status_code == 200, first.text
    assert first.json()["chat_turns_created"] == 1

    sessions = _run_async(
        client,
        lambda: registry.channels.list_peer_sessions(provider="wechat", limit=10),
    )
    peer_sessions = list(sessions)
    first_session = next(
        item for item in peer_sessions if item.get("conversation_id") == "conv_phase88_reuse"
    )
    second_session = next(
        item for item in peer_sessions if item["channel_peer_session_id"] != first_session["channel_peer_session_id"]
    )
    _run_async(
        client,
        lambda: registry.channels.update_peer_session(
            second_session["channel_peer_session_id"],
            {
                "conversation_id": "conv_phase88_reuse",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
        ),
    )

    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase88-b-1", "wxid-phase88-peer-b", "第二条"),
    ]
    wrong_reuse = client.post("/api/channels/providers/wechat/poll-once")
    assert wrong_reuse.status_code == 200, wrong_reuse.text
    assert "wrong_conversation_reuse" in wrong_reuse.json()["taxonomy"]
    assert "session_binding_mismatch" in wrong_reuse.json()["failure_reason_codes"]

    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase88-b-1", "wxid-phase88-peer-b", "第二条"),
    ]
    duplicate = client.post("/api/channels/providers/wechat/poll-once")
    assert duplicate.status_code == 200, duplicate.text
    assert "duplicate_turn" in duplicate.json()["taxonomy"]


def test_phase88_wechat_rejected_and_unpaired_inbound_are_visible_as_no_turn(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)

    GatewayWechatClient.events = [
        _wechat_text_event(
            "evt-phase88-group-blocked",
            "wxid-phase88-group-peer",
            "群里消息",
            chat_type="group",
        ),
        _wechat_text_event(
            "evt-phase88-unpaired",
            "wxid-phase88-unpaired-peer",
            "未配对私聊",
        ),
    ]
    response = client.post("/api/channels/providers/wechat/poll-once")
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["details"]["phase88"]["taxonomy_counts"]["no_turn"] == 2
    assert payload["details"]["phase88"]["failure_reason_counts"]["ingress_policy_blocked"] == 1
    assert (
        payload["details"]["phase88"]["failure_reason_counts"][
            "pairing_rejected_or_missing"
        ]
        == 1
    )


def test_phase88_wechat_submit_failures_classify_worker_and_bootstrap_causes(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase88-worker-disabled")
    registry = cast(Any, client.app).state.registry
    registry.wechat_gateway_service.set_worker_health_provider(
        lambda: {"enabled": False, "running": False, "loop_status": "disabled", "workers": {}}
    )

    async def raise_submit_failure(**_: Any) -> Any:
        raise RuntimeError("submit failed")

    registry.wechat_gateway_service._channel_ingress_runtime.submit_channel_turn = (
        raise_submit_failure
    )
    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase88-worker-disabled", "wxid-phase88-worker-disabled", "worker"),
    ]
    worker_disabled = client.post("/api/channels/providers/wechat/poll-once").json()
    assert (
        worker_disabled["details"]["phase88"]["failure_reason_counts"][
            "worker_not_running_or_disabled"
        ]
        == 1
    )

    _pair_peer(client, "wxid-phase88-bootstrap")
    registry.wechat_gateway_service.set_worker_health_provider(
        lambda: {
            "enabled": True,
            "running": False,
            "loop_status": "stopped",
            "workers": {"wechat_inbound_worker": {"last_status": "healthy"}},
        }
    )
    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase88-bootstrap", "wxid-phase88-bootstrap", "bootstrap"),
    ]
    bootstrap_failed = client.post("/api/channels/providers/wechat/poll-once").json()
    assert (
        bootstrap_failed["details"]["phase88"]["failure_reason_counts"][
            "conversation_bootstrap_failed"
        ]
        == 1
    )


def test_phase88_wechat_runtime_missing_and_async_delivery_failures_reach_snapshot(
    client: TestClient,
) -> None:
    _install_fake_wechat(client, GatewayWechatClient)
    _bind_real_wechat(client)
    _pair_peer(client, "wxid-phase88-runtime-missing")
    registry = cast(Any, client.app).state.registry
    registry.wechat_gateway_service.set_worker_health_provider(
        lambda: {
            "enabled": True,
            "running": False,
            "loop_status": "stopped",
            "workers": {"wechat_inbound_worker": {"last_status": "healthy"}},
        }
    )
    registry.wechat_gateway_service._channel_ingress_runtime = None

    GatewayWechatClient.events = [
        _wechat_text_event("evt-phase88-runtime-missing", "wxid-phase88-runtime-missing", "runtime"),
    ]
    runtime_missing = client.post("/api/channels/providers/wechat/poll-once").json()
    assert (
        runtime_missing["details"]["phase88"]["failure_reason_counts"][
            "turn_created_but_runtime_missing"
        ]
        == 1
    )

    registry.wechat_gateway_service._async_failure_reason_counts[
        "delivery_binding_pending_timeout"
    ] += 1
    registry.wechat_gateway_service._async_failure_reason_counts[
        "delivery_failed_after_turn_completed"
    ] += 2
    snapshot = registry.wechat_gateway_service.reliability_snapshot()
    assert snapshot["failure_reason_counts"]["delivery_binding_pending_timeout"] >= 1
    assert snapshot["failure_reason_counts"]["delivery_failed_after_turn_completed"] >= 2


def test_phase88_readiness_and_release_summary_expose_phase88_block(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness")
    assert readiness.status_code == 200, readiness.text
    phase88 = readiness.json()["phase_readiness"]["phase88_channel_reliability"]

    assert phase88["status"] == "ready"
    assert phase88["details"]["phase88_contract_version"] == "phase88.channel_reliability.v1"

    created = client.post("/api/release-gates", json={})
    assert created.status_code == 200, created.text
    release_gate_id = created.json()["release_gate_id"]

    report = client.get(f"/api/release-gates/{release_gate_id}/report")
    assert report.status_code == 200, report.text
    summary = report.json()["summary"]

    assert summary["chat_mainline_readiness"]["phase88_channel_reliability_status"] == "ready"
    assert "phase88_channel_reliability" in summary
    assert summary["phase88_channel_reliability"]["contract_version"] == "phase88.channel_reliability.v1"
    assert "failure_reason_counts" in summary["phase88_channel_reliability"]
    assert "duplicate_turn" in summary["phase88_channel_reliability"]["taxonomy"]
    assert "wrong_conversation_reuse" in summary["phase88_channel_reliability"]["taxonomy"]


def _run_async(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    portal = client.portal
    assert portal is not None
    if args or kwargs:
        return portal.call(lambda: func(*args, **kwargs))
    return portal.call(func)
