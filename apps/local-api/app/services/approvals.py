from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from core_types import ApprovalDetail, ErrorCode, RiskLevel, TraceSpanStatus, TraceSpanType
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.task_repo import TaskRepository
from app.services.audit import AuditEventService


class ApprovalService:
    def __init__(
        self,
        *,
        repo: TaskRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._trace = trace_service
        self._audit = audit_service
        self._notification_callback: (
            Callable[[ApprovalDetail], Awaitable[Any]] | None
        ) = None

    def set_notification_callback(
        self,
        callback: Callable[[ApprovalDetail], Awaitable[Any]],
    ) -> None:
        self._notification_callback = callback

    async def create_approval(
        self,
        *,
        task_id: str,
        organization_id: str,
        requested_action: str,
        risk_level: RiskLevel,
        summary: str,
        payload: dict[str, Any],
        step_id: str | None = None,
        tool_call_id: str | None = None,
        trace_id: str | None = None,
    ) -> ApprovalDetail:
        existing_task = await self._repo.get_task(task_id)
        if existing_task is None:
            raise AppError(ErrorCode.NOT_FOUND, "任务不存在", status_code=404)
        approval_id = new_id("apr")
        now = utc_now_iso()
        expires_at = (utc_now() + timedelta(hours=2)).isoformat()
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.APPROVAL_CREATE,
            "create approval request",
            metadata={"approval_id": approval_id, "task_id": task_id, "action": requested_action},
        )
        data = {
            "approval_id": approval_id,
            "organization_id": organization_id,
            "task_id": task_id,
            "step_id": step_id,
            "tool_call_id": tool_call_id,
            "approval_type": "action",
            "requested_action": requested_action,
            "risk_level": risk_level.value,
            "summary": summary,
            "payload_redacted": redact(payload),
            "options": ["approve", "deny", "edit"],
            "status": "pending",
            "expires_at": expires_at,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_approval(data)
        await self._repo.insert_approval_event(
            {
                "event_id": new_id("aprevt"),
                "organization_id": organization_id,
                "approval_id": approval_id,
                "event_type": "created",
                "actor_type": "system",
                "actor_id": None,
                "payload": {"requested_action": requested_action, "risk_level": risk_level.value},
                "payload_redacted": {
                    "requested_action": requested_action,
                    "risk_level": risk_level.value,
                },
                "created_at": now,
            }
        )
        await self._repo.insert_event(
            {
                "event_id": new_id("tevt"),
                "organization_id": organization_id,
                "task_id": task_id,
                "step_id": step_id,
                "event_type": "approval.required",
                "payload": {
                    "approval_id": approval_id,
                    "summary": summary,
                    "risk_level": risk_level.value,
                },
                "payload_redacted": {
                    "approval_id": approval_id,
                    "summary": summary,
                    "risk_level": risk_level.value,
                },
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        await self._audit.write_event(
            actor_type="system",
            action="approval.created",
            object_type="approval",
            object_id=approval_id,
            summary="审批请求已创建",
            risk_level=risk_level,
            payload={"approval_id": approval_id, "requested_action": requested_action},
            trace_id=trace_id,
        )
        await self._end_span(span_id, output_data={"approval_id": approval_id})
        approval = ApprovalDetail(**data)
        if self._notification_callback is not None:
            try:
                await self._notification_callback(approval)
            except Exception:
                await self._audit.write_event(
                    actor_type="system",
                    action="notification.approval_callback_failed",
                    object_type="approval",
                    object_id=approval_id,
                    summary="审批通知创建失败，审批本身保持有效",
                    risk_level=RiskLevel.R1,
                    payload={"approval_id": approval_id},
                    trace_id=trace_id,
                )
        return approval

    async def get(self, approval_id: str) -> ApprovalDetail:
        row = await self._repo.get_approval(approval_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "审批不存在", status_code=404)
        return ApprovalDetail(**row)

    async def approve(
        self,
        approval_id: str,
        *,
        actor_type: str,
        actor_id: str | None,
        reason: str | None,
        trace_id: str | None,
    ) -> ApprovalDetail:
        return await self._resolve(
            approval_id,
            status="approved",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            edited_payload=None,
            trace_id=trace_id,
        )

    async def deny(
        self,
        approval_id: str,
        *,
        actor_type: str,
        actor_id: str | None,
        reason: str | None,
        trace_id: str | None,
    ) -> ApprovalDetail:
        return await self._resolve(
            approval_id,
            status="denied",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            edited_payload=None,
            trace_id=trace_id,
        )

    async def edit(
        self,
        approval_id: str,
        *,
        actor_type: str,
        actor_id: str | None,
        reason: str | None,
        edited_payload: dict[str, Any],
        trace_id: str | None,
    ) -> ApprovalDetail:
        return await self._resolve(
            approval_id,
            status="edited",
            actor_type=actor_type,
            actor_id=actor_id,
            reason=reason,
            edited_payload=edited_payload,
            trace_id=trace_id,
        )

    async def _resolve(
        self,
        approval_id: str,
        *,
        status: str,
        actor_type: str,
        actor_id: str | None,
        reason: str | None,
        edited_payload: dict[str, Any] | None,
        trace_id: str | None,
    ) -> ApprovalDetail:
        approval = await self.get(approval_id)
        if approval.status != "pending":
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "审批已处理，不能重复决策",
                status_code=409,
                details={"status": approval.status},
            )
        if approval.expires_at and approval.expires_at.isoformat() <= utc_now_iso():
            await self._repo.update_approval(
                approval_id,
                {"status": "expired", "updated_at": utc_now_iso(), "resolved_at": utc_now_iso()},
            )
            raise AppError(ErrorCode.APPROVAL_EXPIRED, "审批已过期", status_code=409)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.APPROVAL_RESOLVE,
            "resolve approval",
            metadata={"approval_id": approval_id, "status": status},
        )
        now = utc_now_iso()
        await self._repo.update_approval(
            approval_id,
            {
                "status": status,
                "decision_reason": reason,
                "edited_payload": redact(edited_payload) if edited_payload is not None else None,
                "updated_at": now,
                "resolved_at": now,
            },
        )
        await self._repo.insert_approval_event(
            {
                "event_id": new_id("aprevt"),
                "organization_id": approval.organization_id,
                "approval_id": approval_id,
                "event_type": status,
                "actor_type": actor_type,
                "actor_id": actor_id,
                "payload": {"reason": reason, "edited_payload": edited_payload},
                "payload_redacted": redact({"reason": reason, "edited_payload": edited_payload}),
                "created_at": now,
            }
        )
        await self._repo.insert_event(
            {
                "event_id": new_id("tevt"),
                "organization_id": approval.organization_id,
                "task_id": approval.task_id,
                "step_id": approval.step_id,
                "event_type": "approval.resolved",
                "payload": {"approval_id": approval_id, "status": status},
                "payload_redacted": {"approval_id": approval_id, "status": status},
                "trace_id": trace_id,
                "created_at": now,
            }
        )
        await self._audit.write_event(
            actor_type=actor_type,
            actor_id=actor_id,
            action=f"approval.{status}",
            object_type="approval",
            object_id=approval_id,
            summary=f"审批已{status}",
            risk_level=approval.risk_level,
            payload={"approval_id": approval_id, "status": status, "reason": reason},
            trace_id=trace_id,
        )
        await self._end_span(span_id, output_data={"approval_id": approval_id, "status": status})
        return await self.get(approval_id)

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        metadata: dict[str, Any],
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            metadata=metadata,
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
