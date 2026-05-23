from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.chat_intent_router import (
    extract_host_software_name,
    host_software_action,
    is_host_software_install_request,
)
from app.services.chat_safety import ChatTaskStatusPresenter, TaskStatusPresentation


@dataclass(frozen=True)
class ScheduledTaskIntent:
    title: str
    goal: str
    schedule: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {"title": self.title, "goal": self.goal, "schedule": self.schedule}


@dataclass(frozen=True)
class ScheduledTaskCancelIntent:
    target_text: str = ""
    scheduled_task_id: str | None = None
    refers_latest: bool = False


class ScheduledTaskIntentCoordinator:
    """Parses conservative scheduled-task requests from ordinary chat text."""

    def parse(self, text: str) -> ScheduledTaskIntent | None:
        clean = " ".join(str(text or "").strip().split())
        if not clean:
            return None
        direct_only_markers = [
            "不要执行",
            "不要假装执行",
            "别假装执行",
            "不要声称执行",
            "不要创建",
            "不要创建任务",
            "不要创建提醒",
            "不要创建定时任务",
            "不要创建系统提醒",
            "不要真的创建",
            "不要新建提醒",
            "不要新建定时任务",
            "不要设置",
            "不要设置提醒",
            "不要建任务",
            "不要建提醒",
            "不创建提醒",
            "不创建定时任务",
            "不要调用工具",
            "只解释",
            "只给方案",
        ]
        if any(marker in clean for marker in direct_only_markers) and not any(
            marker in clean for marker in ("不要创建模糊任务", "不要创建模糊的任务", "不要创建含糊任务")
        ):
            return None
        if _unsafe_scheduled_goal(clean):
            return None
        if _looks_like_text_generation_or_style_request(clean):
            return None
        if _looks_like_roleplay_chat_request(clean) and not _explicit_roleplay_schedule_request(clean):
            return None
        lowered = clean.lower()
        schedule: dict[str, Any] | None = None
        schedule_kind: str | None = None
        relative_once = _extract_relative_once(clean)
        if relative_once is not None:
            schedule_kind = "once"
            schedule = {
                "type": "once",
                "run_at": _relative_run_at(relative_once[0], relative_once[1]),
                "timezone": "Asia/Shanghai",
            }
        elif any(marker in clean for marker in ["每天", "每日"]) or _english_daily_schedule_request(lowered):
            schedule_kind = "daily"
            schedule = {
                "type": "daily",
                "time": _extract_clock_text(clean),
                "timezone": "Asia/Shanghai",
            }
        elif "每周" in clean or _english_weekly_schedule_request(lowered):
            schedule_kind = "weekly"
            schedule = {
                "type": "weekly",
                "days": [_extract_weekday(clean)],
                "time": _extract_clock_text(clean),
                "timezone": "Asia/Shanghai",
            }
        elif any(marker in clean for marker in ["明早", "明天早上", "明天上午", "明天"]):
            schedule_kind = "once"
            schedule = {
                "type": "once",
                "run_at": _tomorrow_run_at(_extract_clock_text(clean)),
                "timezone": "Asia/Shanghai",
            }
        else:
            interval = re.search(
                r"每隔\s*(\d+)\s*(分钟|小时|天|minute|minutes|hour|hours|day|days)",
                lowered,
            )
            if interval:
                amount = int(interval.group(1))
                unit = interval.group(2)
                multiplier = 60
                if unit in {"小时", "hour", "hours"}:
                    multiplier = 3600
                elif unit in {"天", "day", "days"}:
                    multiplier = 86400
                schedule_kind = "interval"
                schedule = {"type": "interval", "every_seconds": amount * multiplier}
        if schedule is None:
            return None
        scheduled_markers = [
            "帮我",
            "提醒",
            "定时",
            "创建定时任务",
            "新建定时任务",
            "remind me",
            "remind",
            "set a reminder",
            "create a reminder",
        ]
        if schedule_kind == "once" and not _explicit_once_schedule_request(clean):
            return None
        if not any(marker in clean for marker in scheduled_markers):
            return None
        goal = clean
        for marker in ["每天", "每日", "每周", "每隔", "every day", "daily", "every week", "weekly", "remind me", "please remind me"]:
            goal = goal.replace(marker, "", 1).strip()
        goal = _strip_schedule_words_from_goal(goal, schedule_kind)
        return ScheduledTaskIntent(
            title=_scheduled_title(goal),
            goal=goal or clean,
            schedule=schedule,
        )


