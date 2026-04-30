from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core_types import ContextPacket, TraceSpanType
from trace_service import TraceService, redact

from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository


@dataclass(frozen=True)
class ChatExperienceSignals:
    conversation_depth: int
    complexity_score: float
    chat_style: str
    route_profile: str
    context_selection_reason: list[str]
    recoverable: bool
    needs_strong_reasoning: bool = False
    needs_long_output: bool = False
    needs_tool_or_task: bool = False

    def as_payload(self) -> dict[str, Any]:
        return {
            "conversation_depth": self.conversation_depth,
            "complexity_score": self.complexity_score,
            "chat_style": self.chat_style,
            "route_profile": self.route_profile,
            "context_selection_reason": self.context_selection_reason,
            "recoverable": self.recoverable,
            "needs_strong_reasoning": self.needs_strong_reasoning,
            "needs_long_output": self.needs_long_output,
            "needs_tool_or_task": self.needs_tool_or_task,
        }


@dataclass(frozen=True)
class ClarificationDecision:
    clarification_id: str
    turn_id: str
    conversation_id: str
    needs_clarification: bool
    reason: str
    clarification_type: str
    blocking_level: str
    questions: list[str]
    can_answer_partially: bool
    trace_id: str | None
    created_at: str
    updated_at: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "clarification_id": self.clarification_id,
            "turn_id": self.turn_id,
            "conversation_id": self.conversation_id,
            "needs_clarification": self.needs_clarification,
            "reason": self.reason,
            "clarification_type": self.clarification_type,
            "blocking_level": self.blocking_level,
            "questions": self.questions,
            "can_answer_partially": self.can_answer_partially,
            "trace_id": self.trace_id,
        }


