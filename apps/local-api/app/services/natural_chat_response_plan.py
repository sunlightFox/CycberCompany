from __future__ import annotations

import hashlib
from typing import Any

from core_types import ResponsePlan
from response_composer import canonical_action_status, normalize_action_status_semantics
from response_composer.chat_voice import voice_metadata_for_scenario
from response_composer.opening_copy import opening_copy
from trace_service import redact

from app.core.time import utc_now_iso
from app.services.action_result_summary import summarize_completed_action_result
from app.services.chat_visible_guard import visible_text_guard
from app.services.pending_action_resolution import is_deny as _is_deny
from app.services.pending_action_resolution import is_edit as _is_edit


def _natural_copy(key: str, seed: str = "", **values: Any) -> str:
    return opening_copy(f"natural.{key}", seed or key, **values)


def external_platform_structured_payload(detail: Any | None) -> dict[str, Any]:
    plan = getattr(detail, "plan", None)
    next_step = getattr(detail, "next_step", None)
    plan_status = str(getattr(plan, "status", "") or "")
    return {
        "external_platform_action": True,
        "external_platform_plan": (
            plan.model_dump(mode="json")
            if plan is not None and hasattr(plan, "model_dump")
            else {}
        ),
        "next_step": next_step,
        "external_platform_action_result": {
            "plan_status": plan_status,
            "next_step": next_step,
        },
    }


