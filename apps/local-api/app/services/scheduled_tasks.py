from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core_types import (
    ErrorCode,
    RiskLevel,
    ScheduledTask,
    ScheduledTaskEvent,
    ScheduledTaskRun,
    TaskMode,
    TraceSpanStatus,
    TraceSpanType,
    TraceStatus,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.scheduled_task_repo import ScheduledTaskRepository
from app.schemas.scheduled_tasks import ScheduledTaskCreateRequest, ScheduledTaskUpdateRequest
from app.schemas.tasks import TaskCreateRequest
from app.services.audit import AuditEventService
from app.services.tasks import TaskEngine

DEFAULT_TIMEZONE = "Asia/Shanghai"
ACTIVE_STATUS = "active"
TERMINAL_STATUSES = {"cancelled", "archived", "dead_letter", "completed"}
type ScheduledTaskRunList = list[ScheduledTaskRun]
type ScheduledTaskEventList = list[ScheduledTaskEvent]


@dataclass(frozen=True)
class ScheduleComputation:
    schedule: dict[str, Any]
    next_run_at: datetime | None


class ScheduleParser:
    def normalize(
        self,
        schedule: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> ScheduleComputation:
        current = _ensure_utc(now or utc_now())
        schedule_type = str(schedule.get("type") or schedule.get("kind") or "").strip().lower()
        if schedule_type not in {"once", "interval", "daily", "weekly", "monthly-lite"}:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "不支持的定时任务 schedule.type",
                status_code=422,
                details={"supported": ["once", "interval", "daily", "weekly", "monthly-lite"]},
            )
        timezone_name = str(schedule.get("timezone") or DEFAULT_TIMEZONE)
        timezone = _timezone(timezone_name)
        normalized = {**schedule, "type": schedule_type, "timezone": timezone_name}
        next_run_at = self.next_run_at(normalized, after=current, timezone=timezone)
        return ScheduleComputation(schedule=normalized, next_run_at=next_run_at)

    def next_run_at(
        self,
        schedule: dict[str, Any],
        *,
        after: datetime,
        timezone: tzinfo | None = None,
    ) -> datetime | None:
        schedule_type = str(schedule.get("type") or "").strip().lower()
        tz = timezone or _timezone(str(schedule.get("timezone") or DEFAULT_TIMEZONE))
        after_utc = _ensure_utc(after)
        local_after = after_utc.astimezone(tz)
        if schedule_type == "once":
            run_at = _parse_datetime(schedule.get("run_at") or schedule.get("at"), tz)
            return run_at if run_at > after_utc else None
        if schedule_type == "interval":
            seconds = int(schedule.get("every_seconds") or schedule.get("seconds") or 0)
            if seconds <= 0:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "interval schedule 需要 every_seconds > 0",
                    status_code=422,
                )
            start_at = (
                _parse_datetime(schedule["start_at"], tz)
                if schedule.get("start_at")
                else after_utc + timedelta(seconds=seconds)
            )
            if start_at > after_utc:
                return start_at
            elapsed = (after_utc - start_at).total_seconds()
            steps = int(elapsed // seconds) + 1
            return start_at + timedelta(seconds=steps * seconds)
        if schedule_type == "daily":
            desired = _parse_time(str(schedule.get("time") or "09:00"))
            candidate = datetime.combine(local_after.date(), desired, tzinfo=tz)
            if candidate <= local_after:
                candidate += timedelta(days=1)
            return candidate.astimezone(UTC)
        if schedule_type == "weekly":
            desired = _parse_time(str(schedule.get("time") or "09:00"))
            days = _weekly_days(schedule)
            for offset in range(8):
                candidate_date = local_after.date() + timedelta(days=offset)
                if candidate_date.weekday() not in days:
                    continue
                candidate = datetime.combine(candidate_date, desired, tzinfo=tz)
                if candidate > local_after:
                    return candidate.astimezone(UTC)
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "weekly schedule 无可用星期",
                status_code=422,
            )
        if schedule_type == "monthly-lite":
            desired = _parse_time(str(schedule.get("time") or "09:00"))
            day_of_month = max(1, min(int(schedule.get("day") or 1), 28))
            year = local_after.year
            month = local_after.month
            for _ in range(14):
                candidate = datetime(
                    year,
                    month,
                    day_of_month,
                    desired.hour,
                    desired.minute,
                    tzinfo=tz,
                )
                if candidate > local_after:
                    return candidate.astimezone(UTC)
                month += 1
                if month > 12:
                    month = 1
                    year += 1
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "monthly-lite schedule 无法计算",
                status_code=422,
            )
        return None


