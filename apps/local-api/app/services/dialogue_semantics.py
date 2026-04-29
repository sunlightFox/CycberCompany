from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core_types import DialogueState, LowConfidenceDecisionReview, SemanticIntentCandidate
from core_types.enums import TraceSpanType
from trace_service import TraceService, redact

from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.services.model_semantic_verifier import (
    ModelAssistedVerifierService,
    SemanticReviewOutcome,
)


class DialogueStateService:
    def __init__(self, *, repo: ChatRepository, trace_service: TraceService) -> None:
        self._repo = repo
        self._trace = trace_service

    async def get(self, conversation_id: str) -> dict[str, Any] | None:
        return await self._repo.get_dialogue_state(conversation_id)

    async def derive(
        self,
        *,
        text: str,
        member_id: str,
        conversation_id: str | None,
        turn_id: str | None,
        working_state: dict[str, Any] | None,
        trace_id: str | None,
        root_span_id: str | None,
        persist: bool,
    ) -> DialogueState | None:
        if conversation_id is None:
            return None
        previous = await self._repo.get_dialogue_state(conversation_id)
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.DIALOGUE_STATE,
                name="derive dialogue state",
                parent_span_id=root_span_id,
                input_data={"text": redact(text), "conversation_id": conversation_id},
            )
            if trace_id
            else None
        )
        now = utc_now_iso()
        is_continuation = _continuation_reference(text)
        topic_shift = _topic_shift(text, previous, working_state)
        active_topic = _active_topic(text, previous, working_state, is_continuation)
        user_goal = _user_goal(text, previous, working_state, is_continuation)
        old_goal = str((previous or {}).get("user_goal") or "")
        goal_history = _merge_goal_history(previous, old_goal, topic_shift=topic_shift)
        constraints = _merge_strings(
            (previous or {}).get("known_constraints", []),
            _constraints_from_text(text),
            limit=10,
        )
        soft_preferences = _merge_strings(
            (previous or {}).get("soft_preferences", []),
            _soft_preferences_from_text(text),
            limit=8,
        )
        hard_constraints = _merge_strings(
            (previous or {}).get("hard_constraints", []),
            _hard_constraints_from_text(text),
            limit=8,
        )
        decisions = _merge_decisions(
            (previous or {}).get("decisions_made", []),
            _decisions_from_text(text, supersedes=topic_shift or _denies_previous(text)),
            limit=10,
        )
        open_questions = _open_questions_from_text(text)
        if not open_questions and working_state:
            open_questions = list(working_state.get("open_questions") or [])[:5]
        pending = dict((working_state or previous or {}).get("pending_confirmation") or {})
        candidate_actions = _candidate_actions(text)
        state = DialogueState(
            dialogue_state_id=str((previous or {}).get("dialogue_state_id") or new_id("dlg")),
            conversation_id=conversation_id,
            member_id=member_id,
            active_topic=active_topic,
            user_goal=user_goal,
            goal_status=_goal_status(text, topic_shift),
            goal_history=goal_history,
            known_constraints=constraints,
            soft_preferences=soft_preferences,
            hard_constraints=hard_constraints,
            decisions_made=decisions,
            open_questions=open_questions,
            pending_confirmation=pending,
            topic_shift=topic_shift,
            last_user_action=candidate_actions[0] if candidate_actions else None,
            candidate_next_actions=candidate_actions,
            referenced_memories=_referenced_memories(text),
            referenced_artifacts=_referenced_artifacts(text, working_state),
            confidence=_state_confidence(active_topic, constraints, decisions, topic_shift),
            source_turn_id=turn_id,
            trace_id=trace_id,
            created_at=str((previous or {}).get("created_at") or now),
            updated_at=now,
        )
        if persist:
            await self._repo.upsert_dialogue_state(state.model_dump(mode="json"))
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data=redact(
                    {
                        "dialogue_state_id": state.dialogue_state_id,
                        "topic_shift": state.topic_shift,
                        "goal_status": state.goal_status,
                        "confidence": state.confidence,
                    }
                ),
            )
        return state