class ChatExperienceService:
    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        trace_service: TraceService,
    ) -> None:
        self._chat_repo = chat_repo
        self._trace = trace_service

    async def analyze_turn(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        context: ContextPacket,
        privacy_level: str,
    ) -> ChatExperienceSignals:
        span_id = await self._trace.start_span(
            turn["trace_id"],
            span_type=TraceSpanType.CHAT_EXPERIENCE,
            name="analyze chat experience",
            input_data={"text": redact(user_text)},
        )
        existing_state = await self._chat_repo.get_working_state(turn["conversation_id"])
        depth = len(context.conversation.last_messages)
        complexity = _complexity_score(user_text)
        route_profile = _route_profile(user_text, privacy_level, complexity)
        reasons = [
            "current_input",
            "recent_messages",
            "capability_boundary_summary",
        ]
        if existing_state:
            reasons.insert(1, "working_state")
        if context.conversation.recent_summary:
            reasons.append("conversation_summary")
        if context.memories:
            reasons.append("related_memories")
        if context.resource_handles:
            reasons.append("resource_handle_summary")
        if turn.get("retry_of_turn_id"):
            reasons.append(f"retry_of_turn_id:{turn['retry_of_turn_id']}")
        signals = ChatExperienceSignals(
            conversation_depth=depth,
            complexity_score=complexity,
            chat_style=_chat_style(user_text, route_profile),
            route_profile=route_profile,
            context_selection_reason=reasons,
            recoverable=True,
            needs_strong_reasoning=route_profile == "deep_reasoning",
            needs_long_output=_needs_long_output(user_text),
            needs_tool_or_task=route_profile == "tool_or_task",
        )
        await self._trace.end_span(span_id, output_data=signals.as_payload())
        return signals

    async def decide_clarification(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        signals: ChatExperienceSignals,
    ) -> ClarificationDecision:
        span_id = await self._trace.start_span(
            turn["trace_id"],
            span_type=TraceSpanType.CHAT_CLARIFICATION,
            name="decide chat clarification",
            input_data={"text": redact(user_text), "route_profile": signals.route_profile},
        )
        questions, reason = _clarification_questions(user_text)
        now = utc_now_iso()
        decision = ClarificationDecision(
            clarification_id=new_id("clarify"),
            turn_id=turn["turn_id"],
            conversation_id=turn["conversation_id"],
            needs_clarification=bool(questions),
            reason=reason,
            clarification_type=_clarification_type(reason),
            blocking_level="requires_answer" if questions else "none",
            questions=questions[:3],
            can_answer_partially=False if questions else True,
            trace_id=turn["trace_id"],
            created_at=now,
            updated_at=now,
        )
        await self._chat_repo.insert_clarification_decision(decision.as_payload() | {
            "created_at": decision.created_at,
            "updated_at": decision.updated_at,
        })
        await self._trace.end_span(
            span_id,
            output_data={
                "needs_clarification": decision.needs_clarification,
                "reason": decision.reason,
                "question_count": len(decision.questions),
            },
        )
        return decision

    async def update_working_state(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        assistant_text: str,
        response_plan: dict[str, Any] | None,
        clarification: ClarificationDecision | None = None,
        status: str = "active",
    ) -> dict[str, Any]:
        current = await self._chat_repo.get_working_state(turn["conversation_id"])
        now = utc_now_iso()
        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        session_id = _session_id_from_message(user_message)
        current_for_session = _same_session_state(current, session_id)
        topic = _active_topic(user_text, current_for_session)
        constraints = _merge_limited(
            current_for_session.get("known_constraints", []) if current_for_session else [],
            _constraints_from_text(user_text),
            limit=8,
        )
        decision_candidates = (
            _decisions_from_text(user_text, assistant_text)
            if status == "active"
            else []
        )
        decisions = _merge_limited(
            current_for_session.get("decisions_made", []) if current_for_session else [],
            decision_candidates,
            limit=8,
        )
        questions = (
            clarification.questions
            if clarification and clarification.needs_clarification
            else []
        )
        if (
            not questions
            and current_for_session
            and not current_for_session.get("pending_confirmation")
        ):
            questions = current_for_session.get("open_questions", [])
        pending = (
            current_for_session.get("pending_confirmation", {})
            if current_for_session
            else {}
        )
        response_pending = _pending_from_response_plan(response_plan or {})
        if response_pending is not None:
            pending = response_pending
            questions = list(response_pending.get("questions") or [])
        elif clarification and clarification.needs_clarification:
            pending = {
                "turn_id": turn["turn_id"],
                "questions": clarification.questions,
                "reason": clarification.reason,
            }
        elif pending and _looks_like_pending_answer(user_text):
            pending = {}
            questions = []
        state = {
            "conversation_id": turn["conversation_id"],
            "organization_id": "org_default",
            "session_id": session_id,
            "active_topic": topic,
            "user_goal": _user_goal(user_text, current_for_session),
            "known_constraints": constraints,
            "decisions_made": decisions,
            "open_questions": questions,
            "candidate_actions": _candidate_actions(user_text),
            "referenced_artifacts": _referenced_artifacts(response_plan or {}),
            "last_response_summary": _truncate(str(redact(assistant_text)), 360),
            "pending_confirmation": pending,
            "source_turn_id": turn["turn_id"],
            "confidence": _state_confidence(topic, constraints, decisions),
            "status": status,
            "created_at": current.get("created_at") if current else now,
            "updated_at": now,
        }
        await self._chat_repo.upsert_working_state(state)
        return state

    async def mark_failure(
        self,
        *,
        turn: dict[str, Any],
        code: str,
        message: str,
    ) -> dict[str, Any]:
        experience = dict(turn.get("experience") or {})
        recovery = {
            "recoverable": True,
            "failure_summary": str(redact(message)),
            "suggested_next_actions": _suggested_next_actions(code),
        }
        experience.update(recovery)
        await self._chat_repo.update_turn(
            turn["turn_id"],
            experience=experience,
            updated_at=utc_now_iso(),
        )
        return experience

    async def mark_cancelled(
        self,
        *,
        turn: dict[str, Any],
        partial_text: str,
    ) -> dict[str, Any]:
        experience = dict(turn.get("experience") or {})
        experience.update(
            {
                "recoverable": True,
                "failure_summary": "用户取消了本轮生成",
                "suggested_next_actions": ["继续上一轮", "换一种回答方式", "保留部分结果后重试"],
                "last_interrupted_turn": turn["turn_id"],
                "partial_response_available": bool(partial_text),
            }
        )
        await self._chat_repo.update_turn(
            turn["turn_id"],
            experience=experience,
            updated_at=utc_now_iso(),
        )
        await self.update_working_state(
            turn=turn,
            user_text="继续",
            assistant_text=partial_text or "已停止生成。",
            response_plan={"status": "cancelled"},
            status="interrupted",
        )
        return experience

    async def get_working_state(self, conversation_id: str) -> dict[str, Any] | None:
        return await self._chat_repo.get_working_state(conversation_id)

    async def get_clarification(self, turn_id: str) -> dict[str, Any] | None:
        return await self._chat_repo.get_clarification_by_turn(turn_id)


