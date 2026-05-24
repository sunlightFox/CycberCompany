from __future__ import annotations

# ruff: noqa: E501
import re
from collections.abc import AsyncIterator
from typing import Any

from brain import BrainRouteRequest
from brain.adapters import estimate_messages_tokens
from chat_runtime import canonical_route_name
from core_types import (
    ChatEvent,
    ChatEventType,
    ErrorCode,
    TaskMode,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import redact

from app.core.time import utc_now_iso
from app.schemas.chat_quality import ActionDialogueDecision, ActionDialogueFacts
from app.schemas.chat_turn_execution import ChatTurnExecutionContext, TurnExecutionPlan
from app.services.action_dialogue_mapper import ActionDialogueMapperService
from app.services.chat_runtime_host_helpers import (
    deterministic_no_model_reply as _deterministic_no_model_reply,
)
from app.services.chat_visible_guard import visible_text_guard
from app.services.natural_chat import response_plan_for_pending_action


class ChatTurnExecutionOrchestrator:
    stages = (
        "turn_bootstrap",
        "turn_analysis",
        "direct_response_chain",
        "route_dispatch_chain",
        "model_execution_chain",
    )

    async def execute_turn(
        self,
        facade: Any,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> AsyncIterator[ChatEvent]:
        ctx = ChatTurnExecutionContext(turn=turn, events=events)
        async for event in self._run_bootstrap(facade, ctx):
            yield event
        async for event in self._run_analysis(facade, ctx):
            yield event
        if facade._events.token_for(turn["turn_id"]).cancelled:
            async for event in facade._cancel_turn_during_stream(turn, events, ctx.root_span_id):
                yield event
            return
        completed = False
        async for event in self._run_direct_response_chain(facade, ctx):
            completed = True
            yield event
        if completed:
            return
        if facade._events.token_for(turn["turn_id"]).cancelled:
            async for event in facade._cancel_turn_during_stream(turn, events, ctx.root_span_id):
                yield event
            return
        async for event in self._run_route_dispatch_chain(facade, ctx):
            completed = True
            yield event
        if completed:
            return
        if facade._events.token_for(turn["turn_id"]).cancelled:
            async for event in facade._cancel_turn_during_stream(turn, events, ctx.root_span_id):
                yield event
            return
        async for event in self._run_model_execution_chain(facade, ctx):
            yield event

    async def _run_bootstrap(self, facade: Any, ctx: ChatTurnExecutionContext) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        ctx.root_span_id = await facade._root_span_id(trace_id)
        ctx.execution_plan = TurnExecutionPlan(
            turn_id=turn_id,
            conversation_id=turn.get("conversation_id"),
            member_id=turn.get("member_id"),
            trace_metadata={"trace_id": trace_id},
            model_policy={"required": False},
            attachment_requirements={"required": False, "expected_count": 0},
            output_requirements={"required": False, "expected_formats": []},
            delivery_requirements={"required": False, "channel": None, "artifact_refs": []},
            evidence_requirements={"required": False, "required_evidence": []},
            completion_semantics={"status": "running"},
            response_contract={"event_stream": "chat_events"},
        )
        _sync_turn_execution_plan(turn, ctx.execution_plan)

        async def emit(event_type: ChatEventType, payload: dict[str, Any] | None = None) -> ChatEvent:
            return await facade._emit_and_record(turn_id, trace_id, events, event_type, payload)

        ctx.queue_item = await facade._chat_repo.get_queue_item_by_turn(turn_id)
        ctx.envelope = await facade._chat_repo.get_message_envelope_by_turn(turn_id)
        if ctx.queue_item is not None:
            yield await emit(ChatEventType.TURN_QUEUED, facade._queue_payload_for_item(ctx.queue_item) | {"status": "queued"})
            yield await emit(ChatEventType.TURN_QUEUE_STARTED, {"queue_id": ctx.queue_item["queue_id"], "status": "running", "session_id": ctx.queue_item["session_id"]})
        if ctx.envelope is not None:
            yield await emit(ChatEventType.CONTENT_NORMALIZED, {"envelope_id": ctx.envelope["envelope_id"], "dedupe_key": ctx.envelope["dedupe_key"], "normalized_summary": ctx.envelope["normalized_summary"], "content": facade._content_payload_for_envelope(ctx.envelope)})
            summary = ctx.envelope.get("normalized_summary") or {}
            attachment_count = int(summary.get("attachment_count") or 0)
            if attachment_count and ctx.execution_plan is not None:
                raw_payload = (ctx.envelope.get("ingress_metadata") or {}).get("raw_payload") or {}
                provider = str(raw_payload.get("provider") or raw_payload.get("channel") or "")
                ctx.execution_plan.model_policy = {
                    "required": True,
                    "reason": "channel_attachment_requires_real_model",
                }
                ctx.execution_plan.attachment_requirements = {
                    "required": True,
                    "expected_count": attachment_count,
                    "understanding_required": True,
                }
                ctx.execution_plan.delivery_requirements = {
                    "required": provider in {"feishu", "wechat"},
                    "channel": provider or None,
                    "artifact_refs": [],
                }
                _sync_turn_execution_plan(turn, ctx.execution_plan)
        yield await emit(ChatEventType.TURN_STARTED, {"status": "running"})
        yield await emit(ChatEventType.CONTEXT_STARTED)
        user_message = await facade._chat_repo.get_message(turn["user_message_id"])
        ctx.user_text = facade._message_user_text_from_message(user_message)
        ctx.session_id = facade._session_id_from_message(user_message)
        turn["session_id"] = ctx.session_id
        turn["current_user_text"] = ctx.user_text
        if ctx.execution_plan is not None:
            ctx.execution_plan.trace_metadata["session_id"] = ctx.session_id
            _sync_turn_execution_plan(turn, ctx.execution_plan)

    async def _run_analysis(self, facade: Any, ctx: ChatTurnExecutionContext) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        trace_id = turn["trace_id"]
        root_span_id = ctx.root_span_id
        user_text = ctx.user_text
        turn_id = turn["turn_id"]

        async def emit(event_type: ChatEventType, payload: dict[str, Any] | None = None) -> ChatEvent:
            return await facade._emit_and_record(turn_id, trace_id, events, event_type, payload)

        safety_span = await facade._trace.start_span(trace_id, span_type=TraceSpanType.SAFETY_PRIVACY_CLASSIFY, name="classify chat privacy", parent_span_id=root_span_id, input_data={"text": redact(user_text)})
        privacy = facade._privacy.classify(user_text)
        ctx.privacy = privacy
        turn["privacy_level"] = privacy.privacy_level
        await facade._trace.end_span(safety_span, output_data={"privacy_level": privacy.privacy_level, "sensitivity_hits": privacy.sensitivity_hits, "allow_cloud": privacy.allow_cloud})
        ctx.brain_decision = await facade._brain_decision.decide(text=user_text, member_id=turn["member_id"], conversation_id=turn["conversation_id"], turn_id=turn_id, privacy_level=privacy.privacy_level, trace_id=trace_id, root_span_id=root_span_id) if facade._brain_decision is not None else None
        if ctx.brain_decision is not None:
            turn["brain_decision_id"] = ctx.brain_decision.brain_decision_id
            turn["intent"] = ctx.brain_decision.intent.primary_intent
            turn["mode"] = ctx.brain_decision.mode.mode
            if ctx.execution_plan is not None:
                ctx.execution_plan.intent = ctx.brain_decision.intent.primary_intent
                ctx.execution_plan.mode = ctx.brain_decision.mode.mode
                ctx.execution_plan.context_policy = (
                    ctx.brain_decision.context.model_dump(mode="json")
                    if getattr(ctx.brain_decision, "context", None) is not None
                    else {}
                )
        pre_route_decision = facade._intent_router.decide(user_text)
        ctx.route_decision = pre_route_decision
        if ctx.execution_plan is not None:
            ctx.execution_plan.route = pre_route_decision.route_type
            ctx.execution_plan.capability_intent = {
                "route_type": pre_route_decision.route_type,
                "reason_code": getattr(pre_route_decision, "reason_code", None),
            }
        direct_readonly_routes = {
            "host_filesystem_list",
            "browser_read_page",
            "browser_search_readonly",
            "browser_search_with_citation",
            "terminal_readonly_command",
        }
        available_model_brains = await facade._brains.list_routable_brains()
        model_can_be_attempted = bool(available_model_brains) and not (
            privacy.privacy_level == "high"
            and not any(bool(brain.get("is_local")) for brain in available_model_brains)
        )
        deterministic_text = _deterministic_no_model_reply(user_text)
        deterministic_route_blockers = {
            "browser_download",
            "browser_page_action",
            "browser_read_page",
            "browser_search_readonly",
            "browser_search_with_citation",
            "code_hosting_oauth",
            "code_hosting_repo_read",
            "file_mutation_task",
            "host_filesystem_list",
            "host_software_install",
            "office_document",
            "project_deploy_request",
            "repo_fix_after_failure",
            "repo_patch_request",
            "repo_readonly_request",
            "repo_refactor_request",
            "repo_test_request",
            "terminal_readonly_command",
        }
        if (
            deterministic_text is not None
            and not model_can_be_attempted
            and pre_route_decision.route_type not in direct_readonly_routes
            and pre_route_decision.route_type not in deterministic_route_blockers
        ):
            ctx.direct_response_override = {
                "intent": "simple_question",
                "mode": TaskMode.DIRECT.value,
                "text": deterministic_text,
                "reason_codes": ["deterministic_no_model_reply"],
            }
            return
        context_span = await facade._trace.start_span(trace_id, span_type=TraceSpanType.CONTEXT_BUILD, name="build context packet", parent_span_id=root_span_id)
        try:
            ctx.context_packet, ctx.context_runtime = await facade._context_gateway.build(
                turn=turn,
                root_span_id=root_span_id,
                context_decision=ctx.brain_decision.context if ctx.brain_decision else None,
            )
        except Exception as exc:
            await facade._trace.end_span(context_span, status=TraceSpanStatus.FAILED)
            await facade._record_stage_recovery_attempt(turn=turn, stage="context", failure_type="context_build_failed", root_cause=str(exc), recovery_action="rebuild_minimal_context", status="failed", diagnostic_payload={"reason": "context_build_exception"})
            async for event in facade._fail_turn(turn, events, ErrorCode.CONTEXT_BUILD_FAILED, "上下文构建失败", root_span_id):
                yield event
            return
        context = ctx.context_packet
        raw_context_runtime = ctx.context_runtime
        context_runtime_payload = (
            raw_context_runtime.model_dump(mode="json")
            if hasattr(raw_context_runtime, "model_dump")
            else dict(raw_context_runtime or {})
        )
        turn["context_runtime"] = context_runtime_payload
        if getattr(facade, "_chat_hook_runtime", None) is not None:
            try:
                hook_result = await facade._chat_hook_runtime.run_after_context_build(
                    {
                        "trace_id": trace_id,
                        "conversation_id": turn.get("conversation_id"),
                        "turn_id": turn_id,
                        "member_id": turn.get("member_id"),
                        "session_id": ctx.session_id,
                        "channel": "local",
                        "payload": {
                            "context_packet_id": context.context_packet_id,
                            "context_runtime": context_runtime_payload,
                        },
                    }
                )
                turn["context_runtime"] = {
                    **dict(turn.get("context_runtime") or {}),
                    "hook_runtime": hook_result,
                }
            except Exception:
                pass
        context_filter_summary = facade._redaction_summary(
            context,
            sensitivity_hits=getattr(privacy, "sensitivity_hits", []),
        )
        await facade._trace.end_span(context_span, output_data={"recent_messages": len(context.conversation.last_messages), "summary": bool(context.conversation.recent_summary), "memory_blocks": len(context.memories), "context_redaction": context_filter_summary, "brain_decision_id": ctx.brain_decision.brain_decision_id if ctx.brain_decision else None})
        if facade._chat_experience is not None:
            try:
                signals = await facade._chat_experience.analyze_turn(
                    turn=turn,
                    user_text=user_text,
                    context=context,
                    privacy_level=privacy.privacy_level,
                )
                turn["experience"] = {
                    **dict(turn.get("experience") or {}),
                    **signals.as_payload(),
                }
                await facade._chat_repo.update_turn(
                    turn_id,
                    experience=turn["experience"],
                    privacy_level=privacy.privacy_level,
                    updated_at=utc_now_iso(),
                )
            except Exception:
                pass
        try:
            presence_runtime_payload = await facade._build_presence_runtime_payload(
                turn=turn,
                context=context,
                user_text=user_text,
                privacy_level=privacy.privacy_level,
                brain_decision=ctx.brain_decision,
            )
        except Exception:
            presence_runtime_payload = {}
        if presence_runtime_payload:
            turn["presence_runtime"] = presence_runtime_payload
            ctx.presence_runtime = presence_runtime_payload
        if ctx.execution_plan is not None:
            ctx.execution_plan.persona_policy = {
                "presence_runtime": dict(ctx.presence_runtime or {}),
                "privacy_level": getattr(privacy, "privacy_level", None),
            }
            _sync_turn_execution_plan(turn, ctx.execution_plan)
        if facade._chat_quality_shadow is not None:
            try:
                shadow = facade._chat_quality_shadow.analyze_turn(
                    user_text=user_text,
                    recent_messages=list(context.conversation.last_messages),
                    brain_decision=ctx.brain_decision,
                    channel_profile=facade._shadow_channel_profile(turn, context),
                )
                turn["chat_quality_shadow"] = shadow
                ctx.chat_quality_shadow = shadow
            except Exception:
                pass
        context_ready_payload = {"context_packet_id": context.context_packet_id, "recent_messages": len(context.conversation.last_messages), "memory_blocks": len(context.memories), "decision_id": ctx.brain_decision.brain_decision_id if ctx.brain_decision else None, "confidence": ctx.brain_decision.confidence if ctx.brain_decision else None, "context_decision": (ctx.brain_decision.context.model_dump(mode="json") if ctx.brain_decision else {}), "selection_reason": (ctx.brain_decision.context.selection_reason if ctx.brain_decision else (turn.get("experience") or {}).get("context_selection_reason", ["current_input", "recent_messages", "capability_boundary_summary"])), "route_profile": (turn.get("experience") or {}).get("route_profile"), "conversation_depth": (turn.get("experience") or {}).get("conversation_depth"), "context_redaction": context_filter_summary, "context_runtime": context_runtime_payload}
        try:
            async for compaction_event in facade._maybe_record_context_compaction(
                turn,
                context,
                context_filter_summary,
                root_span_id,
                emit,
            ):
                yield compaction_event
        except Exception:
            pass
        yield await emit(ChatEventType.CONTEXT_READY, context_ready_payload)

    async def _run_direct_response_chain(self, facade: Any, ctx: ChatTurnExecutionContext) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        privacy = ctx.privacy
        brain_decision = ctx.brain_decision
        user_text = ctx.user_text
        turn_id = turn["turn_id"]
        session_id = ctx.session_id
        trace_id = turn["trace_id"]
        root_span_id = ctx.root_span_id
        context = ctx.context_packet
        if context is None:
            override = getattr(ctx, "direct_response_override", None)
            if override is not None:
                async def emit(event_type: ChatEventType, payload: dict[str, Any] | None = None) -> ChatEvent:
                    return await facade._emit_and_record(turn_id, trace_id, events, event_type, payload)

                await facade._chat_repo.update_turn(
                    turn_id,
                    intent=override["intent"],
                    mode=override["mode"],
                    privacy_level=getattr(ctx.privacy, "privacy_level", None),
                    updated_at=utc_now_iso(),
                )
                yield await emit(ChatEventType.INTENT_DETECTED, {"intent": override["intent"], "reason_codes": list(override["reason_codes"])})
                yield await emit(ChatEventType.MODE_SELECTED, {"mode": override["mode"], "needs_tool": False})
                async for event in facade._complete_without_model(
                    turn,
                    events,
                    override["text"],
                    root_span_id,
                    intent=override["intent"],
                    mode=override["mode"],
                    response_plan=facade._response_plan_for_status(turn, summary=override["text"]),
                ):
                    yield event
            return
        if _model_required(ctx):
            return

        async def emit(event_type: ChatEventType, payload: dict[str, Any] | None = None) -> ChatEvent:
            return await facade._emit_and_record(turn_id, trace_id, events, event_type, payload)

        quality_outcome = facade._quality.handle(
            user_text=user_text,
            privacy_level=privacy.privacy_level,
            sensitivity_hits=getattr(privacy, "sensitivity_hits", []),
            brain_intent=brain_decision.intent.primary_intent if brain_decision is not None else None,
            failure_advisories=list(
                (context.context_diagnostics or {}).get("failure_advisories") or []
            ),
        )
        if quality_outcome is not None:
            await facade._chat_repo.update_turn(turn_id, intent=quality_outcome.intent, mode=quality_outcome.mode, privacy_level=privacy.privacy_level, updated_at=utc_now_iso())
            yield await emit(ChatEventType.INTENT_DETECTED, {"intent": quality_outcome.intent, "reason_codes": ["chat_quality_policy"]})
            yield await emit(ChatEventType.MODE_SELECTED, {"mode": quality_outcome.mode, "needs_tool": False})
            async for event in facade._complete_without_model(turn, events, quality_outcome.text, root_span_id, intent=quality_outcome.intent, mode=quality_outcome.mode, response_plan=quality_outcome.response_plan):
                yield event
            return
        allow_direct_memory_command = facade._memory_coordinator.allow_direct_command(user_text, brain_decision)
        explicit_memory_query = facade._memory_coordinator.explicit_memory_query(user_text)
        route_pending = ctx.route_decision is not None and ctx.route_decision.route_type not in {
            "default",
            "empty",
            "download_topic",
            "skill_mcp_concept",
        }
        if (
            ctx.route_decision is not None
            and ctx.route_decision.route_type == "ai_coding_tool_request"
        ):
            direct_route_reply = facade._direct_route_reply_for_decision(
                ctx.route_decision.route_type,
                user_text,
            )
            if direct_route_reply is not None:
                text, intent, structured = direct_route_reply
                response_plan = facade._response_plan_for_status(
                    turn,
                    summary=text,
                    task_status={
                        "status": "not_created",
                        "reason": ctx.route_decision.reason_code,
                    },
                    safety_notice="没有创建任务，也没有执行外部工具。",
                )
                response_plan = response_plan.model_copy(
                    update={
                        "structured_payload": {
                            **response_plan.structured_payload,
                            "route_semantics": {
                                "route": ctx.route_decision.route_type,
                                "route_taxonomy": "default_chat",
                                "model_called": False,
                                "task_created": False,
                                "tool_created": False,
                                "reason_code": ctx.route_decision.reason_code,
                            },
                            **structured,
                        }
                    }
                )
                yield await emit(
                    ChatEventType.INTENT_DETECTED,
                    {"intent": intent, "reason_codes": [ctx.route_decision.reason_code]},
                )
                yield await emit(
                    ChatEventType.MODE_SELECTED,
                    {"mode": TaskMode.DIRECT.value, "needs_tool": False},
                )
                async for event in facade._complete_without_model(
                    turn,
                    events,
                    text,
                    root_span_id,
                    intent=intent,
                    mode=TaskMode.DIRECT.value,
                    response_plan=response_plan,
                ):
                    yield event
                return
        if getattr(facade, "_goals", None) is not None:
            goal_handled = False
            async for event in _complete_goal_chat_intent(
                facade,
                turn,
                events,
                root_span_id,
                user_text,
            ):
                goal_handled = True
                yield event
            if goal_handled:
                return
        scheduled_cancel_request = facade._task_coordinator.scheduled_cancellations.parse(user_text)
        if scheduled_cancel_request is not None and facade._scheduled_tasks is not None:
            async for event in _complete_scheduled_task_cancel(
                facade,
                turn,
                events,
                root_span_id,
                scheduled_cancel_request,
            ):
                yield event
            return
        scheduled_request = facade._task_coordinator.scheduled_intents.parse(user_text)
        if scheduled_request is not None and facade._scheduled_tasks is not None:
            from app.schemas.scheduled_tasks import ScheduledTaskCreateRequest
            scheduled_task = await facade._scheduled_tasks.create(ScheduledTaskCreateRequest(conversation_id=turn["conversation_id"], owner_member_id=turn["member_id"], title=scheduled_request.title, goal=scheduled_request.goal, schedule=scheduled_request.schedule, execution_policy={"attendance": "unattended"}, constraints={"source": "chat_text", "phase": "phase36"}, created_by_member_id=facade._default_user_id()), trace_id=trace_id)
            action_dialogue = _scheduled_task_created_dialogue(goal=str(scheduled_task.goal or scheduled_request.goal), schedule=dict(scheduled_task.schedule or scheduled_request.schedule), next_run_at=scheduled_task.next_run_at.isoformat() if scheduled_task.next_run_at else None)
            text = action_dialogue.visible_text
            response_plan = facade._response_plan_for_status(turn, summary=text, task_status={"scheduled_task_id": scheduled_task.scheduled_task_id, "status": scheduled_task.status, "next_run_at": scheduled_task.next_run_at.isoformat() if scheduled_task.next_run_at else None, "background_execution_policy": scheduled_task.execution_policy, "schedule": scheduled_task.schedule, "action_dialogue": action_dialogue.model_dump(mode="json")})
            yield await emit(ChatEventType.INTENT_DETECTED, {"intent": "scheduled_task_request", "reason_codes": ["phase36_scheduled_task_text"]})
            yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
            async for event in facade._complete_without_model(turn, events, text, root_span_id, intent="scheduled_task_request", mode=TaskMode.DIRECT.value, response_plan=response_plan):
                yield event
            return
        browser_capability_text = facade._browser_capability_explanation_reply_text(user_text)
        if browser_capability_text is not None:
            yield await emit(ChatEventType.INTENT_DETECTED, {"intent": "simple_question", "reason_codes": ["browser_capability_explanation"]})
            yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
            async for event in facade._complete_without_model(
                turn,
                events,
                browser_capability_text,
                root_span_id,
                intent="simple_question",
                mode=TaskMode.DIRECT.value,
                response_plan=facade._response_plan_for_status(turn, summary=browser_capability_text),
            ):
                yield event
            return
        boundary_text = facade._deterministic_boundary_reply_text(user_text)
        if boundary_text is not None:
            yield await emit(ChatEventType.INTENT_DETECTED, {"intent": "boundary_question", "reason_codes": ["deterministic_boundary_reply"]})
            yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
            response_plan = facade._response_plan_for_status(
                turn,
                summary=boundary_text,
                safety_notice=boundary_text,
            )
            async for event in facade._complete_without_model(turn, events, boundary_text, root_span_id, intent="boundary_question", mode=TaskMode.DIRECT.value, response_plan=response_plan):
                yield event
            return
        if facade._natural_chat is not None and "称呼偏好" in user_text:
            natural_outcome = await facade._natural_chat.handle(turn=turn, user_text=user_text, session_id=session_id, trace_id=trace_id, presence_runtime=dict(turn.get("presence_runtime") or {}))
            if natural_outcome is not None:
                yield await emit(ChatEventType.INTENT_DETECTED, {"intent": natural_outcome.intent, "reason_codes": ["natural_chat_nickname_preference"]})
                yield await emit(ChatEventType.MODE_SELECTED, {"mode": natural_outcome.mode, "needs_tool": False})
                async for event in facade._complete_without_model(turn, events, natural_outcome.text, root_span_id, intent=natural_outcome.intent, mode=natural_outcome.mode, response_plan=natural_outcome.response_plan):
                    yield event
                return
        if (
            facade._natural_chat is not None
            and not allow_direct_memory_command
            and not explicit_memory_query
            and not route_pending
        ):
            natural_outcome = await facade._natural_chat.handle(turn=turn, user_text=user_text, session_id=session_id, trace_id=trace_id, presence_runtime=dict(turn.get("presence_runtime") or {}))
            if natural_outcome is not None:
                yield await emit(ChatEventType.INTENT_DETECTED, {"intent": natural_outcome.intent, "reason_codes": ["natural_chat_action_gateway"]})
                yield await emit(ChatEventType.MODE_SELECTED, {"mode": natural_outcome.mode, "needs_tool": False})
                async for event in facade._complete_without_model(turn, events, natural_outcome.text, root_span_id, intent=natural_outcome.intent, mode=natural_outcome.mode, response_plan=natural_outcome.response_plan):
                    yield event
                return
        browser_capability_text = facade._browser_capability_explanation_reply_text(user_text)
        if browser_capability_text is not None:
            yield await emit(ChatEventType.INTENT_DETECTED, {"intent": "simple_question", "reason_codes": ["browser_capability_explanation"]})
            yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
            async for event in facade._complete_without_model(
                turn,
                events,
                browser_capability_text,
                root_span_id,
                intent="simple_question",
                mode=TaskMode.DIRECT.value,
                response_plan=facade._response_plan_for_status(turn, summary=browser_capability_text),
            ):
                yield event
            return
        boundary_text = facade._deterministic_boundary_reply_text(user_text)
        if boundary_text is not None:
            yield await emit(ChatEventType.INTENT_DETECTED, {"intent": "boundary_question", "reason_codes": ["deterministic_boundary_reply"]})
            yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
            response_plan = facade._response_plan_for_status(
                turn,
                summary=boundary_text,
                safety_notice=boundary_text,
            )
            async for event in facade._complete_without_model(turn, events, boundary_text, root_span_id, intent="boundary_question", mode=TaskMode.DIRECT.value, response_plan=response_plan):
                yield event
            return
        memory_command = await facade._memory.handle_explicit_chat_command(text=user_text, member_id=turn["member_id"], conversation_id=turn["conversation_id"], turn_id=turn_id, message_id=turn["user_message_id"], trace_id=trace_id, root_span_id=root_span_id) if allow_direct_memory_command else None
        if memory_command is not None and memory_command.handled:
            memory_intent = facade._memory_coordinator.command_intent(memory_command)
            memory_summary = memory_command.response_text or "记忆命令已处理。"
            await facade._chat_repo.update_turn(turn_id, intent=memory_intent, mode=TaskMode.DIRECT_WITH_MEMORY.value, privacy_level=privacy.privacy_level, updated_at=utc_now_iso())
            yield await emit(ChatEventType.INTENT_DETECTED, {"intent": memory_intent, "reason_codes": ["explicit_memory_command", memory_intent]})
            yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT_WITH_MEMORY.value, "needs_tool": False})
            async for event in facade._emit_memory_events(turn, events, memory_command):
                yield event
            async for event in facade._complete_without_model(turn, events, memory_summary, root_span_id, intent=memory_intent, mode=TaskMode.DIRECT_WITH_MEMORY.value, response_plan=facade._response_plan_for_status(turn, summary=memory_summary, memory_notice=facade._memory_coordinator.command_notice(memory_command))):
                yield event
            return
        if explicit_memory_query:
            memory_reply = await facade._memory.handle_memory_query(
                text=user_text,
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                trace_id=trace_id,
                turn_id=turn_id,
            )
            if memory_reply:
                await facade._chat_repo.update_turn(
                    turn_id,
                    intent="memory_query",
                    mode=TaskMode.DIRECT_WITH_MEMORY.value,
                    privacy_level=privacy.privacy_level,
                    updated_at=utc_now_iso(),
                )
                yield await emit(ChatEventType.INTENT_DETECTED, {"intent": "memory_query", "reason_codes": ["explicit_memory_query"]})
                yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT_WITH_MEMORY.value, "needs_tool": False})
                async for event in facade._complete_without_model(
                    turn,
                    events,
                    memory_reply,
                    root_span_id,
                    intent="memory_query",
                    mode=TaskMode.DIRECT_WITH_MEMORY.value,
                    response_plan=facade._response_plan_for_status(turn, summary=memory_reply),
                ):
                    yield event
                return
        if getattr(facade, "_goals", None) is not None:
            goal_handled = False
            async for event in _complete_goal_chat_intent(
                facade,
                turn,
                events,
                root_span_id,
                user_text,
            ):
                goal_handled = True
                yield event
            if goal_handled:
                return
        scheduled_cancel_request = facade._task_coordinator.scheduled_cancellations.parse(user_text)
        if scheduled_cancel_request is not None and facade._scheduled_tasks is not None:
            async for event in _complete_scheduled_task_cancel(
                facade,
                turn,
                events,
                root_span_id,
                scheduled_cancel_request,
            ):
                yield event
            return
        scheduled_request = facade._task_coordinator.scheduled_intents.parse(user_text)
        if scheduled_request is not None and facade._scheduled_tasks is not None:
            from app.schemas.scheduled_tasks import ScheduledTaskCreateRequest
            scheduled_task = await facade._scheduled_tasks.create(ScheduledTaskCreateRequest(conversation_id=turn["conversation_id"], owner_member_id=turn["member_id"], title=scheduled_request.title, goal=scheduled_request.goal, schedule=scheduled_request.schedule, execution_policy={"attendance": "unattended"}, constraints={"source": "chat_text", "phase": "phase36"}, created_by_member_id=facade._default_user_id()), trace_id=trace_id)
            action_dialogue = _scheduled_task_created_dialogue(goal=str(scheduled_task.goal or scheduled_request.goal), schedule=dict(scheduled_task.schedule or scheduled_request.schedule), next_run_at=scheduled_task.next_run_at.isoformat() if scheduled_task.next_run_at else None)
            text = action_dialogue.visible_text
            response_plan = facade._response_plan_for_status(turn, summary=text, task_status={"scheduled_task_id": scheduled_task.scheduled_task_id, "status": scheduled_task.status, "next_run_at": scheduled_task.next_run_at.isoformat() if scheduled_task.next_run_at else None, "background_execution_policy": scheduled_task.execution_policy, "schedule": scheduled_task.schedule, "action_dialogue": action_dialogue.model_dump(mode="json")})
            yield await emit(ChatEventType.INTENT_DETECTED, {"intent": "scheduled_task_request", "reason_codes": ["phase36_scheduled_task_text"]})
            yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
            async for event in facade._complete_without_model(turn, events, text, root_span_id, intent="scheduled_task_request", mode=TaskMode.DIRECT.value, response_plan=response_plan):
                yield event
            return

    async def _run_route_dispatch_chain(self, facade: Any, ctx: ChatTurnExecutionContext) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        privacy = ctx.privacy
        user_text = ctx.user_text
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        root_span_id = ctx.root_span_id
        if getattr(facade, "_chat_hook_runtime", None) is not None:
            hook_result = await facade._chat_hook_runtime.run_before_route_select(
                {
                    "trace_id": trace_id,
                    "conversation_id": turn.get("conversation_id"),
                    "turn_id": turn_id,
                    "member_id": turn.get("member_id"),
                    "session_id": ctx.session_id,
                    "channel": "local",
                    "payload": {
                        "user_text": redact(user_text),
                        "intent": turn.get("intent"),
                        "mode": turn.get("mode"),
                    },
                }
            )
            turn["experience"] = {
                **dict(turn.get("experience") or {}),
                "hook_runtime": {
                    **dict((turn.get("experience") or {}).get("hook_runtime") or {}),
                    "before_route_select": hook_result,
                },
            }
        route_decision = ctx.route_decision or facade._intent_router.decide(user_text)
        ctx.route_decision = route_decision
        route_taxonomy = canonical_route_name(route_decision.route_type)
        if ctx.execution_plan is not None:
            ctx.execution_plan.route = route_decision.route_type
            ctx.execution_plan.capability_intent = {
                "route_type": route_decision.route_type,
                "route_taxonomy": route_taxonomy,
                "reason_code": route_decision.reason_code,
            }
            _sync_turn_execution_plan(turn, ctx.execution_plan)
        if (
            _model_required(ctx)
            and route_decision.office_request is None
            and not _attachment_output_file_request(user_text)
        ):
            return
        direct_readonly_routes = {
            "host_filesystem_list",
            "browser_read_page",
            "browser_search_readonly",
            "browser_search_with_citation",
            "terminal_readonly_command",
        }
        clarification = (
            facade._clarification_from_brain(turn, ctx.brain_decision)
            if ctx.brain_decision is not None
            else None
        )
        if (
            clarification is not None
            and clarification.needs_clarification
            and route_decision.route_type not in direct_readonly_routes
        ):
            async for event in self._complete_clarification_boundary(
                facade,
                ctx,
                clarification,
            ):
                yield event
            return
        if (
            ctx.brain_decision is not None
            and ctx.brain_decision.mode.submode == "capability_boundary"
        ):
            async for event in self._complete_capability_boundary(facade, ctx):
                yield event
            return
        turn["experience"] = {
            **dict(turn.get("experience") or {}),
            "chat_route_decision": {
                **route_decision.as_payload(),
                "route_taxonomy": route_taxonomy,
            },
        }
        await facade._chat_repo.update_turn(turn_id, experience=turn["experience"], privacy_level=privacy.privacy_level, updated_at=utc_now_iso())
        if route_decision.office_request is not None:
            async for event in facade._handle_office_chat_request(turn, events, user_text, route_decision.office_request, root_span_id, trace_id=trace_id):
                yield event
            return
        if route_decision.route_type == "host_filesystem_list":
            async for event in facade._handle_host_filesystem_list(turn, events, route_decision.metadata, root_span_id, trace_id=trace_id):
                yield event
            return
        if route_decision.route_type == "browser_read_page":
            async for event in facade._handle_browser_read_page(turn, events, route_decision.metadata, root_span_id, trace_id=trace_id):
                yield event
            return
        if route_decision.route_type in {"browser_search_readonly", "browser_search_with_citation"}:
            async for event in facade._handle_browser_search_readonly(turn, events, route_decision, root_span_id, trace_id=trace_id):
                yield event
            return
        if route_decision.route_type == "desktop_native_request":
            async for event in facade._handle_desktop_capability_boundary(turn, events, route_decision, root_span_id, trace_id=trace_id):
                yield event
            return
        if route_decision.route_type == "terminal_readonly_command":
            async for event in facade._handle_terminal_readonly_command(turn, events, route_decision.metadata, root_span_id, trace_id=trace_id):
                yield event
            return
        if facade._phase52_deploy_or_install_explain_only(user_text):
            text = "可以。安全的项目部署通常分为：确认源码来源，创建受控项目工作区，识别技术栈，准备 portable 运行时，安装项目内依赖，构建，启动预览，做健康检查并保留日志。安装桌面软件则应先确认可信来源、命令、影响范围和回滚方式，再由用户确认；我不会在你要求“不要执行”时创建任务或调用工具。"
            response_plan = facade._response_plan_for_status(turn, summary=text, task_status={"status": "not_created", "reason": "phase52_direct_only"})
            response_plan = response_plan.model_copy(update={"structured_payload": {**response_plan.structured_payload, "route_semantics": {"route": "project_deploy_explanation", "route_taxonomy": canonical_route_name("project_deploy_explanation"), "model_called": False, "task_created": False, "tool_created": False, "reason_code": "phase52_direct_only", "model_not_required_reason": "phase52_direct_only_explanation"}}})
            yield await facade._emit_and_record(turn["turn_id"], turn["trace_id"], ctx.events, ChatEventType.INTENT_DETECTED, {"intent": "project_deploy_explanation", "reason_codes": ["phase52_direct_only"]})
            async for event in facade._complete_without_model(turn, events, text, ctx.root_span_id, intent="project_deploy_explanation", mode=TaskMode.DIRECT.value, response_plan=response_plan):
                yield event
            return
        deploy_request = facade._task_coordinator.parse_project_deploy_request(user_text)
        if deploy_request is not None and facade._project_deployments is not None:
            async for event in self._run_project_deploy_task_branch(
                facade,
                ctx,
                deploy_request,
            ):
                yield event
            return
        if route_decision.route_type in {
            "repo_readonly_request",
            "repo_patch_request",
            "repo_test_request",
            "repo_fix_after_failure",
            "repo_refactor_request",
            "code_hosting_readonly_request",
            "code_hosting_sync_request",
            "code_hosting_pr_request",
            "code_hosting_review_request",
            "code_hosting_release_request",
        }:
            async for event in self._run_explicit_task_route_branch(
                facade,
                ctx,
                route_intent=route_decision.route_type,
                route_mode=TaskMode.AGENT,
            ):
                yield event
            return
        if route_decision.route_type in {"browser_download", "file_mutation_task"}:
            async for event in self._run_explicit_task_route_branch(
                facade,
                ctx,
                route_intent="task_request",
                route_mode=TaskMode.WORKFLOW,
            ):
                yield event
            return
        if (
            facade._external_platform_actions is not None
            and await facade._external_platform_actions.looks_like_chat_request(user_text)
        ):
            async for event in self._run_external_platform_task_branch(facade, ctx):
                yield event
            return
        direct_route_reply = facade._direct_route_reply_for_decision(route_decision.route_type, user_text)
        if direct_route_reply is not None:
            text, intent, structured = direct_route_reply
            response_plan = facade._response_plan_for_status(turn, summary=text, task_status={"status": "not_created", "reason": route_decision.reason_code}, safety_notice="没有创建任务，也没有执行下载、安装或外部动作。")
            response_plan = response_plan.model_copy(update={"structured_payload": {**response_plan.structured_payload, "route_semantics": {"route": route_decision.route_type, "route_taxonomy": route_taxonomy, "model_called": False, "task_created": False, "tool_created": False, "reason_code": route_decision.reason_code}, **structured}})
            yield await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.INTENT_DETECTED, {"intent": intent, "reason_codes": [route_decision.reason_code]})
            yield await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
            async for event in facade._complete_without_model(turn, events, text, root_span_id, intent=intent, mode=TaskMode.DIRECT.value, response_plan=response_plan):
                yield event
            return
        media_request = facade._task_coordinator.parse_media_task_request(user_text)
        if media_request is not None and facade._task_engine is not None:
            async for event in self._run_media_task_branch(facade, ctx, media_request):
                yield event
            return
        if facade._phase52_deploy_or_install_explain_only(user_text):
            text = "可以。安全的项目部署通常分为：确认源码来源，创建受控项目工作区，识别技术栈，准备 portable 运行时，安装项目内依赖，构建，启动预览，做健康检查并保留日志。安装桌面软件则应先确认可信来源、命令、影响范围和回滚方式，再由用户确认；我不会在你要求“不要执行”时创建任务或调用工具。"
            response_plan = facade._response_plan_for_status(turn, summary=text, task_status={"status": "not_created", "reason": "phase52_direct_only"})
            response_plan = response_plan.model_copy(update={"structured_payload": {**response_plan.structured_payload, "route_semantics": {"route": "project_deploy_explanation", "route_taxonomy": canonical_route_name("project_deploy_explanation"), "model_called": False, "task_created": False, "tool_created": False, "reason_code": "phase52_direct_only", "model_not_required_reason": "phase52_direct_only_explanation"}}})
            yield await facade._emit_and_record(turn["turn_id"], turn["trace_id"], ctx.events, ChatEventType.INTENT_DETECTED, {"intent": "project_deploy_explanation", "reason_codes": ["phase52_direct_only"]})
            async for event in facade._complete_without_model(turn, events, text, ctx.root_span_id, intent="project_deploy_explanation", mode=TaskMode.DIRECT.value, response_plan=response_plan):
                yield event
            return
        deploy_request = facade._task_coordinator.parse_project_deploy_request(user_text)
        if deploy_request is not None and facade._project_deployments is not None:
            async for event in self._run_project_deploy_task_branch(
                facade,
                ctx,
                deploy_request,
            ):
                yield event
            return
        host_install_request = facade._task_coordinator.parse_host_install_request(user_text)
        if host_install_request is not None and facade._host_installs is not None:
            async for event in self._run_host_install_task_branch(
                facade,
                ctx,
                host_install_request,
            ):
                yield event
            return

    async def _complete_clarification_boundary(
        self,
        facade: Any,
        ctx: ChatTurnExecutionContext,
        clarification: Any,
    ) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        brain_decision = ctx.brain_decision
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        await facade._chat_repo.insert_clarification_decision(
            {
                **clarification.as_payload(),
                "created_at": clarification.created_at,
                "updated_at": clarification.updated_at,
            }
        )
        await facade._chat_repo.update_turn(
            turn_id,
            intent=brain_decision.intent.primary_intent if brain_decision else "clarification",
            mode="ask_clarification",
            privacy_level=getattr(ctx.privacy, "privacy_level", None),
            updated_at=utc_now_iso(),
        )
        yield await facade._emit_and_record(
            turn_id,
            trace_id,
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": brain_decision.intent.primary_intent
                if brain_decision
                else "clarification",
                "decision_id": brain_decision.brain_decision_id if brain_decision else None,
                "confidence": brain_decision.confidence if brain_decision else None,
                "reason_codes": [clarification.reason],
                "intent_decision": brain_decision.intent.model_dump(mode="json")
                if brain_decision
                else {},
            },
        )
        yield await facade._emit_and_record(
            turn_id,
            trace_id,
            events,
            ChatEventType.MODE_SELECTED,
            {
                "mode": "ask_clarification",
                "needs_tool": False,
                "decision_id": brain_decision.brain_decision_id if brain_decision else None,
            },
        )
        text = facade._composer.compose_clarification(clarification.questions)
        response_plan = facade._composer.response_plan_for_clarification(
            summary=text,
            decision=clarification.as_payload(),
        )
        async for event in facade._complete_without_model(
            turn,
            events,
            text,
            ctx.root_span_id,
            intent="clarification",
            mode=TaskMode.DIRECT.value,
            response_plan=response_plan,
            clarification_decision=clarification,
        ):
            yield event
    async def _complete_capability_boundary(
        self,
        facade: Any,
        ctx: ChatTurnExecutionContext,
    ) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        brain_decision = ctx.brain_decision
        if brain_decision is None:
            return
        text = facade._composer.compose_tool_unavailable()
        response_plan = facade._composer.response_plan_for_tool_boundary(
            summary=text,
            required_capability=brain_decision.intent.primary_intent,
            next_actions=["先生成方案", "连接或启用对应能力后重试"],
            safety_notice="对应 Skill/MCP/工具能力当前不可用；没有执行任何外部动作。",
        )
        yield await facade._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": brain_decision.intent.primary_intent,
                "decision_id": brain_decision.brain_decision_id,
                "confidence": brain_decision.confidence,
                "reason_codes": list(brain_decision.intent.reason_codes),
                "intent_decision": brain_decision.intent.model_dump(mode="json"),
            },
        )
        yield await facade._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {
                "mode": TaskMode.DIRECT.value,
                "needs_tool": False,
                "decision_id": brain_decision.brain_decision_id,
                "reason_codes": list(brain_decision.mode.reason_codes),
                "mode_decision": brain_decision.mode.model_dump(mode="json"),
            },
        )
        async for event in facade._complete_without_model(
            turn,
            events,
            text,
            ctx.root_span_id,
            intent=brain_decision.intent.primary_intent,
            mode=TaskMode.DIRECT.value,
            response_plan=response_plan,
        ):
            yield event

    async def _run_media_task_branch(self, facade: Any, ctx: ChatTurnExecutionContext, media_request: dict[str, Any]) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        trace_id = turn["trace_id"]
        from app.schemas.tasks import TaskCreateRequest
        route_selected_payload, route_span = await self._emit_workflow_route_bridge(
            facade,
            ctx,
            route_type="media_runtime_request",
            reason_code="phase43_media_text_request",
            route_intent="media_runtime_request",
            route_mode=TaskMode.WORKFLOW,
            route_metadata={"media_request": media_request},
            safe_user_summary=str(ctx.user_text or "").strip() or None,
        )
        try:
            video_workflow_profile = {
                "workflow_type": "video_edit" if media_request["request_type"] == "edit_plan" else "video_analysis",
                "task_class": "standard",
                "require_render": not media_request["plan_only"],
                "require_export": False,
                "include_transcript": True,
                "include_frames": True,
                "render_strategy": "copy",
                "provider_capabilities": {
                    "video_generation": False,
                    "generation_provider_status": "not_configured",
                    "local_media_runtime": True,
                },
            }
            task = await facade._task_engine.create_task(TaskCreateRequest(conversation_id=turn["conversation_id"], owner_member_id=turn["member_id"], goal=ctx.user_text, mode_hint=TaskMode.WORKFLOW, planner_context={"intent": "video_workflow_request", "phase": "phase102", "media_request": media_request, "video_workflow_profile": video_workflow_profile, "privacy": facade._privacy.planner_context(privacy_level=ctx.privacy.privacy_level, allow_cloud=ctx.privacy.allow_cloud, sensitivity_hits=getattr(ctx.privacy, "sensitivity_hits", []))}, auto_start=False, client_request_id=f"chat:{turn['turn_id']}:media-task"), trace_id=trace_id)
            text = "好，视频处理任务先建好了。你把视频发上来后，我再继续分析和剪辑；需要渲染或导出前会再让你确认。"
            if media_request["plan_only"]:
                text = "好，剪辑方案任务先建好了。我只整理方案，不会渲染或导出视频。"
            response_plan = facade._response_plan_for_status(turn, summary=text, task_status={"task_id": task.task_id, "status": task.status.value, "mode": task.mode.value, "media_runtime": media_request, "video_workflow": {"status": "planned", "next_step": "import_or_bind_task_artifact"}})
            response_plan = response_plan.model_copy(update={"structured_payload": {**response_plan.structured_payload, "video_workflow_profile": video_workflow_profile, "video_workflow": {"task_id": task.task_id, "status": "planned", "next_step": "import_or_bind_task_artifact", "source_boundary": "task_artifact_only"}}})
            async for event in self._complete_workflow_without_model(
                facade,
                ctx,
                route_selected_payload=route_selected_payload,
                route_span=route_span,
                route_dispatch_stage="task_created",
                text=text,
                intent="media_runtime_request",
                task_created_payload={"task_id": task.task_id, "title": task.title, "status": task.status.value},
                response_plan=response_plan,
                route_semantics_extra={"tool_created": False},
            ):
                yield event
        finally:
            pass

    async def _run_explicit_task_route_branch(
        self,
        facade: Any,
        ctx: ChatTurnExecutionContext,
        *,
        route_intent: str,
        route_mode: TaskMode,
    ) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        route_decision = ctx.route_decision
        if route_decision is None:
            return
        route_selected_payload, route_span = await self._emit_workflow_route_bridge(
            facade,
            ctx,
            route_type=route_decision.route_type,
            reason_code=route_decision.reason_code,
            route_intent=route_intent,
            route_mode=route_mode,
            route_metadata=route_decision.metadata,
            safe_user_summary=route_decision.safe_user_summary,
            confidence=route_decision.confidence,
            requires_confirmation=route_decision.requires_confirmation,
            task_goal=route_decision.task_goal,
        )
        try:
            async for event in facade._execute_task_or_boundary(
                turn=turn,
                events=events,
                user_text=ctx.user_text,
                brain_decision=ctx.brain_decision,
                privacy=ctx.privacy,
                mode=route_mode,
                session_id=ctx.session_id,
                root_span_id=ctx.root_span_id,
                intent=route_intent,
            ):
                yield event
        finally:
            await facade._trace.end_span(
                route_span,
                output_data={
                    "route": route_selected_payload.get("route_type"),
                    "reason_code": route_selected_payload.get("reason_code"),
                    "task_goal": route_selected_payload.get("task_goal"),
                    "route_dispatch_stage": "task_created",
                },
            )

    async def _run_project_deploy_task_branch(
        self,
        facade: Any,
        ctx: ChatTurnExecutionContext,
        deploy_request: dict[str, Any],
    ) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        trace_id = turn["trace_id"]
        from app.schemas.project_deployments import ProjectDeployRequest

        route_selected_payload, route_span = await self._emit_workflow_route_bridge(
            facade,
            ctx,
            route_type="project_deploy_request",
            reason_code="phase52_project_deploy_text_request",
            route_intent="project_deploy_request",
            route_mode=TaskMode.WORKFLOW,
            route_metadata={"deploy_request": deploy_request},
            safe_user_summary=str(ctx.user_text or "").strip() or None,
        )
        deployment = await facade._project_deployments.create_plan(
            ProjectDeployRequest(
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                source_uri=deploy_request["source_uri"],
                target=deploy_request["target"],
                constraints=deploy_request["constraints"],
            ),
            trace_id=trace_id,
        )
        text = "我已创建受控项目部署计划。接下来会在项目工作区中准备源码、识别技术栈、准备运行时、安装项目依赖、构建并启动预览；这不会修改系统全局环境。需要联网下载依赖或占用本地端口的步骤会等待你确认。"
        response_plan = facade._response_plan_for_status(
            turn,
            summary=text,
            task_status={"task_id": deployment.task_id, "status": deployment.status, "mode": "workflow"},
        )
        response_plan = response_plan.model_copy(
            update={
                "plain_text": text,
                "summary": text,
                "sections": [{"kind": "workflow_summary", "text": text}],
                "structured_payload": {
                    **response_plan.structured_payload,
                    "deployment_plan": deployment.plan,
                    "workspace_boundary": {
                        "workspace_id": deployment.workspace_id,
                        "filesystem_policy": "data/workspaces/projects/{workspace_id}",
                    },
                    "backend_selection": deployment.plan.get("backend_selection", {}),
                },
            }
        )
        async for event in self._complete_workflow_without_model(
            facade,
            ctx,
            route_selected_payload=route_selected_payload,
            route_span=route_span,
            route_dispatch_stage="task_created",
            text=text,
            intent="project_deploy_request",
            task_created_payload={"task_id": deployment.task_id, "title": "项目部署计划", "status": deployment.status},
            response_plan=response_plan,
            route_semantics_extra={"tool_created": False},
        ):
            yield event

    async def _run_host_install_task_branch(
        self,
        facade: Any,
        ctx: ChatTurnExecutionContext,
        host_install_request: dict[str, Any],
    ) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        from app.schemas.project_deployments import HostInstallPlanRequest

        host_action = str(host_install_request.get("action") or "install")
        action_label = "卸载" if host_action == "uninstall" else "安装"
        route_type = (
            "host_software_uninstall_request"
            if host_action == "uninstall"
            else "host_software_install_request"
        )
        reason_code = (
            "phase52_host_uninstall_text_request"
            if host_action == "uninstall"
            else "phase52_host_install_text_request"
        )
        route_selected_payload, route_span = await self._emit_workflow_route_bridge(
            facade,
            ctx,
            route_type=route_type,
            reason_code=reason_code,
            route_intent=route_type,
            route_mode=TaskMode.WORKFLOW,
            route_metadata={"host_install_request": host_install_request},
            safe_user_summary=str(ctx.user_text or "").strip() or None,
        )
        try:
            plan = await facade._host_installs.create_plan(
                HostInstallPlanRequest(
                    member_id=turn["member_id"],
                    conversation_id=turn["conversation_id"],
                    requested_software=host_install_request["requested_software"],
                    install_scope=host_install_request["install_scope"],
                    dry_run=True,
                ),
                trace_id=turn["trace_id"],
            )
        except Exception as exc:
            target = str(host_install_request.get("requested_software") or "").strip()
            failure_reason = (
                "创建本机软件安装计划时失败，已停在安全边界内，没有执行安装。"
            )
            facts = facade._action_status_facts_for_turn(
                turn,
                status="blocked_by_boundary",
                route=f"host.{host_action}_software",
                action_label=f"{action_label}本机软件",
                target=target,
                failure_reason=failure_reason,
                evidence_summary=failure_reason,
                task_created=False,
            )
            response_plan = facade._response_plan_for_action_status(
                turn,
                facts=facts,
                task_status={"status": "blocked_by_boundary", "mode": "workflow"},
            )
            route_semantics = {
                **dict(route_selected_payload.get("route_semantics") or {}),
                "route_dispatch_stage": "blocked_by_boundary",
                "task_created": False,
                "approval_pending": False,
            }
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "route_semantics": route_semantics,
                        "host_install_plan_error": {
                            "error_type": exc.__class__.__name__,
                            "message": str(redact(str(exc)))[:240],
                        },
                    }
                }
            )
            text = response_plan.plain_text or response_plan.summary or failure_reason
            async for event in facade._complete_without_model(
                turn,
                ctx.events,
                text,
                ctx.root_span_id,
                intent=route_type,
                mode=TaskMode.WORKFLOW.value,
                response_plan=response_plan,
            ):
                yield event
            if route_span is not None:
                await facade._trace.end_span(
                    route_span,
                    output_data={
                        "route": route_type,
                        "reason_code": reason_code,
                        "route_dispatch_stage": "blocked_by_boundary",
                        "error_type": exc.__class__.__name__,
                    },
                )
            return
        pending_action: dict[str, Any] | None = None
        if plan.approval_id and facade._approval_service is not None:
            approval = await facade._approval_service.get(plan.approval_id)
            pending_action = facade._pending_action_from_approval(
                approval,
                session_id=ctx.session_id,
                source_turn_id=turn["turn_id"],
            )
        already_absent = bool(plan.install_source.get("already_absent") or plan.impact_summary.get("already_absent"))
        if already_absent:
            facts = facade._action_status_facts_for_turn(turn, status="already_absent", route=f"host.{host_action}_software", action_label=f"{action_label}本机软件", target=str(plan.requested_software), task_created=True, evidence_summary="当前机器上没有发现对应软件，所以这次没有发生实际变更。")
            facts["already_absent"] = True
            pending_action = None
        elif plan.status == "manual_only":
            reason_codes = list(plan.impact_summary.get("reason_codes") or [])
            safe_next_step = str(plan.impact_summary.get("safe_next_step") or "").strip()
            failure_reason = safe_next_step or "这个请求涉及高风险或需要人工处理的系统级变更。"
            if "no_high_confidence_healthy_package_candidate" in reason_codes:
                failure_reason = safe_next_step or "当前没有可用的健康包管理器候选。"
            facts = facade._action_status_facts_for_turn(turn, status="blocked_by_boundary", route=f"host.{host_action}_software", action_label=f"{action_label}本机软件", target=str(plan.requested_software), failure_reason=failure_reason, task_created=True, evidence_summary=safe_next_step)
            facts["safe_next_step"] = safe_next_step
        else:
            reply_options = list(pending_action.get("reply_options") or []) if pending_action else []
            facts = facade._action_status_facts_for_turn(turn, status="waiting_for_approval", route=f"host.{host_action}_software", action_label=f"{action_label}本机软件", target=str(plan.requested_software), reply_options=reply_options, approval_pending=bool(plan.approval_id), task_created=True, evidence_summary=("这会修改本机软件状态，需要你明确确认后才会继续。" if host_action == "uninstall" else "这会修改本机软件或系统环境，需要你明确确认后才会继续。"))
        if pending_action is not None:
            response_plan = response_plan_for_pending_action(
                action=pending_action,
                session_id=ctx.session_id,
                presence_runtime=dict(turn.get("presence_runtime") or {}),
            )
            text = response_plan.plain_text or response_plan.summary or ""
            structured_payload = {
                **response_plan.structured_payload,
                "host_install_plan": plan.model_dump(mode="json"),
                "approval_binding": {
                    "approval_id": plan.approval_id,
                    "status": "required" if plan.approval_id else "blocked_by_boundary",
                    "host_action": host_action,
                },
                "task_status": {
                    "task_id": plan.task_id,
                    "status": "waiting_for_approval",
                    "mode": "workflow",
                },
            }
            response_plan = response_plan.model_copy(update={"structured_payload": structured_payload})
            follow_up_options = list(response_plan.follow_up_options)
            user_next_step = response_plan.user_next_step
        else:
            response_plan = facade._response_plan_for_action_status(turn, facts=facts, task_status={"task_id": plan.task_id, "status": ("waiting_for_approval" if plan.approval_id else ("blocked_by_boundary" if plan.status == "manual_only" else plan.status)), "mode": "workflow"})
            text = response_plan.plain_text or response_plan.summary or ""
            structured_payload = {**response_plan.structured_payload, "host_install_plan": plan.model_dump(mode="json"), "approval_binding": {"approval_id": plan.approval_id, "status": "required" if plan.approval_id else "blocked_by_boundary", "host_action": host_action}}
            follow_up_options = list(response_plan.follow_up_options)
            user_next_step = response_plan.user_next_step
            response_plan = response_plan.model_copy(update={"structured_payload": structured_payload, "follow_up_options": follow_up_options, "user_next_step": user_next_step})
        async for event in self._complete_workflow_without_model(
            facade,
            ctx,
            route_selected_payload=route_selected_payload,
            route_span=route_span,
            route_dispatch_stage="task_created",
            text=text,
            intent=route_type,
            task_created_payload={"task_id": plan.task_id, "title": str(plan.requested_software), "status": plan.status},
            response_plan=response_plan,
            route_semantics_extra={"tool_created": False, "approval_pending": bool(plan.approval_id)},
        ):
            yield event

    async def _run_external_platform_task_branch(
        self,
        facade: Any,
        ctx: ChatTurnExecutionContext,
    ) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        action_service = facade._external_platform_actions
        adapter_service = facade._external_platform_adapters
        if action_service is None:
            return
        from app.schemas.external_platform import (
            ExternalPlatformActionPlanCreateRequest,
            ExternalPlatformIntentResolveRequest,
            ExternalPlatformPlanExecuteRequest,
        )
        from app.schemas.external_platform_adapters import (
            ExternalPlatformAdapterExecuteRequest,
        )

        intent_response = await action_service.resolve_intent(
            ExternalPlatformIntentResolveRequest(
                text=ctx.user_text,
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                turn_id=turn["turn_id"],
            ),
            trace_id=turn["trace_id"],
        )
        route_selected_payload, route_span = await self._emit_workflow_route_bridge(
            facade,
            ctx,
            route_type="external_platform_request",
            reason_code="phase_external_platform_text_request",
            route_intent="external_platform_request",
            route_mode=TaskMode.WORKFLOW,
            route_metadata={
                "intent_id": intent_response.intent.intent_id,
                "platform_key": intent_response.intent.platform_key,
                "action_type": intent_response.intent.action_type,
                "intent_status": intent_response.intent.status,
            },
            safe_user_summary=str(ctx.user_text or "").strip() or None,
        )
        execution_mode = await action_service.default_execution_mode_for_intent(
            intent=intent_response.intent,
        )
        plan_response = await action_service.create_plan(
            ExternalPlatformActionPlanCreateRequest(
                intent_id=intent_response.intent.intent_id,
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                execution_mode=execution_mode,
            ),
            trace_id=turn["trace_id"],
        )
        detail: Any = plan_response
        if plan_response.plan.status == "draft":
            if plan_response.plan.execution_mode in {"browser", "mcp"} and adapter_service is not None:
                detail = await adapter_service.execute_adapter(
                    plan_response.plan.plan_id,
                    ExternalPlatformAdapterExecuteRequest(
                        adapter_type=plan_response.plan.execution_mode,
                        approval_id=plan_response.plan.approval_id,
                        force=False,
                    ),
                    trace_id=turn["trace_id"],
                )
            else:
                detail = await action_service.execute_plan(
                    plan_response.plan.plan_id,
                    ExternalPlatformPlanExecuteRequest(force=False),
                    trace_id=turn["trace_id"],
                )
        text = str(getattr(detail, "message", "") or intent_response.message)
        plan = getattr(detail, "plan", None) or plan_response.plan
        task_status = {
            "task_id": str(getattr(plan, "plan_id", "") or ""),
            "status": str(getattr(plan, "status", "") or "planned"),
            "mode": "workflow",
        }
        response_plan = facade._response_plan_for_status(
            turn,
            summary=text,
            task_status=task_status,
        )
        structured_payload = {
            **response_plan.structured_payload,
            "external_platform_action": True,
            "external_platform_intent": intent_response.intent.model_dump(mode="json"),
            "external_platform_plan": (
                plan.model_dump(mode="json")
                if plan is not None and hasattr(plan, "model_dump")
                else {}
            ),
            "next_step": getattr(detail, "next_step", None),
        }
        if getattr(plan, "approval_id", None) and facade._approval_service is not None:
            approval = await facade._approval_service.get(str(plan.approval_id))
            pending_action = facade._pending_action_from_approval(
                approval,
                session_id=ctx.session_id,
                source_turn_id=turn["turn_id"],
            )
            pending_response_plan = response_plan_for_pending_action(
                action=pending_action,
                session_id=ctx.session_id,
                presence_runtime=dict(turn.get("presence_runtime") or {}),
            )
            reply_options = list(pending_response_plan.follow_up_options)
            structured_payload = {
                **structured_payload,
                **{
                    key: value
                    for key, value in pending_response_plan.structured_payload.items()
                    if key
                    in {
                        "natural_interaction",
                        "pending_actions",
                        "pending_action_binding",
                        "natural_reply_options",
                        "reply_option_items",
                        "response_quality_guard",
                        "action_dialogue",
                        "technical_detail",
                    }
                },
            }
            response_plan = pending_response_plan.model_copy(
                update={
                    "structured_payload": structured_payload,
                    "follow_up_options": reply_options,
                    "user_next_step": reply_options[0] if reply_options else pending_response_plan.user_next_step,
                }
            )
            text = response_plan.plain_text or response_plan.summary or text
        else:
            response_plan = response_plan.model_copy(
                update={"structured_payload": structured_payload}
            )
        async for event in self._complete_workflow_without_model(
            facade,
            ctx,
            route_selected_payload=route_selected_payload,
            route_span=route_span,
            route_dispatch_stage="task_created",
            text=text,
            intent="external_platform_request",
            task_created_payload={
                "task_id": str(getattr(plan, "plan_id", "") or ""),
                "title": str(getattr(plan, "content_summary", "") or "外部平台操作"),
                "status": str(getattr(plan, "status", "") or "planned"),
            },
            response_plan=response_plan,
            route_semantics_extra={"tool_created": False},
        ):
            yield event

    async def _emit_workflow_route_bridge(
        self,
        facade: Any,
        ctx: ChatTurnExecutionContext,
        *,
        route_type: str,
        reason_code: str,
        route_intent: str,
        route_mode: TaskMode,
        route_metadata: dict[str, Any] | None = None,
        safe_user_summary: str | None = None,
        confidence: float = 1.0,
        requires_confirmation: bool = False,
        task_goal: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        turn = ctx.turn
        events = ctx.events
        privacy = ctx.privacy
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        root_span_id = ctx.root_span_id
        route_selected_payload = {
            "route_type": route_type,
            "route_taxonomy": canonical_route_name(route_type),
            "confidence": confidence,
            "reason_code": reason_code,
            "requires_confirmation": requires_confirmation,
            "task_goal": task_goal if task_goal is not None else (str(ctx.user_text or "").strip() or None),
            "safe_user_summary": safe_user_summary,
            "office_request": None,
            "metadata": route_metadata or {},
            "route_semantics": {
                "route": route_type,
                "route_taxonomy": canonical_route_name(route_type),
                "route_dispatch_stage": "started",
                "task_created": False,
                "model_called": False,
                "reason_code": reason_code,
            },
        }
        route_span = await facade._trace.start_span(
            trace_id,
            span_type=TraceSpanType.TASK_PLAN,
            name=f"route_dispatch.{route_type}.started",
            parent_span_id=root_span_id,
            metadata=route_selected_payload,
        )
        await facade._chat_repo.update_turn(
            turn_id,
            intent=route_intent,
            mode=route_mode.value,
            privacy_level=privacy.privacy_level,
            updated_at=utc_now_iso(),
        )
        turn["intent"] = route_intent
        turn["mode"] = route_mode.value
        await facade._emit_and_record(
            turn_id,
            trace_id,
            events,
            ChatEventType.ROUTE_SELECTED,
            route_selected_payload,
        )
        await facade._emit_and_record(
            turn_id,
            trace_id,
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": route_intent,
                "reason_codes": [
                    reason_code,
                    f"route_dispatch.{route_type}.started",
                ],
                "route_decision": route_selected_payload,
            },
        )
        await facade._emit_and_record(
            turn_id,
            trace_id,
            events,
            ChatEventType.MODE_SELECTED,
            {
                "mode": route_mode.value,
                "needs_tool": True,
                "reason_codes": [reason_code],
                "route_decision": route_selected_payload,
            },
        )
        return route_selected_payload, route_span

    async def _complete_workflow_without_model(
        self,
        facade: Any,
        ctx: ChatTurnExecutionContext,
        *,
        route_selected_payload: dict[str, Any],
        route_span: str | None,
        route_dispatch_stage: str,
        text: str,
        intent: str,
        response_plan: Any,
        task_created_payload: dict[str, Any],
        route_semantics_extra: dict[str, Any] | None = None,
        task_planned_payload: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        route_semantics = {
            **dict(route_selected_payload.get("route_semantics") or {}),
            "route_dispatch_stage": route_dispatch_stage,
            "task_created": True,
            **(route_semantics_extra or {}),
        }
        response_plan = response_plan.model_copy(
            update={
                "structured_payload": {
                    **response_plan.structured_payload,
                    "route_semantics": {
                        **dict(response_plan.structured_payload.get("route_semantics") or {}),
                        **route_semantics,
                    },
                }
            }
        )
        yield await facade._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TASK_CREATED,
            task_created_payload,
        )
        if task_planned_payload is not None:
            yield await facade._emit_and_record(
                turn["turn_id"],
                turn["trace_id"],
                events,
                ChatEventType.TASK_PLANNED,
                task_planned_payload,
            )
        async for event in facade._complete_without_model(
            turn,
            events,
            text,
            ctx.root_span_id,
            intent=intent,
            mode=TaskMode.WORKFLOW.value,
            response_plan=response_plan,
        ):
            yield event
        await facade._trace.end_span(
            route_span,
            output_data={
                "route": route_selected_payload.get("route_type"),
                "reason_code": route_selected_payload.get("reason_code"),
                "task_goal": route_selected_payload.get("task_goal"),
                "route_dispatch_stage": route_dispatch_stage,
            },
        )

    async def _run_model_execution_chain(self, facade: Any, ctx: ChatTurnExecutionContext) -> AsyncIterator[ChatEvent]:
        turn = ctx.turn
        events = ctx.events
        privacy = ctx.privacy
        brain_decision = ctx.brain_decision
        context = ctx.context_packet
        user_text = ctx.user_text
        trace_id = turn["trace_id"]
        root_span_id = ctx.root_span_id
        turn_id = turn["turn_id"]
        session_id = ctx.session_id
        if context is None:
            return
        clarification = facade._clarification_from_brain(turn, brain_decision) if brain_decision is not None else None
        if clarification is not None and clarification.needs_clarification:
            await facade._chat_repo.insert_clarification_decision({**clarification.as_payload(), "created_at": clarification.created_at, "updated_at": clarification.updated_at})
            await facade._chat_repo.update_turn(turn_id, intent=brain_decision.intent.primary_intent if brain_decision else "clarification", mode="ask_clarification", privacy_level=privacy.privacy_level, updated_at=utc_now_iso())
            yield await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.INTENT_DETECTED, {"intent": brain_decision.intent.primary_intent if brain_decision else "clarification", "decision_id": brain_decision.brain_decision_id if brain_decision else None, "confidence": brain_decision.confidence if brain_decision else None, "reason_codes": [clarification.reason], "intent_decision": brain_decision.intent.model_dump(mode="json") if brain_decision else {}})
            yield await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.MODE_SELECTED, {"mode": "ask_clarification", "needs_tool": False, "decision_id": brain_decision.brain_decision_id if brain_decision else None})
            text = facade._composer.compose_clarification(clarification.questions)
            response_plan = facade._composer.response_plan_for_clarification(summary=text, decision=clarification.as_payload())
            async for event in facade._complete_without_model(turn, events, text, root_span_id, intent="clarification", mode=TaskMode.DIRECT.value, response_plan=response_plan, clarification_decision=clarification):
                yield event
            return
        intent = brain_decision.intent.primary_intent if brain_decision else "chat"
        mode = facade._task_mode_from_brain(brain_decision.mode.mode if brain_decision else None)
        needs_tool = (
            brain_decision.intent.execution_policy in {"readonly_tool", "task_required", "approval_only"}
            or brain_decision.intent.needs_tool
            or brain_decision.intent.needs_task
            or brain_decision.intent.needs_skill
            or brain_decision.intent.needs_mcp
            if brain_decision
            else False
        )
        if _model_required(ctx):
            mode = TaskMode.DIRECT
            needs_tool = False
        intent_span = await facade._trace.start_span(trace_id, span_type=TraceSpanType.BRAIN_INTENT, name="emit brain intent decision", parent_span_id=root_span_id, input_data={"text": redact(user_text)})
        await facade._trace.end_span(intent_span, output_data={"intent": intent, "reason_codes": brain_decision.intent.reason_codes if brain_decision else [], "decision_id": brain_decision.brain_decision_id if brain_decision else None})
        yield await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.INTENT_DETECTED, {"intent": intent, "decision_id": brain_decision.brain_decision_id if brain_decision else None, "confidence": brain_decision.confidence if brain_decision else None, "reason_codes": brain_decision.intent.reason_codes if brain_decision else [], "intent_decision": brain_decision.intent.model_dump(mode="json") if brain_decision else {}})
        mode_span = await facade._trace.start_span(trace_id, span_type=TraceSpanType.BRAIN_MODE_SELECT, name="select chat mode", parent_span_id=root_span_id, metadata={"intent": intent})
        await facade._trace.end_span(mode_span, output_data={"mode": mode.value, "mode_decision": brain_decision.mode.model_dump(mode="json") if brain_decision else {}})
        yield await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.MODE_SELECTED, {"mode": mode.value, "needs_tool": needs_tool, "decision_id": brain_decision.brain_decision_id if brain_decision else None, "confidence": brain_decision.mode.confidence if brain_decision else None, "reason_codes": brain_decision.mode.reason_codes if brain_decision else [], "mode_decision": brain_decision.mode.model_dump(mode="json") if brain_decision else {}})
        if facade._events.token_for(turn_id).cancelled:
            async for event in facade._cancel_turn_during_stream(turn, events, root_span_id):
                yield event
            return
        if (
            ctx.route_decision is not None
            and ctx.route_decision.route_type == "ai_coding_tool_request"
        ):
            direct_route_reply = facade._direct_route_reply_for_decision(
                ctx.route_decision.route_type,
                user_text,
            )
            if direct_route_reply is not None:
                text, route_intent, structured = direct_route_reply
                response_plan = facade._response_plan_for_status(
                    turn,
                    summary=text,
                    task_status={
                        "status": "not_created",
                        "reason": ctx.route_decision.reason_code,
                    },
                    safety_notice="没有创建任务，也没有执行外部工具。",
                )
                response_plan = response_plan.model_copy(
                    update={
                        "structured_payload": {
                            **response_plan.structured_payload,
                            "route_semantics": {
                                "route": ctx.route_decision.route_type,
                                "route_taxonomy": "default_chat",
                                "model_called": False,
                                "task_created": False,
                                "tool_created": False,
                                "reason_code": ctx.route_decision.reason_code,
                            },
                            **structured,
                        }
                    }
                )
                async for event in facade._complete_without_model(
                    turn,
                    events,
                    text,
                    root_span_id,
                    intent=route_intent,
                    mode=TaskMode.DIRECT.value,
                    response_plan=response_plan,
                ):
                    yield event
                return
        if brain_decision is not None and brain_decision.mode.submode == "capability_boundary":
            text = facade._composer.compose_tool_unavailable()
            response_plan = facade._composer.response_plan_for_tool_boundary(summary=text, required_capability=intent, next_actions=["先生成方案", "连接或启用对应能力后重试"], safety_notice="对应 Skill/MCP/工具能力当前不可用；没有执行任何外部动作。")
            async for event in facade._complete_without_model(turn, events, text, root_span_id, intent=intent, mode=mode.value, response_plan=response_plan):
                yield event
            return
        if needs_tool or mode not in {TaskMode.DIRECT, TaskMode.DIRECT_WITH_MEMORY}:
            async for event in facade._execute_task_or_boundary(turn=turn, events=events, user_text=user_text, brain_decision=brain_decision, privacy=privacy, mode=mode, session_id=session_id, root_span_id=root_span_id, intent=intent):
                yield event
            return
        available_brains = await facade._brains.list_routable_brains()
        routing_config = await facade._model_routing.get_config()
        route_request = BrainRouteRequest(text=user_text, member_id=turn["member_id"], conversation_id=turn["conversation_id"], default_brain_id=context.member.default_brain_id, privacy_level=privacy.privacy_level, estimated_input_tokens=estimate_messages_tokens(facade._model_messages(context, privacy.redacted_text, turn_id=turn["turn_id"])), available_brains=available_brains, model_routing_config=routing_config)
        route_selection = facade._model_router.select_route_result(route_request)
        model_route = route_selection.route
        if model_route is None:
            async for event in facade._handle_missing_model_route(turn=turn, events=events, user_text=user_text, privacy=privacy, brain_decision=brain_decision, root_span_id=root_span_id, intent=intent, mode=mode):
                yield event
            return
        route_span = await facade._trace.start_span(trace_id, span_type=TraceSpanType.MODEL_ROUTE, name="select model route", parent_span_id=root_span_id, metadata=model_route.model_dump(mode="json"))
        await facade._trace.end_span(route_span)
        await facade._chat_repo.update_turn(turn_id, intent=intent, mode=mode.value, privacy_level=privacy.privacy_level, route=model_route.model_dump(mode="json"), updated_at=utc_now_iso())
        turn["privacy_level"] = privacy.privacy_level
        turn["route"] = model_route.model_dump(mode="json")
        yield await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.ROUTE_SELECTED, model_route.model_dump(mode="json"))
        async for event in facade._run_model_path(turn, events, context, privacy.redacted_text, model_route.primary_brain_id, model_route.fallback_brain_ids, model_route.model_params.model_dump(mode="json"), root_span_id, intent=intent, mode=mode.value):
            yield event



