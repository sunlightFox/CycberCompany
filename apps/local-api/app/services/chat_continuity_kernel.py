from __future__ import annotations

from typing import Any

from core_types import (
    ActionLedgerEntry,
    EvidenceLedgerEntry,
    ExecutionEvidenceDecision,
    TurnContinuationDecision,
    TurnEnvelope,
    VisibleReplyPlan,
)

from app.core.time import new_id, utc_now_iso
from app.services.action_result_summary import artifact_names_from_refs
from app.services.action_result_summary import summarize_completed_action_result
from app.services.brain_decision_support import safe_plan_only
from app.services.chat_intent_router import is_host_filesystem_list_request
from app.services.chat_turn_input_facts import (
    looks_like_execution_state_explanation_request,
    looks_like_latest_instruction_override,
)
from app.services.pending_action_resolution import (
    is_ambiguous_continue,
    is_confirm,
    is_deny,
    is_edit,
    looks_like_resolution,
)


def build_turn_envelope(
    *,
    turn: dict[str, Any],
    user_text: str,
    session_id: str | None,
    working_state: dict[str, Any] | None,
    continuity_snapshot: dict[str, Any] | None,
    pending_actions: list[dict[str, Any]] | None = None,
) -> TurnEnvelope:
    state = dict(working_state or {})
    snapshot = dict(continuity_snapshot or {})
    ledgers = action_ledgers_from_snapshot(snapshot)
    latest = ledgers[0] if ledgers else {}
    latest_completed = next(
        (
            item
            for item in ledgers
            if str(item.get("execution_state") or "") == "completed"
        ),
        latest,
    )
    ingress = dict(turn.get("ingress_metadata") or {})
    attachments = list(ingress.get("attachments") or [])
    context_refs = list(ingress.get("context_refs") or [])
    pending = list(pending_actions or [])
    active_pending_ref = ""
    if pending:
        active_pending_ref = str(
            pending[0].get("pending_action_id")
            or pending[0].get("approval_id")
            or pending[0].get("task_id")
            or ""
        )
    last_artifact_refs = list(
        latest.get("artifact_refs")
        or latest_completed.get("artifact_refs")
        or state.get("referenced_artifacts")
        or []
    )
    return TurnEnvelope(
        session_key=str(session_id or turn.get("conversation_id") or ""),
        provider=str(turn.get("channel") or ingress.get("provider") or "local"),
        thread_key=str(ingress.get("channel_thread_id") or turn.get("conversation_id") or ""),
        sender_key=str(turn.get("member_id") or ""),
        source_message_id=str(
            ingress.get("source_message_id")
            or ingress.get("channel_message_id")
            or turn.get("user_message_id")
            or ""
        ),
        raw_text=str(user_text or ""),
        normalized_text=_compact_text(user_text),
        attachments=[dict(item) for item in attachments if isinstance(item, dict)],
        context_refs=[dict(item) for item in context_refs if isinstance(item, dict)],
        queue_policy=str(ingress.get("queue_policy") or "immediate"),
        reply_to_turn_id=snapshot.get("source_turn_id"),
        latest_instruction_override=looks_like_latest_instruction_override(user_text),
        active_pending_action_ref=active_pending_ref or None,
        last_active_action_ref=str(latest.get("action_ref") or "") or None,
        last_completed_action_ref=str(latest_completed.get("action_ref") or "") or None,
        last_artifact_refs=[dict(item) for item in last_artifact_refs if isinstance(item, dict)],
        last_visible_reply_kind=str(latest.get("reply_kind") or "") or None,
    )