def _complexity_score(text: str) -> float:
    clean = text.strip()
    score = min(len(clean) / 280, 0.45)
    keywords = [
        "方案",
        "优化",
        "继续",
        "上一版",
        "架构",
        "排查",
        "对比",
        "权衡",
        "复杂",
        "长期",
        "多轮",
        "why",
        "how",
    ]
    score += 0.08 * sum(1 for keyword in keywords if keyword.lower() in clean.lower())
    return round(min(score, 1.0), 2)


def _route_profile(text: str, privacy_level: str, complexity: float) -> str:
    lowered = text.lower()
    if privacy_level == "high":
        return "privacy_sensitive"
    if not _is_execution_negated(text) and any(
        word in text
        for word in [
            "执行",
            "运行",
            "删除",
            "发帖",
            "发布",
            "购买",
            "转账",
            "支付",
            "整理文件夹",
        ]
    ):
        return "tool_or_task"
    if _needs_long_output(text):
        return "long_writing"
    if any(word in text for word in ["写", "润色", "改写", "文案", "草稿"]):
        return "light_writing"
    if complexity >= 0.35:
        return "deep_reasoning"
    if any(word in lowered for word in ["你好", "hello", "hi", "早上好", "晚上好"]):
        return "casual_chat"
    return "simple_qa"


def _chat_style(text: str, route_profile: str) -> str:
    if route_profile == "tool_or_task":
        return "tool_boundary"
    if route_profile == "deep_reasoning":
        return "deep_dialogue"
    if route_profile in {"light_writing", "long_writing"}:
        return "light_writing"
    if route_profile == "casual_chat":
        return "casual"
    return "simple_qa"


def _clarification_questions(text: str) -> tuple[list[str], str]:
    clean = text.strip()
    if not clean or _is_simple_chat(clean):
        return [], "safe_to_answer"
    if _is_execution_negated(clean):
        return [], "safe_draft_or_plan_requested"
    if any(word in clean for word in ["转账", "支付", "打款"]):
        return [
            "要使用哪个钱包或账户？",
            "收款对象和金额分别是什么？",
            "这是只需要方案说明，还是要创建待确认任务？",
        ], "high_risk_payment_scope_missing"
    if any(word in clean for word in ["购买", "下单", "买入"]):
        return [
            "要购买的具体对象是什么？",
            "预算、数量和使用账户范围是什么？",
            "你希望先生成方案，还是创建待确认任务？",
        ], "high_risk_purchase_scope_missing"
    if any(word in clean for word in ["发帖", "发布", "发送到", "群发"]):
        return [
            "发布到哪个平台或账号？",
            "最终内容和受众范围是什么？",
            "是否只需要先生成草稿？",
        ], "external_publish_scope_missing"
    if any(word in clean for word in ["删除", "清空", "覆盖", "移动文件", "整理文件夹"]):
        if _filesystem_scope_is_ambiguous(clean):
            return [
                "目标文件或文件夹的明确范围是什么？",
                "是只需要整理方案，还是要创建待确认任务？",
                "是否需要保留备份或只读预览？",
            ], "filesystem_scope_missing"
    if any(word in clean for word in ["执行", "运行"]) and not any(
        marker in clean for marker in ["命令", "脚本", "任务", "只分析", "方案"]
    ):
        return [
            "要执行的对象是命令、脚本还是已有任务？",
            "允许的工作范围和成功标准是什么？",
        ], "execution_target_missing"
    return [], "safe_to_answer"


