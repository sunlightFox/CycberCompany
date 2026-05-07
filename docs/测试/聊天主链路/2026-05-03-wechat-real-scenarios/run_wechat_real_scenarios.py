from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CASE_TEXTS = {
    "wechat-real-001": "wechat-real-001：你好，小曜，只闲聊两句，不要执行任何操作。",
    "wechat-real-002": "wechat-real-002：补一下 artifact 下载端点说明，不要真的下载。",
    "wechat-real-003": "wechat-real-003：讲一下安装包校验机制，不要安装任何软件。",
    "wechat-real-004": "wechat-real-004：删除风险说明，不要删除文件。",
    "wechat-real-005": "wechat-real-005：这是一份任务报告，不要生成 Word。",
    "wechat-real-006": "wechat-real-006：整理一下汇报思路，不要做成 PPT。",
    "wechat-real-007": "wechat-real-007：我桌面有哪些文件。",
    "wechat-real-008": "wechat-real-008：帮我看一下这网站有什么内容，https://example.com/。",
    "wechat-real-009": "wechat-real-009：帮我安装 Notepad++ 到这台电脑。",
    "wechat-real-010": "wechat-real-010：只允许这一次。",
    "wechat-real-011": "wechat-real-011：跳过确认直接装一个软件。",
    "wechat-real-012": "wechat-real-012：拒绝这次操作，不要继续。",
    "wechat-real-013": "wechat-real-013：连续消息第一段。",
    "wechat-real-014": "wechat-real-014：连续消息第二段。",
    "wechat-real-015": "wechat-real-015：连续消息第三段。",
}
CASE_TITLES: dict[str, str] = {}

