from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core_types import (
    BrainDecisionBundle,
    ContextDecision,
    DialogueState,
    IntentDecision,
    ModeDecision,
    PrivacyLevel,
    SemanticIntentCandidate,
    TaskMode,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.design_alignment_repo import DesignAlignmentRepository
from app.db.repositories.skill_mcp_repo import SkillMcpRepository
from app.services.chat_intent_router import (
    is_explicit_download_request,
    is_file_mutation_request,
    is_host_filesystem_list_request,
    is_host_software_install_request,
    is_office_document_request,
    is_webpage_read_request,
)
from app.services.dialogue_semantics import (
    DialogueStateService,
    LowConfidenceDecisionReviewer,
    SemanticIntentAnalyzer,
)


@dataclass(frozen=True)
class BrainDecisionPreviewRequest:
    text: str
    member_id: str = "mem_xiaoyao"
    conversation_id: str | None = None
    privacy_level: str = "medium"


class BrainDecisionService:
    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        design_repo: DesignAlignmentRepository,
        skill_mcp_repo: SkillMcpRepository | None = None,
        trace_service: TraceService,
    ) -> None:
        self._chat_repo = chat_repo
        self._design_repo = design_repo
        self._skill_mcp_repo = skill_mcp_repo
        self._trace = trace_service
        self._dialogue_states = DialogueStateService(repo=chat_repo, trace_service=trace_service)
        self._semantic_analyzer = SemanticIntentAnalyzer(trace_service=trace_service)
        self._low_confidence = LowConfidenceDecisionReviewer(trace_service=trace_service)

    async def decide(
        self,
        *,
        text: str,
        member_id: str,
        conversation_id: str | None,
        turn_id: str | None = None,
        privacy_level: str = "medium",
        trace_id: str | None = None,
        root_span_id: str | None = None,
        persist: bool = True,
    ) -> BrainDecisionBundle:
        decision_id = new_id("bd")
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.BRAIN_DECISION_CHAIN,
                name="brain decision chain",
                parent_span_id=root_span_id,
                input_data={"text": redact(text), "member_id": member_id},
            )
            if trace_id
            else None
        )
        capability_snapshot = await self._capability_snapshot()
        working_state = (
            await self._chat_repo.get_working_state(conversation_id)
            if conversation_id
            else None
        )
        dialogue_state = await self._dialogue_states.derive(
            text=text,
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            working_state=working_state,
            trace_id=trace_id,
            root_span_id=span_id or root_span_id,
            persist=persist,
        )
        semantic = await self._semantic_analyzer.analyze(
            text=text,
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            dialogue_state=dialogue_state,
            capability_snapshot=capability_snapshot,
            privacy_level=privacy_level,
            trace_id=trace_id,
            root_span_id=span_id or root_span_id,
        )
        semantic = semantic.model_copy(update={"brain_decision_id": decision_id})
        intent = _intent_decision(
            text,
            privacy_level,
            capability_snapshot,
            working_state=working_state,
            dialogue_state=dialogue_state,
            semantic=semantic,
        )
        mode = _mode_decision(intent, capability_snapshot)
        clarification = _clarification_decision(text, intent, mode, semantic)
        if clarification["needs_clarification"]:
            mode = mode.model_copy(
                update={
                    "mode": "ask_clarification",
                    "submode": clarification["blocking_level"],
                    "fallback_mode": "direct",
                    "reason_codes": [*mode.reason_codes, clarification["reason"]],
                }
            )
            intent = intent.model_copy(update={"needs_clarification": True})
        context = _context_decision(
            intent,
            mode,
            bool(conversation_id),
            working_state=working_state,
            dialogue_state=dialogue_state,
            semantic=semantic,
        )
        review_outcome = await self._low_confidence.review(
            member_id=member_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            brain_decision_id=decision_id,
            intent=intent,
            mode=mode,
            context=context,
            semantic=semantic,
            dialogue_state=dialogue_state,
            clarification=clarification,
            capability_snapshot=capability_snapshot,
            privacy_level=privacy_level,
            text=text,
            trace_id=trace_id,
            root_span_id=span_id or root_span_id,
        )
        review = None
        semantic_review = None
        if review_outcome is not None:
            intent = review_outcome.intent
            mode = review_outcome.mode
            context = review_outcome.context
            clarification = review_outcome.clarification
            review = review_outcome.review
            semantic_review = review_outcome.semantic_review
        confidence = round(min(intent.confidence, mode.confidence), 2)
        bundle = BrainDecisionBundle(
            brain_decision_id=decision_id,
            intent=intent,
            mode=mode,
            context=context,
            clarification=clarification,
            dialogue_state=dialogue_state.model_dump(mode="json") if dialogue_state else None,
            semantic_intent_candidates=[semantic.model_dump(mode="json")],
            low_confidence_review=review.model_dump(mode="json") if review else None,
            semantic_review=semantic_review.model_dump(mode="json") if semantic_review else None,
            capability_snapshot=capability_snapshot,
            confidence=confidence,
            status="completed" if confidence >= 0.45 else "low_confidence",
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        if persist:
            await self._chat_repo.insert_brain_decision(
                {
                    **bundle.model_dump(mode="json"),
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "member_id": member_id,
                    "input_summary": _summary(text),
                }
            )
            await self._chat_repo.insert_semantic_intent_candidate(
                semantic.model_copy(
                    update={"brain_decision_id": decision_id}
                ).model_dump(mode="json")
            )
            if review is not None:
                await self._chat_repo.insert_low_confidence_review(
                    review.model_dump(mode="json")
                )
            if semantic_review is not None:
                await self._persist_semantic_review(
                    semantic_review.model_dump(mode="json")
                )
            if turn_id:
                await self._chat_repo.update_turn(
                    turn_id,
                    brain_decision_id=decision_id,
                    updated_at=utc_now_iso(),
                )
        if trace_id:
            context_span = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.BRAIN_CONTEXT_DECISION,
                name="brain context decision",
                parent_span_id=span_id or root_span_id,
                metadata={"brain_decision_id": decision_id},
            )
            await self._trace.end_span(
                context_span,
                output_data=redact(context.model_dump(mode="json")),
            )
            clarification_span = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.BRAIN_CLARIFICATION_DECISION,
                name="brain clarification decision",
                parent_span_id=span_id or root_span_id,
                metadata={"brain_decision_id": decision_id},
            )
            await self._trace.end_span(
                clarification_span,
                output_data=redact(clarification),
            )
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data={
                    "brain_decision_id": decision_id,
                    "primary_intent": intent.primary_intent,
                    "mode": mode.mode,
                    "confidence": confidence,
                    "reason_codes": intent.reason_codes + mode.reason_codes,
                },
            )
        return bundle

    async def preview(
        self,
        request: BrainDecisionPreviewRequest,
        *,
        trace_id: str | None = None,
    ) -> BrainDecisionBundle:
        return await self.decide(
            text=request.text,
            member_id=request.member_id,
            conversation_id=request.conversation_id,
            privacy_level=request.privacy_level,
            trace_id=trace_id,
            persist=False,
        )

    async def get_by_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._chat_repo.get_brain_decision_by_turn(turn_id)
        return await self._attach_phase18_evidence(row)

    async def get(self, decision_id: str) -> dict[str, Any] | None:
        row = await self._chat_repo.get_brain_decision(decision_id)
        return await self._attach_phase18_evidence(row)

    async def get_dialogue_state(self, conversation_id: str) -> dict[str, Any] | None:
        return await self._chat_repo.get_dialogue_state(conversation_id)

    async def list_semantic_intents(self, turn_id: str) -> list[dict[str, Any]]:
        return await self._chat_repo.list_semantic_intents_by_turn(turn_id)

    async def get_low_confidence_review(self, turn_id: str) -> dict[str, Any] | None:
        return await self._chat_repo.get_low_confidence_review_by_turn(turn_id)

    async def get_semantic_review(self, turn_id: str) -> dict[str, Any] | None:
        return await self._chat_repo.get_semantic_review_by_turn(turn_id)

    async def list_semantic_review_events(self, turn_id: str) -> list[dict[str, Any]]:
        return await self._chat_repo.list_semantic_review_events_by_turn(turn_id)

    async def _attach_phase18_evidence(
        self,
        row: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        decision_id = str(row["brain_decision_id"])
        row["semantic_intent_candidates"] = (
            await self._chat_repo.list_semantic_intents_by_decision(decision_id)
        )
        row["low_confidence_review"] = (
            await self._chat_repo.get_low_confidence_review_by_decision(decision_id)
        )
        row["semantic_review"] = await self._chat_repo.get_semantic_review_by_decision(
            decision_id
        )
        conversation_id = row.get("conversation_id")
        row["dialogue_state"] = (
            await self._chat_repo.get_dialogue_state(str(conversation_id))
            if conversation_id
            else None
        )
        return row

    async def _persist_semantic_review(self, semantic_review: dict[str, Any]) -> None:
        request = dict(semantic_review["request"])
        request["status"] = "completed"
        await self._chat_repo.insert_semantic_review_request(request)
        suggestion = semantic_review.get("suggestion")
        if suggestion is not None:
            await self._chat_repo.insert_semantic_review_suggestion(
                {
                    "suggestion_id": new_id("semsug"),
                    "semantic_review_id": semantic_review["semantic_review_id"],
                    "source": "model"
                    if semantic_review.get("model_assist_attempted")
                    and not semantic_review.get("fallback_used")
                    else "rule_fallback",
                    "suggestion": suggestion,
                    "confidence": suggestion.get("confidence", 0.0),
                    "schema_valid": bool(semantic_review.get("schema_valid")),
                    "rejected_reasons": [],
                    "created_at": utc_now_iso(),
                }
            )
        model_call = dict(semantic_review.get("model_call") or {})
        if model_call:
            await self._chat_repo.insert_semantic_review_model_call(model_call)
        merge = semantic_review.get("merge_result")
        if merge is not None:
            await self._chat_repo.insert_semantic_review_merge_result(merge)

    async def _capability_snapshot(self) -> dict[str, Any]:
        rows = await self._design_repo.list_runtime_contracts()
        contracts = {
            str(row["name"]): {
                "status": row["status"],
                "implemented": row["implemented"],
                "blocker_level": row["blocker_level"],
            }
            for row in rows
        }
        enabled_skill_count = 0
        ready_mcp_server_count = 0
        active_mcp_tool_count = 0
        if self._skill_mcp_repo is not None:
            enabled_skill_count = len(await self._skill_mcp_repo.list_skills(status="enabled"))
            for server in await self._skill_mcp_repo.list_mcp_servers():
                if server.get("status") != "ready":
                    continue
                ready_mcp_server_count += 1
                active_mcp_tool_count += sum(
                    1
                    for tool in await self._skill_mcp_repo.list_mcp_tools(
                        str(server["server_id"])
                    )
                    if tool.get("status") == "active"
                )
        skill_status = contracts.get("SkillEngine", {}).get("status", "not_started")
        mcp_status = contracts.get("MCPConnectionManager", {}).get("status", "not_started")
        return {
            "runtime_contracts": contracts,
            "tool_runtime": contracts.get("ToolRuntime", {}).get("status", "not_started"),
            "skill_engine": skill_status,
            "mcp": mcp_status,
            "skill": {
                "status": skill_status,
                "enabled_count": enabled_skill_count,
                "available": _status_available(skill_status) and enabled_skill_count > 0,
            },
            "mcp_runtime": {
                "status": mcp_status,
                "ready_server_count": ready_mcp_server_count,
                "active_tool_count": active_mcp_tool_count,
                "available": (
                    _status_available(mcp_status)
                    and ready_mcp_server_count > 0
                    and active_mcp_tool_count > 0
                ),
            },
            "model_assist": {
                "enabled": True,
                "real_model_call": False,
                "status": "implemented_with_fallback",
                "reason": "phase24_model_semantic_verifier_fallback_contract",
            },
        }


def _intent_decision(
    text: str,
    privacy_level: str,
    capability_snapshot: dict[str, Any],
    *,
    working_state: dict[str, Any] | None = None,
    dialogue_state: DialogueState | None = None,
    semantic: SemanticIntentCandidate | None = None,
) -> IntentDecision:
    clean = text.strip()
    lowered = clean.lower()
    secondary: list[str] = []
    risks: list[str] = []
    rule_hits: list[str] = []
    needs_memory = False
    needs_tool = False
    needs_skill = False
    needs_mcp = False
    needs_task = False
    primary = "casual_chat"
    domain = _domain(clean)
    complexity = _complexity(clean)
    is_continuation = _continuation_reference(clean)
    has_working_state = bool(working_state)

    if _unknown_input(clean):
        primary = "unknown"
        rule_hits.append("low_information_input")
    elif _log_data_extraction(clean):
        primary = "simple_question"
        secondary.append("data_extraction")
        rule_hits.append("data_extraction_question")
    elif _safe_plan_only(clean):
        primary = "simple_question"
        secondary.append("make_plan")
        rule_hits.append("safe_plan_only")
    elif _persona_boundary_question(clean):
        primary = "boundary_question"
        secondary.append("persona_capability_boundary")
        rule_hits.append("persona_boundary_question")
    elif _memory_write(clean):
        primary = "memory_update"
        needs_memory = True
        rule_hits.append("explicit_memory_write")
    elif _memory_query(clean):
        primary = "memory_query"
        needs_memory = True
        rule_hits.append("memory_reference")
    elif _memory_correction(clean):
        primary = "memory_correction"
        needs_memory = True
        rule_hits.append("memory_correction")
    elif _advice_strategy_direct(clean):
        primary = "complex_dialogue"
        secondary.append("make_plan")
        rule_hits.append("phase51_advice_strategy_direct")
    elif _office_document_request(clean):
        skill_available = _capability_available(capability_snapshot, "skill_engine")
        primary = "skill_request" if skill_available else "task_request"
        needs_skill = skill_available
        needs_task = True
        needs_tool = not skill_available
        secondary.append("generate_document")
        rule_hits.append("office_document_request")
    elif is_host_filesystem_list_request(clean):
        primary = "system_filesystem_read"
        needs_tool = True
        needs_task = False
        secondary.append("filesystem_readonly")
        rule_hits.append("host_filesystem_list_readonly")
    elif is_webpage_read_request(clean):
        primary = "browser_read"
        needs_tool = True
        needs_task = False
        secondary.append("webpage_readonly")
        rule_hits.append("browser_read_page_readonly")
    elif is_host_software_install_request(clean):
        primary = "task_request"
        needs_task = True
        needs_tool = True
        secondary.append("host_software_change")
        rule_hits.append("host_software_install_request")
    elif is_file_mutation_request(clean):
        primary = "task_request"
        needs_task = True
        needs_tool = True
        secondary.append("delete_or_destructive")
        secondary.append("filesystem_scope")
        risks.append("destructive_action")
        risks.append("filesystem_scope_required")
        rule_hits.append("file_mutation_request")
    elif _system_settings(clean):
        primary = "system_settings"
        rule_hits.append("settings_keyword")
    elif _approval_response(clean):
        primary = "approval_response"
        rule_hits.append("approval_response")
    elif _cancel_or_retry(clean):
        primary = "cancel_or_retry"
        rule_hits.append("cancel_retry")
    elif _skill_request(clean):
        skill_available = _capability_available(capability_snapshot, "skill_engine")
        primary = "skill_request"
        needs_skill = skill_available
        needs_task = skill_available
        rule_hits.append("skill_keyword")
    elif _mcp_request(clean):
        mcp_available = _capability_available(capability_snapshot, "mcp")
        primary = "mcp_request"
        needs_mcp = mcp_available
        needs_task = mcp_available
        rule_hits.append("mcp_keyword")
    elif _real_task_request(clean):
        primary = "task_request"
        needs_task = True
        rule_hits.append("real_task_request")
    elif _tool_request(clean):
        primary = "task_request"
        needs_tool = True
        needs_task = True
        rule_hits.append("tool_or_external_action")
    elif any(word in clean for word in ["写", "润色", "改写", "文案", "草稿", "文章"]):
        primary = "creative_writing"
        secondary.append("generate_document")
        rule_hits.append("writing_keyword")
    elif any(word in clean for word in ["总结", "summary", "summarize"]):
        primary = "summarization"
        rule_hits.append("summary_keyword")
    elif is_continuation and has_working_state:
        primary = "complex_dialogue"
        secondary.append("continue_previous_topic")
        rule_hits.append("working_state_continuation")
    elif complexity >= 0.5 or any(word in clean for word in ["方案", "对比", "架构", "排查"]):
        primary = "complex_dialogue"
        secondary.append("make_plan")
        rule_hits.append("complex_dialogue_keyword")
    elif clean.endswith("?") or clean.endswith("？") or any(
        word in lowered for word in ["什么", "为什么", "how", "why"]
    ):
        primary = "simple_question"
        rule_hits.append("question")

    if is_continuation:
        secondary.append("continue_previous_topic")
        if has_working_state and "working_state_continuation" not in rule_hits:
            rule_hits.append("working_state_continuation")
        needs_memory = primary == "memory_query"
    if any(word in clean for word in ["对比", "还是", "取舍"]):
        secondary.append("compare_options")
    if any(word in clean for word in ["调试", "报错", "错误", "排查"]):
        secondary.append("debug_problem")
    advice_strategy_direct = _advice_strategy_direct(clean)
    if any(word in clean for word in ["删除", "清空", "覆盖"]) and not advice_strategy_direct:
        secondary.append("delete_or_destructive")
        risks.append("destructive_action")
    if is_host_software_install_request(clean) and not advice_strategy_direct:
        risks.append("host_software_change")
        risks.append("destructive_action")
    if (
        _filesystem_scope_action(clean)
        and not advice_strategy_direct
        and not is_host_filesystem_list_request(clean)
    ):
        secondary.append("filesystem_scope")
        risks.append("filesystem_scope_required")
    if is_explicit_download_request(clean) or "截图" in clean:
        secondary.append("browser_side_effect")
        risks.append("browser_artifact_or_download")
    if any(word in clean for word in ["发帖", "发布", "发送", "提交"]):
        secondary.append("external_submit")
        risks.append("external_side_effect")
    if any(word in clean for word in ["购买", "下单", "转账", "支付", "签名"]):
        secondary.append("wallet_or_payment")
        risks.append("high_risk_financial_or_signature")
    if privacy_level == PrivacyLevel.HIGH.value or privacy_level == "high":
        risks.append("secret_or_sensitive")
    if semantic is not None:
        secondary.extend(semantic.secondary_intents)
        risks.extend(semantic.risk_intents)
        if semantic.memory_intents:
            needs_memory = needs_memory or semantic.primary_intent in {
                "memory_query",
                "memory_update",
            }
        if (
            semantic.tool_intents
            and not _safe_plan_only(clean)
            and not _persona_boundary_question(clean)
            and not _advice_strategy_direct(clean)
            and not _office_document_request(clean)
            and not is_host_filesystem_list_request(clean)
            and not is_webpage_read_request(clean)
        ):
            primary = "task_request"
            needs_tool = True
            needs_task = True
            rule_hits.append("semantic_tool_intent")
            if semantic.memory_intents:
                secondary.extend(semantic.memory_intents)
        if "old_goal_vs_new_goal" in semantic.conflicts and dialogue_state is not None:
            secondary.append("goal_changed")
            rule_hits.append("semantic_context_conflict")
        if semantic.conflicts:
            rule_hits.extend(semantic.conflicts)
    confidence = _confidence(primary, rule_hits, risks, clean)
    return IntentDecision(
        primary_intent=primary,
        secondary_intents=_dedupe(secondary),
        semantic_candidates=[
            {
                "semantic_candidate_id": semantic.semantic_candidate_id,
                "primary_intent": semantic.primary_intent,
                "confidence": semantic.confidence,
            }
        ]
        if semantic
        else [],
        conflicts=semantic.conflicts if semantic else [],
        actionable_intents=semantic.actionable_intents if semantic else [],
        memory_intents=semantic.memory_intents if semantic else [],
        tool_intents=semantic.tool_intents if semantic else [],
        risk_intents=semantic.risk_intents if semantic else [],
        domain=domain,
        complexity_score=complexity,
        privacy_level=privacy_level,
        risk_signals=_dedupe(risks),
        needs_memory=needs_memory,
        needs_tool=needs_tool,
        needs_skill=needs_skill,
        needs_mcp=needs_mcp,
        needs_task=needs_task,
        needs_clarification=False,
        confidence=confidence,
        reason_codes=_dedupe([*rule_hits, *risks]),
        rule_hits=rule_hits,
        model_hint={"enabled": False, "source": "rule_first_phase18"},
    )


def _mode_decision(
    intent: IntentDecision,
    capability_snapshot: dict[str, Any],
) -> ModeDecision:
    reasons: list[str] = []
    mode = TaskMode.DIRECT.value
    submode = "simple_answer"
    planner_hint = None
    fallback = None
    approval_first = bool(_execution_risks(intent.risk_signals))
    if intent.primary_intent in {"memory_query", "memory_update", "memory_correction"}:
        mode = TaskMode.DIRECT_WITH_MEMORY.value
        submode = "memory_answer"
        reasons.append("memory_visible_scope")
    elif intent.primary_intent == "system_filesystem_read":
        mode = TaskMode.DIRECT.value
        submode = "host_filesystem_read"
        approval_first = False
        reasons.append("readonly_host_tool_supported")
    elif intent.primary_intent == "browser_read":
        mode = TaskMode.DIRECT.value
        submode = "browser_readonly"
        approval_first = False
        reasons.append("readonly_browser_tool_supported")
    elif intent.primary_intent in {"task_request", "tool_request", "asset_management"}:
        mode = TaskMode.WORKFLOW.value
        submode = "plan_first" if approval_first else "workflow"
        planner_hint = "task_runtime"
        reasons.append("task_or_tool_required")
    elif intent.primary_intent == "skill_request":
        if _capability_available(capability_snapshot, "skill_engine"):
            mode = TaskMode.WORKFLOW.value
            planner_hint = "skill_match_then_task"
            reasons.append("skill_available")
        else:
            mode = TaskMode.DIRECT.value
            submode = "capability_boundary"
            fallback = "explain_unavailable"
            reasons.append(_skill_unavailable_reason(capability_snapshot))
    elif intent.primary_intent == "mcp_request":
        if _capability_available(capability_snapshot, "mcp"):
            mode = TaskMode.WORKFLOW.value
            planner_hint = "mcp_tool_via_runtime"
            reasons.append("mcp_degraded_or_available")
        else:
            mode = TaskMode.DIRECT.value
            submode = "capability_boundary"
            fallback = "explain_unavailable"
            reasons.append(_mcp_unavailable_reason(capability_snapshot))
    elif intent.primary_intent == "complex_dialogue":
        mode = TaskMode.DIRECT.value
        submode = "deep_answer"
        planner_hint = "structured_reasoning"
        reasons.append("complex_but_answerable")
    elif intent.primary_intent == "creative_writing":
        submode = "writing"
        reasons.append("writing_direct")
    elif intent.primary_intent == "boundary_question":
        submode = "boundary_answer"
        reasons.append("persona_capability_boundary")
    elif intent.primary_intent == "system_settings":
        mode = TaskMode.WORKFLOW.value
        planner_hint = "settings_guarded"
        reasons.append("settings_requires_backend_guard")
    elif intent.primary_intent == "unknown" or intent.confidence < 0.45:
        mode = "ask_clarification"
        submode = "low_confidence"
        fallback = "safe_direct"
        reasons.append("low_confidence")
    else:
        reasons.append("direct_answer_supported")
    return ModeDecision(
        mode=mode,
        submode=submode,
        planner_hint=planner_hint,
        requires_approval_before_execute=approval_first,
        fallback_mode=fallback,
        confidence=max(0.45, min(intent.confidence, 0.92)),
        reason_codes=reasons,
    )


def _context_decision(
    intent: IntentDecision,
    mode: ModeDecision,
    has_conversation: bool,
    *,
    working_state: dict[str, Any] | None = None,
    dialogue_state: DialogueState | None = None,
    semantic: SemanticIntentCandidate | None = None,
) -> ContextDecision:
    is_continuation = "continue_previous_topic" in intent.secondary_intents
    has_working_state = bool(working_state)
    include_memory = intent.needs_memory or intent.primary_intent == "memory_query"
    include_handles = bool(intent.needs_tool or intent.needs_task or intent.needs_mcp)
    has_context_conflict = bool(semantic and semantic.conflicts)
    include_conversation_state = has_conversation and (
        (is_continuation and has_working_state)
        or (dialogue_state is not None and bool(dialogue_state.topic_shift))
        or has_context_conflict
        or intent.primary_intent
        in {
            "complex_dialogue",
            "memory_query",
            "memory_update",
            "memory_correction",
            "task_request",
            "skill_request",
            "mcp_request",
        }
    )
    include_summary = has_conversation and (
        include_conversation_state
        or intent.primary_intent in {"task_request", "skill_request", "mcp_request"}
    )
    include_recent = has_conversation
    token_profile = "deep_dialogue" if intent.primary_intent == "complex_dialogue" else "balanced"
    if mode.submode == "writing":
        token_profile = "long_writing"
    reasons = ["current_input", "capability_boundary_summary"]
    if include_conversation_state:
        if is_continuation and dialogue_state is not None:
            reasons.append("dialogue_state_goal_continuation")
            if has_working_state:
                reasons.append("working_state_continuation")
        elif is_continuation and has_working_state:
            reasons.append("working_state_continuation")
        else:
            reasons.append("conversation_state")
    if include_recent:
        reasons.append("recent_messages")
    if include_summary:
        reasons.append("session_summary")
    if include_memory:
        reasons.append(
            "memory_explicit_query"
            if intent.primary_intent == "memory_query"
            else "memory_query_enabled"
        )
        if semantic and semantic.memory_intents:
            reasons.append("semantic_memory_needed")
        if intent.primary_intent == "memory_query":
            reasons.append("memory_query_enabled")
    if include_handles:
        reasons.append("asset_handle_summary_only")
    if mode.submode == "capability_boundary":
        reasons.extend(["capability_boundary_unavailable", "capability_unavailable"])
    if semantic and semantic.conflicts:
        reasons.append("context_conflict_detected")
    return ContextDecision(
        include_recent_messages=include_recent,
        include_conversation_state=include_conversation_state,
        include_session_summary=include_summary,
        include_memory=include_memory,
        memory_query={
            "enabled": include_memory,
            "layers": ["semantic", "episodic", "procedural"],
            "max_items": 8 if include_memory else 0,
        },
        include_persona=True,
        include_heart=True,
        include_capability_summary=True,
        include_asset_handles=include_handles,
        include_task_state=mode.mode in {TaskMode.WORKFLOW.value, TaskMode.AGENT.value},
        include_artifact_summary=intent.primary_intent in {"complex_dialogue", "task_request"},
        untrusted_refs=[],
        token_budget_profile=token_profile,
        selection_reason=reasons,
    )


def _clarification_decision(
    text: str,
    intent: IntentDecision,
    mode: ModeDecision,
    semantic: SemanticIntentCandidate | None = None,
) -> dict[str, Any]:
    del mode
    conflicts = set(semantic.conflicts if semantic else [])
    if intent.primary_intent == "boundary_question":
        return _no_clarification()
    if intent.primary_intent in {"system_filesystem_read", "browser_read"}:
        return _no_clarification()
    if "ambiguous_reference" in conflicts:
        return _clarify(
            "ambiguous_reference",
            ["你指的是上一轮哪个对象或方案？"],
            clarification_type="ambiguous_reference",
        )
    if "old_goal_vs_new_goal" in conflicts:
        if any(marker in text for marker in ["不对", "改成", "只做", "换成"]):
            high_risk_change = bool(
                {"high_risk_financial_or_signature", "external_side_effect"}
                & set(intent.risk_signals)
            )
            if not high_risk_change:
                return _no_clarification()
        return _clarify(
            "conflicting_context",
            ["你是要替换上一轮目标，还是在原方案上调整约束？"],
            clarification_type="conflicting_context",
        )
    if intent.confidence < 0.45:
        return _clarify(
            "low_intent_confidence",
            ["你希望我先回答、先规划，还是创建任务？"],
            clarification_type="missing_goal",
        )
    if not intent.risk_signals:
        return _no_clarification()
    if _safe_plan_only(text):
        return _no_clarification()
    if any(signal in intent.risk_signals for signal in ["high_risk_financial_or_signature"]):
        return _clarify(
            "high_risk_without_confirmation",
            ["要使用哪个账户或钱包？", "对象、金额或签名内容是什么？", "是否只需要方案说明？"],
            clarification_type="missing_destination",
        )
    if "external_side_effect" in intent.risk_signals:
        return _clarify(
            "missing_target_scope",
            ["目标平台或账号是什么？", "最终内容和受众范围是什么？", "是否只需要草稿？"],
            clarification_type="missing_destination",
        )
    if (
        "destructive_action" in intent.risk_signals
        or "filesystem_scope_required" in intent.risk_signals
    ) and _ambiguous_scope(text):
        if "任务" in text and "destructive_action" not in intent.risk_signals:
            return _no_clarification()
        return _clarify(
            "filesystem_scope_missing",
            ["目标文件或范围是什么？", "是否需要只读预览或备份？"],
            clarification_type="missing_scope",
        )
    return _no_clarification()


def _clarify(
    reason: str,
    questions: list[str],
    *,
    clarification_type: str,
) -> dict[str, Any]:
    return {
        "needs_clarification": True,
        "needed": True,
        "reason": reason,
        "clarification_type": clarification_type,
        "blocking_level": "blocks_execution",
        "questions": questions[:3],
        "assumptions_if_continue": [],
        "safe_partial_answer_allowed": reason != "high_risk_without_confirmation",
    }


def _no_clarification() -> dict[str, Any]:
    return {
        "needs_clarification": False,
        "needed": False,
        "reason": "safe_to_continue",
        "clarification_type": "none",
        "blocking_level": "none",
        "questions": [],
        "assumptions_if_continue": [],
        "safe_partial_answer_allowed": True,
    }


def _summary(text: str) -> str:
    clean = str(redact(text)).strip().replace("\n", " ")
    return clean if len(clean) <= 160 else f"{clean[:160]}..."


def _complexity(text: str) -> float:
    score = min(len(text.strip()) / 260, 0.45)
    score += 0.08 * sum(
        1
        for marker in ["方案", "架构", "对比", "权衡", "排查", "继续", "长期", "多步骤"]
        if marker in text
    )
    return round(min(score, 1.0), 2)


def _confidence(primary: str, rule_hits: list[str], risks: list[str], text: str) -> float:
    if not text.strip():
        return 0.2
    if primary == "unknown":
        return 0.28
    score = 0.58 + min(len(rule_hits) * 0.08, 0.24)
    if risks:
        score += 0.05
    if primary == "casual_chat" and len(text.strip()) > 80:
        score -= 0.18
    return round(max(0.25, min(score, 0.95)), 2)


def _safe_plan_only(text: str) -> bool:
    if _explicit_task_creation(text):
        return False
    real_execution_markers = [
        "调研",
        "检查",
        "整理",
        "基于当前仓库",
        "基于这个仓库",
    ]
    real_deliverable_markers = [
        "任务报告",
        "生成报告",
        "输出报告",
        "验收证据",
        "测试日志",
        "执行报告",
        "回归报告",
    ]
    if any(marker in text for marker in real_execution_markers) and any(
        marker in text for marker in real_deliverable_markers
    ):
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
            "总结",
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
            "知识",
            "概念",
            "区别",
            "压缩成",
            "压缩为",
            "归纳为",
            "原则",
            "验收原则",
            "应如何记录",
            "不要打开浏览器",
            "不要安装",
            "不要匹配",
            "不要运行",
        ]
    )