def _clarification_type(reason: str) -> str:
    if "payment" in reason or "purchase" in reason:
        return "missing_destination"
    if "publish" in reason:
        return "missing_destination"
    if "filesystem" in reason:
        return "missing_scope"
    if "execution" in reason:
        return "missing_asset"
    if reason == "safe_to_answer":
        return "none"
    return "missing_goal"


def _is_simple_chat(text: str) -> bool:
    return text in {"你好", "hi", "hello", "在吗", "谢谢"} or len(text) <= 6


def _is_execution_negated(text: str) -> bool:
    if "创建任务" in text and "不要创建任务" not in text and "不创建任务" not in text:
        return False
    return any(
        marker in text
        for marker in [
            "不要执行",
            "不执行",
            "别执行",
            "不要创建任务",
            "不要使用工具",
            "不要使用浏览器",
            "不要使用浏览器或工具",
            "不要调用工具",
            "不使用工具",
            "不用工具",
            "不要联网",
            "不浏览",
            "只分析",
            "只解释",
            "请解释",
            "解释",
            "只要方案",
            "只给方案",
            "只生成方案",
            "只输出",
            "先给方案",
            "先写方案",
            "生成草稿",
            "只写草稿",
            "严格 JSON",
            "只用 JSON",
            "术语表",
            "科普",
            "学习路线",
            "路线图",
            "翻译",
            "表格比较",
            "用表格",
            "设计原则",
            "五条原则",
            "知识总结",
            "压缩成",
            "归纳为",
        ]
    )


def _needs_long_output(text: str) -> bool:
    return any(
        marker in text
        for marker in ["长文", "完整文档", "详细展开", "详细方案", "写一篇", "报告", "不少于"]
    )


def _session_id_from_message(message: dict[str, Any] | None) -> str | None:
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, dict):
        return None
    value = content.get("session_id")
    return str(value) if value else None


def _same_session_state(
    current: dict[str, Any] | None,
    session_id: str | None,
) -> dict[str, Any] | None:
    if not current or not session_id:
        return current
    current_session_id = current.get("session_id")
    if not current_session_id or str(current_session_id) == str(session_id):
        return current
    return None


def _filesystem_scope_is_ambiguous(text: str) -> bool:
    if any(marker in text for marker in ["那个", "这个", "一些", "某个"]):
        return True
    if any(marker in text for marker in ["全部", "所有", "清空"]):
        return True
    explicit_scope_markers = [
        "/",
        "\\",
        ".md",
        ".txt",
        ".json",
        "docs/",
        "data/",
        "当前目录",
        "当前项目",
        "只生成方案",
        "只分析",
        "预览",
    ]
    return not any(marker in text for marker in explicit_scope_markers)


def _looks_like_pending_answer(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "只要",
            "先",
            "目标是",
            "范围是",
            "用",
            "不要执行",
            "生成方案",
            "创建任务",
            "确认",
            "拒绝",
            "取消",
            "允许",
            "本会话",
            "改成",
            "修改",
        ]
    )


def _active_topic(text: str, current: dict[str, Any] | None) -> str:
    if current and any(word in text for word in ["继续", "刚才", "上一版", "换个方式"]):
        existing = current.get("active_topic")
        if existing:
            return str(existing)
    clean = str(redact(text)).strip().replace("\n", " ")
    for prefix in ["我们要", "帮我", "请", "继续"]:
        clean = clean.removeprefix(prefix).strip()
    return _truncate(clean, 80) or "当前对话"