class SemanticIntentAnalyzer:
    def __init__(self, *, trace_service: TraceService) -> None:
        self._trace = trace_service

    async def analyze(
        self,
        *,
        text: str,
        member_id: str,
        conversation_id: str | None,
        turn_id: str | None,
        dialogue_state: DialogueState | None,
        capability_snapshot: dict[str, Any],
        privacy_level: str,
        trace_id: str | None,
        root_span_id: str | None,
    ) -> SemanticIntentCandidate:
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.SEMANTIC_INTENT,
                name="analyze semantic intents",
                parent_span_id=root_span_id,
                input_data={"text": redact(text), "conversation_id": conversation_id},
            )
            if trace_id
            else None
        )
        lowered = text.lower()
        secondary: list[str] = []
        actionable: list[str] = []
        non_actionable: list[str] = []
        risks: list[str] = []
        memory: list[str] = []
        tools: list[str] = []
        skills: list[str] = []
        mcp: list[str] = []
        conversation: list[str] = []
        conflicts: list[str] = []
        reasons: list[str] = []

        if _simple_chat(text):
            non_actionable.append("casual_chat")
            conversation.append("casual_chat")
            reasons.append("casual_marker")
        if text.strip().endswith(("?", "？")) or any(
            marker in lowered for marker in ["什么", "为什么", "how", "why"]
        ):
            non_actionable.append("simple_question")
            conversation.append("question")
            reasons.append("question_marker")
        if _continuation_reference(text):
            conversation.append("continue_previous_topic")
            secondary.append("continue_previous_topic")
            reasons.append("continuation_reference")
            if dialogue_state is None:
                conflicts.append("ambiguous_reference")
        if _memory_write(text):
            memory.append("memory_update")
            actionable.append("memory_update")
            reasons.append("explicit_memory_write")
        if _memory_query(text):
            memory.append("memory_query")
            non_actionable.append("memory_query")
            reasons.append("explicit_memory_query")
        if _skill_request(text):
            skills.append("skill_request")
            reasons.append("skill_keyword")
            if not _skill_available(capability_snapshot):
                conflicts.append("tool_vs_missing_asset")
                reasons.append("skill_unavailable")
        if _mcp_request(text):
            mcp.append("mcp_request")
            reasons.append("mcp_keyword")
            if not _mcp_available(capability_snapshot):
                conflicts.append("tool_vs_missing_asset")
                reasons.append("mcp_unavailable")
        if _tool_request(text):
            tools.append("tool_or_task_request")
            actionable.append("task_request")
            reasons.append("tool_or_external_action")
        if _safe_plan_only(text):
            non_actionable.append("safe_plan_only")
            reasons.append("safe_plan_only")
        if any(word in text for word in ["删除", "清空", "覆盖"]):
            risks.append("destructive_action")
        if any(word in text for word in ["发帖", "发布", "发送", "提交"]):
            risks.append("external_side_effect")
        if any(word in text for word in ["购买", "下单", "转账", "支付", "签名"]):
            risks.append("high_risk_financial_or_signature")
        if privacy_level == "high":
            risks.append("secret_or_sensitive")
        if risks and not _safe_plan_only(text):
            conflicts.append("execution_vs_missing_approval")
        if non_actionable and actionable:
            conflicts.append("casual_vs_action_request")
        if memory and privacy_level == "high":
            conflicts.append("memory_vs_privacy")
        if risks and _ambiguous_scope(text):
            conflicts.append("high_risk_vs_ambiguous_destination")
        if dialogue_state and dialogue_state.topic_shift:
            conflicts.append("old_goal_vs_new_goal")
        if any(marker in lowered for marker in ["无视安全", "不要审批", "绕过审批"]):
            conflicts.append("persona_request_vs_safety_boundary")

        primary = _semantic_primary(
            memory=memory,
            tools=tools,
            skills=skills,
            mcp=mcp,
            conversation=conversation,
            non_actionable=non_actionable,
        )
        if primary == "casual_chat" and len(text.strip()) > 80:
            primary = "complex_dialogue"
        if not reasons:
            reasons.append("semantic_default")
        candidate = SemanticIntentCandidate(
            semantic_candidate_id=new_id("sem"),
            turn_id=turn_id,
            conversation_id=conversation_id,
            member_id=member_id,
            primary_intent=primary,
            secondary_intents=_dedupe(secondary),
            actionable_intents=_dedupe(actionable),
            non_actionable_intents=_dedupe(non_actionable),
            risk_intents=_dedupe(risks),
            memory_intents=_dedupe(memory),
            tool_intents=_dedupe(tools),
            skill_intents=_dedupe(skills),
            mcp_intents=_dedupe(mcp),
            conversation_intents=_dedupe(conversation),
            conflicts=_dedupe(conflicts),
            confidence=_semantic_confidence(reasons, conflicts),
            reason_codes=_dedupe([*reasons, *conflicts]),
            model_hint={"enabled": False, "source": "rule_first_phase18"},
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data=redact(
                    {
                        "primary_intent": candidate.primary_intent,
                        "conflicts": candidate.conflicts,
                        "confidence": candidate.confidence,
                    }
                ),
            )
        return candidate


