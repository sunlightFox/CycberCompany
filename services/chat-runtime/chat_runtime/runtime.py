from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from core_types import (
    ApiModel,
    ChatEvent,
    ChatEventType,
    ChatInput,
    ChatTurnRequest,
    ChatTurnResponse,
    ErrorCode,
    TraceSpanStatus,
    TraceSpanType,
    TraceStatus,
)
from trace_service import redact

from app.core.errors import AppError
from app.core.time import utc_now_iso
from app.services.natural_chat import (
    reset_visible_redaction_profile,
    set_visible_redaction_profile,
)

PLACEHOLDER_RESPONSE = "模型还没有配置好。我已经记下这轮输入；配置大脑后，我就能正式处理。"

_ROUTE_TAXONOMY = {
    "chat": "default_chat",
    "memory": "direct_with_memory",
    "natural_action": "natural_action_resolution",
    "browser_read_page": "tool_shortcut_browser_read",
    "browser_search_readonly": "tool_shortcut_browser_read",
    "browser_search_with_citation": "tool_shortcut_browser_read",
    "terminal_readonly_command": "tool_shortcut_terminal_readonly",
    "host_filesystem_list": "tool_shortcut_host_filesystem_list",
    "office_document_hard_route": "office_document_task",
    "browser_download": "task_execution",
    "file_mutation_task": "task_execution",
    "media_runtime_request": "task_execution",
    "project_deploy_request": "task_execution",
    "host_software_install_request": "task_execution",
    "host_software_uninstall_request": "task_execution",
    "task_recovery": "task_recovery",
}


class PlaceholderTurn(ApiModel):
    assistant_text: str
    events: list[ChatEvent]


def canonical_route_name(route: str | None) -> str:
    value = str(route or "").strip()
    if not value:
        return "default_chat"
    if value in _ROUTE_TAXONOMY:
        return _ROUTE_TAXONOMY[value]
    if value.startswith("host.") or value.startswith("task."):
        return "task_execution"
    if value.startswith("browser.") or value.startswith("terminal."):
        return "natural_action_resolution"
    return "default_chat"


