from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from response_composer import canonical_action_status

from app.schemas.chat_quality import ActionDialogueDecision, ActionDialogueFacts


class ActionDialogueMapperService:
    def map(self, facts: ActionDialogueFacts) -> ActionDialogueDecision:
        route_semantics = dict(facts.route_semantics or {})
        natural = dict(facts.natural_interaction or {})
        task_status = dict(facts.task_status or {})
        route = str(route_semantics.get("route") or "")
        related_capabilities = [route] if route else []
        domain = str(facts.domain or route_semantics.get("domain") or "").strip().lower()

        if domain == "scheduled_task" or route == "scheduled_task":
            return _map_scheduled_task(facts, task_status, related_capabilities)

        natural_status = canonical_action_status(natural.get("status"), default="")
        if facts.approval_pending or natural_status == "waiting_for_approval":
            return ActionDialogueDecision(
                action_status="waiting_for_approval",
                narration_style="approval_waiting",
                natural_transition="ask_for_confirmation",
                should_explain_pending=True,
                should_claim_completion=False,
                blocked_by_approval=True,
                visible_failure_strategy="boundary_helpful",
                related_capabilities=related_capabilities,
                reason_codes=["approval_pending"],
            )
        status = canonical_action_status(task_status.get("status"), default="")
        if status in {"planned", "executing", "paused"}:
            return ActionDialogueDecision(
                action_status=status,
                narration_style="brief_progress",
                natural_transition="status_update",
                should_explain_pending=True,
                should_claim_completion=False,
                blocked_by_approval=False,
                visible_failure_strategy="defer_with_anchor",
                related_capabilities=related_capabilities,
                reason_codes=[f"task_status:{status}"],
            )
        if status in {"completed_with_evidence", "partially_completed"}:
            return ActionDialogueDecision(
                action_status=status,
                narration_style=(
                    "tool_contextual"
                    if any(marker in route for marker in ["browser", "terminal", "skill", "mcp", "host"])
                    else "result_first"
                ),
                natural_transition="deliver_result",
                should_explain_pending=False,
                should_claim_completion=True,
                blocked_by_approval=False,
                visible_failure_strategy="partial_honest",
                related_capabilities=related_capabilities,
                reason_codes=[f"task_status:{status}"],
            )
        if status in {"failed_with_reason", "blocked_by_boundary", "cancelled"}:
            return ActionDialogueDecision(
                action_status=status,
                narration_style=(
                    "tool_contextual"
                    if any(marker in route for marker in ["browser", "terminal", "skill", "mcp", "host"])
                    else "partial_honest"
                ),
                natural_transition="repair_or_retry",
                should_explain_pending=False,
                should_claim_completion=False,
                blocked_by_approval=False,
                visible_failure_strategy="retry_softly",
                related_capabilities=related_capabilities,
                reason_codes=[f"task_status:{status}"],
            )
        if any(marker in route for marker in ["browser", "terminal", "skill", "mcp"]):
            return ActionDialogueDecision(
                action_status="tool_context",
                narration_style="tool_contextual",
                natural_transition="answer_with_action_context",
                should_explain_pending=False,
                should_claim_completion=False,
                blocked_by_approval=False,
                visible_failure_strategy="partial_honest",
                related_capabilities=related_capabilities,
                reason_codes=["route_capability_context"],
            )
        return ActionDialogueDecision(reason_codes=["no_action"])


def _map_scheduled_task(
    facts: ActionDialogueFacts,
    task_status: dict[str, Any],
    related_capabilities: list[str],
) -> ActionDialogueDecision:
    schedule = dict(task_status.get("schedule") or facts.route_semantics.get("schedule") or {})
    visible_goal = _visible_scheduled_goal(
        facts.visible_goal
        or facts.target
        or str(task_status.get("goal") or "")
        or facts.action_label
    )
    human_schedule = facts.human_schedule or _human_scheduled_task_schedule(schedule)
    boundary = facts.sensitive_boundary_notice or _scheduled_sensitive_boundary(visible_goal)
    requires_confirmation = bool(facts.requires_user_confirmation or boundary)
    quality_flags = [
        *list(facts.quality_flags or []),
        "deterministic_visible_reply",
        "no_internal_schedule_terms",
    ]
    if boundary:
        quality_flags.append("sensitive_action_boundary")
    return ActionDialogueDecision(
        domain="scheduled_task",
        action_status="scheduled_created",
        narration_style="natural_confirmation",
        natural_transition="confirm_scheduled_task",
        should_explain_pending=False,
        should_claim_completion=True,
        blocked_by_approval=False,
        visible_failure_strategy="boundary_helpful" if boundary else "partial_honest",
        related_capabilities=related_capabilities or ["scheduled_task"],
        reason_codes=["scheduled_task_visible_mapper"],
        visible_goal=visible_goal,
        human_schedule=human_schedule,
        sensitive_boundary_notice=boundary,
        requires_user_confirmation=requires_confirmation,
        quality_flags=quality_flags,
        visible_text=_scheduled_visible_reply(
            visible_goal=visible_goal,
            human_schedule=human_schedule,
            boundary=boundary,
        ),
    )