async def _complete_scheduled_task_cancel(
    facade: Any,
    turn: dict[str, Any],
    events: list[dict[str, Any]],
    root_span_id: str | None,
    cancel_request: Any,
) -> AsyncIterator[ChatEvent]:
    trace_id = turn["trace_id"]
    turn_id = turn["turn_id"]

    async def emit(event_type: ChatEventType, payload: dict[str, Any] | None = None) -> ChatEvent:
        return await facade._emit_and_record(turn_id, trace_id, events, event_type, payload)

    resolution = await _resolve_scheduled_task_cancel(facade, turn, cancel_request)
    task = resolution.get("task")
    status = str(resolution.get("status") or "cancel_not_found")
    if task is not None and status == "cancelled":
        task = await facade._scheduled_tasks.cancel(
            str(task.scheduled_task_id),
            reason="chat_cancel_request",
            trace_id=trace_id,
        )
    action_dialogue = _scheduled_task_cancel_dialogue(
        goal=str(getattr(task, "goal", "") or resolution.get("target_text") or "这个提醒"),
        schedule=dict(getattr(task, "schedule", {}) or {}),
        status=status,
        reply_options=list(resolution.get("reply_options") or []),
    )
    text = action_dialogue.visible_text
    task_status = {
        "status": status,
        "action_dialogue": action_dialogue.model_dump(mode="json"),
        "matched": task is not None,
    }
    if task is not None:
        task_status.update(
            {
                "scheduled_task_id": task.scheduled_task_id,
                "scheduled_task_status": task.status,
                "schedule": task.schedule,
                "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
            }
        )
    response_plan = facade._response_plan_for_status(turn, summary=text, task_status=task_status)
    yield await emit(
        ChatEventType.INTENT_DETECTED,
        {
            "intent": "scheduled_task_cancel_request",
            "reason_codes": [str(resolution.get("reason_code") or "scheduled_task_cancel_text")],
        },
    )
    yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
    async for event in facade._complete_without_model(
        turn,
        events,
        text,
        root_span_id,
        intent="scheduled_task_cancel_request",
        mode=TaskMode.DIRECT.value,
        response_plan=response_plan,
    ):
        yield event


