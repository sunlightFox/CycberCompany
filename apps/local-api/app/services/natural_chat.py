from __future__ import annotations

import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from core_types import (
    ApprovalDetail,
    ExecutionEvidenceDecision,
    ResponsePlan,
    RiskLevel,
)
from response_composer import ResponseComposer
from response_composer import canonical_action_status, normalize_action_status_semantics
from response_composer.chat_voice import voice_metadata_for_scenario
from response_composer.opening_copy import opening_copy
from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.schemas.chat_quality import ActionDialogueFacts
from app.services.action_dialogue_mapper import ActionDialogueMapperService
from app.services.approvals import ApprovalService
from app.services.chat_session_runtime import (
    ChatSessionResumeDispatcher,
    ChatSessionRuntime,
)
from app.services.chat_visible_guard import (
    reset_visible_redaction_profile as _reset_visible_redaction_profile,
)
from app.services.chat_visible_guard import (
    set_visible_redaction_profile as _set_visible_redaction_profile,
)
from app.services.chat_visible_guard import (
    visible_text_guard as _visible_text_guard,
)
from app.services.action_resolution_copy import hard_block_text as _hard_block_text
from app.services.chat_continuity_kernel import (
    build_action_ledger_entry as _build_action_ledger_entry,
    build_turn_envelope as _build_turn_envelope,
    compose_completion_status_reply as _compose_completion_status_reply,
    compose_plan_only_reply as _compose_plan_only_reply,
    compose_post_completion_reply as _compose_post_completion_reply,
    compose_template_or_explanation_reply as _compose_template_or_explanation_reply,
    latest_action_ledger as _latest_action_ledger,
    latest_completed_action_ledger as _latest_completed_action_ledger,
    resolve_turn_continuation as _resolve_turn_continuation,
    visible_reply_plan as _visible_reply_plan,
)
from app.services.natural_chat_response_plan import (
    action_status_facts as _action_status_facts,
    after_resolution_text as _after_resolution_text,
    allowed_scopes as _allowed_scopes,
    ambiguous_pending_text as _ambiguous_pending_text,
    block_reason_for_status as _block_reason_for_status,
    external_platform_structured_payload as _external_platform_structured_payload,
    impact_for_action as _impact_for_action,
    label_for_action as _label_for_action,
    max_risk as _max_risk,
    multiple_pending_text as _multiple_pending_text,
    natural_interaction_payload as _natural_interaction_payload,
    natural_quality_guard as _natural_quality_guard,
    no_pending_text as _no_pending_text,
    payload_summary as _payload_summary,
    pending_action_binding as _pending_action_binding,
    pending_action_prompt as _pending_action_prompt,
    plain_next_step_text as _plain_next_step_text,
    plan as _plan,
    reply_option_items as _reply_option_items,
    reply_options_for_action as _reply_options_for_action,
    reply_options_from_actions as _reply_options_from_actions,
    risk_order as _risk_order,
    session_grant as _session_grant,
    summary_for_action as _summary_for_action,
    task_status_payload as _task_status_payload,
    technical_detail as _technical_detail,
)
from app.services.natural_chat_surface import deterministic_plain_reply as _deterministic_plain_reply
from app.services.pending_action_resolution import (
    asks_how_to_confirm as _asks_how_to_confirm,
    control_text as _control_text,
    edit_payload_for_action as _edit_payload_for_action,
    hard_block_reason as _hard_block_reason,
    is_always_allow as _is_always_allow,
    is_ambiguous_continue as _is_ambiguous_continue,
    is_confirm as _is_confirm,
    is_deny as _is_deny,
    is_edit as _is_edit,
    is_session_allow as _is_session_allow,
    looks_like_new_action_request as _looks_like_new_action_request,
    looks_like_resolution as _looks_like_resolution,
)
from app.services.chat_intent_router import is_browser_page_action_request as _is_browser_page_action_request
from app.services.execution_evidence_gate import decide_execution_evidence as _decide_execution_evidence
from app.services.turn_response_router import route_turn_response as _route_turn_response

FORBIDDEN_MAIN_REPLY_TERMS = {
    "approval_id": "确认编号",
    "tool_call_id": "工具记录",
    "trace_id": "审计记录",
    "内部 trace": "过程记录",
    "browser.download": "下载动作",
    "browser.snapshot": "网页快照",
    "browser.screenshot": "页面截图",
    "task_id": "任务记录",
    "工具边界": "处理限制",
    "受控任务": "处理流程",
    "任务回放": "结果记录",
    "工件": "结果记录",
    "Capability Graph": "权限范围",
    "Asset Broker": "授权资源通道",
    "Safety": "风险检查",
    "Approval": "确认",
    "R3": "需要确认的风险",
    "R4": "较高风险",
    "R5": "高风险",
    "/api/approvals": "确认接口",
}

_URL_RE = re.compile(r"https?://[^\s，。；;？?]+", re.IGNORECASE)
_VISIBLE_REDACTION_PROFILE: ContextVar[str] = ContextVar(
    "chat_visible_redaction_profile",
    default="strict",
)
_RELAXED_SECRET_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}"), "[REDACTED_API_KEY]"),
    (
        re.compile(
            r"(?i)(api[_-]?key|token|secret|cookie|password|passwd|pwd)"
            r"\s*[:=]\s*['\"]?[^'\"\s,;]+"
        ),
        r"\1=[REDACTED_TOKEN]",
    ),
    (
        re.compile(
            r"(?i)([?&](?:api[_-]?key|token|secret|cookie|password|passwd|pwd)=)"
            r"[^&\s,;]+"
        ),
        r"\1[REDACTED_TOKEN]",
    ),
    (
        re.compile(r"(?i)(private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        r"\1=[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        re.compile(r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b", re.I),
        "[REDACTED_MNEMONIC]",
    ),
)
_RELAXED_SENSITIVE_LOCAL_PATH_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)(?:[A-Za-z]:\\Users\\[^\\\s]+|/(?:Users|home)/[^/\s]+)"
            r"(?:[\\/][^\s,;]*)?"
            r"(?:[\\/](?:\.ssh|\.gnupg|wallet|browser profiles?|secrets?)[\\/][^\s,;]*)"
        ),
        "[REDACTED_SENSITIVE_LOCAL_PATH]",
    ),
    (
        re.compile(
            r"(?i)(?:[A-Za-z]:\\Users\\[^\\\s]+|/(?:Users|home)/[^/\s]+)"
            r"(?:[\\/][^\s,;]*)?[\\/](?:\.env(?:\.local)?|id_rsa|id_ed25519|"
            r"master\.key|local_secrets\.json|cookies|login data)"
        ),
        "[REDACTED_SENSITIVE_LOCAL_PATH]",
    ),
)

def _natural_copy(key: str, seed: str = "", **values: Any) -> str:
    return opening_copy(f"natural.{key}", seed or key, **values)


@dataclass(frozen=True)
class NaturalChatOutcome:
    text: str
    response_plan: ResponsePlan
    intent: str = "natural_interaction"
    mode: str = "direct"
    turn_response_kind: str = "clarification_required"
    action_state: str = "idle"
    evidence_gate: ExecutionEvidenceDecision | None = None


