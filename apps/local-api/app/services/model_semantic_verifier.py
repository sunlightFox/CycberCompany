from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from core_types import (
    ContextDecision,
    IntentDecision,
    ModeDecision,
    SemanticIntentCandidate,
    SemanticReviewMergeResult,
    SemanticReviewRequest,
    SemanticReviewResult,
    SemanticReviewSuggestion,
    TraceSpanStatus,
    TraceSpanType,
)
from pydantic import ValidationError
from trace_service import TraceService, redact

from app.core.time import new_id, utc_now_iso


class ModelVerifierAdapter(Protocol):
    name: str

    def available(self, request: SemanticReviewRequest) -> bool: ...

    async def complete(self, request: SemanticReviewRequest) -> dict[str, Any] | str: ...


class DisabledModelVerifierAdapter:
    name = "disabled"

    def available(self, request: SemanticReviewRequest) -> bool:
        del request
        return False

    async def complete(self, request: SemanticReviewRequest) -> dict[str, Any] | str:
        del request
        raise RuntimeError("model verifier adapter is disabled")


@dataclass(frozen=True)
class SemanticReviewOutcome:
    result: SemanticReviewResult
    intent: IntentDecision
    mode: ModeDecision
    context: ContextDecision
    clarification: dict[str, Any]


class ModelAssistedVerifierService:
    def __init__(
        self,
        *,
        trace_service: TraceService,
        adapter: ModelVerifierAdapter | None = None,
        allow_cloud: bool = False,
    ) -> None:
        self._trace = trace_service
        self._adapter = adapter or DisabledModelVerifierAdapter()
        self._allow_cloud = allow_cloud

    async def review_and_merge(
        self,
        *,
        text: str,
        member_id: str,
        conversation_id: str | None,
        turn_id: str | None,
        brain_decision_id: str,
        intent: IntentDecision,
        mode: ModeDecision,
        context: ContextDecision,
        semantic: SemanticIntentCandidate,
        dialogue_state: Any | None,
        clarification: dict[str, Any],
        capability_snapshot: dict[str, Any],
        privacy_level: str,
        trigger_reasons: list[str],
        trace_id: str | None,
        root_span_id: str | None,
    ) -> SemanticReviewOutcome:
        review_id = new_id("semrev")
        started_at = utc_now_iso()
        request = SemanticReviewRequest(
            semantic_review_id=review_id,
            brain_decision_id=brain_decision_id,
            turn_id=turn_id,
            conversation_id=conversation_id,
            member_id=member_id,
            redacted_user_text=str(redact(text)),
            dialogue_state_summary=_dialogue_state_summary(dialogue_state),
            semantic_candidate_summary=_semantic_summary(semantic),
            intent_decision_summary=_intent_summary(intent),
            mode_decision_summary=_mode_summary(mode),
            context_decision_summary=_context_summary(context),
            capability_boundary_summary=_capability_boundary_summary(capability_snapshot),
            risk_signal_summary={
                "risk_signals": intent.risk_signals,
                "risk_intents": semantic.risk_intents,
                "requires_approval_before_execute": mode.requires_approval_before_execute,
            },
            privacy_level=privacy_level,
            privacy_policy="local_only"
            if privacy_level == "high" or not self._allow_cloud
            else "allow_configured_cloud",
            trigger_reasons=trigger_reasons,
            status="completed",
            trace_id=trace_id,
            created_at=started_at,
        )
        started_span = await self._start_span(
            trace_id,
            TraceSpanType.SEMANTIC_REVIEW_STARTED,
            "semantic review started",
            root_span_id,
            input_data={"semantic_review_id": review_id, "request": request.model_dump()},
        )
        suggestion: SemanticReviewSuggestion | None = None
        fallback_used = True
        fallback_reason: str | None = None
        model_assist_attempted = False
        schema_valid: bool | None = None
        usage: dict[str, Any] = {}
        latency_ms = 0
        error_code: str | None = None
        rejected_reasons: list[str] = []

        if request.privacy_policy == "local_only" and self._adapter.name != "local_fake":
            fallback_reason = "local_model_not_configured"
        elif not self._adapter.available(request):
            fallback_reason = "model_verifier_not_configured"
        else:
            model_assist_attempted = True
            call_span = await self._start_span(
                trace_id,
                TraceSpanType.SEMANTIC_REVIEW_MODEL_CALL,
                "semantic review model call",
                started_span or root_span_id,
                metadata={"semantic_review_id": review_id, "adapter": self._adapter.name},
            )
            call_started = time.perf_counter()
            try:
                raw = await self._adapter.complete(request)
                latency_ms = int((time.perf_counter() - call_started) * 1000)
                parsed = _parse_model_output(raw)
                suggestion = SemanticReviewSuggestion(**parsed)
                schema_valid = True
                fallback_used = False
                usage = _usage_from_raw(raw)
                rejected_reasons = _rejected_suggestion_reasons(suggestion)
                if rejected_reasons:
                    fallback_used = True
                    fallback_reason = "unsafe_suggestion_rejected"
            except TimeoutError:
                latency_ms = int((time.perf_counter() - call_started) * 1000)
                fallback_reason = "model_timeout"
                schema_valid = False
                error_code = "MODEL_TIMEOUT"
            except (json.JSONDecodeError, TypeError, ValidationError, ValueError):
                latency_ms = int((time.perf_counter() - call_started) * 1000)
                fallback_reason = "schema_invalid"
                schema_valid = False
                error_code = "PLAN_CANDIDATE_INVALID"
            except Exception:
                latency_ms = int((time.perf_counter() - call_started) * 1000)
                fallback_reason = "model_verifier_failed"
                schema_valid = False
                error_code = "MODEL_UNAVAILABLE"
            if call_span:
                await self._trace.end_span(
                    call_span,
                    status=TraceSpanStatus.FAILED if fallback_used else TraceSpanStatus.COMPLETED,
                    output_data=redact(
                        {
                            "semantic_review_id": review_id,
                            "fallback_used": fallback_used,
                            "fallback_reason": fallback_reason,
                            "schema_valid": schema_valid,
                            "latency_ms": latency_ms,
                            "usage": usage,
                        }
                    ),
                    error_code=error_code,
                )

        if suggestion is None or fallback_used:
            suggestion = _fallback_suggestion(
                semantic=semantic,
                mode=mode,
                clarification=clarification,
                fallback_reason=fallback_reason or "rule_fallback",
            )
            if fallback_reason is None:
                fallback_reason = "rule_fallback"
        schema_span = await self._start_span(
            trace_id,
            TraceSpanType.SEMANTIC_REVIEW_SCHEMA_VALIDATION,
            "semantic review schema validation",
            started_span or root_span_id,
            metadata={"semantic_review_id": review_id},
        )
        if schema_span:
            await self._trace.end_span(
                schema_span,
                output_data={
                    "schema_valid": schema_valid,
                    "fallback_used": fallback_used,
                    "rejected_reasons": rejected_reasons,
                },
            )
        merger = SemanticReviewMerger(trace_service=self._trace)
        merge = await merger.merge(
            semantic_review_id=review_id,
            brain_decision_id=brain_decision_id,
            intent=intent,
            mode=mode,
            context=context,
            clarification=clarification,
            suggestion=suggestion,
            semantic=semantic,
            trace_id=trace_id,
            root_span_id=started_span or root_span_id,
            fallback_used=fallback_used,
        )
        model_call = {
            "model_call_id": new_id("semcall"),
            "semantic_review_id": review_id,
            "brain_id": None,
            "provider": "none" if not model_assist_attempted else "adapter",
            "model_name": None,
            "adapter_name": self._adapter.name,
            "status": "skipped"
            if not model_assist_attempted
            else "fallback"
            if fallback_used
            else "completed",
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "latency_ms": latency_ms,
            "usage": usage,
            "schema_valid": bool(schema_valid),
            "error_code": error_code,
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        if fallback_used:
            fallback_span = await self._start_span(
                trace_id,
                TraceSpanType.SEMANTIC_REVIEW_FALLBACK,
                "semantic review fallback",
                started_span or root_span_id,
                metadata={"semantic_review_id": review_id},
            )
            if fallback_span:
                await self._trace.end_span(
                    fallback_span,
                    output_data={
                        "fallback_reason": fallback_reason,
                        "model_assist_attempted": model_assist_attempted,
                    },
                )
        if started_span:
            await self._trace.end_span(
                started_span,
                output_data={
                    "semantic_review_id": review_id,
                    "fallback_used": fallback_used,
                    "risk_guard_applied": merge.risk_monotonic_guard_applied,
                },
            )
        result = SemanticReviewResult(
            semantic_review_id=review_id,
            brain_decision_id=brain_decision_id,
            turn_id=turn_id,
            conversation_id=conversation_id,
            member_id=member_id,
            request=request,
            suggestion=suggestion,
            model_call=model_call,
            merge_result=merge,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            model_assist_attempted=model_assist_attempted,
            schema_valid=schema_valid,
            risk_guard_applied=merge.risk_monotonic_guard_applied,
            unsafe_downgrade_count=merge.unsafe_downgrade_count,
            status="fallback" if fallback_used else "reviewed",
            trace_id=trace_id,
            created_at=started_at,
        )
        return SemanticReviewOutcome(
            result=result,
            intent=IntentDecision(**merge.merged_intent),
            mode=ModeDecision(**merge.merged_mode),
            context=ContextDecision(**merge.merged_context),
            clarification=merge.merged_clarification,
        )

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        parent_span_id: str | None,
        *,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            parent_span_id=parent_span_id,
            input_data=redact(input_data),
            metadata=redact(metadata or {}),
        )


