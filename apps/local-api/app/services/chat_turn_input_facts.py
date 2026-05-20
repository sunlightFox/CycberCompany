from __future__ import annotations


def looks_like_explicit_continuation(text: str) -> bool:
    raw = str(text or "")
    return any(
        marker in raw
        for marker in (
            "继续刚才",
            "接着刚才",
            "顺着刚才",
            "沿着刚才",
            "继续上一条",
            "接上刚才",
            "继续那个方案",
            "补充指标",
            "接着说",
        )
    )


def looks_like_execution_state_explanation_request(user_text: str) -> bool:
    raw = str(user_text or "").strip()
    if not raw:
        return False
    if any(
        marker in raw
        for marker in (
            "为什么还没做",
            "怎么还没执行",
            "现在是什么状态",
            "卡在哪",
            "为什么没有继续",
            "为什么没往下做",
            "还差什么",
            "为什么停住了",
            "不要说已完成",
            "不要伪称完成",
            "还没真正执行",
            "等什么证据",
            "要等什么证据",
        )
    ):
        return True
    return (
        ("?" in raw or "？" in raw)
        and any(marker in raw for marker in ("状态", "进度", "证据", "完成", "执行", "任务", "步骤"))
    )


def looks_like_plain_analysis_request(text: str) -> bool:
    raw = str(text or "")
    if looks_like_execution_state_explanation_request(raw):
        return True
    action_markers = (
        "执行",
        "安装",
        "删除",
        "下载",
        "打开网站",
        "打开网页",
        "调用工具",
        "帮我操作",
    )
    negative_prefixes = (
        "不要",
        "别",
        "无需",
        "不用",
        "先别",
        "只做分析，不要",
        "只给方案，不要",
    )
    has_positive_action_marker = False
    for marker in action_markers:
        if marker not in raw:
            continue
        marker_index = raw.find(marker)
        prefix = raw[max(0, marker_index - 6) : marker_index]
        if any(neg in prefix for neg in negative_prefixes):
            continue
        has_positive_action_marker = True
        break
    return any(
        marker in raw
        for marker in ("分析", "对比", "比较", "解释", "设计", "方案", "验收", "模板", "讨论", "优化")
    ) and not has_positive_action_marker


def looks_like_latest_instruction_override(text: str) -> bool:
    raw = str(text or "").strip()
    return any(
        marker in raw for marker in ("停，改成", "停 改成", "改成只", "改成先", "不要再", "先别", "只讨论", "只做")
    )


def needs_recent_history_lookup(text: str) -> bool:
    raw = str(text or "")
    if looks_like_latest_instruction_override(raw):
        return False
    return any(
        marker in raw
        for marker in ("刚才", "前面", "上一条", "上个", "偏好", "顺序", "优先级", "记住", "继续", "接着")
    )


def looks_like_short_followup(text: str) -> bool:
    raw = str(text or "").strip()
    if looks_like_latest_instruction_override(raw):
        return False
    compact = raw.replace(" ", "")
    if not compact or len(compact) > 24:
        return False
    return any(
        compact.startswith(marker) or marker in compact
        for marker in ("再", "继续", "接着", "说", "展开", "改短", "改得", "改成", "按", "保持", "加一", "加个")
    )


def strict_format_chat_request(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "只输出json",
            "只输出 json",
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
            "table only",
            "只返回代码",
            "code only",
            "only code",
        )
    )


