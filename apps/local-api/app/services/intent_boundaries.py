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
    "清单",
    "纪要",
    "更新",
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
    "排个任务",
    "新建任务",
    "帮我做",
    "去执行",
    "帮我处理",
    "跑一个",
    "装一个",
)

_TOOL_REQUEST_MARKERS = (
    "调用工具",
    "打开网页",
    "下载",
    "截图",
    "安装",
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
        if structured_summary_chat_request(raw) or preference_application_request(raw):
            return False
        if any(marker in raw for marker in (*_CHAT_CONTINUATION_MARKERS, *_CHAT_COMPRESSION_MARKERS)):
            return False
        if explicit_preference_recall_query(raw):
            return True
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
        return any(marker in raw for marker in _REAL_TASK_MARKERS)

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
        return any(marker in raw for marker in _TOOL_REQUEST_MARKERS)


def looks_like_safe_plan_only(text: str) -> bool:
    return any(marker in str(text or "") for marker in _SAFE_PLAN_ONLY_MARKERS)


def looks_like_chatty_delivery(text: str) -> bool:
    raw = str(text or "")
    if any(marker in raw for marker in (*_CHAT_CONTINUATION_MARKERS, *_ROLEPLAY_SETUP_MARKERS)):
        return True
    return any(marker in raw for marker in _OUTPUT_MARKERS) and not any(
        marker in raw for marker in _EXECUTION_MARKERS
    )


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
