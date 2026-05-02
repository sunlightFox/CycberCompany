from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

OFFICE_BUNDLE_BY_TYPE = {
    "word": {"generate": "clawhub-word-report", "edit": "clawhub-word-edit"},
    "excel": {
        "generate": "clawhub-excel-analysis-workbook",
        "edit": "clawhub-excel-edit",
    },
    "ppt": {"generate": "clawhub-ppt-briefing", "edit": "clawhub-ppt-edit"},
}

OFFICE_TOOL_BY_TYPE = {
    "word": {"generate": "office.word.generate", "edit": "office.word.edit"},
    "excel": {"generate": "office.excel.generate", "edit": "office.excel.edit"},
    "ppt": {"generate": "office.ppt.generate", "edit": "office.ppt.edit"},
}


@dataclass(frozen=True)
class OfficeChatRequest:
    document_type: str
    operation: str
    topic: str
    content: str
    requested_pages_or_sheets: int | None = None
    edit_target_artifact_id: str | None = None
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChatRouteDecision:
    route_type: str
    confidence: float
    reason_code: str
    requires_confirmation: bool = False
    task_goal: str | None = None
    safe_user_summary: str | None = None
    office_request: OfficeChatRequest | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "route_type": self.route_type,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "requires_confirmation": self.requires_confirmation,
            "task_goal": self.task_goal,
            "safe_user_summary": self.safe_user_summary,
            "office_request": self.office_request.__dict__ if self.office_request else None,
            "metadata": self.metadata,
        }


class ChatIntentRouter:
    def decide(self, text: str) -> ChatRouteDecision:
        clean = _clean(text)
        if not clean:
            return ChatRouteDecision("empty", 0.0, "empty_text")
        office_request = parse_office_chat_request(clean)
        if office_request is not None:
            return ChatRouteDecision(
                route_type="office_document",
                confidence=0.95,
                reason_code="office_document_hard_route",
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
                office_request=office_request,
            )
        if is_skill_or_mcp_concept_request(clean):
            return ChatRouteDecision(
                route_type="skill_mcp_concept",
                confidence=0.82,
                reason_code="skill_mcp_concept_explanation",
                task_goal=None,
                safe_user_summary=_safe_summary(clean),
            )
        if is_explicit_download_request(clean):
            return ChatRouteDecision(
                route_type="browser_download",
                confidence=0.9,
                reason_code="explicit_download_target",
                requires_confirmation=True,
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
            )
        if is_download_topic_only(clean):
            return ChatRouteDecision(
                route_type="download_topic",
                confidence=0.78,
                reason_code="download_mentioned_as_topic",
                task_goal=None,
                safe_user_summary=_safe_summary(clean),
            )
        if is_webpage_read_request(clean):
            return ChatRouteDecision(
                route_type="browser_read_page",
                confidence=0.9,
                reason_code="browser_read_page_readonly",
                requires_confirmation=False,
                task_goal=None,
                safe_user_summary=_safe_summary(clean),
                metadata={"url": webpage_read_url(clean)},
            )
        if is_host_filesystem_list_request(clean):
            return ChatRouteDecision(
                route_type="host_filesystem_list",
                confidence=0.9,
                reason_code="host_filesystem_list_readonly",
                requires_confirmation=False,
                task_goal=None,
                safe_user_summary=_safe_summary(clean),
                metadata={
                    "location": host_filesystem_location(clean),
                    "limit": host_filesystem_limit(clean),
                },
            )
        if is_host_software_install_request(clean):
            return ChatRouteDecision(
                route_type="host_software_install",
                confidence=0.9,
                reason_code="explicit_host_software_install",
                requires_confirmation=True,
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
            )
        if is_file_mutation_request(clean):
            return ChatRouteDecision(
                route_type="file_mutation_task",
                confidence=0.88,
                reason_code="explicit_file_mutation_requires_confirmation",
                requires_confirmation=True,
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
            )
        return ChatRouteDecision("default", 0.5, "fallback_to_existing_chat_chain")