def resolve_turn_continuation(
    *,
    envelope: TurnEnvelope,
    user_text: str,
    pending_actions: list[dict[str, Any]] | None,
    continuity_snapshot: dict[str, Any] | None,
    turn_response_kind: str | None = None,
) -> TurnContinuationDecision:
    text = str(user_text or "").strip()
    pending = list(pending_actions or [])
    latest = latest_action_ledger(continuity_snapshot)
    latest_completed = latest_completed_action_ledger(continuity_snapshot)
    latest_artifact = first_artifact_ref(latest_completed or latest)

    if envelope.latest_instruction_override:
        return TurnContinuationDecision(
            turn_kind="fresh_request",
            continuation_confidence=1.0,
            reason_codes=["latest_instruction_override"],
        )
    if is_host_filesystem_list_request(text) or _looks_like_abstract_quality_question(text):
        return TurnContinuationDecision(
            turn_kind="fresh_request",
            continuation_confidence=0.98,
            reason_codes=["fresh_request_explicit_user_intent"],
        )

    if pending and (looks_like_resolution(text) or is_confirm(text) or is_deny(text) or is_edit(text) or is_ambiguous_continue(text)):
        action = pending[0]
        return TurnContinuationDecision(
            turn_kind="approval_reply",
            bound_action_ref=_pending_action_ref(action),
            bound_pending_ref=_pending_action_ref(action),
            continuation_confidence=1.0,
            reason_codes=["active_pending_approval"],
        )

    if not pending and (looks_like_resolution(text) or is_confirm(text) or is_deny(text) or is_ambiguous_continue(text)):
        return TurnContinuationDecision(
            turn_kind="approval_reply",
            continuation_confidence=0.9,
            reason_codes=["no_pending_resolution_reply"],
        )

    if _looks_like_template_or_explanation(text, turn_response_kind=turn_response_kind):
        return TurnContinuationDecision(
            turn_kind="template_or_explanation",
            bound_action_ref=str((latest or {}).get("action_ref") or "") or None,
            bound_artifact_ref=_artifact_id(latest_artifact),
            continuation_confidence=0.9,
            reason_codes=["template_or_explanation_request"],
        )

    if safe_plan_only(text):
        return TurnContinuationDecision(
            turn_kind="plan_only_request",
            bound_action_ref=str((latest or {}).get("action_ref") or "") or None,
            continuation_confidence=0.92,
            reason_codes=["safe_plan_only"],
        )

    if looks_like_execution_state_explanation_request(text):
        bound = pending[0] if pending else latest or latest_completed
        return TurnContinuationDecision(
            turn_kind="followup_question",
            bound_action_ref=str((bound or {}).get("action_ref") or _pending_action_ref(bound or {})) or None,
            bound_pending_ref=_pending_action_ref(bound or {}),
            bound_artifact_ref=_artifact_id(latest_artifact),
            continuation_confidence=0.9 if bound else 0.45,
            reason_codes=["execution_state_followup"],
        )

    if _looks_like_post_completion_recall(text):
        bound = latest_completed or latest
        return TurnContinuationDecision(
            turn_kind="post_completion_recall",
            bound_action_ref=str((bound or {}).get("action_ref") or "") or None,
            bound_artifact_ref=_artifact_id(first_artifact_ref(bound)),
            continuation_confidence=0.88 if bound else 0.4,
            reason_codes=["post_completion_recall"],
        )

    if _looks_like_followup_question(text) and (pending or latest):
        bound = pending[0] if pending else latest
        return TurnContinuationDecision(
            turn_kind="followup_question",
            bound_action_ref=str((bound or {}).get("action_ref") or _pending_action_ref(bound or {})) or None,
            bound_pending_ref=_pending_action_ref(bound or {}),
            bound_artifact_ref=_artifact_id(first_artifact_ref(bound)),
            continuation_confidence=0.8,
            reason_codes=["short_followup_binding"],
        )

    return TurnContinuationDecision(
        turn_kind="fresh_request",
        continuation_confidence=0.35,
        reason_codes=["fresh_request_fallback"],
    )


def visible_reply_plan(
    *,
    reply_mode: str,
    source: str,
    text: str,
    bound_action_ref: str | None = None,
    reason_codes: list[str] | None = None,
) -> VisibleReplyPlan:
    return VisibleReplyPlan(
        reply_mode=reply_mode,
        source=source,
        text=str(text or ""),
        bound_action_ref=bound_action_ref,
        reason_codes=list(reason_codes or []),
    )


def action_ledgers_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = list(dict(snapshot or {}).get("followup_candidates") or [])
    return [
        dict(item.get("action_ledger") or {})
        for item in items
        if isinstance(item, dict) and isinstance(item.get("action_ledger"), dict)
    ]


def evidence_ledgers_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = list(dict(snapshot or {}).get("followup_candidates") or [])
    ledgers: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for evidence in list(item.get("evidence_ledger") or []):
            if isinstance(evidence, dict):
                ledgers.append(dict(evidence))
    return ledgers


