from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from app.services.channel_reliability import (
    phase110_no_turn_reason_group,
    summarize_records,
)


def test_phase110_no_turn_reason_groups_and_runtime_summary_use_canonical_taxonomy() -> None:
    assert phase110_no_turn_reason_group("turn_not_created") == "routing"
    assert phase110_no_turn_reason_group("turn_created_but_not_queued") == "routing"
    assert phase110_no_turn_reason_group("pairing_rejected_or_missing") == "pairing"
    assert phase110_no_turn_reason_group("ingress_policy_blocked") == "policy"
    assert phase110_no_turn_reason_group("worker_not_running_or_disabled") == "worker"

    summary = summarize_records(
        "wechat",
        [
            {
                "taxonomy": ["no_turn"],
                "failure_reason_codes": ["turn_not_created"],
                "turn_formation": {},
                "delivery_binding": {},
            },
            {
                "taxonomy": ["no_turn"],
                "failure_reason_codes": ["pairing_rejected_or_missing"],
                "turn_formation": {},
                "delivery_binding": {},
            },
        ],
    )

    assert summary["no_turn_reason_group_counts"]["routing"] == 1
    assert summary["no_turn_reason_group_counts"]["pairing"] == 1


def test_phase110_readiness_exposes_routing_contract_and_replay_fields(
    client: TestClient,
) -> None:
    readiness = client.get("/api/system/chat-mainline-readiness")
    assert readiness.status_code == 200, readiness.text
    phase110 = readiness.json()["phase_readiness"]["phase110_channel_routing_stability"]

    assert phase110["status"] == "ready"
    assert (
        phase110["details"]["phase110_contract_version"]
        == "phase110.channel_routing_stability.v1"
    )
    assert (
        phase110["details"]["routing_contract_alignment"]["channel_ingress_runtime"]
        == "phase110.channel_routing_stability.v1"
    )
    assert "dedupe_key" in phase110["details"]["routing_replay_fields"]
    assert "session_peer_ref_redacted" in phase110["details"]["session_route_replay_fields"]
    assert phase110["details"]["runtime_no_turn_reason_group_counts"].get("routing", 0) >= 0
    assert "routing" in phase110["details"]["evidence_no_turn_group_counts"]
    phase114 = readiness.json()["phase_readiness"]["phase114_mainline_observability_closure"]
    replay_alignment = phase114["details"]["replay_alignment"]
    assert replay_alignment["routing_replay_fields_present"] is True
    assert "dedupe_key" in replay_alignment["routing_replay_fields"]


def test_phase110_channel_context_and_release_summary_carry_routing_replay_contract(
    client: TestClient,
) -> None:
    registry = cast(Any, client.app).state.registry
    context = registry.wechat_gateway_service._session_context_runtime.build_inbound(
        provider="wechat",
        session={
            "session_id": "sess_phase110",
            "conversation_id": "conv_phase110",
            "member_id": "mem_xiaoyao",
            "channel_peer_session_id": "chps_phase110",
        },
        channel_message_id="msg_phase110",
        raw_payload={"channel_event_id": "chevt_phase110"},
        ui_mode="wechat_chat",
        semantics={
            "channel_account_id": "chacc_phase110",
            "channel_peer_id_redacted": "sha256:peer-phase110",
            "channel_thread_id": "thread_phase110",
            "delivery_mode": "thread",
            "source_timestamp": "2026-05-17T00:00:00Z",
            "dedupe_key": "sha256:dedupe-phase110",
            "session_peer_ref_redacted": "sha256:session-peer-phase110",
            "conversation_binding_mode": "same_channel_only",
            "cross_channel_reuse_allowed": False,
        },
    )

    assert context["session_peer_ref_redacted"] == "sha256:session-peer-phase110"
    assert context["conversation_binding_mode"] == "same_channel_only"
    assert context["cross_channel_reuse_allowed"] is False

    created = client.post("/api/release-gates", json={})
    assert created.status_code == 200, created.text
    report = client.get(f"/api/release-gates/{created.json()['release_gate_id']}/report").json()
    phase110 = report["summary"]["phase110_channel_routing_stability"]

    assert phase110["status"] == "ready"
    assert phase110["contract_version"] == "phase110.channel_routing_stability.v1"
    assert "dedupe_key" in phase110["routing_replay_fields"]
    assert "session_peer_ref_redacted" in phase110["session_route_replay_fields"]
    assert "provider" in phase110["route_identity_fields"]
