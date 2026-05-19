from __future__ import annotations

from app.schemas.browser_research import BrowserResearchPlan, SearchRequest, SearchResult
from app.services.browser_research_assessor import BrowserResearchAssessor
from app.services.browser_research_renderer import BrowserResearchRenderer
from app.services.browser_search_capability import BrowserSearchCapabilityAdapter


def test_phase117_browser_search_capability_maps_browser_search_tool() -> None:
    captured: dict[str, object] = {}

    class _ToolRuntime:
        async def execute(self, request, trace_id=None):  # noqa: ANN001,ANN202
            captured["tool_name"] = request.tool_name
            captured["args"] = dict(request.args)
            captured["trace_id"] = trace_id
            return type(
                "ToolResponse",
                (),
                {
                    "result": {
                        "title": "Search Results",
                        "url": "https://example.test/search?q=phase117",
                        "http_status": 200,
                        "browser_evidence_id": "bev_phase117",
                        "content_preview": "<html><body><li>phase117 result</li></body></html>",
                    },
                    "tool_call": type("ToolCall", (), {"tool_call_id": "call_phase117"})(),
                },
            )()

    adapter = BrowserSearchCapabilityAdapter(tool_runtime=_ToolRuntime())
    result = _run(
        adapter.search(
            SearchRequest(
                member_id="mem_xiaoyao",
                turn_id="turn_phase117",
                trace_id="trc_phase117",
                query="phase117",
            )
        )
    )

    assert captured["tool_name"] == "browser.search"
    assert captured["args"] == {"query": "phase117"}
    assert captured["trace_id"] == "trc_phase117"
    assert result.raw_evidence[0].snippet == "phase117 result"


def test_phase117_assessor_structures_timeliness_and_conflict() -> None:
    assessor = BrowserResearchAssessor()
    assessment = assessor.assess(
        plan=BrowserResearchPlan(
            query="最新门诊安排",
            citation_required=True,
            requested_sections=["主要变化", "需要注意", "出发前确认"],
        ),
        search_result=SearchResult(
            title="最新门诊安排 搜索结果",
            url="https://example.test/search?q=latest+clinic+schedule",
            raw_evidence=[
                {"snippet": "有的资料写现场取号即可"},
                {"snippet": "也有资料写必须先线上预约"},
            ],
        ),
    )

    assert assessment.freshness == "time_sensitive"
    assert assessment.conflict_level == "minor"
    assert assessment.confidence == "cautious"


def test_phase117_renderer_supports_structured_popular_explainer() -> None:
    renderer = BrowserResearchRenderer()
    text = renderer.render(
        plan=BrowserResearchPlan(
            query="海盐为什么要加碘",
            citation_required=True,
            requested_sections=["核心结论", "常见误区", "怎么理解"],
            presentation_style="popular_explainer",
        ),
        search_result=SearchResult(
            title="海盐加碘 搜索结果",
            url="https://example.test/search?q=iodized+salt",
            http_status=200,
            raw_evidence=[
                {"snippet": "核心还是补碘，帮助减少碘缺乏带来的健康问题"},
                {"snippet": "并不是越贵越好，关键看是否符合日常食用需求"},
                {"snippet": "如果日常饮食已经很均衡，也要结合地区和个人情况理解"},
            ],
        ),
        assessment=BrowserResearchAssessor().assess(
            plan=BrowserResearchPlan(
                query="海盐为什么要加碘",
                citation_required=True,
                requested_sections=["核心结论", "常见误区", "怎么理解"],
                presentation_style="popular_explainer",
            ),
            search_result=SearchResult(
                title="海盐加碘 搜索结果",
                url="https://example.test/search?q=iodized+salt",
                raw_evidence=[
                    {"snippet": "核心还是补碘，帮助减少碘缺乏带来的健康问题"},
                    {"snippet": "并不是越贵越好，关键看是否符合日常食用需求"},
                    {"snippet": "如果日常饮食已经很均衡，也要结合地区和个人情况理解"},
                ],
            ),
        ),
    )

    assert "先给一个背景提醒" in text
    assert "核心结论：" in text
    assert "这次总结的证据来源是：" in text


def _run(awaitable):  # noqa: ANN001,ANN201
    import asyncio

    return asyncio.run(awaitable)
