from __future__ import annotations

import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from core_types import ApprovalDetail, ResponsePlan, RiskLevel
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

FORBIDDEN_MAIN_REPLY_TERMS = {
    "approval_id": "纭缂栧彿",
    "tool_call_id": "宸ュ叿璁板綍",
    "trace_id": "瀹¤璁板綍",
    "鍐呴儴 trace": "杩囩▼璁板綍",
    "browser.download": "涓嬭浇鍔ㄤ綔",
    "browser.snapshot": "缃戦〉蹇収",
    "browser.screenshot": "椤甸潰鎴浘",
    "task_id": "浠诲姟璁板綍",
    "宸ュ叿杈圭晫": "澶勭悊闄愬埗",
    "鍙楁帶浠诲姟": "澶勭悊娴佺▼",
    "浠诲姟鍥炴斁": "缁撴灉璁板綍",
    "宸ヤ欢": "缁撴灉璁板綍",
    "Capability Graph": "鏉冮檺鑼冨洿",
    "Asset Broker": "鎺堟潈璧勬簮閫氶亾",
    "Safety": "风险检查",
    "Approval": "纭",
    "R3": "闇€瑕佺‘璁ょ殑椋庨櫓",
    "R4": "杈冮珮椋庨櫓",
    "R5": "高风险",
    "/api/approvals": "纭鎺ュ彛",
}

_URL_RE = re.compile(r"https?://[^\s锛屻€傦紱;锛?]+", re.IGNORECASE)
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
        decision = await self._session_runtime.decide(
            conversation_id=turn["conversation_id"],
            session_id=session_id,
            user_text=text,
        )
        pending = decision.pending_actions
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
            if _looks_like_resolution(text) or _is_ambiguous_continue(text):
                return _outcome(
                    _no_pending_text(text),
                    status="no_pending_action",
                    reason_codes=["no_pending_action"],
                    clear_pending=True,
                )
            return None
        if decision.decision_type == "idle":
            return None
        if not pending and (_looks_like_resolution(text) or _is_ambiguous_continue(text)):
            return _outcome(
                _no_pending_text(text),
                status="no_pending_action",
                reason_codes=["no_pending_action"],
                clear_pending=True,
            )
        if decision.decision_type == "new_action_request":
            return None
        if decision.decision_type == "hard_block":
            return _outcome(
                _hard_block_text(decision.reason_codes[0]),
                status="hard_block",
                reason_codes=list(decision.reason_codes),
                clear_pending=False,
            )
        if decision.decision_type == "plain_next_step":
            return _outcome(
                _plain_next_step_text(pending),
                status="plain_next_step",
                reason_codes=["plain_next_step_requested"],
                pending_actions=pending,
                clear_pending=False,
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
                )
            return _outcome(
                text_builder(pending),
                status="blocked",
                reason_codes=list(decision.reason_codes),
                pending_actions=pending,
                clear_pending=False,
            )
        if decision.decision_type == "resolve_pending" and pending:
            return await self._resolve_action(
                action=pending[0],
                resolution=str(decision.resolution_kind or "once"),
                trace_id=trace_id,
                session_id=session_id,
                presence_runtime=presence_runtime,
                edited_payload=decision.edited_payload,
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
                    reason="纭璁板綍缂哄け",
                ),
                status="blocked",
                reason_codes=["missing_approval_ref"],
                clear_pending=True,
                block_reason="missing_approval_ref",
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
            )
        detail = dispatch.result_payload.get("detail")
        if dispatch.resume_target == "external_platform" and dispatch.status in {"approved", "edited", "denied"}:
            return _external_platform_outcome(
                detail=detail,
                fallback_text=str(dispatch.visible_reply_hint or "我已经继续这项外部平台操作。"),
                presence_runtime=presence_runtime,
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
) -> NaturalChatOutcome:
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
        }
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
        return NaturalChatOutcome(
            text=visible_text_guard(visible_text),
            response_plan=plan,
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
    return NaturalChatOutcome(text=visible_text_guard(text), response_plan=plan)


def _external_platform_outcome(
    *,
    detail: Any | None,
    fallback_text: str,
    presence_runtime: dict[str, Any] | None = None,
) -> NaturalChatOutcome:
    text = str(getattr(detail, "message", "") or fallback_text)
    next_step = getattr(detail, "next_step", None)
    plan = getattr(detail, "plan", None)
    plan_status = str(getattr(plan, "status", "") or "")
    structured_payload = _external_platform_structured_payload(detail)
    structured_payload["natural_interaction"] = {
        "status": canonical_action_status(plan_status or "completed"),
        "reason_codes": ["external_platform_chat_resume"],
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
    return NaturalChatOutcome(text=visible_text_guard(text), response_plan=response_plan)


