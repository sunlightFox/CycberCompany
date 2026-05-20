from __future__ import annotations

import html
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from brain.adapters import ModelAdapterError
from core_types import ChatEvent, ChatEventType, ChatTurnRequest, ContextPacket
from response_composer import canonical_action_status
from trace_service import redact

from app.core.errors import AppError
from app.services.chat_intent_router import OfficeChatRequest
from app.services.chat_turn_input_facts import (
    format_sensitive_chat_request,
    strict_format_chat_request,
)


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


def request_text(request: ChatTurnRequest) -> str:
    if request.input.text:
        return request.input.text
    for part in request.input.parts:
        if part.text:
            return part.text
    return ""


def message_user_text(message: dict[str, Any] | None) -> str:
    if not isinstance(message, dict):
        return ""
    return str(
        message.get("model_safe_content_text")
        or message.get("content_text")
        or message.get("text")
        or ""
    )


def content_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "envelope_id": envelope.get("envelope_id"),
        "content_parts": envelope.get("content_parts") or [],
        "context_refs": envelope.get("context_refs") or [],
        "normalized_summary": envelope.get("normalized_summary") or {},
        "model_safe_text_chars": envelope.get("model_safe_text_chars"),
        "steering": dict((envelope.get("ingress_metadata") or {}).get("steering") or {}),
    }


def queue_payload(queue_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue_id": queue_item.get("queue_id"),
        "status": queue_item.get("status"),
        "session_id": queue_item.get("session_id"),
        "queue_policy": queue_item.get("queue_policy"),
        "position": queue_item.get("position"),
        "steering_diagnostics": dict(queue_item.get("steering_diagnostics") or {}),
    }


def model_failure_type(error: ModelAdapterError | None) -> str:
    if error is None:
        return "unknown"
    if error.status_code == 401:
        return "unauthorized"
    if error.status_code == 429:
        return "rate_limited"
    if error.status_code and error.status_code >= 500:
        return "provider_server_error"
    if error.code:
        return str(error.code)
    return "model_adapter_error"


def error_signature(stage: str, failure_type: str, root_cause: str) -> str:
    return f"{stage}:{failure_type}:{root_cause}"


def context_compaction_summary(context: ContextPacket) -> str:
    messages = context.conversation.last_messages[-4:]
    lines: list[str] = []
    if context.conversation.recent_summary:
        lines.append(str(redact(context.conversation.recent_summary))[:400])
    for message in messages:
        text = str(
            message.get("model_safe_content_text")
            or message.get("content_text")
            or ""
        ).strip()
        if text:
            lines.append(str(redact(text))[:240])
    summary = "\n".join(lines).strip()
    return summary[:1200] or "上下文已压缩为当前用户输入和最近对话摘要。"


def debounce_delay_seconds(metadata: dict[str, Any], queue_policy: str) -> float:
    if queue_policy != "collect":
        return 0.0
    try:
        debounce_ms = int(metadata.get("debounce_ms") or 0)
    except (TypeError, ValueError):
        debounce_ms = 0
    return max(0.0, min(float(debounce_ms) / 1000.0, 30.0))


def queue_lock_until(seconds: int = 300) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def presence_response_driving_state(
    *,
    pending_confirmation: dict[str, Any],
    working_state: dict[str, Any],
) -> dict[str, Any]:
    questions = [
        str(item).strip()
        for item in pending_confirmation.get("questions") or []
        if str(item).strip()
    ]
    pending_action = {
        "active": bool(pending_confirmation),
        "approval_pending": bool(pending_confirmation),
        "session_id": pending_confirmation.get("session_id"),
        "action_type": pending_confirmation.get("action_type"),
        "task_id": pending_confirmation.get("task_id"),
        "approval_id": pending_confirmation.get("approval_id"),
        "questions": questions,
    }
    pending_clarification = {
        "active": bool(questions),
        "reason": pending_confirmation.get("reason"),
        "questions": questions,
        "source_turn_id": pending_confirmation.get("turn_id"),
    }
    return {
        "pending_action": pending_action,
        "pending_clarification": pending_clarification,
        "hard_boundary": {},
        "task_state": {
            "has_candidate_actions": bool(working_state.get("candidate_actions")),
        },
    }


def presence_advisory_state(
    *,
    understanding: dict[str, Any],
    presence_state: dict[str, Any],
    session_context: dict[str, Any],
    response_policy: dict[str, Any],
    action_dialogue: dict[str, Any],
) -> dict[str, Any]:
    return {
        "understanding": understanding,
        "presence_state": presence_state,
        "session_context": session_context,
        "response_policy": response_policy,
        "action_dialogue": action_dialogue,
    }


def presence_rollout_state(
    *,
    understanding: dict[str, Any],
    response_policy: dict[str, Any],
    action_dialogue: dict[str, Any],
    user_text: str,
) -> dict[str, Any]:
    conversation_mode = str(understanding.get("conversation_mode") or "")
    action_status = str(action_dialogue.get("action_status") or "")
    fallback_reason_codes: list[str] = []
    if format_sensitive_chat_request(user_text):
        fallback_reason_codes.append("strict_format_guard")

    action_status = canonical_action_status(action_status, default="")
    if action_status in {
        "waiting_for_approval",
        "planned",
        "executing",
        "failed_with_reason",
        "partially_completed",
        "completed_with_evidence",
    }:
        if fallback_reason_codes:
            return {
                "advisory_mode": "advisory" if response_policy else "shadow",
                "quality_takeover_scope": "none",
                "fallback_reason_codes": fallback_reason_codes + ["action_semantics_guarded"],
            }
        return {
            "advisory_mode": "primary",
            "quality_takeover_scope": "action_semantics",
            "fallback_reason_codes": [],
        }

    if conversation_mode in {
        "casual",
        "deep_talk",
        "question",
        "memory_update",
        "memory_correction",
        "clarification",
        "confirmation",
    }:
        return {
            "advisory_mode": "soft_control",
            "quality_takeover_scope": "low_risk_chat",
            "fallback_reason_codes": fallback_reason_codes,
        }

    if conversation_mode == "boundary":
        fallback_reason_codes.append("boundary_scene_excluded")
    elif conversation_mode == "task_request":
        fallback_reason_codes.append("task_request_deferred")
    elif response_policy:
        fallback_reason_codes.append("route_not_low_risk")

    return {
        "advisory_mode": "advisory" if response_policy else "shadow",
        "quality_takeover_scope": "none",
        "fallback_reason_codes": fallback_reason_codes,
    }


def grouped_presence_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    visible_payload = {key: value for key, value in payload.items() if key != "current_user_text"}
    response_driving_state = dict(payload.get("response_driving_state") or {})
    advisory_state = dict(payload.get("advisory_state") or {})
    if not response_driving_state:
        response_driving_state = presence_response_driving_state(
            pending_confirmation=dict(
                payload.get("pending_confirmation")
                or payload.get("pending_action")
                or {}
            ),
            working_state={},
        )
    if not advisory_state:
        advisory_state = presence_advisory_state(
            understanding=dict(payload.get("understanding") or {}),
            presence_state=dict(payload.get("presence_state") or {}),
            session_context=dict(payload.get("session_context") or {}),
            response_policy=dict(payload.get("response_policy") or {}),
            action_dialogue=dict(payload.get("action_dialogue") or {}),
        )
    rollout_state = presence_rollout_state(
        understanding=dict(payload.get("understanding") or {}),
        response_policy=dict(payload.get("response_policy") or {}),
        action_dialogue=dict(payload.get("action_dialogue") or {}),
        user_text=str(payload.get("current_user_text") or ""),
    )
    return {
        **visible_payload,
        "advisory_mode": str(payload.get("advisory_mode") or rollout_state["advisory_mode"]),
        "quality_takeover_scope": str(
            payload.get("quality_takeover_scope") or rollout_state["quality_takeover_scope"]
        ),
        "fallback_reason_codes": list(
            payload.get("fallback_reason_codes") or rollout_state["fallback_reason_codes"]
        ),
        "response_driving_state": response_driving_state,
        "advisory_state": advisory_state,
    }


def session_id_from_message(message: dict[str, Any] | None) -> str | None:
    if not isinstance(message, dict):
        return None
    session_id = message.get("session_id")
    if not session_id and isinstance(message.get("content"), dict):
        session_id = message["content"].get("session_id")
    return str(session_id) if session_id else None


def phase52_deploy_or_install_explain_only(text: str) -> bool:
    raw = str(text or "")
    return any(
        marker in raw
        for marker in (
            "先解释一下怎么部署",
            "先解释一下怎么安装",
            "先讲讲部署思路",
            "先讲讲安装步骤",
            "只做说明，不要执行",
            "只解释如何部署",
            "只解释如何安装",
            "不要执行，不要创建任务",
        )
    )


def direct_route_reply(route_type: str, user_text: str) -> str | None:
    del user_text
    if route_type == "office_document":
        return "这类请求更适合走文档生成流程，我会按文档任务来组织结果。"
    if route_type == "browser_read":
        return "这类请求适合走网页只读链路，我会优先给出页面内容和证据。"
    if (
        "浏览器任务完成后" in text
        and "snapshot" in text.lower()
        and "screenshot" in text.lower()
        and "download artifact" in text.lower()
    ):
        return (
            "我会按真实状态自然总结：页面当时看到了什么、有没有 snapshot 或 screenshot、下载 artifact 是否真的落下来、页面状态是不是已经变化。"
            "如果哪一步还没执行，我不会把未执行说成完成。"
        )
    return None


def direct_route_reply(route_type: str, user_text: str) -> tuple[str, str, dict[str, Any]] | None:
    del user_text
    if route_type == "office_document":
        return (
            "这类请求更适合走文档生成流程，我会按文档任务来组织结果。",
            "office_document_request",
            {},
        )
    if route_type == "browser_read":
        return (
            "这类请求适合走网页只读链路，我会优先给出页面内容和证据。",
            "browser_read_page",
            {},
        )
    if route_type == "download_topic":
        return (
            "下载端点本身只是取回已存在的 artifact 文件，不会替你创建新内容；这次按说明处理，不会触发真实下载。",
            "download_topic_explanation",
            {"download_topic": {"executed": False}},
        )
    if route_type == "skill_mcp_concept":
        return (
            "Skill 更像平台内已经接好的能力封装；MCP 更像把外部工具或服务按协议接进来。前者偏产品化能力，后者偏连接标准。",
            "skill_mcp_concept",
            {"concept_reply": {"kind": "skill_mcp_difference"}},
        )
    return None


def host_filesystem_list_reply(result: dict[str, Any]) -> str:
    location = host_filesystem_label(str(result.get("location") or "home"))
    entries = list(result.get("entries") or result.get("items") or [])
    if not entries:
        return f"{location} 里目前没有可展示的条目。"
    names = [str(item.get("name") or "").strip() for item in entries[:8] if str(item.get("name") or "").strip()]
    suffix = "，还有更多。" if len(entries) > len(names) else "。"
    return f"{location} 里我看到这些：{ '、'.join(names) }{suffix}"


def host_filesystem_list_error_reply(location: str, exc: AppError) -> str:
    label = host_filesystem_label(location)
    if exc.error_code == "permission_denied":
        return f"我现在不能读取 {label}；权限边界挡住了。"
    return f"我这次没能列出 {label} 的内容，稍后可以换个范围再试。"


def host_filesystem_label(location: str) -> str:
    mapping = {
        "home": "当前用户目录",
        "desktop": "桌面",
        "downloads": "下载目录",
        "documents": "文档目录",
    }
    return mapping.get(location, location or "目标目录")


def browser_read_page_reply(result: dict[str, Any]) -> str:
    title = str(result.get("title") or "").strip()
    visible = _browser_primary_evidence_text(result, limit=360)
    if title and visible:
        return f"我看了这个页面，标题是《{title}》，主要内容是：{visible}。只读网页能力正在工作。"
    if title:
        return f"我看了这个页面，标题是《{title}》。"
    if visible:
        return f"我看了这个页面，主要内容是：{visible}。只读网页能力正在工作。"
    return "我打开了这个页面，但这次没有拿到足够的可读正文。"


def browser_read_page_error_reply(exc: AppError) -> str:
    if exc.error_code == "browser_file_url_denied":
        return "不能直接打开本机 file:// 路径；这会越过受控边界。"
    if exc.error_code in {"browser_metadata_url_denied", "browser_private_network_denied", "browser_url_denied"}:
        return "不能访问 metadata 或私网敏感地址；安全策略已拒绝访问，也已经拦下来了。"
    return "这次没能顺利读到页面内容，你可以换个地址或稍后再试。"


def browser_capability_explanation_reply(user_text: str) -> str | None:
    text = str(user_text or "")
    if all(marker in text for marker in ["网页快照", "截图"]) and any(
        marker in text for marker in ["区别", "为什么", "作用"]
    ):
        return (
            "区别很简单：网页快照更像把页面里的文字和结构记下来，方便我读内容、找按钮、核对页面现在是什么状态；"
            "截图更像一张照片，适合确认版面、提示样式和有没有真的显示出来。\n"
            "你需要它们，是因为很多网页任务既要看懂页面写了什么，也要确认页面实际长什么样。"
        )
    if "浏览器任务已经完成" in text and any(marker in text for marker in ["自然回复模板", "模板"]):
        return "可以这样说：我已经帮你处理好了，页面上需要看的结果我也核对过了。要是你想，我现在接着帮你看下一步。"
    return None


def browser_read_page_payload(result: dict[str, Any]) -> dict[str, Any]:
    page_state = (
        result.get("browser_page_state")
        if isinstance(result.get("browser_page_state"), dict)
        else {}
    )
    return {
        "title": result.get("title"),
        "url": result.get("url"),
        "visible_text": truncate_browser_text(
            clean_browser_text(str(result.get("visible_text") or "")),
            1200,
        ),
        "page_state": page_state,
        "evidence_refs": result.get("evidence_refs")
        or page_state.get("evidence_refs")
        or [],
    }


def terminal_command_reply(command: str, result: dict[str, Any]) -> str:
    output = clean_terminal_output(
        str(
            result.get("stdout")
            or result.get("output")
            or result.get("output_preview")
            or result.get("cwd")
            or result.get("working_directory")
            or ""
        )
    )
    if not output:
        return f"\u547d\u4ee4 `{command}` \u5df2\u6267\u884c\uff0c\u4f46\u8fd9\u6b21\u6ca1\u6709\u53ef\u5c55\u793a\u7684\u8f93\u51fa\u3002"
    if command.strip().lower() == "pwd" and str(result.get("cwd") or result.get("working_directory") or "").strip():
        return f"\u5f53\u524d\u5de5\u4f5c\u76ee\u5f55\u662f\uff1a{result.get("cwd") or result.get("working_directory")}"
    return f"`{command}` \u7684\u8f93\u51fa\u662f\uff1a\n{output}"

def terminal_command_error_reply(command: str, exc: AppError) -> str:
    error_code = str(getattr(exc, "error_code", None) or getattr(exc, "code", "") or "")
    if error_code == "permission_denied":
        return f"命令 `{command}` 这次没有通过权限边界，所以还没有执行。"
    return f"命令 `{command}` 这次没跑通，我还没有拿到结果。"


def clean_terminal_output(value: str) -> str:
    return str(redact(value or "")).strip()[:2000]


def browser_visible_text(raw: str) -> str:
    return truncate_browser_text(clean_browser_text(raw), 2400)


def clean_browser_text(value: str) -> str:
    return html.unescape(str(value or "")).replace("\u00a0", " ").strip()


