from __future__ import annotations

from typing import Any

from app.schemas.browser_research import BrowserResearchPlan, EvidenceAssessment, SearchResult


class BrowserResearchRenderer:
    def render(
        self,
        *,
        plan: BrowserResearchPlan,
        search_result: SearchResult,
        assessment: EvidenceAssessment,
    ) -> str:
        title = search_result.title or "未识别标题"
        status = str(search_result.http_status or "未知")
        url = search_result.url or ""
        snippets = [item.snippet for item in search_result.raw_evidence]
        intro = f"我已经用浏览器搜索了“{plan.query}”。当前拿到的证据来自搜索结果页《{title}》（HTTP {status}）。"
        confidence_label = _confidence_label(assessment)
        if plan.requested_sections and snippets:
            structured_lines = _structured_browser_search_lines(
                requested_sections=plan.requested_sections,
                snippets=snippets,
                presentation_style=plan.presentation_style,
                assessment=assessment,
                confidence_label=confidence_label,
            )
            summary = intro + "\n" + "\n".join(structured_lines)
        elif snippets:
            joined = "；".join(snippets[:3])
            if plan.presentation_style == "popular_explainer":
                summary = f"{intro}\n可信度：{confidence_label}。\n先用好懂的话概括一下：{joined}。"
            else:
                summary = f"{intro}\n可信度：{confidence_label}。\n从结果页可见内容看，相关线索包括：{joined}"
        else:
            summary = f"{intro}但这页没有稳定提取出足够清晰的结果摘要。"
        summary = _append_browser_search_editorial_notes(summary, assessment=assessment)
        if plan.citation_required:
            source = url or "browser.search 返回的搜索结果页"
            summary = summary.rstrip("。") + f"。这次总结的证据来源是：{source}"
        return summary


def _structured_browser_search_lines(
    *,
    requested_sections: list[str],
    snippets: list[str],
    presentation_style: str,
    assessment: EvidenceAssessment,
    confidence_label: str,
) -> list[str]:
    normalized = [section.strip() for section in requested_sections if section.strip()]
    prefix = [f"可信度：{confidence_label}。"]
    if normalized[:3] == ["1.", "2.", "3."] and presentation_style == "popular_explainer":
        prefix.append("先按容易执行的顺序整理一下：")
    prefix.extend(
        _browser_search_preface_lines(
            assessment=assessment,
            presentation_style=presentation_style,
        )
    )
    if normalized[:3] == ["1.", "2.", "3."]:
        body = [f"{index + 1}. {snippet}" for index, snippet in enumerate(snippets[:3])]
        return prefix + body
    for index, section in enumerate(normalized):
        detail = snippets[index] if index < len(snippets) else "当前结果页暂未提取到更明确的相关线索。"
        prefix.append(f"{section}：{detail}")
    return prefix


def _browser_search_preface_lines(
    *,
    assessment: EvidenceAssessment,
    presentation_style: str,
) -> list[str]:
    lines: list[str] = []
    if presentation_style == "popular_explainer":
        lines.append("先给一个背景提醒：这些信息更适合当作初步参考，真正办理或判断前最好再核对一次官方页面。")
    if assessment.source_rank == "official":
        lines.append("来源判断：当前拿到的是偏官方口径的结果页，可以优先参考。")
    elif assessment.source_rank == "community":
        lines.append("来源判断：当前更像是整理页或社区口径，适合先当线索，再回到官方页核对。")
    if assessment.conflict_level != "none":
        lines.append("补一句：当前搜到的不同线索里，个别说法不完全一致，更适合把它当成初步交叉参考。")
    if assessment.freshness == "time_sensitive":
        lines.append("时效提醒：这类信息可能更新较快，真正使用前最好再看一次最新的官方页面或公告。")
    return lines


def _append_browser_search_editorial_notes(summary: str, *, assessment: EvidenceAssessment) -> str:
    notes: list[str] = []
    if assessment.conflict_level != "none":
        notes.append("不同线索之间存在一些口径差异，最终判断更适合以权威来源为准")
    if assessment.freshness == "time_sensitive":
        notes.append("这类信息有明显时效性，真正采用前建议再核对一次最新页面")
    if not notes:
        return summary
    return summary.rstrip("。") + "\n" + "；".join(notes) + "。"


def _confidence_label(assessment: EvidenceAssessment) -> str:
    if assessment.confidence == "high":
        return "较高，可优先参考当前结果"
    if assessment.confidence == "cautious":
        return "中等偏谨慎，适合先当线索再交叉核对"
    if assessment.freshness == "time_sensitive":
        return "中等，主要受时效影响，落地前建议复核"
    return "中等，可作为初步整理参考"
