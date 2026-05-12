from __future__ import annotations

import re
from dataclasses import dataclass
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


class ScheduledTaskIntentCoordinator:
    """Parses conservative scheduled-task requests from ordinary chat text."""

    def parse(self, text: str) -> ScheduledTaskIntent | None:
        direct_only_markers = [
            "不要执行",
            "不要假装执行",
            "别假装执行",
            "不要声称执行",
            "不要创建任务",
            "不要调用工具",
            "只给方案",
        ]
        if any(marker in text for marker in direct_only_markers):
            return None
        clean = " ".join(text.strip().split())
        lowered = clean.lower()
        schedule: dict[str, Any] | None = None
        if any(marker in clean for marker in ["每天", "每日"]):
            schedule = {
                "type": "daily",
                "time": _extract_clock_text(clean),
                "timezone": "Asia/Shanghai",
            }
        elif "每周" in clean:
            schedule = {
                "type": "weekly",
                "days": [_extract_weekday(clean)],
                "time": _extract_clock_text(clean),
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
                schedule = {"type": "interval", "every_seconds": amount * multiplier}
        if schedule is None:
            return None
        scheduled_markers = ["帮我", "提醒", "定时", "创建定时任务", "新建定时任务"]
        if not any(marker in clean for marker in scheduled_markers):
            return None
        goal = clean
        for marker in ["每天", "每日", "每周", "每隔"]:
            goal = goal.replace(marker, "", 1).strip()
        return ScheduledTaskIntent(
            title=_scheduled_title(goal),
            goal=goal or clean,
            schedule=schedule,
        )


class ChatTaskCoordinator:
    """Owns chat-to-task intent helpers that are not API concerns."""

    def __init__(self) -> None:
        self.scheduled_intents = ScheduledTaskIntentCoordinator()
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
    match = re.search(r"(\d{1,2})[:：](\d{2})", text)
    if match:
        return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"
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
