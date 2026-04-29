from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from core_types import (
    ApprovalDetail,
    CollaborationOutput,
    CollaborationPlan,
    CollaborationReplay,
    CollaborationRound,
    ErrorCode,
    HostDecision,
    MemberAvailability,
    RiskLevel,
    SkillPolicy,
    TaskArtifact,
    TaskDetail,
    TaskMode,
    TaskParticipant,
    TaskStatus,
    TaskSubtask,
    ToolCallRecord,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.task_repo import TaskRepository
from app.services.artifacts import ArtifactStore
from app.services.audit import AuditEventService

DEFAULT_ORGANIZATION_ID = "org_default"
DEFAULT_HOST_MEMBER_ID = "mem_xiaoyao"


class SupervisorService:
    def __init__(
        self,
        *,
        repo: TaskRepository,
        member_repo: MemberRepository,
        artifact_store: ArtifactStore,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._members = member_repo
        self._artifacts = artifact_store
        self._trace = trace_service
        self._audit = audit_service
        self._task_detail_provider: TaskDetailProvider | None = None
        self._extension_replay_provider: ExtensionReplayProvider | None = None

    def set_task_detail_provider(self, provider: TaskDetailProvider) -> None:
        self._task_detail_provider = provider

    def set_extension_replay_provider(self, provider: ExtensionReplayProvider) -> None:
        self._extension_replay_provider = provider

    async def plan(self, task_id: str, *, trace_id: str | None = None) -> CollaborationReplay:
        task = await self._get_supervisor_task(task_id)
        existing = await self._repo.get_collaboration_plan(task_id)
        if existing is not None:
            return await self.replay(task_id, trace_id=trace_id)

        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SUPERVISOR_PLAN,
            "plan supervisor task",
            input_data={"task_id": task_id, "goal": task["goal"]},
        )
        try:
            now = utc_now_iso()
            host_member_id = task.get("host_member_id") or task.get("owner_member_id")
            if not host_member_id:
                host_member_id = DEFAULT_HOST_MEMBER_ID
            host = await self._members.get_member(host_member_id)
            if host is None:
                raise AppError(
                    ErrorCode.SUPERVISOR_PLAN_FAILED,
                    "Supervisor 任务缺少有效主持成员",
                    status_code=409,
                )
            select_span_id = await self._start_span(
                trace_id,
                TraceSpanType.SUPERVISOR_PARTICIPANT_SELECT,
                "select supervisor participants",
                input_data={"task_id": task_id, "host_member_id": host_member_id},
            )
            try:
                selected = await self._select_participants(task, host_member_id)
            except Exception:
                await self._end_span(select_span_id, status=TraceSpanStatus.FAILED)
                raise
            await self._end_span(
                select_span_id,
                output_data={"selected_member_ids": [item["member_id"] for item in selected]},
            )
            if len(selected) < 2:
                raise AppError(
                    ErrorCode.SUPERVISOR_PARTICIPANT_SELECTION_FAILED,
                    "没有足够可用成员参与协作",
                    status_code=409,
                )

            plan_id = new_id("cplan")
            participant_policy = {
                "host_member_id": host_member_id,
                "participant_member_ids": [item["member_id"] for item in selected],
                "max_active_participants": min(4, len(selected)),
                "turn_policy": "host_moderated",
                "allow_spontaneous_reply": False,
            }
            success_criteria = task.get("success_criteria") or ["多成员输出可追溯来源"]

            async with self._repo.transaction():
                await self._repo.upsert_collaboration_plan(
                    {
                        "collaboration_plan_id": plan_id,
                        "organization_id": task["organization_id"],
                        "task_id": task_id,
                        "host_member_id": host_member_id,
                        "mode": self._mode_for_goal(task["goal"]),
                        "max_rounds": 4,
                        "participant_policy": participant_policy,
                        "success_criteria": success_criteria,
                        "risk_summary": {
                            "risk_level": task["risk_level"],
                            "approval_required": False,
                        },
                        "status": "planned",
                        "trace_id": trace_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                participant_ids: list[str] = []
                plan_participants: list[dict[str, Any]] = []
                for index, item in enumerate(selected, start=1):
                    participant_id = new_id("pt")
                    context_span_id = await self._start_span(
                        trace_id,
                        TraceSpanType.SUBTASK_CONTEXT_BUILD,
                        "build participant context scope",
                        input_data={
                            "task_id": task_id,
                            "participant_id": participant_id,
                            "member_id": item["member_id"],
                        },
                    )
                    context_scope = self._context_scope(task, item)
                    await self._end_span(
                        context_span_id,
                        output_data={"context_scope": context_scope},
                    )
                    participant_ids.append(participant_id)
                    allowed_skills = await self._allowed_skills_for_member(item["member_id"])
                    await self._repo.insert_participant(
                        {
                            "participant_id": participant_id,
                            "organization_id": task["organization_id"],
                            "task_id": task_id,
                            "member_id": item["member_id"],
                            "role_in_task": item["role_in_task"],
                            "participant_type": item["participant_type"],
                            "status": "context_prepared",
                            "selection_reason": item["selection_reason"],
                            "context_scope": context_scope,
                            "allowed_skills": allowed_skills,
                            "allowed_mcp_tools": [],
                            "trace_id": trace_id,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    await self._repo.insert_subtask(
                        {
                            "subtask_id": new_id("subtask"),
                            "organization_id": task["organization_id"],
                            "parent_task_id": task_id,
                            "participant_id": participant_id,
                            "assigned_member_id": item["member_id"],
                            "title": item["subtask_title"],
                            "objective": item["objective"],
                            "status": "ready",
                            "sequence": index,
                            "context_scope": context_scope,
                            "allowed_skills": allowed_skills,
                            "allowed_mcp_tools": [],
                            "trace_id": trace_id,
                            "created_at": now,
                            "updated_at": now,
                        }
                    )
                    plan_participants.append(
                        {
                            "participant_id": participant_id,
                            "member_id": item["member_id"],
                            "role_in_task": item["role_in_task"],
                            "selection_reason": item["selection_reason"],
                        }
                    )
                    await self._event(
                        task_id,
                        "participant.selected",
                        {
                            "participant_id": participant_id,
                            "member_id": item["member_id"],
                            "reason": item["selection_reason"],
                        },
                        trace_id=trace_id,
                    )
                    await self._audit.write_event(
                        actor_type="system",
                        action="participant.selected",
                        object_type="task_participant",
                        object_id=participant_id,
                        summary="协作参与者已选择",
                        risk_level=RiskLevel.R1,
                        payload={
                            "task_id": task_id,
                            "member_id": item["member_id"],
                            "selection_reason": item["selection_reason"],
                        },
                        trace_id=trace_id,
                    )
                    await self._event(
                        task_id,
                        "participant.context_ready",
                        {"participant_id": participant_id, "context_scope": context_scope},
                        trace_id=trace_id,
                    )
                    await self._event(
                        task_id,
                        "subtask.created",
                        {
                            "participant_id": participant_id,
                            "member_id": item["member_id"],
                            "title": item["subtask_title"],
                        },
                        trace_id=trace_id,
                    )
                round_id = new_id("round")
                await self._repo.insert_round(
                    {
                        "round_id": round_id,
                        "organization_id": task["organization_id"],
                        "task_id": task_id,
                        "collaboration_plan_id": plan_id,
                        "round_index": 1,
                        "mode": "parallel",
                        "status": "ready",
                        "participant_ids": participant_ids,
                        "prompt_summary": "各成员从自身角色视角输出一份可汇总建议。",
                        "trace_id": trace_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                updated_plan = dict(task.get("plan", {}))
                updated_plan["host_member_id"] = host_member_id
                updated_plan["participants"] = plan_participants
                updated_plan["collaboration"] = {
                    "mode": "parallel_then_host_summary",
                    "max_rounds": 4,
                    "round_ids": [round_id],
                }
                await self._repo.update_task(
                    task_id,
                    {
                        "host_member_id": host_member_id,
                        "collaboration_plan_id": plan_id,
                        "supervisor_mode": "parallel_then_host_summary",
                        "plan": updated_plan,
                        "updated_at": now,
                    },
                )
                await self._event(
                    task_id,
                    "supervisor.planned",
                    {
                        "collaboration_plan_id": plan_id,
                        "host_member_id": host_member_id,
                        "participant_count": len(selected),
                    },
                    trace_id=trace_id,
                )
            await self._audit.write_event(
                actor_type="system",
                action="supervisor.planned",
                object_type="task",
                object_id=task_id,
                summary="Supervisor 协作计划已生成",
                risk_level=RiskLevel.R1,
                payload={
                    "task_id": task_id,
                    "host_member_id": host_member_id,
                    "participants": [item["member_id"] for item in selected],
                },
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"participant_count": len(selected)})
            return await self.replay(task_id, trace_id=trace_id)
        except Exception as exc:
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.SUPERVISOR_PLAN_FAILED)},
            )
            raise

    async def start(self, task_id: str, *, trace_id: str | None = None) -> dict[str, Any]:
        task = await self._get_supervisor_task(task_id)
        plan = await self._repo.get_collaboration_plan(task_id)
        if plan is None:
            await self.plan(task_id, trace_id=trace_id)
            plan = await self._repo.get_collaboration_plan(task_id)
        if plan is None:
            raise AppError(ErrorCode.SUPERVISOR_PLAN_FAILED, "协作计划不存在", status_code=409)

        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SUPERVISOR_ROUND_RUN,
            "run collaboration rounds",
            input_data={"task_id": task_id, "plan_id": plan["collaboration_plan_id"]},
        )
        try:
            rounds = await self._repo.list_rounds(task_id)
            round_row = rounds[0] if rounds else None
            if round_row is None:
                raise AppError(
                    ErrorCode.COLLABORATION_ROUND_FAILED,
                    "协作轮次不存在",
                    status_code=409,
                )
            now = utc_now_iso()
            await self._repo.update_round(
                round_row["round_id"],
                {"status": "running", "updated_at": now},
            )
            await self._event(
                task_id,
                "collaboration.round_started",
                {"round_id": round_row["round_id"], "mode": round_row["mode"]},
                trace_id=trace_id,
            )

            output_refs: list[dict[str, Any]] = []
            for subtask in await self._repo.list_subtasks(task_id):
                if subtask["status"] in {"completed", "skipped", "merged"}:
                    continue
                output = await self._run_subtask(task, plan, round_row, subtask, trace_id=trace_id)
                output_refs.append(
                    {
                        "output_id": output.output_id,
                        "member_id": output.member_id,
                        "subtask_id": output.subtask_id,
                    }
                )
            await self._repo.update_round(
                round_row["round_id"],
                {
                    "status": "completed",
                    "round_summary": {
                        "output_count": len(output_refs),
                        "source_refs": output_refs,
                    },
                    "updated_at": utc_now_iso(),
                    "completed_at": utc_now_iso(),
                },
            )
            await self._event(
                task_id,
                "collaboration.round_completed",
                {"round_id": round_row["round_id"], "output_count": len(output_refs)},
                trace_id=trace_id,
            )
            await self._event(
                task_id,
                "host.review_started",
                {"host_member_id": plan["host_member_id"]},
                trace_id=trace_id,
            )
            await self._repo.update_task(
                task_id,
                {"status": TaskStatus.SYNTHESIZING.value, "updated_at": utc_now_iso()},
            )
            decision = await self._host_synthesis(task, plan, output_refs, trace_id=trace_id)
            await self._event(
                task_id,
                "host.review_completed",
                {"decision_id": decision.decision_id, "status": decision.status},
                trace_id=trace_id,
            )
            await self._event(
                task_id,
                "host.synthesis_completed",
                {"decision_id": decision.decision_id, "source_refs": decision.source_refs},
                trace_id=trace_id,
            )
            artifact = await self._artifacts.write_text(
                task_id=task_id,
                organization_id=task["organization_id"],
                display_name="collaboration-summary.md",
                content=_collaboration_report(task, decision, output_refs),
                artifact_type="collaboration_report",
                trace_id=trace_id,
            )
            result = {
                "summary": decision.summary,
                "source_refs": decision.source_refs,
                "artifact_ids": [artifact.artifact_id],
                "degraded": True,
                "degraded_reason": "member_brains_not_configured",
            }
            steps = await self._repo.list_steps(task_id)
            for step in steps:
                if step["status"] != "completed":
                    await self._repo.update_step(
                        step["step_id"],
                        {
                            "status": "completed",
                            "output": result,
                            "updated_at": utc_now_iso(),
                        },
                    )
            await self._repo.update_task(
                task_id,
                {
                    "progress": {
                        "total_steps": len(steps),
                        "completed_steps": len(steps),
                        "current_step_key": None,
                    },
                    "updated_at": utc_now_iso(),
                },
            )
            await self._audit.write_event(
                actor_type="system",
                action="host.synthesis_completed",
                object_type="task",
                object_id=task_id,
                summary="主持成员已完成多成员汇总",
                risk_level=RiskLevel.R1,
                payload={"task_id": task_id, "source_refs": output_refs},
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"status": "completed"})
            return result
        except Exception as exc:
            await self._event(
                task_id,
                "subtask.failed",
                {"error": str(redact(str(exc)))},
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.SUBTASK_RUN_FAILED)},
            )
            raise

    async def participants(self, task_id: str) -> list[TaskParticipant]:
        await self._get_supervisor_task(task_id)
        return [TaskParticipant(**row) for row in await self._repo.list_participants(task_id)]

    async def subtasks(self, task_id: str) -> list[TaskSubtask]:
        await self._get_supervisor_task(task_id)
        return [TaskSubtask(**row) for row in await self._repo.list_subtasks(task_id)]

    async def remove_participant(
        self,
        task_id: str,
        participant_id: str,
        *,
        reason: str,
        trace_id: str | None = None,
    ) -> TaskParticipant:
        task = await self._get_supervisor_task(task_id)
        _ensure_mutable_task(task)
        participant = await self._repo.get_participant(participant_id)
        if participant is None or participant["task_id"] != task_id:
            raise AppError(ErrorCode.PARTICIPANT_NOT_FOUND, "参与者不存在", status_code=404)
        if participant["participant_type"] == "host":
            raise AppError(
                ErrorCode.PARTICIPANT_PERMISSION_DENIED,
                "主持成员不能从协作计划中移除",
                status_code=409,
            )
        await self._repo.update_participant(
            participant_id,
            {
                "status": "removed",
                "error_summary": reason,
                "removed_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
            },
        )
        for subtask in await self._repo.list_subtasks(task_id):
            if subtask["participant_id"] == participant_id and subtask["status"] not in {
                "completed",
                "merged",
            }:
                await self._repo.update_subtask(
                    subtask["subtask_id"],
                    {
                        "status": "skipped",
                        "error_summary": f"participant_removed:{reason}",
                        "updated_at": utc_now_iso(),
                        "completed_at": utc_now_iso(),
                    },
                )
        await self._event(
            task_id,
            "participant.removed",
            {"participant_id": participant_id, "reason": reason},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="participant.removed",
            object_type="task_participant",
            object_id=participant_id,
            summary="协作参与者已移除",
            risk_level=RiskLevel.R1,
            payload={"task_id": task_id, "reason": reason},
            trace_id=trace_id,
        )
        updated = await self._repo.get_participant(participant_id)
        return TaskParticipant(**updated) if updated else TaskParticipant(**participant)

    async def retry_subtask(
        self,
        task_id: str,
        subtask_id: str,
        *,
        trace_id: str | None = None,
    ) -> TaskSubtask:
        task = await self._get_supervisor_task(task_id)
        _ensure_mutable_task(task)
        subtask = await self._repo.get_subtask(subtask_id)
        if subtask is None or subtask["parent_task_id"] != task_id:
            raise AppError(ErrorCode.NOT_FOUND, "子任务不存在", status_code=404)
        if subtask["status"] not in {"failed", "skipped", "cancelled"}:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "只有 failed/skipped/cancelled 子任务可以重试",
                status_code=409,
            )
        await self._repo.update_subtask(
            subtask_id,
            {
                "status": "ready",
                "error_code": None,
                "error_summary": None,
                "updated_at": utc_now_iso(),
            },
        )
        await self._event(task_id, "subtask.retry", {"subtask_id": subtask_id}, trace_id=trace_id)
        updated = await self._repo.get_subtask(subtask_id)
        return TaskSubtask(**updated) if updated else TaskSubtask(**subtask)

    async def skip_subtask(
        self,
        task_id: str,
        subtask_id: str,
        *,
        reason: str | None,
        trace_id: str | None = None,
    ) -> TaskSubtask:
        task = await self._get_supervisor_task(task_id)
        _ensure_mutable_task(task)
        subtask = await self._repo.get_subtask(subtask_id)
        if subtask is None or subtask["parent_task_id"] != task_id:
            raise AppError(ErrorCode.NOT_FOUND, "子任务不存在", status_code=404)
        if subtask["status"] in {"completed", "merged"}:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "已完成子任务不能跳过",
                status_code=409,
            )
        await self._repo.update_subtask(
            subtask_id,
            {
                "status": "skipped",
                "error_summary": reason,
                "updated_at": utc_now_iso(),
                "completed_at": utc_now_iso(),
            },
        )
        await self._event(
            task_id,
            "subtask.skipped",
            {"subtask_id": subtask_id, "reason": reason},
            trace_id=trace_id,
        )
        updated = await self._repo.get_subtask(subtask_id)
        return TaskSubtask(**updated) if updated else TaskSubtask(**subtask)

    async def replay(self, task_id: str, *, trace_id: str | None = None) -> CollaborationReplay:
        task = await self._task_detail(task_id)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.COLLABORATION_REPLAY,
            "build collaboration replay",
            input_data={"task_id": task_id},
        )
        plan_row = await self._repo.get_collaboration_plan(task_id)
        skill_runs: list[dict[str, Any]] = []
        mcp_calls: list[dict[str, Any]] = []
        if self._extension_replay_provider is not None:
            skill_runs, mcp_calls = await self._extension_replay_provider(task_id)
        replay = CollaborationReplay(
            task=task,
            collaboration_plan=CollaborationPlan(**plan_row) if plan_row else None,
            participants=[
                TaskParticipant(**row) for row in await self._repo.list_participants(task_id)
            ],
            subtasks=[TaskSubtask(**row) for row in await self._repo.list_subtasks(task_id)],
            rounds=[CollaborationRound(**row) for row in await self._repo.list_rounds(task_id)],
            outputs=[
                CollaborationOutput(**row)
                for row in await self._repo.list_collaboration_outputs(task_id)
            ],
            host_decisions=[
                HostDecision(**row) for row in await self._repo.list_host_decisions(task_id)
            ],
            tool_calls=[
                ToolCallRecord(**row) for row in await self._repo.list_tool_calls(task_id)
            ],
            skill_runs=skill_runs,
            mcp_calls=mcp_calls,
            approvals=[ApprovalDetail(**row) for row in await self._repo.list_approvals(task_id)],
            artifacts=await self._task_artifacts(task_id),
            trace={"trace_id": task.trace_id, "span_refs": []},
            final_result=task.result,
        )
        await self._end_span(span_id, output_data={"participant_count": len(replay.participants)})
        return replay

    async def get_availability(self, member_id: str) -> MemberAvailability:
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        row = await self._repo.get_availability(member_id)
        if row is None:
            now = utc_now_iso()
            await self._repo.upsert_availability(
                {
                    "member_id": member_id,
                    "organization_id": member["organization_id"],
                    "status": "available",
                    "capacity": 1,
                    "current_load": 0,
                    "source": "default",
                    "updated_at": now,
                }
            )
            row = await self._repo.get_availability(member_id)
        return MemberAvailability(**row) if row else MemberAvailability(**member)

    async def update_availability(
        self,
        member_id: str,
        payload: dict[str, Any],
    ) -> MemberAvailability:
        member = await self._members.get_member(member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        if payload.get("status") not in {"available", "busy", "unavailable", "offline"}:
            raise AppError(ErrorCode.VALIDATION_ERROR, "成员可用性状态不合法", status_code=422)
        data = {
            "member_id": member_id,
            "organization_id": member["organization_id"],
            "status": payload.get("status", "available"),
            "capacity": payload.get("capacity", 1),
            "current_load": payload.get("current_load", 0),
            "unavailable_reason": payload.get("unavailable_reason"),
            "schedule": payload.get("schedule", {}),
            "source": "manual",
            "updated_at": utc_now_iso(),
        }
        await self._repo.upsert_availability(data)
        row = await self._repo.get_availability(member_id)
        await self._audit.write_event(
            actor_type="system",
            action="member.availability_updated",
            object_type="member",
            object_id=member_id,
            summary="成员可用性已更新",
            risk_level=RiskLevel.R0,
            payload=data,
        )
        return MemberAvailability(**row) if row else MemberAvailability(**data)

    async def get_skill_policy(self, subject_type: str, subject_id: str) -> SkillPolicy:
        await self._ensure_policy_subject(subject_type, subject_id)
        row = await self._repo.get_skill_policy(subject_type, subject_id)
        if row is None:
            data = self._default_policy(subject_type, subject_id)
            await self._repo.upsert_skill_policy(data)
            row = await self._repo.get_skill_policy(subject_type, subject_id)
        return SkillPolicy(**row) if row else SkillPolicy(**data)

    async def update_skill_policy(
        self,
        subject_type: str,
        subject_id: str,
        payload: dict[str, Any],
    ) -> SkillPolicy:
        await self._ensure_policy_subject(subject_type, subject_id)
        data = self._default_policy(subject_type, subject_id)
        data.update(
            {
                "allowed_skills": payload.get("allowed_skills", []),
                "denied_skills": payload.get("denied_skills", []),
                "allowed_mcp_tools": payload.get("allowed_mcp_tools", []),
                "denied_mcp_tools": payload.get("denied_mcp_tools", []),
                "risk_policy": payload.get("risk_policy", {}),
                "source": "manual",
                "updated_at": utc_now_iso(),
            }
        )
        await self._repo.upsert_skill_policy(data)
        row = await self._repo.get_skill_policy(subject_type, subject_id)
        await self._audit.write_event(
            actor_type="system",
            action="member_skill_policy.updated",
            object_type=f"{subject_type}_skill_policy",
            object_id=subject_id,
            summary="成员能力策略已更新",
            risk_level=RiskLevel.R1,
            payload=payload,
        )
        return SkillPolicy(**row) if row else SkillPolicy(**data)

    async def _select_participants(
        self,
        task: dict[str, Any],
        host_member_id: str,
    ) -> list[dict[str, Any]]:
        members = {row["member_id"]: row for row in await self._members.list_members()}
        if host_member_id not in members:
            raise AppError(ErrorCode.PARTICIPANT_NOT_FOUND, "主持成员不存在", status_code=404)
        selected: list[dict[str, Any]] = [
            self._selection(
                members[host_member_id],
                "host",
                "主持人负责拆解、协调、评审和最终汇总。",
                "主持协作并收束最终结论",
            )
        ]
        goal = str(task["goal"]).lower()
        candidates = [
            ("mem_ningning", ["产品", "需求", "体验", "roadmap", "product"], "product_review"),
            (
                "mem_aheng",
                ["技术", "架构", "代码", "实现", "engineering", "architecture"],
                "architecture_review",
            ),
            ("mem_mobai", ["运营", "内容", "增长", "账号", "营销"], "operations_review"),
            ("mem_xiaoqi", ["生活", "日程", "家居", "陪伴"], "life_service_review"),
        ]
        for member_id, keywords, role_in_task in candidates:
            if member_id == host_member_id or member_id not in members:
                continue
            if any(keyword in goal for keyword in keywords):
                selected.append(
                    self._selection(
                        members[member_id],
                        role_in_task,
                        f"任务目标命中 {role_in_task} 相关关键词。",
                        self._objective_for_role(role_in_task, task["goal"]),
                    )
                )
        if len(selected) < 3:
            for member_id in ("mem_ningning", "mem_aheng"):
                if member_id != host_member_id and member_id in members:
                    if not any(item["member_id"] == member_id for item in selected):
                        selected.append(
                            self._selection(
                                members[member_id],
                                "cross_function_review",
                                "复杂任务默认需要产品和技术视角共同校验。",
                                self._objective_for_role("cross_function_review", task["goal"]),
                            )
                        )
                if len(selected) >= 3:
                    break
        available: list[dict[str, Any]] = []
        for item in selected:
            availability = await self._repo.get_availability(item["member_id"])
            if availability and availability["status"] in {"unavailable", "offline"}:
                continue
            available.append(item)
        return available

    def _selection(
        self,
        member: dict[str, Any],
        role_in_task: str,
        selection_reason: str,
        objective: str,
    ) -> dict[str, Any]:
        return {
            "member_id": member["member_id"],
            "role_in_task": role_in_task,
            "participant_type": "host" if role_in_task == "host" else "member",
            "selection_reason": selection_reason,
            "subtask_title": f"{member['display_name']}视角分析",
            "objective": objective,
        }

    def _context_scope(self, task: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_goal": str(redact(task["goal"])),
            "role_in_task": item["role_in_task"],
            "visible_memory_scope": "member_private_only",
            "shared_summary": "仅包含当前任务目标和主持人分配给该成员的子目标。",
            "allowed_context": ["task_goal", "own_member_profile", "shared_task_summary"],
            "excluded_context": [
                "other_members_private_memory",
                "all_assets",
                "secret_values",
                "local_sensitive_paths",
                "unrelated_conversations",
            ],
        }

    async def _run_subtask(
        self,
        task: dict[str, Any],
        plan: dict[str, Any],
        round_row: dict[str, Any],
        subtask: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> CollaborationOutput:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.SUBTASK_RUN,
            "run supervisor subtask",
            input_data={
                "task_id": task["task_id"],
                "subtask_id": subtask["subtask_id"],
                "assigned_member_id": subtask["assigned_member_id"],
            },
        )
        try:
            await self._repo.update_subtask(
                subtask["subtask_id"],
                {"status": "running", "updated_at": utc_now_iso()},
            )
            await self._event(
                task["task_id"],
                "subtask.started",
                {
                    "subtask_id": subtask["subtask_id"],
                    "member_id": subtask["assigned_member_id"],
                },
                trace_id=trace_id,
            )
            participant = await self._repo.get_participant(subtask["participant_id"])
            member = await self._members.get_member(subtask["assigned_member_id"])
            output_summary = {
                "status": "degraded_no_model",
                "member_id": subtask["assigned_member_id"],
                "objective": str(redact(subtask["objective"])),
                "result": _degraded_member_summary(member, subtask, task),
            }
            source_refs = [
                {
                    "subtask_id": subtask["subtask_id"],
                    "participant_id": subtask["participant_id"],
                    "member_id": subtask["assigned_member_id"],
                }
            ]
            output_id = new_id("out")
            await self._repo.insert_collaboration_output(
                {
                    "output_id": output_id,
                    "organization_id": task["organization_id"],
                    "task_id": task["task_id"],
                    "collaboration_plan_id": plan["collaboration_plan_id"],
                    "round_id": round_row["round_id"],
                    "subtask_id": subtask["subtask_id"],
                    "participant_id": subtask["participant_id"],
                    "member_id": subtask["assigned_member_id"],
                    "output_type": "degraded_summary",
                    "status": "completed",
                    "content_redacted": output_summary["result"],
                    "summary": output_summary,
                    "source_refs": source_refs,
                    "trace_id": trace_id,
                    "created_at": utc_now_iso(),
                }
            )
            await self._repo.update_subtask(
                subtask["subtask_id"],
                {
                    "status": "completed",
                    "output_summary": output_summary,
                    "source_refs": source_refs,
                    "updated_at": utc_now_iso(),
                    "completed_at": utc_now_iso(),
                },
            )
            if participant is not None:
                await self._repo.update_participant(
                    participant["participant_id"],
                    {
                        "status": "completed",
                        "output_summary": output_summary,
                        "updated_at": utc_now_iso(),
                    },
                )
            await self._event(
                task["task_id"],
                "subtask.completed",
                {
                    "subtask_id": subtask["subtask_id"],
                    "member_id": subtask["assigned_member_id"],
                    "output_id": output_id,
                    "status": "degraded_no_model",
                },
                trace_id=trace_id,
            )
            await self._audit.write_event(
                actor_type="system",
                action="subtask.completed",
                object_type="task_subtask",
                object_id=subtask["subtask_id"],
                summary="协作子任务已完成",
                risk_level=RiskLevel.R1,
                payload=output_summary,
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"output_id": output_id})
            return CollaborationOutput(
                output_id=output_id,
                organization_id=task["organization_id"],
                task_id=task["task_id"],
                collaboration_plan_id=plan["collaboration_plan_id"],
                round_id=round_row["round_id"],
                subtask_id=subtask["subtask_id"],
                participant_id=subtask["participant_id"],
                member_id=subtask["assigned_member_id"],
                output_type="degraded_summary",
                status="completed",
                content_redacted=output_summary["result"],
                summary=output_summary,
                source_refs=source_refs,
                trace_id=trace_id,
                created_at=utc_now_iso(),
            )
        except Exception as exc:
            await self._repo.update_subtask(
                subtask["subtask_id"],
                {
                    "status": "failed",
                    "error_code": getattr(exc, "code", ErrorCode.SUBTASK_RUN_FAILED.value),
                    "error_summary": str(redact(str(exc))),
                    "updated_at": utc_now_iso(),
                },
            )
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def _host_synthesis(
        self,
        task: dict[str, Any],
        plan: dict[str, Any],
        source_refs: list[dict[str, Any]],
        *,
        trace_id: str | None,
    ) -> HostDecision:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.HOST_SYNTHESIS,
            "host synthesis",
            input_data={"task_id": task["task_id"], "source_count": len(source_refs)},
        )
        safe_goal = str(redact(task["goal"]))
        summary = (
            f"已完成多成员协作汇总：围绕“{safe_goal}”收集了 "
            f"{len(source_refs)} 个成员输出。当前成员大脑未配置，结果以结构化占位摘要呈现。"
        )
        decision_id = new_id("hdec")
        data = {
            "decision_id": decision_id,
            "organization_id": task["organization_id"],
            "task_id": task["task_id"],
            "collaboration_plan_id": plan["collaboration_plan_id"],
            "host_member_id": plan["host_member_id"],
            "decision_type": "final_synthesis",
            "status": "completed",
            "summary": summary,
            "rationale": "主持人仅基于已写入的 collaboration_outputs 汇总，不伪造成员输出。",
            "source_refs": source_refs,
            "payload": {"degraded_no_model": True},
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_host_decision(data)
        await self._end_span(span_id, output_data={"decision_id": decision_id})
        return HostDecision(**data)

    async def _allowed_skills_for_member(self, member_id: str) -> list[str]:
        policy = await self._repo.get_skill_policy("member", member_id)
        return list(policy["allowed_skills"]) if policy else []

    async def _ensure_policy_subject(self, subject_type: str, subject_id: str) -> None:
        if subject_type not in {"member", "department", "role"}:
            raise AppError(ErrorCode.VALIDATION_ERROR, "策略主体类型不合法", status_code=422)
        if subject_type == "member" and await self._members.get_member(subject_id) is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)

    def _default_policy(self, subject_type: str, subject_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        return {
            "policy_id": f"msp_{subject_type}_{subject_id}",
            "organization_id": DEFAULT_ORGANIZATION_ID,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "allowed_skills": [],
            "denied_skills": [],
            "allowed_mcp_tools": [],
            "denied_mcp_tools": [],
            "risk_policy": {},
            "source": "manual",
            "created_at": now,
            "updated_at": now,
        }

    def _mode_for_goal(self, goal: str) -> str:
        lowered = goal.lower()
        if "辩论" in lowered or "debate" in lowered:
            return "debate"
        if "评审" in lowered or "review" in lowered:
            return "review"
        if "串行" in lowered or "serial" in lowered:
            return "serial"
        return "parallel"

    def _objective_for_role(self, role_in_task: str, goal: str) -> str:
        safe_goal = str(redact(goal))
        if "product" in role_in_task:
            return f"从需求、体验、边界和验收角度审视目标：{safe_goal}"
        if "architecture" in role_in_task:
            return f"从架构、实现风险、依赖和可维护性角度审视目标：{safe_goal}"
        if "operations" in role_in_task:
            return f"从运营、传播、账号和增长角度审视目标：{safe_goal}"
        if "life" in role_in_task:
            return f"从日程、生活服务和用户照护角度审视目标：{safe_goal}"
        return f"给出可被主持人汇总的专业建议：{safe_goal}"

    async def _get_supervisor_task(self, task_id: str) -> dict[str, Any]:
        task = await self._repo.get_task(task_id)
        if task is None:
            raise AppError(ErrorCode.NOT_FOUND, "任务不存在", status_code=404)
        if task["mode"] != TaskMode.SUPERVISOR.value:
            raise AppError(
                ErrorCode.SUPERVISOR_MODE_UNSUPPORTED,
                "只有 supervisor 任务支持多成员协作接口",
                status_code=409,
            )
        return task

    async def _task_detail(self, task_id: str) -> TaskDetail:
        if self._task_detail_provider is None:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "Task detail provider 未初始化",
                status_code=500,
            )
        return await self._task_detail_provider(task_id)

    async def _task_artifacts(self, task_id: str) -> list[TaskArtifact]:
        rows = await self._repo.list_artifacts(task_id)
        return [TaskArtifact(**row) for row in rows]

    async def _event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> None:
        await self._repo.insert_event(
            {
                "event_id": new_id("tevt"),
                "organization_id": DEFAULT_ORGANIZATION_ID,
                "task_id": task_id,
                "event_type": event_type,
                "payload": payload,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        input_data: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            input_data=input_data,
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                status=status,
                output_data=redact(output_data or {}),
            )


def _degraded_member_summary(
    member: dict[str, Any] | None,
    subtask: dict[str, Any],
    task: dict[str, Any],
) -> str:
    member_name = member["display_name"] if member else subtask["assigned_member_id"]
    objective = str(redact(subtask["objective"]))
    goal = str(redact(task["goal"]))
    return (
        f"{member_name} 的子任务以 degraded_no_model 完成：成员大脑尚未配置健康模型，"
        f"因此只记录目标、角色和待验证建议。子目标：{objective}。"
        f"原任务：{goal}。"
    )


def _collaboration_report(
    task: dict[str, Any],
    decision: HostDecision,
    source_refs: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {redact(task['title'])}",
        "",
        f"- 目标：{redact(task['goal'])}",
        "- 模式：supervisor",
        f"- 汇总：{decision.summary}",
        "",
        "## 来源",
    ]
    for ref in source_refs:
        lines.append(
            f"- output={ref['output_id']} member={ref['member_id']} subtask={ref['subtask_id']}"
        )
    return "\n".join(lines)


def _ensure_mutable_task(task: dict[str, Any]) -> None:
    if task["status"] in {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
        TaskStatus.ARCHIVED.value,
    }:
        raise AppError(
            ErrorCode.TASK_STATE_INVALID,
            "终态任务不能修改协作参与者或子任务",
            status_code=409,
        )


TaskDetailProvider = Callable[[str], Awaitable[TaskDetail]]
ExtensionReplayProvider = Callable[
    [str],
    Awaitable[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
]
