from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError
from app.db.repositories.chat_repo import ChatRepository
from app.services.approvals import ApprovalService
from app.services.chat_pending_state import (
    active_pending_approval_actions,
    explicit_pending_approval_actions,
)
from app.services.pending_action_resolution import (
    asks_how_to_confirm,
    edit_payload_for_action,
    hard_block_reason,
    is_always_allow,
    is_ambiguous_continue,
    is_confirm,
    is_deny,
    is_edit,
    is_session_allow,
    looks_like_new_action_request,
    looks_like_resolution,
)


def _plain_confirm(text: str) -> bool:
    raw = str(text or "").strip()
    compact = "".join(ch for ch in raw if ch not in " \t\r\n，,。?!！？；;:：~")
    return raw in {"确认", "同意", "允许", "只允许这一次", "本次允许"} or any(
        marker in raw for marker in ("确认下载", "确认这次", "确认本次", "确认继续", "确认执行", "只允许这一次")
    ) or compact in {"确认下载这个CSV", "只允许这一次"}


@dataclass(frozen=True)
class SessionRuntimeDecision:
    decision_type: str
    session_state: str
    target_action_id: str | None = None
    resolution_kind: str | None = None
    resume_kind: str | None = None
    should_execute: bool = False
    clear_pending: bool = False
    requires_clarification: bool = False
    reason_codes: list[str] = field(default_factory=list)
    pending_actions: list[dict[str, Any]] = field(default_factory=list)
    edited_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ResumeDispatchResult:
    status: str
    resume_target: str
    task_id: str | None = None
    approval_id: str | None = None
    pending_action_ids: list[str] = field(default_factory=list)
    result_payload: dict[str, Any] = field(default_factory=dict)
    visible_reply_hint: str | None = None
    trace_metadata: dict[str, Any] = field(default_factory=dict)


class ChatSessionRuntime:
    def __init__(self, *, chat_repo: ChatRepository) -> None:
        self._chat_repo = chat_repo

    async def pending_actions(
        self,
        conversation_id: str,
        session_id: str | None,
        *,
        user_text: str | None = None,
    ) -> list[dict[str, Any]]:
        state = await self._chat_repo.get_working_state(conversation_id)
        pending = active_pending_approval_actions(state, session_id=session_id)
        if pending:
            return pending
        confirmation = dict((state or {}).get("pending_confirmation") or {})
        if confirmation:
            pending_session = str(confirmation.get("session_id") or "")
            if not (session_id and pending_session and pending_session != str(session_id)):
                actions = [
                    dict(item)
                    for item in confirmation.get("actions") or []
                    if isinstance(item, dict) and item.get("approval_id")
                ]
                if actions:
                    return actions
        if user_text:
            return explicit_pending_approval_actions(state, user_text=user_text)
        return []

    async def decide(
        self,
        *,
        conversation_id: str,
        session_id: str | None,
        user_text: str,
    ) -> SessionRuntimeDecision:
        text = user_text.strip()
        if not text:
            return SessionRuntimeDecision(
                decision_type="idle",
                session_state="idle",
            )

        pending = await self.pending_actions(
            conversation_id,
            session_id,
            user_text=text,
        )
        resolution_signal = looks_like_resolution(text) or _plain_confirm(text)
        ambiguous_continue = is_ambiguous_continue(text)
        text_is_new_action = looks_like_new_action_request(text)
        external_resume_signal = (
            resolution_signal
            or ambiguous_continue
            or "已登录" in text
            or "登录好了" in text
            or "继续" in text
        )

        if not pending:
            if external_resume_signal:
                return SessionRuntimeDecision(
                    decision_type="probe_external_resume",
                    session_state="ready_to_resume",
                    resume_kind="external_platform",
                    should_execute=True,
                    reason_codes=["external_resume_probe"],
                )
            return SessionRuntimeDecision(
                decision_type="idle",
                session_state="idle",
            )

        if text_is_new_action and not resolution_signal:
            return SessionRuntimeDecision(
                decision_type="new_action_request",
                session_state="waiting_resolution",
                pending_actions=pending,
                reason_codes=["new_action_request_supersedes_pending"],
            )

        blocked_reason = hard_block_reason(text)
        if blocked_reason:
            return SessionRuntimeDecision(
                decision_type="hard_block",
                session_state="blocked",
                clear_pending=False,
                pending_actions=pending,
                reason_codes=[blocked_reason],
            )

        if asks_how_to_confirm(text):
            return SessionRuntimeDecision(
                decision_type="plain_next_step",
                session_state="waiting_resolution",
                clear_pending=False,
                pending_actions=pending,
                reason_codes=["plain_next_step_requested"],
            )

        if ambiguous_continue and (len(pending) > 1 or _max_risk(pending) >= 3):
            return SessionRuntimeDecision(
                decision_type="blocked",
                session_state="blocked",
                clear_pending=False,
                requires_clarification=True,
                pending_actions=pending,
                reason_codes=["ambiguous_confirmation_blocked"],
            )

        if not resolution_signal and not ambiguous_continue:
            return SessionRuntimeDecision(
                decision_type="idle",
                session_state="waiting_resolution",
                pending_actions=pending,
            )

        if len(pending) > 1:
            return SessionRuntimeDecision(
                decision_type="blocked",
                session_state="blocked",
                clear_pending=False,
                requires_clarification=True,
                pending_actions=pending,
                reason_codes=["multiple_pending_actions"],
            )

        action = pending[0]
        risk_level = str(action.get("risk_level") or "R1")
        if is_always_allow(text) and _risk_order(risk_level) >= 3:
            return SessionRuntimeDecision(
                decision_type="blocked",
                session_state="blocked",
                clear_pending=False,
                pending_actions=pending,
                reason_codes=["always_denied_for_risk"],
            )

        resolution_kind = "once"
        if is_deny(text):
            resolution_kind = "deny"
        elif is_edit(text):
            resolution_kind = "edit"
        elif is_session_allow(text):
            resolution_kind = "session"

        return SessionRuntimeDecision(
            decision_type="resolve_pending",
            session_state="ready_to_resume",
            target_action_id=str(action.get("pending_action_id") or "") or None,
            resolution_kind=resolution_kind,
            resume_kind="pending_action",
            should_execute=True,
            clear_pending=resolution_kind in {"once", "session", "deny", "edit"},
            pending_actions=pending,
            reason_codes=[f"natural_language_{resolution_kind}"],
            edited_payload=edit_payload_for_action(action, text) if resolution_kind == "edit" else None,
        )


