from __future__ import annotations

from dataclasses import dataclass

from app.services.chat_turn_input_facts import (
    explicit_preference_recall_query,
    preference_application_request,
    structured_summary_chat_request,
)


_SAFE_PLAN_ONLY_MARKERS = (
    "只做分析",
    "只给方案",
    "不要执行",
    "先别执行",
    "不要创建任务",
    "不创建任务",
    "不要使用工具",
    "不要调用工具",
    "不使用工具",
    "只解释",
    "只输出",
    "只要结果",
    "先给方案",
)

_CHAT_CONTINUATION_MARKERS = (
    "继续刚才",
    "继续这轮",
    "不用长期记忆",
    "按前面的口径",
)

_CHAT_COMPRESSION_MARKERS = (
    "压成两句",
    "压成三句",
    "收尾结论",
    "主要风险",
    "下一步",
)

_ROLEPLAY_SETUP_MARKERS = (
    "不要新建任务",
    "你先当我的生活管家",
    "你先当生活管家",
    "你先像虚拟恋人",
    "你先像靠谱的虚拟员工",
    "你先像虚拟员工",
)

_OUTPUT_MARKERS = (
    "收尾",
    "总结",
    "压成两句",
    "压成三句",
    "三句话",
    "两句",
    "一句话",
    "改短",
    "同步",
    "回复",
    "结论",
    "风险",
    "下一步",
    "安排",
    "方案",
    "计划",
    "清单",
    "纪要",
    "更新",
    "时间线",
    "原则",
    "决策表",
    "步骤",
    "筛选规则",
)

_EXECUTION_MARKERS = (
    "创建任务",
    "去执行",
    "安装",
    "卸载",
    "删除",
    "下载",
    "登录",
    "发布",
    "转账",
    "支付",
    "打开网页",
    "调用工具",
    "浏览器搜索",
    "打开这个页面",
    "打开这个 FAQ 页面",
    "打开这个页面看看",
    "生成一份 Word",
)

_MEMORY_QUERY_MARKERS = (
    "记得",
    "还记得",
    "之前",
    "我之前",
    "我说过",
    "记住了什么",
    "让你记住",
    "记得",
    "还记得",
    "memory",
    "历史记忆",
    "长期记忆",
    "我说过",
    "之前说过",
    "偏好",
)

_REAL_TASK_MARKERS = (
    "创建任务",
    "建个任务",
    "新建任务",
    "去执行",
    "帮我处理",
    "删除",
    "删掉",
    "转账",
    "创建任务",
    "建个任务",
    "排个任务",
    "新建任务",
    "去执行",
    "帮我处理",
    "跑一个",
    "装一个",
)

_TOOL_REQUEST_MARKERS = (
    "调用工具",
    "打开网页",
    "下载",
    "安装",
    "删除",
    "删掉",
    "转账",
    "发布",
    "调用工具",
    "打开网页",
    "下载",
    "安装",
    "联网搜",
    "联网检索",
    "抓取",
)

_OPERATIONAL_TASK_MARKERS = (
    "扫描",
    "检索",
    "联网搜",
    "联网检索",
    "搜索",
    "搜近",
    "抓取",
    "读取",
    "生成",
    "输出为",
    "做一次",
    "体检",
    "列出来",
    "找到我",
)

_OPERATIONAL_TASK_OBJECT_MARKERS = (
    "github",
    "trending",
    "html",
    "markdown",
    "网页",
    "页面",
    "报告",
    "论文",
    "arxiv",
    "电影",
    "电脑",
    "应用",
    "文件",
    "ppt",
    "pdf",
    "注册表",
    "磁盘",
    "浏览器",
    "最近",
    "本周",
)


@dataclass(frozen=True)
class IntentBoundaryAssessment:
    text: str
    safe_plan_only: bool
    chatty_delivery: bool
    memory_query: bool
    real_task_request: bool
    tool_request: bool