def _user_goal(text: str, current: dict[str, Any] | None) -> str:
    if any(word in text for word in ["继续", "刚才", "上一版"]) and current:
        existing = current.get("user_goal")
        if existing:
            return str(existing)
    return _truncate(str(redact(text)).strip().replace("\n", " "), 120)


def _constraints_from_text(text: str) -> list[str]:
    constraints: list[str] = []
    for marker in ["必须", "不要", "不能", "只", "先", "改成", "换成", "约束"]:
        if marker in text:
            constraints.append(_truncate(str(redact(text)).strip(), 120))
            break
    return constraints


def _decisions_from_text(user_text: str, assistant_text: str) -> list[str]:
    if any(word in user_text for word in ["定", "决定", "采用", "确认"]):
        return [_truncate(str(redact(user_text)).strip(), 120)]
    if assistant_text:
        return [_truncate(str(redact(assistant_text)).strip(), 160)]
    return []


def _candidate_actions(text: str) -> list[str]:
    actions = []
    mapping = {
        "删除": "filesystem.delete",
        "整理文件夹": "filesystem.organize",
        "发帖": "external.publish",
        "购买": "external.purchase",
        "转账": "wallet.transfer",
        "执行": "runtime.execute",
        "运行": "runtime.execute",
    }
    for keyword, action in mapping.items():
        if keyword in text and action not in actions:
            actions.append(action)
    return actions


def _referenced_artifacts(response_plan: dict[str, Any]) -> list[dict[str, Any]]:
    refs = response_plan.get("artifact_refs") or []
    return [dict(item) for item in refs if isinstance(item, dict)][:8]


def _pending_from_response_plan(response_plan: dict[str, Any]) -> dict[str, Any] | None:
    structured = response_plan.get("structured_payload")
    if not isinstance(structured, dict):
        return None
    natural = structured.get("natural_interaction")
    if not isinstance(natural, dict):
        return None
    explicit = natural.get("pending_confirmation")
    if isinstance(explicit, dict):
        return explicit
    if natural.get("clear_pending"):
        session_grant = natural.get("session_grant")
        if isinstance(session_grant, dict) and session_grant:
            return {
                "kind": "natural_pending_actions",
                "session_id": session_grant.get("session_id"),
                "actions": [],
                "session_grants": [session_grant],
                "questions": [],
            }
        return {}
    actions = natural.get("pending_actions") or structured.get("pending_actions")
    if isinstance(actions, list) and actions:
        safe_actions = [dict(item) for item in actions if isinstance(item, dict)]
        return {
            "kind": "natural_pending_actions",
            "session_id": safe_actions[0].get("session_id"),
            "actions": safe_actions,
            "questions": list(natural.get("natural_reply_options") or []),
            "created_at": utc_now_iso(),
        }
    return None


def _merge_limited(existing: list[Any], new_items: list[str], *, limit: int) -> list[str]:
    merged: list[str] = []
    for item in [*existing, *new_items]:
        text = _truncate(str(item), 160)
        if text and text not in merged:
            merged.append(text)
    return merged[-limit:]


def _state_confidence(topic: str, constraints: list[str], decisions: list[str]) -> float:
    score = 0.55
    if topic:
        score += 0.15
    if constraints:
        score += 0.1
    if decisions:
        score += 0.1
    return round(min(score, 0.9), 2)


def _suggested_next_actions(code: str) -> list[str]:
    if code == "MODEL_NOT_CONFIGURED":
        return ["配置可用大脑后重试", "改为只创建任务草案", "保留本轮输入稍后继续"]
    if code == "MODEL_ROUTE_BLOCKED_BY_PRIVACY":
        return ["改用本地大脑", "移除敏感信息后重试"]
    return ["重试本轮", "换一种更短的提问方式", "查看 turn 事件和 trace"]


def _truncate(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[:limit]}..."
