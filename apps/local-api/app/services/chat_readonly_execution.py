from __future__ import annotations

from app.schemas.browser_research import BrowserResearchPlan
from app.schemas.chat_routes import BrowserWorkflowResult
from app.services.browser_research_assessor import BrowserResearchAssessor
from app.services.browser_research_renderer import BrowserResearchRenderer
from app.services.browser_research_runtime import BrowserResearchRuntime
from app.services.browser_search_capability import BrowserSearchCapabilityAdapter


class ChatReadonlyExecutionService:
    """Compatibility facade for readonly chat routes."""

    def __init__(self, *, tool_runtime: object | None) -> None:
        capability = BrowserSearchCapabilityAdapter(tool_runtime=tool_runtime)
        self._browser_research = BrowserResearchRuntime(
            search_capability=capability,
            assessor=BrowserResearchAssessor(),
            renderer=BrowserResearchRenderer(),
        )

    async def browser_search(
        self,
        *,
        member_id: str,
        turn_id: str,
        trace_id: str | None,
        query: str,
        require_citation: bool,
        requested_sections: list[str] | None = None,
        presentation_style: str = "default",
    ) -> BrowserWorkflowResult:
        return await self._browser_research.execute(
            member_id=member_id,
            turn_id=turn_id,
            trace_id=trace_id,
            plan=BrowserResearchPlan(
                query=query,
                citation_required=require_citation,
                requested_sections=list(requested_sections or []),
                presentation_style=presentation_style,
            ),
        )
