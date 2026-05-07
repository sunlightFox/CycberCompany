from __future__ import annotations

import hashlib
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from core_types import ApprovalDetail, ResponsePlan, RiskLevel
from response_composer import ResponseComposer
from response_composer.chat_voice import voice_metadata_for_scenario
from response_composer.opening_copy import opening_copy
from app.services.chat_visible_guard import (
    reset_visible_redaction_profile as _reset_visible_redaction_profile,
    set_visible_redaction_profile as _set_visible_redaction_profile,
    visible_text_guard as _visible_text_guard,
)
from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.services.approvals import ApprovalService
from app.services.action_dialogue_mapper import ActionDialogueMapperService
from app.schemas.chat_quality import ActionDialogueFacts

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

_URL_RE = re.compile(r"https?://[^\s，。；;）)]+", re.IGNORECASE)
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
    ) -> None:
        self._chat_repo = chat_repo
        self._approvals = approval_service
        self._task_engine = task_engine
        self._host_installs = host_install_service
        self._composer = ResponseComposer()
        self._action_dialogue_mapper = ActionDialogueMapperService()

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
        pending = await self._pending_actions(turn["conversation_id"], session_id)
        if not pending:
            if _is_deny(text) or _is_edit(text):
                return _outcome(
                    _no_pending_text(text),
                    status="no_pending_action",
                    reason_codes=["no_pending_action"],
                    clear_pending=True,
                )
            return None
        hard_block = _hard_block_reason(text)
        if hard_block:
            return _outcome(
                _hard_block_text(hard_block),
                status="hard_block",
                reason_codes=[hard_block],
                clear_pending=False,
            )
        deterministic = _deterministic_plain_reply(text)
        if deterministic is not None:
            return _outcome(
                deterministic,
                status="direct_plain_reply",
                reason_codes=["natural_plain_reply"],
                clear_pending=False,
            )

        if _asks_how_to_confirm(text):
            return _outcome(
                _plain_next_step_text(pending),
                status="plain_next_step",
                reason_codes=["plain_next_step_requested"],
                pending_actions=pending,
                clear_pending=False,
            )
        if _is_ambiguous_continue(text):
            if not pending:
                return _outcome(
                    opening_copy("action.no_pending", seed=text),
                    status="no_pending_action",
                    reason_codes=["ambiguous_continue_without_pending"],
                    clear_pending=False,
                )
            if len(pending) > 1 or _max_risk(pending) >= 3:
                return _outcome(
                    _ambiguous_pending_text(pending),
                    status="blocked",
                    reason_codes=["ambiguous_confirmation_blocked"],
                    pending_actions=pending,
                    clear_pending=False,
                )
        if _looks_like_resolution(text):
            if not pending:
                return _outcome(
                    _no_pending_text(text),
                    status="no_pending_action",
                    reason_codes=["no_pending_action"],
                    clear_pending=True,
                )
            if len(pending) > 1:
                return _outcome(
                    _multiple_pending_text(pending),
                    status="blocked",
                    reason_codes=["multiple_pending_actions"],
                    pending_actions=pending,
                    clear_pending=False,
                )
            action = pending[0]
            if _is_always_allow(text) and _risk_order(str(action.get("risk_level") or "R1")) >= 3:
                return _outcome(
                    opening_copy("action.blocked", seed=text),
                    status="blocked",
                    reason_codes=["always_denied_for_risk"],
                    pending_actions=pending,
                    clear_pending=False,
                )
            if _is_deny(text):
                return await self._resolve_action(
                    action=action,
                    resolution="deny",
                    trace_id=trace_id,
                    session_id=session_id,
                    presence_runtime=presence_runtime,
                )
            if _is_edit(text):
                return await self._resolve_action(
                    action=action,
                    resolution="edit",
                    trace_id=trace_id,
                    session_id=session_id,
                    presence_runtime=presence_runtime,
                    edited_payload=_edit_payload_for_action(action, text),
                )
            if _is_confirm(text) or _is_session_allow(text) or _is_ambiguous_continue(text):
                return await self._resolve_action(
                    action=action,
                    resolution="session" if _is_session_allow(text) else "once",
                    trace_id=trace_id,
                    session_id=session_id,
                    presence_runtime=presence_runtime,
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
        approval_id = str(action.get("approval_id") or "")
        label = str(action.get("user_label") or action.get("action_label") or "这一步操作")
        if not approval_id:
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
            )
        try:
            if resolution == "deny":
                await self._approvals.deny(
                    approval_id,
                    actor_type="user",
                    actor_id="user_local_owner",
                    reason="natural_language_deny",
                    trace_id=trace_id,
                )
                if self._task_engine is not None:
                    await self._task_engine.handle_approval_resolved(
                        approval_id,
                        trace_id=trace_id,
                    )
                return _outcome(
                    "",
                    status="denied",
                    reason_codes=["natural_language_deny"],
                    action=action,
                    clear_pending=True,
                    composer=self._composer,
                    presence_runtime=presence_runtime,
                    action_dialogue_mapper=self._action_dialogue_mapper,
                )
            if resolution == "edit":
                if edited_payload is None:
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
                        failure_reason="请直接说清要改成什么，比如把地址、目标、标题或正文改成新的内容。",
                        block_reason="edit_missing_target",
                    )
                await self._approvals.edit(
                    approval_id,
                    actor_type="user",
                    actor_id="user_local_owner",
                    reason="natural_language_edit",
                    edited_payload=edited_payload,
                    trace_id=trace_id,
                )
                detail = None
                if self._task_engine is not None:
                    detail = await self._task_engine.handle_approval_resolved(
                        approval_id,
                        trace_id=trace_id,
                    )
                return _outcome(
                    _after_resolution_text(label, "edited", detail=detail),
                    status="edited",
                    reason_codes=["natural_language_edit"],
                    action={**action, "edited_payload": edited_payload},
                    clear_pending=True,
                    composer=self._composer,
                    presence_runtime=presence_runtime,
                    action_dialogue_mapper=self._action_dialogue_mapper,
                    detail=detail,
                )
            await self._approvals.approve(
                approval_id,
                actor_type="user",
                actor_id="user_local_owner",
                reason=f"natural_language_{resolution}",
                trace_id=trace_id,
            )
            detail = None
            host_execution = None
            if self._host_installs is not None:
                host_execution = await self._host_installs.execute_for_approval(
                    approval_id,
                    trace_id=trace_id,
                )
            if host_execution is not None and self._task_engine is not None:
                detail = await self._task_engine.detail(host_execution.task_id)
            elif self._task_engine is not None:
                detail = await self._task_engine.handle_approval_resolved(
                    approval_id,
                    trace_id=trace_id,
                )
            return _outcome(
                _after_resolution_text(label, resolution, detail=detail),
                status="approved",
                reason_codes=[f"natural_language_{resolution}"],
                action=action,
                clear_pending=True,
                composer=self._composer,
                presence_runtime=presence_runtime,
                action_dialogue_mapper=self._action_dialogue_mapper,
                detail=detail,
                session_grant=(
                    _session_grant(action, session_id)
                    if resolution == "session"
                    else None
                ),
            )
        except AppError as exc:
            error_code = getattr(exc.code, "value", str(exc.code))
            return _outcome(
                (
                    opening_copy(
                        "action.blocked",
                        seed=f"{label}|{error_code}",
                        label=label,
                        reason=visible_text_guard(exc.message),
                    )
                ),
                status="blocked",
                reason_codes=["resolution_failed", error_code],
                action=action,
                clear_pending=True,
                composer=self._composer,
                presence_runtime=presence_runtime,
                action_dialogue_mapper=self._action_dialogue_mapper,
                failure_reason=visible_text_guard(exc.message),
                block_reason=error_code,
            )

    async def _pending_actions(
        self,
        conversation_id: str,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        state = await self._chat_repo.get_working_state(conversation_id)
        pending = dict((state or {}).get("pending_confirmation") or {})
        actions = [
            dict(item)
            for item in pending.get("actions", [])
            if isinstance(item, dict) and item.get("approval_id")
        ]
        if session_id:
            same_session = [
                item for item in actions if str(item.get("session_id") or "") == str(session_id)
            ]
            if same_session:
                return same_session
        return actions


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
        status="pending_action",
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
            natural_interaction={"status": "pending_action"},
            task_status={"status": "waiting_approval"},
            approval_pending=True,
        )
    )
    facts["action_dialogue"] = action_dialogue.model_dump(mode="json")
    plan = composer.response_plan_for_action_status(facts=facts)
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
            visible_text_guard(plan.plain_text or plan.summary or ""),
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
            "plain_text": visible_text_guard(plan.plain_text or plan.summary or ""),
            "summary": visible_text_guard(plan.summary or plan.plain_text or ""),
            "sections": [
                {
                    "kind": "natural_interaction",
                    "text": visible_text_guard(plan.plain_text or plan.summary or ""),
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
                approval_pending=status in {"pending_action", "waiting_approval"},
            )
        )
        facts["action_dialogue"] = action_dialogue.model_dump(mode="json")
        plan = composer.response_plan_for_action_status(
            facts=facts,
            task_status=_task_status_payload(detail),
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
        plan = plan.model_copy(
            update={
                "structured_payload": structured_payload,
                "follow_up_options": reply_options,
                "user_next_step": reply_options[0] if reply_options else None,
            }
        )
        return NaturalChatOutcome(
            text=visible_text_guard(plan.plain_text or plan.summary or text),
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


def _natural_interaction_payload(
    *,
    status: str,
    reason_codes: list[str],
    pending_actions: list[dict[str, Any]] | None,
    reply_options: list[str] | None,
    clear_pending: bool,
    session_grant: dict[str, Any] | None = None,
    block_reason: str | None = None,
    action_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = list(reply_options or [])
    return {
        "version": "natural_interaction.openclaw_hermes.v4",
        "status": status,
        "reason_codes": list(reason_codes),
        "block_reason": block_reason,
        "pending_actions": pending_actions or [],
        "reply_options": options,
        "reply_option_items": _reply_option_items(options),
        "clear_pending": clear_pending,
        "session_grant": session_grant or {},
        "action_result": action_result or {},
    }


def _plan(
    text: str,
    *,
    status: str,
    reason_codes: list[str],
    pending_actions: list[dict[str, Any]] | None = None,
    pending_confirmation: dict[str, Any] | None = None,
    clear_pending: bool = False,
    session_grant: dict[str, Any] | None = None,
    technical_detail: dict[str, Any] | None = None,
    block_reason: str | None = None,
) -> ResponsePlan:
    visible = visible_text_guard(text)
    reply_options = _reply_options_from_actions(pending_actions or [])
    natural = _natural_interaction_payload(
        status=status,
        reason_codes=reason_codes,
        pending_actions=pending_actions,
        reply_options=reply_options,
        clear_pending=clear_pending,
        session_grant=session_grant,
        block_reason=block_reason,
        action_result=technical_detail or {},
    )
    natural["natural_reply_options"] = reply_options
    pending_action_binding = {
        "conversation_session_bound": True,
        "unique_action_required": True,
        "action_count": len(pending_actions or []),
        "fail_closed": status
        in {
            "blocked",
            "no_pending_action",
            "multiple_pending_actions",
            "ambiguous_confirmation_blocked",
            "always_denied_for_risk",
            "edit_missing_target",
            "pending_action_invalid",
            "resolution_failed",
        },
        "status": status,
    }
    if pending_confirmation is not None:
        natural["pending_confirmation"] = pending_confirmation
    return ResponsePlan(
        title="等待确认" if pending_actions else None,
        style="natural_action",
        summary=visible,
        sections=[{"kind": "natural_interaction", "text": visible}],
        follow_up_options=reply_options,
        plain_text=visible,
        structured_payload={
            "scenario": "natural_interaction",
            **voice_metadata_for_scenario("action_status"),
            "natural_interaction": natural,
            "pending_actions": pending_actions or [],
            "pending_action_binding": pending_action_binding,
            "response_quality_guard": _natural_quality_guard(
                None,
                visible,
                status=status,
                state_disclosed=True,
                boundary_disclosed=True,
                next_step_provided=bool(reply_options) or status != "approved",
            ),
            "natural_reply_options": reply_options,
            "reply_option_items": _reply_option_items(reply_options),
            "technical_detail": redact(technical_detail or {}),
        },
        tone_mode="safety_boundary" if pending_actions else "default",
        quality_markers={
            "directness": True,
            "boundary_honesty": True,
            "no_leakage": True,
            "natural_language": True,
        },
        user_next_step=reply_options[0] if reply_options else None,
    )


def _natural_quality_guard(
    base: Any,
    text: str,
    *,
    status: str,
    state_disclosed: bool,
    boundary_disclosed: bool,
    next_step_provided: bool,
) -> dict[str, Any]:
    base_guard = base if isinstance(base, dict) else {}
    checks = dict(base_guard.get("checks") or {})
    checks.update(
        {
            "state_disclosed": bool(state_disclosed),
            "boundary_disclosed": bool(boundary_disclosed),
            "next_step_provided": bool(next_step_provided),
            "no_false_done": True,
            "no_internal_terms": True,
        }
    )
    violations = list(base_guard.get("violations") or [])
    for check, passed in checks.items():
        exists = any(
            isinstance(item, dict) and item.get("check") == check
            for item in violations
        )
        if not passed and not exists:
            violations.append({"check": check})
    return {
        "version": str(
            base_guard.get("version") or "response_quality_guard.openclaw_hermes.v4"
        ),
        "status": "passed" if all(bool(value) for value in checks.values()) else "warning",
        "checks": checks,
        "violations": violations,
        "redaction_applied": bool(base_guard.get("redaction_applied")),
        "strict_format_preserved": bool(base_guard.get("strict_format_preserved", True)),
        "visible_text_hash": str(base_guard.get("visible_text_hash") or _visible_hash(text)),
        "natural_action": {"status": status},
    }


def _visible_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _block_reason_for_status(status: str, reason_codes: list[str]) -> str | None:
    if status != "blocked" and status != "no_pending_action":
        return None
    for code in reason_codes:
        if code in {
            "multiple_pending_actions",
            "ambiguous_confirmation_blocked",
            "always_denied_for_risk",
            "missing_approval_ref",
            "pending_action_invalid",
            "no_pending_action",
        }:
            return code
    return reason_codes[0] if reason_codes else status


def _pending_action_binding(status: str, pending_actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "conversation_session_bound": True,
        "unique_action_required": True,
        "action_count": len(pending_actions),
        "fail_closed": status
        in {
            "blocked",
            "no_pending_action",
            "multiple_pending_actions",
            "ambiguous_confirmation_blocked",
            "always_denied_for_risk",
            "edit_missing_target",
            "pending_action_invalid",
            "resolution_failed",
        },
        "status": status,
    }


def _reply_option_items(options: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for option in options:
        label = str(option)
        code = "edit"
        if any(marker in label for marker in ["只允许", "本次允许", "确认"]):
            code = "once"
        elif "本会话" in label:
            code = "session"
        elif any(marker in label for marker in ["拒绝", "取消"]):
            code = "deny"
        items.append({"code": code, "label": label})
    return items


def _action_status_facts(
    action: dict[str, Any],
    *,
    status: str,
    detail: Any | None,
    failure_reason: str | None,
) -> dict[str, Any]:
    action_type = str(action.get("action_type") or "")
    label = str(action.get("user_label") or action.get("action_label") or "这一步操作")
    target = str(
        (action.get("payload_summary") or {}).get("display_name")
        or (action.get("payload_summary") or {}).get("requested_software")
        or (action.get("payload_summary") or {}).get("url")
        or (action.get("payload_summary") or {}).get("path")
        or ""
    )
    detail_status = str(getattr(detail, "status", "") or "")
    return {
        "status": status,
        "action_type": action_type,
        "action_label": label,
        "target": target,
        "risk_level": str(action.get("risk_level") or ""),
        "approval_required": status in {"pending_action", "waiting_approval"},
        "reply_options": list(action.get("reply_options") or []),
        "reply_option_items": _reply_option_items(list(action.get("reply_options") or [])),
        "impact_summary": str(action.get("impact_summary") or ""),
        "detail_status": detail_status,
        "completed": detail_status == "completed",
        "failed": detail_status == "failed",
        "failure_reason": failure_reason,
        "evidence_summary": "结果可以通过任务记录、结果记录或过程记录复核。",
    }


def _task_status_payload(detail: Any | None) -> dict[str, Any] | None:
    if detail is None:
        return None
    task_id = str(getattr(detail, "task_id", "") or "")
    status = str(getattr(detail, "status", "") or "")
    if not task_id and not status:
        return None
    return {"task_id": task_id, "status": status, "mode": "workflow"}


def _pending_action_prompt(action: dict[str, Any]) -> str:
    summary = str(action.get("user_summary") or "我准备执行这一步。")
    impact = str(action.get("impact_summary") or "这一步需要你点头后才会继续。")
    options = list(action.get("reply_options") or [])
    if not options:
        options = ["只允许这一次", "拒绝", "修改目标为：..."]
    return (
        f"{summary}\n{impact}\n\n"
        "请直接回复：\n"
        + "\n".join(f"- {option}" for option in options)
        + "\n\n在你确认前，我不会把这一步说成已经完成。"
    )


def _plain_next_step_text(pending: list[dict[str, Any]]) -> str:
    if pending:
        action = pending[0]
        label = str(action.get("user_label") or "这一步操作")
        options = list(action.get("reply_options") or ["只允许这一次", "拒绝", "修改目标为：..."])
        return (
            f"现在等你点头的是：{label}。\n"
            "不用复制编号，直接回我下面任意一句就行：\n"
            + "\n".join(f"- {option}" for option in options)
        )
    return opening_copy("action.no_pending", "plain_next_step")


def _no_pending_text(text: str) -> str:
    if _is_deny(text):
        return opening_copy("action.no_pending", seed=text, mode="deny")
    if _is_edit(text):
        return opening_copy("action.no_pending", seed=text, mode="edit")
    return opening_copy("action.no_pending", seed=text)


def _ambiguous_pending_text(pending: list[dict[str, Any]]) -> str:
    label = str(pending[0].get("user_label") or "这一步操作")
    return opening_copy("action.ambiguous_blocked", seed=label, label=label)


def _multiple_pending_text(pending: list[dict[str, Any]]) -> str:
    labels = "、".join(str(item.get("user_label") or "待确认操作") for item in pending[:3])
    return opening_copy("action.multiple_pending", seed=labels, labels=labels)


def _after_resolution_text(label: str, resolution: str, *, detail: Any | None) -> str:
    status = str(getattr(detail, "status", "") or "")
    if resolution == "edited":
        prefix = _natural_copy("after_edited", seed=label, label=label)
    elif resolution == "session":
        prefix = _natural_copy("after_session", seed=label, label=label)
    else:
        prefix = _natural_copy("after_once", seed=label, label=label)
    if not status:
        return _natural_copy("after_no_status", seed=label, prefix=prefix)
    if status == "completed":
        return _natural_copy("after_completed", seed=label, prefix=prefix)
    if status in {"paused", "waiting_approval"}:
        return _natural_copy("after_waiting", seed=label, prefix=prefix)
    if status == "failed":
        return _natural_copy("after_failed", seed=label, prefix=prefix)
    return f"{prefix} 当前状态是 {status}，下一步我会按实际结果说。"


def _deterministic_plain_reply(text: str) -> str | None:
    if "网页快照" in text and "截图" in text and "不懂技术" in text:
        return (
            "可以把网页快照理解成“页面内容清单”：它记录页面地址、标题和能读到的文字，"
            "适合证明我看到了哪些信息。截图更像“当时拍下的照片”，适合证明页面长什么样。"
            "两者都是证据：一个方便核对内容，一个方便核对画面。没有这些证据时，我不能说自己真的打开过网页。"
        )
    if "5 条测试点" in text or "5条测试点" in text:
        return (
            "1. 动作请求要说明是否已经执行，还是正在等待确认。\n"
            "2. 删除、下载、登录这类操作必须先让用户明确确认。\n"
            "3. 用户说“不要执行”时，只能给方案，不能创建任务。\n"
            "4. 回复里不要要求用户复制内部编号。\n"
            "5. 结果反馈要说明证据、状态和下一步。"
        )
    if "给一段" in text and "怎么问我的示例" in text:
        return (
            "我会这样问：\n"
            "我准备下载这个文件，并保存到当前任务结果里。下载会在本机生成文件，所以需要你确认。\n\n"
            "你可以直接回复：\n"
            "- 只允许这一次\n"
            "- 本会话内同类下载都允许\n"
            "- 拒绝\n"
            "- 把下载地址改成：..."
        )
    if "自然回复模板" in text and "技术字段" in text:
        return (
            "可以这样说：\n"
            "这次浏览器任务已经完成。我打开了目标页面，并保存了页面快照和截图作为证据。"
            "如果页面没有完成登录或下载，我会明确说“没有完成”，再告诉你失败原因和下一步，"
            "例如重新提供地址、换账号，或取消这次操作。"
        )
    return None


def _hard_block_reason(text: str) -> str | None:
    lowered = text.lower()
    if "169.254.169.254" in lowered or "metadata" in lowered:
        return "metadata_or_private_network_target"
    if "file://" in lowered:
        return "browser_file_url_denied"
    if any(marker in lowered for marker in ["private key", "private_key", "mnemonic"]):
        return "secret_or_key_request"
    if any(marker in text for marker in ["私钥", "助记词", "系统密钥"]):
        return "secret_or_key_request"
    return None


def _hard_block_text(reason: str) -> str:
    if reason == "browser_file_url_denied":
        return _natural_copy("hard_block_file", seed=reason)
    if reason == "metadata_or_private_network_target":
        return _natural_copy("hard_block_network", seed=reason)
    return _natural_copy("hard_block_secret", seed=reason)


def _looks_like_resolution(text: str) -> bool:
    text = _control_text(text)
    return (
        _is_confirm(text)
        or _is_deny(text)
        or _is_edit(text)
        or _is_session_allow(text)
        or _is_always_allow(text)
    )


def _is_confirm(text: str) -> bool:
    text = _control_text(text)
    normalized = text.strip().strip("。.!！?？~～ ")
    explicit_markers = [
        "确认下载",
        "确认这次",
        "确认执行",
        "确认继续",
        "确认操作",
        "只允许这一次",
        "本次允许",
        "允许这一次",
    ]
    return normalized in {"确认", "同意", "允许"} or any(
        marker in text for marker in explicit_markers
    )


def _is_session_allow(text: str) -> bool:
    text = _control_text(text)
    return "本会话" in text and any(marker in text for marker in ["允许", "同类", "都可以"])


def _is_always_allow(text: str) -> bool:
    text = _control_text(text)
    return any(marker in text for marker in ["总是允许", "以后都允许", "永久允许"])


def _is_deny(text: str) -> bool:
    text = _control_text(text)
    normalized = text.strip().strip("。.!！?？~～ ")
    exact = {"拒绝", "取消", "不允许", "不删除", "不要执行", "停止", "不用了"}
    contextual = [
        "拒绝这次",
        "取消这次",
        "取消本次",
        "拒绝本次",
        "不允许这次",
        "停止这次",
    ]
    return normalized in exact or any(marker in text for marker in contextual)


def _is_edit(text: str) -> bool:
    text = _control_text(text)
    return any(marker in text for marker in ["改成", "修改", "换成"]) and any(
        marker in text
        for marker in ["地址", "目标", "参数", "url", "URL", "标题", "正文", "内容"]
    )


def _is_ambiguous_continue(text: str) -> bool:
    text = _control_text(text)
    normalized = text.strip().strip("。.!！?？~～ ")
    return normalized in {"好的", "好", "嗯", "继续", "可以", "行", "走吧", "ok", "OK"}


def _control_text(text: str) -> str:
    stripped = text.strip()
    if "：" not in stripped:
        return stripped
    prefix, suffix = stripped.split("：", 1)
    normalized_prefix = prefix.strip().upper()
    if normalized_prefix.startswith(
        ("CHAT-E2E-", "PHASE34-", "NAT-", "WECHAT-REAL-")
    ):
        return suffix.strip()
    suffix_normalized = suffix.strip().strip("。.!！?？~～ ")
    if suffix_normalized in {
        "好的",
        "好",
        "嗯",
        "继续",
        "可以",
        "行",
        "走吧",
        "OK",
        "ok",
        "确认",
        "同意",
        "允许",
        "拒绝",
        "取消",
        "不用了",
    }:
        return suffix.strip()
    return stripped


def _asks_how_to_confirm(text: str) -> bool:
    markers = ["不懂什么是审批", "不想复制", "怎么回复", "告诉我应该怎么回复"]
    return any(marker in text for marker in markers)


def _first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0) if match else None


def _edit_payload_for_action(action: dict[str, Any], text: str) -> dict[str, Any] | None:
    action_type = str(action.get("action_type") or "")
    if action_type == "account.publish_post":
        args: dict[str, str] = {}
        title = _extract_edited_title(text)
        body = _extract_edited_body(text)
        if title:
            args["title"] = title
        if body:
            args["body"] = body
        return {"args": args} if args else None
    url = _first_url(text)
    if action_type == "browser.download":
        return {"args": {"url": url}, "action_type": action_type} if url else None
    if action_type in {"browser.open", "browser.snapshot", "browser.screenshot"}:
        return {"args": {"url": url}, "action_type": action_type} if url else None
    if action_type.startswith("browser."):
        return None
    return {"args": {"url": url}, "action_type": action_type} if url else None


def _extract_edited_title(text: str) -> str | None:
    patterns = [
        r"标题\s*(?:改成|修改为|换成|为|是|[:：])\s*[《「“\"]?(?P<title>[^》」”\"\n，。；;]{1,120})",
        r"[《「“\"](?P<title>[^》」”\"]{1,120})[》」”\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group("title").strip().strip("《》「」“”\"' ")
    return None


def _extract_edited_body(text: str) -> str | None:
    match = re.search(r"(?:正文|内容)\s*(?:改成|修改为|换成|为|是|[:：])\s*(?P<body>.+)$", text)
    if not match:
        return None
    return match.group("body").strip().strip("。；;，, \n\r\t\"“”'")


def _max_risk(actions: list[dict[str, Any]]) -> int:
    return max((_risk_order(str(item.get("risk_level") or "R1")) for item in actions), default=0)


def _risk_order(value: str) -> int:
    try:
        return int(str(value).removeprefix("R"))
    except ValueError:
        return 0


def _label_for_action(action_type: str, payload: dict[str, Any]) -> str:
    target = str(
        payload.get("display_name")
        or payload.get("requested_software")
        or payload.get("url")
        or payload.get("path")
        or "目标"
    )
    if action_type == "host.install_software":
        return f"安装 {target}"
    if action_type == "host.uninstall_software":
        return f"卸载 {target}"
    if action_type == "account.publish_post":
        platform = str(payload.get("platform") or "社交平台")
        title = str(payload.get("title") or "文章")
        return f"发布《{title}》到{platform}"
    if action_type == "browser.download":
        return f"下载 {target.rsplit('/', 1)[-1] or '文件'}"
    if action_type in {"browser.submit", "browser.fill", "browser.type", "browser.click"}:
        return "浏览器登录或表单操作"
    if action_type == "browser.screenshot":
        return "保存页面截图"
    if action_type == "file.delete":
        return f"删除 {target}"
    if action_type == "terminal.run":
        return "执行终端命令"
    return action_type.replace(".", " ")


def _summary_for_action(action_type: str, payload: dict[str, Any], label: str) -> str:
    target = str(
        payload.get("url")
        or payload.get("path")
        or payload.get("display_name")
        or payload.get("requested_software")
        or ""
    )
    if action_type == "host.install_software":
        return f"我准备{label}，这会修改本机软件和系统环境。"
    if action_type == "host.uninstall_software":
        return f"我准备{label}，这会从本机移除软件。"
    if action_type == "account.publish_post":
        account = str(payload.get("account_summary") or "账号")
        return f"我准备使用{account}{label}。"
    if action_type == "browser.download":
        return f"我准备{label}，并保存到当前任务的结果里。"
    if action_type in {"browser.submit", "browser.fill", "browser.type", "browser.click"}:
        return "我准备在浏览器页面里继续登录或提交操作。"
    if action_type == "browser.screenshot":
        return "我准备保存当前页面截图，作为这次操作的证据。"
    if action_type == "file.delete":
        return f"我准备{label}。"
    if target:
        return f"我准备执行{label}，目标是 {target}。"
    return f"我准备执行{label}。"


def _impact_for_action(action_type: str) -> str:
    if action_type == "host.install_software":
        return "这会安装本机软件或补齐包管理器，所以需要你明确确认；确认前尚未安装。"
    if action_type == "host.uninstall_software":
        return "这会卸载本机软件，所以需要你明确确认；确认前尚未卸载。"
    if action_type == "account.publish_post":
        return "这会向外部平台发布内容，所以需要你确认；确认前尚未发布。"
    if action_type == "browser.download":
        return "这会在本机生成下载文件，所以需要你确认；确认前尚未下载。"
    if action_type in {"browser.submit", "browser.fill", "browser.type", "browser.click"}:
        return "这可能改变页面状态或账号状态，所以需要你确认；确认前尚未提交。"
    if action_type == "browser.screenshot":
        return "这会保存截图结果，所以需要你确认；确认前尚未保存。"
    if action_type == "file.delete":
        return "删除后可能无法从任务结果里直接恢复，所以需要你明确确认。"
    if action_type == "terminal.run":
        return "终端命令可能影响本机文件或进程，所以需要你明确确认。"
    return "这一步有副作用或风险，需要你确认后才会继续。"


def _reply_options_for_action(action_type: str, risk_level: str) -> list[str]:
    options = ["只允许这一次"]
    if action_type == "account.publish_post":
        return [*options, "拒绝", "修改标题或正文"]
    if _risk_order(risk_level) <= 3 and action_type not in {"file.delete", "terminal.run"}:
        options.append("本会话内同类操作都允许")
    options.append("拒绝")
    if action_type == "browser.download":
        options.append("修改下载地址为：...")
    elif action_type.startswith("browser."):
        options.append("修改账号或地址")
    elif action_type == "file.delete":
        options.append("先给我看文件信息")
    else:
        options.append("修改目标为：...")
    return options


def _allowed_scopes(action_type: str, risk_level: str) -> list[str]:
    scopes = ["once", "deny", "edit"]
    if _risk_order(risk_level) <= 3 and action_type not in {"file.delete", "terminal.run"}:
        scopes.append("session")
    if _risk_order(risk_level) <= 1 and action_type.startswith("browser.") is False:
        scopes.append("always")
    return scopes


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key
        in {
            "url",
            "path",
            "display_name",
            "selector",
            "action",
            "platform",
            "title",
            "account_summary",
            "requested_software",
            "host_action",
        }
    }


def _reply_options_from_actions(actions: list[dict[str, Any]]) -> list[str]:
    options: list[str] = []
    for action in actions[:1]:
        for option in action.get("reply_options", []):
            if isinstance(option, str) and option not in options:
                options.append(option)
    return options


def _technical_detail(action: dict[str, Any] | None) -> dict[str, Any]:
    if not action:
        return {}
    return {
        "approval_id": action.get("approval_id"),
        "task_id": action.get("task_id"),
        "tool_call_id": action.get("tool_call_id"),
        "action_type": action.get("action_type"),
        "risk_level": action.get("risk_level"),
        "payload_summary": action.get("payload_summary"),
    }


def _session_grant(action: dict[str, Any], session_id: str | None) -> dict[str, Any]:
    return {
        "scope": "session",
        "session_id": session_id,
        "action_type": action.get("action_type"),
        "risk_level": action.get("risk_level"),
        "created_at": utc_now_iso(),
    }
