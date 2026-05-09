from __future__ import annotations

import re
from typing import Any

from app.core.errors import AppError
from app.schemas.chat_routes import BrowserWorkflowResult
from app.schemas.tasks import ToolExecuteRequest


class ChatReadonlyExecutionService:
    def __init__(self, *, tool_runtime: Any | None) -> None:
        self._tool_runtime = tool_runtime

    async def browser_search(
        self,
        *,
        member_id: str,
        turn_id: str,
        trace_id: str | None,
        query: str,
        require_citation: bool,
    ) -> BrowserWorkflowResult:
        if self._tool_runtime is None:
            return BrowserWorkflowResult(
                status="blocked",
                visible_summary="当前没有可用的浏览器工具，所以这次没有执行搜索。",
                failure_code="browser_tool_unavailable",
                metadata={"route": "browser_search_readonly"},
            )

        try:
            response = await self._tool_runtime.execute(
                ToolExecuteRequest(
                    member_id=member_id,
                    tool_name="browser.search",
                    args={"query": query},
                    idempotency_key=f"chat:{turn_id}:browser.search",
                ),
                trace_id=trace_id,
            )
        except AppError as exc:
            return BrowserWorkflowResult(
                status="blocked",
                visible_summary=f"我尝试用浏览器搜索，但这次搜索没有成功：{exc.message}",
                failure_code=str(exc.code),
                metadata={
                    "route": "browser_search_readonly",
                    "error_details": exc.details or {},
                },
            )

        result = dict(response.result)
        content = str(result.get("content_preview") or result.get("snapshot") or "")
        snippets = _search_result_snippets(content)
        evidence_refs = []
        evidence_id = str(result.get("browser_evidence_id") or "")
        if evidence_id:
            evidence_refs.append(
                {
                    "type": "browser_evidence",
                    "browser_evidence_id": evidence_id,
                    "source": "browser.search",
                    "url": result.get("url"),
                    "title": result.get("title"),
                }
            )
        summary = _browser_search_summary(
            query=query,
            result=result,
            snippets=snippets,
            require_citation=require_citation,
        )
        return BrowserWorkflowResult(
            status="completed",
            visible_summary=summary,
            evidence_refs=evidence_refs,
            tool_calls=[
                {
                    "tool_name": "browser.search",
                    "tool_call_id": response.tool_call.tool_call_id,
                }
            ],
            metadata={
                "route": (
                    "browser_search_with_citation"
                    if require_citation
                    else "browser_search_readonly"
                ),
                "query": query,
                "result_count_hint": len(snippets),
                "search_url": result.get("url"),
                "search_title": result.get("title"),
            },
        )


def _search_result_snippets(content: str) -> list[str]:
    clean = re.sub(r"\s+", " ", content or "").strip()
    if not clean:
        return []
    snippets: list[str] = []
    pattern = (
        r"(?:<li\b[^>]*>|<h2\b[^>]*>|<h3\b[^>]*>|class=\"b_algo\")"
        r"(.*?)(?:</li>|</h2>|</h3>)"
    )
    for match in re.finditer(
        pattern,
        content,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        snippet = re.sub(r"<[^>]+>", " ", match.group(1))
        snippet = re.sub(r"\s+", " ", snippet).strip()
        if snippet and snippet not in snippets:
            snippets.append(snippet[:220])
        if len(snippets) >= 3:
            break
    if snippets:
        return snippets
    fallback = re.sub(r"<[^>]+>", " ", content)
    fallback = re.sub(r"\s+", " ", fallback).strip()
    if not fallback:
        return []
    return [fallback[:220]]


def _browser_search_summary(
    *,
    query: str,
    result: dict[str, Any],
    snippets: list[str],
    require_citation: bool,
) -> str:
    title = str(result.get("title") or "未识别标题")
    status = str(result.get("http_status") or "未知")
    url = str(result.get("url") or "")
    if snippets:
        joined = "；".join(snippets[:3])
        summary = (
            f"我已经用浏览器搜索了“{query}”。当前拿到的证据来自搜索结果页《{title}》"
            f"（HTTP {status}）。从结果页可见内容看，相关线索包括：{joined}"
        )
    else:
        summary = (
            f"我已经用浏览器搜索了“{query}”。当前证据来自搜索结果页《{title}》"
            f"（HTTP {status}），但这页没有稳定提取出足够清晰的结果摘要。"
        )
    if require_citation:
        source = url or "browser.search 返回的搜索结果页"
        summary += f"。这次总结的证据来源是：{source}"
    return summary