def parse_office_chat_request(text: str) -> OfficeChatRequest | None:
    clean = _clean(text)
    if _direct_only(clean):
        return None
    document_type = office_document_type(clean)
    if document_type is None:
        return None
    if is_skill_or_mcp_concept_request(clean) and not _has_office_action(clean):
        return None
    if not _has_office_action(clean):
        return None
    operation = "edit" if _is_office_edit(clean) else "generate"
    return OfficeChatRequest(
        document_type=document_type,
        operation=operation,
        topic=_office_topic(clean, document_type),
        content=clean,
        requested_pages_or_sheets=_requested_count(clean, document_type),
        edit_target_artifact_id=_extract_artifact_id(clean),
        reason_codes=["office_marker", "office_action_marker"],
    )


def office_document_type(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if any(marker in lowered for marker in ["excel", "xlsx"]) or any(
        marker in clean for marker in ["表格", "工作簿", "销售数据", "经营数据"]
    ):
        return "excel"
    if any(marker in lowered for marker in ["ppt", "pptx", "powerpoint"]) or any(
        marker in clean for marker in ["演示稿", "幻灯片", "汇报"]
    ):
        return "ppt"
    if any(marker in lowered for marker in ["word", "docx"]) or any(
        marker in clean for marker in ["文档", "周报", "报告"]
    ):
        return "word"
    return None


def is_office_document_request(text: str) -> bool:
    return parse_office_chat_request(text) is not None


def is_host_software_install_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if any(marker in clean or marker in lowered for marker in ["跳过确认", "免确认", "无需确认"]):
        return False
    if is_office_document_request(clean):
        return False
    if is_skill_install_context(clean):
        return False
    if any(marker in clean or marker in lowered for marker in ["依赖", "项目里", "node_modules"]):
        return False
    if host_software_action(clean) is None:
        return False
    return bool(extract_host_software_name(clean))


def host_software_action(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if any(marker in clean or marker in lowered for marker in ["卸载", "移除", "uninstall"]):
        return "uninstall"
    if any(marker in clean or marker in lowered for marker in ["安装", "装一下", "install"]):
        return "install"
    return None


def extract_host_software_name(text: str) -> str:
    clean = _clean(text)
    for delimiter in ["安装后", "装好后", "然后", "再卸载", "并卸载", "，", ",", "。"]:
        if delimiter in clean:
            clean = clean.split(delimiter, 1)[0]
    quoted = re.search(r"[“\"']([^”\"']{1,80})[”\"']", clean)
    if quoted:
        return quoted.group(1).strip()
    for marker in [
        "帮我",
        "请",
        "安装",
        "下载安装",
        "装一下",
        "卸载",
        "移除",
        "install",
        "uninstall",
        "到这台电脑",
        "到我的电脑",
        "这台电脑",
        "我的电脑",
        "到电脑",
        "全局",
        "本机",
    ]:
        clean = clean.replace(marker, " ")
    clean = re.sub(r"^(?:一下|个|一个|软件)\s*", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" ，。,.")
    return clean[:80]


def is_skill_install_context(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    return any(
        marker in clean or marker in lowered
        for marker in [
            "skill",
            "技能",
            "安装源",
            "仓库",
            "clawhub",
            "skillhub",
            "授权",
            "权限配置",
            "安装启用",
            "安装与授权",
        ]
    )


def is_explicit_download_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if "下载" not in clean and "download" not in lowered:
        return False
    if is_download_topic_only(clean):
        return False
    if _first_url(clean):
        return True
    explicit_target = any(
        marker in clean for marker in ["下载文件", "下载报表", "下载图片", "下载这个", "下载该"]
    )
    return explicit_target and any(
        marker in clean for marker in ["保存", "拿到", "给我", "告诉我结果"]
    )


def is_webpage_read_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if not _first_url(clean):
        return False
    if _has_browser_side_effect_marker(clean, lowered):
        return False
    return _has_webpage_read_marker(clean, lowered)


def webpage_read_url(text: str) -> str | None:
    return _first_url(_clean(text))


def is_file_mutation_request(text: str) -> bool:
    clean = _clean(text)
    if _direct_only(clean):
        return False
    if is_host_filesystem_list_request(clean):
        return False
    return any(marker in clean for marker in ["删除", "删掉", "清空", "覆盖"]) and any(
        marker in clean for marker in ["文件", "CSV", "csv", "下载", "结果", "outputs", "artifact"]
    )


def is_host_filesystem_list_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if any(marker in clean for marker in ["只解释", "只给方案", "不要执行", "不要调用工具"]):
        return False
    if _has_file_mutation_marker(clean):
        return False
    if host_filesystem_location(clean) is None:
        return False
    if _readonly_file_list_marker(clean, lowered):
        return True
    if any(marker in clean for marker in ["桌面有什么", "下载目录有什么", "下载文件夹有什么"]):
        return True
    if any(marker in lowered for marker in ["what files", "list files", "show files"]):
        return True
    return False


def host_filesystem_location(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if "desktop" in lowered or any(marker in clean for marker in ["桌面"]):
        return "desktop"
    if any(
        marker in clean
        for marker in ["下载目录", "下载文件夹", "下载文件", "下载里", "下载里面"]
    ) or any(marker in lowered for marker in ["downloads", "download folder", "download dir"]):
        return "downloads"
    if any(marker in clean for marker in ["文档目录", "文档文件夹", "我的文档"]) or any(
        marker in lowered for marker in ["documents", "document folder"]
    ):
        return "documents"
    if any(marker in clean for marker in ["主目录", "用户目录", "家目录"]) or lowered in {
        "home",
        "list home",
        "show home",
    }:
        return "home"
    return None


def host_filesystem_limit(text: str) -> int | None:
    match = re.search(r"(?:前|最多|limit\s*)\s*(\d{1,3})\s*(?:个|项|条|files?)", text, re.I)
    if not match:
        return None
    value = int(match.group(1))
    return max(1, min(value, 100))


def is_download_topic_only(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    topic_markers = [
        "下载端点",
        "下载接口",
        "下载链接能力",
        "下载功能",
        "artifact 下载",
        "download endpoint",
        "download api",
    ]
    return any(marker in clean or marker in lowered for marker in topic_markers)


def is_skill_or_mcp_concept_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    action_text = _without_direct_only_markers(clean)
    if any(
        marker in action_text
        for marker in ["生成", "创建", "做一个", "做一份", "编辑", "修改"]
    ):
        return False
    concept_markers = ["是什么", "怎么配置", "如何配置", "解释", "介绍", "原理", "区别"]
    target_markers = ["skill", "技能", "mcp"]
    return any(marker in clean or marker in lowered for marker in target_markers) and any(
        marker in clean or marker in lowered for marker in concept_markers
    )


def preferred_office_bundle_id(request: OfficeChatRequest) -> str:
    return OFFICE_BUNDLE_BY_TYPE[request.document_type][request.operation]


def preferred_office_tool_name(request: OfficeChatRequest) -> str:
    return OFFICE_TOOL_BY_TYPE[request.document_type][request.operation]


def office_skill_input(
    request: OfficeChatRequest,
    *,
    source_artifact_id: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "goal": request.content,
        "content": request.content,
        "topic": request.topic,
    }
    if request.requested_pages_or_sheets is not None:
        if request.document_type == "ppt":
            data["slide_count"] = request.requested_pages_or_sheets
        elif request.document_type == "excel":
            data["sheet_count"] = request.requested_pages_or_sheets
    if request.document_type == "word" and request.operation == "generate":
        data.update(_word_report_input_from_request(request))
    if request.document_type == "excel" and request.operation == "generate":
        data["sheets"] = _excel_sheets_from_request(request)
    if request.document_type == "ppt" and request.operation == "generate":
        data["title"] = request.topic
        data["summary"] = _ppt_summary_from_request(request)
        data["slides"] = _ppt_slides_from_request(request)
    artifact_id = request.edit_target_artifact_id or source_artifact_id
    if artifact_id:
        data["source_artifact_id"] = artifact_id
    return data


def _has_office_action(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    return any(
        marker in clean or marker in lowered
        for marker in [
            "生成",
            "创建",
            "做",
            "写",
            "编辑",
            "修改",
            "追加",
            "增加",
            "完善",
            "整理成",
            "做成",
            "导出",
            "office skill",
        ]
    )


def _is_office_edit(text: str) -> bool:
    return any(marker in text for marker in ["编辑", "修改", "追加", "增加", "替换", "完善", "改"])


def _requested_count(text: str, document_type: str) -> int | None:
    unit = "页" if document_type == "ppt" else "个|张"
    match = re.search(rf"(\d{{1,2}})\s*(?:{unit})", text)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 50 else None


def _office_topic(text: str, document_type: str) -> str:
    explicit = _explicit_topic(text, document_type)
    if explicit:
        return explicit
    topic = text
    for marker in [
        "帮我",
        "请",
        "把刚才的",
        "把刚才",
        "刚才的",
        "生成",
        "创建",
        "做一个",
        "做一份",
        "做成",
        "写一份",
        "编辑",
        "修改",
        "追加",
    ]:
        topic = topic.replace(marker, " ")
    for marker in ["word", "docx", "excel", "xlsx", "ppt", "pptx", "powerpoint"]:
        topic = re.sub(marker, " ", topic, flags=re.IGNORECASE)
    for marker in ["文档", "表格", "演示稿", "汇报"]:
        if document_type != "ppt" or marker != "汇报":
            topic = topic.replace(marker, " ")
    topic = re.sub(r"\d{1,2}\s*页", " ", topic)
    topic = topic.replace("增加风险与下一步章节", "风险与下一步")
    topic = re.sub(r"\s+", " ", topic)
    topic = re.sub(r"\s+([，。,.])", r"\1", topic)
    topic = re.sub(r"([（(])\s+", r"\1", topic)
    topic = re.sub(r"\s+([）)])", r"\1", topic)
    topic = topic.strip(" ，。,.")
    return topic[:80] or {"word": "文档", "excel": "表格", "ppt": "演示稿"}[document_type]


def _explicit_topic(text: str, document_type: str) -> str | None:
    clean = _clean(text)
    if document_type == "word" and "周报" in clean:
        return "项目周报"
    if "刚才" in clean and "风险" in clean and "下一步" in clean:
        return "风险与下一步"
    patterns = [
        r"主题(?:是|为|：|:)\s*([^，。；;\n]+)",
        r"关于\s*([^，。；;\n]+)",
        r"数据(?:是|为|：|:)\s*([^。；;\n]+)",
    ]
    if document_type != "word":
        patterns.append(r"内容包括\s*([^。；;\n]+)")
    if document_type == "excel":
        patterns.insert(0, r"把这些([^：:。；;\n]+)(?:做成|整理成)")
    for pattern in patterns:
        match = re.search(pattern, clean, flags=re.IGNORECASE)
        if not match:
            continue
        value = _clean(match.group(1)).strip(" ，。,.")
        value = re.split(r"，面向|，给|，并|，需要|，要求", value, maxsplit=1)[0].strip(" ，。,.")
        if value:
            return value[:80]
    if document_type == "excel" and "销售数据" in clean:
        return "销售数据分析"
    return None


def _word_report_input_from_request(request: OfficeChatRequest) -> dict[str, Any]:
    completed = _extract_after_markers(request.content, ["完成", "完成了", "已完成", "本周完成"])
    risk = _extract_after_markers(request.content, ["风险是", "风险为", "风险"])
    next_step = _extract_after_markers(request.content, ["下一步要", "下一步是", "下一步"])
    sections = [
        {
            "title": "本周进展",
            "paragraphs": [completed or request.content],
            "bullets": [],
        },
        {
            "title": "风险与问题",
            "paragraphs": [risk or "暂无额外风险，后续按上线节奏持续观察。"],
            "bullets": [],
        },
        {
            "title": "下一步计划",
            "paragraphs": [next_step or "继续推进优先事项，按节点同步进展。"],
            "bullets": [],
        },
    ]
    return {
        "title": request.topic or "项目周报",
        "summary": f"围绕{request.topic or '项目周报'}整理本周进展、风险与下一步。",
        "sections": sections,
        "tables": [
            {
                "headers": ["模块", "状态", "说明"],
                "rows": [
                    ["本周进展", "已完成", completed or "按输入内容推进"],
                    ["风险", "关注中", risk or "暂无额外风险"],
                    ["下一步", "计划中", next_step or "继续推进"],
                ],
            }
        ],
    }


def _extract_after_markers(text: str, markers: list[str]) -> str | None:
    clean = _clean(text)
    for marker in markers:
        if marker not in clean:
            continue
        value = clean.split(marker, 1)[1]
        value = re.split(r"，|。|；|;|\n", value, maxsplit=1)[0]
        value = re.sub(r"^[\s：:，。；;]+|[\s：:，。；;]+$", "", value)
        if value:
            return value
    return None


def _excel_sheets_from_request(request: OfficeChatRequest) -> list[dict[str, Any]]:
    rows = _extract_sales_rows(request.content)
    if not rows:
        rows = [
            ["收入", 120000, "示例数据"],
            ["成本", 76000, "示例数据"],
            ["利润", 44000, "示例数据"],
        ]
        headers = ["指标", "数值", "备注"]
    else:
        headers = ["期间", "收入", "成本", "利润"]
    return [
        {
            "name": "Data",
            "summary": request.content,
            "headers": headers,
            "rows": rows,
            "add_totals": True,
            "chart": True,
        }
    ]


def _extract_sales_rows(text: str) -> list[list[Any]]:
    clean = _clean(text)
    pattern = re.compile(
        r"(?P<period>\d{1,2}\s*月|Q[1-4]|第[一二三四1234]季度)"
        r"[^，。；;\n]*?收入\s*(?P<revenue>-?\d+(?:\.\d+)?)"
        r"[^，。；;\n]*?成本\s*(?P<cost>-?\d+(?:\.\d+)?)",
        flags=re.IGNORECASE,
    )
    rows: list[list[Any]] = []
    for match in pattern.finditer(clean):
        revenue = _number(match.group("revenue"))
        cost = _number(match.group("cost"))
        rows.append([match.group("period").replace(" ", ""), revenue, cost, revenue - cost])
    return rows


def _ppt_slides_from_request(request: OfficeChatRequest) -> list[dict[str, Any]]:
    requested = request.requested_pages_or_sheets or 5
    content_slide_count = max(1, min(requested, 20) - 1)
    topic = request.topic or "汇报"
    audience = _extract_audience(request.content)
    base = [
        (
            "背景与目标",
            [f"主题：{topic}", f"对象：{audience or '相关决策方'}", "明确决策问题和成功标准"],
        ),
        ("关键进展", [f"{topic} 的阶段性进展", "当前状态", "关键数据与证据"]),
        ("数据与洞察", ["核心指标变化", "主要原因", "值得关注的信号"]),
        ("风险与下一步", ["主要风险", "缓解动作", "负责人和时间点"]),
        ("决策建议", ["推荐方案", "资源需求", "需要确认的事项"]),
        ("附录", ["补充数据", "参考口径", "后续跟踪方式"]),
    ]
    slides: list[dict[str, Any]] = []
    for index in range(content_slide_count):
        title, bullets = base[index] if index < len(base) else (f"补充页面 {index + 1}", ["待补充"])
        slides.append({"title": title, "bullets": bullets})
    return slides


def _ppt_summary_from_request(request: OfficeChatRequest) -> str:
    audience = _extract_audience(request.content)
    if audience:
        return f"面向{audience}的{request.topic}汇报。"
    return f"{request.topic}汇报。"


def _extract_audience(text: str) -> str | None:
    match = re.search(r"面向\s*([^，。；;\n]+)", text)
    if not match:
        return None
    return match.group(1).strip(" ，。,.")[:40] or None


def _number(value: str) -> int | float:
    parsed = float(value)
    return int(parsed) if parsed.is_integer() else parsed


def _extract_artifact_id(text: str) -> str | None:
    match = re.search(r"\bart_[A-Za-z0-9_-]+\b", text)
    return match.group(0) if match else None


def _readonly_file_list_marker(clean: str, lowered: str) -> bool:
    return any(
        marker in clean
        for marker in [
            "有哪些文件",
            "有什么文件",
            "有啥文件",
            "有什么东西",
            "有哪些东西",
            "列出",
            "列一下",
            "看一下目录",
            "看看目录",
            "查看目录",
            "目录里有什么",
            "目录里面有什么",
            "文件夹里有什么",
            "文件夹里面有什么",
            "文件列表",
        ]
    ) or any(
        marker in lowered
        for marker in [
            "list files",
            "show files",
            "what files",
            "list directory",
            "show directory",
        ]
    )


def _has_file_mutation_marker(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "删除",
            "删掉",
            "清空",
            "覆盖",
            "移动",
            "搬到",
            "写入",
            "保存到",
            "新建",
            "创建文件",
            "执行文件",
            "运行文件",
            "重命名",
            "修改文件",
        ]
    )


def _has_webpage_read_marker(clean: str, lowered: str) -> bool:
    return any(
        marker in clean
        for marker in [
            "看一下这网站",
            "看一下这个网站",
            "看下这网站",
            "看下这个网站",
            "看看这网站",
            "看看这个网站",
            "看一下网页",
            "看看网页",
            "这个网页",
            "这网页",
            "网页内容",
            "网站内容",
            "这个链接",
            "这链接",
            "链接内容",
            "讲什么",
            "说什么",
            "有什么内容",
            "主要内容",
            "帮我看一下",
            "帮我看看",
            "总结这个链接",
            "总结一下这个链接",
            "总结这篇",
            "概括这个链接",
            "分析这个链接",
        ]
    ) or any(
        marker in lowered
        for marker in [
            "summarize this link",
            "summarise this link",
            "what is this page",
            "what's this page",
            "what does this link say",
            "read this page",
            "review this website",
            "page content",
            "website content",
        ]
    )


def _has_browser_side_effect_marker(clean: str, lowered: str) -> bool:
    clean = re.sub(r"https?://[^\s，。；;）)]+", " ", clean, flags=re.IGNORECASE)
    lowered = clean.lower() if lowered else lowered
    return any(
        marker in clean
        for marker in [
            "下载",
            "保存",
            "登录",
            "注册",
            "填写",
            "输入",
            "点击",
            "点一下",
            "提交",
            "发送",
            "发布",
            "上传",
            "购买",
            "下单",
            "支付",
            "转账",
            "截图",
            "截屏",
            "导出",
        ]
    ) or any(
        marker in lowered
        for marker in [
            "download",
            "save",
            "login",
            "log in",
            "sign in",
            "submit",
            "send",
            "post",
            "publish",
            "upload",
            "click",
            "buy",
            "checkout",
            "pay",
            "screenshot",
            "export",
        ]
    )


def _first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s，。；;）)]+", text, flags=re.IGNORECASE)
    return match.group(0) if match else None


def _direct_only(text: str) -> bool:
    return any(
        marker in text
        for marker in ["只解释", "只给方案", "不要执行", "不要创建任务", "不要调用工具"]
    )


def _without_direct_only_markers(text: str) -> str:
    result = text
    for marker in ["只解释", "只给方案", "不要执行", "不要创建任务", "不要调用工具"]:
        result = result.replace(marker, " ")
    return _clean(result)


def _clean(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _safe_summary(text: str) -> str:
    return _clean(text)[:120]