class IntentBoundaryService:
    def assess(self, text: str) -> IntentBoundaryAssessment:
        raw = str(text or "")
        safe_plan = looks_like_safe_plan_only(raw)
        chatty_delivery = looks_like_chatty_delivery(raw)
        memory_query = self._should_treat_as_memory_query(raw)
        real_task_request = self._should_treat_as_real_task_request(
            raw,
            safe_plan_only=safe_plan,
            chatty_delivery=chatty_delivery,
        )
        tool_request = self._should_treat_as_tool_request(
            raw,
            safe_plan_only=safe_plan,
            chatty_delivery=chatty_delivery,
        )
        return IntentBoundaryAssessment(
            text=raw,
            safe_plan_only=safe_plan,
            chatty_delivery=chatty_delivery,
            memory_query=memory_query,
            real_task_request=real_task_request,
            tool_request=tool_request,
        )

    def _should_treat_as_memory_query(self, text: str) -> bool:
        raw = str(text or "")
        if _looks_like_operational_task_request(raw):
            return False
        if structured_summary_chat_request(raw) or preference_application_request(raw):
            return False
        if "今天刚更新" in raw and any(marker in raw for marker in ("不要联网", "不联网", "不能联网", "时效边界", "最新边界")):
            return False
        if any(marker in raw for marker in (*_CHAT_CONTINUATION_MARKERS, *_CHAT_COMPRESSION_MARKERS)):
            return False
        if explicit_preference_recall_query(raw):
            return True
        if _looks_like_memory_concept_question(raw):
            return False
        lowered = raw.lower()
        return any(marker in lowered for marker in _MEMORY_QUERY_MARKERS)

    def _should_treat_as_real_task_request(
        self,
        text: str,
        *,
        safe_plan_only: bool,
        chatty_delivery: bool,
    ) -> bool:
        raw = str(text or "")
        if safe_plan_only or chatty_delivery:
            return False
        if _looks_like_action_policy_question(raw):
            return False
        return any(marker in raw for marker in _REAL_TASK_MARKERS) or _looks_like_operational_task_request(raw)

    def _should_treat_as_tool_request(
        self,
        text: str,
        *,
        safe_plan_only: bool,
        chatty_delivery: bool,
    ) -> bool:
        raw = str(text or "")
        if safe_plan_only or chatty_delivery:
            return False
        if _looks_like_action_policy_question(raw):
            return False
        if _looks_like_described_action_risk_context(raw):
            return False
        return (
            any(marker in raw for marker in _TOOL_REQUEST_MARKERS)
            or _looks_like_screenshot_action(raw)
            or _looks_like_operational_task_request(raw)
        )


def looks_like_safe_plan_only(text: str) -> bool:
    raw = str(text or "")
    if (
        any(marker in raw for marker in ("\u4e0d\u8981\u6267\u884c", "\u5148\u522b\u6267\u884c", "\u4e0d\u6267\u884c"))
        and any(marker in raw for marker in ("\u6253\u62db\u547c", "\u95f2\u804a", "\u804a\u4e24\u53e5", "\u56de\u6d88\u606f", "\u8bf4\u4e00\u53e5", "\u5fae\u4fe1\u91cc\u4e00\u6837\u81ea\u7136"))
        and not any(marker in raw for marker in ("\u5b89\u88c5", "\u5378\u8f7d", "\u5220\u9664", "\u4e0b\u8f7d", "\u767b\u5f55", "\u53d1\u5e03", "\u8f6c\u8d26", "\u652f\u4ed8", "\u8c03\u7528\u5de5\u5177"))
    ):
        return False
    direct_action_markers = tuple(marker for marker in _EXECUTION_MARKERS if marker not in {"去执行", "调用工具"})
    if (
        any(marker in raw for marker in ("不要执行", "先别执行"))
        and any(marker in raw for marker in ("打招呼", "闲聊", "聊两句", "回消息", "说一句"))
        and not any(marker in raw for marker in direct_action_markers)
    ):
        return False
    return any(marker in raw for marker in _SAFE_PLAN_ONLY_MARKERS)


def looks_like_chatty_delivery(text: str) -> bool:
    raw = str(text or "")
    if any(marker in raw for marker in (*_CHAT_CONTINUATION_MARKERS, *_ROLEPLAY_SETUP_MARKERS)):
        return True
    return any(marker in raw for marker in _OUTPUT_MARKERS) and not any(
        marker in raw for marker in _EXECUTION_MARKERS
    )


def _looks_like_operational_task_request(text: str) -> bool:
    raw = str(text or "")
    if not raw:
        return False
    lowered = raw.lower()
    return any(marker in raw for marker in _OPERATIONAL_TASK_MARKERS) and any(
        marker in lowered or marker in raw for marker in _OPERATIONAL_TASK_OBJECT_MARKERS
    )