def _log_data_extraction(text: str) -> bool:
    return (
        "日志片段" in text
        or "最慢接口" in text
        or ("500" in text and "错误" in text and "几次" in text)
    )


def _unknown_input(text: str) -> bool:
    clean = text.strip()
    if not clean:
        return True
    without_punctuation = clean.strip(" ?？.!！。…~、，,;；:")
    return not without_punctuation


def _memory_query(text: str) -> bool:
    lowered = text.lower()
    explicit_markers = [
        "记得",
        "memory",
        "历史记忆",
        "长期记忆",
        "我说过",
        "之前说过",
        "上次说过",
        "偏好",
    ]
    return any(marker in lowered for marker in explicit_markers)


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


def _system_settings(text: str) -> bool:
    if _office_document_request(text):
        return False
    return any(marker in text for marker in ["配置", "设置", "模型", "大脑"])


def _approval_response(text: str) -> bool:
    return text.strip() in {"同意", "批准", "拒绝", "deny", "approve"} or any(
        marker in text for marker in ["我批准", "我拒绝", "确认执行"]
    )


def _cancel_or_retry(text: str) -> bool:
    return any(marker in text for marker in ["取消", "停止", "重试", "再试一次"])


def _skill_request(text: str) -> bool:
    if _safe_plan_only(text):
        return False
    lowered = text.lower()
    return "skill" in lowered or "技能" in text