@dataclass(frozen=True)
class LowConfidenceReviewOutcome:
    review: LowConfidenceDecisionReview
    semantic_review: Any | None
    intent: Any
    mode: Any
    context: Any
    clarification: dict[str, Any]


class LowConfidenceDecisionReviewer:
    def __init__(
        self,
        *,
        trace_service: TraceService,
        verifier: ModelAssistedVerifierService | None = None,
        model_assist_enabled: bool = True,
    ) -> None:
        self._trace = trace_service
        self._model_assist_enabled = model_assist_enabled
        self._verifier = verifier or ModelAssistedVerifierService(trace_service=trace_service)

    async def review(
        self,
        *,
        member_id: str,
        conversation_id: str | None,
        turn_id: str | None,
        brain_decision_id: str,
        intent: Any,
        mode: Any,
        context: Any,
        semantic: SemanticIntentCandidate,
        dialogue_state: DialogueState | None,
        clarification: dict[str, Any],
        capability_snapshot: dict[str, Any],
        privacy_level: str,
        text: str,
        trace_id: str | None,
        root_span_id: str | None,
    ) -> LowConfidenceReviewOutcome | None:
        triggers = _review_triggers(intent, mode, semantic, dialogue_state)
        if not triggers:
            return None
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.LOW_CONFIDENCE_REVIEW,
                name="review low confidence decision",
                parent_span_id=root_span_id,
                metadata={"brain_decision_id": brain_decision_id},
            )
            if trace_id
            else None
        )
        semantic_outcome: SemanticReviewOutcome = await self._verifier.review_and_merge(
            text=text,
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            brain_decision_id=brain_decision_id,
            intent=intent,
            mode=mode,
            context=context,
            semantic=semantic,
            dialogue_state=dialogue_state,
            clarification=clarification,
            capability_snapshot=capability_snapshot,
            privacy_level=privacy_level,
            trigger_reasons=triggers,
            trace_id=trace_id,
            root_span_id=span_id or root_span_id,
        )
        semantic_review = semantic_outcome.result
        review = LowConfidenceDecisionReview(
            review_id=new_id("lcdr"),
            brain_decision_id=brain_decision_id,
            turn_id=turn_id,
            conversation_id=conversation_id,
            member_id=member_id,
            trigger_reasons=triggers,
            rule_decision={
                "primary_intent": intent.primary_intent,
                "mode": mode.mode,
                "submode": mode.submode,
                "confidence": min(intent.confidence, mode.confidence),
                "reason_codes": _dedupe([*intent.reason_codes, *mode.reason_codes]),
            },
            verifier_suggestion={
                "candidate_primary_intent": semantic.primary_intent,
                "candidate_secondary_intents": semantic.secondary_intents,
                "candidate_mode": mode.mode,
                "missing_information": _missing_information(semantic, clarification),
                "risk_notes": semantic.risk_intents,
                "context_notes": semantic.conflicts,
                "confidence": semantic.confidence,
                "explanation_summary": (
                    "规则复核和模型辅助语义复核契约已执行；模型建议只作为结构化"
                    "证据，最终决策仍由 BrainDecisionService 归一化。"
                ),
                "semantic_review_id": semantic_review.semantic_review_id,
                "fallback_reason": semantic_review.fallback_reason,
                "risk_guard_applied": semantic_review.risk_guard_applied,
            },
            clarification_candidates=[
                str(item) for item in clarification.get("questions", [])[:3]
            ],
            fallback_used=semantic_review.fallback_used,
            model_assist_enabled=self._model_assist_enabled,
            semantic_review_id=semantic_review.semantic_review_id,
            model_assist_attempted=semantic_review.model_assist_attempted,
            schema_valid=semantic_review.schema_valid,
            fallback_reason=semantic_review.fallback_reason,
            risk_guard_applied=semantic_review.risk_guard_applied,
            confidence=round(min(intent.confidence, mode.confidence, semantic.confidence), 2),
            status="model_assist_fallback"
            if semantic_review.fallback_used
            else "model_assist_reviewed",
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data=redact(
                    {
                        "review_id": review.review_id,
                        "trigger_reasons": review.trigger_reasons,
                        "fallback_used": review.fallback_used,
                        "semantic_review_id": review.semantic_review_id,
                    }
                ),
            )
        return LowConfidenceReviewOutcome(
            review=review,
            semantic_review=semantic_review,
            intent=semantic_outcome.intent,
            mode=semantic_outcome.mode,
            context=semantic_outcome.context,
            clarification=semantic_outcome.clarification,
        )