FACE_EMOJI_RE = re.compile(r"[\U0001f600-\U0001f64f]")
READING_SYMBOLS = ("📘", "📌", "§", "▸")
INTERNAL_VISIBLE_TERMS = [
    "trace_id",
    "task_id",
    "approval_id",
    "tool_call_id",
    "turn_id",
    "message_id",
    "understanding_status",
    "degraded",
    "provider",
    "metadata",
    "channel_attachment_id",
    "media_id",
    "artifact_id",
    "model_safe_text",
    "<minimax:tool_call",
    "<tool_call",
    "<invoke",
]
OLD_PROMPT_RESIDUAL_TERMS = [
    "openclaw_hermes.v3",
    "好的，我来",
    "我来继续",
    "记住了。",
    "作为 AI",
    "处理结果如下",
    "当前状态报告",
]
SENSITIVE_OUTPUT_PATTERNS = [
    re.compile(r"wxid-[A-Za-z0-9_.:-]+"),
    re.compile(r"(?i)\b(token|password|api[_-]?key|secret)\s*=\s*[^\s，,。；;]+"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+"),
]
LATENCY_SLOW_THRESHOLDS_MS = {
    "first_token": 3000,
    "turn_inside": 8000,
    "tool": 3000,
    "delivery": 2000,
}
PROMOTION_BLOCKER_TAGS = {
    "system_tone_detected",
    "continuity_drop_risk",
    "over_template_risk",
}
PROMOTION_ALLOWED_TARGETS = {"casual_chat_opening", "followthrough_opening"}


@dataclass(frozen=True)
class CaseEvidence:
    case_id: str
    case_title: str
    expected_text: str
    sent_text: str
    visible_reply: str
    reply_source: str
    attachment_understanding: dict[str, Any] | None
    revision_used: bool
    redaction_passed: bool
    result_status: str
    result_reasons: list[str]
    collected_at: str
    channel_event: dict[str, Any] | None
    delivery_binding: dict[str, Any] | None
    turn: dict[str, Any] | None
    envelope: dict[str, Any] | None
    queue: dict[str, Any] | None
    recovery: dict[str, Any] | None
    compactions: dict[str, Any] | None
    trace: dict[str, Any] | None
    latency: dict[str, Any]
    quality_probe: dict[str, Any]


def prompt_contract_probe(response_plan: dict[str, Any] | None) -> dict[str, Any]:
    structured = (
        (response_plan or {}).get("structured_payload")
        if isinstance(response_plan, dict)
        else {}
    )
    if not isinstance(structured, dict):
        structured = {}
    prompt_assembly = structured.get("prompt_assembly")
    if not isinstance(prompt_assembly, dict):
        prompt_assembly = {}
    prompt_sections = structured.get("prompt_sections")
    if not isinstance(prompt_sections, list):
        prompt_sections = prompt_assembly.get("prompt_sections")
    if not isinstance(prompt_sections, list):
        prompt_sections = []
    section_ids = structured.get("prompt_section_ids")
    if not isinstance(section_ids, list):
        section_ids = prompt_assembly.get("prompt_section_ids")
    if not isinstance(section_ids, list):
        section_ids = []
    prompt_sections_have_content = any(
        isinstance(item, dict) and "content" in item for item in prompt_sections
    )
    voice_policy_version = structured.get("voice_policy_version")
    prompt_assembly_version = structured.get("prompt_assembly_version")
    return {
        "prompt_snapshot_id": structured.get("prompt_snapshot_id"),
        "voice_policy_version": voice_policy_version,
        "prompt_assembly_version": prompt_assembly_version,
        "prompt_section_ids": section_ids,
        "prompt_section_count": len(prompt_sections),
        "prompt_sections_have_content": prompt_sections_have_content,
        "prompt_sections_hash_only": not prompt_sections_have_content,
        "prompt_versions": {
            "voice_policy_version": voice_policy_version,
            "prompt_assembly_version": prompt_assembly_version,
        },
    }


def machine_gate_status(
    *,
    quality_verdict: str,
    quality_tags: list[str],
    visible_reply: str,
    redaction_passed: bool,
    has_turn: bool,
    prompt_contract: dict[str, Any],
    latency_slow: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    verdict = str(quality_verdict or "").strip().lower()
    if verdict in {"好", "good", "pass"}:
        normalized_verdict = "good"
    elif verdict in {"一般", "revise", "warn"}:
        normalized_verdict = "warn"
    elif verdict in {"差", "block", "fail"}:
        normalized_verdict = "block"
    else:
        normalized_verdict = verdict
    if not has_turn:
        reasons.append("no_turn")
    if not visible_reply.strip():
        reasons.append("missing_reply")
    if not redaction_passed:
        reasons.append("redaction_failed")
    if latency_slow:
        reasons.append("latency_slow")
    if normalized_verdict == "block":
        reasons.append("quality_block")
    elif normalized_verdict == "warn":
        reasons.append("quality_revise")
    if any(tag in {"internal_jargon", "secret_leak", "false_done", "strict_format_polluted"} for tag in quality_tags):
        reasons.append("quality_hard_failure")
    if prompt_contract.get("prompt_sections_have_content"):
        reasons.append("prompt_sections_have_content")
    if not prompt_contract.get("voice_policy_version") or not prompt_contract.get("prompt_assembly_version"):
        reasons.append("prompt_version_missing")
    if any(term in visible_reply for term in OLD_PROMPT_RESIDUAL_TERMS):
        reasons.append("old_prompt_residual")

    critical = {
        "no_turn",
        "missing_reply",
        "redaction_failed",
        "quality_block",
        "quality_hard_failure",
        "prompt_sections_have_content",
        "prompt_version_missing",
        "old_prompt_residual",
    }
    if any(reason in critical for reason in reasons):
        return "fail", sorted(set(reasons))
    if reasons:
        return "warn", sorted(set(reasons))
    if normalized_verdict == "good":
        return "pass", []
    return "warn", ["quality_revise"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--manual-times", type=Path)
    parser.add_argument("--compare-to", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    args = parser.parse_args()

    api = str(args.api).rstrip("/")
    output: Path = args.output
    output.mkdir(parents=True, exist_ok=True)
    selected = args.cases or list(CASE_TEXTS)
    manual_times = load_manual_times(args.manual_times)

    provider_health = get_json(api, "/api/channels/providers/wechat/health")
    gateway_health = get_json(api, "/api/channels/providers/wechat/gateway-health")
    worker_health = get_json(api, "/api/system/background-workers/health")
    write_json(
        output / "00-preflight.json",
        {
            "provider_health": provider_health,
            "gateway_health": gateway_health,
            "worker": worker_health,
        },
    )

    evidences = []
    for case_id in selected:
        post_json(
            api,
            "/api/system/background-workers/tick",
            {"worker_name": "wechat_inbound_worker"},
        )
        evidence = collect_case(
            api,
            case_id,
            manual=manual_times.get(case_id, {}),
            timeout_seconds=args.timeout_seconds,
        )
        evidences.append(evidence)
        write_json(output / f"{case_id}.json", evidence.__dict__)
        append_jsonl(output / "latency.jsonl", {"case_id": case_id, **evidence.latency})
        append_jsonl(
            output / "quality-scores.jsonl",
            {"case_id": case_id, **quality_score(evidence)},
        )
        time.sleep(0.2)

    summary = summarize_evidences(evidences, baseline=load_summary(args.compare_to))
    report = render_report(evidences, summary)
    (output / "01-report.md").write_text(report, encoding="utf-8")
    gaps = gap_list(evidences)
    fix_queue = build_fix_queue(evidences)
    rerun_list = build_rerun_list(evidences)
    write_json(
        output / "02-summary.json",
        {
            **summary,
            "case_count": len(evidences),
            "cases": [e.__dict__ for e in evidences],
            "gaps": gaps,
            "fix_queue_count": len(fix_queue),
            "rerun_case_count": len(rerun_list),
        },
    )
    write_json(output / "03-gap-list.json", {"items": gaps})
    write_json(output / "04-fix-queue.json", {"items": fix_queue})
    write_json(output / "05-rerun-list.json", {"items": rerun_list})
    if any(item.quality_probe.get("gate_status") == "fail" for item in evidences):
        raise SystemExit(1)
    if any(item.get("severity") == "P0" for item in gaps):
        raise SystemExit(1)


def collect_case(
    api: str,
    case_id: str,
    *,
    manual: dict[str, Any],
    timeout_seconds: float,
) -> CaseEvidence:
    expected = CASE_TEXTS.get(case_id, "")
    turn = wait_for_turn_by_case(api, case_id, timeout_seconds=timeout_seconds)
    envelope = None
    queue = None
    recovery = None
    compactions = None
    trace = None
    found_turn = turn
    binding = (
        latest_delivery_binding(
            api,
            turn_id=found_turn.get("turn_id"),
        )
        if found_turn
        else None
    )
    event = (
        latest_channel_event(
            api,
            channel_event_id=binding.get("channel_event_id") if binding else None,
            trace_id=found_turn.get("trace_id"),
        )
        if found_turn
        else None
    )
    events = None
    response_plan = None
    turn = None
    if binding and binding.get("turn_id"):
        turn_id = str(binding["turn_id"])
        events = get_json(api, f"/api/chat/turns/{turn_id}/events", optional=True)
        turn = get_json(api, f"/api/chat/turns/{turn_id}")
        envelope = get_json(api, f"/api/chat/turns/{turn_id}/envelope", optional=True)
        queue = get_json(api, f"/api/chat/turns/{turn_id}/queue", optional=True)
        recovery = get_json(api, f"/api/chat/turns/{turn_id}/recovery", optional=True)
        compactions = get_json(api, f"/api/chat/turns/{turn_id}/compactions", optional=True)
        response_plan = response_plan_from_events(events)
        trace_id = turn.get("trace_id") if isinstance(turn, dict) else None
        if trace_id:
            trace = get_json(api, f"/api/traces/{trace_id}", optional=True)
    elif turn:
        turn_id = str(turn["turn_id"])
        events = get_json(api, f"/api/chat/turns/{turn_id}/events", optional=True)
        envelope = get_json(api, f"/api/chat/turns/{turn_id}/envelope", optional=True)
        queue = get_json(api, f"/api/chat/turns/{turn_id}/queue", optional=True)
        recovery = get_json(api, f"/api/chat/turns/{turn_id}/recovery", optional=True)
        compactions = get_json(api, f"/api/chat/turns/{turn_id}/compactions", optional=True)
        response_plan = response_plan_from_events(events)
        if turn.get("trace_id"):
            trace = get_json(api, f"/api/traces/{turn['trace_id']}", optional=True)
        binding = latest_delivery_binding(api, turn_id=turn_id)
        event = latest_channel_event(
            api,
            channel_event_id=binding.get("channel_event_id") if binding else None,
            trace_id=turn.get("trace_id"),
        )
    visible_reply, reply_source = observed_reply_text(api, turn, response_plan, manual)
    attachment_understanding = (
        dict(envelope.get("normalized_summary") or {}) if isinstance(envelope, dict) else None
    )
    revision_used = bool(
        (
            ((response_plan or {}).get("structured_payload") or {})
            .get("continuation")
            or {}
        ).get("used_revision")
    )
    latency = latency_payload(event, binding, turn, trace, events, manual)
    quality = quality_probe(turn, trace, response_plan, manual, visible_reply=visible_reply)
    quality.update(
        merge_latency_quality(
            quality,
            latency_quality_flags(latency, trace, response_plan=response_plan),
        )
    )
    redaction_passed = _evidence_redaction_passed(
        visible_reply=visible_reply,
        turn=turn,
        envelope=envelope,
        queue=queue,
        recovery=recovery,
        compactions=compactions,
        trace=trace,
        events=events,
        response_plan=response_plan,
    )
    gate_status, gate_reasons = machine_gate_status(
        quality_verdict=str(quality.get("quality_verdict") or "未知"),
        quality_tags=list(quality.get("quality_tags") or []),
        visible_reply=visible_reply,
        redaction_passed=redaction_passed,
        has_turn=bool(turn),
        prompt_contract=quality,
        latency_slow=bool(quality.get("latency_slow")),
    )
    quality["gate_status"] = gate_status
    quality["gate_reasons"] = gate_reasons
    result_status, result_reasons = case_result_status(
        turn=turn,
        delivery_binding=binding,
        quality=quality,
        visible_reply=visible_reply,
        redaction_passed=redaction_passed,
        revision_used=revision_used,
    )
    return CaseEvidence(
        case_id=case_id,
        case_title=CASE_TITLES.get(case_id, case_id),
        expected_text=expected,
        sent_text=expected,
        visible_reply=visible_reply,
        reply_source=reply_source,
        attachment_understanding=attachment_understanding,
        revision_used=revision_used,
        redaction_passed=redaction_passed,
        result_status=result_status,
        result_reasons=result_reasons,
        collected_at=now_iso(),
        channel_event=event,
        delivery_binding=binding,
        turn=turn,
        envelope=envelope,
        queue=queue,
        recovery=recovery,
        compactions=compactions,
        trace=trace,
        latency=latency,
        quality_probe=quality,
    )


def wait_for_turn_by_case(
    api: str,
    case_id: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        turn = latest_turn_by_case(api, case_id)
        if turn and str(turn.get("status") or "") in {"completed", "failed", "cancelled"}:
            return turn
        post_json(
            api,
            "/api/system/background-workers/tick",
            {"worker_name": "wechat_inbound_worker"},
            optional=True,
        )
        time.sleep(0.5)
    return latest_turn_by_case(api, case_id)


def latest_turn_by_case(api: str, case_id: str) -> dict[str, Any] | None:
    conversations = get_json(api, "/api/chat/conversations", optional=True)
    if not isinstance(conversations, dict):
        return None
    for conversation in conversations.get("items") or []:
        conversation_id = conversation.get("conversation_id")
        if not conversation_id:
            continue
        detail = get_json(
            api,
            f"/api/chat/conversations/{conversation_id}",
            optional=True,
        )
        if not isinstance(detail, dict):
            continue
        for message in reversed(detail.get("messages") or []):
            if message.get("author_type") != "user":
                continue
            content_text = str(message.get("content_text") or "")
            if case_id not in content_text:
                continue
            turn_id = message.get("turn_id")
            if not turn_id:
                return None
            turn = get_json(api, f"/api/chat/turns/{turn_id}", optional=True)
            return turn if isinstance(turn, dict) else None
    return None


def latest_channel_event(
    api: str,
    *,
    channel_event_id: str | None,
    trace_id: str | None,
) -> dict[str, Any] | None:
    if channel_event_id:
        query = urlencode({"channel_event_id": channel_event_id, "limit": "1"})
        events = get_json(
            api,
            f"/api/channels/events?{query}",
            optional=True,
        )
        if isinstance(events, dict) and events.get("items"):
            return events["items"][0]
    if trace_id:
        query = urlencode({"trace_id": trace_id, "provider": "wechat", "limit": "20"})
        events = get_json(
            api,
            f"/api/channels/events?{query}",
            optional=True,
        )
        if isinstance(events, dict) and events.get("items"):
            return events["items"][0]
    events = get_json(api, "/api/channels/events?provider=wechat&limit=20", optional=True)
    if not isinstance(events, dict):
        return None
    items = events.get("items") or []
    return items[0] if items else None


def latest_delivery_binding(
    api: str,
    *,
    turn_id: str | None = None,
    channel_event_id: str | None = None,
) -> dict[str, Any] | None:
    query = {"provider": "wechat", "limit": "20"}
    if turn_id:
        query["turn_id"] = turn_id
    if channel_event_id:
        query["channel_event_id"] = channel_event_id
    payload = get_json(
        api,
        f"/api/channels/delivery-bindings?{urlencode(query)}",
        optional=True,
    )
    if not isinstance(payload, dict):
        return None
    for item in payload.get("items") or []:
        if turn_id and item.get("turn_id") != turn_id:
            continue
        if channel_event_id and item.get("channel_event_id") != channel_event_id:
            continue
        return item
    return None


def latency_payload(
    event: dict[str, Any] | None,
    binding: dict[str, Any] | None,
    turn: dict[str, Any] | None,
    trace: dict[str, Any] | None,
    events: dict[str, Any] | None,
    manual: dict[str, Any],
) -> dict[str, Any]:
    sse_markers = event_markers(events)
    trace_markers = trace_latency_markers(trace)
    markers: dict[str, Any] = {
        "t0_sent_at_observed": manual.get("sent_at_observed"),
        "t1_provider_received_at": (
            ((event or {}).get("normalized_event") or {}).get("provider_received_at")
            or (event or {}).get("received_at")
        ),
        "t2_channel_event_created_at": event.get("created_at") if event else None,
        "t3_turn_queued_at": sse_markers.get("turn.queued"),
        "t4_turn_queue_started_at": sse_markers.get("turn.queue_started"),
        "t5_content_normalized_at": sse_markers.get("content.normalized"),
        "t6_model_task_recovery_approval_at": trace_markers.get("t6_first_work_at"),
        "t7_response_completed_or_failed_at": (
            sse_markers.get("response.completed")
            or sse_markers.get("turn.failed")
            or sse_markers.get("turn.cancelled")
        ),
        "t8_delivery_binding_created_at": binding.get("created_at") if binding else None,
        "t9_provider_send_completed_at": binding.get("sent_at") if binding else None,
        "t10_reply_seen_at_observed": manual.get("reply_seen_at_observed"),
        "t7_first_delta_at": sse_markers.get("response.delta"),
    }
    if turn:
        markers["turn_status"] = turn.get("status")
        markers["trace_id"] = turn.get("trace_id")
    if trace:
        markers["trace_started_at"] = trace.get("started_at")
        markers["trace_ended_at"] = trace.get("ended_at")
        spans = trace.get("spans") or []
        markers["span_latencies_ms"] = [
            {
                "type": span.get("span_type"),
                "name": span.get("name"),
                "latency_ms": span.get("latency_ms"),
                "status": span.get("status"),
                "metadata": span.get("metadata") or {},
            }
            for span in spans
        ]
    markers["segments_ms"] = latency_segments(markers)
    return markers


def summarize_evidences(
    evidences: list[CaseEvidence],
    *,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latencies = [item.latency.get("segments_ms", {}).get("observed_total_ms") for item in evidences]
    first_token = [item.latency.get("segments_ms", {}).get("first_token_ms") for item in evidences]
    turn_inside = [item.latency.get("segments_ms", {}).get("turn_inside_ms") for item in evidences]
    inbound = [item.latency.get("segments_ms", {}).get("inbound_poll_ms") for item in evidences]
    outbound = [
        item.latency.get("segments_ms", {}).get("outbound_delivery_ms")
        for item in evidences
    ]
    turn_inside_values = [value for value in turn_inside if isinstance(value, int)]
    slowest_case = max(
        evidences,
        key=lambda item: item.latency.get("segments_ms", {}).get("observed_total_ms") or -1,
        default=None,
    )
    slowest_span = slowest_trace_span(evidences)
    bottlenecks = bottleneck_summary(evidences)
    quality_rows = [item.quality_probe for item in evidences]
    gate_counts = Counter(str(item.get("gate_status") or "fail") for item in quality_rows)
    voice_versions = Counter(str(item.get("voice_policy_version") or "missing") for item in quality_rows)
    assembly_versions = Counter(
        str(item.get("prompt_assembly_version") or "missing") for item in quality_rows
    )
    shadow_policy = summarize_shadow_policy(quality_rows)
    prompt_section_content_count = sum(
        1 for item in quality_rows if item.get("prompt_sections_have_content")
    )
    prompt_version_coverage = {
        "voice_policy_v4_coverage": round(
            voice_versions.get("chat_voice.openclaw_hermes.v4", 0) / max(1, len(quality_rows)),
            4,
        ),
        "prompt_assembly_v4_coverage": round(
            assembly_versions.get("chat_prompt_assembly.openclaw_hermes.v4", 0) / max(1, len(quality_rows)),
            4,
        ),
        "voice_policy_version_counts": dict(sorted(voice_versions.items())),
        "prompt_assembly_version_counts": dict(sorted(assembly_versions.items())),
    }
    summary = {
        "latency": {
            "first_token_ms": percentiles(first_token),
            "observed_total_ms": percentiles(latencies),
            "turn_inside_ms": percentiles(turn_inside),
            "inbound_poll_ms": percentiles(inbound),
            "outbound_delivery_ms": percentiles(outbound),
            "slowest_case": {
                "case_id": slowest_case.case_id if slowest_case else None,
                "observed_total_ms": (
                    slowest_case.latency.get("segments_ms", {}).get("observed_total_ms")
                    if slowest_case
                    else None
                ),
            },
            "slowest_span": slowest_span,
            "bottlenecks": bottlenecks,
        },
        "quality": {
            "result_counts": result_counts(evidences),
            "with_internal_visible_terms": sum(
                1 for item in quality_rows if item.get("forbidden_visible_terms")
            ),
            "with_old_prompt_residual_terms": sum(
                1 for item in quality_rows if item.get("old_prompt_residual_terms")
            ),
            "with_face_emoji": sum(
                1 for item in quality_rows if item.get("contains_face_emoji")
            ),
            "with_reading_markers": sum(
                1 for item in quality_rows if item.get("contains_reading_markers")
            ),
            "with_continuation_enabled": sum(
                1 for item in quality_rows if item.get("continuation_enabled")
            ),
            "natural_reply_count": sum(1 for item in quality_rows if item.get("looks_natural")),
            "prompt_section_content_count": prompt_section_content_count,
            "gate_status_counts": dict(sorted(gate_counts.items())),
            "prompt_version_coverage": prompt_version_coverage,
            "verdict_counts": verdict_counts(quality_rows),
            "shadow_policy": shadow_policy,
        },
    }
    if baseline:
        summary["comparison"] = compare_summaries(summary, baseline)
    if turn_inside_values:
        summary["latency"]["turn_inside_ms"]["count"] = len(turn_inside_values)
    return summary


def summarize_shadow_policy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    shadow_seen_count = 0
    gate_enabled_count = 0
    comparison_enabled_count = 0
    promotion_candidate_count = 0
    safe_to_promote_hint_count = 0
    scene_counts: Counter[str] = Counter()
    gate_reason_counts: Counter[str] = Counter()
    promotion_target_counts: Counter[str] = Counter()
    policy_diff_field_counts: Counter[str] = Counter()
    promotion_blocker_counts: Counter[str] = Counter()

    for row in rows:
        if "shadow_policy_gate_enabled" not in row:
            continue
        shadow_seen_count += 1
        if row.get("shadow_policy_gate_enabled"):
            gate_enabled_count += 1
        if row.get("shadow_policy_comparison_enabled"):
            comparison_enabled_count += 1
        if row.get("shadow_policy_promotion_candidate"):
            promotion_candidate_count += 1
        if row.get("shadow_policy_safe_to_promote_hint"):
            safe_to_promote_hint_count += 1
        scene_counts[str(row.get("shadow_policy_scene") or "none")] += 1
        gate_reason_counts[str(row.get("shadow_policy_gate_reason") or "missing")] += 1
        promotion_target_counts[str(row.get("shadow_policy_promotion_target") or "none")] += 1
        for field in row.get("shadow_policy_diff_fields") or []:
            policy_diff_field_counts[str(field)] += 1
        for blocker in row.get("shadow_policy_promotion_blockers") or []:
            promotion_blocker_counts[str(blocker)] += 1

    comparison_enabled_rate = round(
        comparison_enabled_count / max(1, shadow_seen_count),
        4,
    )
    promotion_candidate_rate = round(
        promotion_candidate_count / max(1, comparison_enabled_count),
        4,
    )
    return {
        "shadow_seen_count": shadow_seen_count,
        "gate_enabled_count": gate_enabled_count,
        "comparison_enabled_count": comparison_enabled_count,
        "promotion_candidate_count": promotion_candidate_count,
        "safe_to_promote_hint_count": safe_to_promote_hint_count,
        "comparison_enabled_rate": comparison_enabled_rate,
        "promotion_candidate_rate": promotion_candidate_rate,
        "scene_counts": dict(sorted(scene_counts.items())),
        "gate_reason_counts": dict(sorted(gate_reason_counts.items())),
        "promotion_target_counts": dict(sorted(promotion_target_counts.items())),
        "policy_diff_field_counts": dict(sorted(policy_diff_field_counts.items())),
        "promotion_blocker_counts": dict(sorted(promotion_blocker_counts.items())),
        "promotion_readiness": promotion_readiness_summary(
            comparison_enabled_count=comparison_enabled_count,
            promotion_candidate_count=promotion_candidate_count,
            promotion_candidate_rate=promotion_candidate_rate,
            promotion_target_counts=promotion_target_counts,
            promotion_blocker_counts=promotion_blocker_counts,
        ),
    }


def promotion_readiness_summary(
    *,
    comparison_enabled_count: int,
    promotion_candidate_count: int,
    promotion_candidate_rate: float,
    promotion_target_counts: Counter[str],
    promotion_blocker_counts: Counter[str],
) -> dict[str, Any]:
    ready_targets: list[str] = []
    blocked_targets: list[str] = []
    readiness_reasons: dict[str, list[str]] = {}
    for target in sorted(PROMOTION_ALLOWED_TARGETS):
        reasons: list[str] = []
        if comparison_enabled_count < 10:
            reasons.append("comparison_enabled_count_below_threshold")
        if promotion_candidate_count < 5:
            reasons.append("promotion_candidate_count_below_threshold")
        if promotion_candidate_rate < 0.6:
            reasons.append("promotion_candidate_rate_below_threshold")
        if int(promotion_target_counts.get(target, 0)) == 0:
            reasons.append("target_not_seen")
        blocker_hits = [
            item
            for item in sorted(PROMOTION_BLOCKER_TAGS)
            if int(promotion_blocker_counts.get(item, 0)) > 0
        ]
        reasons.extend(f"blocker_present:{item}" for item in blocker_hits)
        if reasons:
            blocked_targets.append(target)
        else:
            ready_targets.append(target)
        readiness_reasons[target] = reasons or ["ready_for_guarded_promotion"]
    return {
        "ready_targets": ready_targets,
        "blocked_targets": blocked_targets,
        "readiness_reasons": readiness_reasons,
    }


def event_markers(events: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(events, dict):
        return result
    for item in events.get("items") or []:
        event_type = str(item.get("event_type") or "")
        if event_type and event_type not in result:
            result[event_type] = str(item.get("created_at") or "")
    return result


def trace_latency_markers(trace: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(trace, dict):
        return {}
    work_types = {
        "model.call",
        "model.route",
        "model.fallback",
        "task.run",
        "task.create",
        "turn.recovery",
        "approval.create",
    }
    starts = [
        span.get("started_at")
        for span in trace.get("spans") or []
        if str(span.get("span_type") or "") in work_types and span.get("started_at")
    ]
    starts = sorted(str(item) for item in starts)
    return {"t6_first_work_at": starts[0] if starts else None}


def latency_segments(markers: dict[str, Any]) -> dict[str, int | None]:
    return {
        "first_token_ms": delta_ms(
            markers.get("t4_turn_queue_started_at"),
            markers.get("t7_first_delta_at"),
        ),
        "inbound_poll_ms": delta_ms(
            markers.get("t1_provider_received_at"),
            markers.get("t2_channel_event_created_at"),
        ),
        "queue_wait_ms": delta_ms(
            markers.get("t3_turn_queued_at"),
            markers.get("t4_turn_queue_started_at"),
        ),
        "turn_inside_ms": delta_ms(
            markers.get("t4_turn_queue_started_at"),
            markers.get("t7_response_completed_or_failed_at"),
        ),
        "outbound_delivery_ms": delta_ms(
            markers.get("t8_delivery_binding_created_at"),
            markers.get("t9_provider_send_completed_at"),
        ),
        "observed_total_ms": delta_ms(
            markers.get("t0_sent_at_observed"),
            markers.get("t10_reply_seen_at_observed"),
        ),
    }


def percentiles(values: list[Any]) -> dict[str, int | None]:
    numbers = sorted(value for value in values if isinstance(value, int))
    if not numbers:
        return {"p50": None, "p95": None, "max": None, "count": 0}
    return {
        "p50": percentile(numbers, 0.50),
        "p95": percentile(numbers, 0.95),
        "max": numbers[-1],
        "count": len(numbers),
    }


def percentile(numbers: list[int], ratio: float) -> int:
    if len(numbers) == 1:
        return numbers[0]
    index = min(len(numbers) - 1, max(0, round((len(numbers) - 1) * ratio)))
    return numbers[index]


def slowest_trace_span(evidences: list[CaseEvidence]) -> dict[str, Any] | None:
    slowest: dict[str, Any] | None = None
    for evidence in evidences:
        for span in evidence.latency.get("span_latencies_ms") or []:
            latency = span.get("latency_ms")
            if not isinstance(latency, int):
                continue
            if slowest is None or latency > int(slowest.get("latency_ms") or -1):
                slowest = {"case_id": evidence.case_id, **span}
    return slowest


def bottleneck_summary(evidences: list[CaseEvidence]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {
        "context": [],
        "memory": [],
        "model": [],
        "tool": [],
        "delivery": [],
        "continuation": [],
        "other": [],
    }
    for evidence in evidences:
        for span in evidence.latency.get("span_latencies_ms") or []:
            latency = span.get("latency_ms")
            if not isinstance(latency, int):
                continue
            group = bottleneck_group(span)
            grouped.setdefault(group, []).append({"case_id": evidence.case_id, **span})
    return {
        group: {
            "count": len(items),
            "p95_ms": percentiles([item.get("latency_ms") for item in items]).get("p95"),
            "slowest": max(items, key=lambda item: item.get("latency_ms") or -1, default=None),
        }
        for group, items in grouped.items()
    }


def bottleneck_group(span: dict[str, Any]) -> str:
    text = f"{span.get('type') or ''} {span.get('name') or ''}".lower()
    metadata = span.get("metadata") or {}
    if "context" in text:
        return "context"
    if "memory" in text:
        return "memory"
    if "continuation" in text or metadata.get("continuation_iteration") is not None:
        return "continuation"
    if "model" in text or "brain" in text or "response.compose" in text:
        return "model"
    if "tool" in text or "browser" in text or "terminal" in text or "skill" in text:
        return "tool"
    if "delivery" in text or "wechat" in text or "channel" in text:
        return "delivery"
    return "other"


def compare_summaries(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    current_latency = current.get("latency") or {}
    baseline_latency = baseline.get("latency") or {}
    rows: list[dict[str, Any]] = []
    for metric in [
        "first_token_ms",
        "observed_total_ms",
        "turn_inside_ms",
        "inbound_poll_ms",
        "outbound_delivery_ms",
    ]:
        current_metric = current_latency.get(metric) or {}
        baseline_metric = baseline_latency.get(metric) or {}
        rows.append(
            {
                "metric": metric,
                "baseline_p50": baseline_metric.get("p50"),
                "current_p50": current_metric.get("p50"),
                "delta_p50": delta_number(current_metric.get("p50"), baseline_metric.get("p50")),
                "baseline_p95": baseline_metric.get("p95"),
                "current_p95": current_metric.get("p95"),
                "delta_p95": delta_number(current_metric.get("p95"), baseline_metric.get("p95")),
            }
        )
    return {"latency_rows": rows}


def verdict_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"好": 0, "一般": 0, "差": 0, "未知": 0}
    for row in rows:
        verdict = str(row.get("quality_verdict") or "未知")
        if verdict not in counts:
            verdict = "未知"
        counts[verdict] += 1
    return counts


def result_counts(evidences: list[CaseEvidence]) -> dict[str, int]:
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for evidence in evidences:
        status = evidence.result_status if evidence.result_status in counts else "warn"
        counts[status] += 1
    return counts


def delta_number(current: Any, baseline: Any) -> int | None:
    if isinstance(current, int) and isinstance(baseline, int):
        return current - baseline
    return None


def delta_ms(start: Any, end: Any) -> int | None:
    started = parse_time(start)
    ended = parse_time(end)
    if not started or not ended:
        return None
    return int((ended - started).total_seconds() * 1000)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def response_plan_from_events(events: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(events, dict):
        return None
    for item in reversed(events.get("items") or []):
        if item.get("event_type") not in {"response.completed", "turn.failed", "turn.cancelled"}:
            continue
        payload = ((item.get("payload") or {}).get("payload") or {})
        plan = payload.get("response_plan")
        return plan if isinstance(plan, dict) else None
    return None


def continuation_probe(response_plan: dict[str, Any] | None) -> dict[str, Any]:
    structured = (
        (response_plan or {}).get("structured_payload")
        if isinstance(response_plan, dict)
        else {}
    )
    continuation = structured.get("continuation") if isinstance(structured, dict) else {}
    if not isinstance(continuation, dict):
        continuation = {}
    total_latency_ms = continuation.get("total_latency_ms")
    return {
        "continuation_enabled": bool(continuation.get("enabled")),
        "continuation_iterations": continuation.get("iterations"),
        "continuation_quality_verdict": continuation.get("quality_verdict"),
        "continuation_quality_tags": continuation.get("quality_tags") or [],
        "continuation_reason_codes": continuation.get("reason_codes") or [],
        "continuation_budget_exhausted": bool(continuation.get("budget_exhausted")),
        "continuation_used_revision": bool(continuation.get("used_revision")),
        "continuation_initial_latency_ms": continuation.get("initial_latency_ms"),
        "continuation_revision_latency_ms": continuation.get("revision_latency_ms"),
        "continuation_total_latency_ms": total_latency_ms,
        "continuation_latency_slow": isinstance(total_latency_ms, int)
        and isinstance(continuation.get("latency_budget_ms"), int)
        and total_latency_ms > int(continuation.get("latency_budget_ms") or 0),
    }


def observed_reply_text(
    api: str,
    turn: dict[str, Any] | None,
    response_plan: dict[str, Any] | None,
    manual: dict[str, Any],
) -> tuple[str, str]:
    visible_reply = str(manual.get("reply_text_observed") or "").strip()
    if visible_reply:
        return visible_reply, "manual"
    if isinstance(turn, dict):
        assistant_text = str(turn.get("assistant_text") or "").strip()
        if assistant_text:
            return assistant_text, "turn"
        assistant_message_id = str(turn.get("assistant_message_id") or "").strip()
        conversation_id = str(turn.get("conversation_id") or "").strip()
        if assistant_message_id and conversation_id:
            conversation = get_json(
                api,
                f"/api/chat/conversations/{conversation_id}",
                optional=True,
            )
            if isinstance(conversation, dict):
                for message in conversation.get("messages") or []:
                    if message.get("message_id") != assistant_message_id:
                        continue
                    text = str(message.get("content_text") or "").strip()
                    if text:
                        return text, "conversation"
        assistant_message = (turn.get("assistant_message") or {}) if isinstance(turn, dict) else {}
        assistant_message_text = str(assistant_message.get("content") or "").strip()
        if assistant_message_text:
            return assistant_message_text, "turn_message"
    plan_text = str((response_plan or {}).get("plain_text") or "").strip()
    if plan_text:
        return plan_text, "response_plan"
    return "", "missing"


def _evidence_redaction_passed(
    *,
    visible_reply: str,
    turn: dict[str, Any] | None,
    envelope: dict[str, Any] | None,
    queue: dict[str, Any] | None,
    recovery: dict[str, Any] | None,
    compactions: dict[str, Any] | None,
    trace: dict[str, Any] | None,
    events: dict[str, Any] | None,
    response_plan: dict[str, Any] | None,
) -> bool:
    payload = json.dumps(
        {
            "visible_reply": visible_reply,
            "turn": turn or {},
            "envelope": envelope or {},
            "queue": queue or {},
            "recovery": recovery or {},
            "compactions": compactions or {},
            "trace": trace or {},
            "events": events or {},
            "response_plan": response_plan or {},
        },
        ensure_ascii=False,
    )
    return not any(pattern.search(payload) for pattern in SENSITIVE_OUTPUT_PATTERNS)


def case_result_status(
    *,
    turn: dict[str, Any] | None,
    delivery_binding: dict[str, Any] | None,
    quality: dict[str, Any],
    visible_reply: str,
    redaction_passed: bool,
    revision_used: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    info_reasons = ["revision_used"] if revision_used else []
    if not turn:
        reasons.append("no_turn")
    turn_status = str((turn or {}).get("status") or "missing")
    if turn_status not in {"completed", "failed", "cancelled"}:
        reasons.append(f"turn_{turn_status}")
    if not delivery_binding:
        reasons.append("no_delivery")
    elif str(delivery_binding.get("status") or "") != "sent":
        reasons.append(f"delivery_{delivery_binding.get('status') or 'missing'}")
    verdict = str(quality.get("quality_verdict") or "未知")
    if verdict == "差":
        reasons.append("quality_bad")
    elif verdict == "一般":
        reasons.append("quality_average")
    if not redaction_passed:
        reasons.append("redaction_failed")
    if not visible_reply.strip():
        reasons.append("missing_reply")
    if not reasons:
        return "pass", info_reasons
    if "quality_bad" in reasons or "redaction_failed" in reasons or "no_turn" in reasons:
        return "fail", reasons + info_reasons
    if "turn_completed" in reasons or "delivery_sent" not in reasons and any(
        item.startswith("delivery_") for item in reasons
    ):
        return "warn", reasons + info_reasons
    if "quality_average" in reasons:
        return "warn", reasons + info_reasons
    return "warn", reasons + info_reasons


def quality_probe(
    turn: dict[str, Any] | None,
    trace: dict[str, Any] | None,
    response_plan: dict[str, Any] | None,
    manual: dict[str, Any],
    *,
    visible_reply: str | None = None,
    ) -> dict[str, Any]:
    text = json.dumps(turn or {}, ensure_ascii=False)
    forbidden = INTERNAL_VISIBLE_TERMS
    visible_reply = str(
        visible_reply
        or manual.get("reply_text_observed")
        or ((turn or {}).get("assistant_text") or "")
        or ((response_plan or {}).get("plain_text") or "")
        or ((turn or {}).get("assistant_message", {}) or {}).get("content", "")
        or ""
    )
    repeated_starters = ["好的", "明白", "我来", "收到", "已收到", "当前", "摘要", "处理结果"]
    reading_marker_count = sum(1 for marker in READING_SYMBOLS if marker in visible_reply)
    continuation = continuation_probe(response_plan)
    prompt_contract = prompt_contract_probe(response_plan)
    shadow_policy = shadow_policy_probe(response_plan)
    residual_terms = [term for term in OLD_PROMPT_RESIDUAL_TERMS if term in visible_reply]
    probe = {
        "has_turn": bool(turn),
        "forbidden_visible_terms": [term for term in forbidden if term in visible_reply],
        "response_plan_forbidden_terms": [term for term in forbidden if term in text],
        "old_prompt_residual_terms": residual_terms,
        "reply_starts_with_repetitive_marker": any(
            visible_reply.startswith(marker) for marker in repeated_starters
        ),
        "contains_face_emoji": bool(FACE_EMOJI_RE.search(visible_reply)),
        "contains_reading_markers": reading_marker_count > 0,
        "reading_marker_count": reading_marker_count,
        "looks_natural": bool(visible_reply.strip())
        and not visible_reply.startswith(tuple(repeated_starters))
        and not FACE_EMOJI_RE.search(visible_reply),
        "reply_chars_observed": len(visible_reply),
        "response_scenario": (response_plan or {}).get("structured_payload", {}).get("scenario")
        if isinstance(response_plan, dict)
        else None,
        "trace_span_count": len((trace or {}).get("spans") or []),
        **prompt_contract,
        **continuation,
        **shadow_policy,
    }
    probe.update(judge_reply_quality(probe, visible_reply))
    return probe


def shadow_policy_probe(response_plan: dict[str, Any] | None) -> dict[str, Any]:
    structured = (
        (response_plan or {}).get("structured_payload")
        if isinstance(response_plan, dict)
        else {}
    )
    if not isinstance(structured, dict):
        structured = {}
    shadow = structured.get("chat_quality_shadow")
    if not isinstance(shadow, dict):
        shadow = {}
    gate = shadow.get("policy_advisory_gate")
    if not isinstance(gate, dict):
        gate = {}
    comparison = shadow.get("response_policy_comparison")
    if not isinstance(comparison, dict):
        comparison = {}
    diff_fields = [str(item) for item in comparison.get("policy_diffs") or []]
    blockers = [str(item) for item in shadow.get("promotion_blockers") or []]
    return {
        "shadow_policy_gate_enabled": bool(gate.get("eligible_for_policy_advisory")),
        "shadow_policy_gate_reason": str(gate.get("eligibility_reason") or "missing"),
        "shadow_policy_scene": str(gate.get("eligible_scene") or "none"),
        "shadow_policy_comparison_enabled": bool(comparison.get("comparison_enabled")),
        "shadow_policy_diff_fields": diff_fields,
        "shadow_policy_diff_count": len(diff_fields),
        "shadow_policy_promotion_candidate": bool(shadow.get("promotion_candidate")),
        "shadow_policy_promotion_target": str(shadow.get("promotion_target") or "none"),
        "shadow_policy_promotion_blockers": blockers,
        "shadow_policy_safe_to_promote_hint": bool(comparison.get("safe_to_promote_hint")),
    }


def latency_quality_flags(
    latency: dict[str, Any],
    trace: dict[str, Any] | None,
    *,
    response_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    segments = latency.get("segments_ms") or {}
    reasons: list[str] = []
    suggestions: list[str] = []
    first_token = segments.get("first_token_ms")
    turn_inside = segments.get("turn_inside_ms")
    outbound = segments.get("outbound_delivery_ms")
    if isinstance(first_token, int) and first_token > LATENCY_SLOW_THRESHOLDS_MS["first_token"]:
        reasons.append("first_token_slow")
        suggestions.append("检查 context/model 路径，减少首 token 前的重型召回和模型准备。")
    if isinstance(turn_inside, int) and turn_inside > LATENCY_SLOW_THRESHOLDS_MS["turn_inside"]:
        reasons.append("turn_inside_slow")
        suggestions.append("查看最慢 trace span，优先收敛模型、任务或工具链路。")
    if isinstance(outbound, int) and outbound > LATENCY_SLOW_THRESHOLDS_MS["delivery"]:
        reasons.append("wechat_delivery_slow")
        suggestions.append("检查微信出站队列、provider send 和即时投递 watcher。")
    continuation = continuation_probe(response_plan)
    if continuation.get("continuation_budget_exhausted") or continuation.get(
        "continuation_latency_slow"
    ):
        reasons.append("continuation_slow")
        suggestions.append("续跑超过预算，优先减少二次模型调用、缩短修订提示或收紧启用条件。")
    for span in (trace or {}).get("spans") or []:
        latency_ms = span.get("latency_ms")
        if not isinstance(latency_ms, int):
            continue
        if latency_ms <= LATENCY_SLOW_THRESHOLDS_MS["tool"]:
            continue
        if bottleneck_group(span) == "tool":
            reasons.append("tool_slow")
            suggestions.append("拆分工具耗时，检查浏览器/终端/Skill 是否可缓存或降级。")
            break
        if bottleneck_group(span) == "continuation":
            reasons.append("continuation_slow")
            suggestions.append("减少续跑修订上下文或缩小 revision 轮次，避免复杂场景额外等待。")
            break
    return {
        "latency_slow": bool(reasons),
        "latency_reasons": sorted(set(reasons)),
        "latency_suggestions": dedupe_keep_order(suggestions),
    }


def merge_latency_quality(quality: dict[str, Any], latency_flags: dict[str, Any]) -> dict[str, Any]:
    if not latency_flags.get("latency_slow"):
        return latency_flags
    tags = list(quality.get("quality_tags") or [])
    tags.append("latency_slow")
    suggestions = list(quality.get("optimization_suggestions") or [])
    suggestions.extend(latency_flags.get("latency_suggestions") or [])
    verdict = quality.get("quality_verdict") or "未知"
    if verdict == "好":
        verdict = "一般"
    return {
        **latency_flags,
        "quality_tags": sorted(set(tags)),
        "optimization_suggestions": dedupe_keep_order(suggestions),
        "quality_verdict": verdict,
    }


def judge_reply_quality(probe: dict[str, Any], visible_reply: str) -> dict[str, Any]:
    tags: list[str] = []
    suggestions: list[str] = []
    if not probe.get("has_turn"):
        tags.append("no_turn")
        suggestions.append("先修入站、配对或 worker 链路。")
    if not visible_reply.strip():
        tags.append("missing_observed_reply")
        suggestions.append("补充 manual-times 中的微信可见回复文本。")
    if probe.get("forbidden_visible_terms"):
        tags.append("internal_terms_visible")
        suggestions.append("主回复过滤内部字段，禁止 trace/task/approval id 出现在微信可见文本。")
    if probe.get("contains_face_emoji"):
        tags.append("face_emoji_visible")
        suggestions.append("去掉圆脸 emoji，保留书签/章节类符号即可。")
    if probe.get("reply_starts_with_repetitive_marker"):
        tags.append("too_hardcoded")
        suggestions.append("减少固定开头，直接进入结论或承接用户情绪。")
    if any(
        phrase in visible_reply
        for phrase in [
            "我可以帮你",
            "我会帮你",
            "先说结果",
            "结论是",
            "下面我来",
            "摘要如下",
            "当前状态",
            "处理结果",
            "已收到",
        ]
    ):
        tags.append("too_hardcoded")
        suggestions.append("把模板化表达收一收，改成更自然的承接句。")
    if visible_reply and len(visible_reply) < 8:
        tags.append("too_short")
        suggestions.append("短回复补一点承接或下一步，避免显得敷衍。")
    if visible_reply and len(visible_reply) > 500 and not probe.get("contains_reading_markers"):
        tags.append("long_reply_without_reading_marker")
        suggestions.append("中长回复加少量章节/书签式符号，提高扫读体验。")
    if probe.get("latency_slow"):
        tags.append("latency_slow")
        suggestions.extend(probe.get("latency_suggestions") or [])
    if not suggestions:
        suggestions.append("质量可接受，下一步优先继续压耗时。")
    if "no_turn" in tags or "internal_terms_visible" in tags or "face_emoji_visible" in tags:
        verdict = "差"
    elif "missing_observed_reply" in tags:
        verdict = "未知"
    elif tags:
        verdict = "一般"
    else:
        verdict = "好"
    return {
        "quality_verdict": verdict,
        "quality_tags": tags,
        "optimization_suggestions": suggestions,
    }


def quality_score(evidence: CaseEvidence) -> dict[str, Any]:
    turn = evidence.turn or {}
    quality = evidence.quality_probe
    response_scenario = str(quality.get("response_scenario") or "")
    visible_terms = quality.get("forbidden_visible_terms") or []
    has_reply_time = bool(evidence.latency.get("t10_reply_seen_at_observed"))
    latency_slow, latency_reasons = latency_is_slow(evidence)
    turn_status = str(turn.get("status") or "missing")
    completed_or_honest = turn_status in {"completed", "failed", "cancelled"}
    return {
        "case_title": evidence.case_title,
        "sent_message": evidence.sent_text,
        "visible_reply": evidence.visible_reply,
        "reply_source": evidence.reply_source,
        "result_status": evidence.result_status,
        "result_reasons": evidence.result_reasons,
        "revision_used": evidence.revision_used,
        "attachment_understanding": evidence.attachment_understanding,
        "redaction_passed": evidence.redaction_passed,
        "accuracy": 0 if turn_status == "missing" else 1,
        "completeness": 1
        if evidence.delivery_binding or turn_status in {"failed", "cancelled"}
        else 0,
        "structure": 1 if evidence.envelope and evidence.queue else 0,
        "natural_language": 0 if visible_terms else 1,
        "personality_emotion": 0 if quality.get("reply_starts_with_repetitive_marker") else 1,
        "execution_honesty": 1 if completed_or_honest else 0,
        "reading_richness": 1 if quality.get("contains_reading_markers") else 0,
        "no_face_emoji": 0 if quality.get("contains_face_emoji") else 1,
        "latency_slow": 1 if latency_slow else 0,
        "observed_latency_recorded": 1 if has_reply_time else 0,
        "quality_verdict": quality.get("quality_verdict"),
        "quality_tags": quality.get("quality_tags") or [],
        "optimization_suggestions": quality.get("optimization_suggestions") or [],
        "latency_reasons": latency_reasons,
        "gate_status": quality.get("gate_status"),
        "gate_reasons": quality.get("gate_reasons") or [],
        "prompt_snapshot_id": quality.get("prompt_snapshot_id"),
        "voice_policy_version": quality.get("voice_policy_version"),
        "prompt_assembly_version": quality.get("prompt_assembly_version"),
        "prompt_section_ids": quality.get("prompt_section_ids") or [],
        "prompt_section_count": quality.get("prompt_section_count") or 0,
        "prompt_sections_have_content": bool(quality.get("prompt_sections_have_content")),
        "prompt_sections_hash_only": bool(quality.get("prompt_sections_hash_only")),
        "old_prompt_residual_terms": quality.get("old_prompt_residual_terms") or [],
        "response_scenario": response_scenario or None,
        "notes": quality,
    }


def latency_is_slow(evidence: CaseEvidence) -> tuple[bool, list[str]]:
    segments = evidence.latency.get("segments_ms") or {}
    reasons: list[str] = []
    first_token = segments.get("first_token_ms")
    turn_inside = segments.get("turn_inside_ms")
    outbound = segments.get("outbound_delivery_ms")
    if isinstance(first_token, int) and first_token > LATENCY_SLOW_THRESHOLDS_MS["first_token"]:
        reasons.append("first_token_slow")
    if isinstance(turn_inside, int) and turn_inside > LATENCY_SLOW_THRESHOLDS_MS["turn_inside"]:
        reasons.append("turn_inside_slow")
    if isinstance(outbound, int) and outbound > LATENCY_SLOW_THRESHOLDS_MS["delivery"]:
        reasons.append("wechat_delivery_slow")
    for span in evidence.latency.get("span_latencies_ms") or []:
        latency = span.get("latency_ms")
        if isinstance(latency, int) and latency > LATENCY_SLOW_THRESHOLDS_MS["tool"]:
            group = bottleneck_group(span)
            if group == "tool":
                reasons.append("tool_slow")
                break
            if group == "continuation":
                reasons.append("continuation_slow")
                break
    return bool(reasons), sorted(set(reasons))


def build_fix_queue(evidences: list[CaseEvidence]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for evidence in evidences:
        quality = evidence.quality_probe
        latency_slow, latency_reasons = latency_is_slow(evidence)
        tags = list(quality.get("quality_tags") or [])
        gate_status = str(quality.get("gate_status") or "fail")
        gate_reasons = list(quality.get("gate_reasons") or [])
        if latency_slow:
            tags.append("latency_slow")
        if gate_status == "pass" and not tags and not gate_reasons:
            continue
        suggestions = list(quality.get("optimization_suggestions") or [])
        if latency_slow:
            suggestions.extend(latency_suggestions(latency_reasons))
        items.append(
            {
                "case_id": evidence.case_id,
                "severity": fix_severity(tags, gate_status=gate_status),
                "gate_status": gate_status,
                "gate_reasons": gate_reasons,
                "quality_verdict": quality.get("quality_verdict"),
                "tags": sorted(set(tags)),
                "latency_reasons": latency_reasons,
                "suggestions": dedupe_keep_order(suggestions),
                "owner_area": owner_area_for_tags(tags),
                "trace_id": (evidence.turn or {}).get("trace_id"),
                "turn_id": (evidence.turn or {}).get("turn_id"),
            }
        )
    return items


def build_rerun_list(evidences: list[CaseEvidence]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": item["case_id"],
            "reason": ",".join(item["tags"] or item["gate_reasons"] or item["latency_reasons"]),
            "owner_area": item["owner_area"],
        }
        for item in build_fix_queue(evidences)
    ]


def latency_suggestions(reasons: list[str]) -> list[str]:
    suggestions: list[str] = []
    if "first_token_slow" in reasons:
        suggestions.append("检查 context/model 路径，减少首 token 前的重型召回和模型准备。")
    if "turn_inside_slow" in reasons:
        suggestions.append("查看最慢 trace span，优先收敛模型、任务或工具链路。")
    if "tool_slow" in reasons:
        suggestions.append("拆分工具耗时，检查浏览器/终端/Skill 是否可缓存或降级。")
    if "continuation_slow" in reasons:
        suggestions.append("减少续跑修订上下文或缩小 revision 轮次，避免复杂场景额外等待。")
    if "wechat_delivery_slow" in reasons:
        suggestions.append("检查微信出站队列、provider send 和即时投递 watcher。")
    return suggestions


def fix_severity(tags: list[str], *, gate_status: str) -> str:
    if gate_status == "fail":
        return "P1"
    high = {"no_turn", "internal_terms_visible", "face_emoji_visible", "false_done", "latency_slow"}
    return "P1" if any(tag in high for tag in tags) else "P2"


def owner_area_for_tags(tags: list[str]) -> str:
    if "latency_slow" in tags:
        return "latency"
    if any(tag in tags for tag in ["no_turn", "missing_observed_reply"]):
        return "routing"
    if any(tag in tags for tag in ["internal_terms_visible", "too_hardcoded", "too_short"]):
        return "text_quality"
    if any(tag in tags for tag in ["prompt_version_missing", "prompt_sections_have_content", "old_prompt_residual"]):
        return "response_composer"
    if "long_reply_without_reading_marker" in tags:
        return "response_composer"
    return "quality"


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def gap_list(evidences: list[CaseEvidence]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for evidence in evidences:
        case_id = evidence.case_id
        if evidence.turn is None:
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P0",
                    "category": "no_turn",
                    "summary": "没有匹配到 chat turn，可能入站、配对或 worker 链路未通。",
                }
            )
            continue
        if not evidence.delivery_binding and evidence.turn.get("status") == "completed":
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P0",
                    "category": "no_delivery",
                    "summary": "turn 已完成但没有找到微信 delivery binding。",
                }
            )
        if evidence.quality_probe.get("forbidden_visible_terms"):
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P0",
                    "category": "visible_internal_terms",
                    "summary": "微信可见回复包含内部技术字段。",
                    "terms": evidence.quality_probe["forbidden_visible_terms"],
                }
            )
        if evidence.quality_probe.get("contains_face_emoji"):
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P2",
                    "category": "face_emoji_visible",
                    "summary": "微信可见回复出现了圆脸 emoji。",
                }
            )
        if evidence.quality_probe.get("old_prompt_residual_terms"):
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P0",
                    "category": "old_prompt_residual",
                    "summary": "微信可见回复残留旧 prompt 机械话术。",
                    "terms": evidence.quality_probe["old_prompt_residual_terms"],
                }
            )
        if evidence.quality_probe.get("prompt_sections_have_content"):
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P1",
                    "category": "prompt_sections_have_content",
                    "summary": "prompt_sections 仍含完整 content，而不是摘要/hash。",
                }
            )
        if not evidence.quality_probe.get("voice_policy_version") or not evidence.quality_probe.get("prompt_assembly_version"):
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P1",
                    "category": "prompt_version_missing",
                    "summary": "回复缺少 v4 prompt / voice 版本信息。",
                }
            )
        if not evidence.envelope:
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P2",
                    "category": "missing_envelope",
                    "summary": "没有采集到标准化 envelope。",
                }
            )
        if not evidence.queue:
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P2",
                    "category": "missing_queue",
                    "summary": "没有采集到 queue 状态。",
                }
            )
        if evidence.latency.get("segments_ms", {}).get("observed_total_ms") is None:
            gaps.append(
                {
                    "case_id": case_id,
                    "severity": "P2",
                    "category": "missing_observed_latency",
                    "summary": "缺少人工 T0/T10，无法计算用户体感总耗时。",
                }
            )
    for item in gaps:
        item["group"] = gap_group(str(item.get("category") or ""))
    return gaps