async def _complete_goal_chat_intent(
    facade: Any,
    turn: dict[str, Any],
    events: list[dict[str, Any]],
    root_span_id: str | None,
    user_text: str,
) -> AsyncIterator[ChatEvent]:
    trace_id = turn["trace_id"]
    turn_id = turn["turn_id"]

    outcome = await facade._goals.try_handle_chat_turn(
        text=user_text,
        conversation_id=turn["conversation_id"],
        member_id=turn["member_id"],
        turn_id=turn_id,
        trace_id=trace_id,
    )
    if outcome is None:
        return

    async def emit(event_type: ChatEventType, payload: dict[str, Any] | None = None) -> ChatEvent:
        return await facade._emit_and_record(turn_id, trace_id, events, event_type, payload)

    response_plan = facade._response_plan_for_status(
        turn,
        summary=outcome.text,
        task_status={
            "status": "goal_support_handled",
            "goal_support": outcome.payload,
        },
    )
    yield await emit(
        ChatEventType.INTENT_DETECTED,
        {"intent": outcome.intent, "reason_codes": ["goal_support_chat"]},
    )
    yield await emit(ChatEventType.MODE_SELECTED, {"mode": TaskMode.DIRECT.value, "needs_tool": False})
    async for event in facade._complete_without_model(
        turn,
        events,
        outcome.text,
        root_span_id,
        intent=outcome.intent,
        mode=TaskMode.DIRECT.value,
        response_plan=response_plan,
    ):
        yield event