def _looks_like_described_action_risk_context(text: str) -> bool:
    raw = str(text or "")
    if not raw:
        return False
    described_markers = (
        "诱导安装",
        "被诱导安装",
        "被骗安装",
        "安装一个",
        "安装了",
        "让家人安装",
        "让父母安装",
        "让爸妈安装",
        "自动转账",
        "设置自动转账",
        "外发",
        "第三方工具",
        "远程控制",
        "远程控件",
        "远控软件",
        "下载远程控件",
        "屏幕共享",
        "验证码",
    )
    advice_markers = (
        "风险",
        "核验",
        "止损",
        "沟通",
        "证据",
        "安全",
        "比较安全",
        "怎么回复",
        "怎么回",
        "该怎么回",
        "安全回复",
        "边界",
        "不要",
        "不能",
        "如何处理",
        "怎么处理",
        "有什么风险",
        "给我安全",
        "帮我列",
        "帮我整理",
        "帮我写",
        "请说明",
    )
    direct_action_markers = (
        "帮我安装",
        "请安装",
        "给我安装",
        "装一下",
        "打开网页",
        "打开这个页面",
        "下载到",
        "帮我下载",
        "请下载",
        "直接转账",
        "帮我转账",
        "发给第三方",
    )
    return (
        any(marker in raw for marker in described_markers)
        and any(marker in raw for marker in advice_markers)
        and not any(marker in raw for marker in direct_action_markers)
    )


def _looks_like_action_policy_question(text: str) -> bool:
    raw = str(text or "")
    if not raw:
        return False
    question_markers = (
        "哪些",
        "什么情况下",
        "什么时候",
        "何时",
        "是否需要",
        "要不要",
        "必须",
    )
    policy_markers = (
        "审批",
        "授权",
        "确认",
        "高风险",
        "需要批准",
        "需要确认",
        "走审批",
    )
    action_markers = (
        "下载",
        "安装",
        "卸载",
        "删除",
        "提交表单",
        "转账",
        "支付",
        "登录",
        "远程控制",
        "远程控件",
        "系统操作",
        "浏览器",
    )
    direct_action_markers = (
        "帮我下载",
        "请下载",
        "帮我安装",
        "请安装",
        "帮我删除",
        "请删除",
        "直接提交",
        "帮我提交",
        "帮我转账",
        "请转账",
        "帮我支付",
        "请支付",
    )
    return (
        any(marker in raw for marker in question_markers)
        and any(marker in raw for marker in policy_markers)
        and any(marker in raw for marker in action_markers)
        and not any(marker in raw for marker in direct_action_markers)
    )


def _looks_like_screenshot_action(text: str) -> bool:
    raw = str(text or "")
    screenshot_action_markers = (
        "帮我截图",
        "请截图",
        "截个图",
        "截一下图",
        "截图留证",
        "截图保存",
        "页面截图",
        "登录截图",
        "打开并截图",
    )
    return any(marker in raw for marker in screenshot_action_markers)


def _looks_like_memory_concept_question(text: str) -> bool:
    raw = str(text or "")
    lowered = raw.lower()
    if not any(marker in lowered for marker in _MEMORY_QUERY_MARKERS):
        return False
    direct_recall_markers = (
        "你记得",
        "还记得",
        "我说过",
        "之前说过",
        "我之前",
        "我的偏好",
        "我偏好",
        "我喜欢",
        "我讨厌",
        "我让你记",
        "记住了什么",
        "记了什么",
    )
    if any(marker in raw for marker in direct_recall_markers):
        return False
    concept_markers = (
        "区别",
        "解释",
        "什么是",
        "是什么",
        "为什么",
        "怎么",
        "如何",
        "来源",
        "写入",
        "召回",
        "评估",
        "角度",
        "概念",
        "原理",
        "用途",
        "边界",
        "rag",
        "RAG",
    )
    return any(marker in raw or marker in lowered for marker in concept_markers)


_SERVICE = IntentBoundaryService()


def should_treat_as_memory_query(text: str) -> bool:
    return _SERVICE.assess(text).memory_query


def should_treat_as_real_task_request(text: str, *, safe_plan_only: bool) -> bool:
    return _SERVICE._should_treat_as_real_task_request(
        text,
        safe_plan_only=safe_plan_only,
        chatty_delivery=looks_like_chatty_delivery(text),
    )


def should_treat_as_tool_request(text: str, *, safe_plan_only: bool) -> bool:
    return _SERVICE._should_treat_as_tool_request(
        text,
        safe_plan_only=safe_plan_only,
        chatty_delivery=looks_like_chatty_delivery(text),
    )


def assess_intent_boundaries(text: str) -> IntentBoundaryAssessment:
    return _SERVICE.assess(text)