class ScheduledTaskService:
    def __init__(
        self,
        *,
        repo: ScheduledTaskRepository,
        member_repo: MemberRepository,
        task_engine: TaskEngine,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._members = member_repo
        self._task_engine = task_engine
        self._trace = trace_service
        self._audit = audit_service
        self._parser = ScheduleParser()
        self._notification_callback: Callable[..., Awaitable[Any]] | None = None

    def set_notification_callback(self, callback: Callable[..., Awaitable[Any]]) -> None:
        self._notification_callback = callback

    async def create(
        self,
        request: ScheduledTaskCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> ScheduledTask:
        member = await self._members.get_member(request.owner_member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        computation = self._parser.normalize(request.schedule)
        now = utc_now_iso()
        scheduled_task_id = new_id("scht")
        policy = _default_policy(request.execution_policy)
        data = {
            "scheduled_task_id": scheduled_task_id,
            "organization_id": "org_default",
            "conversation_id": request.conversation_id,
            "owner_member_id": request.owner_member_id,
            "title": request.title or _title_from_goal(request.goal),
            "goal": request.goal,
            "status": ACTIVE_STATUS,
            "schedule": computation.schedule,
            "execution_policy": policy,
            "constraints": request.constraints,
            "next_run_at": _iso(computation.next_run_at),
            "created_by_member_id": request.created_by_member_id,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_task(data)
        await self._event(
            scheduled_task_id,
            "scheduled_task.created",
            {"next_run_at": data["next_run_at"], "schedule_type": computation.schedule["type"]},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="scheduled_task.created",
            object_type="scheduled_task",
            object_id=scheduled_task_id,
            summary="定时任务已创建",
            risk_level=RiskLevel.R2,
            payload={"scheduled_task_id": scheduled_task_id, "goal": redact(request.goal)},
            trace_id=trace_id,
        )
        return await self.detail(scheduled_task_id)

    async def list(
        self,
        *,
        status: str | None = None,
        owner_member_id: str | None = None,
        limit: int = 100,
    ) -> list[ScheduledTask]:
        return [
            ScheduledTask(**row)
            for row in await self._repo.list_tasks(
                status=status,
                owner_member_id=owner_member_id,
                limit=limit,
            )
        ]

    async def detail(self, scheduled_task_id: str) -> ScheduledTask:
        row = await self._repo.get_task(scheduled_task_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "定时任务不存在", status_code=404)
        return ScheduledTask(**row)

    async def update(
        self,
        scheduled_task_id: str,
        request: ScheduledTaskUpdateRequest,
        *,
        trace_id: str | None = None,
    ) -> ScheduledTask:
        task = await self.detail(scheduled_task_id)
        fields: dict[str, Any] = {"updated_at": utc_now_iso()}
        if request.title is not None:
            fields["title"] = request.title
        if request.goal is not None:
            fields["goal"] = request.goal
        if request.execution_policy is not None:
            fields["execution_policy"] = _default_policy(request.execution_policy)
        if request.constraints is not None:
            fields["constraints"] = request.constraints
        if request.max_consecutive_failures is not None:
            fields["max_consecutive_failures"] = request.max_consecutive_failures
        if request.schedule is not None:
            computation = self._parser.normalize(request.schedule)
            fields["schedule"] = computation.schedule
            fields["next_run_at"] = _iso(computation.next_run_at)
        elif request.next_run_at is not None:
            fields["next_run_at"] = _iso(_ensure_utc(request.next_run_at))
        await self._repo.update_task(scheduled_task_id, fields)
        await self._event(
            scheduled_task_id,
            "scheduled_task.updated",
            {"fields": sorted(key for key in fields if key != "updated_at")},
            trace_id=trace_id,
        )
        return await self.detail(task.scheduled_task_id)

    async def pause(
        self,
        scheduled_task_id: str,
        *,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> ScheduledTask:
        return await self._set_status(
            scheduled_task_id,
            "paused",
            event_type="scheduled_task.paused",
            reason=reason,
            trace_id=trace_id,
        )

    async def resume(self, scheduled_task_id: str, *, trace_id: str | None = None) -> ScheduledTask:
        task = await self.detail(scheduled_task_id)
        if task.status in TERMINAL_STATUSES:
            raise AppError(ErrorCode.TASK_STATE_INVALID, "终态定时任务不能恢复", status_code=409)
        computation = self._parser.normalize(task.schedule)
        await self._repo.update_task(
            scheduled_task_id,
            {
                "status": ACTIVE_STATUS,
                "next_run_at": _iso(computation.next_run_at),
                "updated_at": utc_now_iso(),
            },
        )
        await self._event(
            scheduled_task_id,
            "scheduled_task.resumed",
            {"next_run_at": _iso(computation.next_run_at)},
            trace_id=trace_id,
        )
        return await self.detail(scheduled_task_id)

    async def cancel(
        self,
        scheduled_task_id: str,
        *,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> ScheduledTask:
        return await self._set_status(
            scheduled_task_id,
            "cancelled",
            event_type="scheduled_task.cancelled",
            reason=reason,
            trace_id=trace_id,
            extra={"cancelled_at": utc_now_iso(), "next_run_at": None},
        )

    async def archive(
        self,
        scheduled_task_id: str,
        *,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> ScheduledTask:
        return await self._set_status(
            scheduled_task_id,
            "archived",
            event_type="scheduled_task.archived",
            reason=reason,
            trace_id=trace_id,
            extra={"archived_at": utc_now_iso(), "next_run_at": None},
        )

    async def trigger(
        self,
        scheduled_task_id: str,
        *,
        trigger_type: str = "manual",
        scheduled_for: datetime | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> ScheduledTaskRun:
        task = await self.detail(scheduled_task_id)
        if task.status in {"cancelled", "archived", "dead_letter"}:
            raise AppError(ErrorCode.TASK_STATE_INVALID, "当前定时任务不能触发", status_code=409)
        run_for = _ensure_utc(scheduled_for or task.next_run_at or utc_now())
        idempotency_key = f"scheduled:{scheduled_task_id}:{trigger_type}:{run_for.isoformat()}"
        existing = await self._repo.get_run_by_idempotency_key(idempotency_key)
        if existing is not None:
            return ScheduledTaskRun(**existing)
        own_trace = trace_id is None
        if own_trace:
            trace_id = await self._trace.start_trace()
        if trace_id is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "定时任务 trace 创建失败", status_code=500)
        span_id = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.TASK_CREATE,
            name="trigger scheduled task",
            input_data={"scheduled_task_id": scheduled_task_id, "trigger_type": trigger_type},
        )
        run_id = new_id("schrun")
        now = utc_now_iso()
        policy = _policy_decision(task, trigger_type=trigger_type)
        await self._repo.insert_run(
            {
                "run_id": run_id,
                "scheduled_task_id": scheduled_task_id,
                "organization_id": task.organization_id,
                "trace_id": trace_id,
                "trigger_type": trigger_type,
                "idempotency_key": idempotency_key,
                "scheduled_for": _iso(run_for),
                "started_at": now,
                "status": "created",
                "policy_decision": policy,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self._event(
            scheduled_task_id,
            "scheduled_task.run_created",
            {
                "run_id": run_id,
                "trigger_type": trigger_type,
                "policy_decision": policy,
                "reason": reason,
            },
            run_id=run_id,
            trace_id=trace_id,
        )
        try:
            task_request = TaskCreateRequest(
                conversation_id=task.conversation_id,
                owner_member_id=task.owner_member_id,
                goal=task.goal,
                mode_hint=TaskMode.WORKFLOW,
                constraints={
                    **task.constraints,
                    "scheduled_task_id": scheduled_task_id,
                    "scheduled_run_id": run_id,
                    "background_execution": True,
                },
                planner_context={
                    "scheduled_task": {
                        "scheduled_task_id": scheduled_task_id,
                        "run_id": run_id,
                        "trigger_type": trigger_type,
                        "scheduled_for": _iso(run_for),
                    },
                    "background_execution_policy": policy,
                    "session_approval_reuse": False,
                },
                auto_start=policy["auto_start"],
                client_request_id=idempotency_key,
            )
            created_task = await self._task_engine.create_task(task_request, trace_id=trace_id)
            task_status = created_task.status.value
            if policy["action"] != "execute":
                run_status = "waiting_policy"
            elif task_status == "failed":
                run_status = "failed"
            else:
                run_status = "completed"
            result = {
                "task_id": created_task.task_id,
                "task_status": task_status,
                "policy_action": policy["action"],
                "auto_start": policy["auto_start"],
            }
            if run_status == "failed":
                await self._record_failure(
                    task,
                    run_id=run_id,
                    trigger_type=trigger_type,
                    failure_reason=f"linked_task_failed:{created_task.task_id}",
                    trace_id=trace_id,
                )
                await self._repo.update_run(
                    run_id,
                    {
                        "task_id": created_task.task_id,
                        "result": result,
                        "updated_at": utc_now_iso(),
                    },
                )
            else:
                await self._repo.update_run(
                    run_id,
                    {
                        "task_id": created_task.task_id,
                        "status": run_status,
                        "result": result,
                        "completed_at": utc_now_iso(),
                        "updated_at": utc_now_iso(),
                    },
                )
                await self._after_successful_trigger(
                    task,
                    trigger_type=trigger_type,
                    run_for=run_for,
                )
            await self._event(
                scheduled_task_id,
                "scheduled_task.run_linked_task",
                result,
                run_id=run_id,
                trace_id=trace_id,
            )
            await self._notify_run_update(
                scheduled_task_id=scheduled_task_id,
                run_id=run_id,
                task_id=created_task.task_id,
                status=run_status,
                summary=_scheduled_run_visible_summary(task.goal, run_status),
                trace_id=trace_id,
            )
            await self._trace.end_span(span_id, output_data=result)
        except Exception as exc:
            await self._record_failure(
                task,
                run_id=run_id,
                trigger_type=trigger_type,
                failure_reason=str(exc),
                trace_id=trace_id,
            )
            await self._trace.end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                error_code=getattr(exc, "code", ErrorCode.INTERNAL_ERROR.value),
                output_data={"run_id": run_id, "failure": redact(str(exc))},
            )
            if own_trace:
                await self._trace.end_trace(trace_id, status=TraceStatus.FAILED)
            await self._notify_run_update(
                scheduled_task_id=scheduled_task_id,
                run_id=run_id,
                task_id=None,
                status="failed",
                summary=str(redact(str(exc))),
                trace_id=trace_id,
            )
            raise
        if own_trace:
            await self._trace.end_trace(trace_id)
        run = await self._repo.get_run(run_id)
        if run is None:
            raise AppError(ErrorCode.NOT_FOUND, "定时任务 run 不存在", status_code=404)
        return ScheduledTaskRun(**run)

    async def _notify_run_update(
        self,
        *,
        scheduled_task_id: str,
        run_id: str,
        task_id: str | None,
        status: str,
        summary: str,
        trace_id: str | None,
    ) -> None:
        if self._notification_callback is None:
            return
        try:
            await self._notification_callback(
                scheduled_task_id=scheduled_task_id,
                scheduled_run_id=run_id,
                task_id=task_id,
                status=status,
                summary=summary,
                trace_id=trace_id,
            )
        except Exception:
            await self._event(
                scheduled_task_id,
                "scheduled_task.notification_failed",
                {"run_id": run_id, "status": status},
                run_id=run_id,
                trace_id=trace_id,
            )

    async def scan_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 50,
        trace_id: str | None = None,
    ) -> ScheduledTaskRunList:
        current = _ensure_utc(now or utc_now())
        runs: list[ScheduledTaskRun] = []
        for task in await self._repo.due_tasks(now=current.isoformat(), limit=limit):
            runs.append(
                await self.trigger(
                    task["scheduled_task_id"],
                    trigger_type="due",
                    scheduled_for=_parse_datetime(task["next_run_at"], UTC),
                    trace_id=trace_id,
                )
            )
        return runs

    async def list_runs(
        self,
        scheduled_task_id: str,
        *,
        limit: int = 100,
    ) -> ScheduledTaskRunList:
        await self.detail(scheduled_task_id)
        return [
            ScheduledTaskRun(**row)
            for row in await self._repo.list_runs(scheduled_task_id, limit=limit)
        ]

    async def get_run(self, run_id: str) -> ScheduledTaskRun:
        row = await self._repo.get_run(run_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "定时任务 run 不存在", status_code=404)
        return ScheduledTaskRun(**row)

    async def list_events(self, scheduled_task_id: str) -> ScheduledTaskEventList:
        await self.detail(scheduled_task_id)
        return [
            ScheduledTaskEvent(**row)
            for row in await self._repo.list_events(scheduled_task_id)
        ]

    async def recover_stale_runs(
        self,
        *,
        stale_after_minutes: int = 30,
        trace_id: str | None = None,
    ) -> int:
        recovered = await self._repo.recover_stale_runs(
            stale_before=(utc_now() - timedelta(minutes=stale_after_minutes)).isoformat(),
            updated_at=utc_now_iso(),
        )
        if recovered:
            await self._audit.write_event(
                actor_type="system",
                action="scheduled_task_runs.recovered_stale",
                object_type="scheduled_task_run",
                object_id="batch",
                summary="stale scheduled run 已恢复为失败状态",
                risk_level=RiskLevel.R1,
                payload={"recovered_count": recovered},
                trace_id=trace_id,
            )
        return recovered

    async def _set_status(
        self,
        scheduled_task_id: str,
        status: str,
        *,
        event_type: str,
        reason: str | None,
        trace_id: str | None,
        extra: dict[str, Any] | None = None,
    ) -> ScheduledTask:
        await self.detail(scheduled_task_id)
        await self._repo.update_task(
            scheduled_task_id,
            {"status": status, "updated_at": utc_now_iso(), **(extra or {})},
        )
        await self._event(scheduled_task_id, event_type, {"reason": reason}, trace_id=trace_id)
        return await self.detail(scheduled_task_id)

    async def _after_successful_trigger(
        self,
        task: ScheduledTask,
        *,
        trigger_type: str,
        run_for: datetime,
    ) -> None:
        fields: dict[str, Any] = {
            "last_run_at": _iso(run_for),
            "consecutive_failure_count": 0,
            "updated_at": utc_now_iso(),
        }
        if trigger_type == "due":
            next_run = self._parser.next_run_at(task.schedule, after=run_for)
            fields["next_run_at"] = _iso(next_run)
            if next_run is None and task.schedule.get("type") == "once":
                fields["status"] = "completed"
        await self._repo.update_task(task.scheduled_task_id, fields)

    async def _record_failure(
        self,
        task: ScheduledTask,
        *,
        run_id: str,
        trigger_type: str,
        failure_reason: str,
        trace_id: str | None,
    ) -> None:
        count = task.consecutive_failure_count + 1
        is_dead = count >= task.max_consecutive_failures
        now = utc_now()
        fields: dict[str, Any] = {
            "consecutive_failure_count": count,
            "dead_letter_reason": redact(failure_reason),
            "updated_at": now.isoformat(),
        }
        if is_dead:
            fields["status"] = "dead_letter"
            fields["next_run_at"] = None
        elif trigger_type == "due":
            fields["next_run_at"] = (now + timedelta(minutes=2**min(count, 6))).isoformat()
        await self._repo.update_task(task.scheduled_task_id, fields)
        await self._repo.update_run(
            run_id,
            {
                "status": "failed",
                "failure_reason": redact(failure_reason),
                "completed_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        await self._event(
            task.scheduled_task_id,
            "scheduled_task.run_failed",
            {"run_id": run_id, "consecutive_failure_count": count, "dead_letter": is_dead},
            run_id=run_id,
            trace_id=trace_id,
        )

    async def _event(
        self,
        scheduled_task_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        run_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        await self._repo.insert_event(
            {
                "event_id": new_id("schtev"),
                "scheduled_task_id": scheduled_task_id,
                "organization_id": "org_default",
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )


def _default_policy(policy: dict[str, Any]) -> dict[str, Any]:
    attendance = str(policy.get("attendance") or "unattended")
    return {
        "attendance": attendance,
        "high_risk_action": str(policy.get("high_risk_action") or "pause_wait_approval"),
        "max_runtime_seconds": int(policy.get("max_runtime_seconds") or 1800),
        "session_approval_reuse": False,
        "background_execution": True,
    }


def _policy_decision(task: ScheduledTask, *, trigger_type: str) -> dict[str, Any]:
    risk_level = _risk_level(task.goal, task.constraints)
    unattended = task.execution_policy.get("attendance") == "unattended"
    high_risk = _risk_order(risk_level) >= 3
    if unattended and high_risk:
        return {
            "risk_level": risk_level,
            "attendance": "unattended",
            "action": task.execution_policy.get("high_risk_action") or "pause_wait_approval",
            "auto_start": False,
            "session_approval_reuse": False,
            "reason_codes": ["unattended_high_risk_requires_fresh_approval"],
            "trigger_type": trigger_type,
        }
    return {
        "risk_level": risk_level,
        "attendance": task.execution_policy.get("attendance", "unattended"),
        "action": "execute",
        "auto_start": True,
        "session_approval_reuse": False,
        "reason_codes": ["background_policy_allows_low_risk"],
        "trigger_type": trigger_type,
    }


def _risk_level(goal: str, constraints: dict[str, Any]) -> str:
    text = f"{goal} {constraints}".lower()
    if _looks_like_money_action(text):
        return "R4"
    if _looks_like_automatic_action(text):
        return "R4"
    if _looks_like_reminder_only(text) or _looks_like_manual_prep_reminder(text):
        return "R2"
    if any(
        word in text
        for word in [
            "delete",
            "删除",
            "清空",
            "卸载",
            "重启",
            "terminal",
            "终端",
            "命令",
            "private_key",
            "root",
        ]
    ):
        return "R5"
    if any(
        word in text
        for word in [
            "login",
            "登录",
            "upload",
            "上传",
            "发送",
            "外部邮箱",
            "发资料",
            "汇款",
            "打款",
            "钱包",
            "发布",
            "外发",
            "改密码",
            "密码",
            "导出",
            "投放",
            "批量归档",
            "客户资料",
            "证件",
            "payment",
            "transfer",
        ]
    ):
        return "R4"
    if any(word in text for word in ["download", "下载", "browser", "浏览器", "提交"]):
        return "R3"
    return "R2"


def _scheduled_run_visible_summary(goal: str, run_status: str) -> str:
    visible_goal = _visible_scheduled_goal(goal)
    if run_status == "waiting_policy":
        return f"提醒你{visible_goal}。这类操作不会自动执行，需要你确认后再继续。"
    if run_status == "failed":
        return f"提醒你{visible_goal}，但这次后续处理没有完成。"
    return f"提醒你{visible_goal}。"


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
    if re.search(r"提醒(?:我|你)?", text):
        text = re.split(r"提醒(?:我|你)?[，,：:\s]*", text)[-1]
    text = re.sub(r"^提醒(?:我|你)?[，,：:\s]*", "", text)
    text = re.sub(r"^帮我[，,：:\s]*", "", text)
    text = text.strip("。！？!?.,，；;：: “”（）()[]【】'\" ")
    if text.startswith("我"):
        text = text[1:].strip()
    return text or "这件事"


def _looks_like_money_action(text: str) -> bool:
    if any(
        marker in text
        for marker in (
            "payment",
            "transfer",
            "支付",
            "付款",
            "转账",
            "汇款",
            "打款",
            "银行卡",
            "信用卡",
            "还款",
            "扣款",
            "充值",
        )
    ):
        return True
    return "钱包" in text and not any(marker in text for marker in ("助记词", "私钥", "备份", "环境安全"))


def _looks_like_automatic_action(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "自动上传",
            "自动发送",
            "自动发",
            "自动外发",
            "自动发布",
            "自动提交",
            "自动导出",
            "自动删除",
            "自动清空",
            "自动运行",
            "自动执行",
        )
    )


def _looks_like_reminder_only(text: str) -> bool:
    return any(marker in text for marker in ("提醒我", "提醒你", "提醒一下", "remind me"))


def _looks_like_manual_prep_reminder(text: str) -> bool:
    if "前" not in text:
        return False
    if any(
        marker in text
        for marker in (
            "自动上传",
            "自动发送",
            "自动发",
            "自动外发",
            "帮我发送",
            "帮我发",
            "直接发送",
        )
    ):
        return False
    return any(marker in text for marker in ("打码", "脱敏", "遮住", "确认", "核对", "检查", "通知", "让", "看审批", "复核", "带"))


def _risk_order(value: str) -> int:
    return {"R1": 1, "R2": 2, "R3": 3, "R4": 4, "R5": 5}.get(value, 1)


def _title_from_goal(goal: str) -> str:
    text = " ".join(goal.strip().split())
    return text[:40] or "定时任务"


def _iso(value: datetime | None) -> str | None:
    return _ensure_utc(value).isoformat() if value is not None else None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _timezone(name: str) -> tzinfo:
    if name in {"Asia/Shanghai", "Asia/Chongqing", "Asia/Harbin", "Asia/Urumqi"}:
        return timezone(timedelta(hours=8), name)
    if name.upper() in {"UTC", "Z"}:
        return UTC
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "timezone 不可用",
            status_code=422,
            details={"timezone": name},
        ) from exc


def _parse_datetime(value: Any, timezone: tzinfo) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise AppError(ErrorCode.VALIDATION_ERROR, "时间字段格式不合法", status_code=422)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone)
    return dt.astimezone(UTC)


def _parse_time(value: str) -> time:
    try:
        parts = value.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return time(hour=hour, minute=minute)
    except (ValueError, IndexError) as exc:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "time 需要 HH:MM 格式",
            status_code=422,
        ) from exc


def _weekly_days(schedule: dict[str, Any]) -> set[int]:
    raw = schedule.get("days") or schedule.get("day_of_week") or schedule.get("weekday")
    if raw is None:
        return {0}
    items = raw if isinstance(raw, list) else [raw]
    mapping = {
        "mon": 0,
        "monday": 0,
        "周一": 0,
        "tue": 1,
        "tuesday": 1,
        "周二": 1,
        "wed": 2,
        "wednesday": 2,
        "周三": 2,
        "thu": 3,
        "thursday": 3,
        "周四": 3,
        "fri": 4,
        "friday": 4,
        "周五": 4,
        "sat": 5,
        "saturday": 5,
        "周六": 5,
        "sun": 6,
        "sunday": 6,
        "周日": 6,
        "周天": 6,
    }
    result: set[int] = set()
    for item in items:
        if isinstance(item, int):
            result.add(item % 7)
        else:
            key = str(item).strip().lower()
            if key not in mapping:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "weekly days 包含未知值",
                    status_code=422,
                )
            result.add(mapping[key])
    return result
