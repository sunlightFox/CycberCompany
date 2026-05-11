from __future__ import annotations

from typing import Any

from app.schemas.browser_workflows import (
    BrowserWorkflowExecuteRequest,
    BrowserWorkflowIntentResolveRequest,
    BrowserWorkflowIntentResolveResponse,
    BrowserWorkflowPlanCreateRequest,
    BrowserWorkflowPlanResponse,
    BrowserWorkflowReplayResponse,
    BrowserWorkflowResumeRequest,
)


class BrowserWorkflowRuntime:
    def __init__(
        self,
        *,
        legacy_service: Any,
        intent_resolver: Any,
        plan_runtime: Any,
        replay_store: Any,
    ) -> None:
        self._legacy = legacy_service
        self._intent = intent_resolver
        self._plan = plan_runtime
        self._replay = replay_store

    async def resolve_intent(
        self,
        request: BrowserWorkflowIntentResolveRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowIntentResolveResponse:
        return await self._intent.resolve_intent(request, trace_id=trace_id)

    async def create_plan(
        self,
        request: BrowserWorkflowPlanCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowPlanResponse:
        return await self._legacy.create_plan(request, trace_id=trace_id)

    async def get_plan(self, plan_id: str) -> BrowserWorkflowPlanResponse:
        return await self._legacy.get_plan(plan_id)

    async def execute_plan(
        self,
        plan_id: str,
        request: BrowserWorkflowExecuteRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowPlanResponse:
        return await self._legacy.execute_plan(plan_id, request, trace_id=trace_id)

    async def resume_after_human(
        self,
        plan_id: str,
        request: BrowserWorkflowResumeRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowPlanResponse:
        return await self._legacy.resume_after_human(plan_id, request, trace_id=trace_id)

    async def replay(self, plan_id: str) -> BrowserWorkflowReplayResponse:
        base = await self._legacy.replay(plan_id)
        return await self._replay.replay_bundle(plan_id, base_response=base)

    def diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "browser_workflow_runtime",
            "delegates_execute_to": "autonomous_browser_workflow_service",
            "intent_runtime": "browser_intent_resolver",
            "plan_runtime": "browser_plan_runtime",
            "create_plan_mode": "compat_bridge",
            "session_runtime": "browser_session_runtime",
            "page_state_runtime": "browser_page_state_runtime",
            "replay_store": "browser_replay_store",
            "maturity": "runtime_native",
            "observe_act_split": True,
        }