class ScheduledTaskCancelIntentCoordinator:
    """Parses conservative chat requests to cancel an existing reminder."""

    def parse(self, text: str) -> ScheduledTaskCancelIntent | None:
        clean = " ".join(str(text or "").strip().split())
        if not clean:
            return None
        lowered = clean.lower()
        if any(marker in clean for marker in ("取消这次回复", "停止输出", "别回答了", "取消回答")):
            return None
        if _looks_like_action_inside_reminder(clean):
            return None
        if not any(
            marker in lowered or marker in clean
            for marker in (
                "取消",
                "撤销",
                "删掉",
                "删除",
                "停掉",
                "关掉",
                "停止提醒",
                "不用提醒",
                "别提醒",
                "不要提醒",
                "cancel",
                "stop reminding",
            )
        ):
            return None
        reminder_context = any(
            marker in lowered or marker in clean
            for marker in (
                "提醒",
                "定时任务",
                "定时",
                "闹钟",
                "刚才那个",
                "刚刚那个",
                "上一个",
                "最新那个",
                "那个任务",
                "那个",
                "reminder",
                "scheduled task",
            )
        )
        if not reminder_context:
            return None
        scheduled_task_id = _extract_scheduled_task_id(clean)
        target_text = _extract_cancel_target_text(clean)
        refers_latest = any(marker in clean for marker in ("刚才那个", "刚刚那个", "上一个", "最新那个", "上条"))
        if not target_text and not scheduled_task_id and not refers_latest and clean in {"取消", "撤销", "删掉", "停掉"}:
            return None
        return ScheduledTaskCancelIntent(
            target_text=target_text,
            scheduled_task_id=scheduled_task_id,
            refers_latest=refers_latest,
        )


def _explicit_once_schedule_request(text: str) -> bool:
    clean = " ".join(str(text or "").strip().split())
    lowered = clean.lower()
    has_clock = bool(re.search(r"\d{1,2}\s*[:：点]\s*\d{0,2}", clean))
    if not has_clock and any(marker in clean for marker in ("提醒我一句", "提醒我一声", "提醒我一下")):
        return False
    if any(
        marker in clean or marker in lowered
        for marker in ["提醒", "定时", "创建定时任务", "新建定时任务", "remind me", "set a reminder"]
    ):
        return True
    return has_clock and "帮我" in clean


def _unsafe_scheduled_goal(text: str) -> bool:
    clean = " ".join(str(text or "").strip().split())
    lowered = clean.lower()
    sensitive_markers = (
        "token",
        "secret",
        "私钥",
        "助记词",
        "验证码",
        "短信码",
        "动态码",
        "密码",
        "cookie",
    )
    outbound_or_execution_markers = (
        "发给外部",
        "发到外部",
        "外部供应商",
        "外部群",
        "转给",
        "发送",
        "外发",
        "付款",
        "打款",
        "转账",
        "删除",
        "rm -rf",
    )
    return any(marker in lowered or marker in clean for marker in sensitive_markers) and any(
        marker in lowered or marker in clean for marker in outbound_or_execution_markers
    )


def _strip_schedule_words_from_goal(goal: str, schedule_kind: str | None) -> str:
    text = " ".join(str(goal or "").strip().split())
    if schedule_kind == "weekly":
        text = re.sub(r"^(?:周)?[一二三四五六日天]\s*", "", text)
    text = re.sub(r"^(?:早上|上午|中午|下午|晚上)?\s*\d{1,2}\s*[:：]\s*\d{2}\s*", "", text)
    text = re.sub(r"^(?:早上|上午|中午|下午|晚上)?\s*\d{1,2}\s*点\s*(?:半|[0-9]{1,2}\s*分?)?\s*", "", text)
    text = re.sub(r"^提醒(?:我|你)?[，,：:\s]*", "", text)
    text = re.sub(r"，?\s*不要创建(?:模糊|含糊)的?任务[。.!！?？]*$", "", text)
    return text.strip("。！？!?.,，；;：: “”（）()[]【】'\" ")