def _scheduled_visible_reply(*, visible_goal: str, human_schedule: str, boundary: str) -> str:
    goal = visible_goal or "这件事"
    schedule = human_schedule or "到时候"
    separator = " " if schedule[-1:].isdigit() else ""
    goal_separator = " " if re.match(r"^[A-Za-z0-9]", goal) else ""
    if boundary:
        if boundary.startswith("医疗相关"):
            return f"好，{schedule}{separator}提醒你{goal_separator}{goal}。到点我会提醒你，{boundary}"
        boundary_text = boundary
        if boundary_text.startswith("到时候我会先提醒你，"):
            boundary_text = boundary_text.removeprefix("到时候我会先提醒你，")
        return f"好，{schedule}{separator}提醒你{goal_separator}{goal}。到点我会先提醒你确认，{boundary_text}"
    return f"好，{schedule}{separator}提醒你{goal_separator}{goal}。到点我会直接叫你。"


def _visible_scheduled_goal(goal: str) -> str:
    text = " ".join(str(goal or "").strip().split())
    text = text.strip("。！？!?.,，；;：: “”（）()[]【】'\" ")
    text = re.sub(
        r"^(?:please\s+)?(?:create|set(?:\s+up)?|add|make)\s+(?:a\s+)?(?:scheduled\s+)?reminder(?:\s+(?:for|to))?[，,：:\s]*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^(?:scheduled\s+reminder|reminder)[，,：:\s]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:please\s+)?remind\s+me\s*(?:to|about)?[，,：:\s]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:帮我)?(?:创建|新建|设置|设个|加个|建个)?(?:一个|个)?(?:定时任务|提醒)[，,：:\s“”'\"\[\]【】]*", "", text)
    text = text.strip("。！？!?.,，；;：: “”（）()[]【】'\" ")
    text = re.sub(r"^帮我[，,：:\s]*", "", text)
    text = re.sub(r"^(?:每天|每日|每周|每隔)[，,：:\s]*", "", text)
    text = re.sub(
        r"^(?:再过|过|等|等到)?\s*\d+\s*(?:秒|分钟|小时|天|second|seconds|minute|minutes|hour|hours|day|days)\s*(?:后|later)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"^(?:周一|周二|周三|周四|周五|周六|周日|周天|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[一二三四五六七八九十两0-9]+\s*(?:分钟|小时|天|minutes|minute|hours|hour|days|day)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(?:明天|明早|明天早上|明天上午|明天下午|明天晚上)\s*", "", text)
    text = re.sub(r"^\d{1,2}\s*[:：点]\s*\d{0,2}\s*", "", text)
    text = re.sub(r"^(?:上午|早上|中午|下午|晚上)\s*\d{1,2}\s*[:：]\s*\d{2}\s*", "", text)
    text = re.sub(r"^(?:上午|早上|中午|下午|晚上)\s*\d{1,2}\s*点\s*(?:半|[0-9]{1,2}\s*分?)?\s*", "", text)
    text = re.sub(r"^提醒(?:我|你)?[，,：:\s]*", "", text)
    if re.search(r"提醒(?:我|你)?", text):
        text = re.split(r"提醒(?:我|你)?[，,：:\s]*", text)[-1]
    text = re.sub(r"^帮我[，,：:\s]*", "", text)
    text = re.sub(r"，?\s*不要自动(?:扣款|付款|支付)[。.!！?？]*$", "", text)
    text = text.strip("。！？!?.,，；;：: “”（）()[]【】'\" ")
    if text.startswith("我"):
        text = text[1:].strip()
    return text or "这件事"


def _human_scheduled_task_schedule(schedule: dict[str, Any]) -> str:
    kind = str(schedule.get("type") or "").strip().lower()
    if kind == "daily":
        return f"以后每天{_friendly_clock(schedule.get('time') or '09:00')}"
    if kind == "weekly":
        days = schedule.get("days") or []
        days_text = "、".join(str(item) for item in days) if isinstance(days, list) and days else "每周"
        return f"以后每周{days_text}{_friendly_clock(schedule.get('time') or '09:00')}"
    if kind == "interval":
        seconds = int(schedule.get("every_seconds") or schedule.get("seconds") or 0)
        if seconds > 0 and seconds % 86400 == 0:
            return f"以后每隔 {seconds // 86400} 天"
        if seconds > 0 and seconds % 3600 == 0:
            return f"以后每隔 {seconds // 3600} 小时"
        if seconds > 0 and seconds % 60 == 0:
            return f"以后每隔 {seconds // 60} 分钟"
        if seconds > 0:
            return f"以后每隔 {seconds} 秒"
    if kind == "once":
        return _friendly_once_schedule(str(schedule.get("run_at") or schedule.get("at") or ""))
    return "到时候"


def _friendly_clock(value: object) -> str:
    text = str(value or "09:00").strip()
    match = re.match(r"^(\d{1,2}):(\d{2})", text)
    if not match:
        return f" {text}"
    hour = int(match.group(1))
    minute = int(match.group(2))
    period = ""
    display_hour = hour
    if 5 <= hour < 12:
        period = "早上"
    elif 12 <= hour < 14:
        period = "中午"
    elif 14 <= hour < 18:
        period = "下午"
        display_hour = hour - 12
    elif 18 <= hour < 24:
        period = "晚上"
        display_hour = hour - 12
    if minute == 0:
        return f"{period} {display_hour} 点"
    return f"{period} {display_hour}:{minute:02d}"


def _friendly_once_schedule(run_at: str) -> str:
    text = str(run_at or "").strip()
    if not text:
        return "到时候"
    if "T" in text:
        parsed = _parse_iso_datetime(text)
        if parsed is not None:
            tz = timezone(timedelta(hours=8), "Asia/Shanghai")
            local_run = parsed.astimezone(tz)
            now = datetime.now(tz)
            delta_seconds = int((local_run - now).total_seconds())
            if 0 <= delta_seconds <= 90:
                return f"{max(1, delta_seconds)} 秒后"
            if 0 <= delta_seconds < 3600:
                minutes = max(1, round(delta_seconds / 60))
                return f"{minutes} 分钟后"
            day_prefix = "今天" if local_run.date() == now.date() else "明天" if local_run.date() == (now + timedelta(days=1)).date() else local_run.strftime("%m 月 %d 日")
            return f"{day_prefix}{_friendly_clock(local_run.strftime('%H:%M'))}"
        time_part = text.split("T", 1)[1][:5]
        return f"明天{_friendly_clock(time_part)}"
    return text


def _parse_iso_datetime(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _scheduled_sensitive_boundary(goal: str) -> str:
    text = str(goal or "")
    if any(marker in text for marker in ("付款", "转账", "打款", "支付", "银行卡", "钱包", "信用卡", "还款", "扣款")):
        return "这个我只提醒，不会自动付款。"
    if _looks_like_manual_sensitive_prep_reminder(text):
        return ""
    if any(
        marker in text
        for marker in (
            "删除",
            "卸载",
            "清空",
            "终端",
            "命令",
            "登录",
            "外发",
            "发布",
            "发送",
            "外部邮箱",
            "发资料",
            "外部邮箱",
            "提交",
            "重启",
            "改密码",
            "密码",
            "上传",
            "证件",
            "证件照片",
            "上传身份证",
            "导出",
            "投放",
            "客户清单",
            "客户资料",
            "批量归档",
        )
    ):
        return "到时候我会先提醒你，不会直接替你做高风险操作。"
    if any(marker in text for marker in ("用药", "吃药", "剂量", "药", "复诊", "医生")):
        return "医疗相关的细节以医生或药师确认的为准。"
    return ""


def _looks_like_manual_sensitive_prep_reminder(text: str) -> bool:
    if not any(marker in text for marker in ("上传", "证件", "身份证", "照片")):
        return False
    if any(marker in text for marker in ("自动上传", "自动发送", "外发", "外部邮箱", "发资料")):
        return False
    return any(marker in text for marker in ("打码", "脱敏", "遮住", "确认", "核对", "检查", "带"))