class ChatRuntime:
    def __init__(self, service: Any | None = None) -> None:
        self._bound_context = service
        if service is not None:
            self._bind_context(service)

    def _bind_context(self, service: Any) -> None:
        for name, value in vars(service).items():
            if not name.startswith("_"):
                continue
            if name in {"_runtime_impl", "_runtime"}:
                continue
            setattr(self, name, value)
        for name in dir(service):
            if not name.startswith("_") or name.startswith("__"):
                continue
            if name in {"_runtime_impl", "_runtime"}:
                continue
            if hasattr(ChatRuntime, name):
                continue
            try:
                value = getattr(service, name)
            except AttributeError:
                continue
            setattr(self, name, value)
        bindings = (
            "_members",
            "_chat_repo",
            "_access_policy",
            "_trace",
            "_ingress",
            "_db",
            "_events",
            "_execution",
            "_safety_policy",
            "_composer",
            "_chat_experience",
            "_model_fallback_runtime",
            "_brains",
            "_turn_execution_orchestrator",
            "_turn_finalize",
            "_direct_routes_runtime",
            "_memory",
            "_task_engine",
            "_readonly_execution",
            "_voice",
            "_privacy",
            "_brain_decision",
            "_context_gateway",
            "_chat_quality_shadow",
            "_audit",
            "_continuation",
            "_response_coordinator",
            "_model_execution",
            "_silent_continuity",
            "_natural_chat",
            "_task_coordinator",
            "_scheduled_tasks",
            "_context_assembly",
            "_turn_recovery",
            "_safety_policy",
            "_host_installs",
            "_project_deployments",
            "_skill_plugins",
            "_skill_governance",
            "_tool_runtime",
            "_context_budget_runtime",
            "_context_visibility_runtime",
            "_conversation_understanding",
            "_presence_state",
            "_session_context",
            "_response_policy_runtime",
            "_action_dialogue_mapper",
            "_chat_run_ledger_service",
        )
        methods = (
            "_request_text_from_request",
            "_title_from_text",
            "_create_conversation",
            "_new_id",
            "_collect_into_existing_turn",
            "_debounce_delay_seconds",
            "_queue_lock_until",
            "_root_span_id",
            "_finalize_created_cancel",
            "_event_from_persisted_row",
            "_response_plan_for_status",
            "_with_experience_payload",
            "_record_event",
            "_fail_turn",
            "_content_payload_for_envelope",
            "_queue_payload_for_item",
            "_message_user_text_from_message",
            "_session_id_from_message",
            "_emit_and_record",
            "_context_redaction_summary",
            "_build_presence_runtime_payload",
            "_shadow_channel_profile",
            "_maybe_record_context_compaction",
            "_deterministic_execution_state_reply_text",
            "_deterministic_boundary_reply_text",
            "_deterministic_latest_instruction_reply_text",
            "_maybe_handle_pending_clarification_followup",
            "_emit_memory_events",
            "_complete_model_turn",
            "_style_visible_text",
            "_presence_failure_text",
            "_with_experience_payload",
            "_decorate_chat_payloads",
            "_decorate_response_plan",
            "_decorate_chat_quality_shadow",
            "_persist_assistant_message",
            "_default_user_id",
            "_record_stage_recovery_attempt",
            "_model_failure_type",
            "_cancel_turn_during_stream",
            "_call_model",
            "_response_plan_for_action_status",
            "_action_status_facts_for_turn",
            "_enabled_office_skill_id",
            "_office_skill_has_grant",
            "_latest_office_artifact_id",
            "_office_missing_capability_text",
            "_office_next_actions",
            "_office_task_reply",
            "_recover_task_in_turn",
        )
        for name in bindings + methods:
            if hasattr(service, name):
                setattr(self, name, getattr(service, name))

    async def run_placeholder(self, request: ChatTurnRequest, turn_id: str) -> PlaceholderTurn:
        return PlaceholderTurn(
            assistant_text=PLACEHOLDER_RESPONSE,
            events=self.placeholder_events(turn_id),
        )

    async def create_turn(
        self,
        request: ChatTurnRequest,
        *,
        retry_of_turn_id: str | None = None,
    ) -> ChatTurnResponse:
        self._require_service("create_turn")
        member = await self._members.get_member(request.member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)

        input_text = self._request_text_from_request(request)
        conversation_id = request.conversation_id
        created_conversation_title: str | None = None
        if conversation_id is None:
            created_conversation_title = self._title_from_text(input_text)
            conversation_id = await self._create_conversation(
                member,
                created_conversation_title,
            )
        else:
            conversation = await self._chat_repo.get_conversation(conversation_id)
            if conversation is None:
                raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)
            self._access_policy.assert_can_write(
                member=member,
                conversation=conversation,
            )

        turn_id = self._new_id("turn")
        user_message_id = self._new_id("msg")
        trace_id = await self._trace.start_trace(conversation_id=conversation_id, turn_id=turn_id)
        root_span_id = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.CHAT_TURN,
            name="chat turn",
            input_data={
                "conversation_id": conversation_id,
                "member_id": request.member_id,
                "input": {
                    "type": request.input.type,
                    "text": redact(input_text),
                    "content_part_count": len(request.input.content_parts),
                    "context_ref_count": len(request.context_refs),
                },
                "retry_of_turn_id": retry_of_turn_id,
            },
            metadata={"session_id": request.session_id},
        )
        ingress_plan = await self._ingress.prepare(
            request=request,
            turn_id=turn_id,
            conversation_id=conversation_id,
            trace_id=trace_id,
            root_span_id=root_span_id,
        )
        if ingress_plan.duplicate_turn_id:
            duplicate = await self._chat_repo.get_turn(ingress_plan.duplicate_turn_id)
            duplicate_envelope = await self._chat_repo.get_message_envelope_by_turn(
                ingress_plan.duplicate_turn_id
            )
            await self._trace.end_span(
                root_span_id,
                output_data={
                    "status": "deduped",
                    "duplicate_turn_id": ingress_plan.duplicate_turn_id,
                },
            )
            await self._trace.end_trace(trace_id)
            if duplicate is not None:
                return ChatTurnResponse(
                    turn_id=duplicate["turn_id"],
                    conversation_id=duplicate["conversation_id"],
                    message_id=duplicate["user_message_id"],
                    assistant_message_id=duplicate["assistant_message_id"],
                    task_id=None,
                    trace_id=duplicate["trace_id"],
                    status="superseded",
                    stream_url=f"/api/chat/stream/{duplicate['turn_id']}",
                    queue_status="superseded",
                    envelope_id=(
                        duplicate_envelope["envelope_id"] if duplicate_envelope else None
                    ),
                )
        if ingress_plan.collect_turn_id:
            collected = await self._collect_into_existing_turn(
                request=request,
                collect_turn_id=ingress_plan.collect_turn_id,
                incoming_envelope=ingress_plan.envelope,
                trace_id=trace_id,
                root_span_id=root_span_id,
            )
            await self._trace.end_span(
                root_span_id,
                output_data={
                    "status": "debounce_collected",
                    "collect_turn_id": collected.turn_id,
                    "envelope_id": collected.envelope_id,
                },
            )
            await self._trace.end_trace(trace_id)
            return collected
        if created_conversation_title is not None:
            title_span = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.CONVERSATION_TITLE,
                name="create conversation title",
                parent_span_id=root_span_id,
                metadata={"conversation_id": conversation_id},
            )
            await self._trace.end_span(
                title_span,
                output_data={"title": redact(created_conversation_title)},
            )

        try:
            async with self._db.transaction():
                now = utc_now_iso()
                persist_span = await self._trace.start_span(
                    trace_id,
                    span_type=TraceSpanType.MESSAGE_PERSIST_USER,
                    name="persist user message",
                    parent_span_id=root_span_id,
                    metadata={"message_id": user_message_id},
                )
                await self._chat_repo.insert_message(
                    message_id=user_message_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    author_type="user",
                    author_id=self._default_user_id(),
                    content_type=request.input.type,
                    content_text=ingress_plan.envelope.model_safe_text,
                    content={
                        "type": request.input.type,
                        "text": ingress_plan.envelope.model_safe_text,
                        "raw_text": request.input.text,
                        "session_id": request.session_id,
                        "content_parts": ingress_plan.envelope.content_parts,
                        "context_refs": ingress_plan.envelope.context_refs,
                        "attachments": [
                            item.model_dump(mode="json") for item in request.attachments
                        ],
                        "ingress_metadata": ingress_plan.envelope.ingress_metadata,
                        "normalized_summary": ingress_plan.envelope.normalized_summary,
                        "client_context": request.client_context.model_dump(mode="json"),
                        "retry_of_turn_id": retry_of_turn_id,
                    },
                    trace_id=trace_id,
                    created_at=now,
                )
                await self._trace.end_span(
                    persist_span,
                    output_data={"message_id": user_message_id},
                )
                await self._chat_repo.insert_turn(
                    turn_id=turn_id,
                    conversation_id=conversation_id,
                    member_id=request.member_id,
                    user_message_id=user_message_id,
                    trace_id=trace_id,
                    status="created",
                    retry_of_turn_id=retry_of_turn_id,
                    created_at=now,
                )
                await self._chat_repo.update_turn(
                    turn_id,
                    experience={
                        "client_context": request.client_context.model_dump(mode="json"),
                    },
                    updated_at=now,
                )
                await self._chat_repo.insert_message_envelope(
                    {
                        "envelope_id": ingress_plan.envelope.envelope_id,
                        "turn_id": turn_id,
                        "conversation_id": conversation_id,
                        "session_id": request.session_id,
                        "member_id": request.member_id,
                        "user_message_id": user_message_id,
                        "dedupe_key": ingress_plan.envelope.dedupe_key,
                        "raw_payload_redacted": ingress_plan.envelope.raw_payload_redacted,
                        "content_parts": ingress_plan.envelope.content_parts,
                        "context_refs": ingress_plan.envelope.context_refs,
                        "model_safe_text": ingress_plan.envelope.model_safe_text,
                        "normalized_summary": ingress_plan.envelope.normalized_summary,
                        "ingress_metadata": ingress_plan.envelope.ingress_metadata,
                        "status": "normalized",
                        "trace_id": trace_id,
                        "created_at": now,
                    }
                )
                await self._chat_repo.insert_queue_item(
                    {
                        "queue_id": self._new_id("chatq"),
                        "turn_id": turn_id,
                        "session_id": request.session_id,
                        "conversation_id": conversation_id,
                        "member_id": request.member_id,
                        "status": ingress_plan.queue_status,
                        "queue_policy": ingress_plan.queue_policy,
                        "position": 0,
                        "dedupe_key": ingress_plan.envelope.dedupe_key,
                        "created_at": now,
                    },
                )
                await self._chat_repo.touch_conversation(conversation_id, now)
                if self._chat_run_ledger_service is not None:
                    await self._chat_run_ledger_service.record_turn_created(
                        turn_id=turn_id,
                        conversation_id=conversation_id,
                        session_id=request.session_id,
                        member_id=request.member_id,
                        trace_id=trace_id,
                        retry_of_turn_id=retry_of_turn_id,
                        channel=str(
                            (ingress_plan.envelope.ingress_metadata or {}).get("channel")
                            or "local"
                        ),
                        source_message_id=str(
                            (ingress_plan.envelope.ingress_metadata or {}).get(
                                "channel_message_id"
                            )
                            or user_message_id
                        ),
                        created_at=now,
                        trace_span_id=root_span_id,
                    )
        except Exception:
            await self._trace.end_span(root_span_id, status=TraceSpanStatus.FAILED)
            await self._trace.end_trace(trace_id, status=TraceStatus.FAILED)
            raise

        should_delay = self._debounce_delay_seconds(
            ingress_plan.envelope.ingress_metadata,
            ingress_plan.queue_policy,
        )
        if should_delay > 0:
            self._execution.schedule(turn_id, delay_seconds=should_delay)
        elif await self._chat_repo.has_running_session_turn(request.session_id, turn_id):
            await self._chat_repo.update_queue_item(
                turn_id,
                status="queued",
                updated_at=utc_now_iso(),
            )
        else:
            self._execution.schedule(turn_id)
        return ChatTurnResponse(
            turn_id=turn_id,
            conversation_id=conversation_id,
            message_id=user_message_id,
            assistant_message_id=None,
            task_id=None,
            trace_id=trace_id,
            status="created",
            stream_url=f"/api/chat/stream/{turn_id}",
            queue_status=ingress_plan.queue_status,
            envelope_id=ingress_plan.envelope.envelope_id,
        )

    async def run_turn(self, turn_id: str) -> None:
        self._require_service("run_turn")
        turn = await self._chat_repo.get_turn(turn_id)
        if turn is None or turn["status"] in {"completed", "failed", "cancelled", "retried"}:
            await self._events.mark_completed(turn_id)
            return
        queue_item = await self._chat_repo.get_queue_item_by_turn(turn_id)
        if queue_item is not None and queue_item["status"] == "queued":
            claimed_queue = await self._chat_repo.claim_turn_for_session(
                turn_id,
                session_id=queue_item["session_id"],
                locked_by="local-api",
                locked_until=self._queue_lock_until(),
                updated_at=utc_now_iso(),
            )
            if not claimed_queue:
                return
        if turn["status"] == "created":
            claimed = await self._chat_repo.try_mark_turn_running(turn_id, utc_now_iso())
            if not claimed:
                latest = await self._chat_repo.get_turn(turn_id)
                if latest is None or latest["status"] in {
                    "completed",
                    "failed",
                    "cancelled",
                    "retried",
                }:
                    await self._events.mark_completed(turn_id)
                elif latest["status"] == "created" and latest["cancel_requested"]:
                    await self._finalize_created_cancel(latest)
                return
            turn["status"] = "running"
            if self._chat_run_ledger_service is not None:
                await self._chat_run_ledger_service.mark_turn_started(
                    turn_id,
                    trace_id=turn.get("trace_id"),
                    started_at=utc_now_iso(),
                    trace_span_id=await self._root_span_id(turn["trace_id"]),
                )
            if queue_item is None:
                await self._chat_repo.update_queue_item(
                    turn_id,
                    status="running",
                    updated_at=utc_now_iso(),
                    started_at=utc_now_iso(),
                    locked_by="local-api",
                    locked_until=self._queue_lock_until(),
                )

        events: list[dict[str, Any]] = []
        visible_profile_token = None
        if self._safety_policy is not None:
            policy = await self._safety_policy.get_policy(
                organization_id=str(turn.get("organization_id") or "org_default")
            )
            visible_profile_token = set_visible_redaction_profile(
                policy.chat_visible_redaction
            )
        try:
            async for _event in self._execute_turn(turn, events):
                pass
        except Exception:
            latest = await self._chat_repo.get_turn(turn_id)
            if latest is not None and latest["status"] in {
                "completed",
                "failed",
                "cancelled",
                "retried",
            }:
                return
            root_span_id = await self._root_span_id(turn["trace_id"])
            async for _ in self._fail_turn(
                turn,
                events,
                ErrorCode.CHAT_RUNTIME_FAILED,
                self._composer.compose_failure(ErrorCode.CHAT_RUNTIME_FAILED, "聊天运行时失败"),
                root_span_id,
                persist_assistant=True,
            ):
                pass
        finally:
            latest = await self._chat_repo.get_turn(turn_id)
            queue_item = await self._chat_repo.get_queue_item_by_turn(turn_id)
            if queue_item is not None:
                queue_status = (
                    latest["status"]
                    if latest
                    and latest["status"] in {"completed", "failed", "cancelled", "retried"}
                    else "failed"
                )
                await self._chat_repo.update_queue_item(
                    turn_id,
                    status=queue_status,
                    updated_at=utc_now_iso(),
                    completed_at=utc_now_iso(),
                )
            if queue_item is not None:
                next_item = await self._chat_repo.next_queued_turn_for_session(
                    queue_item["session_id"],
                    exclude_turn_id=turn_id,
                )
                if next_item is not None:
                    self._execution.schedule(next_item["turn_id"])
            await self._events.mark_completed(turn_id)
            if visible_profile_token is not None:
                reset_visible_redaction_profile(visible_profile_token)

    async def stream_turn_events(self, turn_id: str) -> AsyncIterator[ChatEvent]:
        self._require_service("stream_turn_events")
        turn = await self._chat_repo.get_turn(turn_id)
        if turn is None:
            raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
        persisted = await self._chat_repo.list_events(turn_id)
        last_sequence = 0
        for event_row in persisted:
            last_sequence = event_row["sequence"]
            yield self._event_from_persisted_row(event_row)
        turn = await self._chat_repo.get_turn(turn_id) or turn
        if turn["status"] in {"completed", "failed", "cancelled", "retried"}:
            await self._events.mark_completed(turn_id)
            return
        if turn["status"] in {"created", "running"} and not self._execution.is_running(turn_id):
            self._execution.schedule(turn_id)
        async for event in self._events.subscribe(turn_id, after_sequence=last_sequence):
            yield event

    async def cancel_turn(self, turn_id: str) -> ChatTurnResponse:
        self._require_service("cancel_turn")
        turn = await self._chat_repo.get_turn(turn_id)
        if turn is None:
            raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
        if turn["status"] in {"completed", "failed", "cancelled", "retried"}:
            return ChatTurnResponse(
                turn_id=turn_id,
                conversation_id=turn["conversation_id"],
                message_id=turn["user_message_id"],
                assistant_message_id=turn["assistant_message_id"],
                task_id=None,
                trace_id=turn["trace_id"],
                status=turn["status"],
                stream_url=f"/api/chat/stream/{turn_id}",
            )
        self._events.cancel(turn_id)
        now = utc_now_iso()
        await self._chat_repo.request_cancel(turn_id, now)
        cancelled_created = False
        if turn["status"] == "created":
            response_plan = self._response_plan_for_status(
                turn,
                summary="已停止生成。",
                task_status={"status": "cancelled", "finish_reason": "cancelled"},
            )
            response_plan = self._with_experience_payload(turn, response_plan)
            event = self.event(
                ChatEventType.TURN_CANCELLED,
                turn_id=turn_id,
                trace_id=turn["trace_id"],
                payload={
                    "code": ErrorCode.TURN_CANCELLED.value,
                    "message": "已停止生成",
                    "response_plan": response_plan.model_dump(mode="json"),
                },
            )
            event_data = event.model_dump(mode="json")
            cancelled_created = await self._chat_repo.cancel_created_turn(
                turn_id,
                error_code=ErrorCode.TURN_CANCELLED.value,
                error_message="已停止生成",
                events=[event_data],
                updated_at=now,
            )
            if cancelled_created:
                if self._chat_experience is not None:
                    turn["experience"] = await self._chat_experience.mark_cancelled(
                        turn=turn,
                        partial_text="",
                    )
                    await self._chat_repo.update_turn(
                        turn_id,
                        experience=turn["experience"],
                        updated_at=utc_now_iso(),
                    )
                sequence = await self._record_event(event, [])
                await self._events.append(turn_id, sequence, event)
                root_span_id = await self._root_span_id(turn["trace_id"])
                if root_span_id:
                    cancel_span = await self._trace.start_span(
                        turn["trace_id"],
                        span_type=TraceSpanType.TURN_CANCEL,
                        name="cancel turn",
                        parent_span_id=root_span_id,
                    )
                    await self._trace.end_span(cancel_span)
                    await self._trace.end_span(
                        root_span_id,
                        status=TraceSpanStatus.FAILED,
                        error_code=ErrorCode.TURN_CANCELLED.value,
                    )
                await self._trace.end_trace(turn["trace_id"], status=TraceStatus.FAILED)
                await self._events.mark_completed(turn_id)
        latest = await self._chat_repo.get_turn(turn_id) or turn
        response_status = (
            latest["status"]
            if latest["status"] in {"completed", "failed", "cancelled", "retried"}
            else "cancel_requested"
        )
        return ChatTurnResponse(
            turn_id=turn_id,
            conversation_id=latest["conversation_id"],
            message_id=latest["user_message_id"],
            assistant_message_id=latest["assistant_message_id"],
            task_id=None,
            trace_id=latest["trace_id"],
            status="cancelled" if cancelled_created else response_status,
            stream_url=f"/api/chat/stream/{turn_id}",
        )

    async def retry_turn(self, turn_id: str) -> ChatTurnResponse:
        self._require_service("retry_turn")
        turn = await self._chat_repo.get_turn(turn_id)
        if turn is None:
            raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
        if turn["status"] not in {"completed", "failed", "cancelled"}:
            raise AppError(ErrorCode.CONFLICT, "只能重试已结束的 turn", status_code=409)
        member = await self._members.get_member(turn["member_id"])
        conversation = await self._chat_repo.get_conversation(turn["conversation_id"])
        if member is None or conversation is None:
            raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)
        self._access_policy.assert_can_write(member=member, conversation=conversation)
        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        if user_message is None or not user_message.get("content_text"):
            raise AppError(ErrorCode.NOT_FOUND, "原始用户消息不存在", status_code=404)
        await self._chat_repo.update_turn(
            turn_id,
            status="retried",
            updated_at=utc_now_iso(),
        )
        request = ChatTurnRequest(
            session_id="retry",
            conversation_id=turn["conversation_id"],
            member_id=turn["member_id"],
            input=ChatInput(type="text", text=user_message["content_text"]),
        )
        return await self.create_turn(request, retry_of_turn_id=turn_id)

    async def recover_turns(self) -> int:
        self._require_service("recover_turns")
        running_turns = await self._chat_repo.list_running_turns()
        now = utc_now_iso()
        count = await self._chat_repo.mark_running_turns_failed(now)
        for turn in running_turns:
            event = self.event(
                ChatEventType.TURN_FAILED,
                turn_id=turn["turn_id"],
                trace_id=turn["trace_id"],
                payload={
                    "code": ErrorCode.CHAT_RUNTIME_FAILED.value,
                    "message": "服务重启后运行中的 turn 已被关闭",
                    "route_semantics": {
                        "route": "task_recovery",
                        "route_taxonomy": canonical_route_name("task_recovery"),
                    },
                },
            )
            sequence = await self._record_event(event, [])
            await self._events.append(turn["turn_id"], sequence, event)
            await self._events.mark_completed(turn["turn_id"])
            root_span_id = await self._root_span_id(turn["trace_id"])
            failed_span = await self._trace.start_span(
                turn["trace_id"],
                span_type=TraceSpanType.TURN_FAILED,
                name="recover running turn as failed",
                parent_span_id=root_span_id,
                metadata={"error_code": ErrorCode.CHAT_RUNTIME_FAILED.value},
            )
            await self._trace.end_span(
                failed_span,
                status=TraceSpanStatus.FAILED,
                output_data={"message": "服务重启后运行中的 turn 已被关闭"},
                error_code=ErrorCode.CHAT_RUNTIME_FAILED.value,
            )
            if root_span_id:
                await self._trace.end_span(
                    root_span_id,
                    status=TraceSpanStatus.FAILED,
                    error_code=ErrorCode.CHAT_RUNTIME_FAILED.value,
                )
            await self._trace.end_trace(turn["trace_id"], status=TraceStatus.FAILED)
        return count

    async def _execute_turn(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._execute_turn_impl(turn, events):
            yield event

    async def _execute_turn_impl(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._turn_execution_orchestrator.execute_turn(
            self,
            turn,
            events,
        ):
            yield event

    async def _run_model_path(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatEvent]:
        async for event in self._run_model_path_impl(*args, **kwargs):
            yield event

    async def _run_model_path_impl(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        context: Any,
        user_text: str,
        primary_brain_id: str,
        fallback_brain_ids: list[str],
        model_params: dict[str, Any],
        root_span_id: str | None,
        *,
        intent: str,
        mode: str,
        response_plan: Any | None = None,
        clarification_decision: Any | None = None,
    ) -> AsyncIterator[ChatEvent]:
        del response_plan, clarification_decision
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        token = self._events.token_for(turn_id)
        candidate_ids = self._model_fallback_runtime.candidate_chain(
            primary_brain_id=primary_brain_id,
            fallback_brain_ids=fallback_brain_ids,
        )
        last_error = None
        for index, brain_id in enumerate(candidate_ids):
            brain = await self._brains.get_brain(brain_id)
            if brain is None:
                continue
            if index > 0:
                fallback_span = await self._trace.start_span(
                    trace_id,
                    span_type=TraceSpanType.MODEL_FALLBACK,
                    name="model fallback",
                    parent_span_id=root_span_id,
                    metadata={
                        "brain_id": brain_id,
                        "reason": last_error.code.value if last_error else "fallback",
                    },
                )
                await self._trace.end_span(fallback_span)
                await self._record_stage_recovery_attempt(
                    turn=turn,
                    stage="model",
                    failure_type=self._model_failure_type(last_error),
                    root_cause=last_error.message if last_error else "fallback",
                    recovery_action="fallback_model_route",
                    status="recovered",
                    diagnostic_payload={
                        "failed_error_code": last_error.code.value if last_error else None,
                    },
                    action_result={"fallback_brain_id": brain_id},
                )
                yield await self._emit_and_record(
                    turn_id,
                    trace_id,
                    events,
                    ChatEventType.MODEL_FALLBACK,
                    {
                        "brain_id": brain_id,
                        "reason": last_error.code.value if last_error else "fallback",
                    },
                )
            try:
                async for event in self._call_model(
                    turn,
                    events,
                    context,
                    user_text,
                    brain,
                    model_params,
                    root_span_id,
                    token,
                    intent=intent,
                    mode=mode,
                    fallback_used=index > 0,
                ):
                    yield event
                return
            except Exception as exc:
                if not hasattr(exc, "code") or not hasattr(exc, "message"):
                    raise
                last_error = exc
                if token.cancelled:
                    async for event in self._cancel_turn_during_stream(
                        turn,
                        events,
                        root_span_id,
                    ):
                        yield event
                    return
                if index == len(candidate_ids) - 1:
                    await self._record_stage_recovery_attempt(
                        turn=turn,
                        stage="model",
                        failure_type=self._model_failure_type(exc),
                        root_cause=exc.message,
                        recovery_action="ask_user_for_missing_input"
                        if exc.code == ErrorCode.MODEL_NOT_CONFIGURED
                        else "stop_unrecoverable",
                        status="failed",
                        diagnostic_payload={"error_code": exc.code.value},
                    )
                    async for event in self._fail_turn(
                        turn,
                        events,
                        exc.code,
                        self._composer.compose_failure(exc.code, exc.message),
                        root_span_id,
                        persist_assistant=True,
                    ):
                        yield event
                    return

    async def _complete_without_model(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        text: str,
        root_span_id: str | None,
        *,
        intent: str,
        mode: str,
        response_plan: Any | None = None,
        clarification_decision: Any | None = None,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._complete_without_model_impl(
            turn,
            events,
            text,
            root_span_id,
            intent=intent,
            mode=mode,
            response_plan=response_plan,
            clarification_decision=clarification_decision,
        ):
            yield event

    async def _complete_without_model_impl(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        text: str,
        root_span_id: str | None,
        *,
        intent: str,
        mode: str,
        response_plan: Any | None = None,
        clarification_decision: Any | None = None,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._turn_finalize.complete_without_model(
            self,
            turn,
            events,
            text,
            root_span_id,
            intent=intent,
            mode=mode,
            response_plan=response_plan,
            clarification_decision=clarification_decision,
        ):
            yield event

    async def _handle_host_filesystem_list(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._direct_routes_runtime.handle_host_filesystem_list(
            turn,
            events,
            metadata,
            root_span_id,
            trace_id=trace_id,
        ):
            yield event

    async def _handle_browser_read_page(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._direct_routes_runtime.handle_browser_read_page(
            turn,
            events,
            metadata,
            root_span_id,
            trace_id=trace_id,
        ):
            yield event

    async def _handle_browser_search_readonly(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        route_decision: Any,
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._direct_routes_runtime.handle_browser_search_readonly(
            turn,
            events,
            route_decision,
            root_span_id,
            trace_id=trace_id,
        ):
            yield event

    async def _handle_desktop_capability_boundary(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        route_decision: Any,
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._direct_routes_runtime.handle_desktop_capability_boundary(
            turn,
            events,
            route_decision,
            root_span_id,
            trace_id=trace_id,
        ):
            yield event

    async def _handle_terminal_readonly_command(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._direct_routes_runtime.handle_terminal_readonly_command(
            turn,
            events,
            metadata,
            root_span_id,
            trace_id=trace_id,
        ):
            yield event

    async def _handle_office_chat_request(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        user_text: str,
        office_request: Any,
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        async for event in self._direct_routes_runtime.handle_office_chat_request(
            turn,
            events,
            user_text,
            office_request,
            root_span_id,
            trace_id=trace_id,
        ):
            yield event

    def placeholder_events(self, turn_id: str) -> list[ChatEvent]:
        timestamp = utc_now()
        return [
            ChatEvent(event=ChatEventType.TURN_STARTED, turn_id=turn_id, timestamp=timestamp),
            ChatEvent(event=ChatEventType.CONTEXT_STARTED, turn_id=turn_id, timestamp=timestamp),
            ChatEvent(
                event=ChatEventType.CONTEXT_READY,
                turn_id=turn_id,
                timestamp=timestamp,
                payload={"memories": [], "capabilities": [], "resource_handles": []},
            ),
            ChatEvent(
                event=ChatEventType.MODEL_PLACEHOLDER,
                turn_id=turn_id,
                timestamp=timestamp,
                payload={"reason": "brain_not_configured"},
            ),
            ChatEvent(
                event=ChatEventType.RESPONSE_DELTA,
                turn_id=turn_id,
                timestamp=timestamp,
                payload={"text": PLACEHOLDER_RESPONSE},
            ),
            ChatEvent(event=ChatEventType.RESPONSE_COMPLETED, turn_id=turn_id, timestamp=timestamp),
            ChatEvent(event=ChatEventType.TURN_COMPLETED, turn_id=turn_id, timestamp=timestamp),
        ]

    def event(
        self,
        event: ChatEventType,
        *,
        turn_id: str,
        trace_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ChatEvent:
        return ChatEvent(
            event=event,
            turn_id=turn_id,
            trace_id=trace_id,
            timestamp=utc_now(),
            payload=payload or {},
        )

    def failed_events(self, turn_id: str, reason: str) -> list[ChatEvent]:
        timestamp = utc_now()
        return [
            ChatEvent(
                event=ChatEventType.TURN_FAILED,
                turn_id=turn_id,
                timestamp=timestamp,
                payload={"reason": reason},
            )
        ]

    def _require_service(self, operation: str) -> None:
        if self._bound_context is None:
            raise RuntimeError(f"ChatRuntime is not bound to a service for {operation}")

    def diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "chat_runtime",
            "maturity": "runtime_native",
            "ownership_mode": "exclusive_runtime_host",
            "public_entrypoints": [
                "create_turn",
                "run_turn",
                "stream_turn_events",
                "cancel_turn",
                "retry_turn",
                "recover_turns",
            ],
            "execution_owner": "chat_runtime",
            "state_machine_owner": "chat_runtime",
            "event_source": "chat_runtime",
            "response_finalize_owner": "chat_runtime",
            "session_entrypoint": "session_runtime",
            "compat_host_role": "compat_shell",
            "compat_host": "apps/local-api/app/services/chat.py",
            "delegated_helpers": [
                "chat_turn_execution_orchestrator",
                "chat_turn_finalize_service",
                "chat_model_execution_service",
                "chat_direct_routes_runtime",
            ],
        }


def utc_now() -> datetime:
    return datetime.now(UTC)