def _office_document_request(text: str) -> bool:
    return is_office_document_request(text)


def _mcp_request(text: str) -> bool:
    if _safe_plan_only(text):
        return False
    lowered = text.lower()
    return "mcp" in lowered or "外部服务" in text


def _persona_boundary_question(text: str) -> bool:
    lowered = text.lower()
    identity_markers = [
        "你是真人",
        "你是人类",
        "你是不是人",
        "假装真人",
        "真实主观意识",
        "主观意识",
        "pretend human",
        "are you human",
        "real person",
    ]
    hidden_capability_markers = [
        "隐藏账号",
        "隐藏账户",
        "直接替我登录",
        "替我登录",
        "绕过系统",
        "绕过安全",
        "绕过审批",
        "绕过权限",
        "越权审批",
        "忽略审批",
        "不要审批",
        "无视安全",
        "直接操作",
        "偷偷",
        "后台偷偷",
        "登录账号",
        "后台账号",
        "系统管理员",
        "系统提示",
        "内部提示",
        "internal prompt",
        "system prompt",
        "root权限",
        "系统密钥",
        "私钥",
        "private key",
    ]
    return any(marker in lowered or marker in text for marker in identity_markers) or any(
        marker in text for marker in hidden_capability_markers
    )


def _explicit_task_creation(text: str) -> bool:
    if "不要创建任务" in text or "不创建任务" in text:
        return False
    return any(
        marker in text
        for marker in [
            "请创建一个任务",
            "创建一个任务",
            "请创建任务",
            "创建任务",
            "新建任务",
        ]
    )


