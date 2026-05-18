from __future__ import annotations

from typing import Any

PHASE88_CHANNEL_RELIABILITY_VERSION = "phase88.channel_reliability.v1"
PHASE110_CHANNEL_ROUTING_STABILITY_VERSION = "phase110.channel_routing_stability.v1"
PHASE88_TAXONOMY = (
    "no_turn",
    "orphan_turn",
    "duplicate_turn",
    "wrong_conversation_reuse",
)
PHASE88_FAILURE_REASON_CODES = (
    "pairing_rejected_or_missing",
    "ingress_policy_blocked",
    "duplicate_inbound_suppressed",
    "session_binding_mismatch",
    "worker_not_running_or_disabled",
    "conversation_bootstrap_failed",
    "channel_ingress_submit_failed",
    "turn_not_created",
    "turn_created_but_not_queued",
    "turn_created_but_runtime_missing",
    "turn_completed_but_delivery_binding_missing",
    "delivery_binding_pending_timeout",
    "delivery_failed_after_turn_completed",
    "active_run_not_found",
    "stale_steering_target",
    "duplicate_control_event",
    "control_session_mismatch",
)
PHASE110_NO_TURN_REASON_GROUPS = {
    "pairing_rejected_or_missing": "pairing",
    "ingress_policy_blocked": "policy",
    "worker_not_running_or_disabled": "worker",
    "conversation_bootstrap_failed": "bootstrap",
    "channel_ingress_submit_failed": "routing",
    "turn_not_created": "routing",
    "turn_created_but_not_queued": "routing",
    "turn_created_but_runtime_missing": "routing",
    "session_binding_mismatch": "routing",
}


def runtime_contract_details() -> dict[str, Any]:
    return {
        "phase88_reliability_contract": PHASE88_CHANNEL_RELIABILITY_VERSION,
        "phase110_routing_contract": PHASE110_CHANNEL_ROUTING_STABILITY_VERSION,
        "taxonomy": list(PHASE88_TAXONOMY),
        "failure_reason_codes": list(PHASE88_FAILURE_REASON_CODES),
        "no_turn_reason_groups": dict(PHASE110_NO_TURN_REASON_GROUPS),
    }


def phase110_no_turn_reason_group(reason_code: str | None) -> str:
    key = str(reason_code or "").strip()
    return PHASE110_NO_TURN_REASON_GROUPS.get(key, "unknown")


def build_correlation(
    *,
    inbound_event_id: str | None = None,
    provider: str,
    channel_account_id: str | None = None,
    channel_message_id: str | None = None,
    dedupe_key: str | None = None,
    channel_peer_id_redacted: str | None = None,
    channel_thread_id: str | None = None,
    channel_peer_session_id: str | None = None,
    conversation_id: str | None = None,
    turn_id: str | None = None,
    channel_delivery_binding_id: str | None = None,
) -> dict[str, Any]:
    return {
        "inbound_event_id": inbound_event_id,
        "provider": provider,
        "channel_account_id": channel_account_id,
        "channel_message_id": channel_message_id,
        "dedupe_key": dedupe_key,
        "channel_peer_id_redacted": channel_peer_id_redacted,
        "channel_thread_id": channel_thread_id,
        "channel_peer_session_id": channel_peer_session_id,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "channel_delivery_binding_id": channel_delivery_binding_id,
    }


def reliability_payload(
    *,
    reliability_status: str,
    correlation: dict[str, Any],
    taxonomy: list[str] | None = None,
    failure_reason_codes: list[str] | None = None,
    turn_formation: dict[str, Any] | None = None,
    delivery_binding: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "reliability_status": reliability_status,
        "correlation": correlation,
        "taxonomy": list(taxonomy or []),
        "failure_reason_codes": list(failure_reason_codes or []),
        "turn_formation": dict(turn_formation or {}),
        "delivery_binding": dict(delivery_binding or {}),
        "notes": list(notes or []),
        "contract_version": PHASE88_CHANNEL_RELIABILITY_VERSION,
    }


def duplicate_turn_payload(*, correlation: dict[str, Any]) -> dict[str, Any]:
    return reliability_payload(
        reliability_status="suppressed",
        correlation=correlation,
        taxonomy=["duplicate_turn"],
        failure_reason_codes=["duplicate_inbound_suppressed"],
        turn_formation={"status": "suppressed_duplicate", "turn_created": False},
        delivery_binding={"status": "not_applicable", "binding_visible": False},
    )