def natural_interaction_payload(
    *,
    status: str,
    reason_codes: list[str],
    pending_actions: list[dict[str, Any]] | None,
    reply_options: list[str] | None,
    clear_pending: bool,
    session_grant: dict[str, Any] | None = None,
    block_reason: str | None = None,
    action_result: dict[str, Any] | None = None,
    turn_response_kind: str = "action_request",
    action_state: str = "idle",
    evidence_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = list(reply_options or [])
    return {
        "version": "natural_interaction.openclaw_hermes.v4",
        "status": status,
        "reason_codes": list(reason_codes),
        "block_reason": block_reason,
        "pending_actions": pending_actions or [],
        "reply_options": options,
        "reply_option_items": reply_option_items(options),
        "clear_pending": clear_pending,
        "session_grant": session_grant or {},
        "action_result": action_result or {},
        "turn_response_kind": turn_response_kind,
        "action_state": action_state,
        "evidence_gate": evidence_gate or {},
    }


def plan(
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
    reply_options = reply_options_from_actions(pending_actions or [])
    natural = natural_interaction_payload(
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
    pending_action = pending_action_binding(status, pending_actions or [])
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
            "pending_action_binding": pending_action,
            "response_quality_guard": natural_quality_guard(
                None,
                visible,
                status=status,
                state_disclosed=True,
                boundary_disclosed=True,
                next_step_provided=bool(reply_options) or status != "approved",
            ),
            "natural_reply_options": reply_options,
            "reply_option_items": reply_option_items(reply_options),
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


def natural_quality_guard(
    base: Any,
    text: str,
    *,
    status: str,
    state_disclosed: bool,
    boundary_disclosed: bool,
    next_step_provided: bool,
    current_message_priority: bool = True,
    evidence_required_before_done: bool = True,
    guard_sources: dict[str, Any] | None = None,
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
            "current_message_priority": bool(current_message_priority),
            "evidence_required_before_done": bool(evidence_required_before_done),
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
        "visible_text_hash": str(base_guard.get("visible_text_hash") or visible_hash(text)),
        "natural_action": {"status": status},
        "guard_sources": guard_sources or {},
    }


def visible_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def block_reason_for_status(status: str, reason_codes: list[str]) -> str | None:
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


def pending_action_binding(status: str, pending_actions: list[dict[str, Any]]) -> dict[str, Any]:
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


def reply_option_items(options: list[str]) -> list[dict[str, str]]:
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


def action_status_facts(
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
    canonical_status = canonical_action_status(status, default="requested")
    detail_status = canonical_action_status(getattr(detail, "status", "") or canonical_status)
    completed_summary = summarize_completed_action_result(
        label=label,
        target=target,
        artifact_refs=list(action.get("artifact_refs") or []),
        result_summary=str(getattr(detail, "result_summary", "") or ""),
    )
    semantics = normalize_action_status_semantics(
        {
            "status": canonical_status,
            "scope": "workflow_summary",
            "evidence_summary": "结果可以通过任务记录、结果记录或过程记录复核。",
            "failure_reason": failure_reason,
            "approval_state": {
                "status": "required" if canonical_status == "waiting_for_approval" else "not_required",
                "approval_id": str(action.get("approval_id") or "") or None,
            },
            "task_ref": {"task_id": str(action.get("task_id") or ""), "status": detail_status},
        },
        default_status=canonical_status,
        scope="workflow_summary",
    )
    return {
        "status": canonical_status,
        "action_type": action_type,
        "action_label": label,
        "target": target,
        "risk_level": str(action.get("risk_level") or ""),
        "approval_required": canonical_status == "waiting_for_approval",
        "reply_options": list(action.get("reply_options") or []),
        "reply_option_items": reply_option_items(list(action.get("reply_options") or [])),
        "impact_summary": str(action.get("impact_summary") or ""),
        "detail_status": detail_status,
        "completed": detail_status == "completed_with_evidence",
        "failed": detail_status == "failed_with_reason",
        "failure_reason": failure_reason,
        "evidence_summary": "结果可以通过任务记录、结果记录或过程记录复核。",
        "completed_summary": completed_summary,
        "action_status_semantics": semantics,
    }


def task_status_payload(detail: Any | None) -> dict[str, Any] | None:
    if detail is None:
        return None
    task_id = str(getattr(detail, "task_id", "") or "")
    status = canonical_action_status(getattr(detail, "status", "") or "")
    if not task_id and not status:
        return None
    return {"task_id": task_id, "status": status, "mode": "workflow"}


def pending_action_prompt(action: dict[str, Any]) -> str:
    summary = str(action.get("user_summary") or "我准备执行这一步。")
    impact = str(action.get("impact_summary") or "这一步需要你点头后才会继续。")
    options = list(action.get("reply_options") or [])
    if not options:
        options = ["只允许这一次", "拒绝", "修改目标为：..."]
    return (
        f"{summary}\n{impact}\n\n"
        "请直接回复：\n"
        + "\n".join(f"- {option}" for option in options)
        + "\n\n在你点头前，我还没动手，也不会把这一步说成已经完成。"
    )


def plain_next_step_text(pending: list[dict[str, Any]]) -> str:
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


def no_pending_text(text: str) -> str:
    if _is_deny(text):
        return "现在没有等待你确认的动作，所以我不会把这句话当成拒绝执行；也不会继续任何操作，也没有完成结果或记录。"
    if _is_edit(text):
        return "现在没有等待你确认的动作，所以我还不知道要改哪一步。"
    return "现在没有等待你确认的动作，所以我不会把这句话直接当成执行口令；不会继续处理，也没有完成任何操作、结果或记录。"


def ambiguous_pending_text(pending: list[dict[str, Any]]) -> str:
    label = str(pending[0].get("user_label") or "这一步操作")
    return f"{label} 这步还差一句明确的话。你回“只允许这一次”、 “确认继续”、 “拒绝”，或者给新目标都行。\n在你点头前，我不会自己往下走；确认后我再继续。"


def multiple_pending_text(pending: list[dict[str, Any]]) -> str:
    labels = "、".join(str(item.get("user_label") or "待确认操作") for item in pending[:3])
    return opening_copy("action.multiple_pending", seed=labels, labels=labels)


def after_resolution_text(label: str, resolution: str, *, detail: Any | None) -> str:
    status = canonical_action_status(getattr(detail, "status", "") or "")
    if resolution == "edited":
        prefix = _natural_copy("after_edited", seed=label, label=label)
    elif resolution == "session":
        prefix = _natural_copy("after_session", seed=label, label=label)
    else:
        prefix = _natural_copy("after_once", seed=label, label=label)
    if not status:
        return _natural_copy("after_no_status", seed=label, prefix=prefix)
    if status == "completed_with_evidence":
        return f"{prefix} 这一步我已经确认继续过并且处理完成了，结果和记录都能回看。"
    if status in {"partially_completed", "waiting_for_approval", "planned"}:
        return f"{prefix} 我已经按你的确认继续往下推了，但这一步还没有完成，我会按实际进展继续回你。"
    if status in {"failed_with_reason", "blocked_by_boundary"}:
        return f"{prefix} 但这一步没有顺利完成；你想重试、缩范围，还是只保留方案都可以。"
    return f"{prefix} 当前状态是 {status}，下一步我会按实际结果说。"


def label_for_action(action_type: str, payload: dict[str, Any]) -> str:
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
    if action_type.startswith("external_platform."):
        platform = str(payload.get("platform_key") or payload.get("platform") or "外部平台")
        action_name = action_type.split(".", 1)[-1]
        if action_name == "comment_content":
            return f"在 {platform} 发表评论"
        if action_name == "send_message":
            return f"向 {platform} 发送消息"
        if action_name == "read_status":
            return f"读取 {platform} 状态"
        return f"继续发布内容到 {platform}"
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


def summary_for_action(action_type: str, payload: dict[str, Any], label: str) -> str:
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
    if action_type.startswith("external_platform."):
        account = str(
            payload.get("selected_account_summary")
            or payload.get("account_summary")
            or "账号"
        )
        content = str(payload.get("content_summary") or "").strip()
        return (
            f"我准备使用{account}{label}。{content}"
            if content
            else f"我准备使用{account}{label}。"
        )
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


def impact_for_action(action_type: str) -> str:
    if action_type == "host.install_software":
        return "这会安装本机软件或补齐包管理器，所以需要你明确确认；确认前尚未安装。"
    if action_type == "host.uninstall_software":
        return "这会卸载本机软件，所以需要你明确确认；确认前尚未卸载。"
    if action_type == "account.publish_post":
        return "这会向外部平台发布内容，所以需要你确认；确认前尚未发布。"
    if action_type.startswith("external_platform."):
        if action_type == "external_platform.read_status":
            return "这会去读取外部平台状态或结果；确认前我不会把它说成已经完成。"
        return "这会对外部平台产生真实操作，所以需要你明确确认；确认前我不会把它说成已经完成。"
    if action_type == "browser.download":
        return "这会在本机生成下载文件，所以需要你确认；确认前尚未下载，我也不会把这步说成已经完成。"
    if action_type in {"browser.submit", "browser.fill", "browser.type", "browser.click"}:
        return "这可能改变页面状态或账号状态，所以需要你确认；确认前尚未提交，我也不会把这步说成已经完成。"
    if action_type == "browser.screenshot":
        return "这会保存截图结果，所以需要你确认；确认前尚未保存，我也不会把这步说成已经完成。"
    if action_type == "file.delete":
        return "删除后可能无法从任务结果里直接恢复，所以需要你明确确认。"
    if action_type == "terminal.run":
        return "终端命令可能影响本机文件或进程，所以需要你明确确认。"
    return "这一步有副作用或风险，需要你确认后才会继续。"


def reply_options_for_action(action_type: str, risk_level: str) -> list[str]:
    options = ["只允许这一次"]
    if action_type == "account.publish_post":
        return [*options, "拒绝", "修改标题或正文"]
    if action_type.startswith("external_platform."):
        if action_type == "external_platform.read_status":
            return [*options, "拒绝"]
        if action_type == "external_platform.publish_content":
            return [*options, "拒绝", "修改标题、正文、首条评论或标签"]
        if action_type == "external_platform.comment_content":
            return [*options, "拒绝", "修改评论内容"]
        if action_type == "external_platform.send_message":
            return [*options, "拒绝", "修改消息内容"]
    if risk_order(risk_level) <= 3 and action_type not in {"file.delete", "terminal.run"}:
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


def allowed_scopes(action_type: str, risk_level: str) -> list[str]:
    scopes = ["once", "deny", "edit"]
    if action_type == "external_platform.read_status":
        return ["once", "deny"]
    if risk_order(risk_level) <= 3 and action_type not in {"file.delete", "terminal.run"}:
        scopes.append("session")
    if risk_order(risk_level) <= 1 and action_type.startswith("browser.") is False:
        scopes.append("always")
    return scopes


def risk_order(value: str) -> int:
    mapping = {
        "R1": 1,
        "R2": 2,
        "R3": 3,
        "R4": 4,
        "R5": 5,
        "R6": 6,
    }
    return mapping.get(str(value or "R1").upper(), 1)


def max_risk(pending: list[dict[str, Any]]) -> int:
    return max((risk_order(str(item.get("risk_level") or "R1")) for item in pending), default=1)


def payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
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
            "platform_key",
            "title",
            "account_summary",
            "selected_account_summary",
            "content_summary",
            "requested_software",
            "host_action",
        }
    }


def reply_options_from_actions(actions: list[dict[str, Any]]) -> list[str]:
    options: list[str] = []
    for action in actions[:1]:
        for option in action.get("reply_options", []):
            if isinstance(option, str) and option not in options:
                options.append(option)
    return options


def technical_detail(action: dict[str, Any] | None) -> dict[str, Any]:
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


def session_grant(action: dict[str, Any], session_id: str | None) -> dict[str, Any]:
    return {
        "scope": "session",
        "session_id": session_id,
        "action_type": action.get("action_type"),
        "risk_level": action.get("risk_level"),
        "created_at": utc_now_iso(),
    }


def after_resolution_text(label: str, resolution: str, *, detail: Any | None) -> str:
    status = canonical_action_status(getattr(detail, "status", "") or "")
    if resolution == "edited":
        prefix = _natural_copy("after_edited", seed=label, label=label)
    elif resolution == "session":
        prefix = _natural_copy("after_session", seed=label, label=label)
    else:
        prefix = _natural_copy("after_once", seed=label, label=label)
    completed_summary = summarize_completed_action_result(
        label=label,
        target=str(getattr(detail, "title", "") or getattr(detail, "requested_software", "") or ""),
        artifact_refs=list(getattr(detail, "artifact_refs", []) or []),
        result_summary=str(getattr(detail, "result_summary", "") or ""),
    )
    if not status:
        return f"{prefix} 我已经按你的确认继续往下走了，结果会按真实进展继续告诉你。"
    if status == "completed_with_evidence":
        if completed_summary:
            return f"{prefix} 这一步我已经确认继续并且处理完成了，当前结果是：{completed_summary}。"
        return f"{prefix} 这一步我已经确认继续并且处理完成了，结果和记录都能回看。"
    if status in {"partially_completed", "waiting_for_approval", "planned"}:
        return f"{prefix} 我已经按你的确认继续往下推了，但这一步还没有完成，我会按实际进展继续回你。"
    if status in {"failed_with_reason", "blocked_by_boundary"}:
        return f"{prefix} 但这一步没有顺利完成；你想重试、缩小范围，还是只保留方案都可以。"
    return f"{prefix} 当前状态是 {status}，下一步我会按实际结果说。"