class ChatSessionResumeDispatcher:
    def __init__(
        self,
        *,
        approval_service: ApprovalService,
        task_engine: Any | None,
        host_install_service: Any | None = None,
        external_platform_action_service: Any | None = None,
        external_platform_adapter_service: Any | None = None,
    ) -> None:
        self._approvals = approval_service
        self._task_engine = task_engine
        self._host_installs = host_install_service
        self._external_platform_actions = external_platform_action_service
        self._external_platform_adapters = external_platform_adapter_service

    async def dispatch_pending(
        self,
        *,
        action: dict[str, Any],
        resolution: str,
        trace_id: str | None,
        session_id: str | None,
        edited_payload: dict[str, Any] | None = None,
    ) -> ResumeDispatchResult:
        approval_id = str(action.get("approval_id") or "")
        label = str(action.get("user_label") or action.get("action_label") or "这一步操作")
        if not approval_id:
            return ResumeDispatchResult(
                status="blocked",
                resume_target="pending_action",
                task_id=str(action.get("task_id") or "") or None,
                pending_action_ids=_pending_action_ids([action]),
                result_payload={"action": action, "reason_codes": ["missing_approval_ref"]},
                visible_reply_hint=label,
                trace_metadata={"reason_codes": ["missing_approval_ref"]},
            )
        try:
            detail = None
            if resolution == "deny":
                await self._approvals.deny(
                    approval_id,
                    actor_type="user",
                    actor_id="user_local_owner",
                    reason="natural_language_deny",
                    trace_id=trace_id,
                )
                if self._is_external_platform_action(action):
                    approval = await self._approvals.get(approval_id)
                    detail = await self._external_platform_actions.continue_after_approval(
                        approval,
                        adapter_service=self._external_platform_adapters,
                        trace_id=trace_id,
                    )
                elif self._task_engine is not None:
                    await self._task_engine.handle_approval_resolved(
                        approval_id,
                        trace_id=trace_id,
                    )
                return ResumeDispatchResult(
                    status="denied",
                    resume_target="pending_action",
                    task_id=str(action.get("task_id") or "") or None,
                    approval_id=approval_id,
                    pending_action_ids=_pending_action_ids([action]),
                    result_payload={"action": action, "detail": detail},
                    visible_reply_hint=label,
                    trace_metadata={"reason_codes": ["natural_language_deny"]},
                )

            if resolution == "edit":
                if edited_payload is None:
                    return ResumeDispatchResult(
                        status="blocked",
                        resume_target="pending_action",
                        task_id=str(action.get("task_id") or "") or None,
                        approval_id=approval_id,
                        pending_action_ids=_pending_action_ids([action]),
                        result_payload={"action": action, "reason_codes": ["edit_missing_target"]},
                        visible_reply_hint=label,
                        trace_metadata={"reason_codes": ["edit_missing_target"]},
                    )
                await self._approvals.edit(
                    approval_id,
                    actor_type="user",
                    actor_id="user_local_owner",
                    reason="natural_language_edit",
                    edited_payload=edited_payload,
                    trace_id=trace_id,
                )
                if self._is_external_platform_action(action):
                    approval = await self._approvals.get(approval_id)
                    detail = await self._external_platform_actions.continue_after_approval(
                        approval,
                        adapter_service=self._external_platform_adapters,
                        trace_id=trace_id,
                    )
                elif self._task_engine is not None:
                    detail = await self._task_engine.handle_approval_resolved(
                        approval_id,
                        trace_id=trace_id,
                    )
                return ResumeDispatchResult(
                    status="edited",
                    resume_target="pending_action",
                    task_id=str(action.get("task_id") or "") or None,
                    approval_id=approval_id,
                    pending_action_ids=_pending_action_ids([action]),
                    result_payload={"action": action, "detail": detail, "edited_payload": edited_payload},
                    visible_reply_hint=label,
                    trace_metadata={"reason_codes": ["natural_language_edit"]},
                )

            await self._approvals.approve(
                approval_id,
                actor_type="user",
                actor_id="user_local_owner",
                reason=f"natural_language_{resolution}",
                trace_id=trace_id,
            )
            host_execution = None
            if self._host_installs is not None:
                host_execution = await self._host_installs.execute_for_approval(
                    approval_id,
                    trace_id=trace_id,
                )
            if host_execution is not None and self._task_engine is not None:
                detail = await self._task_engine.detail(host_execution.task_id)
            elif self._is_external_platform_action(action):
                approval = await self._approvals.get(approval_id)
                detail = await self._external_platform_actions.continue_after_approval(
                    approval,
                    adapter_service=self._external_platform_adapters,
                    trace_id=trace_id,
                )
            elif self._task_engine is not None:
                detail = await self._task_engine.handle_approval_resolved(
                    approval_id,
                    trace_id=trace_id,
                )
            return ResumeDispatchResult(
                status="approved",
                resume_target="pending_action",
                task_id=str(action.get("task_id") or "") or None,
                approval_id=approval_id,
                pending_action_ids=_pending_action_ids([action]),
                result_payload={
                    "action": action,
                    "detail": detail,
                    "session_grant": (
                        {
                            "scope": "session",
                            "session_id": session_id,
                            "action_type": action.get("action_type"),
                        }
                        if resolution == "session"
                        else None
                    ),
                },
                visible_reply_hint=label,
                trace_metadata={"reason_codes": [f"natural_language_{resolution}"]},
            )
        except AppError as exc:
            error_code = getattr(exc.code, "value", str(exc.code))
            return ResumeDispatchResult(
                status="blocked",
                resume_target="pending_action",
                task_id=str(action.get("task_id") or "") or None,
                approval_id=approval_id or None,
                pending_action_ids=_pending_action_ids([action]),
                result_payload={
                    "action": action,
                    "failure_reason": exc.message,
                    "error_code": error_code,
                },
                visible_reply_hint=label,
                trace_metadata={"reason_codes": ["resolution_failed", error_code]},
            )

    async def dispatch_external_resume(
        self,
        *,
        conversation_id: str,
        text: str,
        trace_id: str | None,
    ) -> ResumeDispatchResult:
        if self._external_platform_actions is None:
            return ResumeDispatchResult(
                status="not_handled",
                resume_target="external_platform",
            )
        plan, status = await self._external_platform_actions.find_chat_resumable_plan(
            conversation_id=conversation_id,
        )
        if status == "multiple":
            return ResumeDispatchResult(
                status="blocked",
                resume_target="external_platform",
                result_payload={"reason_codes": ["multiple_pending_actions", "external_platform_multiple_resumable"]},
                visible_reply_hint="多个外部平台操作待继续",
                trace_metadata={
                    "reason_codes": [
                        "multiple_pending_actions",
                        "external_platform_multiple_resumable",
                    ]
                },
            )
        if plan is None:
            return ResumeDispatchResult(
                status="not_handled",
                resume_target="external_platform",
            )
        if is_deny(text) and plan.approval_id:
            await self._approvals.deny(
                str(plan.approval_id),
                actor_type="user",
                actor_id="user_local_owner",
                reason="natural_language_deny_resumable_external_platform",
                trace_id=trace_id,
            )
            detail = await self._external_platform_actions.continue_after_approval(
                await self._approvals.get(str(plan.approval_id)),
                adapter_service=self._external_platform_adapters,
                trace_id=trace_id,
            )
            return ResumeDispatchResult(
                status="denied",
                resume_target="external_platform",
                approval_id=str(plan.approval_id),
                result_payload={"detail": detail, "plan": plan},
                visible_reply_hint="已取消这项外部平台操作。",
                trace_metadata={"reason_codes": ["natural_language_deny"]},
            )
        if plan.status == "awaiting_approval" and plan.approval_id and (
            is_confirm(text) or _plain_confirm(text) or is_session_allow(text) or is_ambiguous_continue(text)
        ):
            await self._approvals.approve(
                str(plan.approval_id),
                actor_type="user",
                actor_id="user_local_owner",
                reason="natural_language_approve_resumable_external_platform",
                trace_id=trace_id,
            )
            detail = await self._external_platform_actions.continue_after_approval(
                await self._approvals.get(str(plan.approval_id)),
                adapter_service=self._external_platform_adapters,
                trace_id=trace_id,
            )
            return ResumeDispatchResult(
                status="approved",
                resume_target="external_platform",
                approval_id=str(plan.approval_id),
                result_payload={"detail": detail, "plan": plan},
                visible_reply_hint="我已经继续这项外部平台操作。",
                trace_metadata={"reason_codes": ["natural_language_once"]},
            )
        if plan.status == "awaiting_human" and (
            is_confirm(text) or _plain_confirm(text) or is_ambiguous_continue(text) or "已登录" in text or "继续" in text
        ):
            detail = await self._external_platform_actions.resume_from_chat(
                plan=plan,
                text=text,
                adapter_service=self._external_platform_adapters,
                trace_id=trace_id,
            )
            return ResumeDispatchResult(
                status="approved",
                resume_target="external_platform",
                approval_id=str(getattr(plan, "approval_id", "") or "") or None,
                result_payload={"detail": detail, "plan": plan},
                visible_reply_hint="我已经继续这项外部平台操作。",
                trace_metadata={"reason_codes": ["external_platform_chat_resume"]},
            )
        return ResumeDispatchResult(
            status="not_handled",
            resume_target="external_platform",
            approval_id=str(getattr(plan, "approval_id", "") or "") or None,
            result_payload={"plan": plan},
        )

    def _is_external_platform_action(self, action: dict[str, Any]) -> bool:
        return (
            self._external_platform_actions is not None
            and str(action.get("action_type") or "").startswith("external_platform.")
        )


def _risk_order(risk_level: str) -> int:
    try:
        return int(str(risk_level).replace("R", ""))
    except ValueError:
        return 1


def _max_risk(actions: list[dict[str, Any]]) -> int:
    return max((_risk_order(str(item.get("risk_level") or "R1")) for item in actions), default=1)


def _pending_action_ids(actions: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("pending_action_id") or "")
        for item in actions
        if str(item.get("pending_action_id") or "")
    ]
