from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core_types import (
    ErrorCode,
    Goal,
    GoalCheckin,
    GoalEvent,
    GoalPlan,
    GoalPlanItem,
    GoalProgressSnapshot,
    GoalSupervisionPolicy,
    RiskLevel,
    ScheduledTask,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.goal_repo import GoalRepository
from app.db.repositories.member_repo import MemberRepository
from app.schemas.goals import (
    GoalCheckinCreateRequest,
    GoalCheckinReplyRequest,
    GoalCreateRequest,
    GoalSupervisionRequest,
)
from app.schemas.scheduled_tasks import ScheduledTaskCreateRequest
from app.services.audit import AuditEventService

GOAL_ACTIVE_STATUSES = {"awaiting_confirmation", "active", "paused"}
GOAL_TERMINAL_STATUSES = {"cancelled", "archived", "completed"}


@dataclass(frozen=True)
class GoalBundle:
    goal: Goal
    active_plan: GoalPlan | None
    plan_items: list[GoalPlanItem]
    supervision_policy: GoalSupervisionPolicy | None = None
    progress: GoalProgressSnapshot | None = None


@dataclass(frozen=True)
class GoalChatOutcome:
    intent: str
    text: str
    payload: dict[str, Any]


class GoalService:
    def __init__(
        self,
        *,
        repo: GoalRepository,
        member_repo: MemberRepository,
        scheduled_task_service: Any | None,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._members = member_repo
        self._scheduled_tasks = scheduled_task_service
        self._trace = trace_service
        self._audit = audit_service

    async def create_goal(
        self,
        request: GoalCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> GoalBundle:
        member = await self._members.get_member(request.owner_member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "member not found", status_code=404)
        title = request.title or _title_from_text(request.description)
        plan = _build_plan(title=title, description=request.description)
        goal_id = new_id("goal")
        plan_id = new_id("gplan")
        now = utc_now_iso()
        async with self._repo.transaction():
            await self._repo.insert_goal(
                {
                    "goal_id": goal_id,
                    "organization_id": "org_default",
                    "owner_member_id": request.owner_member_id,
                    "conversation_id": request.conversation_id,
                    "title": title,
                    "description": request.description,
                    "domain_label": request.domain_label or _domain_label(request.description),
                    "status": "awaiting_confirmation",
                    "success_criteria": request.success_criteria or plan["success_criteria"],
                    "constraints": request.constraints,
                    "motivation": request.motivation,
                    "active_plan_id": plan_id,
                    "created_from_turn_id": request.created_from_turn_id,
                    "trace_id": trace_id,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            await self._repo.insert_plan(
                {
                    "goal_plan_id": plan_id,
                    "goal_id": goal_id,
                    "version": 1,
                    "status": "proposed",
                    "summary": plan["summary"],
                    "assumptions": plan["assumptions"],
                    "risk_notes": plan["risk_notes"],
                    "created_at": now,
                    "updated_at": now,
                }
            )
            for index, item in enumerate(plan["items"], start=1):
                await self._repo.insert_plan_item(
                    {
                        "goal_plan_item_id": new_id("gitem"),
                        "goal_plan_id": plan_id,
                        "goal_id": goal_id,
                        "title": item["title"],
                        "description": item["description"],
                        "item_type": item["item_type"],
                        "cadence": item["cadence"],
                        "success_metric": item["success_metric"],
                        "status": "planned",
                        "sort_order": index,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            await self._event(
                goal_id,
                "goal.created",
                {"title": title, "plan_id": plan_id},
                trace_id=trace_id,
            )
        await self._audit.write_event(
            actor_type="member",
            actor_id=request.owner_member_id,
            action="goal.created",
            object_type="goal",
            object_id=goal_id,
            summary="Goal created with proposed plan",
            risk_level=RiskLevel.R1,
            payload={"goal_id": goal_id, "title": redact(title)},
            trace_id=trace_id,
        )
        return await self.detail(goal_id)

    async def detail(self, goal_id: str) -> GoalBundle:
        goal = await self._goal(goal_id)
        active_plan = await self._plan(goal.active_plan_id) if goal.active_plan_id else None
        items = (
            [
                GoalPlanItem(**row)
                for row in await self._repo.list_plan_items(active_plan.goal_plan_id)
            ]
            if active_plan is not None
            else []
        )
        policy_row = await self._repo.latest_policy_for_goal(goal_id)
        progress_row = await self._repo.latest_snapshot(goal_id)
        return GoalBundle(
            goal=goal,
            active_plan=active_plan,
            plan_items=items,
            supervision_policy=GoalSupervisionPolicy(**policy_row) if policy_row else None,
            progress=GoalProgressSnapshot(**progress_row) if progress_row else None,
        )

    async def list_goals(
        self,
        *,
        owner_member_id: str | None = None,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Goal]:
        return [
            Goal(**row)
            for row in await self._repo.list_goals(
                owner_member_id=owner_member_id,
                conversation_id=conversation_id,
                status=status,
                limit=limit,
            )
        ]

    async def confirm_plan(
        self,
        goal_id: str,
        goal_plan_id: str,
        *,
        trace_id: str | None = None,
    ) -> GoalBundle:
        goal = await self._goal(goal_id)
        plan = await self._plan(goal_plan_id)
        if plan.goal_id != goal.goal_id:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "plan does not belong to goal",
                status_code=422,
            )
        if goal.status in {"cancelled", "archived", "completed"}:
            raise AppError(ErrorCode.TASK_STATE_INVALID, "goal is terminal", status_code=409)
        now = utc_now_iso()
        async with self._repo.transaction():
            await self._repo.update_plan(goal_plan_id, {"status": "confirmed", "updated_at": now})
            await self._repo.update_goal(
                goal_id,
                {
                    "status": "active",
                    "active_plan_id": goal_plan_id,
                    "updated_at": now,
                },
            )
            await self._event(
                goal_id,
                "goal.plan_confirmed",
                {"plan_id": goal_plan_id},
                trace_id=trace_id,
            )
        return await self.detail(goal_id)

    async def create_supervision(
        self,
        goal_id: str,
        request: GoalSupervisionRequest,
        *,
        trace_id: str | None = None,
    ) -> GoalSupervisionPolicy:
        if self._scheduled_tasks is None:
            raise AppError(
                ErrorCode.CONFIG_ERROR,
                "scheduled task service unavailable",
                status_code=500,
            )
        goal = await self._goal(goal_id)
        if goal.status == "awaiting_confirmation" and goal.active_plan_id:
            await self.confirm_plan(goal_id, goal.active_plan_id, trace_id=trace_id)
            goal = await self._goal(goal_id)
        if goal.status != "active":
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "goal must be active before supervision",
                status_code=409,
            )
        policy_id = new_id("gpol")
        jitter_minutes = max(0, int(request.random_jitter_minutes or 0))
        supervision_constraints = {
            "purpose": "goal_checkin",
            "goal_id": goal.goal_id,
            "policy_id": policy_id,
            "source": "goal_support",
        }
        if jitter_minutes:
            supervision_constraints["random_jitter_minutes"] = jitter_minutes
        scheduled = await self._scheduled_tasks.create(
            ScheduledTaskCreateRequest(
                conversation_id=goal.conversation_id,
                owner_member_id=goal.owner_member_id,
                title=f"目标进度追问：{goal.title}",
                goal=f"追问目标进度：{goal.title}",
                schedule=request.schedule,
                execution_policy={"attendance": "unattended"},
                constraints=supervision_constraints,
                created_by_member_id=goal.owner_member_id,
            ),
            trace_id=trace_id,
        )
        now = utc_now_iso()
        await self._repo.insert_policy(
            {
                "policy_id": policy_id,
                "goal_id": goal.goal_id,
                "status": "active",
                "mode": request.mode,
                "frequency": {
                    **request.schedule,
                    "random_jitter_minutes": jitter_minutes,
                },
                "quiet_hours": request.quiet_hours,
                "tone_policy": request.tone_policy,
                "next_checkin_at": (
                    scheduled.next_run_at.isoformat() if scheduled.next_run_at else None
                ),
                "scheduled_task_id": scheduled.scheduled_task_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self._event(
            goal_id,
            "goal.supervision_started",
            {"policy_id": policy_id, "scheduled_task_id": scheduled.scheduled_task_id},
            trace_id=trace_id,
        )
        row = await self._repo.get_policy(policy_id)
        if row is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "goal supervision policy not found",
                status_code=404,
            )
        return GoalSupervisionPolicy(**row)

    async def create_checkin(
        self,
        goal_id: str,
        request: GoalCheckinCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> GoalCheckin:
        goal = await self._goal(goal_id)
        prompt = request.prompt_text or _default_checkin_prompt(goal)
        checkin_id = new_id("gchk")
        now = utc_now_iso()
        await self._repo.insert_checkin(
            {
                "checkin_id": checkin_id,
                "goal_id": goal_id,
                "policy_id": request.policy_id,
                "scheduled_task_id": request.scheduled_task_id,
                "scheduled_run_id": request.scheduled_run_id,
                "prompt_text": prompt,
                "parsed_status": "pending",
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        await self._event(
            goal_id,
            "goal.checkin_created",
            {"checkin_id": checkin_id, "scheduled_run_id": request.scheduled_run_id},
            trace_id=trace_id,
        )
        row = await self._repo.get_checkin(checkin_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "goal checkin not found", status_code=404)
        return GoalCheckin(**row)

    async def reply_checkin(
        self,
        goal_id: str,
        checkin_id: str,
        request: GoalCheckinReplyRequest,
        *,
        trace_id: str | None = None,
    ) -> GoalProgressSnapshot:
        goal = await self._goal(goal_id)
        checkin_row = await self._repo.get_checkin(checkin_id)
        if checkin_row is None or checkin_row["goal_id"] != goal_id:
            raise AppError(ErrorCode.NOT_FOUND, "goal checkin not found", status_code=404)
        parsed_status = _parse_checkin_status(request.reply_text)
        advice = _advice_for_reply(request.reply_text, parsed_status, goal.title)
        encouragement = _encouragement(parsed_status)
        progress_delta = _progress_delta(parsed_status)
        now = utc_now_iso()
        await self._repo.update_checkin(
            checkin_id,
            {
                "user_reply_text_redacted": str(redact(request.reply_text)),
                "parsed_status": parsed_status,
                "progress_delta": progress_delta,
                "advice": advice,
                "encouragement_text": encouragement,
                "trace_id": trace_id,
                "replied_at": now,
            },
        )
        snapshot = await self._create_progress_snapshot(
            goal,
            source_checkin_id=checkin_id,
            parsed_status=parsed_status,
            trace_id=trace_id,
        )
        await self._advance_plan_items(goal, parsed_status)
        await self._event(
            goal_id,
            "goal.checkin_replied",
            {
                "checkin_id": checkin_id,
                "parsed_status": parsed_status,
                "progress_percent": snapshot.progress_percent,
            },
            trace_id=trace_id,
        )
        return snapshot

    async def handle_scheduled_checkin(
        self,
        *,
        scheduled_task: ScheduledTask,
        scheduled_run_id: str,
        trace_id: str | None,
    ) -> dict[str, Any]:
        constraints = dict(scheduled_task.constraints or {})
        goal_id = str(constraints.get("goal_id") or "")
        policy_id = str(constraints.get("policy_id") or "") or None
        if not goal_id:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "goal checkin task missing goal_id",
                status_code=422,
            )
        goal = await self._goal(goal_id)
        checkin = await self.create_checkin(
            goal_id,
            GoalCheckinCreateRequest(
                policy_id=policy_id,
                scheduled_task_id=scheduled_task.scheduled_task_id,
                scheduled_run_id=scheduled_run_id,
                prompt_text=_default_checkin_prompt(goal),
            ),
            trace_id=trace_id,
        )
        summary = f"{checkin.prompt_text}"
        return {
            "goal_id": goal_id,
            "checkin_id": checkin.checkin_id,
            "summary": summary,
        }

    async def latest_progress(self, goal_id: str) -> GoalProgressSnapshot:
        await self._goal(goal_id)
        row = await self._repo.latest_snapshot(goal_id)
        if row is None:
            now = utc_now_iso()
            await self._repo.insert_snapshot(
                {
                    "snapshot_id": new_id("gprog"),
                    "goal_id": goal_id,
                    "progress_percent": 0,
                    "summary": "还没有记录到执行反馈，先从第一次行动开始。",
                    "blockers": [],
                    "next_focus": ["完成计划里的第一个小动作"],
                    "created_at": now,
                }
            )
            row = await self._repo.latest_snapshot(goal_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "goal progress not found", status_code=404)
        return GoalProgressSnapshot(**row)

    async def list_checkins(self, goal_id: str, *, limit: int = 100) -> list[GoalCheckin]:
        await self._goal(goal_id)
        return [GoalCheckin(**row) for row in await self._repo.list_checkins(goal_id, limit=limit)]

    async def list_events(self, goal_id: str, *, limit: int = 100) -> list[GoalEvent]:
        await self._goal(goal_id)
        return [GoalEvent(**row) for row in await self._repo.list_events(goal_id, limit=limit)]

    async def pause(self, goal_id: str, *, trace_id: str | None = None) -> Goal:
        current = await self._goal(goal_id)
        if current.status != "active":
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "only active goals can be paused",
                status_code=409,
            )
        goal = await self._set_goal_status(goal_id, "paused", trace_id=trace_id)
        policy = await self._repo.latest_policy_for_goal(goal_id)
        if policy and policy.get("scheduled_task_id") and self._scheduled_tasks is not None:
            await self._scheduled_tasks.pause(
                policy["scheduled_task_id"],
                reason="goal_paused",
                trace_id=trace_id,
            )
            await self._update_policy_status(policy["policy_id"], "paused")
        return goal

    async def resume(self, goal_id: str, *, trace_id: str | None = None) -> Goal:
        current = await self._goal(goal_id)
        if current.status != "paused":
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "only paused goals can be resumed",
                status_code=409,
            )
        goal = await self._set_goal_status(goal_id, "active", trace_id=trace_id)
        policy = await self._repo.latest_policy_for_goal(goal_id)
        if policy and policy.get("scheduled_task_id") and self._scheduled_tasks is not None:
            await self._scheduled_tasks.resume(policy["scheduled_task_id"], trace_id=trace_id)
            await self._update_policy_status(policy["policy_id"], "active")
        return goal

    async def cancel(self, goal_id: str, *, trace_id: str | None = None) -> Goal:
        current = await self._goal(goal_id)
        if current.status in GOAL_TERMINAL_STATUSES:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "terminal goals cannot be cancelled",
                status_code=409,
            )
        goal = await self._set_goal_status(
            goal_id,
            "cancelled",
            extra={"cancelled_at": utc_now_iso()},
            trace_id=trace_id,
        )
        policy = await self._repo.latest_policy_for_goal(goal_id)
        if policy and policy.get("scheduled_task_id") and self._scheduled_tasks is not None:
            await self._scheduled_tasks.cancel(
                policy["scheduled_task_id"],
                reason="goal_cancelled",
                trace_id=trace_id,
            )
            await self._update_policy_status(policy["policy_id"], "cancelled")
        return goal

    async def archive(self, goal_id: str, *, trace_id: str | None = None) -> Goal:
        return await self._set_goal_status(
            goal_id,
            "archived",
            extra={"archived_at": utc_now_iso()},
            trace_id=trace_id,
        )

    async def try_handle_chat_turn(
        self,
        *,
        text: str,
        conversation_id: str,
        member_id: str,
        turn_id: str,
        trace_id: str | None,
    ) -> GoalChatOutcome | None:
        clean = " ".join(str(text or "").strip().split())
        if not clean:
            return None
        if _looks_like_goal_plan_request(clean):
            bundle = await self.create_goal(
                GoalCreateRequest(
                    conversation_id=conversation_id,
                    owner_member_id=member_id,
                    description=clean,
                    created_from_turn_id=turn_id,
                ),
                trace_id=trace_id,
            )
            text_out = _goal_plan_reply(bundle)
            return GoalChatOutcome(
                intent="goal_plan_request",
                text=text_out,
                payload=_bundle_payload(bundle) | {"action": "goal_created"},
            )
        pending = await self._latest_conversation_goal(
            owner_member_id=member_id,
            conversation_id=conversation_id,
            statuses={"awaiting_confirmation"},
        )
        if pending is not None and _looks_like_goal_confirmation(clean):
            bundle = await self.confirm_plan(
                pending.goal_id,
                pending.active_plan_id or "",
                trace_id=trace_id,
            )
            policy = None
            if _looks_like_supervision_request(clean):
                jitter_minutes = _random_jitter_from_text(clean)
                policy = await self.create_supervision(
                    pending.goal_id,
                    GoalSupervisionRequest(
                        schedule=_schedule_from_text(clean),
                        mode="random_checkin" if jitter_minutes else "scheduled_checkin",
                        random_jitter_minutes=jitter_minutes,
                    ),
                    trace_id=trace_id,
                )
                bundle = await self.detail(pending.goal_id)
            text_out = _goal_confirm_reply(bundle.goal, policy)
            return GoalChatOutcome(
                intent="goal_confirm_plan",
                text=text_out,
                payload=_bundle_payload(bundle)
                | {"policy": policy.model_dump(mode="json") if policy else None},
            )
        if _looks_like_goal_progress_query(clean):
            goal = await self._resolve_single_active_goal(member_id, conversation_id, clean)
            if goal is None:
                return None
            progress = await self.latest_progress(goal.goal_id)
            text_out = _progress_reply(goal, progress)
            return GoalChatOutcome(
                intent="goal_progress_query",
                text=text_out,
                payload={
                    "goal": goal.model_dump(mode="json"),
                    "progress": progress.model_dump(mode="json"),
                },
            )
        if _looks_like_goal_pause(clean):
            goal = await self._resolve_single_active_goal(member_id, conversation_id, clean)
            if goal is None:
                return None
            updated = await self.pause(goal.goal_id, trace_id=trace_id)
            return GoalChatOutcome(
                intent="goal_pause",
                text=f"已暂停「{updated.title}」的监督。计划还在，之后你说继续，我再接上。",
                payload={"goal": updated.model_dump(mode="json")},
            )
        if _looks_like_goal_cancel(clean):
            goal = await self._resolve_single_active_goal(member_id, conversation_id, clean)
            if goal is None:
                return None
            updated = await self.cancel(goal.goal_id, trace_id=trace_id)
            return GoalChatOutcome(
                intent="goal_cancel",
                text=f"已取消「{updated.title}」。我不会再围绕这个目标追问你。",
                payload={"goal": updated.model_dump(mode="json")},
            )
        if _looks_like_checkin_reply(clean):
            checkin = await self._repo.latest_open_checkin(
                owner_member_id=member_id,
                conversation_id=conversation_id,
            )
            goal: Goal | None = None
            if checkin is not None:
                goal = await self._goal(checkin["goal_id"])
                checkin_id = checkin["checkin_id"]
            else:
                goal = await self._resolve_single_active_goal(member_id, conversation_id, clean)
                if goal is None:
                    return None
                created = await self.create_checkin(
                    goal.goal_id,
                    GoalCheckinCreateRequest(prompt_text=_default_checkin_prompt(goal)),
                    trace_id=trace_id,
                )
                checkin_id = created.checkin_id
            progress = await self.reply_checkin(
                goal.goal_id,
                checkin_id,
                GoalCheckinReplyRequest(reply_text=clean),
                trace_id=trace_id,
            )
            status = _parse_checkin_status(clean)
            text_out = _checkin_reply(goal, progress, status, clean)
            return GoalChatOutcome(
                intent="goal_checkin_reply",
                text=text_out,
                payload={
                    "goal": goal.model_dump(mode="json"),
                    "progress": progress.model_dump(mode="json"),
                },
            )
        return None

    async def _create_progress_snapshot(
        self,
        goal: Goal,
        *,
        source_checkin_id: str,
        parsed_status: str,
        trace_id: str | None,
    ) -> GoalProgressSnapshot:
        previous = await self._repo.latest_snapshot(goal.goal_id)
        checkins = await self._repo.list_checkins(goal.goal_id, limit=500)
        completed = sum(1 for item in checkins if item["parsed_status"] == "done")
        partial = sum(1 for item in checkins if item["parsed_status"] == "partial")
        missed = sum(1 for item in checkins if item["parsed_status"] == "missed")
        blocked = sum(1 for item in checkins if item["parsed_status"] == "blocked")
        base = int(previous["progress_percent"]) if previous else 0
        progress = min(100, max(base + int(_progress_delta(parsed_status)["percent"]), 0))
        blockers = _blockers_for_status(parsed_status)
        next_focus = _next_focus(goal.title, parsed_status)
        snapshot_id = new_id("gprog")
        now = utc_now_iso()
        await self._repo.insert_snapshot(
            {
                "snapshot_id": snapshot_id,
                "goal_id": goal.goal_id,
                "progress_percent": progress,
                "completed_count": completed,
                "partial_count": partial,
                "missed_count": missed,
                "blocked_count": blocked,
                "streak_days": _streak_from_checkins(checkins),
                "summary": _progress_summary(goal.title, progress, parsed_status),
                "blockers": blockers,
                "next_focus": next_focus,
                "source_checkin_id": source_checkin_id,
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        row = await self._repo.latest_snapshot(goal.goal_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "goal progress not found", status_code=404)
        return GoalProgressSnapshot(**row)

    async def _advance_plan_items(self, goal: Goal, parsed_status: str) -> None:
        if not goal.active_plan_id:
            return
        rows = await self._repo.list_plan_items(goal.active_plan_id)
        if not rows:
            return
        now = utc_now_iso()
        if parsed_status in {"done", "partial"}:
            await self._mark_first_plan_item(rows, "planning", "completed", now)
            await self._mark_first_plan_item(rows, "routine", "in_progress", now)
            await self._mark_first_plan_item(rows, "checkin", "in_progress", now)
        elif parsed_status in {"missed", "blocked", "unclear"}:
            await self._mark_first_plan_item(rows, "routine", "in_progress", now)
            await self._mark_first_plan_item(rows, "checkin", "in_progress", now)
        await self._mark_first_plan_item(rows, "review", "planned", now, only_if_missing=True)

    async def _mark_first_plan_item(
        self,
        rows: list[dict[str, Any]],
        item_type: str,
        status: str,
        updated_at: str,
        *,
        only_if_missing: bool = False,
    ) -> None:
        for row in rows:
            if row.get("item_type") != item_type:
                continue
            if only_if_missing and row.get("status"):
                return
            if row.get("status") == status:
                return
            await self._repo.update_plan_item(
                row["goal_plan_item_id"],
                {"status": status, "updated_at": updated_at},
            )
            return

    async def _goal(self, goal_id: str) -> Goal:
        row = await self._repo.get_goal(goal_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "goal not found", status_code=404)
        return Goal(**row)

    async def _plan(self, goal_plan_id: str) -> GoalPlan:
        row = await self._repo.get_plan(goal_plan_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "goal plan not found", status_code=404)
        return GoalPlan(**row)

    async def _event(
        self,
        goal_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> None:
        await self._repo.insert_event(
            {
                "event_id": new_id("gevt"),
                "goal_id": goal_id,
                "event_type": event_type,
                "payload": payload,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )

    async def _set_goal_status(
        self,
        goal_id: str,
        status: str,
        *,
        extra: dict[str, Any] | None = None,
        trace_id: str | None,
    ) -> Goal:
        await self._goal(goal_id)
        await self._repo.update_goal(
            goal_id,
            {"status": status, "updated_at": utc_now_iso(), **(extra or {})},
        )
        await self._event(goal_id, f"goal.{status}", {"status": status}, trace_id=trace_id)
        return await self._goal(goal_id)

    async def _update_policy_status(self, policy_id: str, status: str) -> None:
        await self._repo.update_policy(
            policy_id,
            {"status": status, "updated_at": utc_now_iso()},
        )

    async def _latest_conversation_goal(
        self,
        *,
        owner_member_id: str,
        conversation_id: str,
        statuses: set[str],
    ) -> Goal | None:
        rows = await self._repo.list_goals(
            owner_member_id=owner_member_id,
            conversation_id=conversation_id,
            limit=20,
        )
        for row in rows:
            if row["status"] in statuses:
                return Goal(**row)
        return None

    async def _resolve_single_active_goal(
        self,
        owner_member_id: str,
        conversation_id: str,
        text: str,
    ) -> Goal | None:
        rows = await self._repo.list_goals(
            owner_member_id=owner_member_id,
            conversation_id=conversation_id,
            limit=20,
        )
        goals = [Goal(**row) for row in rows if row["status"] in {"active", "paused"}]
        if not goals:
            return None
        scored = sorted(
            ((_match_score(goal.title, text), goal) for goal in goals),
            key=lambda item: item[0],
            reverse=True,
        )
        if scored and scored[0][0] > 0:
            return scored[0][1]
        if len(goals) == 1:
            return goals[0]
        return None


def _build_plan(*, title: str, description: str) -> dict[str, Any]:
    return {
        "summary": f"围绕「{title}」先做一个可执行的四步计划：定目标、排节奏、做反馈、按周微调。",
        "success_criteria": [
            "目标被拆成可以每天或每周执行的小动作",
            "每次监督都能记录完成、部分完成、未完成或卡住",
            "根据反馈持续更新进度和下一步重点",
        ],
        "assumptions": ["当前先按通用目标辅助流程制定计划，细节可以在执行中逐步补齐。"],
        "risk_notes": [
            "如涉及身体不适、疾病、考试政策或证书规则等高影响事项，建议以专业人士或官方信息为准。",
        ],
        "items": [
            {
                "title": "明确目标和衡量标准",
                "description": (
                    f"把「{description}」整理成一个明确目标，并写下怎样算有进展、怎样算完成。"
                ),
                "item_type": "planning",
                "cadence": {"type": "once"},
                "success_metric": {"type": "checklist", "target": 1},
            },
            {
                "title": "建立低门槛执行节奏",
                "description": "先安排一个容易开始的固定动作，保证能持续，而不是一开始追求强度。",
                "item_type": "routine",
                "cadence": {"type": "daily_or_weekly"},
                "success_metric": {"type": "completion"},
            },
            {
                "title": "监督追问和记录反馈",
                "description": "按约定时间追问完成情况，记录完成、部分完成、未完成或卡住。",
                "item_type": "checkin",
                "cadence": {"type": "scheduled"},
                "success_metric": {"type": "checkin_reply"},
            },
            {
                "title": "复盘并调整下一步",
                "description": "根据最近反馈更新进度、识别阻碍，并把下一步改得更具体。",
                "item_type": "review",
                "cadence": {"type": "weekly"},
                "success_metric": {"type": "review_done"},
            },
        ],
    }


def _title_from_text(text: str) -> str:
    clean = " ".join(str(text or "").strip().split())
    clean = re.sub(r"^(我想|我要|我准备|我打算|帮我|请帮我)\s*", "", clean)
    clean = re.sub(r"(给我)?(制定|做|生成|规划).{0,6}计划.*$", "", clean).strip("，。,. ")
    return (clean[:40] or "长期目标")


def _domain_label(text: str) -> str:
    if any(
        marker in text
        for marker in (
            "健身",
            "运动",
            "减脂",
            "增肌",
            "跑步",
            "半马",
            "马拉松",
            "瑜伽",
            "力量训练",
            "快走",
            "游泳",
            "体能",
            "塑形",
            "有氧",
            "无氧",
        )
    ):
        return "fitness"
    if any(
        marker in text
        for marker in (
            "英语",
            "学",
            "学习",
            "考研",
            "考证",
            "考试",
            "备考",
            "复习",
            "证书",
            "资格证",
        )
    ):
        return "learning"
    if "证" in text and any(marker in text for marker in ("考", "准备", "复习", "备考")):
        return "learning"
    return "general"


def _default_checkin_prompt(goal: Goal) -> str:
    return f"今天「{goal.title}」进展怎么样？回复完成、部分完成、没完成或卡住了都可以。"


def _parse_checkin_status(text: str) -> str:
    clean = str(text or "")
    if any(
        marker in clean
        for marker in (
            "卡住",
            "卡点",
            "瓶颈",
            "不会",
            "不懂",
            "方法不对",
            "困难",
            "阻力",
            "受伤",
            "疼",
            "痛",
            "难受",
            "阻碍",
        )
    ):
        return "blocked"
    if any(
        marker in clean
        for marker in (
            "做了一半",
            "一半",
            "部分",
            "一点",
            "半小时",
            "有一点",
            "推进",
            "有进展",
            "没全部",
        )
    ):
        return "partial"
    if any(
        marker in clean
        for marker in (
            "没做",
            "没完成",
            "没时间",
            "没来得及",
            "来不及",
            "忘了",
            "耽搁",
            "拖延",
            "失败",
            "没有",
        )
    ):
        return "missed"
    if any(
        marker in clean
        for marker in ("完成", "做完", "搞定", "已做", "打卡", "按计划", "done", "finished")
    ):
        return "done"
    return "unclear"


def _progress_delta(status: str) -> dict[str, Any]:
    return {
        "done": {"percent": 10},
        "partial": {"percent": 5},
        "missed": {"percent": 0},
        "blocked": {"percent": 2},
        "unclear": {"percent": 0},
    }.get(status, {"percent": 0})


def _advice_for_reply(text: str, status: str, title: str) -> dict[str, Any]:
    advice = {
        "done": "保持这个节奏，下一次可以继续做同样的小动作。",
        "partial": "已经有进展了。下一次把动作再缩小一点，优先保证能开始。",
        "missed": "没关系，把下一步降到最小动作，先恢复连续性。",
        "blocked": "先把卡点说清楚：是时间、方法、资源还是状态问题。下一次只解决一个阻碍。",
        "unclear": "可以用一句话回复完成、部分完成、没完成或卡住，我会据此更新进度。",
    }.get(status, "继续记录即可。")
    if any(marker in text for marker in ("疼", "痛", "受伤", "胸闷", "头晕", "严重")):
        advice = "如果有明显疼痛、受伤或严重不适，先停止相关动作，并考虑咨询专业人士。"
    return {"summary": advice, "goal_title": title}


def _encouragement(status: str) -> str:
    return {
        "done": "很好，今天这一格算扎实落下了。",
        "partial": "做到一部分也算推进，别把它归零。",
        "missed": "偶尔断一下没关系，明天从最小动作接回来。",
        "blocked": "卡住不是失败，是计划需要调整的信号。",
        "unclear": "我先不硬判定，等你补一句完成情况。",
    }.get(status, "继续往前。")


def _blockers_for_status(status: str) -> list[str]:
    return ["reported_blocker"] if status == "blocked" else []


def _next_focus(title: str, status: str) -> list[str]:
    if status == "done":
        return [f"继续完成「{title}」的下一次固定动作"]
    if status == "partial":
        return [f"把「{title}」下一步缩小到 10-20 分钟内"]
    if status == "missed":
        return [f"为「{title}」安排一个最小可完成动作"]
    if status == "blocked":
        return [f"先拆解「{title}」当前卡点"]
    return [f"补充「{title}」的完成情况"]


def _streak_from_checkins(checkins: list[dict[str, Any]]) -> int:
    streak = 0
    for item in checkins:
        if item["parsed_status"] in {"done", "partial"}:
            streak += 1
        elif item["parsed_status"] in {"missed", "blocked"}:
            break
    return streak


def _progress_summary(title: str, progress: int, status: str) -> str:
    return f"「{title}」当前进度约 {progress}%，最近一次反馈为 {status}。"


def _looks_like_goal_plan_request(text: str) -> bool:
    if any(marker in text for marker in ("提醒我", "定时", "闹钟", "每隔", "明天")):
        return False
    has_goal_marker = any(
        marker in text for marker in ("我要", "我想", "我准备", "我打算", "目标", "监督我")
    )
    has_plan_marker = any(marker in text for marker in ("计划", "规划", "制定", "安排"))
    return has_goal_marker and has_plan_marker


def _looks_like_goal_confirmation(text: str) -> bool:
    return any(
        marker in text for marker in ("可以", "确定", "确认", "就按", "开始", "执行", "行", "好")
    )


def _looks_like_supervision_request(text: str) -> bool:
    return any(
        marker in text
        for marker in ("监督", "提醒", "追问", "每天", "每周", "晚上", "早上", "随机", "不固定")
    )


def _looks_like_goal_progress_query(text: str) -> bool:
    return "进度" in text and any(
        marker in text for marker in ("目标", "计划", "怎么样", "查看", "现在")
    )


def _looks_like_goal_pause(text: str) -> bool:
    return any(marker in text for marker in ("暂停监督", "暂停目标", "先停一下监督"))


def _looks_like_goal_cancel(text: str) -> bool:
    return any(marker in text for marker in ("取消目标", "停止监督", "不要监督", "取消监督"))


def _looks_like_checkin_reply(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "完成",
            "做完",
            "打卡",
            "按计划",
            "没做",
            "没完成",
            "没来得及",
            "来不及",
            "做了一半",
            "部分",
            "推进",
            "卡住",
            "卡点",
            "瓶颈",
            "没时间",
            "忘了",
            "done",
            "finished",
        )
    )


def _schedule_from_text(text: str) -> dict[str, Any]:
    if "每周" in text:
        return {
            "type": "weekly",
            "days": ["monday"],
            "time": _time_from_text(text),
            "timezone": "Asia/Shanghai",
        }
    return {"type": "daily", "time": _time_from_text(text), "timezone": "Asia/Shanghai"}


def _random_jitter_from_text(text: str) -> int:
    if not any(marker in text for marker in ("随机", "不固定", "弹性", "随便找时间")):
        return 0
    match = re.search(r"随机\s*(\d{1,3})\s*分钟", text)
    if match:
        return max(1, min(int(match.group(1)), 720))
    match = re.search(r"(\d{1,3})\s*分钟.*随机", text)
    if match:
        return max(1, min(int(match.group(1)), 720))
    return 30


def _time_from_text(text: str) -> str:
    match = re.search(r"(\d{1,2})[:：](\d{2})", text)
    if match:
        return f"{int(match.group(1)) % 24:02d}:{int(match.group(2)) % 60:02d}"
    match = re.search(r"(早上|上午|中午|下午|晚上)?\s*(\d{1,2})\s*点", text)
    if match:
        prefix = match.group(1) or ""
        hour = int(match.group(2))
        if prefix in {"下午", "晚上"} and hour < 12:
            hour += 12
        if prefix == "中午" and hour < 11:
            hour += 12
        return f"{hour % 24:02d}:00"
    if "早" in text:
        return "08:00"
    if "晚" in text:
        return "20:00"
    return "20:00"


def _match_score(goal_title: str, text: str) -> int:
    needle = re.sub(r"\s+", "", goal_title.lower())
    haystack = re.sub(r"\s+", "", text.lower())
    if needle and needle in haystack:
        return 100 + len(needle)
    chars = set(needle) - set("的了我你他她它一个目标计划进度监督")
    overlap = len(chars & set(haystack))
    return overlap if overlap >= 2 else 0


def _bundle_payload(bundle: GoalBundle) -> dict[str, Any]:
    return {
        "goal": bundle.goal.model_dump(mode="json"),
        "active_plan": bundle.active_plan.model_dump(mode="json") if bundle.active_plan else None,
        "plan_items": [item.model_dump(mode="json") for item in bundle.plan_items],
        "progress": bundle.progress.model_dump(mode="json") if bundle.progress else None,
    }


def _goal_plan_reply(bundle: GoalBundle) -> str:
    lines = [f"可以。我先把「{bundle.goal.title}」设成一个目标，并给你一版通用执行计划："]
    for item in bundle.plan_items:
        lines.append(f"{item.sort_order}. {item.title}：{item.description}")
    lines.append("你确认后，我可以按你指定的节奏开始监督，定时追问完成情况，并根据反馈更新进度。")
    return "\n".join(lines)


def _goal_confirm_reply(goal: Goal, policy: GoalSupervisionPolicy | None) -> str:
    if policy is None:
        return (
            f"已确认「{goal.title}」计划。你告诉我监督频率，"
            "比如每天晚上 8 点，我就开始追问和记录进度。"
        )
    return (
        f"已确认「{goal.title}」计划，并开启监督。"
        "我会按约定节奏追问完成情况，记录进度，再给你建议和鼓励。"
    )


def _progress_reply(goal: Goal, progress: GoalProgressSnapshot) -> str:
    return f"「{goal.title}」当前进度约 {progress.progress_percent}%。{progress.summary}"


def _checkin_reply(goal: Goal, progress: GoalProgressSnapshot, status: str, reply_text: str) -> str:
    advice = _advice_for_reply(reply_text, status, goal.title)["summary"]
    return (
        f"收到，已记录到「{goal.title}」。{advice} "
        f"当前进度约 {progress.progress_percent}%。{_encouragement(status)}"
    )
