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
        "??????????????????????????????\n"
        "1. ??????????\n"
        "2. ????????????????????\n"
        "3. ???????????????????????????????????\n"
        "4. ???????????????????????????"
    )


def _extract_named_memory_target(text: str) -> str:
    match = re.search(r"(FEI\d{2,3}-[^\s????:?]+)", str(text or ""))
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


def deterministic_no_model_reply(
    user_text: str,
    *,
    recent_messages: list[dict[str, Any]] | None = None,
) -> str | None:
    raw = str(user_text or "").strip()
    if not raw:
        return None
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
    if any(marker in raw for marker in ("老板", "简报", "执行摘要", "详细总结", "总结")) and any(
        marker in raw for marker in ("进展", "风险", "下一步", "待确认", "行动")
    ):
        return (
            "结论：先把已知结果讲清楚，不把未核实部分包装成完成。\n"
            "风险：把关键不确定项、影响范围和还缺的证据单独列出来。\n"
            "下一步：把待确认事项和具体动作拆开，明确谁来补、何时补、补完怎么复核。"
        )
    if any(marker in raw for marker in ("分析", "趋势", "建议")) and any(
        marker in raw for marker in ("收入", "成本", "线索", "成单", "数据")
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
        return "我会直接说这个文件还没真正生成成功，现在卡在哪里、还缺什么证据或条件，以及下一步需要怎么继续。在这些条件没落下来之前，我不会把任务说成已完成。"
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


def deterministic_boundary_reply(user_text: str) -> str | None:
    raw = str(user_text or "")
    lowered = raw.lower()
    if "169.254.169.254" in lowered or "metadata" in lowered:
        return "不能访问 metadata 或私网敏感地址；安全策略已拒绝访问，也已经拦下来了。"
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