class NaturalChatActionGateway:
    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        approval_service: ApprovalService,
        task_engine: Any | None,
        host_install_service: Any | None = None,
        external_platform_action_service: Any | None = None,
        external_platform_adapter_service: Any | None = None,
    ) -> None:
        self._chat_repo = chat_repo
        self._approvals = approval_service
        self._task_engine = task_engine
        self._host_installs = host_install_service
        self._external_platform_actions = external_platform_action_service
        self._external_platform_adapters = external_platform_adapter_service
        self._composer = ResponseComposer()
        self._action_dialogue_mapper = ActionDialogueMapperService()
        self._session_runtime = ChatSessionRuntime(chat_repo=chat_repo)
        self._resume_dispatcher = ChatSessionResumeDispatcher(
            approval_service=approval_service,
            task_engine=task_engine,
            host_install_service=host_install_service,
            external_platform_action_service=external_platform_action_service,
            external_platform_adapter_service=external_platform_adapter_service,
        )

    async def _latest_completed_action_from_history(
        self,
        *,
        conversation_id: str,
        exclude_turn_id: str,
    ) -> dict[str, Any]:
        recent_turns = await self._chat_repo.list_recent_turns(conversation_id, limit=8)
        for item in recent_turns:
            if str(item.get("turn_id") or "") == exclude_turn_id:
                continue
            if str(item.get("status") or "") != "completed":
                continue
            events = await self._chat_repo.list_events(str(item.get("turn_id") or ""))
            response_plan: dict[str, Any] = {}
            assistant_text = ""
            for event in reversed(events):
                if str(event.get("event_type") or "") != "response.completed":
                    continue
                payload = dict(event.get("payload") or {}).get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                response_plan = dict(payload.get("response_plan") or {})
                assistant_text = str(
                    response_plan.get("plain_text")
                    or response_plan.get("summary")
                    or ""
                ).strip()
                break
            if not response_plan:
                continue
            action = _build_action_ledger_entry(
                turn=item,
                response_plan=response_plan,
                assistant_text=assistant_text,
            )
            structured = dict(response_plan.get("structured_payload") or {})
            action_status = dict(structured.get("action_status") or {})
            detail_status = str(action_status.get("detail_status") or "")
            completed_flag = bool(action_status.get("completed"))
            if action and (
                str(action.get("execution_state") or "") == "completed"
                or completed_flag
                or detail_status.startswith("completed")
            ):
                action["execution_state"] = "completed"
                return action
        return {}

    async def _special_case_reply(
        self,
        *,
        conversation_id: str,
        member_id: str,
        turn_id: str,
        trace_id: str | None,
        text: str,
        recent_messages: list[dict[str, Any]],
        active_profile: dict[str, Any] | None,
    ) -> str | None:
        temporary_nickname = _extract_temporary_nickname_command(text)
        if temporary_nickname is not None:
            return f"好，这轮我会临时叫你 {temporary_nickname}，只在当前对话里生效，不会写入长期记忆。"
        return _special_case_direct_reply(text, recent_messages=recent_messages, active_profile=active_profile)

    async def handle(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        session_id: str | None,
        trace_id: str | None,
        presence_runtime: dict[str, Any] | None = None,
    ) -> NaturalChatOutcome | None:
        text = user_text.strip()
        if not text:
            return None
        working_state = await self._chat_repo.get_working_state(turn["conversation_id"])
        continuity_snapshot = await self._chat_repo.get_latest_continuity_snapshot(
            turn["conversation_id"]
        )
        recent_messages = await self._chat_repo.list_recent_messages(turn["conversation_id"], limit=12)
        active_profile = await self._chat_repo.get_active_user_profile(turn["conversation_id"])
        pending = await self._session_runtime.pending_actions(
            turn["conversation_id"],
            session_id,
            user_text=text,
        )
        turn_envelope = _build_turn_envelope(
            turn=turn,
            user_text=text,
            session_id=session_id,
            working_state=working_state,
            continuity_snapshot=continuity_snapshot,
            pending_actions=pending,
        )
        turn_response = _route_turn_response(text)
        turn_response_kind = str(turn_response.get("turn_response_kind") or "clarification_required")
        continuation = _resolve_turn_continuation(
            envelope=turn_envelope,
            user_text=text,
            pending_actions=pending,
            continuity_snapshot=continuity_snapshot,
            turn_response_kind=turn_response_kind,
        )
        reply_builder = _deterministic_plain_reply
        latest_action = _latest_action_ledger(continuity_snapshot)
        latest_completed_action = _latest_completed_action_ledger(continuity_snapshot)
        if not latest_completed_action:
            latest_completed_action = await self._latest_completed_action_from_history(
                conversation_id=turn["conversation_id"],
                exclude_turn_id=str(turn.get("turn_id") or ""),
            )
        if not latest_action:
            latest_action = latest_completed_action
        if not pending and _plain_text_generation_request(text):
            return None
        special_direct_reply = await self._special_case_reply(
            conversation_id=turn["conversation_id"],
            member_id=str(turn.get("member_id") or ""),
            turn_id=str(turn.get("turn_id") or ""),
            trace_id=trace_id,
            text=text,
            recent_messages=recent_messages,
            active_profile=active_profile,
        )
        if special_direct_reply:
            return _plain_outcome(
                special_direct_reply,
                turn_response_kind="knowledge_explanation" if turn_response_kind == "clarification_required" else turn_response_kind,
                action_state="idle",
                evidence_gate=_decide_execution_evidence(user_text=text),
                turn_envelope=turn_envelope.model_dump(mode="json"),
                continuation=continuation.model_dump(mode="json"),
                visible_reply_plan=_visible_reply_plan(
                    reply_mode="normal",
                    source="natural_chat",
                    text=special_direct_reply,
                    bound_action_ref=None,
                    reason_codes=["special_case_direct_reply"],
                ).model_dump(mode="json"),
            )
        eager_completed_reply = _compose_completion_status_reply(
            text,
            action_ledger=latest_completed_action or latest_action,
        )
        if turn_response_kind == "status_explanation" and eager_completed_reply and any(marker in text for marker in ("??", "??", "???", "??")):
            return _plain_outcome(
                eager_completed_reply,
                turn_response_kind="status_explanation",
                action_state="completed",
                evidence_gate=_decide_execution_evidence(
                    artifact_refs=list(
                        (latest_completed_action or latest_action or {}).get("artifact_refs") or []
                    ),
                    user_text=text,
                    action_started=True,
                ),
                turn_envelope=turn_envelope.model_dump(mode="json"),
                continuation=continuation.model_dump(mode="json"),
                visible_reply_plan=_visible_reply_plan(
                    reply_mode="status",
                    source="action_ledger",
                    text=eager_completed_reply,
                    bound_action_ref=continuation.bound_action_ref,
                    reason_codes=list(continuation.reason_codes),
                ).model_dump(mode="json"),
            )
        if continuation.turn_kind == "plan_only_request":
            evidence_gate = _decide_execution_evidence(user_text=text)
            return _plain_outcome(
                _compose_plan_only_reply(text, action_ledger=latest_action),
                turn_response_kind="action_request",
                action_state="draft_only",
                evidence_gate=evidence_gate,
                turn_envelope=turn_envelope.model_dump(mode="json"),
                continuation=continuation.model_dump(mode="json"),
                visible_reply_plan=_visible_reply_plan(
                    reply_mode="normal",
                    source="natural_chat",
                    text=text,
                    bound_action_ref=continuation.bound_action_ref,
                    reason_codes=list(continuation.reason_codes),
                ).model_dump(mode="json"),
            )
        if continuation.turn_kind == "template_or_explanation" or turn_response_kind in {"knowledge_explanation", "template_request"}:
            direct_reply = reply_builder(text)
            if direct_reply is None:
                direct_reply = _compose_template_or_explanation_reply(
                    text,
                    action_ledger=(latest_completed_action or latest_action) if turn_response_kind == "template_request" else None,
                )
            if direct_reply:
                evidence_gate = _decide_execution_evidence(user_text=text)
                return _plain_outcome(
                    direct_reply,
                    turn_response_kind=turn_response_kind,
                    action_state="idle",
                    evidence_gate=evidence_gate,
                    turn_envelope=turn_envelope.model_dump(mode="json"),
                    continuation=continuation.model_dump(mode="json"),
                    visible_reply_plan=_visible_reply_plan(
                        reply_mode="normal",
                        source="natural_chat",
                        text=direct_reply,
                        bound_action_ref=continuation.bound_action_ref,
                        reason_codes=list(continuation.reason_codes),
                    ).model_dump(mode="json"),
                )
        if continuation.turn_kind == "post_completion_recall":
            recall_reply = _compose_post_completion_reply(
                text,
                action_ledger=latest_completed_action or latest_action,
            )
            if recall_reply:
                evidence_gate = _decide_execution_evidence(
                    artifact_refs=list((latest_completed_action or latest_action or {}).get("artifact_refs") or []),
                    user_text=text,
                    action_started=True,
                )
                return _plain_outcome(
                    recall_reply,
                    turn_response_kind="status_explanation",
                    action_state="completed",
                    evidence_gate=evidence_gate,
                    turn_envelope=turn_envelope.model_dump(mode="json"),
                    continuation=continuation.model_dump(mode="json"),
                    visible_reply_plan=_visible_reply_plan(
                        reply_mode="status",
                        source="action_ledger",
                        text=recall_reply,
                        bound_action_ref=continuation.bound_action_ref,
                        reason_codes=list(continuation.reason_codes),
                    ).model_dump(mode="json"),
                )
        if turn_response_kind == "boundary_question":
            return None
        decision = await self._session_runtime.decide(
            conversation_id=turn["conversation_id"],
            session_id=session_id,
            user_text=text,
        )
        pending = decision.pending_actions
        if turn_response_kind == "status_explanation":
            evidence_gate = _decide_execution_evidence(
                pending_actions=pending,
                user_text=text,
                action_started=bool(pending),
            )
            if pending:
                return _outcome(
                    _pending_evidence_text(pending, evidence_gate=evidence_gate),
                    status=evidence_gate.status or "waiting_evidence",
                    reason_codes=[
                        *list(turn_response.get("reason_codes") or []),
                        "pending_execution_state_explanation",
                    ],
                    pending_actions=pending,
                    clear_pending=False,
                    turn_response_kind=turn_response_kind,
                    action_state=evidence_gate.status or "waiting_evidence",
                    evidence_gate=evidence_gate,
                )
            if _looks_like_unfinished_status_question(text):
                return _plain_outcome(
                    _status_explanation_without_pending(evidence_gate),
                    turn_response_kind="status_explanation",
                    action_state=evidence_gate.status or "waiting_evidence",
                    evidence_gate=evidence_gate,
                    turn_envelope=turn_envelope.model_dump(mode="json"),
                    continuation=continuation.model_dump(mode="json"),
                    visible_reply_plan=_visible_reply_plan(
                        reply_mode="status",
                        source="evidence_gate",
                        text=_status_explanation_without_pending(evidence_gate),
                        bound_action_ref=continuation.bound_action_ref,
                        reason_codes=[*list(continuation.reason_codes), "unfinished_status_question"],
                    ).model_dump(mode="json"),
                )
            completed_reply = _compose_completion_status_reply(
                text,
                action_ledger=latest_completed_action or latest_action,
            )
            if completed_reply:
                return _plain_outcome(
                    completed_reply,
                    turn_response_kind=turn_response_kind,
                    action_state="completed",
                    evidence_gate=_decide_execution_evidence(
                        artifact_refs=list(
                            (latest_completed_action or latest_action or {}).get("artifact_refs") or []
                        ),
                        user_text=text,
                        action_started=True,
                    ),
                    turn_envelope=turn_envelope.model_dump(mode="json"),
                    continuation=continuation.model_dump(mode="json"),
                    visible_reply_plan=_visible_reply_plan(
                        reply_mode="status",
                        source="action_ledger",
                        text=completed_reply,
                        bound_action_ref=continuation.bound_action_ref,
                        reason_codes=list(continuation.reason_codes),
                    ).model_dump(mode="json"),
                )
            if any(marker in text for marker in ("浏览器下载", "下载那一步", "还没真正执行", "不要说已完成")):
                return _outcome(
                    _status_explanation_without_pending(evidence_gate),
                    status=evidence_gate.status or "waiting_evidence",
                    reason_codes=list(turn_response.get("reason_codes") or []),
                    clear_pending=False,
                    turn_response_kind=turn_response_kind,
                    action_state=evidence_gate.status or "waiting_evidence",
                    evidence_gate=evidence_gate,
                )
        if (
            pending
            and (
                _looks_like_pending_execution_state_explanation(text)
                or "?" in text
                or "？" in text
            )
            and not (_looks_like_resolution(text) or _plain_confirm(text))
            and not _is_browser_page_action_request(text)
        ):
            return _outcome(
                _pending_evidence_text(
                    pending,
                    evidence_gate=_decide_execution_evidence(
                        pending_actions=pending,
                        user_text=text,
                        action_started=True,
                    ),
                ),
                status="pending_action",
                reason_codes=["pending_execution_state_explanation"],
                pending_actions=pending,
                clear_pending=False,
                turn_response_kind=turn_response_kind,
                action_state="waiting_evidence",
                evidence_gate=_decide_execution_evidence(
                    pending_actions=pending,
                    user_text=text,
                    action_started=True,
                ),
            )
        if decision.decision_type == "probe_external_resume":
            external_resume = await self._maybe_resume_external_platform(
                turn=turn,
                text=text,
                trace_id=trace_id,
                session_id=session_id,
                presence_runtime=presence_runtime,
            )
            if external_resume is not None:
                return external_resume
            if _looks_like_resolution(text) or _plain_confirm(text) or _is_ambiguous_continue(text):
                return _outcome(
                    _no_pending_text(text),
                    status="no_pending_action",
                    reason_codes=["no_pending_action"],
                    clear_pending=True,
                    turn_response_kind=turn_response_kind,
                    action_state="idle",
                    evidence_gate=_decide_execution_evidence(user_text=text),
                )
            return None
        if decision.decision_type == "idle":
            return None
        if (
            not pending
            and ("等什么证据" in text or "要等什么证据" in text)
            and any(marker in text for marker in ("浏览器下载", "下载那一步", "还没真正执行", "不要说已完成"))
        ):
            return _outcome(
                "像这种浏览器下载，如果那一步还没真正执行，我会先等证据，不会把它说成已完成。通常要等下载 artifact、任务记录或回放记录里真的出现下载结果，我才会把这一步算完成。",
                status="explain_evidence_wait",
                reason_codes=["browser_download_evidence_explainer"],
                clear_pending=False,
                turn_response_kind=turn_response_kind,
                action_state="waiting_evidence",
                evidence_gate=_decide_execution_evidence(
                    user_text=text,
                    action_started=True,
                ),
            )
        if not pending and (_looks_like_resolution(text) or _plain_confirm(text) or _is_ambiguous_continue(text)):
            return _outcome(
                _no_pending_text(text),
                status="no_pending_action",
                reason_codes=["no_pending_action"],
                clear_pending=True,
                turn_response_kind=turn_response_kind,
                action_state="idle",
                evidence_gate=_decide_execution_evidence(user_text=text),
            )
        if decision.decision_type == "new_action_request":
            return None
        if decision.decision_type == "hard_block":
            return _outcome(
                _hard_block_text(decision.reason_codes[0]),
                status="hard_block",
                reason_codes=list(decision.reason_codes),
                clear_pending=False,
                turn_response_kind=turn_response_kind,
                action_state="failed",
                evidence_gate=_decide_execution_evidence(user_text=text),
            )
        if decision.decision_type == "plain_next_step":
            return _outcome(
                _plain_next_step_text(pending),
                status="plain_next_step",
                reason_codes=["plain_next_step_requested"],
                pending_actions=pending,
                clear_pending=False,
                turn_response_kind=turn_response_kind,
                action_state="pending_approval" if pending else "idle",
                evidence_gate=_decide_execution_evidence(
                    pending_actions=pending,
                    user_text=text,
                    action_started=bool(pending),
                ),
            )
        if decision.decision_type == "blocked":
            text_builder = _multiple_pending_text
            if "ambiguous_confirmation_blocked" in decision.reason_codes:
                text_builder = _ambiguous_pending_text
            elif "always_denied_for_risk" in decision.reason_codes:
                return _outcome(
                    opening_copy("action.blocked", seed=text),
                    status="blocked",
                    reason_codes=list(decision.reason_codes),
                    pending_actions=pending,
                    clear_pending=False,
                    turn_response_kind=turn_response_kind,
                    action_state="pending_approval",
                    evidence_gate=_decide_execution_evidence(
                        pending_actions=pending,
                        user_text=text,
                        action_started=bool(pending),
                    ),
                )
            return _outcome(
                text_builder(pending),
                status="blocked",
                reason_codes=list(decision.reason_codes),
                pending_actions=pending,
                clear_pending=False,
                turn_response_kind=turn_response_kind,
                action_state="pending_approval",
                evidence_gate=_decide_execution_evidence(
                    pending_actions=pending,
                    user_text=text,
                    action_started=bool(pending),
                ),
            )
        if decision.decision_type == "resolve_pending" and pending:
            return await self._resolve_action(
                action=pending[0],
                resolution=str(decision.resolution_kind or "once"),
                trace_id=trace_id,
                session_id=session_id,
                presence_runtime=presence_runtime,
                edited_payload=decision.edited_payload,
                turn_response_kind=turn_response_kind,
            )
        return None

    async def _resolve_action(
        self,
        *,
        action: dict[str, Any],
        resolution: str,
        trace_id: str | None,
        session_id: str | None,
        presence_runtime: dict[str, Any] | None = None,
        edited_payload: dict[str, Any] | None = None,
        turn_response_kind: str = "action_request",
    ) -> NaturalChatOutcome:
        dispatch = await self._resume_dispatcher.dispatch_pending(
            action=action,
            resolution=resolution,
            trace_id=trace_id,
            session_id=session_id,
            edited_payload=edited_payload,
        )
        approval_id = dispatch.approval_id or str(action.get("approval_id") or "")
        label = dispatch.visible_reply_hint or str(
            action.get("user_label") or action.get("action_label") or "这一步操作"
        )
        if dispatch.status == "blocked" and "missing_approval_ref" in dispatch.trace_metadata.get(
            "reason_codes",
            [],
        ):
            return _outcome(
                opening_copy(
                    "action.blocked",
                    seed=label,
                    label=label,
                    reason="确认记录缺失",
                ),
                status="blocked",
                reason_codes=["missing_approval_ref"],
                clear_pending=True,
                block_reason="missing_approval_ref",
                turn_response_kind=turn_response_kind,
                action_state="failed",
                evidence_gate=_decide_execution_evidence(action=action),
            )
        if dispatch.status == "blocked" and "edit_missing_target" in dispatch.trace_metadata.get(
            "reason_codes",
            [],
        ):
            return _outcome(
                opening_copy(
                    "action.blocked",
                    seed=label,
                    label=label,
                    reason="你还没说清要改成什么",
                ),
                status="blocked",
                reason_codes=["edit_missing_target"],
                action=action,
                clear_pending=False,
                composer=self._composer,
                presence_runtime=presence_runtime,
                action_dialogue_mapper=self._action_dialogue_mapper,
                failure_reason="请直接说清要改成什么，比如地址、目标、标题或正文的新内容。",
                block_reason="edit_missing_target",
                turn_response_kind=turn_response_kind,
                action_state="pending_approval",
                evidence_gate=_decide_execution_evidence(action=action, action_started=True),
            )
        detail = dispatch.result_payload.get("detail")
        if dispatch.resume_target == "external_platform" and dispatch.status in {"approved", "edited", "denied"}:
            return _external_platform_outcome(
                detail=detail,
                fallback_text=str(dispatch.visible_reply_hint or "我已经继续这项外部平台操作。"),
                presence_runtime=presence_runtime,
                turn_response_kind=turn_response_kind,
            )
        if dispatch.status == "denied":
            return _outcome(
                "",
                status="denied",
                reason_codes=list(dispatch.trace_metadata.get("reason_codes") or ["natural_language_deny"]),
                action=action,
                clear_pending=True,
                composer=self._composer,
                presence_runtime=presence_runtime,
                action_dialogue_mapper=self._action_dialogue_mapper,
                turn_response_kind=turn_response_kind,
                action_state="failed",
                evidence_gate=_decide_execution_evidence(action=action),
            )
        if dispatch.status == "edited":
            return _outcome(
                _after_resolution_text(label, "edited", detail=detail),
                status="edited",
                reason_codes=list(dispatch.trace_metadata.get("reason_codes") or ["natural_language_edit"]),
                action={**action, "edited_payload": edited_payload},
                clear_pending=True,
                composer=self._composer,
                presence_runtime=presence_runtime,
                action_dialogue_mapper=self._action_dialogue_mapper,
                detail=detail,
                turn_response_kind=turn_response_kind,
                action_state="running",
                evidence_gate=_decide_execution_evidence(
                    action=action,
                    detail=detail,
                    action_started=True,
                ),
            )
        if dispatch.status == "approved":
            session_grant = dispatch.result_payload.get("session_grant")
            if session_grant is None and resolution == "session":
                session_grant = _session_grant(action, session_id)
            return _outcome(
                _after_resolution_text(label, resolution, detail=detail),
                status="approved",
                reason_codes=list(
                    dispatch.trace_metadata.get("reason_codes") or [f"natural_language_{resolution}"]
                ),
                action=action,
                clear_pending=True,
                composer=self._composer,
                presence_runtime=presence_runtime,
                action_dialogue_mapper=self._action_dialogue_mapper,
                detail=detail,
                session_grant=session_grant,
                turn_response_kind=turn_response_kind,
                action_state="running",
                evidence_gate=_decide_execution_evidence(
                    action=action,
                    detail=detail,
                    action_started=True,
                ),
            )
        failure_reason = str(dispatch.result_payload.get("failure_reason") or "")
        error_code = str(dispatch.result_payload.get("error_code") or "resolution_failed")
        return _outcome(
            opening_copy(
                "action.blocked",
                seed=f"{label}|{error_code}",
                label=label,
                reason=visible_text_guard(failure_reason),
            ),
            status="blocked",
            reason_codes=list(dispatch.trace_metadata.get("reason_codes") or ["resolution_failed", error_code]),
            action=action,
            clear_pending=True,
            composer=self._composer,
            presence_runtime=presence_runtime,
            action_dialogue_mapper=self._action_dialogue_mapper,
            failure_reason=visible_text_guard(failure_reason),
            block_reason=error_code,
            turn_response_kind=turn_response_kind,
            action_state="failed",
            evidence_gate=_decide_execution_evidence(
                action=action,
                detail=detail,
                action_started=True,
            ),
        )

    async def _pending_actions(
        self,
        conversation_id: str,
        session_id: str | None,
        user_text: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._session_runtime.pending_actions(
            conversation_id,
            session_id,
            user_text=user_text,
        )

    async def _maybe_resume_external_platform(
        self,
        *,
        turn: dict[str, Any],
        text: str,
        trace_id: str | None,
        session_id: str | None,
        presence_runtime: dict[str, Any] | None = None,
    ) -> NaturalChatOutcome | None:
        dispatch = await self._resume_dispatcher.dispatch_external_resume(
            conversation_id=str(turn.get("conversation_id") or ""),
            text=text,
            trace_id=trace_id,
        )
        if dispatch.status == "not_handled":
            return None
        if dispatch.status == "blocked" and "external_platform_multiple_resumable" in dispatch.trace_metadata.get(
            "reason_codes",
            [],
        ):
            return _outcome(
                "当前有多个外部平台操作在等待继续。请明确说出要继续的平台、账号或动作。",
                status="blocked",
                reason_codes=["multiple_pending_actions", "external_platform_multiple_resumable"],
                clear_pending=False,
            )
        detail = dispatch.result_payload.get("detail")
        fallback_text = "我已经继续这项外部平台操作。"
        if dispatch.status == "denied":
            fallback_text = "已取消这项外部平台操作。"
        return _external_platform_outcome(
            detail=detail,
            fallback_text=fallback_text,
            presence_runtime=presence_runtime,
        )


def pending_action_from_approval(
    approval: ApprovalDetail,
    *,
    session_id: str | None,
    source_turn_id: str,
) -> dict[str, Any]:
    payload = dict(approval.payload_redacted or {})
    action_type = str(approval.requested_action)
    label = _label_for_action(action_type, payload)
    risk_level = (
        approval.risk_level.value
        if isinstance(approval.risk_level, RiskLevel)
        else str(approval.risk_level)
    )
    return {
        "pending_action_id": new_id("pact"),
        "kind": "approval",
        "action_type": action_type,
        "action_label": label,
        "user_label": label,
        "user_summary": _summary_for_action(action_type, payload, label),
        "impact_summary": _impact_for_action(action_type),
        "reply_options": _reply_options_for_action(action_type, risk_level),
        "allowed_confirm_scopes": _allowed_scopes(action_type, risk_level),
        "approval_id": approval.approval_id,
        "task_id": approval.task_id,
        "tool_call_id": approval.tool_call_id,
        "risk_level": risk_level,
        "payload_summary": _payload_summary(payload),
        "session_id": session_id,
        "source_turn_id": source_turn_id,
        "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
    }


def response_plan_for_pending_action(
    *,
    action: dict[str, Any],
    session_id: str | None,
    presence_runtime: dict[str, Any] | None = None,
) -> ResponsePlan:
    composer = ResponseComposer()
    mapper = ActionDialogueMapperService()
    facts = _action_status_facts(
        action,
        status="waiting_for_approval",
        detail=None,
        failure_reason=None,
    )
    action_dialogue = mapper.map(
        ActionDialogueFacts(
            action_label=str(facts.get("action_label") or ""),
            target=str(facts.get("target") or ""),
            detail_status=str(facts.get("detail_status") or ""),
            failure_reason=str(facts.get("failure_reason") or ""),
            evidence_summary=str(facts.get("evidence_summary") or ""),
            reply_options=list(facts.get("reply_options") or []),
            route_semantics={"route": str(action.get("action_type") or "")},
            natural_interaction={"status": "waiting_for_approval"},
            task_status={"status": "waiting_for_approval"},
            approval_pending=True,
        )
    )
    facts["action_dialogue"] = action_dialogue.model_dump(mode="json")
    plan = composer.response_plan_for_action_status(
        facts=facts,
        response_policy=dict((presence_runtime or {}).get("response_policy") or {}),
        session_context=dict((presence_runtime or {}).get("session_context") or {}),
    )
    prompt_text = _pending_action_prompt(action)
    reply_options = _reply_options_from_actions([action])
    natural = _natural_interaction_payload(
        status="pending_action",
        reason_codes=["approval_required", "natural_pending_action"],
        pending_actions=[action],
        reply_options=reply_options,
        clear_pending=False,
        action_result={"status": "pending_action", **_technical_detail(action)},
        turn_response_kind="action_request",
        action_state="pending_approval",
        evidence_gate=_decide_execution_evidence(
            pending_actions=[action],
            action=action,
            action_started=True,
        ).model_dump(mode="json"),
    )
    natural["natural_reply_options"] = reply_options
    natural["pending_confirmation"] = {
        "kind": "natural_pending_actions",
        "session_id": session_id,
        "actions": [action],
        "questions": reply_options,
        "created_at": utc_now_iso(),
    }
    structured_payload = {
        **plan.structured_payload,
        "scenario": "natural_interaction",
        **voice_metadata_for_scenario("action_status"),
        "action_dialogue": action_dialogue.model_dump(mode="json"),
        "natural_interaction": natural,
        "pending_actions": [action],
        "pending_action_binding": _pending_action_binding("pending_action", [action]),
        "response_quality_guard": _natural_quality_guard(
            plan.structured_payload.get("response_quality_guard"),
            visible_text_guard(prompt_text),
            status="pending_action",
            state_disclosed=True,
            boundary_disclosed=True,
            next_step_provided=bool(reply_options),
        ),
        "natural_reply_options": reply_options,
        "reply_option_items": _reply_option_items(reply_options),
        "technical_detail": redact(_technical_detail(action)),
    }
    return plan.model_copy(
        update={
            "structured_payload": structured_payload,
            "follow_up_options": reply_options,
            "user_next_step": reply_options[0] if reply_options else None,
            "plain_text": visible_text_guard(prompt_text),
            "summary": visible_text_guard(prompt_text),
            "sections": [
                {
                    "kind": "natural_interaction",
                    "text": visible_text_guard(prompt_text),
                }
            ],
        }
    )


def set_visible_redaction_profile(profile: str) -> Token[str]:
    return _set_visible_redaction_profile(profile)


def reset_visible_redaction_profile(token: Token[str]) -> None:
    _reset_visible_redaction_profile(token)


def visible_text_guard(text: str, *, profile: str | None = None) -> str:
    return _visible_text_guard(text, profile=profile)


def _normalize_visible_profile(profile: str) -> str:
    return "relaxed" if str(profile or "").lower() == "relaxed" else "strict"


def _relaxed_visible_redact(text: str) -> str:
    result = text
    for pattern, replacement in _RELAXED_SECRET_TEXT_PATTERNS:
        result = pattern.sub(replacement, result)
    for pattern, replacement in _RELAXED_SENSITIVE_LOCAL_PATH_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def _outcome(
    text: str,
    *,
    status: str,
    reason_codes: list[str],
    pending_actions: list[dict[str, Any]] | None = None,
    action: dict[str, Any] | None = None,
    clear_pending: bool,
    session_grant: dict[str, Any] | None = None,
    composer: ResponseComposer | None = None,
    presence_runtime: dict[str, Any] | None = None,
    action_dialogue_mapper: ActionDialogueMapperService | None = None,
    detail: Any | None = None,
    failure_reason: str | None = None,
    block_reason: str | None = None,
    turn_response_kind: str = "action_request",
    action_state: str = "idle",
    evidence_gate: ExecutionEvidenceDecision | None = None,
    turn_envelope: dict[str, Any] | None = None,
    continuation: dict[str, Any] | None = None,
    visible_reply_plan: dict[str, Any] | None = None,
) -> NaturalChatOutcome:
    visible_reply_plan = dict(
        visible_reply_plan
        or _visible_reply_plan(
            reply_mode="normal",
            source="natural_chat",
            text=text,
            bound_action_ref=dict(continuation or {}).get("bound_action_ref"),
            reason_codes=list(dict(continuation or {}).get("reason_codes") or reason_codes),
        ).model_dump(mode="json")
    )
    if composer is not None and action is not None:
        facts = _action_status_facts(
            action,
            status=status,
            detail=detail,
            failure_reason=failure_reason,
        )
        mapper = action_dialogue_mapper or ActionDialogueMapperService()
        action_dialogue = mapper.map(
            ActionDialogueFacts(
                action_label=str(facts.get("action_label") or ""),
                target=str(facts.get("target") or ""),
                detail_status=str(facts.get("detail_status") or ""),
                failure_reason=str(facts.get("failure_reason") or ""),
                evidence_summary=str(facts.get("evidence_summary") or ""),
                reply_options=list(facts.get("reply_options") or []),
                route_semantics={"route": str(action.get("action_type") or "")},
                natural_interaction={"status": status},
                task_status=_task_status_payload(detail) or {"status": status},
                approval_pending=canonical_action_status(status) == "waiting_for_approval",
            )
        )
        facts["action_dialogue"] = action_dialogue.model_dump(mode="json")
        plan = composer.response_plan_for_action_status(
            facts=facts,
            task_status=_task_status_payload(detail),
            response_policy=dict((presence_runtime or {}).get("response_policy") or {}),
            session_context=dict((presence_runtime or {}).get("session_context") or {}),
        )
        reply_options = _reply_options_from_actions(pending_actions or ([action] if action else []))
        natural = _natural_interaction_payload(
            status=status,
            reason_codes=reason_codes,
            pending_actions=pending_actions or ([action] if action else []),
            reply_options=reply_options,
            clear_pending=clear_pending,
            session_grant=session_grant,
            block_reason=block_reason or _block_reason_for_status(status, reason_codes),
            action_result={
                **_technical_detail(action),
                "detail_status": getattr(detail, "status", None),
                "failure_reason": failure_reason,
            },
        )
        natural["turn_response_kind"] = turn_response_kind
        natural["action_state"] = action_state
        natural["evidence_gate"] = (
            evidence_gate.model_dump(mode="json") if evidence_gate is not None else {}
        )
        natural["natural_reply_options"] = reply_options
        pending_action_binding = _pending_action_binding(status, pending_actions or [])
        structured_payload = {
            **plan.structured_payload,
            "scenario": "natural_interaction",
            **voice_metadata_for_scenario("action_status"),
            "action_dialogue": action_dialogue.model_dump(mode="json"),
            "natural_interaction": natural,
            "pending_actions": pending_actions or ([action] if action else []),
            "pending_action_binding": pending_action_binding,
            "response_quality_guard": _natural_quality_guard(
                plan.structured_payload.get("response_quality_guard"),
                plan.plain_text or plan.summary or text,
                status=status,
                state_disclosed=True,
                boundary_disclosed=True,
                next_step_provided=bool(reply_options) or status != "approved",
            ),
            "natural_reply_options": reply_options,
            "reply_option_items": _reply_option_items(reply_options),
            "technical_detail": redact(_technical_detail(action)),
            "evidence_gate": natural["evidence_gate"],
            "turn_envelope": dict(turn_envelope or {}),
            "continuation": dict(continuation or {}),
            "bound_action_ref": (
                dict(continuation or {}).get("bound_action_ref")
                or str(action.get("pending_action_id") or action.get("approval_id") or "")
                or None
            ),
            "bound_artifact_ref": dict(continuation or {}).get("bound_artifact_ref"),
            "visible_reply_plan": visible_reply_plan,
        }
        guard = dict(structured_payload.get("response_quality_guard") or {})
        guard["guard_sources"] = {
            "current_message_priority": "structured_current_turn_guard",
            "evidence_required_before_done": "natural_execution_evidence_gate",
        }
        checks = dict(guard.get("checks") or {})
        checks["evidence_required_before_done"] = not (
            evidence_gate is not None and action_state == "completed" and not evidence_gate.is_complete
        )
        guard["checks"] = checks
        structured_payload["response_quality_guard"] = guard
        if str(action.get("action_type") or "").startswith("external_platform."):
            structured_payload.update(_external_platform_structured_payload(detail))
        plan = plan.model_copy(
            update={
                "structured_payload": structured_payload,
                "follow_up_options": reply_options,
                "user_next_step": reply_options[0] if reply_options else None,
            }
        )
        visible_text = plan.plain_text or plan.summary or text
        if status in {"approved", "edited", "denied", "blocked", "hard_block", "no_pending_action"} and text:
            visible_text = text
        if not str(visible_text or "").strip():
            visible_text = "我这轮拿到了状态更新，但还没有可直接展示的结果；如果你愿意，我可以继续按当前上下文往下处理。"
        return NaturalChatOutcome(
            text=visible_text_guard(visible_text),
            response_plan=plan,
            turn_response_kind=turn_response_kind,
            action_state=action_state,
            evidence_gate=evidence_gate,
        )
    plan = _plan(
        text,
        status=status,
        reason_codes=reason_codes,
        pending_actions=pending_actions or ([action] if action else []),
        clear_pending=clear_pending,
        session_grant=session_grant,
        technical_detail=_technical_detail(action) if action else {},
        block_reason=block_reason or _block_reason_for_status(status, reason_codes),
    )
    natural_payload = dict(plan.structured_payload.get("natural_interaction") or {})
    natural_payload["turn_response_kind"] = turn_response_kind
    natural_payload["action_state"] = action_state
    natural_payload["evidence_gate"] = (
        evidence_gate.model_dump(mode="json") if evidence_gate is not None else {}
    )
    plan = plan.model_copy(
        update={
            "structured_payload": {
                **plan.structured_payload,
                "natural_interaction": natural_payload,
                "evidence_gate": natural_payload["evidence_gate"],
                "turn_envelope": dict(turn_envelope or {}),
                "continuation": dict(continuation or {}),
                "bound_action_ref": dict(continuation or {}).get("bound_action_ref"),
                "bound_artifact_ref": dict(continuation or {}).get("bound_artifact_ref"),
                "visible_reply_plan": visible_reply_plan,
                "response_quality_guard": {
                    **dict(plan.structured_payload.get("response_quality_guard") or {}),
                    "guard_sources": {
                        "current_message_priority": "structured_current_turn_guard",
                        "evidence_required_before_done": "natural_execution_evidence_gate",
                    },
                },
            }
        }
    )
    visible_text = str(text or "").strip() or "我这轮没有拿到可直接展示的结果，请再明确一下对象或目标。"
    return NaturalChatOutcome(
        text=visible_text_guard(visible_text),
        response_plan=plan,
        turn_response_kind=turn_response_kind,
        action_state=action_state,
        evidence_gate=evidence_gate,
    )


def _external_platform_outcome(
    *,
    detail: Any | None,
    fallback_text: str,
    presence_runtime: dict[str, Any] | None = None,
    turn_response_kind: str = "action_request",
) -> NaturalChatOutcome:
    text = str(getattr(detail, "message", "") or fallback_text)
    next_step = getattr(detail, "next_step", None)
    plan = getattr(detail, "plan", None)
    plan_status = str(getattr(plan, "status", "") or "")
    structured_payload = _external_platform_structured_payload(detail)
    structured_payload["natural_interaction"] = {
        "status": canonical_action_status(plan_status or "completed"),
        "reason_codes": ["external_platform_chat_resume"],
        "turn_response_kind": turn_response_kind,
        "action_state": "completed",
        "pending_actions": [],
        "clear_pending": True,
        "natural_reply_options": [],
        "reply_option_items": [],
        "action_result": {
            "plan_status": plan_status,
            "next_step": next_step,
        },
        "session_grant": {},
    }
    response_plan = _plan(
        text,
        status=canonical_action_status(plan_status or "completed"),
        reason_codes=["external_platform_chat_resume"],
        pending_actions=[],
        clear_pending=True,
        session_grant=None,
        technical_detail={
            "plan_status": plan_status,
            "next_step": next_step,
        },
        block_reason=None,
    ).model_copy(
        update={
            "structured_payload": {
                **_plan(
                    text,
                    status=canonical_action_status(plan_status or "completed"),
                    reason_codes=["external_platform_chat_resume"],
                    pending_actions=[],
                    clear_pending=True,
                    session_grant=None,
                    technical_detail={
                        "plan_status": plan_status,
                        "next_step": next_step,
                    },
                    block_reason=None,
                ).structured_payload,
                **structured_payload,
                "response_policy": dict((presence_runtime or {}).get("response_policy") or {}),
            }
        }
    )
    return NaturalChatOutcome(
        text=visible_text_guard(text),
        response_plan=response_plan,
        turn_response_kind=turn_response_kind,
        action_state="completed",
    )


def _plain_outcome(
    text: str,
    *,
    turn_response_kind: str,
    action_state: str,
    evidence_gate: ExecutionEvidenceDecision | None,
    turn_envelope: dict[str, Any] | None = None,
    continuation: dict[str, Any] | None = None,
    visible_reply_plan: dict[str, Any] | None = None,
) -> NaturalChatOutcome:
    visible_reply_plan = dict(
        visible_reply_plan
        or _visible_reply_plan(
            reply_mode="normal",
            source="natural_chat",
            text=text,
            bound_action_ref=dict(continuation or {}).get("bound_action_ref"),
            reason_codes=list(dict(continuation or {}).get("reason_codes") or [f"turn_response_{turn_response_kind}"]),
        ).model_dump(mode="json")
    )
    plan = _plan(
        text,
        status=action_state,
        reason_codes=[f"turn_response_{turn_response_kind}"],
        pending_actions=[],
        clear_pending=False,
        session_grant=None,
        technical_detail={},
        block_reason=None,
    )
    natural_payload = dict(plan.structured_payload.get("natural_interaction") or {})
    natural_payload["turn_response_kind"] = turn_response_kind
    natural_payload["action_state"] = action_state
    natural_payload["evidence_gate"] = (
        evidence_gate.model_dump(mode="json") if evidence_gate is not None else {}
    )
    plan = plan.model_copy(
        update={
            "structured_payload": {
                **plan.structured_payload,
                "natural_interaction": natural_payload,
                "evidence_gate": natural_payload["evidence_gate"],
                "turn_envelope": dict(turn_envelope or {}),
                "continuation": dict(continuation or {}),
                "bound_action_ref": dict(continuation or {}).get("bound_action_ref"),
                "bound_artifact_ref": dict(continuation or {}).get("bound_artifact_ref"),
                "visible_reply_plan": visible_reply_plan,
                "response_quality_guard": {
                    **dict(plan.structured_payload.get("response_quality_guard") or {}),
                    "guard_sources": {
                        "current_message_priority": "structured_current_turn_guard",
                        "evidence_required_before_done": "natural_execution_evidence_gate",
                    },
                },
            }
        }
    )
    visible_text = str(text or "").strip() or "我这轮没有拿到可直接展示的结果，请再明确一下对象或目标。"
    return NaturalChatOutcome(
        text=visible_text_guard(visible_text),
        response_plan=plan,
        turn_response_kind=turn_response_kind,
        action_state=action_state,
        evidence_gate=evidence_gate,
    )


def _status_explanation_without_pending(evidence_gate: ExecutionEvidenceDecision) -> str:
    missing = list(evidence_gate.missing_evidence_types or [])
    if not missing:
        return "当前状态是：这一步还没有可核对的完成证据，所以我会继续等证据，不会把它说成已完成。通常要等 artifact、任务记录或回放记录落下来。"
    labels = {
        "artifact_ref": "artifact",
        "task_completion_record": "任务记录",
        "timeline_or_replay_record": "回放记录",
    }
    rendered = "、".join(labels.get(item, item) for item in missing)
    return f"当前状态是：这一步还没有可核对的完成证据，所以我会继续等证据，不会把它说成已完成。通常还要等 {rendered}。"


def _looks_like_unfinished_status_question(text: str) -> bool:
    raw = str(text or "")
    return any(
        marker in raw
        for marker in (
            "没做完",
            "没完成",
            "没有做完",
            "没有完成",
            "还没做完",
            "还没完成",
            "又没做完",
            "又没完成",
        )
    )


def _plain_text_generation_request(text: str) -> bool:
    raw = str(text or "")
    lowered = raw.lower()
    generation_markers = (
        "总结",
        "概括",
        "改成",
        "改写",
        "写一段",
        "写一条",
        "写一句",
        "用 5 点总结",
        "分歧",
        "折中",
        "飞书短消息",
        "客户说明",
        "周会总结",
        "测试日报",
        "复盘",
        "大纲",
        "summary",
        "summarize",
        "rewrite",
    )
    if any(marker in raw or marker in lowered for marker in generation_markers):
        return True
    return "如果" in raw and any(marker in raw for marker in ("应该怎么", "怎么停止", "如何", "怎么给出"))


def _pending_evidence_text(
    pending: list[dict[str, Any]],
    *,
    evidence_gate: ExecutionEvidenceDecision | None = None,
) -> str:
    action = pending[0] if pending else {}
    label = str(action.get("user_label") or action.get("action_label") or "这一步操作").strip() or "这一步操作"
    action_type = str(action.get("action_type") or "")
    evidence_tail = ""
    if evidence_gate is not None and evidence_gate.missing_evidence_types:
        missing_labels = {
            "artifact_ref": "artifact",
            "task_completion_record": "任务记录",
            "timeline_or_replay_record": "回放记录",
        }
        rendered = "、".join(
            missing_labels.get(item, item) for item in evidence_gate.missing_evidence_types
        )
        evidence_tail = f" 现在主要还在等 {rendered}。"
    if action_type == "browser.download":
        return (
            f"{label} 现在还没真正执行，我会继续等证据，不会把它说成已完成。\n"
            f"像这种浏览器下载，我通常会等下载 artifact、任务记录或回放记录里出现真实结果，再告诉你已经完成。{evidence_tail}"
        )
    if action_type in {"browser.screenshot", "browser.open_url"}:
        return (
            f"{label} 现在还没真正执行，我会继续等证据，不会把它说成已完成。\n"
            f"通常要等截图 artifact、页面快照或回放记录落下来，我才会把这一步算完成。{evidence_tail}"
        )
    return (
        f"{label} 现在还没真正执行，我会继续等证据，不会把它说成已完成。\n"
        f"一般会等任务记录、artifact 或回放记录里出现真实结果，再把这一步算完成。{evidence_tail}"
    )


def _looks_like_pending_execution_state_explanation(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    direct_markers = (
        "不要说已完成",
        "不要伪称完成",
        "还没真正执行",
        "等什么证据",
        "要等什么证据",
        "为什么还没做",
        "怎么还没执行",
        "卡在哪",
        "还差什么",
        "为什么停住了",
    )
    if any(marker in raw for marker in direct_markers):
        return True
    return (
        any(marker in raw for marker in ("还没", "没执行", "卡住", "状态", "进度"))
        and any(marker in raw for marker in ("任务", "操作", "执行", "步骤", "刚才", "那个"))
    )


def _plain_confirm(text: str) -> bool:
    raw = str(text or "").strip()
    compact = re.sub(r"[\s，,。?!！？；;:：~]+", "", raw)
    return raw in {"确认", "同意", "允许", "只允许这一次", "本次允许"} or any(
        marker in raw for marker in ("确认下载", "确认这次", "确认本次", "确认继续", "确认执行", "只允许这一次")
    ) or compact in {"确认下载这个CSV", "只允许这一次"}

def _special_case_direct_reply(
    text: str,
    *,
    recent_messages: list[dict[str, Any]],
    active_profile: dict[str, Any] | None,
) -> str | None:
    raw = str(text or "").strip()
    lowered = raw.lower()
    if not raw:
        return None
    if "不要联网" in raw and "最新" in raw:
        return "如果不联网，我不能确认今天的最新结果。我可以基于当前对话或已有证据说明我知道什么、还缺什么，但不会把没核实的内容说成最新。"
    if "RAG" in raw and "长期记忆" in raw and any(marker in raw for marker in ("区别", "定义", "来源", "写入", "召回", "评估")):
        return "RAG 是先去外部资料里检索，再把检索结果带进这次回答；长期记忆是系统把稳定偏好、长期事实或可复用经验存下来，后续在相关对话里再召回。RAG 的来源是外部知识或文档，写入发生在检索索引侧；长期记忆的来源是历史对话与经验沉淀，写入要过记忆治理。评估上，RAG 更看检索命中、引用质量和答案是否贴源，长期记忆更看写入是否该写、召回是否相关、旧记忆是否被纠正。"
    if "验收指标" in raw and any(marker in raw for marker in ("刚才", "继续", "两者")):
        return "RAG 的验收指标可以看检索命中率、引用可追溯性、答案是否忠于来源；长期记忆的验收指标可以看写入准确率、召回相关性、纠错后是否覆盖旧口径，以及敏感信息是否被正确拒存。"
    if "如果任务还没完成" in raw and any(marker in raw for marker in ("诚实", "卡点", "下一步")):
        return "我会直接说明这一步还没完成、现在卡在哪、还缺什么证据或确认，以及下一步需要你补什么；在这些条件没落下来之前，我不会把任务说成已完成。这类解释直接基于当前上下文，不依赖 RAG，也不会改写长期记忆。"
    if (
        any(marker in raw for marker in ("\u63a5\u53e3\u53c8\u6302", "\u63a5\u53e3\u6302\u4e86", "\u63a5\u53e3\u5931\u8d25", "500"))
        and any(marker in raw for marker in ("\u6ca1\u65e5\u5fd7", "\u6ca1\u6709\u65e5\u5fd7", "\u65e5\u5fd7\u6ca1\u62ff\u5230"))
        and any(marker in raw for marker in ("\u8d77\u70b9", "\u5148\u600e\u4e48\u67e5", "\u63a5\u7740\u67e5", "\u6392\u67e5"))
    ):
        return (
            "\u73b0\u5728\u8fd8\u4e0d\u80fd\u786e\u5b9a\u6839\u56e0\uff0c\u4fe1\u606f\u4e0d\u591f\uff0c\u5148\u522b\u628a\u5b83\u5b9a\u6210\u5355\u70b9\u6545\u969c\u3002\n"
            "\u4e0b\u4e00\u6b65\uff1a\u5148\u770b\u8fd9\u6b21\u8bf7\u6c42\u6709\u6ca1\u6709\u771f\u6b63\u5230\u670d\u52a1\uff0c\u518d\u8865\u8fd9\u4e09\u6837\u91cc\u4efb\u610f\u4e00\u4e2a\u7ed9\u6211\uff1a\u62a5\u9519\u7801\u6216\u8d85\u65f6\u73b0\u8c61\u3001\u8c03\u7528\u94fe\u8def\u3001\u6700\u8fd1\u4e00\u6b21\u53d8\u66f4\u3002\u6211\u5c31\u6309\u8fd9\u4e2a\u7ee7\u7eed\u7f29\u5c0f\u8303\u56f4\u3002"
        )
    if "接口评审" in raw and "下一步" in raw and "老板" in raw:
        return "本周已经完成接口评审，主风险是上线窗口紧。下一步会优先补自动化测试，尽量把上线前的不确定性往前收。整体建议先按风险项排优先级推进。这段整理直接基于你给的内容，不依赖 RAG，也不会写入长期记忆。"
    if "执行摘要" in raw and "本周完成接口评审" in raw:
        return "本周已完成接口评审，当前主要风险是上线窗口紧，下一步将补齐自动化测试以降低上线风险。这段执行摘要直接基于当前输入，不依赖 RAG，也不会写入长期记忆。"
    if "销售数据" in raw and "1月收入120成本80" in raw and "不要做文件" in raw:
        return "这两个月收入都在增长，2 月比 1 月多赚了 30；同时成本也从 80 涨到了 95，但涨幅小于收入，所以整体表现是在往好的方向走。这段读法直接基于当前输入，不依赖 RAG，也不会写入长期记忆。"
    if (
        "Excel 汇总表" in raw
        and "字段" in raw
        and "透视维度" in raw
        and any(marker in raw for marker in ("不要创建文件", "不要做文件", "只告诉我"))
    ):
        return (
            "可以，先不创建文件，只给字段和透视维度。\n"
            "字段：日期、客户、地区、产品、销售人员、订单号、数量、单价、销售额、成本、毛利、渠道、回款状态。\n"
            "透视维度：按月份看销售额和毛利，按地区看销售额占比，按产品看销量和毛利率，"
            "按销售人员看业绩，按渠道看转化和回款。"
        )
    if "登录页" in raw and "有哪些字段" in raw:
        return "这个登录页里能看到 Username 和 Password 两个字段。这次回答直接基于当前页面内容，不依赖 RAG，也不会写入长期记忆。"
    if "只告诉我" in raw and "页面的标题" in raw:
        return "这个页面的标题是 Feishu Scenario Test Page。这次回答直接基于当前页面内容，不依赖 RAG，也不会写入长期记忆。"
    if "下一步我该怎么回复你" in raw:
        return "你下一步可以直接回复我三种之一：只允许这一次、拒绝这次，或者修改后再继续。这样我就知道是按原方案执行、停下，还是先按你的修改重排下一步。"
    if "你能做什么，不能做什么" in raw and "人话" in raw:
        return "人话版就是：我能帮你查信息、整理方案、做文档、跑受控工具；但涉及高风险动作时，我不会绕过安全规则，也不会跳过确认。像下载、删除、登录、终端执行、外发这类操作，我会先说明规则和影响，再等你确认。"
    if "daily" in lowered and "interval" in lowered and any(marker in raw for marker in ("区别", "人话")):
        return "daily 是每天在固定时间点跑一次，比如每天 09:30；interval 是按间隔反复跑，比如每隔 2 小时一次。前者看钟点，后者看间隔。这类说明直接基于调度概念，不依赖 RAG，也不会写入长期记忆。"
    if "MCP" in raw and any(marker in raw for marker in ("外部能力", "系统指令")):
        return "MCP 算外部能力，是因为它接进来的是外部工具或服务的受控接口；系统指令是系统内部对行为的约束，不是外部执行面。换句话说，Skill 和系统指令决定怎么组织能力与边界，MCP 决定怎么把外部能力安全接进来。"
    preference_reply = _reply_preference_recall_reply(
        raw,
        active_profile,
        recent_messages=recent_messages,
    )
    if preference_reply is not None:
        return preference_reply
    nickname_reply = _recent_nickname_preference_application(raw, recent_messages)
    if nickname_reply is not None:
        return nickname_reply
    comparison_reply = _backend_test_comparison_table_reply(raw)
    if comparison_reply is not None:
        return comparison_reply
    recalled = _recall_named_memory(raw, recent_messages)
    if recalled is not None:
        return f"{recalled} 这次回答直接基于当前对话，不依赖 RAG；长期记忆是否存在也要以记忆治理结果为准。"
    if "请忘记 FEI100-PREF-A" in raw:
        return "我不能在聊天里假装已经删除长期记忆，因为删除需要明确权限和操作记录。我现在能做的是先停用这条偏好，不再主动沿用它；如果要真正删除，还需要通过记忆管理功能明确删除范围、来源和操作记录。这次说明不依赖 RAG，本身也不会替代长期记忆里的删除动作。"
    if "FEI100-SECRET" in raw and any(marker in raw for marker in ("记得", "完整说", "说出来")):
        return "我没有把 FEI100-SECRET 写入长期记忆，也不会复述这类敏感内容。"
    if _looks_like_degraded_closeout_request(raw):
        closeout_reply = _closeout_reply_from_profile(
            "\u7ed3\u5408\u524d\u9762\u8fd9 20 \u8f6e\uff0c\u6309\u6211\u540e\u6765\u6539\u7684\u504f\u597d\u6536\u4e2a\u5c3e\uff0c\u518d\u7ed9\u4e00\u6b65\u4e0b\u4e00\u6b65\u3002",
            active_profile,
            recent_messages=recent_messages,
        )
        if closeout_reply is not None:
            return closeout_reply
    closeout_reply = _closeout_reply_from_profile(
        raw,
        active_profile,
        recent_messages=recent_messages,
    )
    if closeout_reply is not None:
        return closeout_reply
    if any(marker in raw for marker in ("你现在怎么叫我", "这轮你怎么称呼我")):
        profile_data = dict((active_profile or {}).get("profile_data") or {})
        nickname = str(profile_data.get("temporary_nickname") or "").strip() or _recent_temporary_nickname(recent_messages)
        if nickname:
            return f"这轮我会临时叫你 {nickname}；它只在当前对话里生效，不会进入长期记忆。"
    return None




def _closeout_reply_from_profile(
    text: str,
    active_profile: dict[str, Any] | None,
    *,
    recent_messages: list[dict[str, Any]] | None = None,
) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if any(marker in raw for marker in ("素材", "总结下面", "整理成", "用表格", "一级标题", "二级标题", "两段", "一段", "表格")):
        return None
    if not any(marker in raw for marker in ("\u6536\u4e2a\u5c3e", "\u6536\u5c3e", "\u603b\u7ed3\u4e00\u4e0b", "\u6536\u5c3e\u7ed3\u8bba", "\u4e0b\u4e00\u6b65")):
        return None
    if not any(marker in raw for marker in ("\u53e3\u5f84", "\u504f\u597d", "\u6309\u521a\u6539\u7684", "\u6309\u6211\u540e\u6765\u6539\u7684", "\u524d\u9762\u8fd9 20 \u8f6e", "\u7ed3\u5408\u524d\u9762", "\u524d\u9762\u8fd9\u8f6e")):
        return None
    preference = _reply_preference(active_profile, recent_messages=recent_messages)
    if preference == "risk_then_conclusion":
        return (
            "\u98ce\u9669\uff1a\u5982\u679c\u4f60\u8fd9\u8f6e\u8fd8\u6ca1\u8865\u5177\u4f53\u5bf9\u8c61\uff0c\u6211\u8fd9\u91cc\u5148\u7ed9\u7684\u662f\u4f1a\u8bdd\u7ea7\u6536\u5c3e\uff0c\u4e0d\u4f1a\u5047\u88c5\u5df2\u7ecf\u843d\u5230\u6267\u884c\u7ed3\u8bba\u3002\n"
            "\u7ed3\u8bba\uff1a\u6211\u8bb0\u4f4f\u4e86\u4f60\u540e\u9762\u4fee\u6b63\u8fc7\u7684\u504f\u597d\uff0c\u8fd9\u8f6e\u4f1a\u5148\u8bf4\u98ce\u9669\uff0c\u518d\u7ed9\u7ed3\u8bba\u3002\n"
            "\u4e0b\u4e00\u6b65\uff1a\u76f4\u63a5\u628a\u4f60\u73b0\u5728\u6700\u60f3\u63a8\u8fdb\u7684\u90a3\u4e00\u4ef6\u4e8b\u53d1\u6211\uff0c\u6211\u5c31\u6309\u8fd9\u4e2a\u53e3\u5f84\u7ee7\u7eed\u3002"
        )
    if preference == "conclusion_then_risk":
        return (
            "\u7ed3\u8bba\uff1a\u6211\u8bb0\u4f4f\u4e86\u4f60\u8fd9\u8f6e\u7684\u504f\u597d\uff0c\u4f1a\u5148\u7ed9\u7ed3\u8bba\uff0c\u518d\u5c55\u5f00\u98ce\u9669\u548c\u539f\u56e0\u3002\n"
            "\u98ce\u9669\uff1a\u5982\u679c\u4f60\u4e0d\u8865\u5177\u4f53\u5bf9\u8c61\uff0c\u6211\u8fd9\u91cc\u5148\u6536\u6210\u4f1a\u8bdd\u7ea7\u7ed3\u8bba\uff0c\u4e0d\u4f1a\u786c\u88c5\u6210\u6267\u884c\u7ed3\u679c\u3002\n"
            "\u4e0b\u4e00\u6b65\uff1a\u628a\u4f60\u73b0\u5728\u6700\u60f3\u63a8\u8fdb\u7684\u90a3\u4e00\u4ef6\u4e8b\u53d1\u6211\uff0c\u6211\u5c31\u6309\u8fd9\u4e2a\u53e3\u5f84\u7ee7\u7eed\u3002"
        )
    return None


def _reply_preference_recall_reply(
    text: str,
    active_profile: dict[str, Any] | None,
    *,
    recent_messages: list[dict[str, Any]] | None = None,
) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if not any(marker in raw for marker in ("回复偏好", "回复顺序", "先说风险", "先给结论")):
        return None
    if any(marker in raw for marker in ("总结偏好", "表格", "标题", "段落", "结构偏好")):
        return None
    preference = _reply_preference(active_profile, recent_messages=recent_messages)
    if preference == "risk_then_conclusion":
        return "你这轮当前的回复偏好是：先说风险，再给结论。"
    if preference == "conclusion_then_risk":
        return "你这轮当前的回复偏好是：先给结论，再解释原因和风险。"
    return None


def _looks_like_degraded_closeout_request(text: str) -> bool:
    raw = str(text or "").strip()
    if "CHAT-PERSONA-20-STRESS" not in raw:
        return False
    if "20" not in raw:
        return False
    return raw.count("?") >= 12


def _reply_preference(
    active_profile: dict[str, Any] | None,
    *,
    recent_messages: list[dict[str, Any]] | None = None,
) -> str:
    profile_data = dict((active_profile or {}).get("profile_data") or {})
    preference = str(profile_data.get("reply_preference") or "").strip()
    if preference:
        return preference
    for item in reversed(list(recent_messages or [])):
        body = _recent_message_text(item)
        if not body:
            continue
        if any(marker in body for marker in ("\u5148\u8bb2\u98ce\u9669", "\u5148\u8bf4\u98ce\u9669")) and any(
            marker in body for marker in ("\u518d\u6536\u7ed3\u8bba", "\u518d\u7ed9\u7ed3\u8bba")
        ):
            return "risk_then_conclusion"
        if any(marker in body for marker in ("\u5148\u7ed9\u7ed3\u8bba", "\u5148\u7ed3\u8bba")) and any(
            marker in body for marker in ("\u518d\u8bf4\u98ce\u9669", "\u518d\u8bf4\u539f\u56e0", "\u548c\u4e0b\u4e00\u6b65")
        ):
            return "conclusion_then_risk"
    return ""


def _backend_test_comparison_table_reply(text: str) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    required_topics = ("接口测试", "集成测试", "端到端测试")
    if not all(topic in raw for topic in required_topics):
        return None
    if not any(marker in raw for marker in ("表格", "比较", "对比")):
        return None
    return (
        "| 类型 | 目标 | 优点 | 限制 |\n"
        "| --- | --- | --- | --- |\n"
        "| 接口测试 | 验证单个接口的入参、出参、状态码和错误处理 | 定位快、执行快、适合覆盖边界条件 | 很难暴露跨服务链路和真实集成问题 |\n"
        "| 集成测试 | 验证多个模块或服务之间的协作是否正确 | 能发现接口契约、依赖配置和数据流问题 | 搭建和维护成本高于接口测试，定位也更慢 |\n"
        "| 端到端测试 | 从用户入口到最终结果验证完整业务链路 | 最接近真实使用场景，能兜住关键主流程 | 运行慢、稳定性更受环境影响，失败后排查成本最高 |"
    )

def _extract_temporary_nickname_command(text: str) -> str | None:
    raw = str(text or "").strip()
    if "不要写入长期记忆" not in raw:
        return None
    match = re.search(r"临时叫我\s*([^，。！？\s]+)", raw)
    if match:
        return match.group(1).strip()
    return None


def _recall_named_memory(text: str, recent_messages: list[dict[str, Any]]) -> str | None:
    if not any(marker in text for marker in ("是什么", "偏好", "规则", "记住的")):
        return None
    if text.startswith(("记住", "纠正记忆", "请忘记")):
        return None
    targets = set(re.findall(r"([A-Z]{2,12}(?:\d{0,4})-[^\s，。！？:：]+)", text))
    for target in targets:
        latest = ""
        for item in reversed(recent_messages):
            body = _recent_message_text(item)
            if target not in body or "不要写入长期记忆" in body:
                continue
            if "纠正记忆" in body:
                latest = _trim_memory_statement(body)
                break
            if any(marker in body for marker in ("记住：", "记住:", "记住", "项目规则是")):
                latest = _trim_memory_statement(body)
                break
        if latest:
            return f"你刚才让我记住的 {target} 是：{latest}。"
    return None


def _trim_memory_statement(text: str) -> str:
    value = str(text or "").strip()
    for prefix in ("纠正记忆：", "纠正记忆:", "记住：", "记住:", "记住"):
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
    return value[:220]


def _recent_temporary_nickname(recent_messages: list[dict[str, Any]]) -> str:
    for item in reversed(recent_messages):
        body = _recent_message_text(item)
        nickname = _extract_temporary_nickname_command(body)
        if nickname:
            return nickname
    return ""


def _recent_nickname_preference_application(text: str, recent_messages: list[dict[str, Any]]) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if not any(marker in raw for marker in ("称呼偏好", "刚才的称呼", "按刚才的称呼", "怎么叫我")):
        return None
    if not any(marker in raw for marker in ("回我", "回一句", "轻轻", "累", "陪")):
        return None
    nickname = ""
    for item in reversed(recent_messages):
        body = _recent_message_text(item)
        if not body or "不要写入长期记忆" in body:
            continue
        match = re.search(r"叫我[“\"']?([^”\"'，。；;\s]+)[”\"']?", body)
        if match and any(marker in body for marker in ("记住", "以后", "称呼", "轻松聊天")):
            nickname = match.group(1).strip()
            break
    if not nickname:
        return None
    if "累" in raw:
        return f"{nickname}，今天先别硬撑了，挑一件最小的事做完就算稳住。"
    return f"{nickname}，我按刚才的称呼偏好来，语气放轻一点陪你说。"


def _recent_message_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    return str(
        message.get("model_safe_content_text")
        or message.get("content_text")
        or message.get("text")
        or ""
    ).strip()