def _real_task_request(text: str) -> bool:
    if _safe_plan_only(text) or _persona_boundary_question(text) or _advice_strategy_direct(text):
        return False
    if _explicit_task_creation(text):
        return True
    action_markers = [
        "调研",
        "研究",
        "检查",
        "整理",
        "汇总",
        "分析这些",
        "基于当前仓库",
        "基于这个仓库",
        "读取这些",
        "处理这些",
    ]
    deliverable_markers = [
        "任务报告",
        "生成报告",
        "输出报告",
        "验收证据",
        "测试日志",
        "执行报告",
        "回归报告",
    ]
    if any(marker in text for marker in action_markers) and any(
        marker in text for marker in deliverable_markers
    ):
        return True
    return any(marker in text for marker in ["请调研", "帮我整理这些测试日志"])


def _tool_request(text: str) -> bool:
    if _safe_plan_only(text) or _persona_boundary_question(text) or _advice_strategy_direct(text):
        return False
    if is_host_filesystem_list_request(text):
        return False
    if is_webpage_read_request(text):
        return False
    if ("下载" in text or "download" in text.lower()) and not is_explicit_download_request(text):
        text = text.replace("下载", "").replace("download", "")
    return any(
        marker in text
        for marker in [
            "打开",
            "运行",
            "执行",
            "发送",
            "登录",
            "下载",
            "截图",
            "浏览器",
            "文件夹",
            "删除",
            "清空",
            "覆盖",
            "移动",
            "发帖",
            "发布",
            "购买",
            "下单",
            "转账",
            "支付",
            "签名",
        ]
    )