def structured_summary_chat_request(text: str) -> bool:
    raw = str(text or "")
    lowered = raw.lower()
    summary_markers = (
        "总结",
        "概括",
        "整理成",
        "归纳",
        "提炼",
        "梳理",
        "summary",
        "summarize",
        "rewrite",
        "organize",
        "summarize into",
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
        "段落",
        "自然段",
        "表格",
        "markdown 表格",
        "markdown table",
        "列表",
        "要点",
        "编号",
        "只输出标题",
        "不要表格",
        "不要列表",
        "两段",
        "一段",
        "三条",
        "bullet",
        "heading",
        "title only",
        "paragraph",
        "paragraphs",
        "numbered list",
    )
    office_action_markers = (
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
    has_summary = any(marker in raw or marker in lowered for marker in summary_markers)
    has_structure = any(marker in raw or marker in lowered for marker in structure_markers)
    has_office_action = any(marker in raw or marker in lowered for marker in office_action_markers)
    return has_structure and (has_summary or "素材：" in raw or "素材:" in raw) and not has_office_action


def preference_application_request(text: str) -> bool:
    raw = str(text or "").strip()
    lowered = raw.lower()
    if not raw or "偏好" not in raw:
        return False
    if structured_summary_chat_request(raw):
        return True
    has_apply_phrase = any(
        marker in raw or marker in lowered
        for marker in (
            "按我刚刚设定",
            "按修正后的偏好",
            "按我后来说的",
            "按这个偏好",
            "按这个口径",
            "按先",
            "按后",
            "follow my preference",
        )
    )
    has_generation_target = any(
        marker in raw or marker in lowered
        for marker in (
            "给我",
            "总结",
            "收尾",
            "结论",
            "下一步",
            "比较",
            "解释",
            "整理",
            "回复",
            "输出",
            "写",
            "说",
            "answer",
            "closeout",
            "next step",
        )
    )
    has_direct_query = any(
        marker in raw or marker in lowered
        for marker in (
            "什么偏好",
            "偏好是什么",
            "什么顺序",
            "还记得",
            "记得",
            "我刚才说过什么",
            "我之前说过什么",
            "tell me my preference",
            "what preference",
        )
    )
    return has_apply_phrase and has_generation_target and not has_direct_query


def explicit_preference_recall_query(text: str) -> bool:
    raw = str(text or "").strip()
    lowered = raw.lower()
    if not raw or structured_summary_chat_request(raw) or preference_application_request(raw):
        return False
    has_query_marker = any(
        marker in raw or marker in lowered
        for marker in (
            "记得",
            "还记得",
            "刚才",
            "之前",
            "上次说过",
            "我刚才说",
            "我之前说",
            "what",
            "recall",
            "remember",
        )
    )
    has_preference_reference = any(
        marker in raw or marker in lowered
        for marker in (
            "偏好",
            "顺序",
            "口径",
            "风格",
            "项目规则",
            "回复规则",
            "输出规则",
            "我的规则",
            "reply preference",
            "preference",
        )
    )
    has_question_shape = any(
        marker in raw or marker in lowered
        for marker in (
            "什么",
            "吗",
            "?",
            "？",
            "which",
            "what",
        )
    )
    return has_preference_reference and (has_query_marker or has_question_shape)


def format_sensitive_chat_request(text: str) -> bool:
    lowered = str(text or "").lower()
    if strict_format_chat_request(text):
        return True
    if structured_summary_chat_request(text):
        return True
    return any(
        marker in lowered
        for marker in (
            "markdown 表格",
            "markdown表格",
            "用表格比较",
            "用表格",
            "表格比较",
            "只要表格",
            "只返回代码",
            "只要代码",
            "use a table",
            "table to compare",
            "compare in a table",
            "markdown table",
            "plain text only",
            "code only",
        )
    )


def latest_instruction_topic(user_text: str) -> str | None:
    raw = str(user_text or "").strip()
    for marker in ("改成", "只讨论", "只做", "先别", "不要再"):
        if marker not in raw:
            continue
        candidate = raw.split(marker, 1)[-1].strip(" ：:，。")
        if candidate:
            return candidate[:80]
    return None


def looks_like_ambiguous_clarification_followup(user_text: str) -> bool:
    compact = str(user_text or "").strip().replace("，", "").replace(",", "").strip("。?!？！~")
    if not compact or len(compact) > 18:
        return False
    return compact in {
        "就是这个",
        "就是这个继续",
        "就这个",
        "就这个继续",
        "按这个",
        "按这个继续",
        "这个",
        "可以",
        "行",
        "对",
        "没错",
        "继续",
        "嗯",
        "好的",
    }


def looks_like_voice_reply_request(text: str) -> bool:
    raw = str(text or "")
    return any(
        marker in raw.lower()
        for marker in ("语音回复", "voice reply", "voice-response", "读给我听", "发语音")
    )
