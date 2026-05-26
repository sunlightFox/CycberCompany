from __future__ import annotations

# ruff: noqa: E501
from collections.abc import AsyncIterator
from typing import Any

from core_types import (
    ChatEvent,
    ChatEventType,
    ErrorCode,
    ResponsePlan,
    TraceSpanStatus,
    TraceSpanType,
    TraceStatus,
)
from trace_service import redact

from app.core.time import utc_now_iso
from app.services.chat_experience import ClarificationDecision


class ChatTurnFinalizeService:
    async def complete_without_model(
        self,
        facade: Any,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        text: str,
        root_span_id: str | None,
        *,
        intent: str,
        mode: str,
        response_plan: ResponsePlan | None = None,
        clarification_decision: ClarificationDecision | None = None,
    ) -> AsyncIterator[ChatEvent]:
        if getattr(facade, "_turn_requires_model_evidence", lambda _turn: False)(turn):
            async for event in facade._ensure_required_model_evidence(
                turn,
                events,
                text,
                root_span_id,
            ):
                yield event
        if hasattr(facade, "_append_channel_attachment_fact_footer"):
            text, _attachment_evidence = await facade._append_channel_attachment_fact_footer(turn, text)
        if hasattr(facade, "_finalize_without_model_visible_text"):
            text = await facade._finalize_without_model_visible_text(
                turn,
                events,
                text,
                root_span_id,
                intent=intent,
                mode=mode,
                response_plan=response_plan,
            )
        text = facade._style_visible_text(turn, text, response_plan=response_plan)
        if intent == "browser_read" and hasattr(facade, "_restore_browser_read_visible_evidence"):
            text = facade._restore_browser_read_visible_evidence(
                text,
                response_plan=response_plan,
            )
        text, response_filter = facade._response_coordinator.filter_text(text)
        yield await facade._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.RESPONSE_DELTA,
            {"text": text, "response_filter": response_filter},
        )
        async for event in facade._complete_model_turn(
            turn,
            events,
            text,
            root_span_id,
            usage={},
            finish_reason="stop",
            route={"brain_id": None, "fallback_used": False},
            intent=intent,
            mode=mode,
            response_plan=response_plan,
            clarification_decision=clarification_decision,
            response_filter=response_filter,
        ):
            yield event

    async def fail_turn(
        self,
        facade: Any,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        code: ErrorCode,
        message: str,
        root_span_id: str | None,
        *,
        persist_assistant: bool = False,
        response_plan: ResponsePlan | None = None,
    ) -> AsyncIterator[ChatEvent]:
        message = facade._presence_failure_text(turn, code, message)
        message = facade._style_visible_text(turn, message, response_plan=response_plan)
        message, response_filter = facade._response_coordinator.filter_text(message)
        assistant_message_id = None
        response_plan = response_plan or facade._composer.response_plan_for_failure(
            code=code,
            message=message,
        )
        recovery_payload = response_plan.structured_payload.get("recovery")
        recovery_payload = recovery_payload if isinstance(recovery_payload, dict) else None
        if facade._chat_experience is not None:
            turn["experience"] = await facade._chat_experience.mark_failure(
                turn=turn,
                code=code.value,
                message=message,
            )
            response_plan = facade._composer.response_plan_for_recovery(
                summary=message,
                error_code=code.value,
                recoverable=True,
                suggested_next_actions=turn["experience"].get("suggested_next_actions", []),
                base_plan=response_plan,
                recovery=recovery_payload,
            )
        if getattr(facade, "_failure_experience", None) is not None:
            await facade._failure_experience.record_failure(
                member_id=turn["member_id"],
                failure_class="runtime_failure",
                summary_text=message,
                reason_code=code.value,
                conversation_id=turn.get("conversation_id"),
                turn_id=turn.get("turn_id"),
                trace_id=turn.get("trace_id"),
                impact_scope="chat_runtime",
                severity="high" if code == ErrorCode.CHAT_RUNTIME_FAILED else "medium",
                evidence_refs=[
                    {"type": "turn", "turn_id": turn.get("turn_id")},
                    {"type": "trace", "trace_id": turn.get("trace_id")},
                ],
                source_payload={"error_code": code.value, "turn_status": "failed"},
            )
        response_plan = facade._with_experience_payload(turn, response_plan)
        response_plan = await facade._decorate_chat_payloads(turn, response_plan)
        response_plan = await facade._decorate_response_plan(
            turn,
            response_plan,
            assistant_text=message,
        )
        response_plan, shadow_trace = facade._decorate_chat_quality_shadow(
            turn,
            response_plan,
            assistant_text=message,
            turn_status="failed",
        )
        response_plan, message = await facade._apply_before_finalize_hook(
            turn=turn,
            response_plan=response_plan,
            assistant_text=message,
            turn_status="failed",
        )
        response_plan = facade._response_coordinator.finalize_plan(
            response_plan,
            message,
            authoritative_text=message,
            response_filter=response_filter,
        )
        message = response_plan.plain_text
        if facade._chat_experience is not None:
            user_message = await facade._chat_repo.get_message(turn["user_message_id"])
            user_text = str(user_message.get("content_text") if user_message else "")
            await facade._chat_experience.update_working_state(
                turn=turn,
                user_text=user_text,
                assistant_text=message,
                response_plan=response_plan.model_dump(mode="json"),
                status="recoverable",
            )
        if persist_assistant:
            compose_span = await facade._trace.start_span(
                turn["trace_id"],
                span_type=TraceSpanType.RESPONSE_COMPOSE,
                name="compose failure response",
                parent_span_id=root_span_id,
                metadata={"error_code": code.value},
            )
            await facade._trace.end_span(
                compose_span,
                output_data={
                    "text_chars": len(message),
                    "chat_quality_shadow": shadow_trace,
                    "response_plan": redact(response_plan.model_dump(mode="json")),
                },
            )
            assistant_message_id = await facade._persist_assistant_message(
                turn,
                message,
                {
                    "status": "failed",
                    "error_code": code.value,
                    "response_plan": response_plan.model_dump(mode="json"),
                },
                root_span_id,
            )
        failed_span = await facade._trace.start_span(
            turn["trace_id"],
            span_type=TraceSpanType.TURN_FAILED,
            name="turn failed",
            parent_span_id=root_span_id,
            metadata={"error_code": code.value},
        )
        await facade._trace.end_span(
            failed_span,
            status=TraceSpanStatus.FAILED,
            output_data={
                "message": message,
                "chat_quality_shadow": shadow_trace,
            },
            error_code=code.value,
        )
        yield await facade._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TURN_FAILED,
            {
                "code": code.value,
                "message": message,
                "assistant_message_id": assistant_message_id,
                "response_plan": response_plan.model_dump(mode="json"),
                "response_filter": response_filter,
            },
        )
        now = utc_now_iso()
        await facade._chat_repo.update_turn(
            turn["turn_id"],
            status="failed",
            assistant_message_id=assistant_message_id,
            error_code=code.value,
            error_message=message,
            events=events,
            experience=turn.get("experience") or {},
            updated_at=now,
            ended_at=now,
        )
        if facade._silent_continuity is not None:
            user_message = await facade._chat_repo.get_message(turn["user_message_id"])
            user_text = str(user_message.get("content_text") if user_message else "")
            await facade._silent_continuity.capture_turn(
                turn=turn,
                user_text=user_text,
                assistant_text=message,
                presence_payload=dict(turn.get("presence_runtime") or {}),
                response_plan=response_plan.model_dump(mode="json"),
                status="failed",
            )
        if root_span_id:
            await facade._trace.end_span(root_span_id, status=TraceSpanStatus.FAILED)
        await facade._trace.end_trace(turn["trace_id"], status=TraceStatus.FAILED)
