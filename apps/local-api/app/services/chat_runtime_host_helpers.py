from __future__ import annotations

import html
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from brain.adapters import ModelAdapterError
from core_types import ChatEvent, ChatEventType, ChatTurnRequest, ContextPacket
from response_composer import canonical_action_status
from trace_service import redact

from app.core.errors import AppError
from app.services.chat_intent_router import OfficeChatRequest
from app.services.chat_turn_input_facts import format_sensitive_chat_request


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
    }


def queue_payload(queue_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue_id": queue_item.get("queue_id"),
        "status": queue_item.get("status"),
        "session_id": queue_item.get("session_id"),
        "queue_policy": queue_item.get("queue_policy"),
        "position": queue_item.get("position"),
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
        )
    )


def direct_route_reply(route_type: str, user_text: str) -> str | None:
    del user_text
    if route_type == "office_document":
        return "这类请求更适合走文档生成流程，我会按文档任务来组织结果。"
    if route_type == "browser_read":
        return "这类请求适合走网页只读链路，我会优先给出页面内容和证据。"
    return None


def host_filesystem_list_reply(result: dict[str, Any]) -> str:
    location = host_filesystem_label(str(result.get("location") or "home"))
    entries = list(result.get("entries") or [])
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
    visible = truncate_browser_text(
        clean_browser_text(str(result.get("visible_text") or "")),
        360,
    )
    if title and visible:
        return f"我看了这个页面。标题是《{title}》，主要内容是：{visible}"
    if title:
        return f"我看了这个页面，标题是《{title}》。"
    if visible:
        return f"我看了这个页面，主要内容是：{visible}"
    return "我打开了这个页面，但这次没有拿到足够的可读正文。"


def browser_read_page_error_reply(exc: AppError) -> str:
    if exc.error_code == "browser_file_url_denied":
        return "不能直接打开本机 file:// 路径；这会越过受控边界。"
    return "这次没能顺利读到页面内容，你可以换个地址或稍后再试。"


def browser_read_page_payload(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": result.get("title"),
        "url": result.get("url"),
        "visible_text": truncate_browser_text(
            clean_browser_text(str(result.get("visible_text") or "")),
            1200,
        ),
        "evidence_refs": result.get("evidence_refs") or [],
    }


def terminal_command_reply(command: str, result: dict[str, Any]) -> str:
    output = clean_terminal_output(str(result.get("stdout") or result.get("output") or ""))
    if not output:
        return f"命令 `{command}` 已执行，但这次没有可展示的输出。"
    return f"`{command}` 的输出是：\n{output}"


def terminal_command_error_reply(command: str, exc: AppError) -> str:
    if exc.error_code == "permission_denied":
        return f"命令 `{command}` 这次没有通过权限边界。"
    return f"命令 `{command}` 这次没跑通。"


def clean_terminal_output(value: str) -> str:
    return str(redact(value or "")).strip()[:2000]


def browser_visible_text(raw: str) -> str:
    return truncate_browser_text(clean_browser_text(raw), 2400)


def clean_browser_text(value: str) -> str:
    return html.unescape(str(value or "")).replace("\u00a0", " ").strip()


def truncate_browser_text(value: str, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def strategy_advice_fallback_text(user_text: str) -> str:
    return (
        "我先给你一个稳妥版本："
        if any(marker in str(user_text or "") for marker in ("方案", "建议", "优化"))
        else "我先按当前信息给你一个直接回答："
    )


def deterministic_no_model_reply(user_text: str) -> str | None:
    raw = str(user_text or "")
    if "你能做什么" in raw:
        return "我可以先帮你分析、整理方案、比较取舍，也能在有明确目标和边界时继续往执行链路走。"
    return None


def deterministic_boundary_reply(user_text: str) -> str | None:
    raw = str(user_text or "")
    lowered = raw.lower()
    if "169.254.169.254" in lowered or "metadata" in lowered:
        return "不能访问这类元数据或私有网络地址；安全策略已拒绝。"
    if "file://" in lowered:
        return "不能直接打开本机 file:// 路径；这会越过受控边界。"
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
    return str(office_request.summary or office_request.user_text or "").strip()


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
                "uri": getattr(artifact, "uri", None),
            }
        )
    return refs


def first_office_artifact(artifacts: list[Any], document_type: str) -> Any | None:
    for artifact in artifacts:
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
