from __future__ import annotations

from typing import Any, Awaitable, Callable

from core_types import ApprovalDetail, ErrorCode, ExternalPlatformActionPlan
from trace_service import redact

from app.core.errors import AppError
from app.core.time import utc_now_iso

LOGIN_COMPLETED_MARKERS: tuple[str, ...] = (
    "已登录",
    "登录好了",
    "login completed",
    "logged in",
)


def adapter_type_for_execution_mode(execution_mode: str | None) -> str | None:
    mode = str(execution_mode or "").strip().lower()
    if mode == "mcp_adapter":
        return "mcp"
    if mode in {"browser", "mcp"}:
        return mode
    return None


class ExternalPlatformChatOrchestrator:
    def __init__(
        self,
        *,
        repo: Any,
        get_plan_by_approval_id: Callable[[str], Awaitable[ExternalPlatformActionPlan | None]],
        get_plan: Callable[[str], Awaitable[Any]],
        plan_event: Callable[..., Awaitable[None]],
    ) -> None:
        self._repo = repo
        self._get_plan_by_approval_id = get_plan_by_approval_id
        self._get_plan = get_plan
        self._plan_event = plan_event

    async def find_chat_resumable_plan(
        self,
        *,
        conversation_id: str,
    ) -> tuple[ExternalPlatformActionPlan | None, str]:
        rows = await self._repo.list_recent_plans(
            conversation_id=conversation_id,
            statuses=["awaiting_human", "awaiting_approval"],
            limit=5,
        )
        plans = [ExternalPlatformActionPlan(**row) for row in rows]
        if not plans:
            return None, "none"
        pending = [
            plan
            for plan in plans
            if plan.status == "awaiting_human"
            or (plan.status == "awaiting_approval" and plan.approval_id)
        ]
        if len(pending) != 1:
            return None, "multiple"
        return pending[0], "single"

    async def continue_after_approval(
        self,
        approval: ApprovalDetail,
        *,
        adapter_service: Any | None = None,
        trace_id: str | None = None,
    ) -> Any | None:
        if not str(approval.requested_action or "").startswith("external_platform."):
            return None
        plan = await self._get_plan_by_approval_id(approval.approval_id)
        if plan is None:
            return None
        if approval.status == "denied":
            await self._repo.update_plan(
                plan.plan_id,
                {
                    "status": "cancelled",
                    "failure_reason": "approval_denied",
                    "updated_at": utc_now_iso(),
                },
            )
            await self._plan_event(
                plan.plan_id,
                "plan.cancelled",
                {"reason": "approval_denied"},
                trace_id=trace_id,
            )
            return await self._get_plan(plan.plan_id)
        if approval.status not in {"approved", "edited"}:
            return await self._get_plan(plan.plan_id)
        adapter_type = adapter_type_for_execution_mode(plan.execution_mode)
        if adapter_service is not None and adapter_type is not None:
            from app.schemas.external_platform_adapters import ExternalPlatformAdapterExecuteRequest

            return await adapter_service.execute_adapter(
                plan.plan_id,
                ExternalPlatformAdapterExecuteRequest(
                    adapter_type=adapter_type,
                    approval_id=plan.approval_id,
                    force=True,
                ),
                trace_id=trace_id,
            )
        return await self._get_plan(plan.plan_id)

    async def resume_from_chat(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        text: str,
        adapter_service: Any | None,
        trace_id: str | None = None,
    ) -> Any | None:
        if plan.status == "awaiting_approval" and plan.approval_id:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "当前还在等你确认，先明确同意或拒绝这项外部平台操作。",
                status_code=409,
            )
        if adapter_service is None:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "外部平台恢复执行服务暂时不可用。",
                status_code=500,
            )
        from app.schemas.external_platform_adapters import ExternalPlatformAdapterResumeRequest

        normalized = str(text or "").strip().lower()
        login_completed = any(marker in str(text or "") for marker in LOGIN_COMPLETED_MARKERS)
        if "resume_after_login" in str(plan.metadata.get("chat_next_step") or ""):
            login_completed = True
        return await adapter_service.resume_after_human(
            plan.plan_id,
            ExternalPlatformAdapterResumeRequest(
                adapter_type=adapter_type_for_execution_mode(plan.execution_mode),
                approval_id=plan.approval_id,
                human_resolution={
                    "login_completed": login_completed,
                    "chat_resume": True,
                    "reply_text": str(redact(text)),
                    "normalized": normalized[:80],
                },
            ),
            trace_id=trace_id,
        )
