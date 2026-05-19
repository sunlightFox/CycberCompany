from __future__ import annotations

from app.core.errors import AppError
from app.schemas.browser_research import BrowserResearchPlan, CapabilityExecutionResult, SearchRequest
from app.schemas.chat_routes import BrowserWorkflowResult
from app.services.browser_research_assessor import BrowserResearchAssessor
from app.services.browser_research_renderer import BrowserResearchRenderer
from app.services.browser_search_capability import SearchCapability


class BrowserResearchRuntime:
    def __init__(
        self,
        *,
        search_capability: SearchCapability,
        assessor: BrowserResearchAssessor,
        renderer: BrowserResearchRenderer,
    ) -> None:
        self._search_capability = search_capability
        self._assessor = assessor
        self._renderer = renderer

    async def execute(
        self,
        *,
        member_id: str,
        turn_id: str,
        trace_id: str | None,
        plan: BrowserResearchPlan,
    ) -> BrowserWorkflowResult:
        try:
            search_result = await self._search_capability.search(
                SearchRequest(
                    member_id=member_id,
                    turn_id=turn_id,
                    trace_id=trace_id,
                    query=plan.query,
                )
            )
        except AppError as exc:
            return BrowserWorkflowResult(
                status="blocked",
                visible_summary=f"我尝试用浏览器搜索，但这次搜索没有成功：{exc.message}",
                failure_code=str(exc.code),
                browser_research_plan=plan,
                metadata={
                    "route": "browser_search_readonly",
                    "error_details": exc.details or {},
                },
            )

        assessment = self._assessor.assess(plan=plan, search_result=search_result)
        summary = self._renderer.render(
            plan=plan,
            search_result=search_result,
            assessment=assessment,
        )
        capability_result = CapabilityExecutionResult(
            capability_name="browser_research",
            plan={
                "query": plan.query,
                "citation_required": plan.citation_required,
                "requested_sections": list(plan.requested_sections),
                "presentation_style": plan.presentation_style,
            },
            authorize={
                "status": "not_required",
                "reason": "readonly_browser_search",
            },
            execute={
                "status": "completed",
                "tool_name": search_result.tool_name,
                "tool_call_id": search_result.tool_call_id,
                "search_url": search_result.url,
            },
            summarize={
                "status": "completed",
                "visible_summary": summary,
                "assessment": assessment.model_dump(mode="json"),
            },
            emit_evidence={
                "status": "emitted" if search_result.evidence_refs else "none",
                "evidence_ref_count": len(search_result.evidence_refs),
            },
        )
        return BrowserWorkflowResult(
            status="completed",
            visible_summary=summary,
            evidence_refs=search_result.evidence_refs,
            tool_calls=(
                [
                    {
                        "tool_name": search_result.tool_name,
                        "tool_call_id": search_result.tool_call_id,
                    }
                ]
                if search_result.tool_call_id
                else []
            ),
            assessment=assessment,
            browser_research_plan=plan,
            metadata={
                "route": (
                    "browser_search_with_citation"
                    if plan.citation_required
                    else "browser_search_readonly"
                ),
                "query": plan.query,
                "result_count_hint": len(search_result.raw_evidence),
                "search_url": search_result.url,
                "search_title": search_result.title,
                "capability_contract": capability_result.model_dump(mode="json"),
                "approval_status": "not_required",
                "evidence_status": "present" if search_result.evidence_refs else "not_emitted",
            },
        )

    def runtime_diagnostic(self) -> dict[str, object]:
        return {
            "runtime": "browser_research_runtime",
            "plane": "capability_plane",
            "owner": "browser_research_runtime",
            "contract_version": "phase117.browser_research_runtime.v1",
            "contract_stages": ["plan", "authorize", "execute", "summarize", "emit_evidence"],
            "delegates_to": [
                "browser_search_capability",
                "browser_research_assessor",
                "browser_research_renderer",
            ],
        }
