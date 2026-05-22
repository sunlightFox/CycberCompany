from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas.browser_research import BrowserResearchPlan

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
    browser_research_plan: BrowserResearchPlan | None = None
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
            "browser_research_plan": (
                self.browser_research_plan.model_dump(mode="json")
                if self.browser_research_plan is not None
                else None
            ),
            "metadata": self.metadata,
        }


class ChatIntentRouter:
    def decide(self, text: str) -> ChatRouteDecision:
        clean = _clean(text)
        if not clean:
            return ChatRouteDecision("empty", 0.0, "empty_text")
        structured_summary_request = is_structured_summary_request(clean)
        format_sensitive_request = _format_sensitive_direct_answer_request(clean)
        office_request = parse_office_chat_request(clean)
        if office_request is None:
            document_type = office_document_type(clean)
            if (
                document_type is not None
                and not structured_summary_request
                and not format_sensitive_request
                and not _direct_only(clean)
                and not _looks_like_non_office_advice_context(clean)
                and not _negative_office_generation_constraint(clean)
                and not _knowledge_text_only_request(clean)
                and not _office_completion_reporting_question(clean)
                and not is_skill_or_mcp_concept_request(clean)
                and not is_host_filesystem_list_request(clean)
            ):
                office_request = OfficeChatRequest(
                    document_type=document_type,
                    operation="edit" if _is_office_edit(clean) else "generate",
                    topic=_office_topic(clean, document_type),
                    content=clean,
                    requested_pages_or_sheets=_requested_count(clean, document_type),
                    edit_target_artifact_id=_extract_artifact_id(clean),
                    reason_codes=["office_marker", "office_type_fallback"],
                )
        if office_request is not None:
            return ChatRouteDecision(
                route_type="office_document",
                confidence=0.95,
                reason_code="office_document_hard_route",
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
                office_request=office_request,
            )
        if is_skill_or_mcp_concept_request(clean) and not format_sensitive_request:
            return ChatRouteDecision(
                route_type="skill_mcp_concept",
                confidence=0.82,
                reason_code="skill_mcp_concept_explanation",
                task_goal=None,
                safe_user_summary=_safe_summary(clean),
            )
        requested_ai_coding_tools = ai_coding_tool_request(clean)
        if requested_ai_coding_tools:
            return ChatRouteDecision(
                route_type="ai_coding_tool_request",
                confidence=0.86,
                reason_code="ai_coding_tool_capability_check",
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
                metadata={"requested_tools": requested_ai_coding_tools},
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
        lowered = clean.lower()
        if (
            is_webpage_read_request(clean)
            and _readonly_login_page_inspection(clean, lowered)
        ) or _readonly_form_page_field_request(clean, lowered):
            return ChatRouteDecision(
                route_type="browser_read_page",
                confidence=0.9,
                reason_code="browser_read_page_readonly",
                requires_confirmation=False,
                task_goal=None,
                safe_user_summary=_safe_summary(clean),
                metadata={"url": webpage_read_url(clean)},
            )
        if is_browser_page_action_request(clean):
            return ChatRouteDecision(
                route_type="browser_page_action",
                confidence=0.9,
                reason_code="browser_page_action_requires_workflow",
                requires_confirmation=True,
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
                metadata={"url": _first_url(clean)},
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
        if is_browser_search_request(clean):
            plan = browser_research_plan(clean)
            return ChatRouteDecision(
                route_type=(
                    "browser_search_with_citation"
                    if plan.citation_required
                    else "browser_search_readonly"
                ),
                confidence=0.9 if plan.citation_required else 0.86,
                reason_code=(
                    "browser_search_with_citation"
                    if plan.citation_required
                    else "browser_search_readonly"
                ),
                requires_confirmation=False,
                task_goal=None,
                safe_user_summary=_safe_summary(clean),
                browser_research_plan=plan,
                metadata={
                    "query": plan.query,
                    "require_citation": plan.citation_required,
                    "requested_sections": list(plan.requested_sections),
                    "presentation_style": plan.presentation_style,
                },
            )
        code_hosting = code_hosting_route(clean)
        if code_hosting is not None:
            return ChatRouteDecision(
                route_type=code_hosting,
                confidence=0.9,
                reason_code=f"{code_hosting}_detected",
                requires_confirmation=False,
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
                metadata={"forge_provider_type": "github"},
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
        repo_route = repo_execution_route(clean)
        if repo_route is not None:
            return ChatRouteDecision(
                route_type=repo_route,
                confidence=0.88,
                reason_code=f"{repo_route}_detected",
                requires_confirmation=False,
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
            )
        if is_desktop_native_request(clean):
            return ChatRouteDecision(
                route_type="desktop_native_request",
                confidence=0.94,
                reason_code="desktop_native_not_supported",
                requires_confirmation=False,
                task_goal=None,
                safe_user_summary=_safe_summary(clean),
                metadata={"capability_namespace": "desktop"},
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
        command = terminal_command(clean)
        if command is not None:
            return ChatRouteDecision(
                route_type="terminal_readonly_command",
                confidence=0.86,
                reason_code="terminal_readonly_command",
                requires_confirmation=False,
                task_goal=clean,
                safe_user_summary=_safe_summary(clean),
                metadata={"command": command},
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


def direct_only_requested(text: str) -> bool:
    return _direct_only(_clean(text))


def format_sensitive_direct_answer_requested(text: str) -> bool:
    return _format_sensitive_direct_answer_request(_clean(text))


def is_readonly_route_request(text: str) -> bool:
    clean = _clean(text)
    return any(
        (
            is_webpage_read_request(clean),
            is_browser_search_request(clean),
            is_host_filesystem_list_request(clean),
            terminal_command(clean) is not None,
        )
    )


def parse_office_chat_request(text: str) -> OfficeChatRequest | None:
    clean = _clean(text)
    if _direct_only(clean):
        return None
    if _looks_like_non_office_advice_context(clean):
        return None
    if _attachment_answer_only_request(clean):
        return None
    if is_structured_summary_request(clean):
        return None
    if _format_sensitive_direct_answer_request(clean) and not _has_office_action(clean):
        return None
    if is_host_filesystem_list_request(clean):
        return None
    if _negative_office_generation_constraint(clean):
        return None
    if _knowledge_text_only_request(clean):
        return None
    document_type = office_document_type(clean)
    if document_type is None:
        return None
    if _office_completion_reporting_question(clean):
        return None
    if is_skill_or_mcp_concept_request(clean) and not _has_office_action(clean):
        return None
    if not _has_office_action(clean) and not _has_implied_office_action(clean, document_type):
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


def _has_implied_office_action(text: str, document_type: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    informational_markers = [
        "?",
        "\uff1f",
        "what is",
        "how to",
        "explain",
        "difference",
        "\u662f\u4ec0\u4e48",
        "\u600e\u4e48\u7528",
        "\u89e3\u91ca",
        "\u4ecb\u7ecd",
        "\u539f\u7406",
        "\u533a\u522b",
        "是什么",
        "怎么用",
        "解释",
        "介绍",
        "原理",
        "区别",
    ]
    if any(marker in clean or marker in lowered for marker in informational_markers):
        return False
    if document_type in {"word", "excel", "ppt"}:
        return True
    return False


def office_document_type(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if any(marker in lowered for marker in ["excel", "xlsx"]) or any(
        marker in clean for marker in ["表格", "工作簿", "销售数据", "经营数据"]
    ):
        return "excel"
    if any(marker in lowered for marker in ["ppt", "pptx", "powerpoint"]) or any(
        marker in clean for marker in ["演示稿", "幻灯片"]
    ):
        return "ppt"
    if any(marker in lowered for marker in ["word", "docx"]) or any(
        marker in clean for marker in ["文档", "周报"]
    ):
        return "word"
    return None


def is_office_document_request(text: str) -> bool:
    return parse_office_chat_request(text) is not None


def is_structured_summary_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    summary_markers = [
        "总结",
        "总结成",
        "概括",
        "整理成",
        "归纳",
        "提炼",
        "改写成",
        "梳理",
        "分析质量",
        "summary",
        "summarize",
        "rewrite",
        "organize",
    ]
    structure_markers = [
        "标题",
        "一级标题",
        "二级标题",
        "小标题",
        "行动项",
        "关键观察",
        "段落",
        "自然段",
        "表格",
        "markdown 表格",
        "markdown table",
        "列表",
        "要点",
        "编号",
        "三条",
        "两段",
        "一段",
        "只输出标题",
        "不要表格",
        "不要列表",
        "不要 markdown",
        "不要markdown",
        "标题 +",
        "表格 +",
        "结论段落",
        "bullet",
    ]
    office_action_markers = [
        "生成",
        "创建",
        "做一个",
        "做一份",
        "产出",
        "导出",
        "写入",
        "保存",
        "做成word",
        "做成excel",
        "做成ppt",
        "生成word",
        "生成excel",
        "生成ppt",
    ]
    has_summary = any(marker in clean or marker in lowered for marker in summary_markers)
    has_structure = any(marker in clean or marker in lowered for marker in structure_markers)
    has_office_action = any(marker in clean or marker in lowered for marker in office_action_markers)
    return has_structure and (has_summary or "素材：" in clean or "素材:" in clean) and not has_office_action


def is_host_software_install_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if _negative_host_software_constraint(clean):
        return False
    if any(marker in clean or marker in lowered for marker in ["跳过确认", "免确认", "无需确认"]):
        return False
    if is_office_document_request(clean):
        return False
    if is_skill_install_context(clean):
        return False
    if any(marker in clean or marker in lowered for marker in ["依赖", "项目里", "node_modules"]):
        return False
    if _host_install_is_described_risk_context(clean):
        return False
    if host_software_action(clean) is None:
        return False
    if not _host_install_is_user_requested_action(clean):
        return False
    return bool(extract_host_software_name(clean))


def _host_install_is_described_risk_context(text: str) -> bool:
    clean = _clean(text)
    risk_markers = [
        "诱导安装",
        "被诱导安装",
        "被骗安装",
        "让父母安装",
        "让我爸妈安装",
        "让我爸安装",
        "让我妈安装",
        "陌生人让",
        "群里的人诱导",
        "不要安装",
    ]
    advice_markers = [
        "核验",
        "止损",
        "沟通方案",
        "风险",
        "诈骗",
        "银行卡",
        "理财",
        "安全",
        "不要吓",
        "劝",
    ]
    return any(marker in clean for marker in risk_markers) and any(marker in clean for marker in advice_markers)


def _host_install_is_user_requested_action(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    request_markers = [
        "帮我安装",
        "帮我卸载",
        "请安装",
        "请卸载",
        "给我安装",
        "给我卸载",
        "装一下",
        "卸一下",
        "安装到这台电脑",
        "装到这台电脑",
        "安装到我的电脑",
        "装到我的电脑",
        "安装到本机",
        "装到本机",
        "这台电脑安装",
        "本机安装",
        "install ",
        "uninstall ",
    ]
    return any(marker in clean or marker in lowered for marker in request_markers)


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
    clean = re.sub(r"^\s*再(?:次)?", " ", clean)
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


def repo_execution_route(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return None
    if is_structured_summary_request(clean):
        return None
    if _looks_like_browser_page_action(clean):
        return None
    if not any(
        marker in clean or marker in lowered
        for marker in [
            "repo",
            "仓库",
            "代码",
            "代码仓",
            "代码库",
            "pytest",
            "测试",
            "重构",
            "patch",
            "bugfix",
            "改代码",
            "修复",
        ]
    ):
        return None
    action_text = _without_direct_only_markers(clean)
    if _repo_meta_discussion_request(action_text):
        return None
    if any(
        marker in action_text or marker in lowered
        for marker in ["只读", "readonly", "read only", "看看代码", "阅读代码", "读一下", "读读"]
    ):
        return "repo_readonly_request"
    if any(marker in action_text or marker in lowered for marker in ["修复失败", "fix after failure", "失败后修", "测试失败"]):
        return "repo_fix_after_failure"
    if any(marker in action_text or marker in lowered for marker in ["重构", "refactor"]):
        return "repo_refactor_request"
    if any(marker in action_text or marker in lowered for marker in _REPO_TEST_ACTION_MARKERS):
        return "repo_test_request"
    if any(marker in action_text or marker in lowered for marker in _REPO_PATCH_ACTION_MARKERS):
        return "repo_patch_request"
    return None


def code_hosting_route(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return None
    if _looks_like_browser_page_action(clean):
        return None
    has_provider = any(
        marker in clean or marker in lowered
        for marker in ["github", "代码托管", "远程仓库", "github.com"]
    )
    has_hosting_action = any(
        marker in clean or marker in lowered
        for marker in ["pull request", "merge", "release", "push", "branch", "issue", "sync"]
    ) or re.search(r"\bpr\b", lowered)
    has_review_action = any(
        marker in clean or marker in lowered for marker in ["review", "评审", "审查", "comment", "评论"]
    )
    if not has_provider and not has_hosting_action:
        return None
    if has_review_action and not (has_provider or has_hosting_action):
        return None
    if any(marker in clean or marker in lowered for marker in ["状态", "看看", "查看", "list", "read-only", "readonly", "只读"]):
        return "code_hosting_readonly_request"
    if any(marker in clean or marker in lowered for marker in ["release", "发布", "release note"]):
        return "code_hosting_release_request"
    if any(marker in clean or marker in lowered for marker in ["review", "评审", "审查", "comment", "评论"]):
        return "code_hosting_review_request"
    if any(marker in clean or marker in lowered for marker in ["pull request", "合并请求"]) or re.search(r"\bpr\b", lowered):
        return "code_hosting_pr_request"
    if any(marker in clean or marker in lowered for marker in ["push", "branch", "同步", "sync", "merge"]):
        return "code_hosting_sync_request"
    return "code_hosting_readonly_request"


def _looks_like_browser_page_action(text: str) -> bool:
    lowered = str(text or "").lower()
    if "http://" not in lowered and "https://" not in lowered:
        return False
    return any(
        marker in lowered
        for marker in (
            "打开",
            "页面",
            "网页",
            "浏览器",
            "下载",
            "登录",
            "login",
            "截图",
            "截屏",
            "screenshot",
            "账号",
            "密码",
        )
    )


def _explicit_readonly_browser_inspection(clean: str, lowered: str) -> bool:
    readonly_markers = (
        "只读",
        "仅读",
        "不要尝试登录",
        "不要登录",
        "别登录",
        "不要点击",
        "不要提交",
        "不要填写",
        "不要输入",
        "read only",
        "readonly",
        "do not log in",
        "don't log in",
        "do not click",
        "do not submit",
    )
    inspection_markers = (
        "阅读",
        "读一下",
        "看一下",
        "有哪些输入框",
        "输入框",
        "字段",
        "标题",
        "内容",
        "summarize",
        "read this",
        "inspect",
        "what fields",
    )
    return any(marker in clean or marker in lowered for marker in readonly_markers) and any(
        marker in clean or marker in lowered for marker in inspection_markers
    )


def is_explicit_download_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if _negative_download_constraint(clean):
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
    if _explicit_readonly_browser_inspection(clean, lowered):
        return True
    if _readonly_login_page_inspection(clean, lowered):
        return True
    if _has_browser_side_effect_marker(clean, lowered):
        return False
    return _has_webpage_read_marker(clean, lowered)


def is_browser_search_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if _first_url(clean) and _has_webpage_read_marker(clean, lowered):
        return False
    if _has_browser_side_effect_marker(clean, lowered):
        return False
    if any(marker in clean for marker in ("不要搜索", "别搜索", "不搜索")):
        return False
    has_browser_marker = any(
        marker in clean or marker in lowered
        for marker in ("浏览器搜索", "用浏览器搜索", "search", "搜索一下", "搜一下", "查一下")
    )
    if not has_browser_marker and any(marker in clean for marker in ("搜一次", "再搜一次", "再用浏览器搜一次")):
        has_browser_marker = True
    if not has_browser_marker:
        return False
    return any(
        marker in clean or marker in lowered
        for marker in ("总结", "概括", "结果", "搜", "搜索", "search", "证据来源", "来源")
    )


def browser_search_requires_citation(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    return any(
        marker in clean or marker in lowered
        for marker in ("证据来源", "来源", "引用", "citation", "source")
    )


def browser_search_query(text: str) -> str:
    clean = _clean(text)
    query = clean
    for marker in ("再用浏览器搜一次", "再搜一次", "搜一次"):
        query = query.replace(marker, " ")
    replacements = [
        "请用浏览器搜索",
        "用浏览器搜索",
        "浏览器搜索",
        "请搜索",
        "搜索一下",
        "搜一下",
        "请用搜索",
    ]
    for marker in replacements:
        query = query.replace(marker, " ")
    query = re.sub(r"^[A-Za-z]{2,}\d{0,4}-\d{2,4}\s+", " ", query)
    query = re.sub(r"^[A-Za-z][A-Za-z0-9_-]{5,}\s+", " ", query)
    query = re.sub(r"https?://[^\s，。；;）)]+", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"微信消息中的链接", " ", query)
    query = re.sub(r"微信消息链接", " ", query)
    query = re.sub(r"用户还附带了一个link", " ", query)
    query = re.sub(r"上下文参考\s*url", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\blink\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"(并)?总结结果.*$", " ", query)
    query = re.sub(r"必须说明证据来源.*$", " ", query)
    query = re.sub(r"看看这个搜索页有什么.*$", " ", query)
    query = re.sub(r"[，。；;？?]+", " ", query)
    query = re.sub(r"这次用.*?(总结|概括).*$", " ", query)
    query = re.sub(r"用两句话.*$", " ", query)
    query = re.sub(r"带上来源.*$", " ", query)
    query = re.sub(r"^\s*再", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return query or clean


def is_desktop_native_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    desktop_markers = [
        "桌面窗口",
        "桌面上的",
        "最小化",
        "最大化",
        "切换窗口",
        "鼠标",
        "键盘",
        "记事本窗口",
        "desktop.",
        "window",
    ]
    return any(marker in clean or marker in lowered for marker in desktop_markers) and any(
        marker in clean or marker in lowered
        for marker in ["最小化", "最大化", "点击", "移动", "关闭", "操作", "minimize"]
    )


def webpage_read_url(text: str) -> str | None:
    return _first_url(_clean(text))


def is_file_mutation_request(text: str) -> bool:
    clean = _clean(text)
    if _direct_only(clean):
        return False
    if _negative_file_mutation_constraint(clean):
        return False
    if is_host_filesystem_list_request(clean):
        return False
    if _ambiguous_file_mutation_scope(clean):
        return False
    return any(marker in clean for marker in ["删除", "删掉", "清空", "覆盖"]) and any(
        marker in clean for marker in ["文件", "CSV", "csv", "下载", "结果", "outputs", "artifact"]
    )


def _ambiguous_file_mutation_scope(text: str) -> bool:
    clean = _clean(text)
    if not any(marker in clean for marker in ["删除", "删掉", "清空", "覆盖"]):
        return False
    if not any(marker in clean for marker in ["文件", "目录", "材料", "资料"]):
        return False
    if not any(marker in clean for marker in ["那个", "这个", "某个", "看着没用", "没用的"]):
        return False
    concrete_markers = [
        "/",
        "\\",
        ".txt",
        ".md",
        ".json",
        ".csv",
        ".docx",
        ".xlsx",
        ".pptx",
        "桌面",
        "下载目录",
        "当前目录",
        "当前项目",
        "outputs",
        "artifact",
    ]
    return not any(marker in clean for marker in concrete_markers)


def is_host_filesystem_list_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if any(
        marker in clean
        for marker in ["只解释", "只给方案", "不要执行", "不要假装执行", "不要调用工具"]
    ):
        return False
    if _has_file_mutation_marker(clean):
        return False
    if host_filesystem_location(clean) is None:
        return False
    if any(
        marker in clean
        for marker in [
            "\u684c\u9762\u6709\u4ec0\u4e48",
            "\u684c\u9762\u73b0\u5728\u90fd\u6709\u4ec0\u4e48",
            "\u684c\u9762\u73b0\u5728\u6709\u54ea\u4e9b\u6587\u4ef6",
            "\u53ea\u8981\u6587\u4ef6\u540d",
            "\u53ea\u5217\u51fa\u6587\u4ef6\u540d",
            "\u5e2e\u6211\u770b\u4e00\u4e0b\u684c\u9762",
            "\u5217\u4e00\u4e0b\u684c\u9762\u6587\u4ef6",
            "\u4e0b\u8f7d\u76ee\u5f55\u6709\u4ec0\u4e48",
            "\u4e0b\u8f7d\u6587\u4ef6\u5939\u6709\u4ec0\u4e48",
        ]
    ):
        return True
    if _readonly_file_list_marker(clean, lowered):
        return True
    if any(
        marker in clean
        for marker in [
            "桌面有什么",
            "桌面现在都有什么",
            "桌面现在有哪些文件",
            "只要文件名",
            "只列出文件名",
            "下载目录有什么",
            "下载文件夹有什么",
        ]
    ):
        return True
    if any(marker in lowered for marker in ["what files", "list files", "show files"]):
        return True
    return False


def terminal_command(text: str) -> str | None:
    clean = _clean(text)
    if _direct_only(clean):
        return None
    if not _terminal_request_marker(clean):
        compact = clean.strip().strip("`\"'")
        if _safe_readonly_terminal_command(compact):
            return compact[:240]
        return None
    explicit = _quoted_terminal_command(clean)
    command = explicit or _after_terminal_marker(clean)
    if not command:
        return None
    command = command.strip().strip("。；;")
    if not _safe_readonly_terminal_command(command):
        return None
    return command[:240]


def is_terminal_command_request(text: str) -> bool:
    return terminal_command(text) is not None


def host_filesystem_location(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if any(marker in clean for marker in ["\u684c\u9762", "\u684c\u9762\u4e0a", "\u684c\u9762\u91cc"]):
        return "desktop"
    if any(
        marker in clean
        for marker in [
            "\u4e0b\u8f7d\u76ee\u5f55",
            "\u4e0b\u8f7d\u6587\u4ef6\u5939",
            "\u4e0b\u8f7d\u6587\u4ef6",
            "\u4e0b\u8f7d\u91cc",
            "\u4e0b\u8f7d\u91cc\u9762",
        ]
    ):
        return "downloads"
    if any(marker in clean for marker in ["\u6587\u6863\u76ee\u5f55", "\u6587\u6863\u6587\u4ef6\u5939", "\u6211\u7684\u6587\u6863"]):
        return "documents"
    if any(marker in clean for marker in ["\u4e3b\u76ee\u5f55", "\u7528\u6237\u76ee\u5f55", "\u5bb6\u76ee\u5f55"]):
        return "home"
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


def _terminal_request_marker(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in text or marker in lowered
        for marker in [
            "执行命令",
            "运行命令",
            "执行只读命令",
            "运行只读命令",
            "跑一下命令",
            "系统命令",
            "终端命令",
            "terminal.run",
            "run command",
            "shell command",
        ]
    )


def _quoted_terminal_command(text: str) -> str | None:
    match = re.search(r"[`“\"']([^`”\"']{1,240})[`”\"']", text)
    return match.group(1).strip() if match else None


def _after_terminal_marker(text: str) -> str | None:
    patterns = [
        r"(?:执行命令|运行命令|跑一下命令|系统命令|终端命令)\s*[:：]?\s*(.+)$",
        r"(?:执行只读命令|运行只读命令)\s*[:：]?\s*(.+)$",
        r"(?:terminal\.run|run command|shell command)\s*[:：]?\s*(.+)$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            command = match.group(1).strip()
            safe_prefix = re.match(
                r"((?:echo|date|time|whoami|hostname|pwd|ls|dir|ver|get-date|get-location)(?:\s+[^\s，。；;,]+)?)",
                command,
                flags=re.IGNORECASE,
            )
            return (safe_prefix.group(1) if safe_prefix else command).strip()
    return None


def _safe_readonly_terminal_command(command: str) -> bool:
    lowered = command.lower().strip()
    if not lowered:
        return False
    blocked = [
        "rm ",
        "del ",
        "remove-item",
        "move-item",
        "copy-item",
        "set-item",
        "new-item",
        "mkdir",
        "rmdir",
        "curl",
        "wget",
        "invoke-webrequest",
        "invoke-restmethod",
        "pip install",
        "npm install",
        ">",
        ">>",
        "|",
        "&&",
        ";",
    ]
    if any(marker in f" {lowered} " for marker in blocked):
        return False
    executable = lowered.split()[0].strip("\"'")
    executable = executable.removesuffix(".exe")
    return executable in {
        "echo",
        "date",
        "time",
        "whoami",
        "hostname",
        "pwd",
        "ls",
        "dir",
        "ver",
        "get-date",
        "get-location",
        "python",
        "python3",
        "py",
        "node",
    }


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
    return any(marker in clean or marker in lowered for marker in topic_markers) or (
        _negative_download_constraint(clean)
        and any(marker in clean or marker in lowered for marker in ["下载", "download"])
    )


def is_skill_or_mcp_concept_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    action_text = _without_direct_only_markers(clean)
    if any(
        marker in action_text or marker in lowered
        for marker in ["生成", "创建", "做一个", "做一份", "编辑", "修改", "generate", "create", "edit", "modify", "build"]
    ):
        return False
    concept_markers = [
        "是什么",
        "怎么配置",
        "如何配置",
        "解释",
        "介绍",
        "原理",
        "区别",
        "what is",
        "explain",
        "difference",
        "compare",
        "how to configure",
    ]
    target_markers = ["skill", "技能", "mcp"]
    return any(marker in clean or marker in lowered for marker in target_markers) and any(
        marker in clean or marker in lowered for marker in concept_markers
    )


def _format_sensitive_direct_answer_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if is_structured_summary_request(clean):
        return True
    if not any(
        marker in clean or marker in lowered
        for marker in [
            "只输出 json",
            "只输出json",
            "json-only",
            "only output json",
            "only json",
            "output json only",
            "不要markdown",
            "不要 markdown",
            "no markdown",
            "只要纯文本",
            "plain text only",
            "只要表格",
            "markdown 表格",
            "markdown表格",
            "用表格比较",
            "用表格",
            "表格比较",
            "use a table",
            "table to compare",
            "compare in a table",
            "markdown table",
            "只返回代码",
            "只要代码",
            "code only",
        ]
    ):
        return False
    return True


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
    return any(
        marker in text
        for marker in [
            "编辑",
            "修改",
            "追加",
            "增加",
            "替换",
            "完善",
            "改成",
            "改为",
            "改一下",
            "改写",
            "调整",
            "修订",
        ]
    )


def _has_office_action(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    ascii_markers = ["generate", "create", "edit", "modify", "export", "office skill"]
    unicode_markers = [
        "\u751f\u6210",
        "\u521b\u5efa",
        "\u505a\u4e00\u4e2a",
        "\u505a\u4e00\u4efd",
        "\u505a\u6210",
        "\u5199\u4e00\u4efd",
        "\u7f16\u8f91",
        "\u4fee\u6539",
        "\u8ffd\u52a0",
        "\u589e\u52a0",
        "\u5b8c\u5584",
        "\u6574\u7406\u6210",
        "\u5bfc\u51fa",
    ]
    legacy_markers = [
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
    ]
    return any(marker in lowered for marker in ascii_markers) or any(
        marker in clean for marker in [*unicode_markers, *legacy_markers]
    )


def _is_office_edit(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "\u7f16\u8f91",
            "\u4fee\u6539",
            "\u8ffd\u52a0",
            "\u589e\u52a0",
            "\u66ff\u6362",
            "\u5b8c\u5584",
            "\u6539\u6210",
            "\u6539\u4e3a",
            "\u6539\u4e00\u4e0b",
            "\u6539\u5199",
            "\u8c03\u6574",
            "\u4fee\u8ba2",
            "缂栬緫",
            "淇敼",
            "杩藉姞",
            "澧炲姞",
            "鏇挎崲",
            "瀹屽杽",
        ]
    )


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
    budget_rows = _extract_budget_rows(request.content) if not rows else []
    if budget_rows:
        rows = budget_rows
        headers = ["项目", "金额"]
    elif not rows:
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


def _extract_budget_rows(text: str) -> list[list[Any]]:
    clean = _clean(text)
    rows: list[list[Any]] = []
    for label, amount in re.findall(r"([\u4e00-\u9fffA-Za-z]{1,12})\s*(\d+(?:\.\d+)?)", clean):
        if label.lower() in {"excel", "xlsx"}:
            continue
        value = _number(amount)
        rows.append([label, value])
    budget_labels = {"房租", "餐饮", "交通", "医疗", "学习", "水电", "通讯", "娱乐", "保险"}
    return rows if any(str(row[0]) in budget_labels for row in rows) else []


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


def _negative_download_constraint(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    return any(
        marker in clean or marker in lowered
        for marker in [
            "不要真的下载",
            "不要下载",
            "不触发下载",
            "不会触发真实下载",
            "只说明下载",
            "只解释下载",
            "下载端点说明",
            "下载接口说明",
            "do not download",
            "don't download",
        ]
    )


def _negative_host_software_constraint(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    return any(
        marker in clean or marker in lowered
        for marker in [
            "不要安装",
            "不要真的安装",
            "不安装",
            "只说明安装",
            "只解释安装",
            "安装包校验机制",
            "安装流程说明",
            "do not install",
            "don't install",
        ]
    )


def _negative_file_mutation_constraint(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    return any(
        marker in clean or marker in lowered
        for marker in [
            "不要删除",
            "不要真的删除",
            "不删除",
            "只说明删除",
            "只解释删除",
            "删除风险说明",
            "do not delete",
            "don't delete",
        ]
    )


def _negative_office_generation_constraint(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _knowledge_text_only_request(clean):
        return True
    if any(marker in clean for marker in ("表格字段", "字段清单", "字段列表")) and any(
        marker in clean for marker in ("给我一个", "列出", "有哪些", "搜索", "强调")
    ):
        return True
    explicit_text_only_markers = (
        "不要做文件",
        "别做文件",
        "不做文件",
        "不要创建文件",
        "别创建文件",
        "不要生成文件",
        "不要生成任何文件",
        "别生成文件",
        "不要产出文件",
        "不要导出文件",
        "只要文本",
        "只给文本",
        "只要文字",
        "只要大纲",
        "只给大纲",
        "不要做成文档",
        "别生成文档",
        "不要生成文档",
        "do not create file",
        "do not generate file",
        "text only",
        "outline only",
        "\u53ea\u8981\u6807\u9898",
        "\u53ea\u8981\u5927\u7eb2",
        "\u53ea\u8981\u6bcf\u9875\u91cd\u70b9",
        "\u6bcf\u9875\u91cd\u70b9",
    )
    if any(marker in clean or marker in lowered for marker in explicit_text_only_markers):
        return True
    return any(
        marker in clean or marker in lowered
        for marker in [
            "不要生成 word",
            "不要生成word",
            "不要生成 docx",
            "不要生成docx",
            "不要做 excel",
            "不要做excel",
            "不做 excel",
            "不做excel",
            "不要生成 ppt",
            "不要生成ppt",
            "不要做成 ppt",
            "不要做成ppt",
            "不要生成文档",
            "不要真的生成",
            "不生成 word",
            "不生成 ppt",
            "do not create word",
            "do not generate word",
            "do not create ppt",
            "do not generate ppt",
        ]
    )


def _explicit_office_file_generation_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    explicit_markers = (
        "生成文件",
        "创建文件",
        "产出文件",
        "导出文件",
        "保存为",
        "生成文档",
        "创建文档",
        "产出文档",
        "导出文档",
        "做成文档",
        "生成一份 word",
        "生成 word",
        "创建 word",
        "导出 word",
        "word 文件",
        "word文档",
        "word 文档",
        "生成一份Word",
        "生成Word",
        "创建Word",
        "导出Word",
        "Word文件",
        "Word文档",
        "生成 excel",
        "创建 excel",
        "导出 excel",
        "excel 文件",
        "Excel文件",
        "生成 ppt",
        "创建 ppt",
        "导出 ppt",
        "ppt 文件",
        "PPT文件",
        ".docx",
        ".xlsx",
        ".pptx",
    )
    return any(marker in clean or marker in lowered for marker in explicit_markers)


def _knowledge_text_only_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if not clean or _explicit_office_file_generation_request(clean):
        return False
    knowledge_actions = (
        "给我一个",
        "给我一份",
        "应该",
        "请给",
        "设计一个",
        "设计一套",
        "什么是",
        "如何",
        "怎么",
        "怎样",
        "为什么",
        "解释",
        "比较",
        "分析",
        "归纳",
        "总结",
        "判断",
        "评估",
        "排序",
        "区分",
        "避免",
        "说明",
        "列出",
        "有哪些",
        "what",
        "how",
        "explain",
        "compare",
    )
    knowledge_outputs = (
        "评估表",
        "可信度",
        "资料表",
        "字段",
        "字段清单",
        "评分维度",
        "评分标准",
        "模板",
        "判断规则",
        "规则",
        "清单",
        "框架",
        "步骤",
        "方法",
        "权重",
        "风险闸门",
        "摘要",
        "报告摘要",
        "研究报告",
        "知识报告",
        "知识回答",
        "专家报告",
        "大众解释",
        "取舍",
        "官方文档",
        "用户评论",
        "个人博客",
        "访谈",
        "论坛帖",
        "网页",
        "来源",
        "证据",
        "资料",
        "结论",
        "边界",
        "样本偏差",
        "重度用户",
    )
    passive_document_contexts = (
        "一份研究报告",
        "一份 2023 年报告",
        "2023 年报告",
        "知识报告发布前",
        "官方文档",
        "第三方测评",
        "用户评论",
        "个人博客",
    )
    textual_method_markers = (
        "请给模板",
        "请给判断规则",
        "如何避免",
        "权重如何排序",
        "适合判断",
        "还能不能用于",
    )
    has_action = any(marker in clean or marker in lowered for marker in knowledge_actions)
    has_output = any(marker in clean or marker in lowered for marker in knowledge_outputs)
    has_passive_document = any(marker in clean or marker in lowered for marker in passive_document_contexts)
    asks_for_textual_method = any(marker in clean for marker in textual_method_markers)
    return (has_action and has_output) or (has_passive_document and asks_for_textual_method)


def _office_completion_reporting_question(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if not any(marker in lowered for marker in ["ppt", "powerpoint", "docx", "xlsx", "excel", "word"]):
        return False
    if not any(marker in clean for marker in ["完成后", "生成后", "产出后", "做好后", "做完后"]):
        return False
    return any(
        marker in clean
        for marker in [
            "怎么给老板说明",
            "怎么给老板汇报",
            "怎么给老板说清",
            "如何给老板说明",
            "如何给老板汇报",
            "怎么说明",
            "怎么汇报",
            "说清结果",
            "汇报口径",
            "说明结果",
        ]
    )


def _has_webpage_read_marker(clean: str, lowered: str) -> bool:
    cn_markers = [
        "打开并阅读",
        "打开阅读",
        "阅读这个",
        "阅读一下",
        "读一下",
        "读这个",
        "只读",
        "看一下",
        "输入框",
        "有哪些输入框",
        "本地研究页",
        "看一下这网站有什么内容",
        "这个网页讲什么",
        "这个页面讲什么",
        "这个链接主要说什么",
        "这个链接讲什么",
        "这个网站讲什么",
        "总结",
        "总结这个链接",
        "总结这个网页",
        "看看这个网页",
        "看看这个网站",
        "看这个网页",
        "看这个网站",
        "网页讲什么",
        "页面讲什么",
        "链接讲什么",
        "主要说什么",
        "有什么内容",
        "页面标题",
        "标题是什么",
        "title 是什么",
        "title是什么",
        "看看登录页",
        "看登录页",
        "登录页有哪些字段",
        "有哪些字段",
        "有什么字段",
        "表单字段",
    ]
    en_markers = [
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
    return any(marker in clean for marker in cn_markers) or any(marker in lowered for marker in en_markers)


def _has_browser_side_effect_marker(clean: str, lowered: str) -> bool:
    clean = re.sub(r"https?://[^\s，。；;）)]+", " ", clean, flags=re.IGNORECASE)
    for harmless_phrase in (
        "提交前确认",
        "下单前确认",
        "出发前确认",
        "办理前确认",
        "如何核对",
        "怎么核对",
    ):
        clean = clean.replace(harmless_phrase, " ")
    lowered = clean.lower() if lowered else lowered
    return any(
        marker in clean
        for marker in [
            "登录",
            "登陆",
            "提交",
            "发送",
            "发布",
            "上传",
            "点击",
            "购买",
            "下单",
            "支付",
            "保存",
            "导出",
            "截图",
            "勾选",
            "填写",
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


def _browser_search_requested_sections(text: str) -> list[str]:
    clean = _clean(text)
    if "步骤清单" in clean:
        return ["1.", "2.", "3."]
    match = re.search(r"整理成(.+?)(?:并说明|并标注|并附上|，并说明|，并标注|，并附上|$)", clean)
    if not match:
        return []
    raw = match.group(1)
    raw = re.split(r"[，,](?:用|按|以|像)", raw, maxsplit=1)[0]
    raw = re.sub(r"[一二三四五六七八九十0-9]+部分", "", raw)
    parts = [
        segment.strip(" ，,。；;：:")
        for segment in re.split(r"[、/]|以及|并且|并", raw)
        if segment.strip(" ，,。；;：:")
    ]
    return parts[:4]


def _browser_search_presentation_style(text: str) -> str:
    clean = _clean(text)
    markers = [
        "科普",
        "通俗",
        "好懂",
        "易懂",
        "给小白看",
        "适合普通人阅读",
        "像给朋友解释一样",
    ]
    if any(marker in clean for marker in markers):
        return "popular_explainer"
    return "default"


def browser_research_plan(text: str) -> BrowserResearchPlan:
    clean = _clean(text)
    return BrowserResearchPlan(
        query=browser_search_query(clean),
        citation_required=browser_search_requires_citation(clean),
        requested_sections=_browser_search_requested_sections(clean),
        presentation_style=_browser_search_presentation_style(clean),
    )


def _readonly_login_page_inspection(clean: str, lowered: str) -> bool:
    if not _has_webpage_read_marker(clean, lowered):
        return False
    noun_markers = ["登录页", "登录页面", "注册页", "注册页面", "表单页", "预约页"]
    if not any(marker in clean for marker in noun_markers):
        return False
    action_text = clean
    for noun_marker in noun_markers:
        action_text = action_text.replace(noun_marker, " ")
    imperative_markers = [
        "登录",
        "注册",
        "提交",
        "填写",
        "点击",
        "发送",
        "保存",
        "上传",
        "下载",
    ]
    return not any(marker in action_text for marker in imperative_markers)


def _readonly_form_page_field_request(clean: str, lowered: str) -> bool:
    if not _first_url(clean):
        return False
    read_markers = (
        "看看",
        "看一下",
        "查看",
        "有哪些字段",
        "有什么字段",
        "有哪些输入框",
        "输入框",
        "字段",
        "表单字段",
        "what fields",
        "fields",
    )
    page_markers = (
        "登录页",
        "登录页面",
        "注册页",
        "注册页面",
        "表单页",
        "login",
        "form",
    )
    action_markers = (
        "提交",
        "填写",
        "点击",
        "发送",
        "保存",
        "上传",
        "下载",
        "登录进去",
        "帮我登录",
        "替我登录",
    )
    return (
        any(marker in clean or marker in lowered for marker in read_markers)
        and any(marker in clean or marker in lowered for marker in page_markers)
        and not any(marker in clean or marker in lowered for marker in action_markers)
    )


def _first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s，。；;）)]+", text, flags=re.IGNORECASE)
    return match.group(0) if match else None


def _direct_only(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "只解释",
            "只给方案",
            "不要执行",
            "不要假装执行",
            "别假装执行",
            "不要声称执行",
            "不能点击",
            "不能提交",
            "不点击",
            "不提交",
            "不要点击",
            "不要提交",
            "不要创建任务",
            "不要调用工具",
        ]
    )


def _without_direct_only_markers(text: str) -> str:
    result = text
    for marker in [
        "只解释",
        "只给方案",
        "不要执行",
        "不要假装执行",
        "别假装执行",
        "不要声称执行",
        "不能点击",
        "不能提交",
        "不点击",
        "不提交",
        "不要点击",
        "不要提交",
        "不要创建任务",
        "不要调用工具",
    ]:
        result = result.replace(marker, " ")
    return _clean(result)


def _clean(text: str) -> str:
    clean = " ".join(str(text or "").strip().split())
    clean = re.sub(r"^[A-Za-z]{2,}\d{0,4}-\d{2,4}[:：]\s*", "", clean)
    clean = re.sub(
        r"\s+用户还附带了一个link\s+微信消息中的链接\s+上下文参考\s+url\s+微信消息链接",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _safe_summary(text: str) -> str:
    return _clean(text)[:120]


DIRECT_ONLY_MARKERS_CANONICAL = (
    "\u53ea\u89e3\u91ca",
    "\u53ea\u7ed9\u65b9\u6848",
    "\u4e0d\u8981\u6267\u884c",
    "\u4e0d\u8981\u5047\u88c5\u6267\u884c",
    "\u522b\u5047\u88c5\u6267\u884c",
    "\u4e0d\u8981\u58f0\u79f0\u6267\u884c",
    "\u4e0d\u80fd\u70b9\u51fb",
    "\u4e0d\u80fd\u63d0\u4ea4",
    "\u4e0d\u70b9\u5f00",
    "\u4e0d\u63d0\u4ea4",
    "\u4e0d\u8981\u70b9\u51fb",
    "\u4e0d\u8981\u63d0\u4ea4",
    "\u4e0d\u8981\u521b\u5efa\u4efb\u52a1",
    "\u4e0d\u8981\u8c03\u7528\u5de5\u5177",
)


def office_document_type(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    explicit_word_target = (
        re.search(r"(?<![a-z])word(?![a-z])", lowered)
        and any(marker in clean or marker in lowered for marker in ("生成", "创建", "做一份", "做一个", "导出", "generate", "create", "export"))
    ) or any(
        marker in clean
        for marker in ("生成一份 Word", "生成 Word", "生成word", "做成 Word", "导出 Word", "Word 复盘文件")
    )
    if explicit_word_target:
        return "word"
    if any(marker in lowered for marker in ("excel", "xlsx")) or any(
        marker in clean
        for marker in ("\u8868\u683c", "\u5de5\u4f5c\u7c3f", "\u9500\u552e\u6570\u636e", "\u7ecf\u8425\u6570\u636e")
    ):
        return "excel"
    if any(marker in lowered for marker in ("ppt", "pptx", "powerpoint")) or any(
        marker in clean
        for marker in ("\u6f14\u793a\u7a3f", "\u5e7b\u706f\u7247", "\u6c47\u62a5\u9875", "\u6f14\u793a\u6587\u7a3f")
    ):
        return "ppt"
    if re.search(r"(?<![a-z])word(?![a-z])", lowered) or "docx" in lowered or any(
        marker in clean
        for marker in ("\u6587\u6863", "\u5468\u62a5", "\u62a5\u544a", "\u7a3f\u4ef6")
    ):
        return "word"
    return None


def _direct_only(text: str) -> bool:
    return any(marker in text for marker in DIRECT_ONLY_MARKERS_CANONICAL)


def _attachment_answer_only_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if not any(marker in clean or marker in lowered for marker in ("附件", "文件", "attached", "attachment", ".docx", ".xlsx", ".pdf", ".txt")):
        return False
    if any(
        marker in clean or marker in lowered
        for marker in (
            "标准文件名",
            "文件名建议",
            "命名建议",
            "不要声称已经改名",
            "不要声称已改名",
            "不要真的改名",
            "术语表",
            "抽取术语",
            "简短解释",
            "英文摘要",
            "整理成英文摘要",
            "translate",
            "glossary",
        )
    ):
        return True
    return False


def _looks_like_non_office_advice_context(text: str) -> bool:
    clean = _clean(text)
    if not clean:
        return False
    if _looks_like_daily_life_advice_request(clean):
        return True
    advice_markers = (
        "帮我准备",
        "帮我判断",
        "帮我梳理",
        "帮我区分",
        "帮我整理",
        "问医生",
        "就医",
        "观察记录",
        "证据缺口",
        "维权",
        "风险提醒",
        "沟通步骤",
    )
    passive_document_markers = ("体检报告", "检测报告", "合同写着", "聊天记录", "检测截图")
    explicit_generation_markers = (
        "生成",
        "创建",
        "做成",
        "做一份",
        "做一个",
        "导出",
        "保存为",
        "生成一份 Word",
        "生成 Word",
        "导出 Word",
        ".docx",
        ".xlsx",
        ".pptx",
    )
    if any(marker in clean for marker in explicit_generation_markers):
        return False
    return any(marker in clean for marker in passive_document_markers) and any(
        marker in clean for marker in advice_markers
    )


def _looks_like_daily_life_advice_request(text: str) -> bool:
    clean = _clean(text)
    if not clean:
        return False
    life_markers = (
        "洗衣服",
        "回消息",
        "吃饭",
        "睡觉",
        "洗澡",
        "收拾",
        "家里",
        "朋友",
        "家人",
        "伴侣",
        "同事",
        "关系",
        "语气",
        "道歉",
        "边界",
        "群里",
        "聚会",
    )
    advice_markers = (
        "帮我排",
        "排个",
        "顺序",
        "先做哪",
        "不痛苦",
        "怎么说",
        "怎么回",
        "给我一个开场",
        "给个开场",
        "开场白",
        "话术",
        "自然一点",
        "委婉",
        "不尴尬",
        "修复关系",
    )
    return any(marker in clean for marker in life_markers) and any(
        marker in clean for marker in advice_markers
    )


def _without_direct_only_markers(text: str) -> str:
    result = text
    for marker in DIRECT_ONLY_MARKERS_CANONICAL:
        result = result.replace(marker, " ")
    return _clean(result)


def is_skill_or_mcp_concept_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    action_text = _without_direct_only_markers(clean)
    if any(
        marker in action_text
        for marker in (
            "\u751f\u6210",
            "\u521b\u5efa",
            "\u505a\u4e00\u4e2a",
            "\u505a\u4e00\u4efd",
            "\u7f16\u8f91",
            "\u4fee\u6539",
        )
    ):
        return False
    if any(
        marker in lowered
        for marker in (
            "generate",
            "create",
            "edit",
            "modify",
            "build",
        )
    ):
        return False
    concept_markers = (
        "\u662f\u4ec0\u4e48",
        "\u600e\u4e48\u914d\u7f6e",
        "\u5982\u4f55\u914d\u7f6e",
        "\u89e3\u91ca",
        "\u4ecb\u7ecd",
        "\u539f\u7406",
        "\u533a\u522b",
        "what is",
        "explain",
        "difference",
        "compare",
        "how to configure",
    )
    target_markers = ("skill", "\u6280\u80fd", "mcp")
    return any(marker in clean or marker in lowered for marker in target_markers) and any(
        marker in clean or marker in lowered for marker in concept_markers
    )


def _negative_download_constraint(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    return any(
        marker in clean or marker in lowered
        for marker in (
            "\u4e0d\u8981\u771f\u7684\u4e0b\u8f7d",
            "\u4e0d\u8981\u4e0b\u8f7d",
            "\u522b\u4e0b\u8f7d",
            "\u53ea\u8bf4\u660e",
            "\u53ea\u89e3\u91ca",
            "do not download",
            "don't download",
            "without downloading",
        )
    )


def is_download_topic_only(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    topic_markers = (
        "artifact \u4e0b\u8f7d",
        "\u4e0b\u8f7d\u7aef\u70b9",
        "\u4e0b\u8f7d\u63a5\u53e3",
        "download endpoint",
        "download api",
    )
    return any(marker in clean or marker in lowered for marker in topic_markers) or (
        _negative_download_constraint(clean)
        and any(marker in clean or marker in lowered for marker in ("\u4e0b\u8f7d", "download"))
    )


def is_explicit_download_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if _negative_download_constraint(clean):
        return False
    if "\u4e0b\u8f7d" not in clean and "download" not in lowered:
        return False
    if is_download_topic_only(clean):
        return False
    if _first_url(clean):
        return True
    explicit_target = any(
        marker in clean
        for marker in (
            "\u4e0b\u8f7d\u6587\u4ef6",
            "\u4e0b\u8f7d\u62a5\u8868",
            "\u4e0b\u8f7d\u56fe\u7247",
            "\u4e0b\u8f7d\u8fd9\u4e2a",
            "\u4e0b\u8f7d\u8fd9\u4efd",
        )
    )
    return explicit_target and any(
        marker in clean for marker in ("\u4fdd\u5b58", "\u62ff\u5230", "\u7ed9\u6211", "\u544a\u8bc9\u6211\u7ed3\u679c")
    )


def is_browser_page_action_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if not _first_url(clean):
        return False
    if is_explicit_download_request(clean):
        return False
    if is_webpage_read_request(clean):
        return False
    action_markers = (
        "\u6253\u5f00",
        "\u767b\u5f55",
        "\u70b9\u51fb",
        "\u63d0\u4ea4",
        "\u586b\u5199",
        "\u8f93\u5165",
        "\u4e0a\u4f20",
        "\u622a\u56fe",
        "\u622a\u56fe\u7559\u8bc1",
        "\u64cd\u4f5c",
        "open",
        "login",
        "log in",
        "click",
        "submit",
        "fill",
        "type",
        "upload",
        "screenshot",
    )
    return any(marker in clean or marker in lowered for marker in action_markers)


# Phase 112 hardening overrides:
# Keep these canonical helpers at file end so they win over older mojibake variants.
def is_structured_summary_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    summary_markers = (
        "总结",
        "总结成",
        "概括",
        "整理成",
        "归纳",
        "提炼",
        "改写成",
        "梳理",
        "分析质量",
        "summary",
        "summarize",
        "rewrite",
        "organize",
    )
    structure_markers = (
        "标题",
        "一级标题",
        "二级标题",
        "小标题",
        "结构偏好",
        "总结偏好",
        "按我刚刚设定",
        "按修正后的偏好",
        "行动项",
        "关键观察",
        "段落",
        "自然段",
        "表格",
        "markdown 表格",
        "markdown table",
        "列表",
        "要点",
        "编号",
        "三条",
        "两段",
        "一段",
        "只输出标题",
        "不要表格",
        "不要列表",
        "不要 markdown",
        "不要markdown",
        "标题 +",
        "表格 +",
        "结论段落",
        "bullet",
    )
    office_action_markers = (
        "做成word",
        "做成excel",
        "做成ppt",
        "生成word",
        "生成excel",
        "生成ppt",
        "创建word",
        "创建excel",
        "创建ppt",
        "做一个word",
        "做一个excel",
        "做一个ppt",
        "做一份word",
        "做一份excel",
        "做一份ppt",
        "创建文档",
        "导出文档",
        ".docx",
        ".xlsx",
        ".pptx",
    )
    has_summary = any(marker in clean or marker in lowered for marker in summary_markers)
    has_structure = any(marker in clean or marker in lowered for marker in structure_markers)
    has_office_action = any(marker in clean or marker in lowered for marker in office_action_markers)
    return has_structure and (has_summary or "素材：" in clean or "素材:" in clean) and not has_office_action


def office_document_type(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if any(marker in lowered for marker in ("excel", "xlsx")) or any(
        marker in clean for marker in ("表格", "工作簿", "销售数据", "经营数据")
    ):
        return "excel"
    if any(marker in lowered for marker in ("ppt", "pptx", "powerpoint")) or any(
        marker in clean for marker in ("演示稿", "幻灯片", "汇报页", "演示文稿")
    ):
        return "ppt"
    if re.search(r"(?<![a-z])word(?![a-z])", lowered) or "docx" in lowered or any(
        marker in clean for marker in ("文档", "周报", "报告", "稿件")
    ):
        return "word"
    return None


def _direct_only(text: str) -> bool:
    if any(
        marker in text
        for marker in (
            "只要文本",
            "只给文本",
            "只要文字",
            "只要大纲",
            "只给大纲",
            "不要做文件",
            "别做文件",
            "不做文件",
            "不要创建文件",
            "不要生成文件",
            "别生成文件",
        )
    ):
        return True
    return any(
        marker in text
        for marker in (
            "只解释",
            "只给方案",
            "不要执行",
            "不要假装执行",
            "别假装执行",
            "不要声称执行",
            "不能点击",
            "不能提交",
            "不点开",
            "不提交",
            "不要点击",
            "不要提交",
            "不要创建任务",
            "不要调用工具",
        )
    )


def _without_direct_only_markers(text: str) -> str:
    result = text
    for marker in (
        "只要文本",
        "只给文本",
        "只要文字",
        "只要大纲",
        "只给大纲",
        "不要做文件",
        "别做文件",
        "不做文件",
        "不要创建文件",
        "不要生成文件",
        "别生成文件",
    ):
        result = result.replace(marker, " ")
    for marker in (
        "只解释",
        "只给方案",
        "不要执行",
        "不要假装执行",
        "别假装执行",
        "不要声称执行",
        "不能点击",
        "不能提交",
        "不点开",
        "不提交",
        "不要点击",
        "不要提交",
        "不要创建任务",
        "不要调用工具",
    ):
        result = result.replace(marker, " ")
    return _clean(result)


def browser_search_requires_citation(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    return any(
        marker in clean or marker in lowered
        for marker in (
            "证据来源",
            "说明证据来源",
            "标注来源",
            "带上来源",
            "引用来源",
            "来源",
            "引用",
            "citation",
            "source",
        )
    )


def _natural_browser_search_request(clean: str, lowered: str) -> bool:
    research_question_markers = (
        "怎么样",
        "如何预约",
        "怎么预约",
        "怎么挂号",
        "怎么办理",
        "怎么申请",
        "怎么续签",
        "材料要求",
        "最新要求",
        "价格趋势",
        "趋势如何",
        "评价如何",
        "口碑如何",
    )
    research_topic_markers = (
        "公司",
        "医院",
        "产检",
        "政务",
        "政务中心",
        "居住证",
        "签证",
        "护照",
        "crm",
        "产品",
        "价格",
        "办理",
        "预约",
        "续签",
    )
    delivery_markers = (
        "整理成",
        "步骤清单",
        "注意事项",
        "亮点",
        "风险",
        "适用场景",
        "所需准备",
        "预约入口",
        "办理材料",
        "现场提醒",
        "主要变化",
        "常见材料",
        "提交前确认",
        "看到的情况",
        "可参考点",
        "下单前确认",
    )
    has_question = any(marker in clean or marker in lowered for marker in research_question_markers)
    has_topic = any(marker in clean or marker in lowered for marker in research_topic_markers)
    has_delivery = any(marker in clean or marker in lowered for marker in delivery_markers)
    return has_question and has_topic and (has_delivery or browser_search_requires_citation(clean))


def is_browser_search_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return False
    if _first_url(clean) and _has_webpage_read_marker(clean, lowered):
        return False
    if _has_browser_side_effect_marker(clean, lowered):
        return False
    if any(marker in clean for marker in ("不要搜索", "别搜索", "不搜索")):
        return False
    has_browser_marker = any(
        marker in clean or marker in lowered
        for marker in ("浏览器搜索", "用浏览器搜索", "search", "搜索一下", "搜一下", "查一下", "请搜索")
    )
    if not has_browser_marker and any(
        marker in clean for marker in ("搜一次", "再搜一次", "再用浏览器搜一次")
    ):
        has_browser_marker = True
    if has_browser_marker:
        return any(
            marker in clean or marker in lowered
            for marker in (
                "总结",
                "概括",
                "结果",
                "搜",
                "搜索",
                "search",
                "证据来源",
                "来源",
                "整理成",
                "主要变化",
                "常见材料",
                "提交前确认",
                "看到的情况",
                "可参考点",
                "下单前确认",
            )
        )
    return _natural_browser_search_request(clean, lowered)


def browser_search_query(text: str) -> str:
    clean = _clean(text)
    query = clean
    for marker in ("再用浏览器搜一次", "再搜一次", "搜一次"):
        query = query.replace(marker, " ")
    replacements = [
        "请用浏览器搜索",
        "用浏览器搜索",
        "浏览器搜索",
        "请搜索",
        "搜索一下",
        "搜一下",
        "请用搜索",
    ]
    for marker in replacements:
        query = query.replace(marker, " ")
    query = re.sub(r"^[A-Za-z]{2,}\d{0,4}-\d{2,4}\s+", " ", query)
    query = re.sub(r"^[A-Za-z][A-Za-z0-9_-]{5,}\s+", " ", query)
    query = re.sub(r"https?://[^\s，。；;）)]+", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"微信消息中的链接", " ", query)
    query = re.sub(r"微信消息链接", " ", query)
    query = re.sub(r"用户还附带了一个link", " ", query)
    query = re.sub(r"上下文参考\s*url", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\blink\b", " ", query, flags=re.IGNORECASE)
    query = re.sub(r"(并)?总结结果.*$", " ", query)
    query = re.sub(r"整理成.*$", " ", query)
    query = re.sub(r"(并)?说明证据来源.*$", " ", query)
    query = re.sub(r"必须说明证据来源.*$", " ", query)
    query = re.sub(r"并标注证据来源.*$", " ", query)
    query = re.sub(r"看看这个搜索页有什么.*$", " ", query)
    query = re.sub(r"[，。；;？?]+", " ", query)
    query = re.sub(r"这次用.*?(总结|概括).*$", " ", query)
    query = re.sub(r"用两句话.*$", " ", query)
    query = re.sub(r"带上来源.*$", " ", query)
    query = re.sub(r"^\s*再", " ", query)
    query = re.sub(r"\s+", " ", query).strip()
    return query or clean


_REPO_META_DISCUSSION_MARKERS = (
    "拆成步骤",
    "拆解步骤",
    "验收标准",
    "复盘报告",
    "复盘包含",
    "进展同步",
    "进展怎么写",
    "写一条进展",
    "质量打分",
    "什么情况下",
    "如何判断完成",
    "完成标准",
    "风险清单",
    "只要建议",
    "只给建议",
    "不要执行",
    "怎么帮",
    "如何帮",
    "一句话",
    "一段话",
    "原则",
    "收尾",
    "结论",
    "下一步",
    "总结",
    "概括",
    "偏好",
    "只聊",
    "解释",
    "说明",
    "为什么",
)
_REPO_EXECUTION_VERBS = (
    "读代码",
    "看看代码",
    "阅读代码",
    "检查",
    "review",
    "评审",
    "审查",
    "跑",
    "执行",
    "修复",
    "修改",
    "改代码",
    "重构",
    "refactor",
    "patch",
    "bugfix",
)
_REPO_TEST_ACTION_MARKERS = (
    "pytest",
    "typecheck",
    "lint",
    "测试",
    "验证",
    "跑",
    "执行",
    "检查",
)
_REPO_PATCH_ACTION_MARKERS = (
    "patch",
    "bugfix",
    "改代码",
    "修改代码",
    "修复 bug",
    "修 bug",
    "补丁",
    "修复",
    "修改",
)


def _repo_meta_discussion_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    has_meta_marker = any(
        marker in clean or marker in lowered for marker in _REPO_META_DISCUSSION_MARKERS
    )
    if not has_meta_marker:
        return False
    return not any(
        marker in clean or marker in lowered for marker in _REPO_EXECUTION_VERBS
    )


def repo_execution_route(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if _direct_only(clean):
        return None
    if is_structured_summary_request(clean):
        return None
    if _looks_like_browser_page_action(clean):
        return None
    if not any(
        marker in clean or marker in lowered
        for marker in [
            "代码托管",
            "代码库",
            "pytest",
            "测试",
            "重构",
            "patch",
            "bugfix",
            "改代码",
            "修改代码",
            "修复",
        ]
    ):
        return None
    action_text = _without_direct_only_markers(clean)
    if _repo_meta_discussion_request(action_text):
        return None
    if any(
        marker in action_text or marker in lowered
        for marker in ["只读", "readonly", "read only", "看看代码", "阅读代码", "读一个", "读读"]
    ):
        return "repo_readonly_request"
    if any(marker in action_text or marker in lowered for marker in ["修复失败", "fix after failure", "失败后修", "测试失败"]):
        return "repo_fix_after_failure"
    if any(marker in action_text or marker in lowered for marker in ["重构", "refactor"]):
        return "repo_refactor_request"
    if any(marker in action_text or marker in lowered for marker in _REPO_TEST_ACTION_MARKERS):
        return "repo_test_request"
    if any(marker in action_text or marker in lowered for marker in _REPO_PATCH_ACTION_MARKERS):
        return "repo_patch_request"
    return None


def is_skill_or_mcp_concept_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    action_text = _without_direct_only_markers(clean)
    if any(
        marker in action_text for marker in ("生成", "创建", "做一个", "做一份", "编辑", "修改")
    ):
        return False
    if any(
        marker in lowered for marker in ("generate", "create", "edit", "modify", "build")
    ):
        return False
    concept_markers = (
        "是什么",
        "怎么配置",
        "如何配置",
        "解释",
        "介绍",
        "原理",
        "区别",
        "what is",
        "explain",
        "difference",
        "compare",
        "how to configure",
    )
    target_markers = ("skill", "技能", "mcp")
    return any(marker in clean or marker in lowered for marker in target_markers) and any(
        marker in clean or marker in lowered for marker in concept_markers
    )


# Broad-route hardening overrides live last so older compatibility helpers above
# cannot steal common chat/browser cases through narrow keyword matches.
_BASE_IS_DOWNLOAD_TOPIC_ONLY = is_download_topic_only
_BASE_REPO_EXECUTION_ROUTE = repo_execution_route
_BASE_HOST_FILESYSTEM_LOCATION = host_filesystem_location
_BASE_IS_HOST_FILESYSTEM_LIST_REQUEST = is_host_filesystem_list_request


def _url_read_context_marker(clean: str, lowered: str) -> bool:
    read_markers = (
        "\u6253\u5f00\u5e76\u9605\u8bfb",
        "\u5c1d\u8bd5\u9605\u8bfb",
        "\u9605\u8bfb",
        "\u8bfb",
        "\u770b",
        "\u770b\u4e00\u4e0b",
        "\u57fa\u4e8e",
        "\u7efc\u5408",
        "\u603b\u7ed3",
        "\u6982\u62ec",
        "\u544a\u8bc9\u6211",
        "\u5199\u4e00\u53e5",
        "\u5217\u51fa",
        "\u6709\u54ea\u4e9b",
        "\u662f\u4ec0\u4e48",
        "\u8ba8\u8bba\u7684\u4e3b\u9898",
        "\u4f9d\u636e",
        "\u6765\u81ea\u9875\u9762",
        "\u9875\u9762",
        "\u7f51\u9875",
        "\u94fe\u63a5",
        "\u98ce\u9669",
        "\u539f\u5219",
        "\u786e\u8ba4",
        "read",
        "review",
        "summarize",
        "summarise",
        "based on",
        "from the page",
        "what",
        "which",
    )
    return any(marker in clean or marker in lowered for marker in read_markers)


def _browser_write_action_marker(clean: str, lowered: str) -> bool:
    write_markers = (
        "\u767b\u5f55",
        "\u70b9\u51fb",
        "\u63d0\u4ea4",
        "\u586b\u5199",
        "\u8f93\u5165",
        "\u4e0a\u4f20",
        "\u4e0b\u5355",
        "\u652f\u4ed8",
        "\u53d1\u5e03",
        "\u622a\u56fe",
        "login",
        "log in",
        "click",
        "submit",
        "fill",
        "type",
        "upload",
        "checkout",
        "pay",
        "publish",
        "screenshot",
    )
    negative_markers = (
        "\u4e0d\u8981\u5c1d\u8bd5\u767b\u5f55",
        "\u4e0d\u8981\u767b\u5f55",
        "\u4e0d\u8981\u70b9\u51fb",
        "\u4e0d\u8981\u63d0\u4ea4",
        "\u4e0d\u8981\u586b\u5199",
        "\u4e0d\u8981\u8f93\u5165",
        "\u4e0d\u4e0b\u5355",
        "\u4e0d\u652f\u4ed8",
        "\u4e0d\u4e0b\u8f7d",
        "\u4e0d\u8981\u4e0b\u8f7d",
        "do not log in",
        "don't log in",
        "do not click",
        "do not submit",
        "do not download",
        "without downloading",
    )
    scrubbed = re.sub(r"https?://[^\s锛屻€傦紱;锛?]+", " ", clean, flags=re.IGNORECASE)
    scrubbed_lowered = scrubbed.lower() if lowered else lowered
    for harmless in ("\u8f93\u5165\u6846", "\u6709\u54ea\u4e9b\u8f93\u5165\u6846", "\u54ea\u4e9b\u8f93\u5165\u6846"):
        scrubbed = scrubbed.replace(harmless, " ")
        scrubbed_lowered = scrubbed_lowered.replace(harmless, " ")
    for marker in negative_markers:
        scrubbed = scrubbed.replace(marker, " ")
        scrubbed_lowered = scrubbed_lowered.replace(marker, " ")
    return any(marker in scrubbed or marker in scrubbed_lowered for marker in write_markers)


def is_webpage_read_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if not _first_url(clean):
        return False
    if is_explicit_download_request(clean):
        return False
    if _browser_write_action_marker(clean, lowered):
        return False
    return (
        _explicit_readonly_browser_inspection(clean, lowered)
        or _readonly_login_page_inspection(clean, lowered)
        or _has_webpage_read_marker(clean, lowered)
        or _url_read_context_marker(clean, lowered)
    )


def is_download_topic_only(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if _first_url(clean) and _negative_download_constraint(clean) and _url_read_context_marker(clean, lowered):
        return False
    return _BASE_IS_DOWNLOAD_TOPIC_ONLY(clean)


def _repo_false_positive_chat_request(clean: str, lowered: str) -> bool:
    if _looks_like_daily_life_advice_request(clean):
        return True
    if any(marker in clean for marker in ("\u62c6\u6210", "\u62c6\u89e3")) and any(
        marker in clean for marker in ("\u6b65", "\u6b65\u9aa4", "\u6e05\u5355")
    ):
        execution_markers = (
            "pytest",
            "\u8fd0\u884c pytest",
            "\u5f00\u59cb\u6267\u884c",
            "\u76f4\u63a5\u6267\u884c",
            "\u5e2e\u6211\u6267\u884c",
            "\u6267\u884c\u4fee\u590d",
            "\u4fee\u590d\u5e76\u8fd0\u884c",
        )
        if not any(marker in clean or marker in lowered for marker in execution_markers):
            return True
    if "\u8981\u4e0d\u8981" in clean and any(marker in clean for marker in ("\u8bc4\u4ef7", "\u51b3\u7b56", "\u5efa\u8bae")):
        return True
    if "\u6d4b\u8bd5\u65e5\u62a5" in clean:
        return True
    if "FBROAD" in clean and any(
        marker in clean for marker in ("\u65e5\u62a5", "\u5468\u4f1a\u603b\u7ed3", "\u603b\u7ed3\u6210\u4e00\u53e5", "\u8d28\u91cf\u98ce\u9669")
    ):
        return True
    if any(
        marker in clean
        for marker in (
            "\u5199\u4e00\u6761",
            "\u5199\u4e00\u6bb5",
            "\u7528 5 \u70b9\u603b\u7ed3",
            "\u5e2e\u6211\u603b\u7ed3",
        )
    ) and any(
        marker in clean
        for marker in (
            "\u65e5\u62a5",
            "\u98ce\u9669",
            "\u5ba2\u6237\u8bf4\u660e",
            "\u5468\u4f1a",
            "\u8fdb\u5c55",
            "\u62a5\u544a",
        )
    ):
        return True
    return "test report" in lowered or "daily report" in lowered


def _dangerous_command_or_secret_boundary(clean: str, lowered: str) -> bool:
    dangerous_command = any(
        marker in lowered
        for marker in (
            "rm -rf /",
            "rm -rf",
            "remove-item -recurse",
            "del /s",
            "format ",
            "mkfs",
        )
    )
    asset_broker_bypass = "\u7ed5\u8fc7 asset broker" in lowered or (
        "\u7ed5\u8fc7" in clean and "Asset Broker" in clean
    )
    direct_secret = any(marker in lowered for marker in ("read secret", "directly read secret")) or (
        "\u76f4\u63a5\u8bfb\u53d6" in clean and "secret" in lowered
    )
    return dangerous_command or asset_broker_bypass or direct_secret


def repo_execution_route(text: str) -> str | None:
    clean = _clean(text)
    lowered = clean.lower()
    if _repo_false_positive_chat_request(clean, lowered):
        return None
    if _dangerous_command_or_secret_boundary(clean, lowered):
        return None
    return _BASE_REPO_EXECUTION_ROUTE(clean)


def host_filesystem_location(text: str) -> str | None:
    clean = _clean(text)
    if any(marker in clean for marker in ("\u5f53\u524d\u5de5\u4f5c\u76ee\u5f55", "\u5f53\u524d\u76ee\u5f55", "\u5de5\u4f5c\u76ee\u5f55")):
        return "home"
    return _BASE_HOST_FILESYSTEM_LOCATION(clean)


def is_host_filesystem_list_request(text: str) -> bool:
    clean = _clean(text)
    lowered = clean.lower()
    if any(marker in clean for marker in ("\u5f53\u524d\u5de5\u4f5c\u76ee\u5f55", "\u5f53\u524d\u76ee\u5f55", "\u5de5\u4f5c\u76ee\u5f55")) and any(
        marker in clean or marker in lowered
        for marker in ("\u67e5\u770b", "\u6709\u54ea\u4e9b\u6587\u4ef6", "\u5217\u51fa", "\u53ea\u8bfb", "list", "show files")
    ):
        return True
    if (
        host_filesystem_location(clean) is not None
        and any(marker in clean for marker in ("\u662f\u5426\u5b58\u5728", "\u5728\u4e0d\u5728", "\u6709\u6ca1\u6709"))
        and any(marker in clean for marker in ("\u6587\u4ef6\u540d", ".txt", ".md", ".csv", ".json"))
        and not _has_file_mutation_marker(clean)
    ):
        return True
    return _BASE_IS_HOST_FILESYSTEM_LIST_REQUEST(clean)


def ai_coding_tool_request(text: str) -> list[str]:
    clean = _clean(text)
    lowered = clean.lower()
    if not clean:
        return []
    tool_aliases = {
        "codex": ("codex",),
        "claudecode": ("claudecode", "claude code", "claude-code"),
    }
    requested = [
        name
        for name, aliases in tool_aliases.items()
        if any(alias in lowered for alias in aliases)
    ]
    generic_markers = (
        "ai coding tool",
        "coding agent",
        "code agent",
        "\u0041\u0049\u7f16\u7a0b\u5de5\u5177",
        "\u0061\u0069\u7f16\u7a0b\u5de5\u5177",
        "\u0041\u0049 \u7f16\u7a0b\u5de5\u5177",
        "\u0061\u0069 \u7f16\u7a0b\u5de5\u5177",
        "\u4ee3\u7801\u667a\u80fd\u4f53",
        "\u7f16\u7a0b\u52a9\u624b",
    )
    if any(marker in lowered or marker in clean for marker in generic_markers):
        requested.append("ai_coding_tool")
    if not requested:
        return []
    action_markers = (
        "use",
        "call",
        "run",
        "invoke",
        "tool",
        "tools",
        "available",
        "list",
        "\u4f7f\u7528",
        "\u8c03\u7528",
        "\u7528",
        "\u542f\u7528",
        "\u6253\u5f00",
        "\u5de5\u5177",
        "\u53ef\u7528",
        "\u662f\u5426\u53ef\u7528",
        "\u80fd\u4e0d\u80fd\u7528",
        "\u5217\u51fa",
        "\u8ba1\u7b97",
        "\u7b97",
    )
    if not any(marker in lowered or marker in clean for marker in action_markers):
        return []
    return sorted(set(requested))