def _advice_strategy_direct(text: str) -> bool:
    if _explicit_task_creation(text):
        return False
    hard_execution = [
        "运行命令",
        "执行命令",
        "打开网页",
        "打开浏览器",
        "下载",
        "删除",
        "登录",
        "截图",
        "发帖",
        "发布",
        "转账",
        "支付",
        "签名",
        "基于当前仓库",
        "基于这个仓库",
        "读取文件",
        "写文件",
    ]
    if any(marker in text for marker in hard_execution):
        return False
    advice_markers = [
        "建议",
        "取舍",
        "策略",
        "对比",
        "方案",
        "解释",
        "总结",
        "优缺点",
        "利弊",
        "权衡",
        "成本",
        "覆盖率",
        "速度",
        "如何选择",
        "怎么选",
        "医疗建议",
        "金融建议",
    ]
    return any(marker in text for marker in advice_markers)


def _filesystem_scope_action(text: str) -> bool:
    if _safe_plan_only(text):
        return False
    if is_host_filesystem_list_request(text):
        return False
    return any(marker in text for marker in ["文件夹", "目录", "文件", "移动"]) or any(
        marker in text for marker in ["整理文件", "整理目录", "整理这些测试日志"]
    )


def _ambiguous_scope(text: str) -> bool:
    if any(marker in text for marker in ["那个", "这个", "某个", "一些", "全部", "所有"]):
        return True
    return not any(
        marker in text
        for marker in ["/", "\\", ".md", ".txt", ".json", "当前目录", "当前项目", "data/"]
    )