def _english_daily_schedule_request(lowered: str) -> bool:
    return bool(re.search(r"\b(?:every\s+day|daily)\b", lowered))


def _english_weekly_schedule_request(lowered: str) -> bool:
    return bool(re.search(r"\b(?:every\s+week|weekly)\b", lowered))


def _looks_like_roleplay_chat_request(text: str) -> bool:
    raw = str(text or "")
    roleplay_markers = (
        "角色扮演",
        "扮演",
        "假装是",
        "假装成",
        "保持角色",
        "沿用角色",
        "继续刚才这个角色",
        "用这个角色",
        "角色口吻",
        "身份词",
        "叫我",
    )
    return any(marker in raw for marker in roleplay_markers) or bool(re.search(r"像.{1,24}一样", raw))


def _looks_like_text_generation_or_style_request(text: str) -> bool:
    raw = " ".join(str(text or "").strip().split())
    if not raw:
        return False
    if any(marker in raw for marker in ("提醒我一句", "提醒我一声", "提醒自己一句")):
        return True
    if any(
        marker in raw
        for marker in (
            "提醒我",
            "提醒你",
            "提醒一下",
            "设置提醒",
            "设个提醒",
            "创建提醒",
            "新建提醒",
            "定时提醒",
            "到点提醒",
        )
    ):
        return False
    generation_markers = (
        "写一句",
        "写一段",
        "写一个",
        "给一句",
        "给我一句",
        "给我一个",
        "文案",
        "话术",
        "口吻",
        "语气",
        "晚安",
        "结构",
        "段落感",
    )
    return any(marker in raw for marker in generation_markers)


def _explicit_roleplay_schedule_request(text: str) -> bool:
    clean = " ".join(str(text or "").strip().split())
    explicit_create_markers = (
        "创建定时任务",
        "新建定时任务",
        "创建提醒",
        "新建提醒",
        "设置提醒",
        "设个提醒",
        "加个提醒",
        "建个提醒",
        "定时提醒",
        "定时任务",
        "到点提醒",
    )
    if any(marker in clean for marker in explicit_create_markers):
        return True
    has_clock = bool(re.search(r"\d{1,2}\s*[:：点]\s*\d{0,2}", clean))
    has_recurrence = any(marker in clean for marker in ("每天", "每日", "每周", "每隔"))
    return ("提醒我" in clean or "提醒" in clean) and (has_clock or has_recurrence)