def latest_action_ledger(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    ledgers = action_ledgers_from_snapshot(snapshot)
    return ledgers[0] if ledgers else {}


def latest_completed_action_ledger(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    for item in action_ledgers_from_snapshot(snapshot):
        if str(item.get("execution_state") or "") == "completed":
            return item
    return {}


def first_artifact_ref(action_ledger: dict[str, Any] | None) -> dict[str, Any] | None:
    action = dict(action_ledger or {})
    refs = list(action.get("artifact_refs") or [])
    for item in refs:
        if isinstance(item, dict):
            return dict(item)
    return None


def build_action_ledger_entry(
    *,
    turn: dict[str, Any],
    response_plan: dict[str, Any] | None,
    assistant_text: str,
) -> dict[str, Any] | None:
    plan = dict(response_plan or {})
    structured = dict(plan.get("structured_payload") or {})
    route_semantics = dict(structured.get("route_semantics") or {})
    natural = dict(structured.get("natural_interaction") or {})
    route_type = str(route_semantics.get("route") or "")
    action_state = str(
        natural.get("action_state")
        or natural.get("status")
        or plan.get("status")
        or "completed"
    )
    artifact_refs = _artifact_refs_from_plan(plan)
    if not route_type and not natural and not artifact_refs:
        return None
    action_ref = str(turn.get("turn_id") or new_id("act"))
    pending_actions = list(natural.get("pending_actions") or structured.get("pending_actions") or [])
    primary_action = dict(pending_actions[0]) if pending_actions else {}
    reply_kind = str(natural.get("turn_response_kind") or route_type or "natural_reply")
    summary = _truncate(str(assistant_text or plan.get("plain_text") or plan.get("summary") or ""), 240)
    return ActionLedgerEntry(
        action_ref=action_ref,
        session_key=str(turn.get("session_id") or turn.get("conversation_id") or ""),
        provider=str(turn.get("channel") or "local"),
        route_type=route_type or str(turn.get("intent") or "natural_interaction"),
        intent=str(turn.get("intent") or route_type or "natural_interaction"),
        user_visible_goal=str(primary_action.get("user_label") or summary),
        target_summary=_target_summary(plan, primary_action),
        approval_state=_approval_state(primary_action, action_state),
        execution_state=_normalized_execution_state(action_state, artifact_refs),
        started_at=str(turn.get("created_at") or utc_now_iso()),
        ended_at=utc_now_iso(),
        artifact_refs=artifact_refs,
        last_tool_result_refs=_tool_result_refs(structured),
        superseded_by=None,
        reason_codes=_unique(
            [
                *list(natural.get("reason_codes") or []),
                *list(route_semantics.get("reason_codes") or []),
                reply_kind,
            ]
        ),
    ).model_dump(mode="json") | {"reply_kind": reply_kind, "result_summary": summary}


def build_evidence_ledger_entries(
    *,
    action_ledger: dict[str, Any] | ActionLedgerEntry | None,
    evidence_gate: ExecutionEvidenceDecision | dict[str, Any] | None,
    response_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if action_ledger is None:
        return []
    if hasattr(action_ledger, "model_dump"):
        action = action_ledger.model_dump(mode="json")
    else:
        action = dict(action_ledger or {})
    gate = (
        evidence_gate.model_dump(mode="json")
        if hasattr(evidence_gate, "model_dump")
        else dict(evidence_gate or {})
    )
    action_ref = str(action.get("action_ref") or "")
    if not action_ref:
        return []
    refs = list(gate.get("evidence_refs") or _artifact_refs_from_plan(dict(response_plan or {})))
    reason_codes = list(gate.get("reason_codes") or [])
    status = str(gate.get("status") or "recorded")
    items: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        evidence_type = str(ref.get("type") or ("artifact_ref" if ref.get("artifact_id") else "evidence_ref"))
        items.append(
            EvidenceLedgerEntry(
                action_ref=action_ref,
                evidence_type=evidence_type,
                ref=dict(ref),
                status=status,
                created_at=utc_now_iso(),
                reason_codes=reason_codes,
            ).model_dump(mode="json")
        )
    return items


def compose_template_or_explanation_reply(
    text: str,
    *,
    action_ledger: dict[str, Any] | None = None,
    evidence_gate: ExecutionEvidenceDecision | None = None,
) -> str | None:
    raw = str(text or "")
    lowered = raw.lower()
    if "office" in lowered and (
        "模板" in raw
        or ("自然话" in raw and ("告诉我" in raw or "回复" in raw))
        or ("怎么" in raw and ("告诉我" in raw or "回复" in raw))
    ):
        return (
            "可以这样说：这次 Office 任务已经处理好了，我已经生成或更新了对应文件，"
            "也保留了可核对的结果记录。"
            "如果其中某一步没有真的完成，我会直接告诉你卡在哪、还缺什么，"
            "以及下一步需要你补什么。"
        )
    if "office" in lowered and "模板" in raw:
        return (
            "可以这样告诉你：这次 Office 文件已经处理好了，我已经生成或更新了对应文件，并保留了可核对的结果。"
            "如果其中某一步没有真正完成，我会直接告诉你卡在哪、还缺什么，以及下一步需要你补什么。"
        )
    if "skill" in lowered and "mcp" in lowered:
        return "Skill 更像平台内已经接好的能力封装，MCP 更像把外部工具或服务按协议接进来。前者偏产品化能力，后者偏连接标准。"
    if any(marker in raw for marker in ("为什么浏览器任务完成后要带证据", "为什么要带证据", "为什么要先问我确认")):
        return (
            "因为这类任务不是只要说一句“完成”就够了，我需要拿到能核对的结果，才能证明真的做到了。"
            "如果还没执行、还在等确认，或者结果没落到记录里，我就应该明确告诉你现在卡在哪，而不是把未完成说成完成。"
        )
    if any(marker in raw for marker in ("如果文件还没生成成功", "不要说已完成", "这时候你会怎么回复")):
        return (
            "我会直接告诉你这一步还没完成，并说明现在缺什么结果或证据。"
            "比如会说：文件还没真正生成成功，我先不把它说成完成；等文件产物或任务记录落下来后，我再把最终结果告诉你。"
        )
    if "模板" in raw and any(marker in lowered for marker in ("browser", "office", "download", "task")):
        return (
            "可以这样说：这次任务已经处理好了，我已经核对了结果，并保留了可回看的记录。"
            "如果其中某一步没有真正完成，我会直接说明没完成、卡在哪，以及下一步需要你补什么。"
        )
    if "标题" in raw and action_ledger:
        title = _title_from_action(action_ledger)
        if title:
            return title
    if evidence_gate is not None and "证据" in raw and "为什么" in raw:
        missing = "、".join(list(evidence_gate.missing_evidence_types or []))
        if missing:
            return f"因为现在还缺 {missing} 这类完成证据，所以我不能把这一步说成已经完成。"
    return None


def compose_completion_status_reply(
    text: str,
    *,
    action_ledger: dict[str, Any] | None,
) -> str | None:
    action = dict(action_ledger or {})
    if not action:
        return None
    raw = str(text or "")
    if is_host_filesystem_list_request(raw):
        return None
    execution_state = str(action.get("execution_state") or "")
    if execution_state == "completed" and any(
        marker in raw for marker in ("证据", "完成", "还在等", "已经", "状态")
    ):
        summary = _completed_action_summary(action)
        if summary:
            return (
                "这一步已经完成了，不是在等额外证据。"
                f"当前能核对到的结果是：{summary}。"
            )
        return "这一步已经完成了，不是在等额外证据。我现在是根据已经落下来的结果和记录来确认完成的。"
    return None


def compose_plan_only_reply(text: str, *, action_ledger: dict[str, Any] | None = None) -> str:
    raw = str(text or "")
    if any(marker in raw for marker in ("删除", "删掉")):
        return "可以，先只给方案：先确认目标文件路径，再检查是否可恢复，然后执行删除，最后再核对文件是否已经不在原位置。你现在这句是方案请求，我不会直接动手。"
    if any(marker in raw for marker in ("卸载", "移除软件")):
        return "可以，先只给方案：先确认软件名称和版本，再确认卸载方式与影响范围，执行前保留回滚路径，最后再检查程序、快捷方式和残留目录是否清理完成。你现在这句是方案请求，我不会直接执行。"
    label = str((action_ledger or {}).get("user_visible_goal") or "这件事")
    return f"可以，先只给方案：先确认目标和范围，再列执行步骤、风险点和回滚方式。你现在这句是方案请求，我不会直接执行 {label}。"


def compose_post_completion_reply(
    text: str,
    *,
    action_ledger: dict[str, Any] | None,
) -> str | None:
    action = dict(action_ledger or {})
    if not action:
        return None
    raw = str(text or "")
    if "标题" in raw:
        title = _title_from_action(action)
        if title:
            return title
    if any(marker in raw for marker in ("什么文件", "哪个文件", "生成的文件")):
        names = _artifact_names(action)
        if names:
            return f"刚才生成的文件是 {'、'.join(names[:3])}。"
    summary = _completed_action_summary(action)
    if summary:
        return summary
    return None


def _looks_like_template_or_explanation(text: str, *, turn_response_kind: str | None) -> bool:
    raw = str(text or "")
    lowered = raw.lower()
    if turn_response_kind in {"knowledge_explanation", "template_request"}:
        return True
    if "office" in lowered and (
        ("自然话" in raw and ("告诉我" in raw or "回复" in raw))
        or ("怎么" in raw and ("告诉我" in raw or "回复" in raw))
    ):
        return True
    if "skill" in lowered and "mcp" in lowered:
        return True
    markers = ("模板", "怎么回复", "怎么告诉我", "解释", "区别", "为什么", "什么意思", "通俗")
    return any(marker in raw for marker in markers)


def _looks_like_post_completion_recall(text: str) -> bool:
    raw = str(text or "").strip()
    markers = ("刚才", "前面", "上一个", "刚刚", "那个页面", "那个文件", "标题", "什么文件")
    return any(marker in raw for marker in markers) and any(
        marker in raw for marker in ("标题", "什么", "哪个", "生成", "结果", "文件")
    )


def _looks_like_followup_question(text: str) -> bool:
    raw = str(text or "").strip()
    if len(_compact_text(raw)) > 24:
        return False
    return any(marker in raw for marker in ("好的", "继续", "然后", "下一步", "怎么回", "怎么说", "现在呢"))


def _looks_like_abstract_quality_question(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if any(
        marker in raw
        for marker in (
            "刚才",
            "前面",
            "上一步",
            "这次操作",
            "那个文件",
            "那个页面",
            "安装现在",
            "下载现在",
        )
    ):
        return False
    quality_pairs = (
        ("有回复", "有证据"),
        ("未完成", "已完成"),
        ("多个子任务", "已完成结论"),
    )
    return any(all(marker in raw for marker in pair) for pair in quality_pairs)


def _pending_action_ref(action: dict[str, Any]) -> str | None:
    return str(
        action.get("pending_action_id")
        or action.get("approval_id")
        or action.get("task_id")
        or action.get("action_ref")
        or ""
    ) or None


def _artifact_id(ref: dict[str, Any] | None) -> str | None:
    return str((ref or {}).get("artifact_id") or (ref or {}).get("task_id") or "") or None


def _compact_text(text: str) -> str:
    return "".join(str(text or "").strip().split())


def _artifact_refs_from_plan(response_plan: dict[str, Any]) -> list[dict[str, Any]]:
    refs = list(response_plan.get("artifact_refs") or [])
    structured = dict(response_plan.get("structured_payload") or {})
    for key in ("browser_read_page", "host_filesystem_list", "terminal_readonly_command"):
        payload = structured.get(key)
        if isinstance(payload, dict):
            refs.extend(list(payload.get("evidence_refs") or []))
    return [dict(item) for item in refs if isinstance(item, dict)]


def _tool_result_refs(structured_payload: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for key in ("browser_read_page", "host_filesystem_list", "terminal_readonly_command"):
        payload = structured_payload.get(key)
        if isinstance(payload, dict):
            refs.append(dict(payload))
    return refs[:4]


def _target_summary(response_plan: dict[str, Any], action: dict[str, Any]) -> str:
    structured = dict(response_plan.get("structured_payload") or {})
    browser = dict(structured.get("browser_read_page") or {})
    if browser.get("url"):
        return str(browser.get("url"))
    payload_summary = dict(action.get("payload_summary") or {})
    for key in ("url", "path", "requested_software", "display_name"):
        value = str(payload_summary.get(key) or "").strip()
        if value:
            return value
    return str(action.get("user_summary") or action.get("user_label") or "")[:160]


def _approval_state(action: dict[str, Any], action_state: str) -> str:
    if str(action_state) == "pending_approval":
        return "required"
    if action:
        return "required"
    return "not_required"


def _normalized_execution_state(action_state: str, artifact_refs: list[dict[str, Any]]) -> str:
    if artifact_refs and action_state not in {"pending_approval", "waiting_evidence"}:
        return "completed"
    mapping = {
        "approved": "running",
        "edited": "running",
        "denied": "failed",
        "blocked": "failed",
        "hard_block": "failed",
        "plain_next_step": "pending_approval",
        "pending_action": "waiting_evidence",
        "no_pending_action": "idle",
    }
    return mapping.get(action_state, action_state or "completed")


def _title_from_action(action: dict[str, Any]) -> str | None:
    refs = list(action.get("last_tool_result_refs") or [])
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        title = str(ref.get("title") or ref.get("page_state", {}).get("title") or "").strip()
        if title:
            return title
    return None


def _artifact_names(action: dict[str, Any]) -> list[str]:
    return artifact_names_from_refs(list(action.get("artifact_refs") or []))


def _completed_action_summary(action: dict[str, Any]) -> str:
    return summarize_completed_action_result(
        label=str(action.get("user_visible_goal") or ""),
        target=str(action.get("target_summary") or ""),
        artifact_refs=list(action.get("artifact_refs") or []),
        result_summary=str(action.get("result_summary") or ""),
    )


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def _truncate(text: str, limit: int) -> str:
    value = str(text or "").strip()
    return value if len(value) <= limit else f"{value[:limit].rstrip()}..."