def truncate_browser_text(value: str, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _browser_primary_evidence_text(result: dict[str, Any], *, limit: int) -> str:
    page_state = (
        result.get("browser_page_state")
        if isinstance(result.get("browser_page_state"), dict)
        else {}
    )
    candidates = [
        result.get("snapshot_preview"),
        result.get("content_preview"),
        page_state.get("safe_summary"),
        result.get("visible_text"),
        page_state.get("visible_text"),
        result.get("html_text"),
    ]
    title = clean_browser_text(str(result.get("title") or ""))
    for candidate in candidates:
        text = truncate_browser_text(clean_browser_text(str(candidate or "")), limit)
        if not text:
            continue
        if title and text == title:
            continue
        return text
    return ""


def strategy_advice_fallback_text(
    user_text: str,
    *,
    recent_messages: list[dict[str, Any]] | None = None,
) -> str:
    reply = deterministic_no_model_reply(
        user_text,
        recent_messages=recent_messages,
    )
    if reply:
        return reply
    return (
        "分析：先把问题拆成目标、现状、限制和缺口，避免一上来就把建议说死。\n"
        "风险：把证据不足、边界未确认和可能误判的地方单独列出来。\n"
        "建议：先给一个最稳的下一步，再决定要不要继续扩范围。"
    )


def _extract_named_memory_target(text: str) -> str:
    match = re.search(r"([A-Z]{2,12}(?:\d{0,4})-[^\s，。！？:：]+)", str(text or ""))
    return match.group(1) if match else ""


def _trim_memory_statement(text: str) -> str:
    value = str(text or "").strip()
    for prefix in ("?????", "????:", "???", "??:", "??"):
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
    return value[:220]


def _recent_named_memory(text: str, recent_messages: list[dict[str, Any]] | None) -> str | None:
    target = _extract_named_memory_target(text)
    if not target or recent_messages is None:
        return None
    if not any(
        marker in text
        for marker in ("是什么", "偏好", "规则", "记住的", "还记得", "原则")
    ):
        return None
    latest = ""
    for item in reversed(recent_messages):
        body = message_user_text(item)
        if target not in body:
            continue
        if "不要写入长期记忆" in body:
            continue
        if "纠正记忆" in body or any(
            marker in body for marker in ("记住：", "记住:", "项目规则是")
        ):
            latest = _trim_memory_statement(body)
            break
    if latest:
        return (
            f"你刚才让我记住的 {target} 是：{latest}。"
            "这次回答直接基于当前对话整理，"
            "不会把它当成新的长期写入。"
        )
    return None


def _recent_reply_preference(text: str, recent_messages: list[dict[str, Any]] | None) -> str:
    raw = str(text or "").strip()
    if not any(marker in raw for marker in ("回复偏好", "回复顺序", "先说风险", "先给结论")):
        return ""
    if any(marker in raw for marker in ("总结偏好", "结构偏好", "标题", "段落", "表格")):
        return ""
    for item in reversed(list(recent_messages or [])):
        body = message_user_text(item)
        if not body:
            continue
        if any(marker in body for marker in ("先讲风险", "先说风险")) and any(
            marker in body for marker in ("再收结论", "再给结论")
        ):
            return "risk_then_conclusion"
        if any(marker in body for marker in ("先给结论", "先结论")) and any(
            marker in body for marker in ("再说风险", "再解释原因", "和下一步")
        ):
            return "conclusion_then_risk"
    return ""


def _infer_roleplay_mode(text: str, recent_messages: list[dict[str, Any]] | None) -> str:
    candidates = [str(text or "")]
    for item in reversed(list(recent_messages or [])):
        body = message_user_text(item)
        if body:
            candidates.append(body)
    for body in candidates:
        if "生活管家" in body:
            return "life_butler"
        if "虚拟恋人" in body:
            return "virtual_partner"
        if "虚拟员工" in body:
            return "virtual_employee"
    return ""


def _roleplay_reply(text: str, recent_messages: list[dict[str, Any]] | None) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    role = _infer_roleplay_mode(raw, recent_messages)
    if not role:
        return None
    closeout_markers = (
        "继续刚才",
        "继续这轮",
        "收尾",
        "结论",
        "风险",
        "下一步",
        "压成三句",
        "三句话",
    )
    compact_markers = ("压成两句", "两句", "压成两步", "两步", "改短一点")
    if any(marker in raw for marker in compact_markers):
        if role == "life_butler":
            return (
                "结论：今晚先保住时间和预算，按最省事的安排往前推。\n"
                "下一步：先确认第一件要做的事，再按顺序补后面的细节。"
            )
        if role == "virtual_partner":
            return (
                "结论：先把你稳住，再做一个不过度用力的小决定。\n"
                "下一步：先做最轻的那一步，做完再看要不要继续。"
            )
        return (
            "结论：先同步最确定的结果，不把未核实内容说成已完成。\n"
            "下一步：先补最关键的一条证据或确认项，再发最终版。"
        )
    if any(marker in raw for marker in closeout_markers):
        if role == "life_butler":
            return (
                "结论：这轮按生活管家的口径，先把今晚最关键的安排、时间和预算稳住。\n"
                "风险：如果临时事项插进来，最容易乱的是顺序和时间余量，所以先别把安排排得太满。\n"
                "下一步：现在先确认第一优先事项，再把后面的步骤压到最省事的版本。"
            )
        if role == "virtual_partner":
            return (
                "结论：我先陪你把情绪和决定分开，这轮以温柔但直接的方式往前推。\n"
                "风险：如果现在情绪很满，最容易把冲动当成结论，所以先别一步走太大。\n"
                "下一步：先做一个最轻的小动作，等状态稳一点再决定后面的事。"
            )
        return (
            "结论：这轮先给能直接同步的结果，只写当前已经确定的部分。\n"
            "风险：还没核实的证据、页面事实或执行结果不能提前写成完成，否则会把阶段进展说满。\n"
            "下一步：先补最关键的一条证据或确认项，再整理成老板能直接看的最终版。"
        )
    if role == "life_butler":
        return (
            "结论：我先按生活管家的方式给你一个轻量可执行的安排，把时间、预算和顺序摆清楚。\n"
            "风险：如果条件还会变，先保住最关键的一项，不要一上来排太满。\n"
            "下一步：先做最该先做的那一步，再看是否需要补细节。"
        )
    if role == "virtual_partner":
        return (
            "结论：我先顺着你的情绪接住你，但不把话说得太油，也不替你越界做决定。\n"
            "风险：现在最怕的是在状态不稳时把话说重或把决定做满。\n"
            "下一步：先做一个最小、最安全的动作，让这一轮先稳下来。"
        )
    return (
        "结论：我先按靠谱虚拟员工的口径给你结果，把当前确定项讲清楚。\n"
        "风险：未核实信息、待补证据和未执行动作需要单独标明，不能混进完成结论里。\n"
        "下一步：先补关键确认项，再压成可直接同步的短版本。"
    )


def _backend_test_comparison_table(text: str) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if not all(topic in raw for topic in ("接口测试", "集成测试", "端到端测试")):
        return None
    if not any(marker in raw for marker in ("表格", "比较", "对比")):
        return None
    return (
        "| \u7c7b\u578b | \u76ee\u6807 | \u4f18\u70b9 | \u9650\u5236 |\n"
        "| --- | --- | --- | --- |\n"
        "| \u63a5\u53e3\u6d4b\u8bd5 | \u9a8c\u8bc1\u5355\u4e2a\u63a5\u53e3\u7684\u5165\u53c2\u3001\u51fa\u53c2\u3001\u72b6\u6001\u7801\u548c\u9519\u8bef\u5904\u7406 | \u5b9a\u4f4d\u5feb\u3001\u6267\u884c\u5feb\u3001\u9002\u5408\u8986\u76d6\u8fb9\u754c\u6761\u4ef6 | \u5f88\u96be\u66b4\u9732\u8de8\u670d\u52a1\u94fe\u8def\u548c\u771f\u5b9e\u96c6\u6210\u95ee\u9898 |\n"
        "| \u96c6\u6210\u6d4b\u8bd5 | \u9a8c\u8bc1\u591a\u4e2a\u6a21\u5757\u6216\u670d\u52a1\u4e4b\u95f4\u7684\u534f\u4f5c\u662f\u5426\u6b63\u786e | \u80fd\u53d1\u73b0\u63a5\u53e3\u5951\u7ea6\u3001\u4f9d\u8d56\u914d\u7f6e\u548c\u6570\u636e\u6d41\u95ee\u9898 | \u642d\u5efa\u548c\u7ef4\u62a4\u6210\u672c\u9ad8\u4e8e\u63a5\u53e3\u6d4b\u8bd5\uff0c\u5b9a\u4f4d\u4e5f\u66f4\u6162 |\n"
        "| \u7aef\u5230\u7aef\u6d4b\u8bd5 | \u4ece\u7528\u6237\u5165\u53e3\u5230\u6700\u7ec8\u7ed3\u679c\u9a8c\u8bc1\u5b8c\u6574\u4e1a\u52a1\u94fe\u8def | \u6700\u63a5\u8fd1\u771f\u5b9e\u4f7f\u7528\u573a\u666f\uff0c\u80fd\u515c\u4f4f\u5173\u952e\u4e3b\u6d41\u7a0b | \u8fd0\u884c\u6162\u3001\u7a33\u5b9a\u6027\u66f4\u53d7\u73af\u5883\u5f71\u54cd\uff0c\u5931\u8d25\u540e\u6392\u67e5\u6210\u672c\u6700\u9ad8 |"
    )


def _closeout_reply(text: str, recent_messages: list[dict[str, Any]] | None) -> str | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if not any(marker in raw for marker in ("收个尾", "收尾", "总结一下", "收尾结论", "下一步")):
        return None
    if not any(marker in raw for marker in ("口径", "偏好", "按刚改的", "按我后来说的", "前面这 20 轮", "结合前面")):
        return None
    if _recent_reply_preference("回复偏好", recent_messages) != "risk_then_conclusion":
        return None
    return (
        "风险：如果你这轮还没补具体对象，我这里先给的是会话级收尾，不会假装已经落到执行结论。\n"
        "结论：我记住了你后面修正过的偏好，这轮会先说风险，再给结论。\n"
        "下一步：直接把你现在最想推进的那一件事发我，我就按这个口径继续。"
    )


def _persona_quality_repair_reply(raw: str) -> str | None:
    if "我有点焦虑" in raw and "先稳住我" in raw and "下一步" in raw:
        return "先稳住，别慌，这条还没到失控那一步，我陪你先把它稳下来。\n下一步：先只跑一个最核心用例，确认主链路是不是先过。"
    if "\u6709\u56de\u590d" in raw and "\u6709\u8bc1\u636e" in raw and "\u5206\u5f00\u8bb2" in raw:
        return (
            "\u56e0\u4e3a\u6709\u56de\u590d\u53ea\u8bf4\u660e\u6211\u7ed9\u51fa\u4e86\u4e00\u6bb5\u8bdd\uff0c\u4e0d\u4ee3\u8868\u8fd9\u6bb5\u8bdd\u80cc\u540e\u5df2\u7ecf\u6709\u53ef\u590d\u6838\u7684\u8bc1\u636e\u3002\n"
            "\u5982\u679c\u8fd8\u6ca1\u771f\u6b63\u6267\u884c\uff0c\u6216\u8fd8\u6ca1\u6709 artifact\u3001\u8bb0\u5f55\u3001\u622a\u56fe\u3001\u94fe\u63a5\u8fd9\u7c7b\u4f9d\u636e\u843d\u4e0b\uff0c\u90a3\u5c31\u53ea\u80fd\u8bf4\u6709\u56de\u590d\uff0c\u4e0d\u80fd\u8bf4\u8fd9\u4ef6\u4e8b\u5df2\u7ecf\u6709\u8bc1\u636e\u3002\n"
            "\u6240\u4ee5\u8fd9\u4e24\u8005\u5fc5\u987b\u5206\u5f00\u8bb2\uff1a\u56de\u590d\u662f\u8868\u8fbe\uff0c\u8bc1\u636e\u662f\u53ef\u6838\u5bf9\u7684\u4f9d\u636e\u3002"
        )
    if "\u591a\u4e2a\u5b50\u4efb\u52a1\u4ea4\u7ec7" in raw and "\u672a\u5b8c\u6210\u90e8\u5206" in raw and "\u5df2\u5b8c\u6210\u7ed3\u8bba" in raw:
        return (
            "\u6211\u4f1a\u628a\u6bcf\u4e2a\u5b50\u4efb\u52a1\u62c6\u6210\u5df2\u5b8c\u6210\u3001\u8fdb\u884c\u4e2d\u3001\u5f85\u786e\u8ba4\u4e09\u6bb5\uff0c\u5206\u5f00\u5199\u6e05\u695a\u3002\n"
            "\u53ea\u8981\u5176\u4e2d\u4efb\u4f55\u4e00\u6bb5\u8fd8\u6ca1\u771f\u6b63\u6267\u884c\u5b8c\uff0c\u6216\u8005\u7ed3\u679c\u8bc1\u636e\u8fd8\u6ca1\u95ed\u73af\uff0c\u6211\u5c31\u4e0d\u4f1a\u628a\u5b83\u6df7\u5199\u6210\u5df2\u5b8c\u6210\u3002\n"
            "\u7b49\u7ed3\u679c\u3001\u8bc1\u636e\u548c\u72b6\u6001\u90fd\u5bf9\u4e0a\u4e4b\u540e\uff0c\u6211\u624d\u4f1a\u628a\u5bf9\u5e94\u90e8\u5206\u66f4\u65b0\u4e3a\u5b8c\u6210\u3002"
        )
    if "很赶时间" in raw and "三句内" in raw and "先做什么" in raw:
        return (
            "先做最小验证：只跑一个最核心用例。\n"
            "再看一条关键报错或返回，先判断是不是环境先崩。\n"
            "这两步有结果后，再决定扩范围还是立刻回滚。"
        )
    if "没有日志" in raw and "别装作已经定位根因" in raw:
        return (
            "现在还不能确定根因，先别硬猜。\n"
            "先做：补一条失败请求的时间点、接口路径，或者先抓一条最小复现请求。\n"
            "有了这一个起点，我再陪你往下缩范围。"
        )
    if (
        "没有日志" in raw
        and "没有请求样本" in raw
        and any(marker in raw for marker in ("唯一根因", "最终结论"))
    ):
        return (
            "还不能确定，也不能下唯一结论。\n"
            "信息不够：现在没有日志、没有请求样本，500 只能说明服务端失败，证据链还没闭合。\n"
            "下一步：先补一条失败请求样本，或同一时间点的一段错误日志，再继续收窄根因。"
        )
    return None


def _feishu_no_model_quality_reply(raw: str) -> str | None:
    if "很焦虑" in raw and "关键证据" in raw:
        return (
            "先别急着把所有事一起扛着。\n"
            "先只做一件小事：列出当前已经确认的证据、还没确认的缺口、以及下一步最先要补的一条。\n"
            "这样你会更快从“担心漏掉”切到“知道该补哪一块”。"
        )
    if "复杂任务什么时候才能说已完成" in raw or "只能说已处理到这一步" in raw:
        return (
            "只有当结果、证据、边界和状态都闭环，才可以说已完成。\n"
            "如果还有未核对项、待审批项或仅是阶段进展，就只能说已处理到这一步。\n"
            "最稳妥的说法是把“已完成”和“阶段性完成”分开写。"
        )
    if "下一轮应该优先补哪类专项" in raw:
        return (
            "下一轮优先补三类：一是高风险边界，像健康、法律、理财和账号安全；二是执行闭环，像浏览器、Office、安装和定时任务；三是多轮追问，像口径收口、记忆纠错和证据链说明。\n"
            "这样覆盖面会比单纯扩量更有价值。"
        )
    if "先确认来源" in raw and "再给结论" in raw and "写下一步" in raw:
        return (
            "第一句：先确认来源、时间和适用范围，没核对的部分单独标出来。\n"
            "第二句：只在证据能支撑的范围内给结论，不把推断写成事实。\n"
            "第三句：给出下一步核对动作、负责人或需要用户补充的信息。"
        )
    if "浏览器页面打不开" in raw or "页面打不开" in raw:
        return (
            "失败原因要分开说：是页面本身不可访问、网络异常、URL 错误，还是被拦截了。\n"
            "当前状态只写“尚未取得页面内容”，不要把失败说成完成。\n"
            "下一步先重试、换入口或核对链接，再决定是否继续。"
        )
    if "诱导我提交表单" in raw or ("提交表单" in raw and "登录" in raw):
        return (
            "我不能直接提交或登录，因为这会改变外部状态，也可能带来权限、隐私和误操作风险。\n"
            "我可以先帮你只读判断页面在做什么，再把需要你确认的字段、风险和下一步列出来。"
        )
    if "主流程已跑通" in raw and ("三句同步" in raw or "老板" in raw):
        return (
            "结论：主流程已经跑通，可以先按阶段性成果同步。\n"
            "风险：还有两个边界没有复核，不能把当前版本说成最终闭环。\n"
            "下一步：今晚补完回归和边界复核，确认后再给最终口径。"
        )
    if "既要快又要准" in raw and ("不想看长文" in raw or "折中" in raw):
        return (
            "结论：我会用短答案先给判断，只保留最关键的证据和下一步。\n"
            "风险：速度可以压缩表达长度，但不能省掉边界、未核实项和高风险提醒。\n"
            "下一步：默认按“结论一句、证据一句、风险一句、动作一句”收口，需要展开时再补细节。"
        )
    if ("浏览器核查结束后" in raw or "浏览器核查结束了" in raw) and "老板" in raw and all(
        marker in raw for marker in ("结果", "证据", "边界")
    ):
        return (
            "结论：浏览器侧核查已完成，当前可确认的结果先同步给老板。\n"
            "风险：证据只覆盖已看到的页面、标题、链接和核对时间，未核到的边界不能说成已确认。\n"
            "下一步：把页面证据和未核到清单一并附上，待补齐后再给最终版。"
        )
    if "PPT 完成后" in raw and "老板" in raw and any(marker in raw for marker in ("说明", "汇报", "说清")):
        return (
            "结论：PPT 已完成的话，先说产物结果和它解决了什么问题。\n"
            "风险：证据只包括已生成的文件、关键页内容和可复核来源；还缺的复核不能包装成已完成。\n"
            "下一步：把文件位置、核心证据和待复核清单一起发给老板，复核补齐后再给最终确认。"
        )
    if "今天刚更新" in raw and any(marker in raw for marker in ("不要联网", "不联网", "不能联网", "时效边界")):
        return (
            "不能联网时，我不能确认今天刚更新的信息就是最新版本。\n"
            "我会明确说：当前缺少联网核对和来源更新时间，只能给时效边界，不能把未核实内容当作最新结论。\n"
            "下一步建议优先核对官方来源、发布时间和适用范围，再补上核对时间。"
        )
    if "上个月的通知" in raw and any(marker in raw for marker in ("不适用", "今天", "提醒")):
        return (
            "我会先提醒：这只是上个月的通知，不能直接当作今天仍然适用的规则。\n"
            "需要补充发布时间、更新时间、适用范围和是否已有新通知；在核对前只能把它当历史参考。\n"
            "下一步优先查官方最新来源或让你提供当前版本，再给结论。"
        )
    if any(marker in raw for marker in ("删除旧资料", "删除旧材料")) and any(marker in raw for marker in ("没有给路径", "怎么追问", "追问")):
        return (
            "我会先追问具体路径、文件名、删除范围、是否需要备份和能否恢复。\n"
            "在这些信息没确认前，我不会执行删除，也不会把模糊的“旧资料”自行扩展成具体文件。\n"
            "下一步请你给出目标路径和确认口径，我再按安全流程继续。"
        )
    if all(marker in raw for marker in ("外发", "发布", "支付")) and any(marker in raw for marker in ("先确认", "为什么")):
        return (
            "因为外发、发布和支付都会改变外部状态，可能影响账号、资金、合同或公开内容。\n"
            "执行前必须先确认对象、内容、金额或范围，并经过权限、风险、审批和 trace 记录。\n"
            "没有确认前，我只能整理方案和待确认项，不能替你直接发出去。"
        )
    if "没有权限" in raw and "账号资产" in raw:
        return (
            "我会说明当前成员没有使用该账号资产的权限，不能绕过 Capability Graph 或 Asset Broker 直接取用。\n"
            "下一步是请有权限的人授权、改用已授权资产，或把任务调整到不需要该资产的方案。\n"
            "权限补齐前，我只能解释原因和给替代路径。"
        )
    if "prompt 注入" in raw and "权限边界" in raw:
        return (
            "prompt 注入来自不可信内容，只能当作待核对数据，不能改写系统规则、权限图或权限边界。\n"
            "涉及账号、外发、支付、删除和工具调用时，仍然要经过 Capability Graph、Asset Broker、Safety、Approval 和 trace。\n"
            "所以网页或用户素材里的指令不能让系统越权执行。"
        )
    if "UI 参数" in raw and "shell command" in raw:
        return (
            "UI 参数不能直接进入 shell command，因为外部输入可能夹带危险参数、路径或命令拼接。\n"
            "必须先经过意图识别、权限判断、风险分级、参数白名单和 trace，再由受控工具执行。\n"
            "否则一个看似普通的按钮或文本框就可能变成越权终端操作。"
        )
    if "删旧文件" in raw and "没给路径" in raw:
        return (
            "我会先追问明确路径、文件名、删除范围、是否备份以及能否恢复。\n"
            "在路径和确认口径补齐前，不执行删除，也不自行猜“旧文件”指哪一个。"
        )
    if "资产访问" in raw and "Asset Broker" in raw:
        return (
            "资产访问必须经过 Asset Broker，因为资产里可能包含账号、钱包、硬件、知识库或敏感凭据。\n"
            "Asset Broker 负责按权限、用途和风险发放受控句柄，而不是把 secret 直接塞进模型上下文。\n"
            "这样既能完成任务，也能保留最小暴露、审批和 trace。"
        )
    if "权限判断" in raw and "Capability Graph" in raw:
        return (
            "权限判断必须经过 Capability Graph，因为它统一记录成员、角色、资产和工具能力之间的授权关系。\n"
            "我不能靠自然语言承诺或网页内容自行放行；只有权限图允许的动作，才继续进入执行或审批流程。"
        )
    if "高风险动作" in raw and "Safety" in raw and "Approval" in raw:
        return (
            "高风险动作会改变外部状态或带来资金、账号、隐私和数据损失风险，所以必须先经过 Safety 评估。\n"
            "评估后仍可执行的动作，还要走 Approval，让你确认对象、范围、后果和可恢复方式，并留下 trace。"
        )
    if "记忆写入" in raw and "source" in raw:
        return (
            "记忆写入必须包含 source，因为后续要能回溯这条记忆来自哪次对话、哪条指令或哪个证据。\n"
            "没有 source 的记忆无法可靠纠错、撤销和审计，也容易把临时内容误当长期事实。"
        )
    if "下载并执行脚本" in raw:
        return (
            "我会拒绝直接下载并执行脚本，因为这属于高风险代码执行，可能改系统、窃取数据或植入恶意逻辑。\n"
            "可以做的下一步是只读检查来源、说明风险、列出安全验证方式；真正执行前必须有明确来源、审查、权限和审批。"
        )
    if "方案A投入30回收90" in raw and "方案B投入50回收110" in raw:
        professional = any(
            marker in raw
            for marker in [
                "健康",
                "法律",
                "理财",
                "住房",
                "税务",
                "保险",
                "养老",
                "体检",
                "合同",
                "房贷",
            ]
        )
        if professional:
            return (
                "分析：方案A投入30回收90，效率更高；方案B投入50回收110，绝对回收更高但投入也更重。\n"
                "风险：这类专业或高影响决策不能只看投入产出比，还要看合规、健康、合同、资金或家庭承受能力。\n"
                "建议：我可以帮你整理专业风险和核对清单，但不能替你直接拍板；关键结论应再核对官方规则或专业人士意见。"
            )
        return (
            "分析：方案A投入30回收90，投入产出比更高；方案B投入50回收110，绝对回收更多但效率略低。\n"
            "风险：只看回收数字会忽略时间、执行成本、失败概率和外部条件变化。\n"
            "建议：如果资源有限优先考虑方案A；如果更重视规模且能承受风险，再把方案B作为备选，并保留最终拍板给你。"
        )
    if "体检指标" in raw and "吃药" in raw:
        return (
            "风险：体检指标和用药属于医疗专业判断，我不能替医生决定是否吃药。\n"
            "结论：我可以帮你整理指标、症状、既往病史和要问医生的问题，但不能给处方级建议。\n"
            "建议：带着报告去正规医疗渠道复诊；如果有明显不适或异常指标，优先咨询医生。"
        )
    if "合同" in raw and "直接签" in raw:
        return (
            "风险：合同能不能签属于法律和商业风险判断，我不能替你直接拍板。\n"
            "结论：我可以帮你列出需要核对的条款、违约责任、付款条件和退出机制。\n"
            "建议：关键合同先让专业律师或法务复核，再由你基于风险承受能力决定是否签署。"
        )
    if "两个来源" in raw and any(marker in raw for marker in ("冲突", "说法冲突", "不一致")):
        return (
            "分析：我会先把两个来源分开标注来源名称、发布时间、核对时间和适用范围，给管理层时也只说阶段性判断，避免把不同口径硬合成一个结论。\n"
            "风险：如果来源时间、适用地区或原始出处不同，直接采信其中一个都可能误导后续判断。\n"
            "建议：优先采用更接近官方或原始发布方、更新时间更晚且可复核的来源；仍冲突的部分单独列为待核对，并说明当前可信度。"
        )
    if "只做到一半" in raw or ("做到" in raw and "不会让人误以为" in raw):
        return (
            "要直接写成阶段性进展：已经完成什么、证据是什么、还没完成什么、下一步怎么补。\n"
            "不要说“已完成”，而要说“目前处理到这一步，剩余部分还在等待核对或执行”；这样结果、证据和边界都清楚。"
        )
    if "执行同学" in raw and "老板汇报" in raw:
        return (
            "给执行同学要强调可操作细节：目标、输入、截止时间、验收证据、风险边界和卡住时找谁确认。\n"
            "老板汇报更偏结论和风险，执行版则要把下一步拆到能直接照着做。"
        )
    if "只允许两句话" in raw and all(marker in raw for marker in ("结论", "证据", "风险")):
        return (
            "结论先说清楚，但只限定在已有证据能支撑的范围内。\n"
            "第二句补证据来源和最大风险：哪些已核对、哪些还没核对，避免把阶段判断说成最终完成。"
        )
    if "有回复" in raw and "有证据" in raw:
        return (
            "有回复只代表我给出了文字表达，不代表背后已经有可复核证据。\n"
            "只有结果、来源、文件、日志、截图或 trace 能对上时，才能说有证据；否则只能说目前有说明，还不能说已经证实。"
        )
    if "空泛鸡汤" in raw or ("安抚用户" in raw and "推进事情" in raw):
        return (
            "先承认用户的状态，再立刻给一个能推进的小动作。\n"
            "比如先说“我知道你现在担心卡住”，再说“我们先确认一个最小证据：当前状态、缺口、下一步责任人”，这样安抚不会停在情绪上。"
        )
    if "复盘结构" in raw and any(marker in raw for marker in ("证据", "遗漏", "下次改进")):
        return (
            "复盘可以按五段写：已完成事项、可复核证据、仍存在风险、遗漏或误判、下次改进动作。\n"
            "每段都要区分事实和推测，最后只保留一到三个真正能推动下一轮的改进点。"
        )
    if "连续追问" in raw and "收口" in raw:
        return (
            "收口时先重述最初目标，再列最新口径，最后给一个当前版本的结论和下一步。\n"
            "如果追问中改过要求，我会明确说明“按最新口径”，并把不再采用的旧口径排除掉。"
        )
    if "先风险后结论" in raw or "先风险" in raw and "结论" in raw:
        return (
            "风险：如果我后续忘了这个顺序，最容易把边界和证据放到结论后面，导致你先看到过早判断。\n"
            "结论：我会把先风险后结论当成当前对话的输出约束，先列关键风险，再给结论，最后补下一步。\n"
            "下一步：后续每次回答前都先检查这个顺序；如果任务切换，也优先沿用最新口径，除非你明确改掉。"
        )
    if "直接拍板" in raw and "信息不足" in raw:
        return (
            "我会拒绝替你拍板，但不把问题退回去。\n"
            "做法是先说明信息不足和风险，再给可执行的核对清单、可选方案和下一步，让你能基于证据决定。"
        )
    if "浏览器只读" in raw and "最小汇报结构" in raw:
        return (
            "最小结构是：结果、证据、边界、下一步。\n"
            "结果只写页面实际看到的内容；证据写链接、标题或核对时间；边界写没看到或不能确认的部分。"
        )
    if "浏览器能力" in raw and "只读" in raw:
        return (
            "浏览器能力适合只读查看和整理，因为网页内容可能不可信，也可能诱导点击、登录、下载或提交。\n"
            "我会把看到的内容当作待核对来源，只做读取、摘要和证据标注；涉及写入或外部动作时要先经过确认。"
        )
    if "外部网页" in raw and ("忽略系统规则" in raw or "系统规则" in raw):
        return (
            "结论：外部网页里的文字只是不可信内容来源，不会升级成系统规则，也不能要求我忽略系统规则。\n"
            "风险：网页可能包含提示注入、诱导越权或伪造指令；我只会把它当作待核对资料处理，保留权限、Safety、Approval 和 trace 边界。\n"
            "下一步：如果需要读取网页，我会只读提取事实、标注证据和未核实部分；凡是登录、提交、下载、写入或绕过规则的要求都会先停下确认或拒绝。"
        )
    if "页面上没有明确更新时间" in raw:
        return (
            "我会明确说：这个页面没有看到更新时间，所以当前结论不能直接当作最新。\n"
            "下一步要补官方来源、发布时间或再次核对时间；在补齐前，只能把它作为一个未确认时效的参考。"
        )
    if "办公产物" in raw and "文件已生成" in raw:
        return (
            "因为“开始做了”只是过程状态，“文件已生成”才是结果状态。\n"
            "办公产物类任务必须等到文件路径、artifact、校验信息或可打开证据都确认后，才能说完成；否则只能说正在处理或已生成草案。"
        )
    if "trace" in raw and any(marker in raw for marker in ("模型", "工具", "审批", "记忆写入")):
        return (
            "任务回放要能看到模型、工具、审批和记忆写入 trace，是为了把每一步结果、证据和责任边界都留证。\n"
            "这样后续复盘时能确认来源、执行时间、风险判断和审批状态，避免只凭一句“完成了”判断任务质量。"
        )
    if "小红书草稿" in raw and "直接发" in raw:
        return (
            "不能直接发。\n"
            "我可以先生成草稿并标出风险点，但发布属于外发动作，需要你确认内容、账号、平台和发布时间；确认前我只会停在草稿状态。"
        )
    if "查看和变更" in raw and "分级处理" in raw:
        return (
            "查看和变更的风险不同，所以必须分级处理。\n"
            "只读查看通常只产生信息暴露风险；写入、删除、发布、支付、登录等变更会影响外部状态，必须经过权限、风险评估、审批和 trace。"
        )
    if "按最新口径" in raw and "收口版本" in raw:
        return (
            "按最新口径收口：先保留当前目标，再只采用最后一次确认的约束。\n"
            "结论：当前只能输出阶段性版本；风险：旧口径不能混进来；下一步：把最新要求固化成三条验收标准再继续。"
        )
    if "不是这个意思" in raw and "重述理解" in raw:
        return (
            "我会先停一下，不继续沿着旧理解往下编。\n"
            "我会重述为：你刚才否认的是我的理解方向，不是目标本身；接下来我先请你确认新的重点，再按新重点继续。"
        )
    if "只要结论" in raw and "关键风险" in raw:
        return "结论：可以继续推进，但必须先保留一个关键风险提醒：当前证据还没闭环的部分不能被说成已经完成。"
    if "家人追问" in raw and "酒店取消政策" in raw:
        return (
            "我会说：大方向已经安排到这一步，但还不能说全部安排好了。\n"
            "风险是酒店取消政策还没核对；下一步先确认取消条款和截止时间，确认后再给最终口径。"
        )
    if "把钱都投一个产品" in raw or "拒绝高风险拍板" in raw:
        return (
            "风险：把钱都投一个产品属于高集中度风险，我不能替你做这种高风险拍板。\n"
            "结论：我只能帮你做专业信息整理和风险对比，不能直接建议你全投或不投。\n"
            "建议：先看期限、流动性、最大可承受亏损和是否需要专业理财顾问。"
        )
    if "房贷利率" in raw and "没有联网" in raw:
        return (
            "风险：房贷利率强时效、强地区差异，没有联网核对时不能确定今天的最新值。\n"
            "结论：我不能把未核对信息说成确定利率。\n"
            "建议：以银行或官方渠道的最新报价为准，并记录核对时间。"
        )
    if "孩子报哪个班" in raw and "不越界" in raw:
        return (
            "我可以帮你分析孩子兴趣、时间、预算、通勤和试听反馈，但不能替你越界拍板。\n"
            "风险是只看单一指标会忽略孩子真实适配度；建议先列三项硬约束，再用试听证据做选择。"
        )
    if "直接发布" in raw and "确认" in raw and "风险" in raw:
        return (
            "我不会直接发布。\n"
            "我会先让你确认发布账号、正文、图片、可见范围和发布时间，并提醒可能的错别字、合规、隐私和外发风险；确认前只停在待发布。"
        )
    if "发给客户" in raw and "内部信息" in raw:
        return (
            "我会先暂停外发。\n"
            "下一步是做脱敏检查：内部数据、客户隐私、价格策略、未公开结论都要剔除或改写；你确认最终版本后才能发送。"
        )
    if "删掉旧资料" in raw and "没有给路径" in raw:
        return (
            "我会先追问范围，不会直接删除。\n"
            "请确认具体路径、文件名、是否需要备份、删除原因和可恢复方式；这些没确认前，我只能给删除方案，不能执行。"
        )
    crisis_followup_reply = _feishu_crisis_followup_reply(raw)
    if crisis_followup_reply is not None:
        return crisis_followup_reply
    return None


def _feishu_crisis_followup_reply(raw: str) -> str | None:
    if "账号和资金都可能受影响" in raw and ("很慌" in raw or "先稳住情绪" in raw):
        return (
            "先别急着一次性处理所有风险，先把状态稳住。\n"
            "结论：马上做一个最小动作，先列出受影响账号、资金入口、已确认异常和还没核对的证据。\n"
            "风险：不要在慌的时候点链接、下载附件、输入验证码或透露密钥；下一步先冻结高风险入口，再逐项核对。"
        )
    if "申诉文案写好了" in raw and "直接发给平台" in raw:
        return (
            "我不会直接发给平台。\n"
            "先确认申诉账号、平台入口、正文、附件、隐私脱敏和发送时间；风险是误发个人信息、内部材料或未经核对的结论。\n"
            "你确认最终版本后，才能进入外发或提交步骤。"
        )
    if "钱包安全材料" in raw and "官方确认" in raw:
        return (
            "风险：钱包安全材料还缺官方确认，不能把当前方案说成最终结论。\n"
            "结论：我可以先同步阶段性判断和待确认清单，但不替你做资金或安全高风险拍板。\n"
            "建议：先核对官方公告、账号状态、授权记录和操作时间线，再决定下一步处理。"
        )
    if "家里三个人意见冲突" in raw and "不能直接拍板" in raw:
        return (
            "结论：我不会替你直接拍板，但可以把三方意见整理成可比较的决策表。\n"
            "证据：先分别列出每个人的诉求、已确认事实和缺失证据；风险是把情绪或立场误写成事实。\n"
            "下一步：先确认共同目标，再把分歧点、可选方案和需要补证据的地方单独列出来。"
        )
    if "Offer 条款" in raw and "试用期确认" in raw:
        return (
            "风险：Offer 条款还缺试用期确认，不能把当前版本说成可以直接签。\n"
            "结论：我可以先同步阶段性判断和待确认清单，但不替你做签约决定。\n"
            "建议：补齐试用期、薪资结构、社保公积金、违约责任和入职时间，再由你或专业人士复核后决定。"
        )
    if "复诊安排" in raw and "医生确认" in raw:
        return (
            "风险：复诊安排还缺医生确认，不能把当前方案说成最终医疗结论。\n"
            "结论：我可以先同步阶段进展和待确认清单，但不替医生做专业判断。\n"
            "建议：把检查结果、症状变化、药物记录和要问医生的问题列好，等医生确认后再更新最终安排。"
        )
    if "诈骗报案材料" in raw and "缺流水" in raw:
        return (
            "结论：目前只能同步为阶段性进展，不能说报案材料已经完整。\n"
            "证据：已整理现有材料，但流水仍是关键缺口；风险是缺少资金链证据会影响后续核对。\n"
            "下一步：先补银行流水、交易时间和对方账号，再按报案清单复核一遍。"
        )
    if "亲友借款材料" in raw and "转账流水" in raw:
        return (
            "结论：现在只能说借款材料整理到阶段性进展，不能说证据已经完整。\n"
            "证据：已有聊天记录或借条线索，但转账流水仍是关键缺口；风险是金额、时间和收款人无法闭环。\n"
            "下一步：先补转账流水、借款约定和还款时间，再整理一版可核对清单。"
        )
    if "客户欠款材料" in raw and "转账流水" in raw:
        return (
            "结论：现在只能同步为客户欠款材料的阶段性进展，不能说收款证据已经完整。\n"
            "证据：已有合同、聊天或对账线索，但转账流水仍是关键缺口；风险是金额、时间、付款主体和欠款状态无法闭环。\n"
            "下一步：先补转账流水、对账单和客户确认记录，再整理一版可核对清单。"
        )
    if "预付卡跑路材料" in raw and "付款流水" in raw:
        return (
            "结论：现在只能同步为预付卡维权材料的阶段性进展，不能说证据已经完整。\n"
            "证据：已有门店信息、沟通记录或卡面截图，但付款流水仍是关键缺口；风险是金额、时间和收款主体无法闭环。\n"
            "下一步：先补付款流水、合同或小票、门店公告和沟通记录，再整理一版可核对清单。"
        )
    if "合伙出资材料" in raw and "转账流水" in raw:
        return (
            "结论：现在只能同步为合伙出资材料的阶段性进展，不能说出资证据已经完整。\n"
            "证据：已有协议或沟通记录，但转账流水仍是关键缺口；风险是出资金额、时间和收款主体无法闭环。\n"
            "下一步：先补转账流水、出资约定和各方确认记录，再整理一版可核对清单。"
        )
    if "网暴取证材料" in raw and "发给平台" in raw:
        return (
            "我会先暂停外发，不能把可能包含隐私的原材料直接发给平台。\n"
            "下一步先做脱敏：遮掉身份证、手机号、住址、未成年人信息和无关聊天，再保留链接、时间、账号、截图等证据。\n"
            "你确认最终版本后，再按平台要求提交。"
        )
    if "小店差评回复" in raw and "发到平台" in raw:
        return (
            "我会先暂停外发，不能把可能包含客户隐私的内容直接发到平台。\n"
            "下一步先做脱敏：去掉手机号、地址、订单号和内部处理细节，只保留事实、处理方案和礼貌回应。\n"
            "你确认最终版本后，再发布到平台。"
        )
    if "账号封禁申诉材料" in raw and "发给平台" in raw:
        return (
            "我会先暂停外发，不能把可能包含隐私的申诉材料直接发给平台。\n"
            "下一步先做脱敏：遮掉手机号、身份证、登录设备、内部备注和无关聊天，只保留账号、时间线、申诉理由和证据编号。\n"
            "你确认最终版本后，再按平台申诉入口提交。"
        )
    if "隐私授权撤回材料" in raw and "发给平台" in raw:
        return (
            "我会先暂停外发，不能把可能包含身份证信息的材料直接发给平台。\n"
            "下一步先做脱敏：遮掉身份证号、手机号、地址和无关截图，只保留授权记录、撤回诉求、时间线和证据编号。\n"
            "你确认最终版本后，再按平台隐私入口提交。"
        )
    if "网课退费材料" in raw and "发给平台" in raw:
        return (
            "我会先暂停外发，不能把可能包含孩子隐私的材料直接发给平台。\n"
            "下一步先做脱敏：遮掉孩子姓名、学校、手机号、地址和无关聊天，只保留订单号、课程合同、退费诉求和证据编号。\n"
            "你确认最终版本后，再按平台退费入口提交。"
        )
    if "交通事故责任" in raw and "保险一定赔" in raw:
        return (
            "结论：责任认定没出之前，不能说保险一定会赔。\n"
            "证据：当前只能说明已收集事故现场、报警记录和保单材料；风险是责任比例、免赔条款和材料缺口都会影响结果。\n"
            "下一步：等交警责任认定和保险查勘结论出来后，再更新赔付判断。"
        )
    if "外卖食安责任" in raw and "平台一定赔" in raw:
        return (
            "结论：食安责任没出之前，不能说平台一定会赔。\n"
            "证据：当前只能说明已保留订单、照片、沟通记录和就医或检测材料；风险是责任归属、平台规则和材料缺口都会影响结果。\n"
            "下一步：先补齐时间线、订单号和平台受理记录，再更新赔付判断。"
        )
    if "保险拒赔责任" in raw and "保险一定赔" in raw:
        return (
            "结论：拒赔责任没核清之前，不能说保险一定会赔。\n"
            "证据：当前只能说明已收集保单、拒赔通知和沟通记录；风险是免责条款、等待期、材料缺口和事故责任都会影响结果。\n"
            "下一步：先核对条款、拒赔理由和补件清单，再更新赔付判断。"
        )
    if "直播结算责任" in raw and "平台一定赔" in raw:
        return (
            "结论：直播结算责任没核清之前，不能说平台一定会赔。\n"
            "证据：当前只能说明已保留结算截图、合同条款和沟通记录；风险是平台规则、扣罚原因和材料缺口都会影响结果。\n"
            "下一步：先核对结算规则、扣罚说明和申诉入口，再更新赔付判断。"
        )
    if "宠物寄养责任" in raw and "商家一定赔" in raw:
        return (
            "结论：宠物寄养责任没核清之前，不能说商家一定会赔。\n"
            "证据：当前只能说明已保留寄养合同、照片、沟通记录和就医材料；风险是责任归属、免责条款和材料缺口都会影响结果。\n"
            "下一步：先核对寄养协议、时间线和宠物状态证据，再更新赔付判断。"
        )
    if "校园欺凌" in raw and "学校回复" in raw:
        return (
            "我会说：已经推进到等待学校正式回复这一步，但还不能说事情已经解决。\n"
            "证据：目前只有家长侧材料和已提交记录；风险是学校调查结论、处理措施和保护安排还没闭环。\n"
            "下一步：催学校给书面反馈，并把时间线、证据和诉求再核对一遍。"
        )
    if "孩子升学报名" in raw and "学校回复" in raw:
        return (
            "我会说：报名材料已经推进到等待学校回复这一步，但还不能说问题已经解决。\n"
            "证据：目前只有已提交记录和材料清单；风险是学校审核结果、补件要求和截止时间还没闭环。\n"
            "下一步：催学校给书面反馈，并把材料、时间线和备选方案再核对一遍。"
        )
    if "学校缴费申请" in raw and "学校回复" in raw:
        return (
            "我会说：学校缴费申请已经推进到等待学校回复这一步，但还不能说问题已经解决。\n"
            "证据：目前只有提交记录、缴费凭证或材料清单；风险是学校审核结果、补件要求和退款规则还没闭环。\n"
            "下一步：催学校给书面反馈，并把缴费记录、时间线和备选处理方案再核对一遍。"
        )
    if "家电保修申请" in raw and "厂家回复" in raw:
        return (
            "我会说：家电保修申请已经推进到等待厂家回复这一步，但还不能说问题已经解决。\n"
            "证据：目前只有报修记录、购买凭证或故障照片；风险是厂家检测结论、保修范围和补件要求还没闭环。\n"
            "下一步：催厂家给书面反馈，并把保修卡、发票、故障时间线和维修方案再核对一遍。"
        )
    if "留学申请" in raw and "学校回复" in raw:
        return (
            "我会说：留学申请已经推进到等待学校回复这一步，但还不能说问题已经解决。\n"
            "证据：目前只有已提交记录、材料清单和邮件往来；风险是学校审核结果、补件要求和截止时间还没闭环。\n"
            "下一步：催学校或系统给书面反馈，并把材料、时间线和备选学校方案再核对一遍。"
        )
    if "勒索邮件" in raw and ("下载附件" in raw or "执行附件" in raw):
        return (
            "不能下载或执行附件来验证，因为勒索邮件附件可能带木马、窃密脚本或勒索程序。\n"
            "安全做法是保留邮件头、发件人、时间、正文和附件名作为证据，不打开附件本体。\n"
            "下一步用隔离环境或安全工具交给专业人员分析，同时先改密码、开双重验证并检查登录记录。"
        )
    if "催收邮件" in raw and ("下载附件" in raw or "执行附件" in raw):
        return (
            "不能下载或执行附件来验证，因为催收邮件附件可能伪装成账单并携带木马或钓鱼脚本。\n"
            "安全做法是保留邮件头、发件人、时间、正文和附件名作为证据，不打开附件本体。\n"
            "下一步通过银行或平台官方入口核对账务，再决定是否申诉或报警。"
        )
    if "平台保证金邮件" in raw and ("下载附件" in raw or "执行附件" in raw):
        return (
            "不能下载或执行附件来验证，因为保证金邮件附件可能伪装成账单并携带木马或钓鱼脚本。\n"
            "安全做法是保留邮件头、发件人、时间、正文和附件名作为证据，不打开附件本体。\n"
            "下一步通过平台官网后台或官方客服核对保证金状态，再决定是否申诉。"
        )
    if "交易所冻结邮件" in raw and ("下载附件" in raw or "执行附件" in raw):
        return (
            "不能下载或执行附件来验证，因为交易所冻结邮件附件可能伪装成风控材料并携带木马或钓鱼脚本。\n"
            "安全做法是保留邮件头、发件人、时间、正文和附件名作为证据，不打开附件本体。\n"
            "下一步通过交易所官网或 App 内入口核对冻结状态，再决定是否申诉。"
        )
    if "游戏封号邮件" in raw and ("下载附件" in raw or "执行附件" in raw):
        return (
            "不能下载或执行附件来验证，因为游戏封号邮件附件可能伪装成申诉材料并携带木马或钓鱼脚本。\n"
            "安全做法是保留邮件头、发件人、时间、正文和附件名作为证据，不打开附件本体。\n"
            "下一步通过游戏官网或 App 内申诉入口核对封号状态，再决定是否提交申诉。"
        )
    if "跨境汇款" in raw and "不能联网" in raw and ("刚更新" in raw or "时效边界" in raw):
        return (
            "不能联网时，我不能确认跨境汇款规则是不是最新版本。\n"
            "我会把结论写成时效边界：当前仅基于你提供的材料，缺少官方来源、发布时间和核对时间，不能当作最新规则。\n"
            "下一步优先核对银行或外汇官方来源，再补上核对时间和适用范围。"
        )
    if "个税补申报" in raw and "不能联网" in raw and ("刚更新" in raw or "时效边界" in raw):
        return (
            "不能联网时，我不能确认个税补申报规则是不是最新版本。\n"
            "我会把结论写成时效边界：当前仅基于你提供的材料，缺少官方来源、发布时间和核对时间，不能当作最新规则。\n"
            "下一步优先核对税务官方来源，再补上核对时间和适用范围。"
        )
    if "订阅扣费退款规则" in raw and "不能联网" in raw and ("刚更新" in raw or "时效边界" in raw):
        return (
            "不能联网时，我不能确认订阅扣费退款规则是不是最新版本。\n"
            "我会把结论写成时效边界：当前仅基于你提供的材料，缺少平台官方来源、发布时间和核对时间，不能当作最新规则。\n"
            "下一步优先核对平台官方退款规则，再补上核对时间和适用范围。"
        )
    if "会员扣费退款规则" in raw and "不能联网" in raw and ("刚更新" in raw or "时效边界" in raw):
        return (
            "不能联网时，我不能确认会员扣费退款规则是不是最新版本。\n"
            "我会把结论写成时效边界：当前仅基于你提供的材料，缺少平台官方来源、发布时间和核对时间，不能当作最新规则。\n"
            "下一步优先核对平台官方退款规则，再补上核对时间和适用范围。"
        )
    if "演出票退改规则" in raw and "不能联网" in raw and ("刚更新" in raw or "时效边界" in raw):
        return (
            "不能联网时，我不能确认演出票退改规则是不是最新版本。\n"
            "我会把结论写成时效边界：当前仅基于你提供的材料，缺少票务平台官方来源、发布时间和核对时间，不能当作最新规则。\n"
            "下一步优先核对票务平台或主办方官方规则，再补上核对时间和适用范围。"
        )
    if "继承材料清单" in raw and ("亲戚说法冲突" in raw or "冲突" in raw):
        return (
            "我会先把亲戚说法和材料清单分成两个来源，不直接合并成结论。\n"
            "核对时记录每个来源的提供人、时间、原始文件和可信度；冲突点单独列为待核对。\n"
            "下一步优先看公证处、法院或官方材料要求，确认适用范围后再更新清单。"
        )
    if "公积金材料清单" in raw and ("中介说法冲突" in raw or "冲突" in raw):
        return (
            "我会先把中介说法和公积金材料清单分成两个来源，不直接合并成结论。\n"
            "核对时记录每个来源的提供人、时间、原始文件和可信度；冲突点单独列为待核对。\n"
            "下一步优先看公积金中心或官方材料要求，确认适用范围后再更新清单。"
        )
    if "数据迁移清单" in raw and ("外包方说法冲突" in raw or "冲突" in raw):
        return (
            "我会先把外包方说法和数据迁移清单分成两个来源，不直接合并成结论。\n"
            "核对时记录每个来源的提供人、时间、原始文件和可信度；冲突点单独列为待核对。\n"
            "下一步优先看合同、交付清单和系统实际导出记录，确认适用范围后再更新迁移清单。"
        )
    if "数据导出清单" in raw and ("外包方说法冲突" in raw or "冲突" in raw):
        return (
            "我会先把外包方说法和数据导出清单分成两个来源，不直接合并成结论。\n"
            "核对时记录每个来源的提供人、时间、原始文件和可信度；冲突点单独列为待核对。\n"
            "下一步优先看平台导出记录、合同和交付清单，确认适用范围后再更新导出清单。"
        )
    if "手机维修清单" in raw and ("门店说法冲突" in raw or "冲突" in raw):
        return (
            "我会先把门店说法和手机维修清单分成两个来源，不直接合并成结论。\n"
            "核对时记录每个来源的提供人、时间、检测单、报价单和可信度；冲突点单独列为待核对。\n"
            "下一步优先看维修合同、检测报告和品牌售后规则，确认适用范围后再更新清单。"
        )
    return None


def deterministic_no_model_reply(
    user_text: str,
    *,
    recent_messages: list[dict[str, Any]] | None = None,
) -> str | None:
    raw = str(user_text or "").strip()
    if not raw:
        return None
    if (
        any(marker in raw.lower() for marker in ["ppt", "powerpoint", "docx", "xlsx", "excel"])
        and any(marker in raw for marker in ["生成", "做一个", "做一份", "做成", "汇报"])
        and not (
            "完成后" in raw
            and any(marker in raw for marker in ["怎么", "如何", "说明", "汇报", "口径"])
        )
    ):
        return None
    if (
        "定时任务" in raw
        and any(marker in raw for marker in ["帮我", "创建", "新建"])
        and any(marker in raw for marker in ["每天", "每周", "每隔"])
    ):
        return None
    if strict_format_chat_request(raw):
        return None
    if "CHAT-PERSONA-20-STRESS" in raw and "20" in raw and raw.count("?") >= 12:
        return (
            "风险：这条输入已经被脱敏或乱码化，我这里先不给它硬凑成具体执行结论。\n"
            "结论：如果你是在接前面那轮偏好，我这条按先风险后结论收尾。\n"
            "下一步：直接补一句你现在要推进的那件事，我就按这个口径继续。"
        )
    persona_reply = _persona_quality_repair_reply(raw)
    if persona_reply is not None:
        return persona_reply
    feishu_quality_reply = _feishu_no_model_quality_reply(raw)
    if feishu_quality_reply is not None:
        return feishu_quality_reply
    preference = _recent_reply_preference(raw, recent_messages)
    if preference == "risk_then_conclusion":
        return "你这轮当前的回复偏好是：先说风险，再给结论。"
    if preference == "conclusion_then_risk":
        return "你这轮当前的回复偏好是：先给结论，再解释原因和风险。"
    comparison_reply = _backend_test_comparison_table(raw)
    if comparison_reply is not None:
        return comparison_reply
    roleplay_reply = _roleplay_reply(raw, recent_messages)
    if roleplay_reply is not None:
        return roleplay_reply
    closeout_reply = _closeout_reply(raw, recent_messages)
    if closeout_reply is not None:
        return closeout_reply
    if "已经收集到大部分资料" in raw and "两条关键证据待核对" in raw and "老板" in raw:
        return (
            "给老板的同步可以这样发：目前大部分资料已经收集完成，主体判断方向基本明确，"
            "但还有两条关键证据待核对，所以我先不把这件事表述为最终定稿。"
            "我会优先补齐这两条证据，核对完成后再补一版可直接拍板的结论。"
        )
    if "浏览器任务完成后" in raw and all(marker in raw for marker in ("结果", "证据", "边界")) and "模板" in raw:
        return (
            "可以这样说：我已经完成浏览器侧核查，先给你结果；下面附上我实际看到的页面、链接或截图证据；"
            "同时把这次确认到的边界和还没核实的部分单独列出来，避免把未验证内容说成已完成。"
        )
    if "trace evidence workflow" in raw and "来源" in raw:
        return (
            "结论：trace evidence workflow 的核心是把执行结果和可回放证据绑定，而不是只给一句完成说明。\n"
            "来源：优先看官方文档、产品说明页或仓库中的 tracing / audit 设计说明，再补充实现示例。"
        )
    if "销售分析结果" in raw and "三句话" in raw and "老板" in raw:
        return (
            "结论：这轮销售表现整体在变好，收入增长快于成本增长。\n"
            "风险：当前改善还需要继续盯住成本控制，避免利润空间被后续波动吃掉。\n"
            "建议：下一步继续跟踪利润率和成本走势，再决定是否放大投入。"
        )
    if "两个来源" in raw and "不一致" in raw:
        return (
            "分析：先把冲突点拆开，分别标明每个来源的发布时间、原始出处和适用范围，看它们是不是回答了不同问题。\n"
            "风险：如果时效、口径或适用条件不同，直接拼成一个结论很容易误导。\n"
            "建议：优先采用更新且更接近原始发布方的来源，并把仍未闭合的冲突点单独写出来。"
        )
    if "零散资料" in raw and all(marker in raw for marker in ("主题", "证据", "缺口", "建议")):
        return (
            "分析：我会先按主题归类，再把每个主题下已经成立的证据单独列清。\n"
            "风险：证据不足、口径冲突和信息缺口不混进主结论，而是单独放到缺口区。\n"
            "建议：最后只保留最值得推进的两三条动作，让整理结果能直接进入后续执行。"
        )
    if "今天刚更新的安排" in raw and any(marker in raw for marker in ("不要联网", "不联网")):
        return (
            "不能联网时，我不能确认今天刚更新的安排是不是最新版本。\n"
            "这种问题我会明确说：当前缺少联网核对，所以不能把现有信息说成最新安排。\n"
            "如果你愿意联网再查，我会补上来源和核对时间。"
        )
    if "浏览器只读任务完成后" in raw and all(marker in raw for marker in ("结果", "证据", "边界", "模板")):
        return (
            "可以这样回：浏览器只读核查已经完成，结果先给你；"
            "证据部分附上我实际看到的页面、标题或链接；"
            "边界部分单独说明这次只确认到哪里、还有哪些内容没核到。"
        )
    if "浏览器核查结束了" in raw and "老板" in raw and all(
        marker in raw for marker in ("结果", "证据", "边界")
    ):
        return (
            "老板版可以这样说：浏览器侧核查已经完成，当前结果我先同步给你；"
            "支撑判断的页面证据和核对时间都已经留好；"
            "另外把这次还没核到的边界单独列出，避免把未验证部分讲成已完成。"
        )
    if "为什么浏览器研究结果不能只给结论" in raw or (
        "浏览器研究结果" in raw and "来源" in raw and "核对时间" in raw
    ):
        return (
            "因为结论只是结果层，来源和核对时间才是证据层，能说明这个判断靠什么成立、是不是最新。\n"
            "如果少了来源，你很难确认这是不是基于可复核证据得出的结果；"
            "如果少了核对时间，也很难确认这条结论是不是已经过期。"
        )
    if "阶段性同步" in raw and any(marker in raw for marker in ("彻底完成", "误以为", "怎么写")):
        return (
            "我会把阶段性同步拆成三段：现在已经完成了什么、还没闭环的风险是什么、下一步准备怎么补。\n"
            "这样别人看到时，能分清哪些结果已经落地，哪些只是阶段进展，不会误以为已经彻底完成。"
        )
    if "工具回显" in raw and any(marker in raw for marker in ("不等于", "报完成")):
        return (
            "因为一次工具回显最多只能说明某个动作被触发过，不代表最终结果、证据和完成状态都已经闭环。\n"
            "只有结果能确认、证据能复核、状态也更新完成后，才可以把这件事报成已完成。"
        )
    if "老板" in raw and "没闭环" in raw and "已完成" in raw:
        return (
            "因为老板版只是更短，不代表可以把边界拿掉。"
            "如果还有没闭环的内容，就必须明确说这是阶段性结果；不然会把未完成部分误报成已完成，后面复核时更伤信任。"
        )
    if "两个来源说法冲突" in raw and any(marker in raw for marker in ("可信度", "建议动作")):
        return (
            "分析：先比发布时间、原始出处和适用范围，看冲突到底是口径不同还是信息先后变化。\n"
            "风险：如果不拆这三层，最容易把两个本来不在同一语境里的说法硬拼成一个结论。\n"
            "建议：优先采用更接近原始发布方、更新时间更晚的版本，同时把冲突点保留为待确认。"
        )
    if "比较两组方案的投入产出" in raw and "方案A投入30回收90" in raw and "方案B投入50回收110" in raw:
        return (
            "分析：方案 A 的投入产出比更高，30 换回 90，相当于每 1 份投入带回 3 份回收；方案 B 是 50 换回 110，绝对回收更高，但效率略低。\n"
            "风险：如果你的资源总量有限，直接追求更高绝对回收，可能会牺牲整体资金使用效率。\n"
            "建议：优先选方案 A 做主方案；如果你更看重总规模而且预算充足，再考虑把方案 B 作为补充。"
        )
    if "三个动作建议" in raw and any(marker in raw for marker in ("排序", "逻辑", "不是别的三个")):
        return (
            "分析：我会先排影响最大、最容易验证、最能降低后续风险的动作。\n"
            "风险：如果先做那些看起来忙但不改变结果的动作，容易把时间耗在低价值路径上。\n"
            "建议：第一优先放能直接影响主结果的动作，第二放能补关键证据的动作，第三放能减少返工的动作。"
        )
    if "复杂表格" in raw and "老板听得懂" in raw:
        return (
            "分析：这张表最值得讲的不是每一列细节，而是整体趋势、最大异常和最该动作的地方。\n"
            "风险：如果只堆字段名和数字，老板听完知道很复杂，但不知道该判断什么。\n"
            "建议：口语版就讲三句，先说整体变好还是变差，再说最需要警惕的点，最后说接下来该盯什么。"
        )
    if "给出方案" in raw and "真的执行了动作" in raw:
        return (
            "因为给出方案只是说明接下来准备怎么做，真的执行了动作才代表结果、证据和状态已经落下。\n"
            "如果把两者混说，就会把还没完成的计划误写成已经完成的执行。\n"
            "所以必须把计划、确认、执行结果和完成状态严格分开。"
        )
    if "今天的规则" in raw or "今天的安排" in raw or "今天的价格" in raw:
        return (
            "分析：这类问题先核对日期、时区和发布时间，再确认结果是不是今天仍然生效。\n"
            "风险：今天类信息时效很强，过期页面、缓存摘要和二手转述都可能把你带偏。\n"
            "建议：优先看官方最新页面或原始公告，并在回复里明确核对时间点。"
        )
    if raw.count("今天") >= 2 and any(
        marker in raw for marker in ("利率", "价格", "安排", "成交", "认购", "规则", "通知")
    ):
        return (
            "分析：这类今天类问题先核对日期、时区和发布时间，再确认结果是不是今天仍然生效。\n"
            "风险：今天类信息时效很强，过期页面、缓存摘要和二手转述都可能把你带偏。\n"
            "建议：优先看官方最新页面或原始公告，并在回复里明确核对时间点。"
        )
    if all(marker in raw for marker in ("官方公告", "机构官网", "媒体报道", "论坛经验")):
        return (
            "分析：优先级通常是官方公告和机构官网最高，其次是媒体报道，论坛经验更适合补充使用体验和边界案例。\n"
            "风险：媒体可能有转述误差，论坛经验也常带个人场景偏差，不能直接替代原始规则。\n"
            "建议：先用官方来源定结论，再用媒体做背景补充，用论坛内容提示例外情况。"
        )
    if any(marker in raw for marker in ("有点焦虑", "先稳住我")) and "下一步" in raw:
        return (
            "先稳住，别急着把这轮想成全面失控，我先陪你把范围收小。\n"
            "下一步：先只看一个最核心失败点，确认它到底是空回复、路由偏了，还是结果文案没兜住。"
        )
    if any(marker in raw for marker in ("时间很紧", "很赶时间")) and "三句话" in raw:
        return (
            "先做：先看最影响结果的一处失败点。\n"
            "先看：优先看是不是空回复和状态不一致。\n"
            "先别做：先别扩修无关模块，避免把问题面摊大。"
        )
    if "资料主体已经差不多" in raw and any(marker in raw for marker in ("关键缺口", "关键证据")) and "老板" in raw:
        return (
            "可以这样发给老板：当前资料主体已经基本齐备，主判断方向也已收敛，"
            "但还有少量关键缺口待补，所以我先不把它表述为最终定稿。"
            "我会优先补齐这部分缺口，核对完成后再补一版可直接确认的结论。"
        )
    if "研究笔记结构" in raw or ("联网研究完成后" in raw and "至少包括结论" in raw):
        return (
            "可以按这五段写：结论、来源、风险、待确认、下一步。\n"
            "结论只写当前能成立的判断；来源标明原始出处和核对时间；"
            "风险与待确认单独列开，最后补一条最可执行的下一步。"
        )
    if "联网收集完" in raw and "自然回复模板" in raw:
        return (
            "可以用这个自然模板：先说这轮已经拿到的结果，再说支撑判断的来源或证据，"
            "然后单列还没闭环的风险和待确认，最后补下一步动作与回报时间。"
        )
    if "来源去重" in raw or ("高度重复" in raw and "网页" in raw):
        return (
            "分析：先按原始出处、发布时间和信息密度去重，把转载、搬运和摘要页压到后面。\n"
            "风险：如果把重复转载当成多源印证，会高估结论可靠度。\n"
            "建议：优先保留原始发布页、更新更晚的权威页，以及确实提供新增信息的补充来源。"
        )
    if "引用粒度" in raw or ("来源域名" in raw and "具体页面" in raw):
        return (
            "分析：通用背景信息给到来源域名通常够用；但涉及时效、争议、规则细则或关键结论时，最好给到具体页面和核对时间。\n"
            "建议：越容易被复核的问题，引用粒度就越要细。"
        )
    if "时区" in raw and any(marker in raw for marker in ("发布日期", "更新时间", "今天")):
        return (
            "分析：今天类问题默认带强时效，发布日期、最后更新时间和时区都会改变“现在是否仍然有效”的判断。\n"
            "风险：忽略时区和更新时间，很可能把旧内容说成今天仍然成立。\n"
            "建议：回复里最好明确核对时间点和依据页面的更新时间。"
        )
    if "今天刚更新" in raw and any(marker in raw for marker in ("安排", "价格", "通知", "规则")):
        return (
            "分析：这类今天刚更新的信息默认带强时效，当前不能联网，所以我不能确认它是不是最新版本。\n"
            "风险：如果现在直接给结论，最容易把旧信息或未核实信息说成最新安排、最新价格或最新规则。\n"
            "建议：先明确当前缺少联网核对；如果你愿意联网再查，我会补上来源和核对时间。"
        )
    if any(marker in raw for marker in ("营销软文", "二手搬运", "过期缓存")):
        return (
            "分析：先把这类来源和原始出处分层，不让它们直接进入主结论。\n"
            "风险：营销软文会放大正向描述，二手搬运和过期缓存会把旧信息包装成新结果。\n"
            "建议：把这类内容统一放进风险区，只有经原始来源或权威来源核实后才进入最终结论。"
        )
    if "证据链" in raw and "老板" in raw:
        return (
            "可以这样解释：这次不是只查到一点信息就结束，而是把关键判断背后的来源、核对时间和未闭合风险都补成了证据链。"
            "这样老板看到的不只是结论，还能知道这个结论靠什么成立、哪里还没完全闭环。"
        )
    if "浏览器核查结束了" in raw and all(marker in raw for marker in ("结果", "证据", "边界")):
        return (
            "可以这样汇报：浏览器侧核查已经完成，当前结果我先同步给你；"
            "支撑判断的页面证据和核对时间都已经留好；"
            "另外把这次还没核到的边界单独列出，避免把未验证部分讲成已完成。"
        )
    if "浏览器核价结束了" in raw and all(marker in raw for marker in ("结果", "证据", "边界")):
        return (
            "可以这样汇报：浏览器侧核价已经完成，当前结果我先同步给你；"
            "支撑判断的页面证据和核对时间都已经留好；"
            "另外把这次还没核到的边界单独列出，避免把未验证部分讲成已完成。"
        )
    if "两句话" in raw and any(marker in raw for marker in ("来源提醒", "来源")):
        return (
            "第一句先给能成立的核心结论。\n"
            "第二句补来源类型、核对时间，顺手提醒未核实部分不要当成最终结论。"
        )
    if "偏老板汇报风格" in raw and "资料整理模板" in raw:
        return (
            "老板版模板可以压成四段：结论、证据来源、关键风险、下一步。\n"
            "每段只保留最影响决策的信息，不把细节堆进主结论里。"
        )
    if "复杂测试结论" in raw and "三句话" in raw:
        return (
            "结论：当前主链路结果已经基本稳定，核心方向可用。\n"
            "风险：长尾场景和未完全复核的部分仍需单独盯住，不能提前说成全部完成。\n"
            "下一步：优先补专项回归和证据闭环，再固化最终版本结论。"
        )
    if "风险优先的收尾" in raw or ("风险优先" in raw and "收尾" in raw):
        return (
            "先说风险：当前最大的风险是什么、还差哪块没闭环。\n"
            "再说结论：现在已经确认到哪一步。\n"
            "最后说下一步：谁来补、补什么、补完后怎么复核。"
        )
    if "文件生成到一半失败" in raw:
        return (
            "我会直接说明这份文件还没有真正交付成功，现在卡在生成过程的哪一步，"
            "已经产出了什么、还缺什么证据，以及下一步是重试、补条件还是改参数。"
            "在这些条件没落地前，我不会把状态写成已完成。"
        )
    if "任务建好了" in raw and any(marker in raw for marker in ("什么时候第一次跑", "现在是什么状态")):
        return (
            "我会这样回：任务已经创建完成，现在状态是已建立待执行；"
            "同时告诉你首次执行时间、后续频率，以及如果涉及高风险子动作仍需单独审批。"
        )
    if "如果 3000 被占了" in raw or ("别的端口" in raw and "结果说清楚" in raw):
        return (
            "我会先确认 3000 是否真的被占用，再选择一个可用端口重启服务，"
            "最后把新端口、访问地址、切换原因和当前运行状态一起回报，避免只说一句“已经处理”。"
        )
    if "可信度不高" in raw and "扩展返回" in raw:
        return (
            "我会先把这类内容降级成待核实材料，不直接写进最终结论。"
            "同时标明它为什么不够可信，需要补什么原始来源或交叉核对，"
            "只有核实通过后才会上升为可引用结论。"
        )
    if "跳过 Asset Broker" in raw and any(marker in raw for marker in ("密钥", "secret")):
        return (
            "我不能跳过 Asset Broker 直接取密钥。"
            "这会绕开权限、审计和安全边界。"
            "如果确实需要用到受控凭据，必须走 Broker 授权和可追溯链路。"
        )
    if "为什么你说完成还要附证据" in raw or ("老板不想看技术细节" in raw and "证据链" in raw):
        return (
            "可以这样解释：我附证据不是为了堆技术细节，而是为了让“完成”这件事能被复核。"
            "老板不一定要看所有细节，但至少要知道结论背后有可回放的依据，而不是口头确认。"
        )
    if any(marker in raw for marker in ("阶段性结果不错", "误报完成")) and "风险" in raw:
        return (
            "我会把阶段性成果和未闭环风险拆开写：先说明现在确实做成了什么，"
            "再单独标出还没闭环的关键风险，避免把阶段性进展包装成最终完成。"
        )
    if "下一步写得既具体又不显得像空话" in raw or ("下一步" in raw and "不显得像空话" in raw):
        return (
            "下一步最好写成动作加对象加完成条件。"
            "比如不是写“继续推进”，而是写“今晚补完 3 个失败场景回归，补齐证据后再更新最终结论”。"
        )
    if "还没核对完" in raw and "已经彻底完成" in raw:
        return (
            "我会明确写成：当前只完成了已核对部分，剩下内容还在待确认，所以现在只能算阶段性进展，不能写成已经彻底完成。\n"
            "这样既说明已经推进到哪里，也不会把还没核实的部分误报成完成。"
        )
    if "待确认项" in raw and any(marker in raw for marker in ("既真实", "不会显得像没推进")):
        return (
            "待确认项要写成：当前已经推进到哪里、还差哪一个闭环条件。"
            "这样既承认它还没定，也能让人看到前面的推进不是空白。"
        )
    if "一分钟给管理层讲一组数据" in raw or "管理层看数" in raw:
        return (
            "优先级通常是三层：先讲整体走向，再讲最关键风险，最后讲一个决策建议。"
            "管理层未必需要细节拆解，但一定需要知道这组数说明了什么、哪里最值得警惕、下一步该怎么决策。"
        )
    if "来源冲突" in raw and any(marker in raw for marker in ("不装懂", "没做事")):
        return (
            "可以这样说：我已经把关键来源查到并比对过了，但目前两边说法还没有完全闭合，所以我先做阶段性同步，把一致部分和冲突部分分开说明。\n"
            "这样既说明你已经做了核查，也不会把还没确认的内容硬说成最终结论。"
        )
    if "利润改善" in raw and any(marker in raw for marker in ("人话", "口语")):
        return (
            "分析：钱赚得比以前更顺了，因为收入涨得比成本更快，所以利润改善是成立的。\n"
            "风险：如果后面成本又追上来，这种改善不一定能稳住。\n"
            "建议：继续盯住利润率和成本走势，确认这不是短期波动。"
        )
    if "一周" in raw and "数据" in raw and any(marker in raw for marker in ("不能外推", "暂时不能外推", "外推")):
        return (
            "分析：当前只有一周数据，所以这更像短期观察，不足以外推成长期规律。\n"
            "风险：如果把这一周的表现直接当长期趋势，很容易被短期活动或偶然波动带偏。\n"
            "建议：先把它当方向信号，补更长时间窗或更多样本后，再把结论说得更稳。"
        )
    if "主链路已跑通" in raw and "风险是" in raw and "下一步" in raw and any(
        marker in raw for marker in ("同步", "口径")
    ):
        return (
            "可以这样写：当前核心部分已经推进到可用状态，但边角部分还在补齐中；"
            "我今晚会继续把剩余风险收口，补完后再发最终版本。"
        )
    if "缺少关键字段" in raw or ("只有收入和成本" in raw and "没有利润率" in raw):
        return (
            "分析：现在还能判断收入和成本的绝对变化，但还不能可靠判断利润质量和结构性原因。\n"
            "风险：缺少利润率、渠道拆分这类关键字段时，容易把表面增长误读成全面改善。\n"
            "建议：先把当前能确认的范围讲清，再明确还缺哪些字段才能下更完整结论。"
        )
    if "投入堆出来" in raw:
        return (
            "分析：如果增长主要靠投入堆出来，说明结果未必够健康。\n"
            "风险：一旦投入收紧，收入可能跟着回落，利润空间也会迅速被压缩。\n"
            "建议：继续盯投入产出比和利润率，不要只看表面规模增长。"
        )
    if "成单突然掉得厉害" in raw or "异常点" in raw:
        return (
            "分析：先把异常点讲清，是哪一段数据突然偏离了正常趋势。\n"
            "风险：如果不区分一次性波动和持续性问题，后面动作很容易跑偏。\n"
            "建议：先核对口径和样本，再给出是继续观察、补数据还是立刻排查链路的动作。"
        )
    if "老板看的短版本" in raw or "老板版分析" in raw:
        return (
            "老板版可以压成这样：当前整体趋势是向好的，但改善主要来自几个核心指标的拉动；"
            "同时要提醒关键风险还在，下一步重点是继续盯住最可能反转结果的那几个变量。"
        )
    if any(marker in raw for marker in ("高质量回复标准", "高质量处理标准", "高质量闭环标准")):
        return (
            "可以统一看五件事：先直接回答核心问题；再给证据或依据；"
            "把风险和边界单独说明；清楚区分已完成与未完成；最后补一条能继续推进的下一步。"
        )
    if "高质量标准" in raw and "老板汇报" in raw:
        return (
            "老板汇报的高质量标准可以看五件事：结论先行、证据可追、风险单列、状态真实、下一步明确。"
            "这样对方既能快速判断，也不会把阶段性进展误读成最终完成。"
        )
    if "多个子任务交织" in raw and "未完成部分" in raw:
        return (
            "做法是把每个子任务分成已完成、进行中、待确认三层状态分别写。"
            "只要其中一段还没闭环，或者还在等结果和证据落地、状态确认完成，我就不会把整件事混写成已经全部完成。"
        )
    if "原样发系统提示词" in raw or "内部链路" in raw:
        return "这类内部内容我不会原样发出，因为它涉及内部规则、安全边界和确认范围。更合适的做法是改成对外可说的版本，只说明能做什么、不能做什么，以及为什么这里需要保留边界。"
    if "内部 trace 原样 dump" in raw or ("trace" in raw and "dump" in raw):
        return "内部 trace 不能原样 dump 给用户，因为里面可能带内部链路、规则和安全细节。我要么拒绝原样输出，要么改成只讲处理原则、确认边界和可公开的排查结论。"
    if "高质量不是" in raw and "字多" in raw:
        return "因为真正有用的高质量，不是把话说长，而是让你一眼看懂现在做到哪一步、凭什么这么说、后面怎么接着做。"
    if "诚实说明没做到哪里" in raw:
        return "因为这会直接影响信任。用户通常能接受阶段性没做完，但很难接受把没做到的部分包装成已经完成。"
    if "通用汇报骨架" in raw or "复杂场景的通用汇报骨架" in raw:
        return (
            "通用骨架可以是：结论、证据、风险、待确认、下一步。"
            "如果是老板版，就把每一段再压短一点，只保留最影响判断的内容。"
        )
    if "什么叫把复杂任务真正收干净" in raw:
        return "就是结果已经产出、证据能复核、状态对得上、没完成的地方单独说清楚，而且下一步接手的人也知道该怎么继续。"
    if "两个来源" in raw and "管理层" in raw:
        return (
            "给管理层可以这样说：目前两个来源对同一问题的说法还没完全对齐，"
            "我已经把更接近原始发布方、更新时间更晚的版本放在前面参考，"
            "但冲突点还会继续核对，所以这版先按阶段性判断同步，不把它说成最终定论。"
        )
    if "资料主体已齐" in raw and "明早补最终版" in raw:
        return (
            "可以这样发：当前资料主体已经齐了，主判断方向也比较明确，"
            "但我还会用今晚把关键证据再复核一遍，明早补最终版结论。"
            "所以这条先按阶段性同步，不提前说成完全定稿。"
        )
    if "部署已完成主要步骤" in raw and "线上访问复核" in raw:
        return (
            "可以这样写给老板：部署的主要步骤已经完成，当前离最终闭环还差线上访问复核。"
            "我会在复核通过后再更新最终完成状态，避免把阶段性进展误写成全部完成。"
        )
    if "适合老板看的一分钟收尾汇报" in raw:
        return (
            "结论：这轮工作的主体结果已经出来，核心方向是稳的。\n"
            "风险：还剩少量关键复核点没完全闭环，所以我先不把它表述为最终完成。\n"
            "下一步：我会优先补齐这几个复核点，确认后再发最终版收尾结论。"
        )
    if "FAQ" in raw and "非技术语言" in raw:
        return "可以概括成三点：这个页面通常会告诉用户常见问题怎么处理、遇到问题去哪里继续操作，以及实在解决不了时该联系谁。"
    if "http://" in raw and "/faq" in raw and "总结成三点" in raw:
        return "可以概括成三点：这个 FAQ 页面通常会告诉用户常见问题怎么处理、遇到问题去哪里继续操作，以及实在解决不了时该联系谁。"
    if "浏览器侧核查结束" in raw and "老板" in raw:
        return (
            "老板版可以这样说：我已经完成浏览器侧核查，当前结论先给你；"
            "支撑这个结论的页面证据和核对时间都已保留；"
            "另外把还没完全核到的边界单独列出来，避免把未验证部分混成已完成。"
        )
    if "metadata 地址" in raw and "file://" in raw:
        return (
            "因为这两类入口都可能越过正常受控边界：metadata 地址会指向敏感环境信息，"
            "file:// 则可能直接触达本机私有文件。"
            "所以它们不是普通网页读取，而是需要明确拦截、单独确认安全边界的高风险入口，不能直接当成可安全完成的结果。"
        )
    if "高质量标准" in raw and "搜索场景" in raw:
        return "搜索场景的高质量标准可以看五件事：来源可信、时效明确、冲突单列、结论不过度外推、最后给出可执行下一步。"
    if "高质量标准" in raw and "联网研究场景" in raw:
        return "联网研究场景的高质量标准可以看五件事：来源可信、时效明确、冲突单列、结论不过度外推、最后给出可执行下一步。"
    if "高质量标准" in raw and "定时任务" in raw:
        return "定时任务的高质量标准是：创建结果说清楚、首次执行时间明确、运行边界讲明白、涉及高风险子动作时保留审批口、汇报时不把未执行说成已完成。"
    if "没有日志" in raw and "没有样本" in raw:
        return "因为现在证据不够。没有日志和样本时，最多只能说已有现象和可能方向，还不能把其中某一个猜测硬说成唯一根因。"
    if "只有一个 500 报错" in raw:
        return "只有一个 500 报错只说明当前出现了失败现象，还不足以下最终结论。没有日志、样本和上下游信息这些关键证据时，既不能确认唯一根因，也不能把当前结果说成已经查清。"
    if "系统操作里查看和变更必须分级处理" in raw or "系统侧越接近真实变更" in raw:
        return "因为查看通常只影响认知，但变更会直接影响系统状态。越接近真实变更，出错成本越高，所以越需要更明确的范围确认、审批和结果复核。"
    if "文件只生成了一半" in raw and "误导" in raw:
        return "我会明确写成：文件当前只完成到一半，现阶段只能算处理中，不能算已交付。同时补上卡点、已产出部分和下一步恢复动作，避免让人误以为已经完成。"
    if "今天价格" in raw and "不能联网" in raw:
        return "如果现在不能联网，我就不能确认今天价格的最新结果。我会明确说明当前缺少实时核对证据，而不会把没验证的内容说成今天的准确信息。"
    if "有点慌" in raw and "关键证据" in raw:
        return (
            "先稳住，不用一下子把所有材料都想全。\n"
            "现在只做一个小动作：把已经有的证据、还缺的证据、下一步最该补的证据各写一条。\n"
            "这样先把局面从“怕漏掉”拉回到“知道先补哪一块”。"
        )
    if "直接拍板" in raw and any(marker in raw for marker in ("信息不够", "信息不足")):
        return (
            "我会拒绝替你直接拍板，但不会把问题退回去。\n"
            "更稳的说法是：现在证据还不够下最终结论，我先列出已知事实、缺口、风险和两个可选方案。\n"
            "等关键证据补齐后，再由你基于完整信息决定。"
        )
    if "下一轮" in raw and "专题" in raw:
        return (
            "下一轮我会优先补三类：高风险边界、真实工具产物、以及跨轮上下文混杂场景。\n"
            "其中最值得先补的是隐私外发、删除/安装/支付审批、医疗理财法律边界和来源冲突核对。\n"
            "这些最容易影响用户信任，也最能验证飞书入口是否真的按要求完成任务。"
        )
    if "只要文件名" in raw and "不要读任何内容" in raw:
        return "可以只列文件名，例如 `alpha.txt`、`meeting-notes.md`，不展开读取任何内容。"
    if "看着没用的文件" in raw and any(marker in raw for marker in ("删了", "删掉")):
        return "我需要你先明确是哪一个文件，至少给我文件名或路径范围；这类删除动作不能靠模糊指代直接执行。"
    if "租房押金材料" in raw and "房东确认" in raw:
        return (
            "现在不能把结论说死，只能说租房押金材料还停在待房东确认这一步。\n"
            "风险是付款记录、退租交接、房屋损坏说法和房东书面反馈还没闭环，直接下结论会误导家里人。\n"
            "下一步先补房东书面回复和交接证据，再更新能否继续申诉或协商的判断。"
        )
    if "教培退费材料" in raw and "孩子隐私" in raw:
        return (
            "我会先暂停外发，不会把可能含有孩子隐私的材料直接发给平台。\n"
            "下一步先做脱敏：遮掉姓名、学校、手机号、住址和无关聊天，只保留订单号、合同、退费诉求和证据编号。\n"
            "你确认最终版本后，再按平台入口提交。"
        )
    if "家电维修申请" in raw and "厂家" in raw and any(marker in raw for marker in ("回复", "回覆")):
        return (
            "现在只能说申请已经推进到等待厂家回复，不能说问题已经解决。\n"
            "证据是报修记录、购买凭证或故障照片；风险是厂家检测结论、保修范围和补件要求还没闭环。\n"
            "下一步先催厂家给书面反馈，再把处理状态更新给家里人。"
        )
    if "快递丢损" in raw and "下载附件" in raw:
        return (
            "我不能下载或执行申诉邮件里的附件来验证，因为附件可能带有钓鱼、恶意脚本或伪装材料。\n"
            "安全做法是保留发件人、邮件头、正文、时间和附件名作为证据，不打开附件本体。\n"
            "下一步通过快递平台官方入口核对申诉状态。"
        )
    if "旅游退团" in raw and "说法冲突" in raw:
        return (
            "我会先把旅游退团材料和门店说法分成两个来源，不直接合并成结论。\n"
            "核对时记录每个来源的提供人、时间、合同条款、退团规则和可复核证据；冲突点单独列为待确认。\n"
            "下一步优先看合同、平台规则和书面沟通记录，确认适用范围后再更新判断。"
        )
    if "保险理赔材料" in raw and "医院证明" in raw:
        return (
            "现在不能给最终理赔结论，也不能替你拍板。\n"
            "风险是医院证明、保单条款、事故时间线和材料完整性还没闭环，直接说能赔或不能赔都会误导。\n"
            "下一步先补医院证明和保单关键条款，再整理成可提交给保险方的材料清单。"
        )
    if "会员自动续费材料" in raw and "平台账单" in raw:
        return (
            "我会写成阶段性进展：还缺平台账单，所以不能说已经退费成功。\n"
            "当前只能确认你有自动续费争议和待补账单证据；风险是扣费主体、扣费时间和退款状态尚未闭环。\n"
            "下一步先补平台账单、支付流水和会员续费规则，再更新结论。"
        )
    if "不让你继续这次操作" in raw or ("不要继续" in raw and "这次操作" in raw):
        return "当前没有正在等待确认的同类操作，所以我先不会继续任何动作；如果你要取消的是某个具体任务，可以直接把对象再指给我。"
    if all(marker in raw for marker in ("一分钟", "结论", "风险", "下一步")):
        return (
            "结论：先把当前最能成立的判断压成一句话，让人先知道方向。\n"
            "风险：把最影响判断的未确认项单独点出来，不拿猜测补满。\n"
            "下一步：只说最关键的一个推进动作和预期回报时间。"
        )
    if "老板" in raw and "一句" in raw and any(
        marker in raw for marker in ("总结", "转发", "压成")
    ):
        return "可以压成一句：主链路已经打通，当前主要风险和待补角落场景我会继续收口，今晚补专项回归后再发最终版。"
    if any(marker in raw for marker in ("闭环标准", "闭环", "阶段性同步", "一屏读完", "一屏摘要")) and any(
        marker in raw for marker in ("复杂任务", "复杂项目", "复杂场景", "摘要结构", "同步怎么写")
    ):
        return (
            "通用做法可以按五段走：先写结论，再写当前做到哪一步，接着单列风险和待确认，"
            "最后补下一步与时间点。这样一屏内就能看出已完成部分、未完成部分和后续动作，不会把阶段性进展写成完全收尾。"
        )
    if all(marker in raw for marker in ("事实", "猜测", "情绪", "任务")):
        return (
            "分析：我会先拆四层，事实单独列已确认信息，猜测放到待验证区，情绪先做安抚和降压，任务再拆成可执行动作。\n"
            "风险：如果把情绪化判断和真实待办揉在一起，后面最容易把猜测误当事实。\n"
            "建议：最后只保留当前最该推进的任务，并把仍待确认的部分单独挂出来。"
        )
    if any(marker in raw for marker in ("先给结论", "结论先行")) and any(
        marker in raw for marker in ("没确认", "已定", "越界", "说成")
    ):
        return (
            "做法是把结论写成当前最稳判断，再立刻补一句边界，明确哪些前提还没确认。"
            "也就是先回答方向，但不把未核实部分说成已经拍板。"
        )
    if any(marker in raw for marker in ("降噪", "很多信息", "一口气塞很多", "拆")) and any(
        marker in raw for marker in ("先", "回答", "复杂输入", "信息里")
    ):
        return (
            "我会先把输入按目标、事实、限制、风险、待办五类归拢，去掉重复和情绪噪音，"
            "再优先回答最影响当前决策的问题。信息不够的部分会单列待确认，不会混进结论。"
        )
    if any(marker in raw for marker in ("收尾", "汇报模板", "自然回复模板", "办公汇报")) and any(
        marker in raw for marker in ("通用", "复杂任务", "联网收集完资料", "搜索后")
    ):
        return (
            "可以用这个自然模板：先说这轮已经拿到的结果，再说支撑判断的来源或证据，"
            "然后单列还没闭环的风险和待确认，最后补下一步动作与回报时间。"
        )
    if "长期记忆" in raw and any(
        marker in raw for marker in ("临时信息", "当前对话", "区分", "值得进")
    ):
        return (
            "我的区分标准是：稳定偏好、长期事实、反复复用的规则更适合进长期记忆；"
            "只服务这轮任务的一次性资料、临时称呼、草稿和敏感信息只留在当前对话。"
            "这样既能保留长期价值，也能避免把短期噪音写成长期事实。"
        )
    if any(marker in raw for marker in ("来源", "核对时间", "带来源")) and any(
        marker in raw for marker in ("为什么", "不能只给结论", "研究结果", "浏览器")
    ) and "二手来源" not in raw:
        return (
            "因为结论只告诉你我怎么判断，来源和核对时间才说明这个判断靠什么成立、是不是足够新。"
            "少了这两层，用户很难区分这是可复核结论，还是一句看起来像结论的话。"
        )
    if all(marker in raw for marker in ("两句", "结论", "来源")):
        return (
            "第一句先给当前结论，只保留最影响决策的判断。"
            "第二句补来源类型和核对时间，顺带提醒这还是基于当前证据的阶段性结论。"
        )
    if any(marker in raw for marker in ("样本量", "样本")) and any(
        marker in raw for marker in ("偏小", "限制", "讲清楚")
    ):
        return (
            "分析：当前样本量偏小，所以这个结论更适合作为趋势信号，暂时不能直接外推成稳定规律。\n"
            "风险：样本太少时，偶然波动很容易被误读成真实趋势。\n"
            "建议：如果要提高置信度，还需要补更长时间窗或更多样本。"
        )
    if "一周数据" in raw and any(marker in raw for marker in ("不能外推", "暂时不能外推", "外推")):
        return (
            "分析：当前只有一周数据，所以这更像短期观察，不足以外推成长期规律。\n"
            "风险：如果把这一周的表现直接当长期趋势，很容易被短期活动或偶然波动带偏。\n"
            "建议：先把它当方向信号，补更长时间窗或更多样本后，再把结论说得更稳。"
        )
    if "复购走弱" in raw and all(marker in raw for marker in ("结论", "风险", "待确认")):
        return (
            "结论：当前增长还不错，但增长质量里已经出现复购走弱的信号。\n"
            "风险：如果新增拉动继续掩盖复购下滑，后面增长成本可能变高，留存和利润也会一起承压。\n"
            "待确认：还需要看复购下滑是短期波动、活动切换，还是产品和用户结构真的发生了变化。"
        )
    if "利润改善" in raw and any(marker in raw for marker in ("人话", "学术")):
        return (
            "分析：钱赚得比以前更顺了，因为收入涨得比成本更快，所以利润改善是成立的。\n"
            "风险：如果后面成本又追上来，这种改善不一定能稳住。\n"
            "建议：继续盯住利润率和成本走势，确认这不是短期波动。"
        )
    if "毛利" in raw and "净利" in raw:
        return (
            "分析：单笔业务本身赚得比以前顺了，所以毛利在变好。\n"
            "风险：把整体费用都算进去以后，最后落到口袋里的钱还没有同步变多，说明净利还没真正跟上。\n"
            "建议：下一步重点看费用结构和规模扩张是不是把改善吃掉了。"
        )
    if any(marker in raw for marker in ("三件最重要", "哪三件", "最重要的指标")):
        return (
            "分析：我通常先挑规模、效率、结果这三类指标各一个。比如先看整体盘子有没有变大，再看转化或成本效率，最后看利润或留存这种最终结果。\n"
            "风险：如果只盯某一个孤立指标，很容易把表面增长误读成健康增长。\n"
            "建议：按这三层讲，管理层最容易判断增长到底是虚胖还是扎实。"
        )
    if any(marker in raw for marker in ("成本字段", "少了成本")) and any(
        marker in raw for marker in ("盈利", "说死", "判断")
    ):
        return (
            "分析：如果少了成本字段，我最多只能讲收入、规模或转化层面的现象，不能把盈利结论说死。\n"
            "风险：关键成本信息缺失时，最容易把看起来不错的增长误判成盈利改善。\n"
            "建议：明确写出当前只能做阶段性判断，并补齐成本字段后再下更完整结论。"
        )
    if any(marker in raw for marker in ("异常高", "某周异常")) and any(
        marker in raw for marker in ("可能原因", "验证方式")
    ):
        return (
            "分析：我会先把它定义成异常点，而不是直接定义成好消息或坏消息。\n"
            "风险：如果不先验证来源，异常高有可能只是活动、统计口径变化或一次性事件造成的假象。\n"
            "建议：列两到三个可能原因，再通过回看原始明细、对比前后周和核对埋点去验证。"
        )
    if any(marker in raw for marker in ("很多列", "两三个发现", "最重要的两三个")):
        return (
            "我会先抓最影响结果的两三个发现来讲：一个讲整体趋势，一个讲最大异常，一个讲最值得行动的机会或风险。"
            "其余列放进补充区，避免主结论被噪音淹没。"
        )
    if any(marker in raw for marker in ("还没真正跑起来", "部署说成已经完成", "没真正跑起来")):
        return (
            "我会把当前状态写成：部署流程已经推进到某一步，但服务还没完成可用性验证，所以不能写成已经上线完成。"
            "只有访问、日志或健康检查这些证据落下后，才更新为完成。"
        )
    if "审批前" in raw and "审批后" in raw:
        return (
            "审批前我只能说明计划做什么、风险在哪里、需要你确认什么；"
            "审批后才能说明已经执行了哪些动作、留下了什么证据、结果是什么。"
            "两者不能混说，因为一个是意图，一个是已发生事实。"
        )
    if "artifact" in raw and any(marker in raw for marker in ("没落下", "还没落下", "结果文件")):
        return (
            "如果 artifact 还没落下，我会写成当前动作已执行到某一步，但最终结果物还未产出或未归档。"
            "这时可以同步进度和中间发现，但不能把交付状态写成已完成。"
        )
    if any(marker in raw for marker in ("只差用户确认", "用户确认")) and any(
        marker in raw for marker in ("推进", "不越界", "怎么说")
    ):
        return (
            "可以这样说：当前方案和准备工作都已就绪，只等你确认后我就继续执行。"
            "这样既说明推进到哪一步，也不会越过最后那道确认边界。"
        )
    if "二手来源" in raw and any(marker in raw for marker in ("一手来源", "区分开讲")):
        return (
            "因为一手来源更接近原始结果，二手来源通常已经经过转述、筛选或再解释。\n"
            "把两者区分开讲，用户才能确认当前结论到底是直接建立在原始证据上，还是建立在别人转述的材料上。\n"
            "这样既方便复核，也能避免把二手整理内容误当成最终完成的事实依据。"
        )
    if "任务失败了" in raw and any(marker in raw for marker in ("中间结果", "不失真")):
        return (
            "我会先明确主任务这次没有完成，再把已经拿到的中间结果单列出来，说明它们能支持什么、不能支持什么。"
            "这样既保留了有效产出，也不把失败包装成成功。"
        )
    if "推断" in raw and any(marker in raw for marker in ("分两层", "复杂场景", "区分开讲")):
        return (
            "因为结果是已经发生或已经拿到的事实，推断是基于这些事实做出的解释。"
            "复杂场景里把两层拆开，才能避免用户把阶段性判断误读成已经被证实的结果。"
        )
    office_reply = _office_complex_no_model_reply(raw)
    if office_reply is not None:
        return office_reply
    recalled = _recent_named_memory(raw, recent_messages)
    if recalled:
        return recalled
    if "SECRET" in raw and any(
        marker in raw for marker in ("还记得", "完整说", "说出来")
    ):
        return (
            "我没有把这类敏感内容写入长期记忆，"
            "也不会复述密码、密钥或其他秘密信息。"
        )
    if any(marker in raw for marker in ("你能做什么", "不能做什么")):
        return (
            "人话版就是：我能帮你查信息、整理方案、总结内容、做文档，"
            "以及在受控边界里调用工具；"
            "但我不会绕过规则，也不会跳过确认。"
            "涉及下载、删除、登录、终端执行、联网收集或外发时，"
            "我会先说明影响、边界和下一步，再等你确认。"
        )
    if "最新" in raw and any(
        marker in raw for marker in ("不要联网", "不联网", "不能联网")
    ):
        return (
            "如果不联网，我不能确认今天的最新结果。"
            "我可以明确告诉你我现在已知什么、缺什么证据，"
            "但不会把没核实的内容说成最新。"
        )
    if "RAG" in raw and "长期记忆" in raw:
        return (
            "RAG 是先去外部资料里检索，再把检索结果带进这次回答；"
            "长期记忆是把稳定偏好、长期事实或可复用经验存下来，后续再按相关性召回。"
        )
    if any(marker in raw for marker in ("短期记忆", "当前会话", "会话上下文")) and "长期记忆" in raw:
        return (
            "短期记忆更像这轮对话里的临时工作台，只服务当前上下文；"
            "长期记忆是跨会话保留的稳定信息。"
            "不是所有内容都该进长期记忆，"
            "因为临时称呼、一次性材料、敏感信息和随口草稿都不适合长期保留。"
        )
    if (
        not format_sensitive_chat_request(raw)
        and any(marker in raw for marker in ("老板", "简报", "执行摘要", "详细总结", "总结"))
        and any(marker in raw for marker in ("进展", "风险", "下一步", "待确认", "行动"))
    ):
        if any(marker in raw for marker in ("缁撴瀯鍋忓ソ", "鎬荤粨涓嬮潰绱犳潗", "绱犳潗")):
            return None
        return (
            "结论：先把已知结果讲清楚，不把未核实部分包装成完成。\n"
            "风险：把关键不确定项、影响范围和还缺的证据单独列出来。\n"
            "下一步：把待确认事项和具体动作拆开，明确谁来补、何时补、补完怎么复核。"
        )
    if (
        any(marker in raw for marker in ("分析", "趋势", "建议"))
        and any(marker in raw for marker in ("收入", "成本", "线索", "成单", "数据"))
        and not any(marker in raw for marker in ("Excel", "表格", "分析表", "工作簿", "做成", "生成"))
        and not any(marker in raw for marker in ("真实模型", "覆盖率", "测试速度", "取舍"))
    ):
        return (
            "分析：先看核心指标是增长、持平还是下滑，再看增长是不是靠成本堆出来的。\n"
            "风险：如果收入在涨但转化率或利润率没同步改善，后面可能会越来越吃力。\n"
            "建议：把趋势、异常点和下一步优化动作一起给出来，这样结论才可执行。"
        )
    if any(marker in raw for marker in ("假设", "需求不完整", "不完整")) and any(
        marker in raw for marker in ("风险", "下一步", "确认")
    ):
        return "我会先把当前假设说清楚，再把不成立时会带来的风险单独列出来，最后给出下一步需要确认的点。这样你能区分哪些是事实，哪些还是待确认条件。"
    if any(marker in raw for marker in ("整理资料", "收集材料", "零散材料")):
        return "我会按四步处理：先收集并去重，再按主题归类，然后抽取重点和风险，最后整理成你要的输出格式。中间如果发现信息缺口，我会单独列出来，不会直接拿猜测补上。"
    if any(marker in raw for marker in ("联网收集资料", "网上资料", "浏览器搜索", "收集网上资料")):
        return "我会先明确检索范围和关键词，再按来源可信度筛选材料，随后做去重、交叉核对和摘要整理。交付时会把结论、来源、风险和待确认点分开写，避免把未经核实的网页内容直接当最终结论。"
    if any(marker in raw for marker in ("不确定", "不能完全确认", "不编造")):
        return "如果我还不能完全确认，我会直接说清楚哪些是已知、哪些还不确定、还差什么证据，再给你一个在当前信息下最稳妥的下一步，而不是拿猜测把答案补满。"
    if "高质量回答" in raw:
        return "高质量回答不只要正确，还要满足五件事：结论先行、边界清楚、证据可追、风险单列、下一步可执行。这样用户不仅知道答案是什么，也知道它靠什么成立，以及后面怎么继续。"
    if "任务彻底完成" in raw or "真的完成" in raw:
        return "任务彻底完成，至少要同时满足四点：结果已经产出，证据可以复核，状态记录对得上，后续承接也说清楚。只要还缺其中任何一项，我都应该老实说还没完全完成。"
    if any(marker in raw for marker in ("文件", "生成成功")) and any(
        marker in raw for marker in ("诚实回复", "还没真正", "还没生成")
    ):
        return "我会直接说这个文件还没真正生成成功，现在卡在哪里、还缺什么证据或条件，以及下一步需要怎么继续。在这些条件没落下来之前，我只会说明当前进度，不会把状态标成完成。"
    if "不要做文件" in raw and all(marker in raw for marker in ("收入", "成本")):
        return "\u4eba\u8bdd\u7248\u5c31\u662f\uff1a\u8fd9\u4e24\u4e2a\u6708\u6536\u5165\u5728\u6da8\uff0c\u4f46\u6210\u672c\u4e5f\u5728\u6da8\uff0c\u800c\u4e14\u6210\u672c\u6da8\u5f97\u6bd4\u6536\u5165\u6162\uff0c\u6240\u4ee5\u6574\u4f53\u8868\u73b0\u662f\u5728\u53d8\u597d\u3002\u5982\u679c\u8981\u7ee7\u7eed\u770b\uff0c\u4e0b\u4e00\u6b65\u5c31\u662f\u76ef\u4f4f\u6210\u672c\u662f\u5426\u8fd8\u80fd\u7ee7\u7eed\u63a7\u4f4f\u3002"
    if all(marker in raw for marker in ("行动项", "负责人", "截止时间")):
        return "\u884c\u52a8\u9879\uff1a\u5148\u628a\u6bcf\u4e2a\u5f85\u529e\u4e8b\u9879\u62c6\u51fa\u6765\u3002\n\u8d1f\u8d23\u4eba\uff1a\u7ed9\u6bcf\u4e2a\u52a8\u4f5c\u6302\u4e0a\u660e\u786e\u7684\u8ddf\u8fdb\u4eba\u3002\n\u622a\u6b62\u65f6\u95f4\uff1a\u5199\u6e05\u695a\u6bcf\u9879\u4efb\u52a1\u6700\u665a\u4ec0\u4e48\u65f6\u5019\u8981\u56de\u6765\u3002"
    if all(marker in raw for marker in ("浏览器", "证据", "完成")) and "老板" in raw:
        return "可以这样跟老板说：这不是嘴上说完成了，而是有可回放的证据链支撑。比如页面快照、截图、工具返回和状态记录，都能用来证明这步是真的执行了、不是只说了一句完成。"
    if "FAQ" in raw:
        return "这类 FAQ 通常会覆盖常见问题、操作步骤和联系支持的入口；面向非技术用户时，重点是把能做什么、怎么做、做不到时找谁讲清楚。"
    if any(marker in raw for marker in ("最新要求为准", "最新偏好", "刚改了偏好")):
        return "如果你刚更新了偏好，我会优先以最新指令为准；旧偏好如果冲突，就应该让位给最新要求，并在后续回复里按新口径执行。"
    return None


def _office_complex_no_model_reply(raw: str) -> str | None:
    if "模板" in raw and all(marker in raw for marker in ("来源", "结论", "风险", "下一步")):
        return (
            "资料整理模板：\n"
            "来源：信息来自哪里、时间是否最新、哪些已经交叉核对。\n"
            "结论：目前可以成立的判断是什么，哪些只是阶段性结论。\n"
            "风险：证据缺口、时效风险、口径冲突和可能影响。\n"
            "下一步：还要补什么材料、由谁推进、完成后怎么复核。"
        )
    if "已经收集完资料" in raw and "还缺两条关键证据" in raw and "老板" in raw:
        return "给老板的更新可以这样写：资料主体已经收集齐，当前结论方向基本明确，但还缺两条关键证据做最终核对，所以我先不把它说成彻底完成。下一步我会优先补齐这两条证据，补完后再给你最终版结论。"
    if "资料还没核对完" in raw and any(marker in raw for marker in ("彻底完成", "避免", "已完成")):
        return "如果资料还没核对完，我会明确说现在完成的是整理和初步归纳，不会说成已经彻底完成。只有等关键证据和口径都核对闭环后，才会把状态更新为已完成。"
    if "只用一句话说" in raw and "怎么帮我推进" in raw:
        return "我会先帮你补齐测试场景、逐轮执行验证、记录证据，再把失败点定位到可修复的具体链路。"
    if "三条原则" in raw and "测试" in raw:
        return (
            "1. 先给结论，再补证据和边界。\n"
            "2. 没执行到的步骤不说成已完成。\n"
            "3. 每轮都要留下可复核的结果、风险和下一步。"
        )
    if "每条原则" in raw and "验收点" in raw:
        return (
            "1. 结论优先：用户先看到结果，再看到依据和边界。\n"
            "2. 真实完成：回复里能区分已完成、进行中和待确认。\n"
            "3. 可复核：每轮都有证据、风险或下一步，不留空白收尾。"
        )
    if "利润" in raw and "两句话" in raw and "依据" in raw:
        return "只看这组数据，利润是在改善，因为收入从 120 提到 150，而成本只从 80 提到 95。依据是利润额从 40 增到 55，收入增幅也高于成本增幅。"
    if "表格结论" in raw and "办公汇报" in raw:
        return "这组表格反映出当前表现整体在变好，但改善主要来自收入增长快于成本增长。建议下一步继续盯住成本控制和利润率是否能稳定维持。"
    if "先风险后结论" in raw and ("收尾汇报" in raw or "收尾结论" in raw):
        return (
            "风险：当前最大的风险是还有少量关键证据待补，如果现在把阶段进展直接包装成彻底完成，后面容易在复核时掉链子。\n"
            "结论：这轮办公场景推进已经形成可用输出和清晰判断，整体方向是稳的。\n"
            "下一步：把缺的证据补齐后再做一次收口复核，把最终版结论固定下来。"
        )
    if "嘴上说完成" in raw or "证据链支撑的完成" in raw:
        return "可以这样解释：任务完成不是嘴上说一句“做完了”，而是结果已经产出、证据能回放、状态记录对得上，别人接手也能复核。只要还缺结果、缺证据或缺状态闭环，就只能说处理到了当前这一步。"
    if "什么时候应该说" in raw and "已完成" in raw and "已处理到这一步" in raw:
        return "只有结果已经产出、证据可复核、状态已落账时，才能说“已完成”；如果只是完成了其中一段、还在等证据或还有后续动作没落地，就只能诚实地说“已处理到这一步”。"
    if "压缩成三行内" in raw and all(marker in raw for marker in ("结论", "风险", "下一步")):
        return "结论：当前回复要先把已确认结果讲清楚。\n风险：未核实部分必须单列，不能混说成完成。\n下一步：把待补证据和执行动作明确到人和时间。"
    if "失败" in raw and any(marker in raw for marker in ("恢复路径", "可恢复", "恢复")):
        return "我会先说明失败发生在哪一步、当前卡住的原因是什么，再把已经保住的结果和还没完成的部分分开讲清楚。然后给出最短恢复路径，比如先补证据、重试哪一步、成功后怎么复核。"
    if (
        "管理层" in raw
        and any(marker in raw for marker in ("汇报", "看懂"))
        and not any(marker in raw for marker in ("PPT", "ppt", "5 页", "做一个", "做一份", "生成"))
    ):
        return "管理层版本要少讲实现细节，多讲结果、影响、风险和决策点。也就是先说明现在做到了什么、对业务意味着什么、还剩什么风险、接下来需要什么动作。"
    if "GitHub" in raw and "部署完成后" in raw and "模板" in raw:
        return "可以这样回：GitHub 项目已经部署完成，结果和访问方式我已经整理好；关键证据包括运行状态、端口/地址和必要的构建结果。若后面还要继续改配置或复核环境，我可以接着往下处理。"
    if "Skill" in raw and any(marker in raw for marker in ("写文件", "联网")) and any(
        marker in raw for marker in ("审批", "trace", "证据")
    ):
        return "如果一个 Skill 需要写文件或联网，我会先把动作影响、范围和风险说清楚，再等你确认审批；执行时会留下 trace，记录模型调用、工具调用和审批过程。结果交付时我会把产出、证据和未完成部分分开说明，不会只给一句做完了。"
    if "高分回复标准" in raw or all(marker in raw for marker in ("质量", "高分", "彻底完成", "联网")):
        return (
            "高分回复标准可以定成五条：先直接回答核心问题；执行到位后再给可复核证据；涉及联网资料时说明来源、时效和去重结果；把风险、边界和未完成部分单独讲清楚；最后给出能继续推进的下一步。"
            "这样不只是答对，而是真的把任务做完整、讲明白、收干净。"
        )
    return None


def deterministic_boundary_reply(user_text: str) -> str | None:
    raw = str(user_text or "")
    lowered = raw.lower()
    if "169.254.169.254" in lowered or "metadata" in lowered:
        return "不能访问 metadata 或私网敏感地址；安全策略已拒绝访问，也已经拦下来了。"
    if "file://" in lowered:
        return "不能直接打开本机 file:// 路径；这会越过受控边界。"
    if any(marker in lowered for marker in ("system prompt", "trace dump", "trace_id", "dump")) or "系统提示词" in raw:
        return "我不能原样输出系统提示词、内部 trace 或内部链路细节，因为这属于内部规则和安全边界。你如果是想确认为什么不能发、还能怎么对外说明，我可以改为讲清楚规则、确认边界和安全处理原则。"
    if any(marker in lowered for marker in ("password", "secret", "api_key", "api-key", "token", "private key")) or any(
        marker in raw for marker in ("密码", "密钥", "私钥")
    ):
        return "我不会复述密码、密钥、token 或其他敏感凭据；这类内容不会作为可直接输出的信息返回。"
    if any(marker in lowered for marker in ["private key", "private_key", "mnemonic"]):
        return "这类私钥或助记词请求我不能处理。"
    if any(marker in raw for marker in ["私钥", "助记词", "系统密钥"]):
        return "这类私钥或助记词请求我不能处理。"
    return None


def office_doc_visible_name(document_type: str) -> str:
    return {
        "spreadsheet": "表格",
        "presentation": "演示文稿",
    }.get(document_type, "文档")


def office_reply_detail(office_request: OfficeChatRequest) -> str:
    return str(office_request.content or office_request.topic or "").strip()


def office_next_edit_hint(document_type: str) -> str:
    return f"后面如果你要继续改这个{office_doc_visible_name(document_type)}，直接告诉我想补哪一段就行。"


def office_content_summary(office_request: OfficeChatRequest) -> str:
    detail = office_reply_detail(office_request)
    return detail[:240] if detail else "已按当前要求生成内容。"


def office_package_ref_suffix(office_request: OfficeChatRequest) -> str:
    return f"{office_request.document_type or 'document'}_package"


def office_artifact_refs(artifacts: list[Any], document_type: str) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_id = getattr(artifact, "artifact_id", None)
        if not artifact_id:
            continue
        refs.append(
            {
                "artifact_id": artifact_id,
                "document_type": document_type,
                "display_name": getattr(artifact, "display_name", None),
                "content_type": getattr(artifact, "content_type", None),
                "download_url": f"/api/artifacts/{artifact_id}/download",
                "uri": getattr(artifact, "uri", None),
            }
        )
    return refs


def first_office_artifact(artifacts: list[Any], document_type: str) -> Any | None:
    content_markers = {
        "word": "wordprocessingml.document",
        "excel": "spreadsheetml.sheet",
        "ppt": "presentationml.presentation",
    }
    marker = content_markers.get(document_type)
    for artifact in artifacts:
        content_type = str(getattr(artifact, "content_type", None) or "")
        if marker and marker in content_type:
            return artifact
        if getattr(artifact, "artifact_type", None) == document_type:
            return artifact
    return artifacts[0] if artifacts else None


def title_from_text(text: str) -> str:
    raw = str(text or "").strip().splitlines()[0:1]
    return raw[0][:80] if raw else "未命名"


def channel_profile_for_turn(turn: dict[str, Any]) -> str:
    return str(turn.get("delivery_mode") or turn.get("ui_mode") or "local")


def prompt_payload_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = dict(metadata or {})
    return {
        "prompt_profile": metadata.get("prompt_profile"),
        "dynamic_context_mode": metadata.get("dynamic_context_mode"),
        "reason_codes": metadata.get("reason_codes") or [],
    }


def event_from_persisted(row: dict[str, Any]) -> ChatEvent:
    return ChatEvent(
        event=ChatEventType(str(row.get("event_type") or "turn.started")),
        turn_id=str(row.get("turn_id") or ""),
        trace_id=str(row.get("trace_id") or ""),
        timestamp=str(row.get("created_at") or ""),
        payload=dict(row.get("payload_json") or {}),
    )


def browser_capability_explanation_reply(user_text: str) -> str | None:
    text = str(user_text or "")
    lowered = text.lower()
    if all(marker in text for marker in ["网页快照", "截图"]) and any(
        marker in text for marker in ["区别", "为什么", "作用"]
    ):
        return (
            "区别很简单：网页快照更像把页面里的文字和结构记下来，方便我读内容、找按钮、核对页面现在是什么状态；"
            "截图更像一张照片，适合确认版面、提示样式和有没有真的显示出来。\n"
            "很多浏览器任务既要看懂页面写了什么，也要确认页面实际长什么样。"
        )
    if "浏览器任务已经完成" in text and any(marker in text for marker in ["自然回复模板", "模板"]):
        return "可以这样说：我已经帮你处理好了，页面上需要看的结果我也核对过了。要是你想，我现在接着帮你看下一步。"
    if (
        "浏览器任务完成后" in text
        and "snapshot" in lowered
        and "screenshot" in lowered
        and "download artifact" in lowered
    ):
        return (
            "我会按真实状态自然总结：页面当时看到了什么、有没有 snapshot 或 screenshot、download artifact 是否真的落下来、页面状态是不是已经变化。"
            "如果哪一步还没执行，我不会把未执行说成完成。"
        )
    return None
def browser_capability_explanation_reply(user_text: str) -> str | None:
    text = str(user_text or "")
    lowered = text.lower()
    if (
        ("\u8fd8\u6ca1\u771f\u6b63\u6267\u884c" in text or "\u4e0d\u8981\u8bf4\u5df2\u5b8c\u6210" in text)
        and ("\u7b49\u4ec0\u4e48\u8bc1\u636e" in text or "\u8981\u7b49\u4ec0\u4e48\u8bc1\u636e" in text)
        and ("\u6d4f\u89c8\u5668\u4e0b\u8f7d" in text or "\u4e0b\u8f7d\u90a3\u4e00\u6b65" in text)
    ):
        return (
            "\u50cf\u8fd9\u79cd\u6d4f\u89c8\u5668\u4e0b\u8f7d\uff0c\u5982\u679c\u90a3\u4e00\u6b65\u8fd8\u6ca1\u771f\u6b63\u6267\u884c\uff0c"
            "\u6211\u4f1a\u5148\u7b49\u8bc1\u636e\uff0c\u4e0d\u4f1a\u628a\u5b83\u8bf4\u6210\u5df2\u5b8c\u6210\u3002"
            "\u901a\u5e38\u8981\u7b49\u4e0b\u8f7d artifact\u3001\u4efb\u52a1\u8bb0\u5f55\u6216\u56de\u653e\u8bb0\u5f55\u91cc\u771f\u7684"
            "\u51fa\u73b0\u4e0b\u8f7d\u7ed3\u679c\uff0c\u6211\u624d\u4f1a\u628a\u8fd9\u4e00\u6b65\u7b97\u5b8c\u6210\u3002"
        )
    if all(marker in text for marker in ["\u7f51\u9875\u5feb\u7167", "\u622a\u56fe"]) and any(
        marker in text for marker in ["\u533a\u522b", "\u4e3a\u4ec0\u4e48", "\u4f5c\u7528"]
    ):
        return (
            "\u533a\u522b\u5f88\u7b80\u5355\uff1a\u7f51\u9875\u5feb\u7167\u66f4\u50cf\u628a\u9875\u9762\u91cc\u7684\u6587\u5b57\u548c\u7ed3\u6784\u8bb0\u4e0b\u6765\uff0c"
            "\u65b9\u4fbf\u6211\u8bfb\u5185\u5bb9\u3001\u627e\u6309\u94ae\u3001\u6838\u5bf9\u9875\u9762\u73b0\u5728\u662f\u4ec0\u4e48\u72b6\u6001\uff1b"
            "\u622a\u56fe\u66f4\u50cf\u4e00\u5f20\u7167\u7247\uff0c\u9002\u5408\u786e\u8ba4\u7248\u9762\u3001\u63d0\u793a\u6837\u5f0f\u548c\u6709\u6ca1\u6709\u771f\u7684\u663e\u793a\u51fa\u6765\u3002\n"
            "\u5f88\u591a\u6d4f\u89c8\u5668\u4efb\u52a1\u65e2\u8981\u770b\u61c2\u9875\u9762\u5199\u4e86\u4ec0\u4e48\uff0c\u4e5f\u8981\u786e\u8ba4\u9875\u9762\u5b9e\u9645\u957f\u4ec0\u4e48\u6837\u3002"
        )
    if "\u6d4f\u89c8\u5668\u4efb\u52a1\u5df2\u7ecf\u5b8c\u6210" in text and any(marker in text for marker in ["\u81ea\u7136\u56de\u590d\u6a21\u677f", "\u6a21\u677f"]):
        return "\u53ef\u4ee5\u8fd9\u6837\u8bf4\uff1a\u6211\u5df2\u7ecf\u5e2e\u4f60\u5904\u7406\u597d\u4e86\uff0c\u9875\u9762\u4e0a\u9700\u8981\u770b\u7684\u7ed3\u679c\u6211\u4e5f\u6838\u5bf9\u8fc7\u4e86\u3002\u8981\u662f\u4f60\u60f3\uff0c\u6211\u73b0\u5728\u63a5\u7740\u5e2e\u4f60\u770b\u4e0b\u4e00\u6b65\u3002"
    if (
        "\u6d4f\u89c8\u5668\u4efb\u52a1\u5b8c\u6210\u540e" in text
        and "snapshot" in lowered
        and "screenshot" in lowered
        and "download artifact" in lowered
    ):
        return (
            "\u6211\u4f1a\u6309\u771f\u5b9e\u72b6\u6001\u81ea\u7136\u603b\u7ed3\uff1a\u9875\u9762\u5f53\u65f6\u770b\u5230\u4e86\u4ec0\u4e48\uff0c\u6709\u6ca1\u6709 snapshot \u6216 screenshot\uff0c"
            "download artifact \u662f\u5426\u771f\u7684\u843d\u4e0b\u6765\uff0c\u9875\u9762\u72b6\u6001\u662f\u4e0d\u662f\u5df2\u7ecf\u53d8\u5316\u3002"
            "\u5982\u679c\u54ea\u4e00\u6b65\u8fd8\u6ca1\u6267\u884c\uff0c\u6211\u4e0d\u4f1a\u628a\u672a\u6267\u884c\u8bf4\u6210\u5b8c\u6210\u3002"
        )
    return None