class ChatTaskCoordinator:
    """Owns chat-to-task intent helpers that are not API concerns."""

    def __init__(self) -> None:
        self.scheduled_intents = ScheduledTaskIntentCoordinator()
        self.scheduled_cancellations = ScheduledTaskCancelIntentCoordinator()
        self._status_presenter = ChatTaskStatusPresenter()

    def parse_media_task_request(self, text: str) -> dict[str, Any] | None:
        clean = " ".join(text.strip().split())
        lowered = clean.lower()
        media_markers = ["视频", "音频", "剪辑", "抽帧", "转写", "字幕", "timeline", "mp4"]
        action_markers = ["分析", "剪", "裁切", "合并", "转码", "抽取", "导出", "生成"]
        if not any(marker in clean or marker in lowered for marker in media_markers):
            return None
        if not any(marker in clean for marker in action_markers):
            return None
        direct_only = any(
            marker in clean
            for marker in ["只解释", "科普", "什么是", "不要创建任务", "不要调用工具"]
        )
        if direct_only:
            return None
        plan_only = any(
            marker in clean
            for marker in ["只给方案", "不要执行", "不要假装执行", "不要渲染"]
        )
        return {
            "request_type": "edit_plan" if "剪" in clean or "剪辑" in clean else "analysis",
            "plan_only": plan_only,
            "source_boundary": "task_artifact_only",
            "requires_confirmation_for_render_export": True,
        }

    def parse_project_deploy_request(self, text: str) -> dict[str, Any] | None:
        clean = " ".join(text.strip().split())
        lowered = clean.lower()
        if _direct_only(clean):
            return None
        if not any(
            marker in clean or marker in lowered
            for marker in ["部署", "跑起来", "启动项目", "clone", "github", "git 仓库", "git仓库"]
        ):
            return None
        source_uri = _extract_source_uri(clean)
        if source_uri is None and "github" not in lowered and "仓库" not in clean:
            return None
        return {
            "source_uri": source_uri or "fixture://node-static",
            "target": {"mode": "preview", "preferred_backend": "auto"},
            "constraints": {"preferred_port": _extract_port(clean) or 5173},
        }

    def parse_host_install_request(self, text: str) -> dict[str, Any] | None:
        clean = " ".join(text.strip().split())
        if not is_host_software_install_request(clean):
            return None
        software = extract_host_software_name(clean)
        if not software:
            return None
        action = host_software_action(clean) or "install"
        return {
            "requested_software": f"{action} {software}" if action == "uninstall" else software,
            "install_scope": "host",
            "dry_run": True,
            "action": action,
        }

    def intent_creates_task(self, intent: str) -> bool:
        return intent in {
            "task_request",
            "tool_request",
            "skill_request",
            "mcp_request",
            "asset_management",
            "browser_download",
            "browser_page_action",
            "file_mutation_task",
            "repo_readonly_request",
            "repo_patch_request",
            "repo_test_request",
            "repo_fix_after_failure",
            "repo_refactor_request",
            "code_hosting_readonly_request",
            "code_hosting_sync_request",
            "code_hosting_pr_request",
            "code_hosting_review_request",
            "code_hosting_release_request",
        }

    def present_task_status(self, task: Any) -> TaskStatusPresentation:
        return self._status_presenter.present(task)


def _direct_only(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "只解释",
            "只给方案",
            "不要执行",
            "不要假装执行",
            "别假装执行",
            "不要声称执行",
            "不要创建任务",
            "不要调用工具",
        ]
    )


def _extract_source_uri(text: str) -> str | None:
    match = re.search(r"(https?://[^\s，。]+)", text)
    if match:
        return match.group(1).rstrip(".,，。")
    match = re.search(r"(fixture://[a-zA-Z0-9._/-]+)", text)
    if match:
        return match.group(1).rstrip(".,，。")
    return None


def _extract_port(text: str) -> int | None:
    match = re.search(r"(?:端口|port)\s*(\d{2,5})", text, flags=re.IGNORECASE)
    if not match:
        return None
    port = int(match.group(1))
    return port if 1 <= port <= 65535 else None


def _extract_clock_text(text: str) -> str:
    match = re.search(r"(早上|上午|中午|下午|晚上)?\s*(\d{1,2})[:：](\d{2})", text)
    if match:
        prefix = match.group(1) or ""
        hour = int(match.group(2))
        minute = int(match.group(3))
        if prefix in {"下午", "晚上"} and hour < 12:
            hour += 12
        if prefix == "中午" and hour < 11:
            hour += 12
        return f"{hour % 24:02d}:{minute:02d}"
    match = re.search(r"(早上|上午|中午|下午|晚上)?\s*(\d{1,2})\s*点", text)
    if not match:
        return "09:00"
    hour = int(match.group(2))
    prefix = match.group(1) or ""
    if prefix in {"下午", "晚上"} and hour < 12:
        hour += 12
    if prefix == "中午" and hour < 11:
        hour += 12
    return f"{hour % 24:02d}:00"


