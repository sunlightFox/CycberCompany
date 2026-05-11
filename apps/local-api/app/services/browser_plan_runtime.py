from __future__ import annotations

from typing import Any

from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.schemas.browser_workflows import BrowserWorkflowPlanCreateRequest, BrowserWorkflowPlanResponse
from app.schemas.tasks import TaskCreateRequest
from app.services.browser_workflows import _host, _normalize_action_type, _risk_for_action
from core_types import ErrorCode, TaskMode


class BrowserPlanRuntime:
    def __init__(self, *, repo: Any, task_engine: Any, task_repo: Any, response_builder: Any) -> None:
        self._repo = repo
        self._tasks = task_engine
        self._task_repo = task_repo
        self._response = response_builder

    async def create_plan(
        self,
        request: BrowserWorkflowPlanCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowPlanResponse:
        intent = await self._repo.get_intent(request.intent_id)
        if intent is None:
            raise AppError(ErrorCode.NOT_FOUND, "浏览器工作流意图不存在", status_code=404)
        now = utc_now_iso()
        action_type = _normalize_action_type(request.action_type) or intent["action_type"]
        target_url = request.target_url or intent.get("target_url")
        target_key = _host(target_url) if target_url else intent.get("target_key")
        status = "awaiting_intent_clarification" if intent["status"] != "resolved" else "planned"
        merged_constraints = {
            **dict(intent.get("constraints") or {}),
            **dict(request.constraints or {}),
        }
        task_id = None
        if status == "planned":
            task = await self._tasks.create_task(
                TaskCreateRequest(
                    conversation_id=intent.get("conversation_id"),
                    owner_member_id=str(intent.get("member_id") or "mem_xiaoyao"),
                    goal=request.goal
                    or intent["natural_language_goal"]
                    or f"Autonomous browser workflow: {action_type}",
                    mode_hint=TaskMode.WORKFLOW,
                    success_criteria=[
                        "observe target page",
                        "execute low risk browser actions",
                        "stop before approval boundary",
                        "record replay evidence",
                    ],
                    constraints={
                        "phase": "autonomous_browser_workflow",
                        "target_url": target_url,
                        "action_type": action_type,
                    },
                    auto_start=False,
                ),
                trace_id=trace_id,
            )
            task_id = task.task_id
        plan_data = {
            "plan_id": new_id("bwplan"),
            "intent_id": request.intent_id,
            "organization_id": intent.get("organization_id") or "org_default",
            "member_id": intent.get("member_id") or "mem_xiaoyao",
            "conversation_id": intent.get("conversation_id"),
            "task_id": task_id,
            "trace_id": trace_id,
            "action_type": action_type,
            "target_url": target_url,
            "target_key": target_key,
            "goal": request.goal or intent["natural_language_goal"],
            "status": status,
            "risk_level": _risk_for_action(action_type),
            "current_url": target_url,
            "content_summary": request.content_summary or intent.get("content_summary"),
            "form_data": redact(request.form_data),
            "file_refs": redact(request.file_refs),
            "steps": [{"step_type": "observe", "status": "planned"}],
            "approval_binding": {},
            "evidence": {},
            "metadata": redact(
                {
                    "source": "browser_plan_runtime",
                    "max_steps": request.max_steps,
                    "constraints": merged_constraints,
                    "session_handle_id": merged_constraints.get("session_handle_id"),
                    "browser_session_handle_id": merged_constraints.get(
                        "browser_session_handle_id"
                    ),
                    "observe_act_contract": {
                        "observe_tools": ["browser.snapshot", "browser.open", "browser.extract"],
                        "act_tools": ["browser.click", "browser.submit", "browser.upload", "browser.download"],
                    },
                }
            ),
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_plan(plan_data)
        await self._repo.insert_event(
            {
                "event_id": new_id("bwevt"),
                "plan_id": plan_data["plan_id"],
                "organization_id": plan_data["organization_id"],
                "execution_id": None,
                "event_type": "plan.created",
                "payload_redacted": {"status": status, "action_type": action_type},
                "evidence_refs": [],
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )
        return await self._response(
            plan_data["plan_id"],
            message=(
                "已创建通用浏览器工作流计划。"
                if status == "planned"
                else "还缺少目标信息，暂不进入浏览器探索。"
            ),
            next_step="execute" if status == "planned" else "resolve_missing_fields",
        )
