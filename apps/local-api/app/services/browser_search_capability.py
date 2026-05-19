from __future__ import annotations

import re
from typing import Any, Protocol

from app.core.errors import AppError
from app.schemas.browser_research import RawEvidence, SearchRequest, SearchResult
from app.schemas.tasks import ToolExecuteRequest


class SearchCapability(Protocol):
    async def search(self, request: SearchRequest) -> SearchResult: ...


class BrowserSearchCapabilityAdapter:
    def __init__(self, *, tool_runtime: Any | None) -> None:
        self._tool_runtime = tool_runtime

    async def search(self, request: SearchRequest) -> SearchResult:
        if self._tool_runtime is None:
            raise AppError("browser_tool_unavailable", "当前没有可用的浏览器工具，所以这次没有执行搜索。")

        response = await self._tool_runtime.execute(
            ToolExecuteRequest(
                member_id=request.member_id,
                tool_name="browser.search",
                args={"query": request.query},
                idempotency_key=f"chat:{request.turn_id}:browser.search",
            ),
            trace_id=request.trace_id,
        )

        result = dict(response.result)
        title = str(result.get("title") or "未识别标题")
        url = str(result.get("url") or "")
        content = str(result.get("content_preview") or result.get("snapshot") or "")
        evidence_id = str(result.get("browser_evidence_id") or "")
        evidence_refs: list[dict[str, Any]] = []
        if evidence_id:
            evidence_refs.append(
                {
                    "type": "browser_evidence",
                    "browser_evidence_id": evidence_id,
                    "source": "browser.search",
                    "url": url,
                    "title": title,
                }
            )

        return SearchResult(
            title=title,
            url=url,
            http_status=result.get("http_status"),
            content_preview=content,
            raw_result=result,
            evidence_refs=evidence_refs,
            raw_evidence=_extract_raw_evidence(content=content, title=title, url=url),
            tool_call_id=str(getattr(response.tool_call, "tool_call_id", "") or ""),
        )

    def diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "browser_search_capability",
            "plane": "capability_plane",
            "owner": "browser_search_capability",
            "contract_version": "phase117.browser_search_capability.v1",
            "contract_stages": ["plan", "authorize", "execute", "summarize", "emit_evidence"],
            "delegates_to": ["tool_runtime.execute"],
            "tool_name": "browser.search",
        }


def _extract_raw_evidence(*, content: str, title: str, url: str) -> list[RawEvidence]:
    clean = re.sub(r"\s+", " ", content or "").strip()
    if not clean:
        return []
    snippets: list[RawEvidence] = []
    pattern = (
        r"(?:<li\b[^>]*>|<h2\b[^>]*>|<h3\b[^>]*>|class=\"b_algo\")"
        r"(.*?)(?:</li>|</h2>|</h3>)"
    )
    for match in re.finditer(pattern, content, flags=re.IGNORECASE | re.DOTALL):
        snippet = re.sub(r"<[^>]+>", " ", match.group(1))
        snippet = re.sub(r"\s+", " ", snippet).strip()
        snippet = snippet.replace("证件有效期", "有效期")
        if not snippet:
            continue
        if any(existing.snippet == snippet[:220] for existing in snippets):
            continue
        snippets.append(
            RawEvidence(
                snippet=snippet[:220],
                source_url=url or None,
                source_title=title or None,
            )
        )
        if len(snippets) >= 3:
            break
    if snippets:
        return snippets

    fallback = re.sub(r"<[^>]+>", " ", content)
    fallback = re.sub(r"\s+", " ", fallback).strip().replace("证件有效期", "有效期")
    if not fallback:
        return []
    return [RawEvidence(snippet=fallback[:220], source_url=url or None, source_title=title or None)]