def _active_topic(
    text: str,
    previous: dict[str, Any] | None,
    working_state: dict[str, Any] | None,
    continuation: bool,
) -> str:
    if continuation:
        for source in (previous, working_state):
            value = (source or {}).get("active_topic")
            if value:
                return str(value)
    clean = _truncate(str(redact(text)).strip().replace("\n", " "), 96)
    for prefix in ["我们要", "帮我", "请", "继续", "再"]:
        clean = clean.removeprefix(prefix).strip()
    return clean or "当前对话"


def _user_goal(
    text: str,
    previous: dict[str, Any] | None,
    working_state: dict[str, Any] | None,
    continuation: bool,
) -> str:
    if continuation:
        for source in (previous, working_state):
            value = (source or {}).get("user_goal")
            if value:
                return str(value)
    return _truncate(str(redact(text)).strip().replace("\n", " "), 140)


def _goal_status(text: str, topic_shift: bool) -> str:
    if _denies_previous(text):
        return "changed"
    if topic_shift:
        return "changed"
    if _unknown_input(text):
        return "unclear"
    return "active"


def _merge_goal_history(
    previous: dict[str, Any] | None,
    old_goal: str,
    *,
    topic_shift: bool,
) -> list[dict[str, Any]]:
    history = list((previous or {}).get("goal_history") or [])
    if topic_shift and old_goal:
        history.append({"goal": old_goal, "status": "superseded", "at": utc_now_iso()})
    return history[-8:]


def _constraints_from_text(text: str) -> list[str]:
    return [_truncate(str(redact(text)).strip(), 160)] if _constraint_marked(text) else []


def _hard_constraints_from_text(text: str) -> list[str]:
    return (
        [_truncate(str(redact(text)).strip(), 160)]
        if any(marker in text for marker in ["必须", "不能", "不要", "禁止"])
        else []
    )


def _soft_preferences_from_text(text: str) -> list[str]:
    return (
        [_truncate(str(redact(text)).strip(), 160)]
        if any(marker in text for marker in ["偏好", "最好", "尽量", "喜欢"])
        else []
    )


def _decisions_from_text(text: str, *, supersedes: bool) -> list[dict[str, Any]]:
    if any(marker in text for marker in ["决定", "采用", "确认", "定下来"]):
        return [
            {
                "summary": _truncate(str(redact(text)).strip(), 160),
                "status": "active",
                "supersedes_previous": supersedes,
            }
        ]
    if supersedes:
        return [
            {
                "summary": _truncate(str(redact(text)).strip(), 160),
                "status": "supersedes_previous",
                "supersedes_previous": True,
            }
        ]
    return []


def _open_questions_from_text(text: str) -> list[str]:
    if text.strip().endswith(("?", "？")):
        return [_truncate(str(redact(text)).strip(), 140)]
    return []


def _candidate_actions(text: str) -> list[str]:
    mapping = {
        "删除": "filesystem.delete",
        "清空": "filesystem.delete",
        "整理文件夹": "filesystem.organize",
        "发帖": "external.publish",
        "发布": "external.publish",
        "购买": "external.purchase",
        "转账": "wallet.transfer",
        "支付": "wallet.payment",
        "签名": "wallet.sign",
        "执行": "runtime.execute",
        "运行": "runtime.execute",
    }
    return _dedupe([action for marker, action in mapping.items() if marker in text])


