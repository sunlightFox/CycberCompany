from __future__ import annotations

# ruff: noqa: E501
import time
from collections.abc import AsyncIterator
from typing import Any

from brain.adapters import (
    CancelToken,
    ModelAdapterError,
    ModelChatRequest,
    estimate_messages_tokens,
)
from core_types import (
    ChatEvent,
    ChatEventType,
    ErrorCode,
    RiskLevel,
    TraceSpanStatus,
    TraceSpanType,
)
from response_composer import ComposeRequest


class ChatModelExecutionService:
    async def call_model(
        self,
        facade: Any,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        context: Any,
        user_text: str,
        brain: dict[str, Any],
        model_params: dict[str, Any],
        root_span_id: str | None,
        cancel_token: CancelToken,
        *,
        intent: str,
        mode: str,
        fallback_used: bool,
    ) -> AsyncIterator[ChatEvent]:
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        channel_profile = facade._channel_profile_for_turn(turn)
        prompt_options = facade._prompt_options_for_turn(
            turn=turn,
            context=context,
            user_text=user_text,
            intent=intent,
            mode=mode,
        )
        prompt_assembly = facade._model_coordinator.model_assembly(
            context,
            user_text,
            prompt_mode=prompt_options["prompt_mode"],
            channel_profile=channel_profile,
            delivery_mode="final",
            turn_id=turn_id,
            include_dynamic_context=prompt_options["include_dynamic_context"],
            include_trusted_context=prompt_options["include_trusted_context"],
            include_untrusted_context=prompt_options["include_untrusted_context"],
            include_history=prompt_options["include_history"],
            include_session_summary=prompt_options["include_session_summary"],
            recent_history_limit=prompt_options["recent_history_limit"],
            dynamic_context_mode=prompt_options["dynamic_context_mode"],
            prompt_profile=prompt_options["prompt_profile"],
        )
        messages = prompt_assembly.messages
        prompt_metadata = prompt_assembly.metadata
        continuation_decision = facade._continuation.decide(
            turn=turn,
            user_text=user_text,
            context=context,
            intent=intent,
            mode=mode,
        )
        buffer_visible_response = continuation_decision.enabled
        model_span = await facade._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MODEL_CALL,
            name="call chat model",
            parent_span_id=root_span_id,
            metadata={
                "brain_id": brain["brain_id"],
                "provider": brain["provider"],
                "model_name": brain["model_name"],
                "is_local": brain["is_local"],
                "fallback_used": fallback_used,
                "continuation_enabled": continuation_decision.enabled,
                "continuation_reason_codes": continuation_decision.reason_codes,
                "prompt_assembly": prompt_metadata,
            },
            input_data={
                "message_count": len(messages),
                "input_token_estimate": estimate_messages_tokens(messages),
                **facade._prompt_payload_from_metadata(prompt_metadata),
            },
        )
        if not brain["is_local"]:
            await facade._audit.write_event(
                actor_type="system",
                action="model_call.cloud_used",
                object_type="brain",
                object_id=brain["brain_id"],
                summary="聊天 turn 使用了云端模型",
                risk_level=RiskLevel.R2,
                payload={"brain_id": brain["brain_id"], "turn_id": turn_id},
                trace_id=trace_id,
            )
        request = ModelChatRequest(
            model=str(brain["model_name"]),
            messages=messages,
            temperature=float(model_params.get("temperature") or 0.3),
            max_output_tokens=int(model_params.get("max_output_tokens") or 1024),
            top_p=float(model_params.get("top_p") or 0.9),
            timeout_seconds=int(model_params.get("timeout_seconds") or 180),
            stream=True,
            trace_id=trace_id,
            turn_id=turn_id,
            route_id=f"route_{brain['brain_id']}",
            privacy_level=turn.get("privacy_level") or "medium",
            first_token_timeout_seconds=30,
            retry_count=int(model_params.get("retry_count") or 1),
        )
        output_parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        delta_filter = facade._composer.begin_delta_stream()
        visible_filter = facade._response_coordinator.begin_visible_stream()
        model_call_started = time.perf_counter()
        try:
            async for model_event in facade._model_gateway.stream_chat(brain, request, cancel_token):
                if model_event.event == "started":
                    yield await facade._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_STARTED,
                        {"brain_id": brain["brain_id"]},
                    )
                elif model_event.event == "delta":
                    usage.update(model_event.usage)
                    text = visible_filter.feed(delta_filter.feed(model_event.text))
                    if text:
                        output_parts.append(text)
                        if not buffer_visible_response:
                            yield await facade._emit_and_record(
                                turn_id,
                                trace_id,
                                events,
                                ChatEventType.RESPONSE_DELTA,
                                {"text": text, "response_filter": visible_filter.summary()},
                            )
                elif model_event.event == "usage_delta":
                    usage.update(model_event.usage)
                elif model_event.event == "completed":
                    usage.update(model_event.usage)
                    finish_reason = model_event.finish_reason or "stop"
                    tail_text = visible_filter.feed(delta_filter.finish())
                    tail_text += visible_filter.finish()
                    if tail_text:
                        output_parts.append(tail_text)
                        if not buffer_visible_response:
                            yield await facade._emit_and_record(
                                turn_id,
                                trace_id,
                                events,
                                ChatEventType.RESPONSE_DELTA,
                                {"text": tail_text, "response_filter": visible_filter.summary()},
                            )
                    yield await facade._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_COMPLETED,
                        {
                            "finish_reason": finish_reason,
                            "usage": usage,
                            "response_filter": visible_filter.summary(),
                            "continuation_enabled": continuation_decision.enabled,
                        },
                    )
                    break
                elif model_event.event == "cancelled":
                    cancel_token.cancel()
                    break
        except ModelAdapterError as exc:
            await facade._trace.end_span(
                model_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": exc.code.value, "message": exc.message},
                error_code=exc.code.value,
            )
            raise
        if cancel_token.cancelled:
            await facade._trace.end_span(
                model_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.TURN_CANCELLED.value},
                error_code=ErrorCode.TURN_CANCELLED.value,
            )
            async for event in facade._cancel_turn_during_stream(turn, events, root_span_id):
                yield event
            return
        response_filter = visible_filter.summary()
        assistant_text = "".join(output_parts).strip()
        if not assistant_text:
            raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型没有返回可用文本")
        await facade._trace.end_span(
            model_span,
            output_data={
                "finish_reason": finish_reason,
                "usage": usage,
                "response_filter": response_filter,
                "continuation_enabled": continuation_decision.enabled,
            },
        )
        response_plan = None
        continuation_payload: dict[str, Any] | None = None
        if continuation_decision.enabled:
            response_plan, assistant_text, response_filter, continuation_payload, finish_reason, usage = await self._run_continuation_flow(
                facade,
                turn=turn,
                events=events,
                context=context,
                user_text=user_text,
                messages=messages,
                assistant_text=assistant_text,
                response_filter=response_filter,
                usage=usage,
                finish_reason=finish_reason,
                prompt_metadata=prompt_metadata,
                continuation_decision=continuation_decision,
                model_call_started=model_call_started,
                brain=brain,
                model_params=model_params,
                root_span_id=root_span_id,
            )
            yield await facade._emit_and_record(
                turn_id,
                trace_id,
                events,
                ChatEventType.RESPONSE_DELTA,
                {
                    "text": assistant_text,
                    "response_filter": response_filter,
                    **({"continuation": continuation_payload} if continuation_payload else {}),
                },
            )
        async for event in facade._complete_model_turn(
            turn,
            events,
            assistant_text,
            root_span_id,
            usage=usage,
            finish_reason=finish_reason,
            route={"brain_id": brain["brain_id"], "fallback_used": fallback_used},
            intent=intent,
            mode=mode,
            response_plan=response_plan,
            response_filter=response_filter,
            prompt_metadata=prompt_metadata,
        ):
            yield event

    async def _run_continuation_flow(self, facade: Any, **kwargs: Any) -> tuple[Any, str, Any, dict[str, Any] | None, str, dict[str, Any]]:
        turn = kwargs["turn"]
        context = kwargs["context"]
        user_text = kwargs["user_text"]
        messages = kwargs["messages"]
        assistant_text = kwargs["assistant_text"]
        prompt_metadata = kwargs["prompt_metadata"]
        continuation_decision = kwargs["continuation_decision"]
        model_call_started = kwargs["model_call_started"]
        brain = kwargs["brain"]
        model_params = kwargs["model_params"]
        root_span_id = kwargs["root_span_id"]
        usage = dict(kwargs["usage"])
        finish_reason = kwargs["finish_reason"]
        response_plan = await self._compose_response_plan(facade, turn, context, user_text, assistant_text, prompt_metadata)
        final_text_for_quality = facade._style_visible_text(turn, assistant_text, response_plan=response_plan)
        final_text_for_quality, final_filter = facade._response_coordinator.filter_text(final_text_for_quality)
        assistant_text = final_text_for_quality
        response_filter = final_filter
        initial_latency_ms = int((time.perf_counter() - model_call_started) * 1000)
        continuation_started = time.perf_counter()
        evaluation = facade._continuation.evaluate(
            text=assistant_text,
            user_text=user_text,
            decision=continuation_decision,
            response_quality_guard=response_plan.structured_payload.get("response_quality_guard"),
        )
        iterations = 0
        used_revision = False
        budget_exhausted = False
        usage = {"initial": usage, "continuation_iterations": 0}
        revision_latency_ms: int | None = None
        if evaluation.should_revise and continuation_decision.max_iterations > 0:
            try:
                revision_started = time.perf_counter()
                revision = await self.run_continuation_revision(
                    facade,
                    turn=turn,
                    events=kwargs["events"],
                    messages=messages,
                    user_text=user_text,
                    draft_text=assistant_text,
                    evaluation=evaluation,
                    brain=brain,
                    model_params=model_params,
                    root_span_id=root_span_id,
                )
                revision_latency_ms = int((time.perf_counter() - revision_started) * 1000)
                iterations = 1
                usage["continuation_iterations"] = 1
                usage["revision"] = revision["usage"]
                revised_text = str(revision.get("text") or "").strip()
                if revised_text:
                    assistant_text = revised_text
                    finish_reason = str(revision.get("finish_reason") or finish_reason)
                    used_revision = True
                    response_plan = None
            except ModelAdapterError as exc:
                if exc.code == ErrorCode.TURN_CANCELLED:
                    raise
                budget_exhausted = exc.code == ErrorCode.MODEL_TIMEOUT
                usage["continuation_error"] = exc.code.value
        if response_plan is None:
            response_plan = await self._compose_response_plan(facade, turn, context, user_text, assistant_text, prompt_metadata)
            assistant_text = facade._style_visible_text(turn, assistant_text, response_plan=response_plan)
            assistant_text, final_filter = facade._response_coordinator.filter_text(assistant_text)
            response_filter = final_filter
        evaluation = facade._continuation.evaluate(
            text=assistant_text,
            user_text=user_text,
            decision=continuation_decision,
            elapsed_ms=int((time.perf_counter() - continuation_started) * 1000),
            response_quality_guard=response_plan.structured_payload.get("response_quality_guard"),
        )
        used_safe_fallback = False
        if evaluation.verdict == "block":
            used_safe_fallback = True
            fallback_text = facade._continuation.safe_fallback_text(user_text=user_text, evaluation=evaluation)
            fallback_scenario = "tool_boundary" if set(evaluation.tags) & {"internal_jargon", "secret_leak", "false_done"} else "direct"
            fallback_result = await facade._composer.compose(
                ComposeRequest(
                    user_text=user_text,
                    result_summary=fallback_text,
                    scenario=fallback_scenario,
                    persona=facade._context_persona_payload(context),
                    heart=facade._context_heart_payload(context),
                    route_profile=str((turn.get("experience") or {}).get("route_profile") or ""),
                    channel_profile=facade._channel_profile_for_turn(turn),
                    prompt_mode=str(prompt_metadata.get("prompt_mode") or "full"),
                    prompt_snapshot_id=str(prompt_metadata.get("prompt_snapshot_id") or ""),
                    prompt_assembly_version=str(prompt_metadata.get("prompt_assembly_version") or "") or None,
                    stable_prompt_hash=str(prompt_metadata.get("stable_prompt_hash") or "") or None,
                    dynamic_context_hash=str(prompt_metadata.get("dynamic_context_hash") or "") or None,
                    trusted_context_hash=str(prompt_metadata.get("trusted_context_hash") or "") or None,
                    untrusted_context_hash=str(prompt_metadata.get("untrusted_context_hash") or "") or None,
                    history_context_hash=str(prompt_metadata.get("history_context_hash") or "") or None,
                    current_message_hash=str(prompt_metadata.get("current_message_hash") or "") or None,
                    prompt_section_ids=[str(item) for item in prompt_metadata.get("prompt_section_ids") or []],
                    prompt_sections=[dict(item) for item in prompt_metadata.get("prompt_sections") or [] if isinstance(item, dict)],
                )
            )
            response_plan = fallback_result.response_plan
            assistant_text = facade._style_visible_text(turn, fallback_result.text, response_plan=response_plan)
            assistant_text, final_filter = facade._response_coordinator.filter_text(assistant_text)
            response_filter = final_filter
            evaluation = facade._continuation.evaluate(
                text=assistant_text,
                user_text=user_text,
                decision=continuation_decision,
                elapsed_ms=int((time.perf_counter() - continuation_started) * 1000),
                response_quality_guard=response_plan.structured_payload.get("response_quality_guard"),
            )
        continuation_payload = None
        if used_revision or used_safe_fallback or evaluation.verdict != "good":
            continuation_payload = facade._continuation.payload(
                decision=continuation_decision,
                evaluation=evaluation,
                iterations=iterations,
                budget_exhausted=budget_exhausted,
                used_revision=used_revision,
                used_safe_fallback=used_safe_fallback,
                initial_latency_ms=initial_latency_ms,
                revision_latency_ms=revision_latency_ms,
                total_latency_ms=int((time.perf_counter() - continuation_started) * 1000),
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "continuation": continuation_payload,
                    },
                    "quality_markers": {
                        **response_plan.quality_markers,
                        "continuation_quality_verdict": evaluation.verdict,
                        "continuation_quality_tags": evaluation.tags,
                        "continuation_diagnostics": evaluation.diagnostics,
                    },
                }
            )
        return response_plan, assistant_text, response_filter, continuation_payload, finish_reason, usage

    async def _compose_response_plan(self, facade: Any, turn: dict[str, Any], context: Any, user_text: str, assistant_text: str, prompt_metadata: dict[str, Any]):
        compose_result = await facade._composer.compose(
            ComposeRequest(
                user_text=user_text,
                result_summary=assistant_text,
                scenario="direct",
                persona=facade._context_persona_payload(context),
                heart=facade._context_heart_payload(context),
                route_profile=str((turn.get("experience") or {}).get("route_profile") or ""),
                channel_profile=facade._channel_profile_for_turn(turn),
                prompt_mode=str(prompt_metadata.get("prompt_mode") or "full"),
                prompt_snapshot_id=str(prompt_metadata.get("prompt_snapshot_id") or ""),
                prompt_assembly_version=str(prompt_metadata.get("prompt_assembly_version") or "") or None,
                stable_prompt_hash=str(prompt_metadata.get("stable_prompt_hash") or "") or None,
                dynamic_context_hash=str(prompt_metadata.get("dynamic_context_hash") or "") or None,
                trusted_context_hash=str(prompt_metadata.get("trusted_context_hash") or "") or None,
                untrusted_context_hash=str(prompt_metadata.get("untrusted_context_hash") or "") or None,
                history_context_hash=str(prompt_metadata.get("history_context_hash") or "") or None,
                current_message_hash=str(prompt_metadata.get("current_message_hash") or "") or None,
                prompt_section_ids=[str(item) for item in prompt_metadata.get("prompt_section_ids") or []],
                prompt_sections=[dict(item) for item in prompt_metadata.get("prompt_sections") or [] if isinstance(item, dict)],
            )
        )
        return compose_result.response_plan

    async def run_continuation_revision(self, facade: Any, *, turn: dict[str, Any], events: list[dict[str, Any]], messages: list[dict[str, str]], user_text: str, draft_text: str, evaluation: Any, brain: dict[str, Any], model_params: dict[str, Any], root_span_id: str | None) -> dict[str, Any]:
        trace_id = turn["trace_id"]
        turn_id = turn["turn_id"]
        revision_messages = facade._continuation.revision_messages(messages=messages, user_text=user_text, draft_text=draft_text, evaluation=evaluation)
        revision_span = await facade._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MODEL_CALL,
            name="call chat model continuation revision",
            parent_span_id=root_span_id,
            metadata={
                "brain_id": brain["brain_id"],
                "provider": brain["provider"],
                "model_name": brain["model_name"],
                "continuation_iteration": 1,
                "quality_verdict": evaluation.verdict,
                "quality_tags": evaluation.tags,
            },
            input_data={"message_count": len(revision_messages), "input_token_estimate": estimate_messages_tokens(revision_messages)},
        )
        request = ModelChatRequest(
            model=str(brain["model_name"]),
            messages=revision_messages,
            temperature=min(float(model_params.get("temperature") or 0.3), 0.25),
            max_output_tokens=int(model_params.get("max_output_tokens") or 1024),
            top_p=float(model_params.get("top_p") or 0.9),
            timeout_seconds=min(int(model_params.get("timeout_seconds") or 180), 20),
            stream=True,
            trace_id=trace_id,
            turn_id=turn_id,
            route_id=f"route_{brain['brain_id']}:continuation:1",
            privacy_level=turn.get("privacy_level") or "medium",
            first_token_timeout_seconds=20,
            retry_count=0,
        )
        token = facade._events.token_for(turn_id)
        output_parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        delta_filter = facade._composer.begin_delta_stream()
        visible_filter = facade._response_coordinator.begin_visible_stream()
        try:
            async for model_event in facade._model_gateway.stream_chat(brain, request, token):
                if model_event.event == "started":
                    await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.MODEL_STARTED, {"brain_id": brain["brain_id"], "continuation_iteration": 1})
                elif model_event.event == "delta":
                    usage.update(model_event.usage)
                    text = visible_filter.feed(delta_filter.feed(model_event.text))
                    if text:
                        output_parts.append(text)
                elif model_event.event == "usage_delta":
                    usage.update(model_event.usage)
                elif model_event.event == "completed":
                    usage.update(model_event.usage)
                    finish_reason = model_event.finish_reason or "stop"
                    tail_text = visible_filter.feed(delta_filter.finish())
                    tail_text += visible_filter.finish()
                    if tail_text:
                        output_parts.append(tail_text)
                    await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.MODEL_COMPLETED, {"finish_reason": finish_reason, "usage": usage, "response_filter": visible_filter.summary(), "continuation_iteration": 1})
                    break
                elif model_event.event == "cancelled":
                    token.cancel()
                    break
        except ModelAdapterError as exc:
            await facade._trace.end_span(revision_span, status=TraceSpanStatus.FAILED, output_data={"error_code": exc.code.value, "message": exc.message}, error_code=exc.code.value)
            raise
        if token.cancelled:
            await facade._trace.end_span(revision_span, status=TraceSpanStatus.FAILED, output_data={"error_code": ErrorCode.TURN_CANCELLED.value}, error_code=ErrorCode.TURN_CANCELLED.value)
            raise ModelAdapterError(ErrorCode.TURN_CANCELLED, "生成已取消")
        text = "".join(output_parts).strip()
        if not text:
            await facade._trace.end_span(revision_span, status=TraceSpanStatus.FAILED, output_data={"error_code": ErrorCode.MODEL_PROTOCOL_ERROR.value}, error_code=ErrorCode.MODEL_PROTOCOL_ERROR.value)
            raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "续跑修订没有返回可用文本")
        await facade._trace.end_span(revision_span, output_data={"finish_reason": finish_reason, "usage": usage, "response_filter": visible_filter.summary(), "text_chars": len(text)})
        return {"text": text, "usage": usage, "finish_reason": finish_reason}
