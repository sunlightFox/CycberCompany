from __future__ import annotations

# ruff: noqa: E501
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
from app.schemas.chat_turn_execution import ChatTurnExecutionContext
from app.schemas.chat_turn_execution import TurnExecutionPlan
from app.services.chat_runtime_host_helpers import (
    deterministic_no_model_reply as _deterministic_no_model_reply,
)
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
        deterministic_text = _deterministic_no_model_reply(user_text)
        if (
            deterministic_text is not None
            and pre_route_decision.route_type not in direct_readonly_routes
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
        office_route_pending = (
            ctx.route_decision is not None and ctx.route_decision.office_request is not None
        )
        if (
            facade._natural_chat is not None
            and not allow_direct_memory_command
            and not explicit_memory_query
            and not office_route_pending
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
        scheduled_request = facade._task_coordinator.scheduled_intents.parse(user_text)
        if scheduled_request is not None and facade._scheduled_tasks is not None:
            from app.schemas.scheduled_tasks import ScheduledTaskCreateRequest
            scheduled_task = await facade._scheduled_tasks.create(ScheduledTaskCreateRequest(conversation_id=turn["conversation_id"], owner_member_id=turn["member_id"], title=scheduled_request.title, goal=scheduled_request.goal, schedule=scheduled_request.schedule, execution_policy={"attendance": "unattended"}, constraints={"source": "chat_text", "phase": "phase36"}, created_by_member_id=facade._default_user_id()), trace_id=trace_id)
            text = _scheduled_task_created_reply(goal=str(scheduled_task.goal or scheduled_request.goal), schedule=dict(scheduled_task.schedule or scheduled_request.schedule), next_run_at=scheduled_task.next_run_at.isoformat() if scheduled_task.next_run_at else None)
            response_plan = facade._response_plan_for_status(turn, summary=text, task_status={"scheduled_task_id": scheduled_task.scheduled_task_id, "status": scheduled_task.status, "next_run_at": scheduled_task.next_run_at.isoformat() if scheduled_task.next_run_at else None, "background_execution_policy": scheduled_task.execution_policy})
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
            text = "已创建受控视频工作流任务。请先把视频作为任务 artifact 绑定进来；后续分析、剪辑、渲染都会走 artifact 边界，渲染和导出前会再次确认。"
            if media_request["plan_only"]:
                text = "已创建受控视频计划任务；我会只生成剪辑方案，不渲染或导出视频。"
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



def _scheduled_task_created_reply(
    *,
    goal: str,
    schedule: dict[str, Any],
    next_run_at: str | None,
) -> str:
    next_run_text = (
        f"下一次执行时间是 {next_run_at}。"
        if next_run_at
        else "下一次执行时间会按这个调度规则计算。"
    )
    return (
        f"定时任务已经建好了，目标是：{goal}。"
        f"调度方式是：{_human_schedule_text(schedule)}。"
        f"{next_run_text}"
        "到点后我会先按后台流程往下推；如果过程中碰到下载、删除、终端、登录或外发这类高风险动作，我会停一下，再找你确认。"
    )


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