def _referenced_memories(text: str) -> list[dict[str, Any]]:
    if _memory_query(text) or _memory_write(text):
        return [{"type": "memory_reference", "summary": "explicit_memory_intent"}]
    return []


def _referenced_artifacts(
    text: str,
    working_state: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    refs = list((working_state or {}).get("referenced_artifacts") or [])
    if any(marker in text for marker in ["工件", "artifact", "上一版"]):
        refs.append({"type": "artifact_reference", "summary": "user_referenced_artifact"})
    return refs[-8:]


def _topic_shift(
    text: str,
    previous: dict[str, Any] | None,
    working_state: dict[str, Any] | None,
) -> bool:
    del working_state
    if not previous:
        return False
    if _continuation_reference(text):
        return False
    if _memory_write(text) or _memory_query(text) or _memory_correction(text):
        return False
    return any(
        marker in text
        for marker in ["换个话题", "另外", "转到", "不是这个", "改成", "换成", "推翻"]
    )


def _state_confidence(
    active_topic: str | None,
    constraints: list[str],
    decisions: list[dict[str, Any]],
    topic_shift: bool,
) -> float:
    score = 0.54
    if active_topic:
        score += 0.14
    if constraints:
        score += 0.08
    if decisions:
        score += 0.08
    if topic_shift:
        score -= 0.08
    return round(max(0.3, min(score, 0.9)), 2)


def _semantic_primary(
    *,
    memory: list[str],
    tools: list[str],
    skills: list[str],
    mcp: list[str],
    conversation: list[str],
    non_actionable: list[str],
) -> str:
    if tools:
        return "task_request"
    if skills:
        return "skill_request"
    if mcp:
        return "mcp_request"
    if "memory_update" in memory:
        return "memory_update"
    if "memory_query" in memory:
        return "memory_query"
    if "continue_previous_topic" in conversation:
        return "complex_dialogue"
    if "safe_plan_only" in non_actionable:
        return "simple_question"
    if "simple_question" in non_actionable:
        return "simple_question"
    if "casual_chat" in non_actionable:
        return "casual_chat"
    return "unknown" if _unknown_input(" ".join(non_actionable)) else "complex_dialogue"


def _semantic_confidence(reasons: list[str], conflicts: list[str]) -> float:
    score = 0.56 + min(len(reasons) * 0.06, 0.24) - min(len(conflicts) * 0.06, 0.2)
    return round(max(0.25, min(score, 0.92)), 2)


def _review_triggers(
    intent: Any,
    mode: Any,
    semantic: SemanticIntentCandidate,
    dialogue_state: DialogueState | None,
) -> list[str]:
    triggers: list[str] = []
    if intent.confidence < 0.55:
        triggers.append("intent_low_confidence")
    if mode.confidence < 0.55:
        triggers.append("mode_low_confidence")
    if len(
        _dedupe(
            [
                *semantic.actionable_intents,
                *semantic.non_actionable_intents,
                *semantic.memory_intents,
                *semantic.tool_intents,
                *semantic.skill_intents,
                *semantic.mcp_intents,
            ]
        )
    ) >= 3:
        triggers.append("multi_intent_complex")
    if semantic.conflicts:
        triggers.append("context_conflict")
    if semantic.risk_intents and "high_risk_vs_ambiguous_destination" in semantic.conflicts:
        triggers.append("high_risk_missing_destination")
    if mode.submode == "capability_boundary":
        triggers.append("capability_unavailable")
    if "continue_previous_topic" in semantic.conversation_intents and dialogue_state is None:
        triggers.append("ambiguous_continuation_without_state")
    return _dedupe(triggers)


def _missing_information(
    semantic: SemanticIntentCandidate,
    clarification: dict[str, Any],
) -> list[str]:
    if clarification.get("questions"):
        return [str(item) for item in clarification["questions"][:3]]
    missing: list[str] = []
    if "high_risk_vs_ambiguous_destination" in semantic.conflicts:
        missing.append("destination_or_scope")
    if "tool_vs_missing_asset" in semantic.conflicts:
        missing.append("available_capability_or_asset")
    if "ambiguous_reference" in semantic.conflicts:
        missing.append("referenced_context")
    return missing


def _simple_chat(text: str) -> bool:
    clean = text.strip().lower()
    return clean in {"你好", "hi", "hello", "在吗", "谢谢"} or clean.startswith("你好")


def _memory_query(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ["记得", "memory", "历史记忆", "长期记忆", "我说过", "之前说过", "偏好"]
    )


def _memory_write(text: str) -> bool:
    return any(marker in text for marker in ["记住：", "记住:", "请记住", "帮我记住"])


def _memory_correction(text: str) -> bool:
    lowered = text.lower()
    if "纠正记忆" in text or "记错" in text or "memory correction" in lowered:
        return True
    memory_markers = ["记忆", "记得", "我说过", "之前说过", "偏好"]
    correction_markers = ["不是", "改成", "换成", "以后不"]
    return any(marker in text for marker in memory_markers) and any(
        marker in text for marker in correction_markers
    )


def _skill_request(text: str) -> bool:
    return "skill" in text.lower() or "技能" in text


def _mcp_request(text: str) -> bool:
    return "mcp" in text.lower() or "外部服务" in text


def _tool_request(text: str) -> bool:
    if _safe_plan_only(text):
        return False
    return any(
        marker in text
        for marker in [
            "打开",
            "运行",
            "执行",
            "发送",
            "登录",
            "浏览器",
            "文件夹",
            "删除",
            "清空",
            "覆盖",
            "移动",
            "整理",
            "发帖",
            "发布",
            "购买",
            "下单",
            "转账",
            "支付",
            "签名",
        ]
    )


def _safe_plan_only(text: str) -> bool:
    return any(
        marker in text
        for marker in [
            "不要执行",
            "不执行",
            "别执行",
            "只分析",
            "只要方案",
            "只给方案",
            "先给方案",
            "只生成方案",
            "只写草稿",
        ]
    )


def _continuation_reference(text: str) -> bool:
    return any(
        marker in text
        for marker in ["继续", "刚才", "刚刚", "上一版", "上一个", "按之前", "之前方案", "上次方案"]
    )


def _constraint_marked(text: str) -> bool:
    markers = ["必须", "不要", "不能", "只", "先", "改成", "换成", "约束"]
    return any(marker in text for marker in markers)


def _denies_previous(text: str) -> bool:
    return any(marker in text for marker in ["不是", "不对", "记错", "推翻", "改成", "换成"])


def _unknown_input(text: str) -> bool:
    clean = text.strip()
    return not clean or not clean.strip(" ?？.!！。…~、，,;；:")


def _ambiguous_scope(text: str) -> bool:
    if any(marker in text for marker in ["那个", "这个", "某个", "一些", "全部", "所有"]):
        return True
    return not any(
        marker in text
        for marker in ["/", "\\", ".md", ".txt", ".json", "当前目录", "当前项目", "data/"]
    )


def _skill_available(snapshot: dict[str, Any]) -> bool:
    raw_skill = snapshot.get("skill")
    skill = raw_skill if isinstance(raw_skill, dict) else {}
    return bool(skill.get("available"))


def _mcp_available(snapshot: dict[str, Any]) -> bool:
    raw_mcp = snapshot.get("mcp_runtime")
    mcp = raw_mcp if isinstance(raw_mcp, dict) else {}
    return bool(mcp.get("available"))


def _merge_strings(existing: list[Any], new_items: list[str], *, limit: int) -> list[str]:
    merged: list[str] = []
    for item in [*existing, *new_items]:
        value = _truncate(str(item), 180)
        if value and value not in merged:
            merged.append(value)
    return merged[-limit:]


def _merge_decisions(
    existing: list[Any],
    new_items: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*existing, *new_items]:
        if isinstance(item, dict):
            summary = _truncate(str(item.get("summary") or ""), 180)
            value = {**item, "summary": summary}
        else:
            summary = _truncate(str(item), 180)
            value = {"summary": summary, "status": "active"}
        if summary and summary not in seen:
            seen.add(summary)
            merged.append(value)
    return merged[-limit:]


def _truncate(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[:limit]}..."


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