class SemanticReviewMerger:
    def __init__(self, *, trace_service: TraceService) -> None:
        self._trace = trace_service

    async def merge(
        self,
        *,
        semantic_review_id: str,
        brain_decision_id: str,
        intent: IntentDecision,
        mode: ModeDecision,
        context: ContextDecision,
        clarification: dict[str, Any],
        suggestion: SemanticReviewSuggestion,
        semantic: SemanticIntentCandidate,
        trace_id: str | None,
        root_span_id: str | None,
        fallback_used: bool,
    ) -> SemanticReviewMergeResult:
        span_id = (
            await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.SEMANTIC_REVIEW_MERGE,
                name="semantic review merge",
                parent_span_id=root_span_id,
                metadata={"semantic_review_id": semantic_review_id},
            )
            if trace_id
            else None
        )
        merged_intent = intent
        merged_mode = mode
        merged_context = context
        merged_clarification = dict(clarification)
        reason_codes = [
            "semantic_review_fallback_merge" if fallback_used else "semantic_review_merge"
        ]
        risk_guard = False
        unsafe_downgrades = 0
        high_risk = bool(intent.risk_signals or semantic.risk_intents)
        if high_risk and _suggests_risk_downgrade(suggestion, mode):
            risk_guard = True
            unsafe_downgrades += 1
            reason_codes.append("risk_monotonic_guard_applied")
        if suggestion.context_conflicts:
            reason_codes.append("model_context_conflict")
            selection = _append_unique(
                merged_context.selection_reason,
                ["semantic_review_context_conflict"],
            )
            merged_context = merged_context.model_copy(update={"selection_reason": selection})
        if suggestion.missing_information or suggestion.ambiguous_references:
            questions = _clarification_questions(suggestion, merged_clarification)
            if questions:
                merged_clarification = {
                    **merged_clarification,
                    "needs_clarification": True,
                    "reason": merged_clarification.get("reason")
                    if merged_clarification.get("reason") != "safe_to_continue"
                    else "semantic_review_missing_information",
                    "clarification_type": merged_clarification.get("clarification_type")
                    if merged_clarification.get("clarification_type") != "none"
                    else "semantic_review_missing_information",
                    "blocking_level": "blocks_execution" if high_risk else "can_answer_partially",
                    "questions": questions,
                }
                if high_risk:
                    merged_mode = merged_mode.model_copy(
                        update={
                            "mode": "ask_clarification",
                            "submode": "blocks_execution",
                            "fallback_mode": merged_mode.fallback_mode or "direct",
                            "reason_codes": _append_unique(
                                merged_mode.reason_codes,
                                ["semantic_review_requires_clarification"],
                            ),
                        }
                    )
                merged_intent = merged_intent.model_copy(update={"needs_clarification": True})
                reason_codes.append("semantic_review_missing_information")
        if suggestion.suggested_primary_intent in {
            intent.primary_intent,
            semantic.primary_intent,
        }:
            merged_intent = merged_intent.model_copy(
                update={
                    "confidence": round(min(0.95, intent.confidence + 0.03), 2),
                    "reason_codes": _append_unique(
                        intent.reason_codes,
                        ["semantic_review_confirms_intent"],
                    ),
                    "model_hint": {
                        **intent.model_hint,
                        "semantic_review": "confirmed",
                    },
                }
            )
        if mode.submode == "capability_boundary" and _suggests_capability_action(suggestion):
            reason_codes.append("capability_boundary_preserved")
            merged_mode = merged_mode.model_copy(
                update={
                    "reason_codes": _append_unique(
                        merged_mode.reason_codes,
                        ["semantic_review_capability_unavailable"],
                    )
                }
            )
        result = SemanticReviewMergeResult(
            merge_id=new_id("semmerge"),
            semantic_review_id=semantic_review_id,
            brain_decision_id=brain_decision_id,
            merged_intent=merged_intent.model_dump(mode="json"),
            merged_mode=merged_mode.model_dump(mode="json"),
            merged_context=merged_context.model_dump(mode="json"),
            merged_clarification=merged_clarification,
            reason_codes=_append_unique(reason_codes, merged_mode.reason_codes),
            risk_monotonic_guard_applied=risk_guard,
            unsafe_downgrade_count=unsafe_downgrades,
            status="guarded" if risk_guard else "completed",
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        if span_id:
            await self._trace.end_span(
                span_id,
                output_data=redact(
                    {
                        "semantic_review_id": semantic_review_id,
                        "reason_codes": result.reason_codes,
                        "risk_monotonic_guard_applied": risk_guard,
                        "unsafe_downgrade_count": unsafe_downgrades,
                    }
                ),
            )
        return result


def _dialogue_state_summary(dialogue_state: Any | None) -> dict[str, Any]:
    if dialogue_state is None:
        return {}
    source = (
        dialogue_state.model_dump(mode="json")
        if hasattr(dialogue_state, "model_dump")
        else dict(dialogue_state)
        if isinstance(dialogue_state, dict)
        else {}
    )
    return redact(
        {
            "active_topic": source.get("active_topic"),
            "user_goal": source.get("user_goal"),
            "goal_status": source.get("goal_status"),
            "topic_shift": source.get("topic_shift"),
            "open_questions": source.get("open_questions", [])[:3],
            "confidence": source.get("confidence"),
        }
    )


def _semantic_summary(semantic: SemanticIntentCandidate) -> dict[str, Any]:
    return semantic.model_dump(
        mode="json",
        include={
            "semantic_candidate_id",
            "primary_intent",
            "secondary_intents",
            "actionable_intents",
            "memory_intents",
            "tool_intents",
            "skill_intents",
            "mcp_intents",
            "risk_intents",
            "conversation_intents",
            "conflicts",
            "confidence",
            "reason_codes",
        },
    )


def _intent_summary(intent: IntentDecision) -> dict[str, Any]:
    return intent.model_dump(
        mode="json",
        include={
            "primary_intent",
            "secondary_intents",
            "risk_signals",
            "needs_memory",
            "needs_tool",
            "needs_skill",
            "needs_mcp",
            "needs_task",
            "needs_clarification",
            "confidence",
            "reason_codes",
        },
    )


def _mode_summary(mode: ModeDecision) -> dict[str, Any]:
    return mode.model_dump(
        mode="json",
        include={
            "mode",
            "submode",
            "requires_approval_before_execute",
            "fallback_mode",
            "confidence",
            "reason_codes",
        },
    )


def _context_summary(context: ContextDecision) -> dict[str, Any]:
    return context.model_dump(
        mode="json",
        include={
            "include_recent_messages",
            "include_conversation_state",
            "include_session_summary",
            "include_memory",
            "include_capability_summary",
            "include_asset_handles",
            "selection_reason",
        },
    )


def _capability_boundary_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw_skill = snapshot.get("skill")
    raw_mcp = snapshot.get("mcp_runtime")
    skill: dict[str, Any] = raw_skill if isinstance(raw_skill, dict) else {}
    mcp: dict[str, Any] = raw_mcp if isinstance(raw_mcp, dict) else {}
    return {
        "skill_available": bool(skill.get("available")),
        "skill_enabled_count": int(skill.get("enabled_count") or 0),
        "mcp_available": bool(mcp.get("available")),
        "mcp_ready_server_count": int(mcp.get("ready_server_count") or 0),
        "mcp_active_tool_count": int(mcp.get("active_tool_count") or 0),
    }


def _fallback_suggestion(
    *,
    semantic: SemanticIntentCandidate,
    mode: ModeDecision,
    clarification: dict[str, Any],
    fallback_reason: str,
) -> SemanticReviewSuggestion:
    return SemanticReviewSuggestion(
        suggested_primary_intent=semantic.primary_intent,
        suggested_secondary_intents=semantic.secondary_intents,
        suggested_mode=mode.mode,
        missing_information=[
            str(item)
            for item in clarification.get("questions", [])[:3]
        ],
        context_conflicts=semantic.conflicts,
        risk_notes=semantic.risk_intents,
        memory_notes=semantic.memory_intents,
        tool_notes=semantic.tool_intents,
        skill_notes=semantic.skill_intents,
        mcp_notes=semantic.mcp_intents,
        clarification_questions=[
            str(item)
            for item in clarification.get("questions", [])[:3]
        ],
        confidence=semantic.confidence,
        reason_summary=f"rule fallback used: {fallback_reason}",
    )


def _parse_model_output(raw: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(raw, dict):
        payload = raw.get("suggestion", raw)
    else:
        payload = json.loads(_strip_reasoning_tags(raw))
    if not isinstance(payload, dict):
        raise TypeError("semantic review model output must be an object")
    return payload


def _strip_reasoning_tags(text: str) -> str:
    cleaned = text
    for start, end in [("<think>", "</think>"), ("<reasoning>", "</reasoning>")]:
        while start in cleaned and end in cleaned:
            before, rest = cleaned.split(start, 1)
            _, after = rest.split(end, 1)
            cleaned = before + after
    return cleaned.strip()


def _usage_from_raw(raw: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(raw, dict) and isinstance(raw.get("usage"), dict):
        return dict(raw["usage"])
    return {"source": "adapter", "recorded": False}


def _rejected_suggestion_reasons(suggestion: SemanticReviewSuggestion) -> list[str]:
    serialized = json.dumps(suggestion.model_dump(mode="json"), ensure_ascii=False).lower()
    rejected: list[str] = []
    if any(marker in serialized for marker in ["bypass approval", "绕过审批", "不要审批"]):
        rejected.append("approval_bypass_suggestion")
    if any(marker in serialized for marker in ["execute_tool", "run_tool", "直接执行工具"]):
        rejected.append("direct_tool_execution_suggestion")
    if any(marker in serialized for marker in ["write_memory", "直接写记忆"]):
        rejected.append("direct_memory_write_suggestion")
    return rejected


def _suggests_risk_downgrade(
    suggestion: SemanticReviewSuggestion,
    mode: ModeDecision,
) -> bool:
    suggested_mode = (suggestion.suggested_mode or "").lower()
    if suggested_mode in {"direct", "direct_with_memory"} and (
        mode.requires_approval_before_execute or mode.mode == "ask_clarification"
    ):
        return True
    risk_text = " ".join(suggestion.risk_notes).lower()
    return any(marker in risk_text for marker in ["safe_to_execute", "no_approval_needed"])


def _suggests_capability_action(suggestion: SemanticReviewSuggestion) -> bool:
    return bool(suggestion.tool_notes or suggestion.skill_notes or suggestion.mcp_notes)


def _clarification_questions(
    suggestion: SemanticReviewSuggestion,
    clarification: dict[str, Any],
) -> list[str]:
    questions = [
        str(item)
        for item in [
            *(clarification.get("questions") or []),
            *suggestion.clarification_questions,
            *suggestion.missing_information,
            *suggestion.ambiguous_references,
        ]
        if str(item).strip()
    ]
    if not questions and suggestion.context_conflicts:
        questions = ["你希望我按哪个目标或上下文继续？"]
    return _append_unique([], questions)[:3]


def _append_unique(existing: list[str], items: list[str]) -> list[str]:
    result = list(existing)
    for item in items:
        if item and item not in result:
            result.append(item)
    return result