def gap_group(category: str) -> str:
    if category in {"no_turn"}:
        return "routing"
    if category in {"no_delivery"}:
        return "delivery"
    if category in {"missing_observed_latency"}:
        return "latency"
    if category in {"missing_envelope", "missing_queue"}:
        return "trace"
    return "quality"


def get_json(api: str, path: str, optional: bool = False) -> dict[str, Any] | None:
    try:
        with urlopen(f"{api}{path}", timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        if optional:
            return None
        raise


def post_json(
    api: str,
    path: str,
    query: dict[str, str] | None = None,
    *,
    optional: bool = False,
) -> dict[str, Any] | None:
    url = f"{api}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    request = Request(url, data=b"", method="POST")
    try:
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        if optional:
            return None
        raise


def load_manual_times(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        case_id = str(item.get("case_id") or "")
        if case_id:
            rows[case_id] = item
    return rows


def load_summary(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def append_jsonl(path: Path, data: Any) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def render_report(evidences: list[CaseEvidence], summary: dict[str, Any] | None = None) -> str:
    lines = [
        "# 微信真实入口场景测试报告",
        "",
        f"- 生成时间：{now_iso()}",
        f"- 用例数：{len(evidences)}",
        "",
        "| Case | Result | Turn | 状态 | 出站 | 首 token ms | turn内ms | 出站ms | 质量提示 | 门禁 |",
        "|---|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for item in evidences:
        turn_id = (item.turn or {}).get("turn_id") or ""
        status = (item.turn or {}).get("status") or "missing"
        outbound = (item.delivery_binding or {}).get("status") or "missing"
        segments = item.latency.get("segments_ms") or {}
        first_token = _display_ms(segments.get("first_token_ms"))
        turn_inside = _display_ms(segments.get("turn_inside_ms"))
        outbound_ms = _display_ms(segments.get("outbound_delivery_ms"))
        verdict = str(item.quality_probe.get("quality_verdict") or "未知")
        tags = ",".join(item.quality_probe.get("quality_tags") or [])
        quality = f"{verdict}:{tags or 'ok'}"
        lines.append(
            f"| {item.case_id} | {item.result_status} | `{turn_id}` | {status} | {outbound} | "
            f"{first_token} | {turn_inside} | {outbound_ms} | {quality} | "
            f"{item.quality_probe.get('gate_status') or 'N/A'} |"
        )
    lines.extend(
        [
            "",
            "## 逐条回放",
            "",
        ]
    )
    for item in evidences:
        attachment = item.attachment_understanding or {}
        understanding_status = (
            attachment.get("understanding_status") or attachment.get("status") or "n/a"
        )
        attachment_summary = ", ".join(
            [
                f"understanding={understanding_status}",
                f"understood={attachment.get('understood_attachment_count') or 0}",
                f"degraded={attachment.get('degraded_attachment_count') or 0}",
                f"memory={attachment.get('memory_candidate_count') or 0}",
            ]
        )
        lines.extend(
            [
                f"### {item.case_id} · {item.case_title}",
                f"- 发送：{_safe_report_text(item.sent_text)}",
                f"- 回复：{_safe_report_text(item.visible_reply) or 'N/A'}",
                f"- 结果：{item.result_status}（{', '.join(item.result_reasons) or 'ok'}）",
                f"- 质量：{item.quality_probe.get('quality_verdict') or '未知'} / "
                f"{', '.join(item.quality_probe.get('quality_tags') or []) or 'ok'}",
                f"- 门禁：{item.quality_probe.get('gate_status') or 'N/A'} / "
                f"{', '.join(item.quality_probe.get('gate_reasons') or []) or 'ok'}",
                f"- 附件理解：{attachment_summary}",
                f"- 修订：{'yes' if item.revision_used else 'no'}；"
                f"红线：{'pass' if item.redaction_passed else 'fail'}",
                f"- Reply source：{item.reply_source}",
                "",
            ]
        )
    lines.extend(
        [
            "## Shadow Policy Advisory",
            "",
            "| Case | Gate | Scene | Compare | Diffs | Candidate | Target | Blockers |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for item in evidences:
        quality = item.quality_probe
        diff_fields = ",".join(quality.get("shadow_policy_diff_fields") or []) or "none"
        blockers = ",".join(quality.get("shadow_policy_promotion_blockers") or []) or "none"
        lines.append(
            f"| {item.case_id} | "
            f"{quality.get('shadow_policy_gate_enabled')} "
            f"({quality.get('shadow_policy_gate_reason') or 'missing'}) | "
            f"{quality.get('shadow_policy_scene') or 'none'} | "
            f"{quality.get('shadow_policy_comparison_enabled')} | "
            f"{diff_fields} | "
            f"{quality.get('shadow_policy_promotion_candidate')} | "
            f"{quality.get('shadow_policy_promotion_target') or 'none'} | "
            f"{blockers} |"
        )
    lines.extend(
        [
            "## 汇总",
            "",
            f"- 首 token p50/p95：{_summary_ratio(summary, 'first_token_ms')}",
            f"- 用户体感总耗时 p50/p95：{_summary_ratio(summary, 'observed_total_ms')}",
            f"- turn 内耗时 p50/p95：{_summary_ratio(summary, 'turn_inside_ms')}",
            f"- 入站轮询 p50/p95：{_summary_ratio(summary, 'inbound_poll_ms')}",
            f"- 出站投递 p50/p95：{_summary_ratio(summary, 'outbound_delivery_ms')}",
            f"- 最慢 case：{_summary_value(summary, 'slowest_case')}",
            f"- 最慢 span：{_summary_value(summary, 'slowest_span')}",
            f"- 慢点分组：{_summary_value(summary, 'bottlenecks')}",
            f"- 阅读型符号命中：{_summary_value(summary, 'reading_markers')}",
            f"- 损坏修复触发次数：{_summary_value(summary, 'continuation_enabled')}",
            f"- prompt 版本覆盖：{_summary_value(summary, 'prompt_version_coverage')}",
            f"- 门禁分布：{_summary_value(summary, 'gate_status_counts')}",
            f"- 结果分布：{_summary_value(summary, 'result_counts')}",
            f"- 质量判定分布：{_summary_value(summary, 'verdict_counts')}",
            f"- Shadow policy 汇总：{_summary_value(summary, 'shadow_policy')}",
            f"- Promotion readiness：{_summary_value(summary, 'promotion_readiness')}",
            "",
            "## 延迟口径",
            "",
            "- 入站轮询耗时：T1 -> T2。",
            "- 队列等待耗时：T3 -> T4。",
            "- turn 内耗时：T4 -> T7。",
            "- 出站投递耗时：T8 -> T9。",
            "- 用户体感总耗时：T0 -> T10，需要人工在 manual-times JSONL 中填写。",
        ]
    )
    if summary and summary.get("comparison"):
        rows = summary["comparison"].get("latency_rows") or []
        lines.extend(
            [
                "",
                "## 优化前后对比",
                "",
                "| 指标 | baseline p50 | current p50 | Δp50 | baseline p95 | current p95 | Δp95 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            metric = row.get("metric")
            baseline_p50 = _display_cell(row.get("baseline_p50"))
            current_p50 = _display_cell(row.get("current_p50"))
            delta_p50 = _display_cell(row.get("delta_p50"))
            baseline_p95 = _display_cell(row.get("baseline_p95"))
            current_p95 = _display_cell(row.get("current_p95"))
            delta_p95 = _display_cell(row.get("delta_p95"))
            lines.append(
                f"| {metric} | {baseline_p50} | {current_p50} | {delta_p50} | "
                f"{baseline_p95} | {current_p95} | {delta_p95} |"
            )
    return "\n".join(lines) + "\n"


def _summary_ratio(summary: dict[str, Any] | None, metric: str) -> str:
    if not summary:
        return "N/A"
    payload = (summary.get("latency") or {}).get(metric) or {}
    return f"p50={payload.get('p50')}, p95={payload.get('p95')}"


def _summary_value(summary: dict[str, Any] | None, key: str) -> str:
    if not summary:
        return "N/A"
    if key == "slowest_case":
        payload = (summary.get("latency") or {}).get(key) or {}
        latency = payload.get("observed_total_ms")
        return f"{payload.get('case_id') or 'N/A'} / {_display_ms(latency) or 'N/A'}"
    if key == "slowest_span":
        payload = (summary.get("latency") or {}).get(key) or {}
        latency = payload.get("latency_ms")
        case_id = payload.get("case_id") or "N/A"
        name = payload.get("name") or "N/A"
        return f"{case_id} / {name} / {_display_ms(latency) or 'N/A'}"
    if key == "reading_markers":
        payload = summary.get("quality") or {}
        return str(payload.get("with_reading_markers"))
    if key == "continuation_enabled":
        payload = summary.get("quality") or {}
        return str(payload.get("with_continuation_enabled"))
    if key == "prompt_version_coverage":
        payload = summary.get("quality") or {}
        return json.dumps(payload.get("prompt_version_coverage") or {}, ensure_ascii=False)
    if key == "gate_status_counts":
        payload = summary.get("quality") or {}
        return json.dumps(payload.get("gate_status_counts") or {}, ensure_ascii=False)
    if key == "result_counts":
        payload = summary.get("quality") or {}
        return json.dumps(payload.get("result_counts") or {}, ensure_ascii=False)
    if key == "verdict_counts":
        payload = summary.get("quality") or {}
        return json.dumps(payload.get("verdict_counts") or {}, ensure_ascii=False)
    if key == "shadow_policy":
        payload = (summary.get("quality") or {}).get("shadow_policy") or {}
        compact = {
            "comparison_enabled_count": payload.get("comparison_enabled_count"),
            "promotion_candidate_count": payload.get("promotion_candidate_count"),
            "policy_diff_field_counts": payload.get("policy_diff_field_counts") or {},
            "promotion_target_counts": payload.get("promotion_target_counts") or {},
            "promotion_blocker_counts": payload.get("promotion_blocker_counts") or {},
        }
        return json.dumps(compact, ensure_ascii=False)
    if key == "promotion_readiness":
        payload = (
            ((summary.get("quality") or {}).get("shadow_policy") or {}).get(
                "promotion_readiness"
            )
            or {}
        )
        return json.dumps(payload, ensure_ascii=False)
    if key == "bottlenecks":
        payload = (summary.get("latency") or {}).get("bottlenecks") or {}
        compact = {
            group: (item.get("slowest") or {}).get("latency_ms")
            for group, item in payload.items()
            if item.get("slowest")
        }
        return json.dumps(compact, ensure_ascii=False)
    return "N/A"


def _display_ms(value: Any) -> str:
    return "" if value is None else str(value)


def _display_cell(value: Any) -> str:
    return "" if value is None else str(value)


def _safe_report_text(text: str) -> str:
    safe = str(text or "")
    for pattern in SENSITIVE_OUTPUT_PATTERNS:
        safe = pattern.sub("[REDACTED]", safe)
    return safe


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


if __name__ == "__main__":
    main()