def wrong_reuse_payload(*, correlation: dict[str, Any], conflicting_session_id: str | None) -> dict[str, Any]:
    return reliability_payload(
        reliability_status="failed",
        correlation=correlation,
        taxonomy=["wrong_conversation_reuse"],
        failure_reason_codes=["session_binding_mismatch"],
        turn_formation={
            "status": "rejected_wrong_reuse",
            "turn_created": False,
            "conflicting_session_id": conflicting_session_id,
        },
        delivery_binding={"status": "not_created", "binding_visible": False},
    )


def no_turn_payload(
    *,
    correlation: dict[str, Any],
    reason_code: str,
    turn_formation: dict[str, Any] | None = None,
    delivery_binding: dict[str, Any] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return reliability_payload(
        reliability_status="failed",
        correlation=correlation,
        taxonomy=["no_turn"],
        failure_reason_codes=[reason_code],
        turn_formation={
            "status": "not_created",
            "turn_created": False,
            **dict(turn_formation or {}),
        },
        delivery_binding={
            "status": "not_created",
            "binding_visible": False,
            **dict(delivery_binding or {}),
        },
        notes=notes,
    )


def orphan_turn_payload(
    *,
    correlation: dict[str, Any],
    reason_code: str,
    turn_id: str | None,
    queue_status: str | None = None,
) -> dict[str, Any]:
    return reliability_payload(
        reliability_status="failed",
        correlation={**correlation, "turn_id": turn_id or correlation.get("turn_id")},
        taxonomy=["orphan_turn"],
        failure_reason_codes=[reason_code],
        turn_formation={
            "status": "turn_created",
            "turn_created": bool(turn_id),
            "queue_status": queue_status,
        },
        delivery_binding={"status": "missing", "binding_visible": False},
    )


def success_payload(
    *,
    correlation: dict[str, Any],
    queue_status: str | None,
    delivery_binding_id: str | None,
    delivery_status: str | None,
) -> dict[str, Any]:
    return reliability_payload(
        reliability_status="ok",
        correlation=correlation,
        taxonomy=[],
        failure_reason_codes=[],
        turn_formation={
            "status": "turn_created",
            "turn_created": bool(correlation.get("turn_id")),
            "queue_status": queue_status,
        },
        delivery_binding={
            "status": delivery_status or ("pending" if delivery_binding_id else "missing"),
            "binding_visible": bool(delivery_binding_id),
            "channel_delivery_binding_id": delivery_binding_id,
        },
    )


def summarize_records(provider: str, records: list[dict[str, Any]] | None) -> dict[str, Any]:
    items = [dict(item) for item in (records or [])]
    counts = {name: 0 for name in PHASE88_TAXONOMY}
    failure_reason_counts = {name: 0 for name in PHASE88_FAILURE_REASON_CODES}
    no_turn_reason_group_counts: dict[str, int] = {}
    failure_reason_codes: list[str] = []
    last_payload = reliability_payload(
        reliability_status="ok",
        correlation=build_correlation(provider=provider),
        turn_formation={"status": "idle"},
        delivery_binding={"status": "idle", "binding_visible": False},
    )
    for item in items:
        for taxonomy in item.get("taxonomy") or []:
            if taxonomy in counts:
                counts[taxonomy] += 1
        for reason_code in item.get("failure_reason_codes") or []:
            if reason_code in failure_reason_counts:
                failure_reason_counts[reason_code] += 1
            if reason_code not in failure_reason_codes:
                failure_reason_codes.append(reason_code)
            if "no_turn" in list(item.get("taxonomy") or []):
                group = phase110_no_turn_reason_group(str(reason_code or ""))
                no_turn_reason_group_counts[group] = (
                    int(no_turn_reason_group_counts.get(group) or 0) + 1
                )
        last_payload = item
    total = max(1, len(items)) if items else 0
    delivery_complete = sum(
        1
        for item in items
        if dict(item.get("delivery_binding") or {}).get("binding_visible") is True
    )
    return {
        **last_payload,
        "failure_reason_codes": failure_reason_codes,
        "failure_reason_counts": failure_reason_counts,
        "no_turn_reason_group_counts": no_turn_reason_group_counts,
        "taxonomy_counts": counts,
        "delivery_binding_completeness": 1.0 if total == 0 else delivery_complete / total,
        "reliability_records": items,
    }