def _domain(text: str) -> str:
    if any(marker in text for marker in ["代码", "后端", "API", "数据库", "架构", "报错"]):
        return "software_product"
    if any(marker in text for marker in ["文案", "文章", "报告"]):
        return "writing"
    if any(marker in text for marker in ["钱包", "支付", "转账"]):
        return "finance_or_wallet"
    return "general"


def _capability_available(snapshot: dict[str, Any], key: str) -> bool:
    if key == "skill_engine" and isinstance(snapshot.get("skill"), dict):
        return bool(snapshot["skill"].get("available"))
    if key == "mcp" and isinstance(snapshot.get("mcp_runtime"), dict):
        return bool(snapshot["mcp_runtime"].get("available"))
    status = str(snapshot.get(key) or "")
    return _status_available(status)


def _status_available(status: str) -> bool:
    return status in {"implemented", "degraded"}


def _execution_risks(risks: list[str]) -> list[str]:
    return [risk for risk in risks if risk != "secret_or_sensitive"]


def _continuation_reference(text: str) -> bool:
    return any(
        marker in text
        for marker in ["继续", "刚才", "刚刚", "上一版", "上一个", "按之前", "之前方案", "上次方案"]
    )


def _skill_unavailable_reason(snapshot: dict[str, Any]) -> str:
    raw_skill = snapshot.get("skill")
    skill = raw_skill if isinstance(raw_skill, dict) else {}
    if not _status_available(str(skill.get("status") or snapshot.get("skill_engine") or "")):
        return "skill_runtime_unavailable"
    if int(skill.get("enabled_count") or 0) <= 0:
        return "skill_no_enabled_skill"
    return "skill_unavailable"


def _mcp_unavailable_reason(snapshot: dict[str, Any]) -> str:
    raw_mcp = snapshot.get("mcp_runtime")
    mcp = raw_mcp if isinstance(raw_mcp, dict) else {}
    if not _status_available(str(mcp.get("status") or snapshot.get("mcp") or "")):
        return "mcp_runtime_unavailable"
    if int(mcp.get("ready_server_count") or 0) <= 0:
        return "mcp_no_ready_server"
    if int(mcp.get("active_tool_count") or 0) <= 0:
        return "mcp_no_active_tool"
    return "mcp_unavailable"


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
