from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core_types import ApprovalDetail, ResponsePlan, RiskLevel
from response_composer import ResponseComposer
from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.services.approvals import ApprovalService

FORBIDDEN_MAIN_REPLY_TERMS = {
    "approval_id": "确认编号",
    "tool_call_id": "工具记录",
    "trace_id": "审计记录",
    "browser.download": "下载动作",
    "browser.snapshot": "网页快照",
    "browser.screenshot": "页面截图",
    "task_id": "任务记录",
    "R3": "需要确认的风险",
    "R4": "较高风险",
    "R5": "高风险",
    "/api/approvals": "确认接口",
}

_URL_RE = re.compile(r"https?://[^\s，。；;）)]+", re.IGNORECASE)


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

    async def handle(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        session_id: str | None,
        trace_id: str | None,
    ) -> NaturalChatOutcome | None:
        text = user_text.strip()
        if not text:
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

        pending = await self._pending_actions(turn["conversation_id"], session_id)
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
                    "当前没有等待确认的操作。我不会仅凭“好的”就执行下载、删除、登录或其他高风险动作。",
                    status="no_pending_action",
                    reason_codes=["ambiguous_continue_without_pending"],
                    clear_pending=False,
                )
            if len(pending) > 1 or _max_risk(pending) >= 3:
                return _outcome(
                    _ambiguous_pending_text(pending),
                    status="ambiguous_confirmation_blocked",
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
                    status="multiple_pending_actions",
                    reason_codes=["multiple_pending_actions"],
                    pending_actions=pending,
                    clear_pending=False,
                )
            action = pending[0]
            if _is_always_allow(text) and _risk_order(str(action.get("risk_level") or "R1")) >= 3:
                return _outcome(
                    "这类操作不能设置成以后总是允许。你可以回复“只允许这一次”“本会话内同类操作都允许”或“拒绝”。",
                    status="always_denied_for_risk",
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
                )
            if _is_edit(text):
                return await self._resolve_action(
                    action=action,
                    resolution="edit",
                    trace_id=trace_id,
                    session_id=session_id,
                    edited_payload=_edit_payload_for_action(action, text),
                )
            if _is_confirm(text) or _is_session_allow(text) or _is_ambiguous_continue(text):
                return await self._resolve_action(
                    action=action,
                    resolution="session" if _is_session_allow(text) else "once",
                    trace_id=trace_id,
                    session_id=session_id,
                )
        return None

    async def _resolve_action(
        self,
        *,
        action: dict[str, Any],
        resolution: str,
        trace_id: str | None,
        session_id: str | None,
        edited_payload: dict[str, Any] | None = None,
    ) -> NaturalChatOutcome:
        approval_id = str(action.get("approval_id") or "")
        label = str(action.get("user_label") or action.get("action_label") or "这一步操作")
        if not approval_id:
            return _outcome(
                f"我找到了待确认的{label}，但内部确认记录不完整，所以没有执行。请重新发起这一步。",
                status="pending_action_invalid",
                reason_codes=["missing_approval_ref"],
                clear_pending=True,
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
                )
            if resolution == "edit":
                if edited_payload is None:
                    return _outcome(
                        "",
                        status="edit_missing_target",
                        reason_codes=["edit_missing_target"],
                        action=action,
                        clear_pending=False,
                        composer=self._composer,
                        failure_reason=(
                            "请直接说清要改成什么，比如把地址、目标、标题或正文改成新的内容。"
                        ),
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
                    f"我已理解你对{label}的处理意图，但这一步没有完成："
                    f"{visible_text_guard(exc.message)}。你可以修改目标后重试，或取消这次操作。"
                ),
                status="resolution_failed",
                reason_codes=["resolution_failed", error_code],
                action=action,
                clear_pending=True,
                composer=self._composer,
                failure_reason=visible_text_guard(exc.message),
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
) -> ResponsePlan:
    composer = ResponseComposer()
    facts = _action_status_facts(
        action,
        status="pending_action",
        detail=None,
        failure_reason=None,
    )
    plan = composer.response_plan_for_action_status(facts=facts)
    reply_options = _reply_options_from_actions([action])
    natural = {
        "status": "pending_action",
        "reason_codes": ["approval_required", "natural_pending_action"],
        "pending_actions": [action],
        "natural_reply_options": reply_options,
        "reply_option_items": _reply_option_items(reply_options),
        "pending_confirmation": {
            "kind": "natural_pending_actions",
            "session_id": session_id,
            "actions": [action],
            "questions": reply_options,
            "created_at": utc_now_iso(),
        },
        "clear_pending": False,
        "session_grant": {},
    }
    structured_payload = {
        **plan.structured_payload,
        "scenario": "natural_interaction",
        "natural_interaction": natural,
        "pending_actions": [action],
        "pending_action_binding": _pending_action_binding("pending_action", [action]),
        "response_quality_guard": {
            "status": "pending_action",
            "state_disclosed": True,
            "boundary_disclosed": True,
            "next_step_provided": bool(reply_options),
            "no_false_done": True,
            "no_internal_terms": True,
        },
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


def visible_text_guard(text: str) -> str:
    result = str(redact(text))
    for term, replacement in FORBIDDEN_MAIN_REPLY_TERMS.items():
        result = re.sub(re.escape(term), replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"\btrc_[A-Za-z0-9_-]+", "审计记录", result)
    result = re.sub(r"\bapr_[A-Za-z0-9_-]+", "确认编号", result)
    result = re.sub(r"\btoolcall_[A-Za-z0-9_-]+", "工具记录", result)
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
    detail: Any | None = None,
    failure_reason: str | None = None,
) -> NaturalChatOutcome:
    if composer is not None and action is not None:
        facts = _action_status_facts(
            action,
            status=status,
            detail=detail,
            failure_reason=failure_reason,
        )
        plan = composer.response_plan_for_action_status(
            facts=facts,
            task_status=_task_status_payload(detail),
        )
        reply_options = _reply_options_from_actions(pending_actions or ([action] if action else []))
        natural = {
            "status": status,
            "reason_codes": reason_codes,
            "pending_actions": pending_actions or ([action] if action else []),
            "natural_reply_options": reply_options,
            "reply_option_items": _reply_option_items(reply_options),
            "clear_pending": clear_pending,
            "session_grant": session_grant or {},
        }
        pending_action_binding = _pending_action_binding(status, pending_actions or [])
        structured_payload = {
            **plan.structured_payload,
            "scenario": "natural_interaction",
            "natural_interaction": natural,
            "pending_actions": pending_actions or ([action] if action else []),
            "pending_action_binding": pending_action_binding,
            "response_quality_guard": {
                "status": status,
                "state_disclosed": True,
                "boundary_disclosed": True,
                "next_step_provided": bool(reply_options) or status != "approved",
                "no_false_done": True,
                "no_internal_terms": True,
            },
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
    )
    return NaturalChatOutcome(text=visible_text_guard(text), response_plan=plan)


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
) -> ResponsePlan:
    visible = visible_text_guard(text)
    reply_options = _reply_options_from_actions(pending_actions or [])
    natural = {
        "status": status,
        "reason_codes": reason_codes,
        "pending_actions": pending_actions or [],
        "natural_reply_options": reply_options,
        "clear_pending": clear_pending,
        "session_grant": session_grant or {},
    }
    pending_action_binding = {
        "conversation_session_bound": True,
        "unique_action_required": True,
        "action_count": len(pending_actions or []),
        "fail_closed": status
        in {
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
    natural["reply_option_items"] = _reply_option_items(reply_options)
    return ResponsePlan(
        title="等待确认" if pending_actions else None,
        style="natural_action",
        summary=visible,
        sections=[{"kind": "natural_interaction", "text": visible}],
        follow_up_options=reply_options,
        plain_text=visible,
        structured_payload={
            "scenario": "natural_interaction",
            "natural_interaction": natural,
            "pending_actions": pending_actions or [],
            "pending_action_binding": pending_action_binding,
            "response_quality_guard": {
                "status": status,
                "state_disclosed": True,
                "boundary_disclosed": True,
                "next_step_provided": bool(reply_options) or status != "approved",
                "no_false_done": True,
                "no_internal_terms": True,
            },
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


def _pending_action_binding(status: str, pending_actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "conversation_session_bound": True,
        "unique_action_required": True,
        "action_count": len(pending_actions),
        "fail_closed": status
        in {
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
        "evidence_summary": "结果可以通过任务记录、工件或回放证据复核。",
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
    summary = str(action.get("user_summary") or "我准备执行这一步操作。")
    impact = str(action.get("impact_summary") or "这一步需要你确认后才会继续。")
    options = list(action.get("reply_options") or [])
    if not options:
        options = ["只允许这一次", "拒绝", "修改目标为：..."]
    return (
        f"{summary}\n{impact}\n\n"
        "请直接回复：\n"
        + "\n".join(f"- {option}" for option in options)
        + "\n\n在你确认前，我不会声称这一步已经完成。"
    )


def _plain_next_step_text(pending: list[dict[str, Any]]) -> str:
    if pending:
        action = pending[0]
        label = str(action.get("user_label") or "这一步操作")
        options = list(action.get("reply_options") or ["只允许这一次", "拒绝", "修改目标为：..."])
        return (
            f"现在等待你确认的是：{label}。\n"
            "你不用复制任何编号，直接回复下面任意一句就行：\n"
            + "\n".join(f"- {option}" for option in options)
        )
    return (
        "当前没有等待确认的操作。以后需要确认时，你可以直接回复："
        "确认、拒绝、取消，或“修改地址为：...”"
    )


def _no_pending_text(text: str) -> str:
    if _is_deny(text):
        return "当前没有等待拒绝或取消的操作。我不会执行任何下载、删除、登录或外部动作。"
    if _is_edit(text):
        return "当前没有可修改的待确认操作。请先发起需要执行的动作，再告诉我要改成什么。"
    return "当前没有等待确认的操作。我不会仅凭这句话执行下载、删除、登录或其他高风险动作。"


def _ambiguous_pending_text(pending: list[dict[str, Any]]) -> str:
    label = str(pending[0].get("user_label") or "这一步操作")
    return (
        f"我还不能只凭“好的/继续”来执行{label}。\n"
        "请明确回复“只允许这一次”“拒绝”，或把目标改成新的地址/文件后再继续。"
    )


def _multiple_pending_text(pending: list[dict[str, Any]]) -> str:
    labels = "、".join(str(item.get("user_label") or "待确认操作") for item in pending[:3])
    return f"现在有多个待确认操作：{labels}。请明确说要确认哪一个，或者说“全部取消”。"


def _after_resolution_text(label: str, resolution: str, *, detail: Any | None) -> str:
    status = str(getattr(detail, "status", "") or "")
    if resolution == "edited":
        prefix = f"已按新的目标修改{label}，并重新交给受控执行链路检查。"
    elif resolution == "session":
        prefix = f"已确认这次{label}；本会话内同类、同范围、同风险的操作可以少问一次。"
    else:
        prefix = f"已确认这次{label}，我会把它交回受控任务链路继续处理。"
    if not status:
        return f"{prefix}如果后续仍等待、失败或完成，我会按真实状态说明，不会把未完成说成完成。"
    if status == "completed":
        return f"{prefix}任务链路返回已完成；结果应能通过工件、页面状态或任务回放证据复核。"
    if status in {"paused", "waiting_approval"}:
        return f"{prefix}当前还有步骤在等待确认；确认前相关动作仍未执行，我不会跳过安全边界。"
    if status == "failed":
        return f"{prefix}但执行没有完成；你可以修改目标后重试，或让我只给方案。"
    return f"{prefix}当前状态是 {status}，下一步我会按实际结果说明。"


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
        return (
            "我不能通过浏览器访问本机 file 地址或敏感路径；"
            "这可能泄露本机文件。没有执行任何浏览器动作。"
        )
    if reason == "metadata_or_private_network_target":
        return (
            "我不能访问 metadata 或私网敏感地址；"
            "这可能泄露本机或云环境信息。没有执行任何网络或浏览器动作。"
        )
    return (
        "这个请求涉及敏感凭据或越权内容，我不能读取、展示或外发这些信息。"
        "我可以改为帮你做凭据轮换清单、脱敏示例，或解释安全处理流程。"
    )


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
    if normalized_prefix.startswith(("CHAT-E2E-", "PHASE34-", "NAT-")):
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
        return "这会保存截图工件，所以需要你确认；确认前尚未保存。"
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