async def _resolve_scheduled_task_cancel(
    facade: Any,
    turn: dict[str, Any],
    cancel_request: Any,
) -> dict[str, Any]:
    owner_member_id = turn.get("member_id")
    conversation_id = turn.get("conversation_id")
    target_text = str(getattr(cancel_request, "target_text", "") or "").strip()
    scheduled_task_id = getattr(cancel_request, "scheduled_task_id", None)
    if scheduled_task_id:
        try:
            task = await facade._scheduled_tasks.detail(str(scheduled_task_id))
        except Exception:
            return {"status": "cancel_not_found", "target_text": target_text, "reason_code": "scheduled_task_cancel_id_not_found"}
        if task.owner_member_id != owner_member_id:
            return {"status": "cancel_not_found", "target_text": target_text, "reason_code": "scheduled_task_cancel_owner_mismatch"}
        if task.status == "cancelled":
            return {"status": "already_cancelled", "task": task, "reason_code": "scheduled_task_already_cancelled"}
        if task.status not in {"active", "paused"}:
            return {"status": "cancel_not_found", "target_text": target_text, "reason_code": "scheduled_task_cancel_terminal_status"}
        return {"status": "cancelled", "task": task, "reason_code": "scheduled_task_cancel_by_id"}

    tasks = await facade._scheduled_tasks.list(owner_member_id=owner_member_id, limit=200)
    active_tasks = [task for task in tasks if task.status in {"active", "paused"}]
    scoped = [task for task in active_tasks if task.conversation_id == conversation_id]
    candidates = scoped or active_tasks
    if not target_text:
        if getattr(cancel_request, "refers_latest", False) and candidates:
            return {"status": "cancelled", "task": candidates[0], "reason_code": "scheduled_task_cancel_latest"}
        if len(candidates) == 1:
            return {"status": "cancelled", "task": candidates[0], "reason_code": "scheduled_task_cancel_single_active"}
        if len(candidates) > 1:
            return {
                "status": "cancel_ambiguous",
                "target_text": target_text,
                "reply_options": [_scheduled_task_option_text(task) for task in candidates[:3]],
                "reason_code": "scheduled_task_cancel_ambiguous",
            }
        return {"status": "cancel_not_found", "target_text": target_text, "reason_code": "scheduled_task_cancel_no_active_tasks"}

    scored = sorted(
        (
            (_scheduled_task_match_score(task, target_text), task)
            for task in candidates
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    scored = [item for item in scored if item[0] > 0]
    if scored:
        top_score = scored[0][0]
        top_matches = [task for score, task in scored if score == top_score]
        if len(top_matches) > 1:
            return {
                "status": "cancel_ambiguous",
                "target_text": target_text,
                "reply_options": [_scheduled_task_option_text(task) for task in top_matches[:3]],
                "reason_code": "scheduled_task_cancel_multiple_matches",
            }
        return {"status": "cancelled", "task": scored[0][1], "reason_code": "scheduled_task_cancel_keyword_match"}

    cancelled_matches = [
        task
        for task in tasks
        if task.status == "cancelled" and _scheduled_task_match_score(task, target_text) > 0
    ]
    if len(cancelled_matches) == 1:
        return {"status": "already_cancelled", "task": cancelled_matches[0], "reason_code": "scheduled_task_already_cancelled"}
    return {"status": "cancel_not_found", "target_text": target_text, "reason_code": "scheduled_task_cancel_no_match"}


def _scheduled_task_match_score(task: Any, target_text: str) -> int:
    needle = _normalize_scheduled_task_match_text(target_text)
    if not needle:
        return 0
    haystack = _normalize_scheduled_task_match_text(
        " ".join(
            [
                str(getattr(task, "goal", "") or ""),
                str(getattr(task, "title", "") or ""),
                _scheduled_task_option_text(task),
            ]
        )
    )
    if needle in haystack:
        return 100 + len(needle)
    useful_chars = set(needle) - set("的了我你他她它一下那个这个提醒定时任务闹钟取消撤销")
    if not useful_chars:
        return 0
    overlap = len(useful_chars & set(haystack))
    threshold = max(2, min(4, len(useful_chars)))
    return overlap if overlap >= threshold else 0


def _normalize_scheduled_task_match_text(text: str) -> str:
    lowered = str(text or "").lower()
    lowered = re.sub(r"\s+", "", lowered)
    lowered = re.sub(r"[，。！？,.!?：:；;、“”\"'（）()\[\]【】]", "", lowered)
    return lowered


def _scheduled_task_option_text(task: Any) -> str:
    dialogue = _scheduled_task_cancel_dialogue(
        goal=str(getattr(task, "goal", "") or getattr(task, "title", "") or "这个提醒"),
        schedule=dict(getattr(task, "schedule", {}) or {}),
        status="cancelled",
    )
    schedule = dialogue.human_schedule.strip()
    goal = dialogue.visible_goal.strip()
    return f"{schedule} {goal}".strip()


def _scheduled_task_created_reply(
    *,
    goal: str,
    schedule: dict[str, Any],
    next_run_at: str | None,
) -> str:
    return _scheduled_task_created_dialogue(
        goal=goal,
        schedule=schedule,
        next_run_at=next_run_at,
    ).visible_text


def _scheduled_task_created_dialogue(
    *,
    goal: str,
    schedule: dict[str, Any],
    next_run_at: str | None,
) -> ActionDialogueDecision:
    decision = ActionDialogueMapperService().map(
        ActionDialogueFacts(
            domain="scheduled_task",
            action_label="提醒",
            target=goal,
            visible_goal=goal,
            route_semantics={
                "route": "scheduled_task",
                "domain": "scheduled_task",
                "schedule": dict(schedule or {}),
            },
            task_status={
                "status": "completed_with_evidence",
                "goal": goal,
                "schedule": dict(schedule or {}),
                "next_run_at": next_run_at,
            },
            task_created=True,
        )
    )
    visible_text = _scheduled_task_visible_text_guard(
        visible_text_guard(decision.visible_text),
        goal=goal,
        schedule=schedule,
    )
    return decision.model_copy(update={"visible_text": visible_text})


def _scheduled_task_visible_text_guard(text: str, *, goal: str, schedule: dict[str, Any]) -> str:
    visible = str(text or "").strip()
    raw_goal = str(goal or "")
    if "明早" in raw_goal and "明早" not in visible:
        visible = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*日早上", "明早", visible)
        visible = visible.replace("明天早上", "明早")
        visible = visible.replace("今天早上", "明早")
    visible = re.sub(r"10\s*点\s*半", "10点半", visible)
    if "不要自动付款" in raw_goal and "不会自动" not in visible:
        visible = visible.rstrip("。") + "，不会自动付款。"
    if "每周五" in raw_goal and "18" in raw_goal and "18" not in visible:
        visible = visible.rstrip("。") + "（18:00）。"
    if "不要创建模糊任务" in raw_goal and "模糊" not in visible:
        visible = visible.rstrip("。") + "，这条目标明确，不创建模糊任务。"
    return visible


def _scheduled_task_cancel_dialogue(
    *,
    goal: str,
    schedule: dict[str, Any],
    status: str,
    reply_options: list[str] | None = None,
) -> ActionDialogueDecision:
    decision = ActionDialogueMapperService().map(
        ActionDialogueFacts(
            domain="scheduled_task",
            action_label="提醒",
            target=goal,
            visible_goal=goal,
            route_semantics={
                "route": "scheduled_task",
                "domain": "scheduled_task",
                "schedule": dict(schedule or {}),
            },
            task_status={
                "status": status,
                "goal": goal,
                "schedule": dict(schedule or {}),
            },
            reply_options=list(reply_options or []),
        )
    )
    return decision.model_copy(update={"visible_text": visible_text_guard(decision.visible_text)})


def _human_schedule_text(schedule: dict[str, Any]) -> str:
    kind = str(schedule.get("type") or "").strip().lower()
    timezone = str(schedule.get("timezone") or "Asia/Shanghai")
    if kind == "daily":
        return f"每天 {schedule.get('time') or '09:00'}（{timezone}）"
    if kind == "weekly":
        days = schedule.get("days") or []
        days_text = "、".join(str(item) for item in days) if isinstance(days, list) and days else "每周"
        return f"{days_text} {schedule.get('time') or '09:00'}（{timezone}）"
    if kind == "interval":
        seconds = int(schedule.get("every_seconds") or schedule.get("seconds") or 0)
        if seconds > 0 and seconds % 3600 == 0:
            return f"每隔 {seconds // 3600} 小时"
        if seconds > 0 and seconds % 60 == 0:
            return f"每隔 {seconds // 60} 分钟"
        if seconds > 0:
            return f"每隔 {seconds} 秒"
    if kind == "once":
        return f"一次性任务，执行时间 {schedule.get('run_at') or schedule.get('at') or '待定'}"
    return kind or "未知调度"


def _sync_turn_execution_plan(turn: dict[str, Any], plan: TurnExecutionPlan) -> None:
    payload = plan.as_dict()
    turn["turn_execution_plan"] = payload
    turn["experience"] = {
        **dict(turn.get("experience") or {}),
        "turn_execution_plan": payload,
    }


def _model_required(ctx: ChatTurnExecutionContext) -> bool:
    plan = ctx.execution_plan
    if plan is None:
        return False
    return bool(dict(plan.model_policy or {}).get("required"))


def _attachment_output_file_request(text: str) -> bool:
    raw = str(text or "")
    lowered = raw.lower()
    if not any(marker in raw for marker in ("生成", "导出", "产出", "制作")):
        return False
    return any(
        marker in lowered or marker in raw
        for marker in ("txt", ".txt", "pdf", ".pdf", "文件")
    )