def _extract_relative_once(text: str) -> tuple[int, str] | None:
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in ("每天", "每日", "每周", "每隔")):
        return None
    match = re.search(
        r"(再过|过|等|等到)?\s*(\d+)\s*(秒|分钟|小时|天|second|seconds|minute|minutes|hour|hours|day|days)\s*(后|later)?",
        lowered,
    )
    if not match:
        return None
    if not (match.group(1) or match.group(4)):
        return None
    amount = int(match.group(2))
    unit = match.group(3)
    return (amount, unit) if amount > 0 else None


def _relative_run_at(amount: int, unit: str) -> str:
    tz = timezone(timedelta(hours=8), "Asia/Shanghai")
    seconds = amount
    if unit in {"分钟", "minute", "minutes"}:
        seconds = amount * 60
    elif unit in {"小时", "hour", "hours"}:
        seconds = amount * 3600
    elif unit in {"天", "day", "days"}:
        seconds = amount * 86400
    return (datetime.now(tz) + timedelta(seconds=seconds)).isoformat()


def _tomorrow_run_at(clock_text: str) -> str:
    tz = timezone(timedelta(hours=8), "Asia/Shanghai")
    now = datetime.now(tz)
    hour, minute = [int(part) for part in clock_text.split(":", 1)]
    run_at = datetime.combine(
        (now + timedelta(days=1)).date(),
        datetime.min.time().replace(hour=hour, minute=minute),
        tzinfo=tz,
    )
    return run_at.isoformat()


def _extract_weekday(text: str) -> str:
    for value in ["周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天"]:
        if value in text:
            return value
    mapping = {
        "monday": "monday",
        "tuesday": "tuesday",
        "wednesday": "wednesday",
        "thursday": "thursday",
        "friday": "friday",
        "saturday": "saturday",
        "sunday": "sunday",
    }
    lowered = text.lower()
    for key, value in mapping.items():
        if key in lowered:
            return value
    return "周一"


def _scheduled_title(goal: str) -> str:
    title = goal.strip(" ，。,.")[:40]
    return title or "聊天创建的定时任务"


def _extract_scheduled_task_id(text: str) -> str | None:
    match = re.search(r"\bscht_[a-zA-Z0-9]{8,}\b", text)
    return match.group(0) if match else None


def _looks_like_action_inside_reminder(text: str) -> bool:
    reminder_action = re.search(
        r"提醒(?:我|你)?[^，。！？,.!?]{0,40}(?:取消|撤销|删掉|删除|停掉|关掉|停止)",
        text,
    )
    if not reminder_action:
        return False
    explicit_cancel_target = re.search(
        r"(?:取消|撤销|删掉|删除|停掉|关掉|停止)[^，。！？,.!?]{0,24}(?:提醒|定时任务|闹钟|reminder|scheduled task)",
        text,
        flags=re.IGNORECASE,
    )
    return explicit_cancel_target is None


def _extract_cancel_target_text(text: str) -> str:
    target = str(text or "").strip()
    target = re.sub(r"\bscht_[a-zA-Z0-9]{8,}\b", " ", target)
    target = re.sub(
        r"(帮我|麻烦你|请你|请|把|给我|这个|那个|刚才那个|刚刚那个|上一个|最新那个|上条)",
        " ",
        target,
    )
    target = re.sub(
        r"(取消|撤销|删掉|删除|停掉|关掉|停止|不用|别|不要|cancel|stop reminding)",
        " ",
        target,
        flags=re.IGNORECASE,
    )
    target = re.sub(r"(提醒我|提醒你|提醒|定时任务|定时|闹钟|任务|reminder|scheduled task)", " ", target, flags=re.IGNORECASE)
    target = re.sub(r"[，。！？,.!?：:\s]+", " ", target).strip()
    return target


class ChatTurnOrchestrator:
    """Documents the stable chat turn stage order after Phase 45 decomposition."""

    stages = (
        "access",
        "privacy",
        "brain_decision",
        "context",
        "quality_policy",
        "natural_action",
        "memory",
        "scheduled_task",
        "task_or_capability",
        "model",
        "response_compose",
    )

    def stage_names(self) -> tuple[str, ...]:
        return self.stages
