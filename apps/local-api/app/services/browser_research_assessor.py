from __future__ import annotations

from urllib.parse import urlparse

from app.schemas.browser_research import BrowserResearchPlan, EvidenceAssessment, SearchResult


class BrowserResearchAssessor:
    def assess(
        self,
        *,
        plan: BrowserResearchPlan,
        search_result: SearchResult,
    ) -> EvidenceAssessment:
        snippets = [item.snippet for item in search_result.raw_evidence]
        query_is_time_sensitive = _browser_search_is_time_sensitive(plan.query)
        snippets_have_conflict = _browser_search_has_conflicting_signals(snippets)
        source_rank = _browser_search_source_rank(url=search_result.url, title=search_result.title)
        confidence = _browser_search_confidence(
            source_rank=source_rank,
            query_is_time_sensitive=query_is_time_sensitive,
            snippets_have_conflict=snippets_have_conflict,
        )
        notes: list[str] = []
        if source_rank == "official":
            notes.append("来源判断：当前拿到的是偏官方口径的结果页，可以优先参考。")
        elif source_rank == "community":
            notes.append("来源判断：当前更像是整理页或社区口径，适合先当线索，再回到官方页核对。")
        if snippets_have_conflict:
            notes.append("不同线索之间存在一些口径差异，最终判断更适合以权威来源为准。")
        if query_is_time_sensitive:
            notes.append("这类信息有明显时效性，真正采用前建议再核对一次最新页面。")
        return EvidenceAssessment(
            freshness="time_sensitive" if query_is_time_sensitive else "stable",
            source_rank=source_rank,
            conflict_level="minor" if snippets_have_conflict else "none",
            confidence=confidence,
            notes=notes,
        )


def _browser_search_is_time_sensitive(query: str) -> bool:
    clean = str(query or "").strip()
    cn_markers = [
        "最新",
        "最近",
        "近期",
        "今天",
        "本周",
        "本月",
        "刚刚",
        "今年",
        "现在",
        "截至",
    ]
    en_markers = ["updated", "latest", "recent", "today", "current"]
    lower = clean.lower()
    return any(marker in clean for marker in cn_markers) or any(marker in lower for marker in en_markers)


def _browser_search_has_conflicting_signals(snippets: list[str]) -> bool:
    combined = " ".join(snippets)
    markers = [
        "不同来源",
        "说法不一",
        "说法不完全一致",
        "有的资料",
        "也有资料",
        "另一种说法",
        "口径不一",
        "各地要求不同",
        "以当地为准",
        "版本不同",
    ]
    return any(marker in combined for marker in markers)


def _browser_search_source_rank(*, url: str, title: str) -> str:
    host = urlparse(url).netloc.lower()
    title_text = str(title or "")
    official_domains = (".gov.cn", ".edu.cn", ".org.cn", ".gov", ".edu", ".hospital", ".police")
    if any(host.endswith(domain) for domain in official_domains):
        return "official"
    if any(marker in host for marker in ["gov", "edu", "hospital", "clinic", "railway", "12306"]):
        return "official"
    if any(marker in title_text for marker in ["官网", "官方", "政务", "医院", "学校", "公告", "通知"]):
        return "official"
    if any(marker in host for marker in ["forum", "bbs", "zhidao", "tieba", "weibo", "xiaohongshu"]):
        return "community"
    return "generic"


def _browser_search_confidence(
    *,
    source_rank: str,
    query_is_time_sensitive: bool,
    snippets_have_conflict: bool,
) -> str:
    if source_rank == "official" and not snippets_have_conflict and not query_is_time_sensitive:
        return "high"
    if source_rank == "community" or snippets_have_conflict:
        return "cautious"
    return "medium"
