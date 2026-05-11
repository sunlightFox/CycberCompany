from __future__ import annotations


def looks_like_explicit_continuation(text: str) -> bool:
    return any(
        marker in str(text or "")
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
        )
    ):
        return True
    return (
        any(
            marker in raw
            for marker in (
                "还没",
                "没继续",
                "没执行",
                "卡住",
                "状态",
                "进度",
            )
        )
        and any(
            marker in raw
            for marker in (
                "任务",
                "操作",
                "执行",
                "步骤",
                "刚才",
                "那个",
            )
        )
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
        prefix = raw[max(0, marker_index - 6):marker_index]
        if any(neg in prefix for neg in negative_prefixes):
            continue
        has_positive_action_marker = True
        break
    return any(
        marker in raw
        for marker in (
            "分析",
            "对比",
            "比较",
            "解释",
            "设计",
            "方案",
            "验收",
            "模板",
            "讨论",
            "优化",
        )
    ) and not has_positive_action_marker


def looks_like_latest_instruction_override(text: str) -> bool:
    raw = str(text or "").strip()
    return any(
        marker in raw
        for marker in (
            "停，改成",
            "停,改成",
            "改成只",
            "改成先",
            "不要再",
            "先别",
            "只讨论",
            "只做",
        )
    )


def needs_recent_history_lookup(text: str) -> bool:
    raw = str(text or "")
    if looks_like_latest_instruction_override(raw):
        return False
    return any(
        marker in raw
        for marker in (
            "刚才",
            "前面",
            "上一条",
            "上个",
            "偏好",
            "顺序",
            "优先级",
            "记住",
            "继续",
            "接着",
        )
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
        for marker in (
            "再",
            "继续",
            "接着",
            "补",
            "展开",
            "改短",
            "改得",
            "改成",
            "按",
            "保持",
            "加一",
            "加个",
        )
    )


def strict_format_chat_request(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "只输出json",
            "只输出 json",
            "json-only",
            "不要markdown",
            "不要 markdown",
            "只要纯文本",
            "只要表格",
            "只返回代码",
        )
    )


def format_sensitive_chat_request(text: str) -> bool:
    lowered = str(text or "").lower()
    if strict_format_chat_request(text):
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
        )
    )


def latest_instruction_topic(user_text: str) -> str | None:
    raw = str(user_text or "").strip()
    for marker in ("改成", "只讨论", "只做", "先别", "不要再"):
        if marker not in raw:
            continue
        candidate = raw.split(marker, 1)[-1].strip(" ，,。")
        if candidate:
            return candidate[:80]
    return None


def looks_like_ambiguous_clarification_followup(user_text: str) -> bool:
    compact = (
        str(user_text or "")
        .strip()
        .replace("，", "")
        .replace(",", "")
        .strip("。.!！?？~～ ")
    )
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
        for marker in (
            "语音回复",
            "voice reply",
            "voice-response",
            "读给我听",
            "发语音",
        )
    )
