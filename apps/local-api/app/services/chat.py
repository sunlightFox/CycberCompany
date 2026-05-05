from __future__ import annotations

import html
import re
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from brain import BrainRouteRequest, ModelRouter
from brain.adapters import (
    CancelToken,
    ModelAdapterError,
    ModelChatRequest,
    OpenAICompatibleClient,
    estimate_messages_tokens,
)
from chat_runtime import ChatRuntime
from core_types import (
    ChatEvent,
    ChatEventType,
    ChatInput,
    ChatTurnRequest,
    ChatTurnResponse,
    ContextPacket,
    ErrorCode,
    ResponsePlan,
    RiskLevel,
    TaskMode,
    TraceSpanStatus,
    TraceSpanType,
    TraceStatus,
)
from response_composer import ComposeRequest, ResponseComposer
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.brain_repo import BrainRepository
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.member_repo import MemberRepository
from app.db.session import Database
from app.schemas.tasks import ToolExecuteRequest
from app.services.asset_broker import AssetBrokerService
from app.services.audit import AuditEventService
from app.services.brain_decision import BrainDecisionService
from app.services.chat_context import ChatContextCoordinator
from app.services.chat_continuation import ChatContinuationCoordinator, ContinuationEvaluation
from app.services.chat_experience import ChatExperienceService, ClarificationDecision
from app.services.chat_ingress import ChatContentNormalizer, ChatIngressService
from app.services.chat_intent_router import (
    ChatIntentRouter,
    OfficeChatRequest,
    office_skill_input,
    preferred_office_bundle_id,
    preferred_office_tool_name,
)
from app.services.chat_memory import ChatMemoryCoordinator
from app.services.chat_model import ChatModelCoordinator
from app.services.chat_privacy import ChatPrivacyCoordinator
from app.services.chat_quality import ChatQualityPolicy
from app.services.chat_response import ChatResponseCoordinator
from app.services.chat_safety import ChatTurnAccessPolicy
from app.services.chat_tasks import ChatTaskCoordinator, ChatTurnOrchestrator
from app.services.context_gateway import RuntimeContextGateway
from app.services.memory import MemoryCommandResult, MemoryService
from app.services.model_routing import ModelRoutingService
from app.services.natural_chat import (
    NaturalChatActionGateway,
    pending_action_from_approval,
    response_plan_for_pending_action,
    reset_visible_redaction_profile,
    set_visible_redaction_profile,
)
from app.services.safety_policy import RuntimeSafetyPolicyService
from app.services.secrets import SecretStore
from app.services.turn_events import TurnEventStore
from app.services.turn_execution import TurnExecutionManager
from app.services.turn_recovery import TurnRecoveryResult, TurnRecoveryService

DEFAULT_USER_ID = "user_local_owner"


def _reply_option_items(options: list[str]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for option in options:
        label = str(option)
        code = "edit"
        if any(marker in label for marker in ["只允许", "本次允许", "确认"]):
            code = "once"
        elif "本会话" in label:
            code = "session"
        elif any(marker in label for marker in ["拒绝", "取消"]):
            code = "deny"
        items.append({"code": code, "label": label})
    return items


def _request_text(request: ChatTurnRequest) -> str:
    if request.input.text:
        return request.input.text
    text_parts = [
        str(part.text)
        for part in request.input.content_parts
        if part.type == "text" and part.text
    ]
    if text_parts:
        return "\n".join(text_parts)
    labels = [
        str(part.name or part.ref_id or part.uri or part.type)
        for part in request.input.content_parts
    ]
    return "\n".join(labels) or "multi_part"


def _content_payload(envelope: dict[str, Any]) -> dict[str, Any]:
    return {
        "envelope_id": envelope["envelope_id"],
        "content_parts": envelope.get("content_parts") or [],
        "context_refs": envelope.get("context_refs") or [],
        "normalized_summary": envelope.get("normalized_summary") or {},
        "model_safe_text_chars": len(str(envelope.get("model_safe_text") or "")),
    }


def _queue_payload(queue_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue_id": queue_item["queue_id"],
        "status": queue_item["status"],
        "session_id": queue_item["session_id"],
        "queue_policy": queue_item.get("queue_policy") or "immediate",
        "position": int(queue_item.get("position") or 0),
    }


def _model_failure_type(error: ModelAdapterError | None) -> str:
    if error is None:
        return "model_unavailable"
    code = error.code
    if code == ErrorCode.MODEL_NOT_CONFIGURED:
        return "model_not_configured"
    if code == ErrorCode.MODEL_TIMEOUT:
        return "model_timeout"
    if code == ErrorCode.MODEL_PROTOCOL_ERROR:
        return "model_invalid_response"
    return "model_unavailable"


def _error_signature(stage: str, failure_type: str, root_cause: str) -> str:
    value = f"{stage}:{failure_type}:{redact(root_cause)}"
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _context_compaction_summary(context: ContextPacket) -> str:
    messages = context.conversation.last_messages[-4:]
    lines: list[str] = []
    if context.conversation.recent_summary:
        lines.append(str(redact(context.conversation.recent_summary))[:400])
    for message in messages:
        text = str(
            message.get("model_safe_content_text")
            or message.get("content_text")
            or ""
        ).strip()
        if text:
            lines.append(str(redact(text))[:240])
    summary = "\n".join(lines).strip()
    return summary[:1200] or "上下文已压缩为当前用户输入和最近对话摘要。"


def _debounce_delay_seconds(metadata: dict[str, Any], queue_policy: str) -> float:
    if queue_policy != "collect":
        return 0.0
    try:
        debounce_ms = int(metadata.get("debounce_ms") or 0)
    except (TypeError, ValueError):
        debounce_ms = 0
    return max(0.0, min(float(debounce_ms) / 1000.0, 30.0))


def _queue_lock_until(seconds: int = 300) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


class ChatService:
    def __init__(
        self,
        db: Database,
        trace_service: TraceService,
        audit_service: AuditEventService,
        model_routing: ModelRoutingService,
        secret_store: SecretStore,
        memory_service: MemoryService,
        agent_workbench_service: Any | None = None,
        asset_broker_service: AssetBrokerService | None = None,
        persona_heart_service: Any | None = None,
        task_engine: Any | None = None,
        chat_experience_service: ChatExperienceService | None = None,
        brain_decision_service: BrainDecisionService | None = None,
        approval_service: Any | None = None,
        scheduled_task_service: Any | None = None,
        project_deployment_service: Any | None = None,
        host_install_service: Any | None = None,
        skill_plugin_service: Any | None = None,
        skill_governance_service: Any | None = None,
        tool_runtime: Any | None = None,
        voice_service: Any | None = None,
        safety_policy_service: RuntimeSafetyPolicyService | None = None,
    ) -> None:
        self._db = db
        self._chat_repo = ChatRepository(db)
        self._members = MemberRepository(db)
        self._brains = BrainRepository(db)
        self._trace = trace_service
        self._audit = audit_service
        self._model_routing = model_routing
        self._secrets = secret_store
        self._memory = memory_service
        self._agent_workbench = agent_workbench_service
        self._asset_broker = asset_broker_service
        self._persona_heart = persona_heart_service
        self._task_engine = task_engine
        self._chat_experience = chat_experience_service
        self._brain_decision = brain_decision_service
        self._approval_service = approval_service
        self._scheduled_tasks = scheduled_task_service
        self._project_deployments = project_deployment_service
        self._host_installs = host_install_service
        self._skill_plugins = skill_plugin_service
        self._skill_governance = skill_governance_service
        self._tool_runtime = tool_runtime
        self._voice = voice_service
        self._safety_policy = safety_policy_service
        self._natural_chat = (
            NaturalChatActionGateway(
                chat_repo=self._chat_repo,
                approval_service=approval_service,
                task_engine=task_engine,
                host_install_service=host_install_service,
            )
            if approval_service is not None
            else None
        )
        self._runtime = ChatRuntime()
        self._model_router = ModelRouter()
        self._model_coordinator = ChatModelCoordinator()
        self._privacy = ChatPrivacyCoordinator(model_coordinator=self._model_coordinator)
        self._composer = ResponseComposer()
        self._quality = ChatQualityPolicy(composer=self._composer)
        self._memory_coordinator = ChatMemoryCoordinator()
        self._task_coordinator = ChatTaskCoordinator()
        self._context_coordinator = ChatContextCoordinator()
        self._continuation = ChatContinuationCoordinator()
        self._response_coordinator = ChatResponseCoordinator()
        self._turn_orchestrator = ChatTurnOrchestrator()
        self._access_policy = ChatTurnAccessPolicy()
        self._intent_router = ChatIntentRouter()
        self._events = TurnEventStore()
        self._ingress = ChatIngressService(
            chat_repo=self._chat_repo,
            normalizer=ChatContentNormalizer(
                asset_broker=asset_broker_service,
                trace_service=trace_service,
            ),
        )
        self._turn_recovery = (
            TurnRecoveryService(
                chat_repo=self._chat_repo,
                task_engine=task_engine,
                trace_service=trace_service,
                composer=self._composer,
            )
            if task_engine is not None
            else None
        )
        self._context_gateway = RuntimeContextGateway(
            chat_repo=self._chat_repo,
            member_repo=self._members,
            brain_repo=self._brains,
            trace_service=self._trace,
            memory_service=self._memory,
            asset_broker_service=asset_broker_service,
            persona_heart_service=persona_heart_service,
            chat_experience_service=chat_experience_service,
            agent_workbench_service=agent_workbench_service,
        )
        self._execution = TurnExecutionManager(self.run_turn)

    async def create_turn(
        self,
        request: ChatTurnRequest,
        *,
        retry_of_turn_id: str | None = None,
    ) -> ChatTurnResponse:
        member = await self._members.get_member(request.member_id)
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)

        input_text = _request_text(request)
        conversation_id = request.conversation_id
        created_conversation_title: str | None = None
        if conversation_id is None:
            created_conversation_title = _title_from_text(input_text)
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

        turn_id = new_id("turn")
        user_message_id = new_id("msg")
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
                    author_id=DEFAULT_USER_ID,
                    content_type=request.input.type,
                    content_text=ingress_plan.envelope.model_safe_text,
                    content={
                        "type": request.input.type,
                        "text": ingress_plan.envelope.model_safe_text,
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
                        "queue_id": new_id("chatq"),
                        "turn_id": turn_id,
                        "session_id": request.session_id,
                        "conversation_id": conversation_id,
                        "member_id": request.member_id,
                        "status": ingress_plan.queue_status,
                        "queue_policy": ingress_plan.queue_policy,
                        "position": 0,
                        "dedupe_key": ingress_plan.envelope.dedupe_key,
                        "created_at": now,
                    }
                )
                await self._chat_repo.touch_conversation(conversation_id, now)
        except Exception:
            await self._trace.end_span(root_span_id, status=TraceSpanStatus.FAILED)
            await self._trace.end_trace(trace_id, status=TraceStatus.FAILED)
            raise

        should_delay = _debounce_delay_seconds(
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

    async def stream_turn_events(self, turn_id: str) -> AsyncIterator[ChatEvent]:
        turn = await self._chat_repo.get_turn(turn_id)
        if turn is None:
            raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
        persisted = await self._chat_repo.list_events(turn_id)
        last_sequence = 0
        for event_row in persisted:
            last_sequence = event_row["sequence"]
            yield _event_from_persisted(event_row)
        turn = await self._chat_repo.get_turn(turn_id) or turn
        if turn["status"] in {"completed", "failed", "cancelled", "retried"}:
            await self._events.mark_completed(turn_id)
            return
        if turn["status"] in {"created", "running"} and not self._execution.is_running(turn_id):
            self._execution.schedule(turn_id)
        async for event in self._events.subscribe(turn_id, after_sequence=last_sequence):
            yield event

    async def run_turn(self, turn_id: str) -> None:
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
                locked_until=_queue_lock_until(),
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
            if queue_item is None:
                await self._chat_repo.update_queue_item(
                    turn_id,
                    status="running",
                    updated_at=utc_now_iso(),
                    started_at=utc_now_iso(),
                    locked_by="local-api",
                    locked_until=_queue_lock_until(),
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

    async def recover_incomplete_turns(self) -> int:
        running_turns = await self._chat_repo.list_running_turns()
        now = utc_now_iso()
        count = await self._chat_repo.mark_running_turns_failed(now)
        for turn in running_turns:
            event = self._runtime.event(
                ChatEventType.TURN_FAILED,
                turn_id=turn["turn_id"],
                trace_id=turn["trace_id"],
                payload={
                    "code": ErrorCode.CHAT_RUNTIME_FAILED.value,
                    "message": "服务重启后运行中的 turn 已被关闭",
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

    async def cancel_turn(self, turn_id: str) -> ChatTurnResponse:
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
            response_plan = self._composer.response_plan_for_status(
                summary="已停止生成。",
                task_status={"status": "cancelled", "finish_reason": "cancelled"},
            )
            response_plan = self._with_experience_payload(turn, response_plan)
            event = self._runtime.event(
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

    async def _collect_into_existing_turn(
        self,
        *,
        request: ChatTurnRequest,
        collect_turn_id: str,
        incoming_envelope: Any,
        trace_id: str,
        root_span_id: str | None,
    ) -> ChatTurnResponse:
        existing_turn = await self._chat_repo.get_turn(collect_turn_id)
        existing_envelope = await self._chat_repo.get_message_envelope_by_turn(collect_turn_id)
        if existing_turn is None or existing_envelope is None:
            raise AppError(ErrorCode.NOT_FOUND, "可合并的聊天 turn 不存在", status_code=404)
        merged = self._ingress.merge_envelopes(existing_envelope, incoming_envelope)
        now = utc_now_iso()
        content_type = "multi_part" if len(merged.content_parts) > 1 else request.input.type
        async with self._db.transaction():
            await self._chat_repo.merge_message_envelope(
                collect_turn_id,
                raw_payload_redacted=merged.raw_payload_redacted,
                content_parts=merged.content_parts,
                context_refs=merged.context_refs,
                model_safe_text=merged.model_safe_text,
                normalized_summary=merged.normalized_summary,
                ingress_metadata=merged.ingress_metadata,
                status="normalized",
                updated_at=now,
            )
            await self._chat_repo.update_user_message_content(
                existing_turn["user_message_id"],
                content_type=content_type,
                content_text=merged.model_safe_text,
                content={
                    "type": content_type,
                    "text": merged.model_safe_text,
                    "session_id": request.session_id,
                    "content_parts": merged.content_parts,
                    "context_refs": merged.context_refs,
                    "attachments": [
                        item.model_dump(mode="json") for item in request.attachments
                    ],
                    "ingress_metadata": merged.ingress_metadata,
                    "normalized_summary": merged.normalized_summary,
                    "client_context": request.client_context.model_dump(mode="json"),
                    "collected_into_turn_id": collect_turn_id,
                },
            )
            await self._chat_repo.update_queue_policy(
                collect_turn_id,
                status="queued",
                queue_policy="collect",
                updated_at=now,
                locked_until=None,
            )
            await self._chat_repo.update_turn(
                collect_turn_id,
                updated_at=now,
            )
            await self._chat_repo.touch_conversation(existing_turn["conversation_id"], now)
        if not self._execution.is_running(collect_turn_id):
            self._execution.schedule(
                collect_turn_id,
                delay_seconds=_debounce_delay_seconds(
                    merged.ingress_metadata,
                    "collect",
                ),
            )
        del root_span_id
        return ChatTurnResponse(
            turn_id=collect_turn_id,
            conversation_id=existing_turn["conversation_id"],
            message_id=existing_turn["user_message_id"],
            assistant_message_id=existing_turn["assistant_message_id"],
            task_id=None,
            trace_id=existing_turn["trace_id"],
            status="superseded",
            stream_url=f"/api/chat/stream/{collect_turn_id}",
            queue_status="superseded",
            envelope_id=merged.envelope_id,
        )

    async def placeholder_events(self, turn_id: str) -> list[ChatEvent]:
        rows = await self._chat_repo.list_events(turn_id)
        if rows:
            return [_event_from_persisted(row) for row in rows]
        return self._runtime.placeholder_events(turn_id)

    async def _execute_turn(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> AsyncIterator[ChatEvent]:
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        root_span_id = await self._root_span_id(trace_id)

        async def emit(
            event_type: ChatEventType,
            payload: dict[str, Any] | None = None,
        ) -> ChatEvent:
            return await self._emit_and_record(turn_id, trace_id, events, event_type, payload)

        queue_item = await self._chat_repo.get_queue_item_by_turn(turn_id)
        envelope = await self._chat_repo.get_message_envelope_by_turn(turn_id)
        if queue_item is not None:
            yield await emit(
                ChatEventType.TURN_QUEUED,
                {
                    "queue_id": queue_item["queue_id"],
                    "status": "queued",
                    "session_id": queue_item["session_id"],
                    "queue_policy": queue_item["queue_policy"],
                    "position": queue_item["position"],
                },
            )
            yield await emit(
                ChatEventType.TURN_QUEUE_STARTED,
                {
                    "queue_id": queue_item["queue_id"],
                    "status": "running",
                    "session_id": queue_item["session_id"],
                },
            )
        if envelope is not None:
            yield await emit(
                ChatEventType.CONTENT_NORMALIZED,
                {
                    "envelope_id": envelope["envelope_id"],
                    "dedupe_key": envelope["dedupe_key"],
                    "normalized_summary": envelope["normalized_summary"],
                    "content": _content_payload(envelope),
                },
            )
        yield await emit(ChatEventType.TURN_STARTED, {"status": "running"})
        yield await emit(ChatEventType.CONTEXT_STARTED)

        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        user_text = str(user_message["content_text"] if user_message else "")
        session_id = _session_id_from_message(user_message)

        safety_span = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.SAFETY_PRIVACY_CLASSIFY,
            name="classify chat privacy",
            parent_span_id=root_span_id,
            input_data={"text": redact(user_text)},
        )
        privacy = self._privacy.classify(user_text)
        await self._trace.end_span(
            safety_span,
            output_data={
                "privacy_level": privacy.privacy_level,
                "sensitivity_hits": privacy.sensitivity_hits,
                "allow_cloud": privacy.allow_cloud,
            },
        )
        brain_decision = (
            await self._brain_decision.decide(
                text=user_text,
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                turn_id=turn_id,
                privacy_level=privacy.privacy_level,
                trace_id=trace_id,
                root_span_id=root_span_id,
            )
            if self._brain_decision is not None
            else None
        )
        if brain_decision is not None:
            turn["brain_decision_id"] = brain_decision.brain_decision_id
            turn["intent"] = brain_decision.intent.primary_intent
            turn["mode"] = brain_decision.mode.mode

        context_span = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.CONTEXT_BUILD,
            name="build context packet",
            parent_span_id=root_span_id,
        )
        try:
            context = await self._context_gateway.build(
                turn=turn,
                root_span_id=root_span_id,
                context_decision=brain_decision.context if brain_decision else None,
            )
        except Exception as exc:
            await self._trace.end_span(context_span, status=TraceSpanStatus.FAILED)
            await self._record_stage_recovery_attempt(
                turn=turn,
                stage="context",
                failure_type="context_build_failed",
                root_cause=str(exc),
                recovery_action="rebuild_minimal_context",
                status="failed",
                diagnostic_payload={"reason": "context_build_exception"},
            )
            async for event in self._fail_turn(
                turn,
                events,
                ErrorCode.CONTEXT_BUILD_FAILED,
                "上下文构建失败",
                root_span_id,
            ):
                yield event
            return
        context_filter_summary = self._context_coordinator.redaction_summary(
            context,
            sensitivity_hits=getattr(privacy, "sensitivity_hits", []),
        )
        await self._trace.end_span(
            context_span,
            output_data={
                "recent_messages": len(context.conversation.last_messages),
                "summary": bool(context.conversation.recent_summary),
                "memory_blocks": len(context.memories),
                "context_redaction": context_filter_summary,
                "brain_decision_id": brain_decision.brain_decision_id
                if brain_decision
                else None,
            },
        )
        if self._chat_experience is not None:
            signals = await self._chat_experience.analyze_turn(
                turn=turn,
                user_text=user_text,
                context=context,
                privacy_level=privacy.privacy_level,
            )
            turn["experience"] = {
                **dict(turn.get("experience") or {}),
                **signals.as_payload(),
            }
            await self._chat_repo.update_turn(
                turn_id,
                experience=turn["experience"],
                privacy_level=privacy.privacy_level,
                updated_at=utc_now_iso(),
            )
        context_ready_payload = {
            "context_packet_id": context.context_packet_id,
            "recent_messages": len(context.conversation.last_messages),
            "memory_blocks": len(context.memories),
            "decision_id": brain_decision.brain_decision_id if brain_decision else None,
            "confidence": brain_decision.confidence if brain_decision else None,
            "context_decision": (
                brain_decision.context.model_dump(mode="json") if brain_decision else {}
            ),
            "selection_reason": (
                brain_decision.context.selection_reason
                if brain_decision
                else (turn.get("experience") or {}).get(
                    "context_selection_reason",
                    ["current_input", "recent_messages", "capability_boundary_summary"],
                )
            ),
            "route_profile": (turn.get("experience") or {}).get("route_profile"),
            "conversation_depth": (turn.get("experience") or {}).get("conversation_depth"),
            "context_redaction": context_filter_summary,
        }
        async for compaction_event in self._maybe_record_context_compaction(
            turn,
            context,
            context_filter_summary,
            root_span_id,
            emit,
        ):
            yield compaction_event
        yield await emit(ChatEventType.CONTEXT_READY, context_ready_payload)
        if self._events.token_for(turn_id).cancelled:
            async for event in self._cancel_turn_during_stream(turn, events, root_span_id):
                yield event
            return

        quality_outcome = self._quality.handle(
            user_text=user_text,
            privacy_level=privacy.privacy_level,
            sensitivity_hits=getattr(privacy, "sensitivity_hits", []),
            brain_intent=brain_decision.intent.primary_intent
            if brain_decision is not None
            else None,
        )
        if quality_outcome is not None:
            await self._chat_repo.update_turn(
                turn_id,
                intent=quality_outcome.intent,
                mode=quality_outcome.mode,
                privacy_level=privacy.privacy_level,
                updated_at=utc_now_iso(),
            )
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": quality_outcome.intent,
                    "reason_codes": ["chat_quality_policy"],
                },
            )
            yield await emit(
                ChatEventType.MODE_SELECTED,
                {"mode": quality_outcome.mode, "needs_tool": False},
            )
            async for event in self._complete_without_model(
                turn,
                events,
                quality_outcome.text,
                root_span_id,
                intent=quality_outcome.intent,
                mode=quality_outcome.mode,
                response_plan=quality_outcome.response_plan,
            ):
                yield event
            return

        if self._natural_chat is not None:
            natural_outcome = await self._natural_chat.handle(
                turn=turn,
                user_text=user_text,
                session_id=session_id,
                trace_id=trace_id,
            )
            if natural_outcome is not None:
                yield await emit(
                    ChatEventType.INTENT_DETECTED,
                    {
                        "intent": natural_outcome.intent,
                        "reason_codes": ["natural_chat_action_gateway"],
                    },
                )
                yield await emit(
                    ChatEventType.MODE_SELECTED,
                    {"mode": natural_outcome.mode, "needs_tool": False},
                )
                async for event in self._complete_without_model(
                    turn,
                    events,
                    natural_outcome.text,
                    root_span_id,
                    intent=natural_outcome.intent,
                    mode=natural_outcome.mode,
                    response_plan=natural_outcome.response_plan,
                ):
                    yield event
                return

        allow_direct_memory_command = self._memory_coordinator.allow_direct_command(
            user_text,
            brain_decision,
        )
        memory_command = (
            await self._memory.handle_explicit_chat_command(
                text=user_text,
                member_id=turn["member_id"],
                conversation_id=turn["conversation_id"],
                turn_id=turn_id,
                message_id=turn["user_message_id"],
                trace_id=trace_id,
                root_span_id=root_span_id,
            )
            if allow_direct_memory_command
            else None
        )
        if memory_command is not None and memory_command.handled:
            memory_intent = self._memory_coordinator.command_intent(memory_command)
            memory_summary = memory_command.response_text or "记忆命令已处理。"
            await self._chat_repo.update_turn(
                turn_id,
                intent=memory_intent,
                mode=TaskMode.DIRECT_WITH_MEMORY.value,
                privacy_level=privacy.privacy_level,
                updated_at=utc_now_iso(),
            )
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": memory_intent,
                    "reason_codes": ["explicit_memory_command", memory_intent],
                },
            )
            yield await emit(
                ChatEventType.MODE_SELECTED,
                {"mode": TaskMode.DIRECT_WITH_MEMORY.value, "needs_tool": False},
            )
            async for event in self._emit_memory_events(turn, events, memory_command):
                yield event
            async for event in self._complete_without_model(
                turn,
                events,
                memory_summary,
                root_span_id,
                intent=memory_intent,
                mode=TaskMode.DIRECT_WITH_MEMORY.value,
                response_plan=self._composer.response_plan_for_status(
                    summary=memory_summary,
                    memory_notice=self._memory_coordinator.command_notice(memory_command),
                ),
            ):
                yield event
            return

        scheduled_request = self._task_coordinator.scheduled_intents.parse(user_text)
        if scheduled_request is not None and self._scheduled_tasks is not None:
            from app.schemas.scheduled_tasks import ScheduledTaskCreateRequest

            scheduled_task = await self._scheduled_tasks.create(
                ScheduledTaskCreateRequest(
                    conversation_id=turn["conversation_id"],
                    owner_member_id=turn["member_id"],
                    title=scheduled_request.title,
                    goal=scheduled_request.goal,
                    schedule=scheduled_request.schedule,
                    execution_policy={"attendance": "unattended"},
                    constraints={"source": "chat_text", "phase": "phase36"},
                    created_by_member_id=DEFAULT_USER_ID,
                ),
                trace_id=trace_id,
            )
            text = (
                "定时任务已经建好了。到时间后我会先按后台流程往下推；"
                "一碰到下载、登录、删除、终端或外发这类高风险动作，我会停一下，再找你确认。"
            )
            response_plan = self._composer.response_plan_for_status(
                summary=text,
                task_status={
                    "scheduled_task_id": scheduled_task.scheduled_task_id,
                    "status": scheduled_task.status,
                    "next_run_at": scheduled_task.next_run_at.isoformat()
                    if scheduled_task.next_run_at
                    else None,
                    "background_execution_policy": scheduled_task.execution_policy,
                },
            )
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": "scheduled_task_request",
                    "reason_codes": ["phase36_scheduled_task_text"],
                },
            )
            yield await emit(
                ChatEventType.MODE_SELECTED,
                {"mode": TaskMode.DIRECT.value, "needs_tool": False},
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="scheduled_task_request",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return

        route_decision = self._intent_router.decide(user_text)
        turn["experience"] = {
            **dict(turn.get("experience") or {}),
            "chat_route_decision": route_decision.as_payload(),
        }
        await self._chat_repo.update_turn(
            turn_id,
            experience=turn["experience"],
            privacy_level=privacy.privacy_level,
            updated_at=utc_now_iso(),
        )
        if route_decision.office_request is not None:
            async for event in self._handle_office_chat_request(
                turn,
                events,
                user_text,
                route_decision.office_request,
                root_span_id,
                trace_id=trace_id,
            ):
                yield event
            return
        if route_decision.route_type == "host_filesystem_list":
            async for event in self._handle_host_filesystem_list(
                turn,
                events,
                route_decision.metadata,
                root_span_id,
                trace_id=trace_id,
            ):
                yield event
            return
        if route_decision.route_type == "browser_read_page":
            async for event in self._handle_browser_read_page(
                turn,
                events,
                route_decision.metadata,
                root_span_id,
                trace_id=trace_id,
            ):
                yield event
            return
        if route_decision.route_type == "terminal_readonly_command":
            async for event in self._handle_terminal_readonly_command(
                turn,
                events,
                route_decision.metadata,
                root_span_id,
                trace_id=trace_id,
            ):
                yield event
            return
        direct_route_reply = _direct_route_reply(route_decision.route_type, user_text)
        if direct_route_reply is not None:
            text, intent, structured = direct_route_reply
            response_plan = self._composer.response_plan_for_status(
                summary=text,
                task_status={"status": "not_created", "reason": route_decision.reason_code},
                safety_notice="没有创建任务，也没有执行下载、安装或外部动作。",
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "route_semantics": {
                            "route": route_decision.route_type,
                            "model_called": False,
                            "task_created": False,
                            "tool_created": False,
                            "reason_code": route_decision.reason_code,
                        },
                        **structured,
                    },
                }
            )
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": intent,
                    "reason_codes": [route_decision.reason_code],
                },
            )
            yield await emit(
                ChatEventType.MODE_SELECTED,
                {"mode": TaskMode.DIRECT.value, "needs_tool": False},
            )
            async for event in self._complete_without_model(
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

        media_request = self._task_coordinator.parse_media_task_request(user_text)
        if media_request is not None and self._task_engine is not None:
            from app.schemas.tasks import TaskCreateRequest

            task = await self._task_engine.create_task(
                TaskCreateRequest(
                    conversation_id=turn["conversation_id"],
                    owner_member_id=turn["member_id"],
                    goal=user_text,
                    mode_hint=TaskMode.WORKFLOW,
                    planner_context={
                        "intent": "media_runtime_request",
                        "phase": "phase43",
                        "media_request": media_request,
                        "privacy": self._privacy.planner_context(
                            privacy_level=privacy.privacy_level,
                            allow_cloud=privacy.allow_cloud,
                            sensitivity_hits=getattr(privacy, "sensitivity_hits", []),
                        ),
                    },
                    auto_start=False,
                    client_request_id=f"chat:{turn_id}:media-task",
                ),
                trace_id=trace_id,
            )
            text = (
                "已创建受控媒体任务。视频分析和剪辑只会处理任务 artifact 中的媒体；"
                "剪辑渲染、导出或外部上传前会再次等待确认。"
            )
            if media_request["plan_only"]:
                text = "已创建受控媒体计划任务；我会只生成剪辑方案，不渲染或导出视频。"
            response_plan = self._composer.response_plan_for_status(
                summary=text,
                task_status={
                    "task_id": task.task_id,
                    "status": task.status.value,
                    "mode": task.mode.value,
                    "media_runtime": media_request,
                },
            )
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": "media_runtime_request",
                    "reason_codes": ["phase43_media_text_request"],
                },
            )
            yield await emit(
                ChatEventType.TASK_CREATED,
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "status": task.status.value,
                },
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="media_runtime_request",
                mode=TaskMode.WORKFLOW.value,
                response_plan=response_plan,
            ):
                yield event
            return

        if _phase52_deploy_or_install_explain_only(user_text):
            text = (
                "可以。安全的项目部署通常分为：确认源码来源，创建受控项目工作区，"
                "识别技术栈，准备 portable 运行时，安装项目内依赖，构建，启动预览，"
                "做健康检查并保留日志。安装桌面软件则应先确认可信来源、命令、影响范围"
                "和回滚方式，再由用户确认；我不会在你要求“不要执行”时创建任务或调用工具。"
            )
            response_plan = self._composer.response_plan_for_status(
                summary=text,
                task_status={"status": "not_created", "reason": "phase52_direct_only"},
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "route_semantics": {
                            "model_not_required_reason": "phase52_direct_only_explanation",
                            "task_created": False,
                        },
                    },
                }
            )
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": "project_deploy_explanation",
                    "reason_codes": ["phase52_direct_only"],
                },
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="project_deploy_explanation",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return

        deploy_request = self._task_coordinator.parse_project_deploy_request(user_text)
        if deploy_request is not None and self._project_deployments is not None:
            from app.schemas.project_deployments import ProjectDeployRequest

            deployment = await self._project_deployments.create_plan(
                ProjectDeployRequest(
                    member_id=turn["member_id"],
                    conversation_id=turn["conversation_id"],
                    source_uri=deploy_request["source_uri"],
                    target=deploy_request["target"],
                    constraints=deploy_request["constraints"],
                ),
                trace_id=trace_id,
            )
            text = (
                "我已创建受控项目部署计划。接下来会在项目工作区中准备源码、识别技术栈、"
                "准备运行时、安装项目依赖、构建并启动预览；这不会修改系统全局环境。"
                "需要联网下载依赖或占用本地端口的步骤会等待你确认。"
            )
            response_plan = self._composer.response_plan_for_status(
                summary=text,
                task_status={
                    "task_id": deployment.task_id,
                    "status": deployment.status,
                    "mode": "workflow",
                },
            )
            response_plan = response_plan.model_copy(
                update={
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
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": "project_deploy_request",
                    "reason_codes": ["phase52_project_deploy_text_request"],
                },
            )
            yield await emit(
                ChatEventType.TASK_CREATED,
                {
                    "task_id": deployment.task_id,
                    "title": "项目部署计划",
                    "status": deployment.status,
                },
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="project_deploy_request",
                mode=TaskMode.WORKFLOW.value,
                response_plan=response_plan,
            ):
                yield event
            return

        host_install_request = self._task_coordinator.parse_host_install_request(user_text)
        if host_install_request is not None and self._host_installs is not None:
            from app.schemas.project_deployments import HostInstallPlanRequest

            host_action = str(host_install_request.get("action") or "install")
            action_label = "卸载" if host_action == "uninstall" else "安装"
            plan = await self._host_installs.create_plan(
                HostInstallPlanRequest(
                    member_id=turn["member_id"],
                    conversation_id=turn["conversation_id"],
                    requested_software=host_install_request["requested_software"],
                    install_scope=host_install_request["install_scope"],
                    dry_run=True,
                ),
                trace_id=trace_id,
            )
            pending_action: dict[str, Any] | None = None
            if plan.approval_id and self._approval_service is not None:
                approval = await self._approval_service.get(plan.approval_id)
                pending_action = pending_action_from_approval(
                    approval,
                    session_id=session_id,
                    source_turn_id=turn_id,
                )
            already_absent = bool(
                plan.install_source.get("already_absent")
                or plan.impact_summary.get("already_absent")
            )
            if already_absent:
                facts = {
                    "status": "already_absent",
                    "action_type": f"host.{host_action}_software",
                    "action_label": f"{action_label}本机软件",
                    "target": plan.requested_software,
                    "risk_level": plan.risk_level.value
                    if isinstance(plan.risk_level, RiskLevel)
                    else str(plan.risk_level),
                    "approval_required": False,
                    "already_absent": True,
                    "reply_options": [],
                    "reply_option_items": [],
                }
                pending_action = None
            elif plan.status == "manual_only":
                reason_codes = list(plan.impact_summary.get("reason_codes") or [])
                safe_next_step = str(plan.impact_summary.get("safe_next_step") or "").strip()
                if "no_high_confidence_healthy_package_candidate" in reason_codes:
                    failure_reason = safe_next_step or "当前没有可用的健康包管理器候选。"
                else:
                    failure_reason = "这个请求涉及高风险或需要人工处理的系统级变更。"
                facts = {
                    "status": "manual_only",
                    "action_type": f"host.{host_action}_software",
                    "action_label": f"{action_label}本机软件",
                    "target": plan.requested_software,
                    "risk_level": plan.risk_level.value
                    if isinstance(plan.risk_level, RiskLevel)
                    else str(plan.risk_level),
                    "approval_required": False,
                    "failure_reason": failure_reason,
                    "safe_next_step": safe_next_step,
                    "reply_options": [],
                    "reply_option_items": [],
                }
            else:
                reply_options = (
                    list(pending_action.get("reply_options") or [])
                    if pending_action
                    else []
                )
                facts = {
                    "status": "pending_action",
                    "action_type": f"host.{host_action}_software",
                    "action_label": f"{action_label}本机软件",
                    "target": plan.requested_software,
                    "risk_level": plan.risk_level.value
                    if isinstance(plan.risk_level, RiskLevel)
                    else str(plan.risk_level),
                    "approval_required": bool(plan.approval_id),
                    "reply_options": reply_options,
                    "reply_option_items": _reply_option_items(reply_options),
                    "impact_summary": (
                        "这会修改本机软件状态，需要你明确确认后才会继续。"
                        if host_action == "uninstall"
                        else "这会修改本机软件或系统环境，需要你明确确认后才会继续。"
                    ),
                }
            response_plan = self._composer.response_plan_for_action_status(
                facts=facts,
                task_status={
                    "task_id": plan.task_id,
                    "status": plan.status,
                    "mode": "workflow",
                },
            )
            text = response_plan.plain_text or response_plan.summary or ""
            structured_payload = {
                **response_plan.structured_payload,
                "host_install_plan": plan.model_dump(mode="json"),
                "approval_binding": {
                    "approval_id": plan.approval_id,
                    "status": "required" if plan.approval_id else "manual_only",
                    "host_action": host_action,
                },
            }
            if pending_action is not None:
                reply_options = list(pending_action.get("reply_options") or [])
                structured_payload = {
                    **structured_payload,
                    "natural_interaction": {
                        "status": "pending_action",
                        "reason_codes": ["approval_required", "host_install_pending_action"],
                        "pending_actions": [pending_action],
                        "natural_reply_options": reply_options,
                        "reply_option_items": _reply_option_items(reply_options),
                        "pending_confirmation": {
                            "kind": "natural_pending_actions",
                            "session_id": session_id,
                            "actions": [pending_action],
                            "questions": reply_options,
                            "created_at": utc_now_iso(),
                        },
                        "clear_pending": False,
                        "session_grant": {},
                    },
                    "pending_actions": [pending_action],
                    "natural_reply_options": reply_options,
                    "reply_option_items": _reply_option_items(reply_options),
                }
            follow_up_options = (
                reply_options
                if pending_action is not None
                else list(response_plan.follow_up_options)
            )
            user_next_step = (
                follow_up_options[0]
                if pending_action is not None and follow_up_options
                else response_plan.user_next_step
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": structured_payload,
                    "follow_up_options": follow_up_options,
                    "user_next_step": user_next_step,
                }
            )
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": (
                        "host_software_uninstall_request"
                        if host_action == "uninstall"
                        else "host_software_install_request"
                    ),
                    "reason_codes": [
                        "phase52_host_uninstall_text_request"
                        if host_action == "uninstall"
                        else "phase52_host_install_text_request"
                    ],
                },
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent=(
                    "host_software_uninstall_request"
                    if host_action == "uninstall"
                    else "host_software_install_request"
                ),
                mode=TaskMode.WORKFLOW.value,
                response_plan=response_plan,
            ):
                yield event
            return

        clarification = (
            self._clarification_from_brain(turn, brain_decision)
            if brain_decision is not None
            else None
        )
        if clarification is not None and clarification.needs_clarification:
            await self._chat_repo.insert_clarification_decision(
                {
                    **clarification.as_payload(),
                    "created_at": clarification.created_at,
                    "updated_at": clarification.updated_at,
                }
            )
            await self._chat_repo.update_turn(
                turn_id,
                intent=brain_decision.intent.primary_intent if brain_decision else "clarification",
                mode="ask_clarification",
                privacy_level=privacy.privacy_level,
                updated_at=utc_now_iso(),
            )
            yield await emit(
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": brain_decision.intent.primary_intent
                    if brain_decision
                    else "clarification",
                    "decision_id": brain_decision.brain_decision_id
                    if brain_decision
                    else None,
                    "confidence": brain_decision.confidence if brain_decision else None,
                    "reason_codes": [clarification.reason],
                    "intent_decision": brain_decision.intent.model_dump(mode="json")
                    if brain_decision
                    else {},
                },
            )
            yield await emit(
                ChatEventType.MODE_SELECTED,
                {
                    "mode": "ask_clarification",
                    "needs_tool": False,
                    "decision_id": brain_decision.brain_decision_id
                    if brain_decision
                    else None,
                },
            )
            text = self._composer.compose_clarification(clarification.questions)
            response_plan = self._composer.response_plan_for_clarification(
                summary=text,
                decision=clarification.as_payload(),
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="clarification",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
                clarification_decision=clarification,
            ):
                yield event
            return

        intent = brain_decision.intent.primary_intent if brain_decision else "chat"
        mode = self._task_mode_from_brain(brain_decision.mode.mode if brain_decision else None)
        needs_tool = (
            brain_decision.intent.needs_tool
            or brain_decision.intent.needs_task
            or brain_decision.intent.needs_skill
            or brain_decision.intent.needs_mcp
            if brain_decision
            else False
        )

        intent_span = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.BRAIN_INTENT,
            name="emit brain intent decision",
            parent_span_id=root_span_id,
            input_data={"text": redact(user_text)},
        )
        await self._trace.end_span(
            intent_span,
            output_data={
                "intent": intent,
                "reason_codes": brain_decision.intent.reason_codes
                if brain_decision
                else [],
                "decision_id": brain_decision.brain_decision_id
                if brain_decision
                else None,
            },
        )
        yield await emit(
            ChatEventType.INTENT_DETECTED,
            {
                "intent": intent,
                "decision_id": brain_decision.brain_decision_id if brain_decision else None,
                "confidence": brain_decision.confidence if brain_decision else None,
                "reason_codes": brain_decision.intent.reason_codes if brain_decision else [],
                "intent_decision": brain_decision.intent.model_dump(mode="json")
                if brain_decision
                else {},
            },
        )

        mode_span = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.BRAIN_MODE_SELECT,
            name="select chat mode",
            parent_span_id=root_span_id,
            metadata={"intent": intent},
        )
        await self._trace.end_span(
            mode_span,
            output_data={
                "mode": mode.value,
                "mode_decision": brain_decision.mode.model_dump(mode="json")
                if brain_decision
                else {},
            },
        )
        yield await emit(
            ChatEventType.MODE_SELECTED,
            {
                "mode": mode.value,
                "needs_tool": needs_tool,
                "decision_id": brain_decision.brain_decision_id if brain_decision else None,
                "confidence": brain_decision.mode.confidence if brain_decision else None,
                "reason_codes": brain_decision.mode.reason_codes if brain_decision else [],
                "mode_decision": brain_decision.mode.model_dump(mode="json")
                if brain_decision
                else {},
            },
        )
        if self._events.token_for(turn_id).cancelled:
            async for event in self._cancel_turn_during_stream(turn, events, root_span_id):
                yield event
            return

        if (
            brain_decision is not None
            and brain_decision.mode.submode == "capability_boundary"
        ):
            text = self._composer.compose_tool_unavailable()
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability=intent,
                next_actions=["先生成方案", "连接或启用对应能力后重试"],
                safety_notice="对应 Skill/MCP/工具能力当前不可用；没有执行任何外部动作。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent=intent,
                mode=mode.value,
                response_plan=response_plan,
            ):
                yield event
            return

        if needs_tool or mode not in {
            TaskMode.DIRECT,
            TaskMode.DIRECT_WITH_MEMORY,
        }:
            if self._task_engine is not None and self._intent_creates_task(intent):
                from app.schemas.tasks import TaskCreateRequest

                task = await self._task_engine.create_task(
                    TaskCreateRequest(
                        conversation_id=turn["conversation_id"],
                        owner_member_id=turn["member_id"],
                        goal=user_text,
                        mode_hint=mode,
                        brain_decision_id=(
                            brain_decision.brain_decision_id if brain_decision else None
                        ),
                        planner_context={
                            "intent": brain_decision.intent.model_dump(mode="json")
                            if brain_decision
                            else {},
                            "mode_decision": brain_decision.mode.model_dump(mode="json")
                            if brain_decision
                            else {},
                            "context_decision": brain_decision.context.model_dump(mode="json")
                            if brain_decision
                            else {},
                            "privacy": self._privacy.planner_context(
                                privacy_level=privacy.privacy_level,
                                allow_cloud=privacy.allow_cloud,
                                sensitivity_hits=getattr(privacy, "sensitivity_hits", []),
                            ),
                        },
                        auto_start=True,
                        client_request_id=f"chat:{turn_id}:task",
                    ),
                    trace_id=trace_id,
                )
                yield await emit(
                    ChatEventType.TASK_CREATED,
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "status": task.status.value,
                    },
                )
                yield await emit(
                    ChatEventType.TASK_PLANNED,
                    {
                        "task_id": task.task_id,
                        "mode": task.mode.value,
                        "risk_level": task.risk_level.value,
                    },
                )
                recovery = await self._recover_task_in_turn(turn, events, task, root_span_id)
                task = recovery.task
                if task.status.value == "waiting_approval":
                    presentation = self._task_coordinator.present_task_status(task)
                    pending_action = None
                    if self._approval_service is not None and task.current_approval_id:
                        approval = await self._approval_service.get(task.current_approval_id)
                        pending_action = pending_action_from_approval(
                            approval,
                            session_id=session_id,
                            source_turn_id=turn_id,
                        )
                    yield await emit(
                        ChatEventType.APPROVAL_REQUIRED,
                        {
                            "task_id": task.task_id,
                            "approval_id": task.current_approval_id,
                            "summary": (
                                pending_action.get("user_summary")
                                if pending_action
                                else "任务需要确认后继续。"
                            ),
                        },
                    )
                    if pending_action is not None:
                        response_plan = response_plan_for_pending_action(
                            action=pending_action,
                            session_id=session_id,
                        )
                        response_plan = response_plan.model_copy(
                            update={
                                "task_status": presentation.task_status,
                                "structured_payload": {
                                    **response_plan.structured_payload,
                                    "task_status_semantics": presentation.task_status,
                                    "recovery": recovery.recovery_payload,
                                },
                            }
                        )
                        text = response_plan.plain_text or response_plan.summary or ""
                    else:
                        text = (
                            "当前有一步操作需要你确认后才会继续。"
                            "请回复：只允许这一次、拒绝，或修改目标。"
                        )
                        response_plan = self._composer.response_plan_for_status(
                            summary=text,
                            task_status={
                                "task_id": task.task_id,
                                "status": task.status.value,
                                "mode": task.mode.value,
                            },
                            approval_prompt={
                                "summary": "任务需要确认后继续。",
                            },
                            safety_notice=presentation.safety_notice,
                        )
                        response_plan = response_plan.model_copy(
                            update={
                                "structured_payload": {
                                    **response_plan.structured_payload,
                                    "task_status_semantics": presentation.task_status,
                                    "recovery": recovery.recovery_payload,
                                },
                            }
                        )
                else:
                    presentation = self._task_coordinator.present_task_status(task)
                    if presentation.event_type is not None:
                        yield await emit(
                            presentation.event_type,
                            presentation.event_payload,
                        )
                    text = f"{recovery.response_prefix}{presentation.text}"
                    response_plan = self._composer.response_plan_for_status(
                        summary=text,
                        task_status=presentation.task_status,
                        safety_notice=presentation.safety_notice,
                        tool_notice=presentation.tool_notice,
                    )
                    if recovery.recovery_payload.get("attempt_count"):
                        turn_recovery = self._turn_recovery
                        if turn_recovery is not None:
                            response_plan = turn_recovery.response_plan_for_task(
                                summary=text,
                                task_status=presentation.task_status,
                                recovery_payload=recovery.recovery_payload,
                                safety_notice=presentation.safety_notice,
                                tool_notice=presentation.tool_notice,
                            )
                    response_plan = response_plan.model_copy(
                        update={
                            "structured_payload": {
                                **response_plan.structured_payload,
                                "task_status_semantics": presentation.task_status,
                                "recovery": recovery.recovery_payload,
                            },
                        }
                    )
                    if recovery.recovery_payload.get("status") == "exhausted":
                        async for event in self._fail_turn(
                            turn,
                            events,
                            ErrorCode.TASK_STEP_FAILED,
                            text,
                            root_span_id,
                            persist_assistant=True,
                            response_plan=response_plan,
                        ):
                            yield event
                        return
                async for event in self._complete_without_model(
                    turn,
                    events,
                    text,
                    root_span_id,
                    intent=intent,
                    mode=mode.value,
                    response_plan=response_plan,
                ):
                    yield event
                return
            text = self._composer.compose_tool_unavailable()
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability=intent,
                next_actions=["创建任务并执行", "补充范围后重试", "先生成执行计划"],
                safety_notice="未找到可执行工具路径；没有执行任何外部动作。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent=intent,
                mode=mode.value,
                response_plan=response_plan,
            ):
                yield event
            return

        available_brains = await self._brains.list_routable_brains()
        routing_config = await self._model_routing.get_config()
        route_request = BrainRouteRequest(
            text=user_text,
            member_id=turn["member_id"],
            conversation_id=turn["conversation_id"],
            default_brain_id=context.member.default_brain_id,
            privacy_level=privacy.privacy_level,
            estimated_input_tokens=estimate_messages_tokens(
                self._model_messages(
                    context,
                    privacy.redacted_text,
                    turn_id=turn["turn_id"],
                )
            ),
            available_brains=available_brains,
            model_routing_config=routing_config,
        )
        route_selection = self._model_router.select_route_result(route_request)
        model_route = route_selection.route
        if model_route is None:
            code = self._route_error_code(available_brains, privacy.privacy_level)
            reason_codes = brain_decision.intent.reason_codes if brain_decision else []
            if (
                code == ErrorCode.MODEL_NOT_CONFIGURED
                and "phase51_advice_strategy_direct" in reason_codes
            ):
                text = _strategy_advice_fallback_text(user_text)
                response_plan = self._composer.response_plan_for_status(
                    summary=text,
                    task_status={"status": "not_created", "reason": "local_strategy_fallback"},
                    safety_notice="没有可用模型时只给确定性建议；没有创建任务或调用工具。",
                )
                response_plan = response_plan.model_copy(
                    update={
                        "structured_payload": {
                            **response_plan.structured_payload,
                            "route_semantics": {
                                "route": "direct_strategy_fallback",
                                "model_called": False,
                                "task_created": False,
                                "tool_created": False,
                                "model_not_required_reason": "phase51_strategy_no_model_fallback",
                            },
                        },
                    }
                )
                async for event in self._complete_without_model(
                    turn,
                    events,
                    text,
                    root_span_id,
                    intent=intent,
                    mode=mode.value,
                    response_plan=response_plan,
                ):
                    yield event
                return
            if intent == "boundary_question" and code == ErrorCode.MODEL_NOT_CONFIGURED:
                boundary_text = (
                    "我不是隐藏真人账号，也不会绕过系统替你登录或直接操作；"
                    "涉及登录、工具、文件、浏览器和外部动作时，我会先走安全流程，"
                    "该确认的地方停住等你点头。"
                )
                response_plan = self._composer.response_plan_for_status(
                    summary=boundary_text,
                    safety_notice=boundary_text,
                )
                async for event in self._complete_without_model(
                    turn,
                    events,
                    boundary_text,
                    root_span_id,
                    intent=intent,
                    mode=mode.value,
                    response_plan=response_plan,
                ):
                    yield event
                return
            if code == ErrorCode.MODEL_ROUTE_BLOCKED_BY_PRIVACY:
                await self._audit.write_event(
                    actor_type="system",
                    action="model_route.blocked_by_privacy",
                    object_type="chat_turn",
                    object_id=turn_id,
                    summary="高隐私输入阻止云端路由",
                    risk_level=RiskLevel.R2,
                    payload={"privacy_level": privacy.privacy_level},
                    trace_id=trace_id,
                )
            await self._record_stage_recovery_attempt(
                turn=turn,
                stage="model",
                failure_type="model_not_configured"
                if code == ErrorCode.MODEL_NOT_CONFIGURED
                else "model_route_failed",
                root_cause=code.value,
                recovery_action="ask_user_for_missing_input"
                if code == ErrorCode.MODEL_NOT_CONFIGURED
                else "stop_unrecoverable",
                status="failed",
                diagnostic_payload={"error_code": code.value},
            )
            async for event in self._fail_turn(
                turn,
                events,
                code,
                self._composer.compose_failure(code, "没有可用模型路由"),
                root_span_id,
                persist_assistant=True,
            ):
                yield event
            return

        route_span = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MODEL_ROUTE,
            name="select model route",
            parent_span_id=root_span_id,
            metadata=model_route.model_dump(mode="json"),
        )
        await self._trace.end_span(route_span)
        await self._chat_repo.update_turn(
            turn_id,
            intent=intent,
            mode=mode.value,
            privacy_level=privacy.privacy_level,
            route=model_route.model_dump(mode="json"),
            updated_at=utc_now_iso(),
        )
        turn["privacy_level"] = privacy.privacy_level
        turn["route"] = model_route.model_dump(mode="json")
        yield await emit(
            ChatEventType.ROUTE_SELECTED,
            model_route.model_dump(mode="json"),
        )

        async for event in self._run_model_path(
            turn,
            events,
            context,
            privacy.redacted_text,
            model_route.primary_brain_id,
            model_route.fallback_brain_ids,
            model_route.model_params.model_dump(mode="json"),
            root_span_id,
            intent=intent,
            mode=mode.value,
        ):
            yield event

    async def _chat_payloads_for_turn(self, turn: dict[str, Any]) -> dict[str, Any]:
        envelope = await self._chat_repo.get_message_envelope_by_turn(turn["turn_id"])
        queue_item = await self._chat_repo.get_queue_item_by_turn(turn["turn_id"])
        payloads: dict[str, Any] = {}
        if envelope is not None:
            payloads["content"] = _content_payload(envelope)
        if queue_item is not None:
            payloads["queue"] = _queue_payload(queue_item)
        return payloads

    async def _decorate_chat_payloads(
        self,
        turn: dict[str, Any],
        response_plan: ResponsePlan,
    ) -> ResponsePlan:
        payloads = await self._chat_payloads_for_turn(turn)
        if not payloads:
            return response_plan
        return response_plan.model_copy(
            update={
                "structured_payload": {
                    **response_plan.structured_payload,
                    **payloads,
                }
            }
        )

    async def _record_stage_recovery_attempt(
        self,
        *,
        turn: dict[str, Any],
        stage: str,
        failure_type: str,
        root_cause: str,
        recovery_action: str,
        status: str,
        diagnostic_payload: dict[str, Any] | None = None,
        action_result: dict[str, Any] | None = None,
    ) -> None:
        attempts = await self._chat_repo.list_recovery_attempts(turn["turn_id"])
        now = utc_now_iso()
        await self._chat_repo.insert_recovery_attempt(
            {
                "recovery_attempt_id": new_id("trra"),
                "turn_id": turn["turn_id"],
                "task_id": None,
                "attempt_index": len(attempts) + 1,
                "failure_type": failure_type,
                "root_cause": str(redact(root_cause)),
                "recovery_action": recovery_action,
                "status": status,
                "recovery_stage": stage,
                "error_signature": _error_signature(stage, failure_type, root_cause),
                "diagnostic_payload": redact(diagnostic_payload or {}),
                "action_result": redact(action_result or {}),
                "trace_id": turn["trace_id"],
                "started_at": now,
                "completed_at": now,
            }
        )
        if status == "recovered":
            try:
                await self._memory.record_recovery_lesson_candidate(
                    turn_id=turn["turn_id"],
                    stage=stage,
                    failure_type=failure_type,
                    recovery_action=recovery_action,
                    trace_id=turn["trace_id"],
                )
            except Exception:
                return

    async def _maybe_record_context_compaction(
        self,
        turn: dict[str, Any],
        context: ContextPacket,
        context_filter_summary: dict[str, Any],
        root_span_id: str | None,
        emit: Any,
    ) -> AsyncIterator[ChatEvent]:
        messages = [
            {
                "role": str(item.get("role") or "user"),
                "content": str(
                    item.get("model_safe_content_text")
                    or item.get("content_text")
                    or ""
                ),
            }
            for item in context.conversation.last_messages
        ]
        if context.conversation.recent_summary:
            messages.append({"role": "system", "content": context.conversation.recent_summary})
        token_before = estimate_messages_tokens(messages)
        if token_before < 2400 and len(context.conversation.last_messages) <= 12:
            return
        compaction_id = new_id("ctxcmp")
        yield await emit(
            ChatEventType.CONTEXT_COMPACTION_STARTED,
            {
                "compaction_id": compaction_id,
                "reason": "context_budget_guard",
                "token_estimate_before": token_before,
            },
        )
        span_id = await self._trace.start_span(
            turn["trace_id"],
            span_type=TraceSpanType.CONTEXT_COMPACTION,
            name="compact chat context evidence",
            parent_span_id=root_span_id,
            input_data={
                "token_estimate_before": token_before,
                "message_count": len(context.conversation.last_messages),
            },
        )
        summary = _context_compaction_summary(context)
        token_after = estimate_messages_tokens([{"role": "system", "content": summary}])
        await self._chat_repo.insert_context_compaction(
            {
                "compaction_id": compaction_id,
                "turn_id": turn["turn_id"],
                "conversation_id": turn["conversation_id"],
                "reason": "context_budget_guard",
                "status": "completed",
                "token_estimate_before": token_before,
                "token_estimate_after": token_after,
                "summary": summary,
                "payload": {
                    "context_redaction": context_filter_summary,
                    "message_count": len(context.conversation.last_messages),
                },
                "trace_id": turn["trace_id"],
                "created_at": utc_now_iso(),
                "completed_at": utc_now_iso(),
            }
        )
        await self._record_stage_recovery_attempt(
            turn=turn,
            stage="context",
            failure_type="context_over_budget",
            root_cause="context_budget_guard",
            recovery_action="rebuild_minimal_context",
            status="recovered",
            diagnostic_payload={"token_estimate_before": token_before},
            action_result={"token_estimate_after": token_after, "compaction_id": compaction_id},
        )
        await self._trace.end_span(
            span_id,
            output_data={
                "compaction_id": compaction_id,
                "token_estimate_after": token_after,
            },
        )
        yield await emit(
            ChatEventType.CONTEXT_COMPACTION_COMPLETED,
            {
                "compaction_id": compaction_id,
                "status": "completed",
                "token_estimate_after": token_after,
            },
        )

    async def _run_model_path(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        context: ContextPacket,
        user_text: str,
        primary_brain_id: str,
        fallback_brain_ids: list[str],
        model_params: dict[str, Any],
        root_span_id: str | None,
        *,
        intent: str,
        mode: str,
        response_plan: ResponsePlan | None = None,
        clarification_decision: ClarificationDecision | None = None,
    ) -> AsyncIterator[ChatEvent]:
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        token = self._events.token_for(turn_id)
        candidate_ids = [primary_brain_id, *fallback_brain_ids]
        last_error: ModelAdapterError | None = None
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
                    failure_type=_model_failure_type(last_error),
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
            except ModelAdapterError as exc:
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
                        failure_type=_model_failure_type(exc),
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

    async def _call_model(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        context: ContextPacket,
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
        channel_profile = _channel_profile_for_turn(turn)
        prompt_assembly = self._model_coordinator.model_assembly(
            context,
            user_text,
            channel_profile=channel_profile,
            delivery_mode="final",
            turn_id=turn_id,
        )
        messages = prompt_assembly.messages
        prompt_metadata = prompt_assembly.metadata
        continuation_decision = self._continuation.decide(
            turn=turn,
            user_text=user_text,
            context=context,
            intent=intent,
            mode=mode,
        )
        buffer_visible_response = continuation_decision.enabled
        model_span = await self._trace.start_span(
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
                **_prompt_payload_from_metadata(prompt_metadata),
            },
        )
        if not brain["is_local"]:
            await self._audit.write_event(
                actor_type="system",
                action="model_call.cloud_used",
                object_type="brain",
                object_id=brain["brain_id"],
                summary="聊天 turn 使用了云端模型",
                risk_level=RiskLevel.R2,
                payload={"brain_id": brain["brain_id"], "turn_id": turn_id},
                trace_id=trace_id,
            )
        client = OpenAICompatibleClient(
            str(brain["endpoint"]),
            self._secrets.get_secret(brain.get("api_key_ref")),
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
        delta_filter = self._composer.begin_delta_stream()
        visible_filter = self._response_coordinator.begin_visible_stream()
        model_call_started = time.perf_counter()
        try:
            async for model_event in client.stream_chat(request, cancel_token):
                if model_event.event == "started":
                    yield await self._emit_and_record(
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
                            yield await self._emit_and_record(
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
                            yield await self._emit_and_record(
                                turn_id,
                                trace_id,
                                events,
                                ChatEventType.RESPONSE_DELTA,
                                {"text": tail_text, "response_filter": visible_filter.summary()},
                            )
                    yield await self._emit_and_record(
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
            await self._trace.end_span(
                model_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": exc.code.value, "message": exc.message},
                error_code=exc.code.value,
            )
            raise
        if cancel_token.cancelled:
            await self._trace.end_span(
                model_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.TURN_CANCELLED.value},
                error_code=ErrorCode.TURN_CANCELLED.value,
            )
            async for event in self._cancel_turn_during_stream(turn, events, root_span_id):
                yield event
            return
        response_filter = visible_filter.summary()
        assistant_text = "".join(output_parts).strip()
        if not assistant_text:
            raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型没有返回可用文本")
        await self._trace.end_span(
            model_span,
            output_data={
                "finish_reason": finish_reason,
                "usage": usage,
                "response_filter": response_filter,
                "continuation_enabled": continuation_decision.enabled,
            },
        )
        response_plan = None
        if continuation_decision.enabled:
            initial_latency_ms = int((time.perf_counter() - model_call_started) * 1000)
            continuation_started = time.perf_counter()
            evaluation = self._continuation.evaluate(
                text=assistant_text,
                user_text=user_text,
                decision=continuation_decision,
            )
            iterations = 0
            used_revision = False
            budget_exhausted = False
            usage = {"initial": usage, "continuation_iterations": 0}
            revision_latency_ms: int | None = None
            if evaluation.should_revise and continuation_decision.max_iterations > 0:
                try:
                    revision_started = time.perf_counter()
                    revision = await self._run_continuation_revision(
                        turn=turn,
                        events=events,
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
                    revised_text = str(revision["text"] or "").strip()
                    revised_evaluation = self._continuation.evaluate(
                        text=revised_text,
                        user_text=user_text,
                        decision=continuation_decision,
                    )
                    if revised_text and not (
                        set(revised_evaluation.tags)
                        & {
                            "missing_reply",
                            "internal_jargon",
                            "secret_leak",
                            "false_done",
                            "strict_format_polluted",
                        }
                    ):
                        assistant_text = revised_text
                        finish_reason = str(revision.get("finish_reason") or finish_reason)
                        used_revision = True
                except ModelAdapterError as exc:
                    if exc.code == ErrorCode.TURN_CANCELLED:
                        raise
                    budget_exhausted = exc.code == ErrorCode.MODEL_TIMEOUT
                    usage["continuation_error"] = exc.code.value
            compose_result = await self._composer.compose(
                ComposeRequest(
                    user_text=user_text,
                    result_summary=assistant_text,
                    scenario="direct",
                    persona=(
                        context.persona.model_dump(mode="json")
                        if getattr(context, "persona", None) is not None
                        and hasattr(context.persona, "model_dump")
                        else {}
                    ),
                    heart=(
                        context.heart.model_dump(mode="json")
                        if getattr(context, "heart", None) is not None
                        and hasattr(context.heart, "model_dump")
                        else {}
                    ),
                    route_profile=str((turn.get("experience") or {}).get("route_profile") or ""),
                    channel_profile=channel_profile,
                    prompt_mode=str(prompt_metadata.get("prompt_mode") or "full"),
                    prompt_snapshot_id=str(prompt_metadata.get("prompt_snapshot_id") or ""),
                    prompt_assembly_version=str(
                        prompt_metadata.get("prompt_assembly_version") or ""
                    )
                    or None,
                    stable_prompt_hash=str(prompt_metadata.get("stable_prompt_hash") or "")
                    or None,
                    dynamic_context_hash=str(prompt_metadata.get("dynamic_context_hash") or "")
                    or None,
                    trusted_context_hash=str(prompt_metadata.get("trusted_context_hash") or "")
                    or None,
                    untrusted_context_hash=str(prompt_metadata.get("untrusted_context_hash") or "")
                    or None,
                    history_context_hash=str(prompt_metadata.get("history_context_hash") or "")
                    or None,
                    current_message_hash=str(prompt_metadata.get("current_message_hash") or "")
                    or None,
                    prompt_section_ids=[
                        str(item) for item in prompt_metadata.get("prompt_section_ids") or []
                    ],
                    prompt_sections=[
                        dict(item)
                        for item in prompt_metadata.get("prompt_sections") or []
                        if isinstance(item, dict)
                    ],
                )
            )
            response_plan = compose_result.response_plan
            final_text_for_quality = self._style_visible_text(
                turn,
                assistant_text,
                response_plan=response_plan,
            )
            final_text_for_quality, final_filter = self._response_coordinator.filter_text(
                final_text_for_quality
            )
            assistant_text = final_text_for_quality
            response_filter = final_filter
            evaluation = self._continuation.evaluate(
                text=assistant_text,
                user_text=user_text,
                decision=continuation_decision,
                elapsed_ms=int((time.perf_counter() - continuation_started) * 1000),
                response_quality_guard=response_plan.structured_payload.get(
                    "response_quality_guard"
                ),
            )
            used_safe_fallback = False
            if evaluation.verdict == "block":
                used_safe_fallback = True
                fallback_text = self._continuation.safe_fallback_text(
                    user_text=user_text,
                    evaluation=evaluation,
                )
                fallback_scenario = (
                    "tool_boundary"
                    if set(evaluation.tags)
                    & {"internal_jargon", "secret_leak", "false_done"}
                    else "direct"
                )
                fallback_result = await self._composer.compose(
                    ComposeRequest(
                        user_text=user_text,
                        result_summary=fallback_text,
                        scenario=fallback_scenario,
                        persona=(
                            context.persona.model_dump(mode="json")
                            if getattr(context, "persona", None) is not None
                            and hasattr(context.persona, "model_dump")
                            else {}
                        ),
                        heart=(
                            context.heart.model_dump(mode="json")
                            if getattr(context, "heart", None) is not None
                            and hasattr(context.heart, "model_dump")
                            else {}
                        ),
                        route_profile=str((turn.get("experience") or {}).get("route_profile") or ""),
                        channel_profile=channel_profile,
                        prompt_mode=str(prompt_metadata.get("prompt_mode") or "full"),
                        prompt_snapshot_id=str(prompt_metadata.get("prompt_snapshot_id") or ""),
                        prompt_assembly_version=str(
                            prompt_metadata.get("prompt_assembly_version") or ""
                        )
                        or None,
                        stable_prompt_hash=str(prompt_metadata.get("stable_prompt_hash") or "")
                        or None,
                        dynamic_context_hash=str(prompt_metadata.get("dynamic_context_hash") or "")
                        or None,
                        trusted_context_hash=str(prompt_metadata.get("trusted_context_hash") or "")
                        or None,
                        untrusted_context_hash=str(prompt_metadata.get("untrusted_context_hash") or "")
                        or None,
                        history_context_hash=str(prompt_metadata.get("history_context_hash") or "")
                        or None,
                        current_message_hash=str(prompt_metadata.get("current_message_hash") or "")
                        or None,
                        prompt_section_ids=[
                            str(item) for item in prompt_metadata.get("prompt_section_ids") or []
                        ],
                        prompt_sections=[
                            dict(item)
                            for item in prompt_metadata.get("prompt_sections") or []
                            if isinstance(item, dict)
                        ],
                    )
                )
                response_plan = fallback_result.response_plan
                assistant_text = self._style_visible_text(
                    turn,
                    fallback_result.text,
                    response_plan=response_plan,
                )
                assistant_text, final_filter = self._response_coordinator.filter_text(
                    assistant_text
                )
                response_filter = final_filter
                evaluation = self._continuation.evaluate(
                    text=assistant_text,
                    user_text=user_text,
                    decision=continuation_decision,
                    elapsed_ms=int((time.perf_counter() - continuation_started) * 1000),
                    response_quality_guard=response_plan.structured_payload.get(
                        "response_quality_guard"
                    ),
                )
            continuation_payload = self._continuation.payload(
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
            yield await self._emit_and_record(
                turn_id,
                trace_id,
                events,
                ChatEventType.RESPONSE_DELTA,
                {
                    "text": assistant_text,
                    "response_filter": final_filter,
                    "continuation": continuation_payload,
                },
            )
        async for event in self._complete_model_turn(
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

    async def _run_continuation_revision(
        self,
        *,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        messages: list[dict[str, str]],
        user_text: str,
        draft_text: str,
        evaluation: ContinuationEvaluation,
        brain: dict[str, Any],
        model_params: dict[str, Any],
        root_span_id: str | None,
    ) -> dict[str, Any]:
        trace_id = turn["trace_id"]
        turn_id = turn["turn_id"]
        revision_messages = self._continuation.revision_messages(
            messages=messages,
            user_text=user_text,
            draft_text=draft_text,
            evaluation=evaluation,
        )
        revision_span = await self._trace.start_span(
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
            input_data={
                "message_count": len(revision_messages),
                "input_token_estimate": estimate_messages_tokens(revision_messages),
            },
        )
        client = OpenAICompatibleClient(
            str(brain["endpoint"]),
            self._secrets.get_secret(brain.get("api_key_ref")),
        )
        request = ModelChatRequest(
            model=str(brain["model_name"]),
            messages=revision_messages,
            temperature=min(float(model_params.get("temperature") or 0.3), 0.25),
            max_output_tokens=int(model_params.get("max_output_tokens") or 1024),
            top_p=float(model_params.get("top_p") or 0.9),
            timeout_seconds=min(
                int(model_params.get("timeout_seconds") or 180),
                20,
            ),
            stream=True,
            trace_id=trace_id,
            turn_id=turn_id,
            route_id=f"route_{brain['brain_id']}:continuation:1",
            privacy_level=turn.get("privacy_level") or "medium",
            first_token_timeout_seconds=20,
            retry_count=0,
        )
        token = self._events.token_for(turn_id)
        output_parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        delta_filter = self._composer.begin_delta_stream()
        visible_filter = self._response_coordinator.begin_visible_stream()
        try:
            async for model_event in client.stream_chat(request, token):
                if model_event.event == "started":
                    await self._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_STARTED,
                        {
                            "brain_id": brain["brain_id"],
                            "continuation_iteration": 1,
                        },
                    )
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
                    await self._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_COMPLETED,
                        {
                            "finish_reason": finish_reason,
                            "usage": usage,
                            "response_filter": visible_filter.summary(),
                            "continuation_iteration": 1,
                        },
                    )
                    break
                elif model_event.event == "cancelled":
                    token.cancel()
                    break
        except ModelAdapterError as exc:
            await self._trace.end_span(
                revision_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": exc.code.value, "message": exc.message},
                error_code=exc.code.value,
            )
            raise
        if token.cancelled:
            await self._trace.end_span(
                revision_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.TURN_CANCELLED.value},
                error_code=ErrorCode.TURN_CANCELLED.value,
            )
            raise ModelAdapterError(ErrorCode.TURN_CANCELLED, "生成已取消")
        text = "".join(output_parts).strip()
        if not text:
            await self._trace.end_span(
                revision_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.MODEL_PROTOCOL_ERROR.value},
                error_code=ErrorCode.MODEL_PROTOCOL_ERROR.value,
            )
            raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "续跑修订没有返回可用文本")
        await self._trace.end_span(
            revision_span,
            output_data={
                "finish_reason": finish_reason,
                "usage": usage,
                "response_filter": visible_filter.summary(),
                "text_chars": len(text),
            },
        )
        return {"text": text, "usage": usage, "finish_reason": finish_reason}

    async def _complete_without_model(
        self,
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
        text = self._style_visible_text(turn, text, response_plan=response_plan)
        text, response_filter = self._response_coordinator.filter_text(text)
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.RESPONSE_DELTA,
            {"text": text, "response_filter": response_filter},
        )
        async for event in self._complete_model_turn(
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

    async def _handle_host_filesystem_list(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        if self._tool_runtime is None:
            text = "当前本机文件列表工具不可用；我没有查看目录，也不会假装已经看过。"
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="host.fs.list",
                next_actions=["检查工具注册", "稍后重试"],
                safety_notice="没有读取文件内容，也没有执行文件修改。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="system_filesystem_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        location = str(metadata.get("location") or "home")
        limit = metadata.get("limit") or 50
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": "system_filesystem_read",
                "reason_codes": ["host_filesystem_list_readonly"],
            },
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {"mode": TaskMode.DIRECT.value, "needs_tool": True},
        )
        try:
            response = await self._tool_runtime.execute(
                ToolExecuteRequest(
                    member_id=turn["member_id"],
                    tool_name="host.fs.list",
                    args={"location": location, "limit": limit},
                    idempotency_key=f"chat:{turn['turn_id']}:host.fs.list:{location}",
                ),
                trace_id=trace_id,
            )
        except AppError as exc:
            text = _host_filesystem_list_error_reply(location, exc)
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="host.fs.list",
                next_actions=["换成桌面、下载、文档或主目录", "确认目录授权后重试"],
                safety_notice="请求被目录边界策略拦截；没有读取文件内容或修改文件。",
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "host_filesystem_list": {
                            "location": location,
                            "status": "blocked",
                            "error_code": exc.code,
                            "details": exc.details,
                        },
                    },
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="system_filesystem_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        result = response.result
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TOOL_COMPLETED,
            {
                "tool_call_id": response.tool_call.tool_call_id,
                "tool_name": "host.fs.list",
                "risk_level": response.tool_call.risk_level.value
                if hasattr(response.tool_call.risk_level, "value")
                else str(response.tool_call.risk_level),
            },
        )
        text = _host_filesystem_list_reply(result)
        response_plan = self._composer.response_plan_for_status(
            summary=text,
            task_status={"status": "not_created", "reason": "readonly_host_filesystem_list"},
            tool_notice="只列出目录项元数据，没有读取文件内容、递归扫描或修改文件。",
        )
        response_plan = response_plan.model_copy(
            update={
                "structured_payload": {
                    **response_plan.structured_payload,
                    "host_filesystem_list": result,
                    "route_semantics": {
                        "route": "host_filesystem_list",
                        "model_called": False,
                        "task_created": False,
                        "tool_created": True,
                        "tool_name": "host.fs.list",
                        "tool_call_id": response.tool_call.tool_call_id,
                        "reason_code": "host_filesystem_list_readonly",
                    },
                },
            }
        )
        async for event in self._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent="system_filesystem_read",
            mode=TaskMode.DIRECT.value,
            response_plan=response_plan,
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
        url = str(metadata.get("url") or "").strip()
        if self._tool_runtime is None:
            text = "当前浏览器只读工具不可用；我没有打开网页，也不会假装已经看过。"
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="browser.snapshot",
                next_actions=["检查浏览器工具注册", "稍后重试"],
                safety_notice="没有访问外部链接，也没有执行网页交互。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="browser_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        if not url:
            text = "我没有识别到可查看的链接；请把完整的 http 或 https 链接发给我。"
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="browser.snapshot",
                next_actions=["补充完整链接后重试"],
                safety_notice="没有访问外部链接，也没有执行网页交互。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="browser_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": "browser_read",
                "reason_codes": ["browser_read_page_readonly"],
            },
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {"mode": TaskMode.DIRECT.value, "needs_tool": True},
        )
        try:
            response = await self._tool_runtime.execute(
                ToolExecuteRequest(
                    member_id=turn["member_id"],
                    tool_name="browser.snapshot",
                    args={
                        "url": url,
                        "intent": "readonly_page_summary",
                        "provider_mode": "auto",
                    },
                    idempotency_key=f"chat:{turn['turn_id']}:browser.snapshot",
                ),
                trace_id=trace_id,
            )
        except AppError as exc:
            text = _browser_read_page_error_reply(exc)
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="browser.snapshot",
                next_actions=["确认链接可公开访问", "换一个 http/https 链接后重试"],
                safety_notice="这次没有执行下载、登录、提交、点击或截图。",
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "browser_read_page": {
                            "status": "blocked",
                            "error_code": exc.code,
                            "details": exc.details,
                        },
                    },
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="browser_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        result = response.result
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TOOL_COMPLETED,
            {
                "tool_call_id": response.tool_call.tool_call_id,
                "tool_name": "browser.snapshot",
                "risk_level": response.tool_call.risk_level.value
                if hasattr(response.tool_call.risk_level, "value")
                else str(response.tool_call.risk_level),
            },
        )
        text = _browser_read_page_reply(result)
        response_plan = self._composer.response_plan_for_status(
            summary=text,
            task_status={"status": "not_created", "reason": "readonly_browser_page_read"},
            tool_notice="只读取网页快照文本，没有下载文件、登录、提交、点击或截图。",
        )
        response_plan = response_plan.model_copy(
            update={
                "structured_payload": {
                    **response_plan.structured_payload,
                    "browser_read_page": _browser_read_page_payload(result),
                    "route_semantics": {
                        "route": "browser_read_page",
                        "model_called": False,
                        "task_created": False,
                        "tool_created": True,
                        "tool_name": "browser.snapshot",
                        "tool_call_id": response.tool_call.tool_call_id,
                        "reason_code": "browser_read_page_readonly",
                    },
                },
            }
        )
        async for event in self._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent="browser_read",
            mode=TaskMode.DIRECT.value,
            response_plan=response_plan,
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
        if self._tool_runtime is None or self._task_engine is None:
            text = "当前终端工具不可用；我没有执行系统命令，也不会假装已经执行。"
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="terminal.run",
                next_actions=["检查工具注册", "稍后重试"],
                safety_notice="没有执行任何系统命令。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="terminal_readonly_command",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        command = str(metadata.get("command") or "").strip()
        if not command:
            text = "我没拿到可执行的系统命令，所以没有运行。"
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="terminal.run",
                next_actions=["重新说明命令", "直接贴出命令字符串"],
                safety_notice="没有执行任何系统命令。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="terminal_readonly_command",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": "terminal_readonly_command",
                "reason_codes": ["terminal_readonly_command"],
            },
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {"mode": TaskMode.WORKFLOW.value, "needs_tool": True},
        )
        from app.schemas.tasks import TaskCreateRequest, ToolExecuteRequest

        task = await self._task_engine.create_task(
            TaskCreateRequest(
                conversation_id=turn["conversation_id"],
                owner_member_id=turn["member_id"],
                goal=command,
                mode_hint=TaskMode.WORKFLOW,
                planner_context={
                    "intent": {
                        "primary_intent": "terminal_readonly_command",
                        "reason_codes": ["terminal_readonly_command"],
                    },
                    "route": "terminal_readonly_command",
                    "command": command,
                },
                auto_start=False,
                client_request_id=f"chat:{turn['turn_id']}:terminal-readonly",
            ),
            trace_id=trace_id,
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TASK_CREATED,
            {"task_id": task.task_id, "title": task.title, "status": task.status.value},
        )
        try:
            response = await self._tool_runtime.execute(
                ToolExecuteRequest(
                    task_id=task.task_id,
                    member_id=turn["member_id"],
                    tool_name="terminal.run",
                    args={"command": command, "chat_readonly_command": True},
                    idempotency_key=f"chat:{turn['turn_id']}:terminal.run:{command}",
                ),
                trace_id=trace_id,
            )
        except AppError as exc:
            text = _terminal_command_error_reply(command, exc)
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="terminal.run",
                next_actions=["换成只读命令", "先确认命令是否安全"],
                safety_notice="命令被终端安全策略拦截；没有执行危险操作。",
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "terminal_route": {
                            "command": command,
                            "status": "blocked",
                            "error_code": exc.code,
                            "details": exc.details,
                        },
                    },
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="terminal_readonly_command",
                mode=TaskMode.WORKFLOW.value,
                response_plan=response_plan,
            ):
                yield event
            return
        result = response.result
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TOOL_COMPLETED,
            {
                "tool_call_id": response.tool_call.tool_call_id,
                "tool_name": "terminal.run",
                "risk_level": response.tool_call.risk_level.value
                if hasattr(response.tool_call.risk_level, "value")
                else str(response.tool_call.risk_level),
            },
        )
        text = _terminal_command_reply(command, result)
        response_plan = self._composer.response_plan_for_status(
            summary=text,
            task_status={"status": "completed", "reason": "terminal_readonly_command"},
            tool_notice="命令在受控终端沙箱中执行，未使用自定义 cwd，输出已脱敏。",
        )
        response_plan = response_plan.model_copy(
            update={
                "structured_payload": {
                    **response_plan.structured_payload,
                    "terminal_route": {
                        "command": command,
                        "status": "completed",
                        "tool_call_id": response.tool_call.tool_call_id,
                        "task_id": task.task_id,
                        "output_preview": str(result.get("output_preview") or "")[:1000],
                        "sandbox_profile": result.get("sandbox_profile"),
                        "backend_status": result.get("backend_status"),
                        "fallback_chain": result.get("fallback_chain"),
                        "degraded_reason": result.get("degraded_reason"),
                        "resource_usage": result.get("resource_usage"),
                        "cleanup": result.get("cleanup"),
                        "dlp_report_id": result.get("dlp_report_id"),
                    },
                    "route_semantics": {
                        "route": "terminal_readonly_command",
                        "model_called": False,
                        "task_created": True,
                        "tool_created": True,
                        "tool_name": "terminal.run",
                        "tool_call_id": response.tool_call.tool_call_id,
                        "reason_code": "terminal_readonly_command",
                    },
                },
            }
        )
        async for event in self._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent="terminal_readonly_command",
            mode=TaskMode.WORKFLOW.value,
            response_plan=response_plan,
        ):
            yield event

    async def _handle_office_chat_request(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        user_text: str,
        office_request: OfficeChatRequest,
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        turn_id = turn["turn_id"]
        if self._task_engine is None:
            text = "我识别到这是 Office 文件任务，但当前任务引擎不可用；没有生成文件。"
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="office_document",
                next_actions=["检查任务引擎", "稍后重试"],
                safety_notice="没有执行任何文件写入动作。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="office_document_request",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        skill_id = await self._enabled_office_skill_id(office_request)
        tool_name = preferred_office_tool_name(office_request)
        missing_reason = None
        if skill_id is None:
            missing_reason = "missing_enabled_skill"
        elif not await self._office_skill_has_grant(skill_id, tool_name, str(turn["member_id"])):
            missing_reason = "missing_skill_grant"
        if missing_reason is not None:
            text = self._office_missing_capability_text(office_request, missing_reason)
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability=f"office.{office_request.document_type}.{office_request.operation}",
                next_actions=self._office_next_actions(office_request, missing_reason),
                safety_notice="Office 文件这步还没真正走完，我先不把结果说满。",
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "office_route": {
                            "document_type": office_request.document_type,
                            "operation": office_request.operation,
                            "missing_reason": missing_reason,
                            "tool_name": tool_name,
                        },
                    },
                }
            )
            yield await self._emit_and_record(
                turn_id,
                turn["trace_id"],
                events,
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": "office_document_request",
                    "reason_codes": ["office_document_hard_route", missing_reason],
                },
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="office_document_request",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return

        from app.schemas.tasks import TaskCreateRequest

        source_artifact_id = await self._latest_office_artifact_id(
            str(turn["conversation_id"]),
            office_request.document_type,
        )
        task = await self._task_engine.create_task(
            TaskCreateRequest(
                conversation_id=turn["conversation_id"],
                owner_member_id=turn["member_id"],
                goal=user_text,
                mode_hint=TaskMode.WORKFLOW,
                constraints={
                    "skill_id": skill_id,
                    "skill_input": office_skill_input(
                        office_request,
                        source_artifact_id=source_artifact_id,
                    ),
                    "office_chat_request": office_request.__dict__,
                },
                planner_context={
                    "intent": {
                        "primary_intent": "office_document_request",
                        "reason_codes": ["office_document_hard_route"],
                    },
                    "route": "office_document_hard_route",
                },
                auto_start=True,
                client_request_id=f"chat:{turn_id}:office-task",
            ),
            trace_id=trace_id,
        )
        yield await self._emit_and_record(
            turn_id,
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": "office_document_request",
                "reason_codes": ["office_document_hard_route", "office_skill_auto_execute"],
            },
        )
        yield await self._emit_and_record(
            turn_id,
            turn["trace_id"],
            events,
            ChatEventType.TASK_CREATED,
            {"task_id": task.task_id, "title": task.title, "status": task.status.value},
        )
        artifacts = await self._task_engine.artifacts(task.task_id)
        office_artifact_refs = _office_artifact_refs(artifacts, office_request.document_type)
        recovery = await self._recover_task_in_turn(turn, events, task, root_span_id)
        task = recovery.task
        artifacts = await self._task_engine.artifacts(task.task_id)
        office_artifact_refs = _office_artifact_refs(artifacts, office_request.document_type)
        office_reply = self._office_task_reply(office_request, task, artifacts)
        text = f"{recovery.response_prefix}{office_reply}"
        presentation = self._task_coordinator.present_task_status(task)
        response_plan = self._composer.response_plan_for_status(
            summary=text,
            task_status={
                **presentation.task_status,
                "artifact_count": len(artifacts),
                "office_document_type": office_request.document_type,
                "office_operation": office_request.operation,
            },
            safety_notice=presentation.safety_notice,
            tool_notice=presentation.tool_notice,
        )
        if recovery.recovery_payload.get("attempt_count"):
            turn_recovery = self._turn_recovery
            if turn_recovery is not None:
                response_plan = turn_recovery.response_plan_for_task(
                    summary=text,
                    task_status={
                        **presentation.task_status,
                        "artifact_count": len(artifacts),
                        "office_document_type": office_request.document_type,
                        "office_operation": office_request.operation,
                    },
                    recovery_payload=recovery.recovery_payload,
                    safety_notice=presentation.safety_notice,
                    tool_notice=presentation.tool_notice,
                )
        response_plan = response_plan.model_copy(
            update={
                "artifact_refs": office_artifact_refs,
                "structured_payload": {
                    **response_plan.structured_payload,
                    "office_route": {
                        "document_type": office_request.document_type,
                        "operation": office_request.operation,
                        "artifact_count": len(artifacts),
                        "artifacts": office_artifact_refs,
                        "status": task.status.value,
                    },
                    "recovery": recovery.recovery_payload,
                },
            }
        )
        if recovery.recovery_payload.get("status") == "exhausted":
            async for event in self._fail_turn(
                turn,
                events,
                ErrorCode.TASK_STEP_FAILED,
                text,
                root_span_id,
                persist_assistant=True,
                response_plan=response_plan,
            ):
                yield event
            return
        async for event in self._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent="office_document_request",
            mode=TaskMode.WORKFLOW.value,
            response_plan=response_plan,
        ):
            yield event

    async def _emit_memory_events(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        result: MemoryCommandResult,
    ) -> AsyncIterator[ChatEvent]:
        for candidate in result.candidates:
            yield await self._emit_and_record(
                turn["turn_id"],
                turn["trace_id"],
                events,
                ChatEventType.MEMORY_CANDIDATE,
                {
                    "candidate_id": candidate.candidate_id,
                    "decision": candidate.decision,
                    "kind": candidate.proposed_kind,
                    "blocked": result.blocked,
                    "reason": result.reason,
                },
            )
        for memory in result.memories:
            event_type = (
                ChatEventType.MEMORY_CORRECTION_APPLIED
                if memory.supersedes or memory.kind == "correction"
                else ChatEventType.MEMORY_WRITTEN
            )
            correction_status = None
            if memory.kind == "correction":
                correction_status = "applied" if memory.supersedes else "not_found"
            yield await self._emit_and_record(
                turn["turn_id"],
                turn["trace_id"],
                events,
                event_type,
                {
                    "memory_id": memory.memory_id,
                    "kind": memory.kind,
                    "supersedes": memory.supersedes,
                    "correction_status": correction_status,
                },
            )

    async def _complete_model_turn(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        text: str,
        root_span_id: str | None,
        *,
        usage: dict[str, Any],
        finish_reason: str,
        route: dict[str, Any],
        intent: str,
        mode: str,
        response_plan: ResponsePlan | None = None,
        clarification_decision: ClarificationDecision | None = None,
        response_filter: dict[str, Any] | None = None,
        prompt_metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[ChatEvent]:
        text = self._style_visible_text(turn, text, response_plan=response_plan)
        filtered_text, final_filter = self._response_coordinator.filter_text(text)
        text = filtered_text
        merged_filter = self._response_coordinator.merge_filter(response_filter, final_filter)
        if response_plan is None:
            prompt_mode = str((prompt_metadata or {}).get("prompt_mode") or "full")
            prompt_snapshot_id = str((prompt_metadata or {}).get("prompt_snapshot_id") or "")
            channel_profile = str((prompt_metadata or {}).get("channel_profile") or "local")
            compose_result = await self._composer.compose(
                ComposeRequest(
                    user_text="",
                    result_summary=text,
                    prompt_mode=prompt_mode,
                    prompt_snapshot_id=prompt_snapshot_id or None,
                    channel_profile=channel_profile,
                    delivery_mode=str((prompt_metadata or {}).get("delivery_mode") or "final"),
                    prompt_assembly_version=str(
                        (prompt_metadata or {}).get("prompt_assembly_version") or ""
                    )
                    or None,
                    stable_prompt_hash=str((prompt_metadata or {}).get("stable_prompt_hash") or "")
                    or None,
                    dynamic_context_hash=str(
                        (prompt_metadata or {}).get("dynamic_context_hash") or ""
                    )
                    or None,
                    trusted_context_hash=str(
                        (prompt_metadata or {}).get("trusted_context_hash") or ""
                    )
                    or None,
                    untrusted_context_hash=str(
                        (prompt_metadata or {}).get("untrusted_context_hash") or ""
                    )
                    or None,
                    history_context_hash=str(
                        (prompt_metadata or {}).get("history_context_hash") or ""
                    )
                    or None,
                    current_message_hash=str(
                        (prompt_metadata or {}).get("current_message_hash") or ""
                    )
                    or None,
                    prompt_section_ids=[
                        str(item)
                        for item in (prompt_metadata or {}).get("prompt_section_ids") or []
                    ],
                    prompt_sections=[
                        dict(item)
                        for item in (prompt_metadata or {}).get("prompt_sections") or []
                        if isinstance(item, dict)
                    ],
                )
            )
            response_plan = compose_result.response_plan.model_copy(
                update={
                    "structured_payload": {
                        **compose_result.response_plan.structured_payload,
                        **_prompt_payload_from_metadata(prompt_metadata),
                        "finish_reason": finish_reason,
                        "mode": mode,
                        "intent": intent,
                        "response_filter": merged_filter,
                        **(
                            {"prompt_assembly": prompt_metadata}
                            if prompt_metadata is not None
                            else {}
                        ),
                    }
                }
            )
        else:
            response_plan = response_plan.model_copy(
                update={
                    **self._response_coordinator.normalize_plan_text(response_plan, text),
                    "structured_payload": {
                        **response_plan.structured_payload,
                        **_prompt_payload_from_metadata(prompt_metadata),
                        "finish_reason": finish_reason,
                        "mode": mode,
                        "intent": intent,
                        "response_filter": merged_filter,
                        **(
                            {"prompt_assembly": prompt_metadata}
                            if prompt_metadata is not None
                            else {}
                        ),
                    },
                }
            )
        if intent == "boundary_question":
            boundary_notice = (
                "我是本地智能体成员，不是真人，也没有隐藏账号或绕过系统的能力；"
                "登录、工具、文件、浏览器和外部动作都得先走安全流程，"
                "该确认的地方我会先停一下。"
            )
            response_plan = response_plan.model_copy(
                update={
                    "title": "能力边界",
                    "style": "safety_boundary",
                    "safety_notice": response_plan.safety_notice or boundary_notice,
                    "boundary_notice": response_plan.boundary_notice or boundary_notice,
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "scenario": "persona_capability_boundary",
                        "boundary_notice": boundary_notice,
                        "forbidden_claims": [
                            "pretending_to_be_human",
                            "claiming_hidden_account_access",
                            "claiming_safety_or_approval_bypass",
                        ],
                    },
                }
            )
        response_plan = self._with_experience_payload(turn, response_plan)
        response_plan = await self._decorate_chat_payloads(turn, response_plan)
        response_plan = await self._decorate_response_plan(
            turn,
            response_plan,
            assistant_text=text,
        )
        text = await self._repair_voice_capability_refusal(
            turn=turn,
            response_plan=response_plan,
            assistant_text=text,
        )
        voice_reply = await self._decorate_voice_reply(
            turn=turn,
            assistant_text=text,
            response_plan=response_plan,
            root_span_id=root_span_id,
        )
        response_plan = self._with_voice_reply(response_plan, voice_reply)
        compose_span = await self._trace.start_span(
            turn["trace_id"],
            span_type=TraceSpanType.RESPONSE_COMPOSE,
            name="compose final response",
            parent_span_id=root_span_id,
            metadata={"mode": mode, "intent": intent},
        )
        await self._trace.end_span(
            compose_span,
            output_data={
                "text_chars": len(text),
                "finish_reason": finish_reason,
                "response_filter": merged_filter,
                "response_plan": redact(response_plan.model_dump(mode="json")),
            },
        )
        assistant_message_id = await self._persist_assistant_message(
            turn,
            text,
            {
                "finish_reason": finish_reason,
                "usage": usage,
                "route": route,
                "mode": mode,
                "intent": intent,
                "status": "completed",
                "response_plan": response_plan.model_dump(mode="json"),
                "response_filter": merged_filter,
                "voice_reply": voice_reply,
            },
            root_span_id,
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.RESPONSE_COMPLETED,
            {
                "message_id": assistant_message_id,
                "finish_reason": finish_reason,
                "usage": usage,
                "route": route,
                "mode": mode,
                "response_plan": response_plan.model_dump(mode="json"),
                "response_filter": merged_filter,
            },
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TURN_COMPLETED,
            {"status": "completed"},
        )
        now = utc_now_iso()
        await self._chat_repo.update_turn(
            turn["turn_id"],
            status="completed",
            assistant_message_id=assistant_message_id,
            intent=intent,
            mode=mode,
            route=route,
            usage=usage,
            events=events,
            updated_at=now,
            ended_at=now,
        )
        await self._update_conversation_summary(turn, text, now)
        if self._chat_experience is not None:
            user_message = await self._chat_repo.get_message(turn["user_message_id"])
            user_text = str(user_message.get("content_text") if user_message else "")
            await self._chat_experience.update_working_state(
                turn=turn,
                user_text=user_text,
                assistant_text=text,
                response_plan=response_plan.model_dump(mode="json"),
                clarification=clarification_decision,
            )
        if intent != "memory_update":
            if self._agent_workbench is not None:
                await self._agent_workbench.enqueue_reflect_after_turn(turn["turn_id"])
            else:
                await self._memory.enqueue_extract_after_turn(turn["turn_id"], schedule=True)
        if root_span_id:
            await self._trace.end_span(
                root_span_id,
                output_data={"assistant_message_id": assistant_message_id, "status": "completed"},
            )
        await self._trace.end_trace(turn["trace_id"])

    async def _update_conversation_summary(
        self,
        turn: dict[str, Any],
        assistant_text: str,
        updated_at: str,
    ) -> None:
        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        user_text = str(redact(user_message.get("content_text") if user_message else ""))
        safe_assistant_text = str(redact(assistant_text))
        summary = f"用户：{user_text}\n回复：{safe_assistant_text}"
        if len(summary) > 800:
            summary = f"{summary[:800]}..."
        await self._chat_repo.upsert_conversation_summary(
            summary_id=new_id("sum"),
            conversation_id=turn["conversation_id"],
            summary_text=summary,
            source_turn_id=turn["turn_id"],
            token_estimate=estimate_messages_tokens(
                [{"role": "system", "content": summary}]
            ),
            updated_at=updated_at,
        )

    async def _fail_turn(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        code: ErrorCode,
        message: str,
        root_span_id: str | None,
        *,
        persist_assistant: bool = False,
        response_plan: ResponsePlan | None = None,
    ) -> AsyncIterator[ChatEvent]:
        message = self._style_visible_text(turn, message, response_plan=response_plan)
        message, response_filter = self._response_coordinator.filter_text(message)
        assistant_message_id = None
        response_plan = response_plan or self._composer.response_plan_for_failure(
            code=code,
            message=message,
        )
        recovery_payload = response_plan.structured_payload.get("recovery")
        recovery_payload = recovery_payload if isinstance(recovery_payload, dict) else None
        if self._chat_experience is not None:
            turn["experience"] = await self._chat_experience.mark_failure(
                turn=turn,
                code=code.value,
                message=message,
            )
            user_message = await self._chat_repo.get_message(turn["user_message_id"])
            user_text = str(user_message.get("content_text") if user_message else "")
            await self._chat_experience.update_working_state(
                turn=turn,
                user_text=user_text,
                assistant_text=message,
                response_plan=response_plan.model_dump(mode="json"),
                status="recoverable",
            )
            response_plan = self._composer.response_plan_for_recovery(
                summary=message,
                error_code=code.value,
                recoverable=True,
                suggested_next_actions=turn["experience"].get("suggested_next_actions", []),
                base_plan=response_plan,
                recovery=recovery_payload,
            )
        response_plan = response_plan.model_copy(
            update={
                "structured_payload": {
                    **response_plan.structured_payload,
                    "response_filter": response_filter,
                },
            }
        )
        response_plan = self._with_experience_payload(turn, response_plan)
        response_plan = await self._decorate_chat_payloads(turn, response_plan)
        response_plan = await self._decorate_response_plan(
            turn,
            response_plan,
            assistant_text=message,
        )
        if persist_assistant:
            compose_span = await self._trace.start_span(
                turn["trace_id"],
                span_type=TraceSpanType.RESPONSE_COMPOSE,
                name="compose failure response",
                parent_span_id=root_span_id,
                metadata={"error_code": code.value},
            )
            await self._trace.end_span(
                compose_span,
                output_data={
                    "text_chars": len(message),
                    "response_plan": redact(response_plan.model_dump(mode="json")),
                },
            )
            assistant_message_id = await self._persist_assistant_message(
                turn,
                message,
                {
                    "status": "failed",
                    "error_code": code.value,
                    "response_plan": response_plan.model_dump(mode="json"),
                },
                root_span_id,
            )
        failed_span = await self._trace.start_span(
            turn["trace_id"],
            span_type=TraceSpanType.TURN_FAILED,
            name="turn failed",
            parent_span_id=root_span_id,
            metadata={"error_code": code.value},
        )
        await self._trace.end_span(
            failed_span,
            status=TraceSpanStatus.FAILED,
            output_data={"message": message},
            error_code=code.value,
        )
        yield await self._emit_and_record(
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
        await self._chat_repo.update_turn(
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
        if root_span_id:
            await self._trace.end_span(root_span_id, status=TraceSpanStatus.FAILED)
        await self._trace.end_trace(turn["trace_id"], status=TraceStatus.FAILED)

    async def _recover_task_in_turn(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        task: Any,
        root_span_id: str | None,
    ) -> TurnRecoveryResult:
        if self._turn_recovery is None:
            return TurnRecoveryResult(
                task=task,
                recovery_payload={
                    "status": (
                        "recovered" if task.status.value == "completed" else task.status.value
                    ),
                    "attempt_count": 0,
                    "root_cause": None,
                    "actions_taken": [],
                    "next_action": None,
                    "task_id": task.task_id,
                },
            )
        recovery = await self._turn_recovery.recover_task_for_turn(
            turn=turn,
            task=task,
            root_span_id=root_span_id,
        )
        for recovery_event in recovery.events:
            await self._emit_and_record(
                turn["turn_id"],
                turn["trace_id"],
                events,
                recovery_event.event_type,
                recovery_event.payload,
            )
        return recovery

    async def _cancel_turn_during_stream(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        root_span_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        partial = "".join(
            event["payload"].get("text", "")
            for event in events
            if event.get("event") == ChatEventType.RESPONSE_DELTA.value
        )
        text = self._style_visible_text(turn, self._composer.compose_cancelled(partial))
        response_plan = self._composer.response_plan_for_status(
            summary=text,
            task_status={"status": "cancelled", "finish_reason": "cancelled"},
        )
        if self._chat_experience is not None:
            turn["experience"] = await self._chat_experience.mark_cancelled(
                turn=turn,
                partial_text=partial,
            )
            response_plan = self._composer.response_plan_for_recovery(
                summary=text,
                error_code=ErrorCode.TURN_CANCELLED.value,
                recoverable=True,
                suggested_next_actions=turn["experience"].get("suggested_next_actions", []),
                base_plan=response_plan,
            )
        response_plan = self._with_experience_payload(turn, response_plan)
        response_plan = await self._decorate_response_plan(
            turn,
            response_plan,
            assistant_text=text,
        )
        assistant_message_id = await self._persist_assistant_message(
            turn,
            text,
            {
                "status": "cancelled",
                "finish_reason": "cancelled",
                "response_plan": response_plan.model_dump(mode="json"),
            },
            root_span_id,
        )
        cancel_span = await self._trace.start_span(
            turn["trace_id"],
            span_type=TraceSpanType.TURN_CANCEL,
            name="turn cancelled",
            parent_span_id=root_span_id,
        )
        await self._trace.end_span(
            cancel_span,
            output_data={"assistant_message_id": assistant_message_id},
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TURN_CANCELLED,
            {
                "code": ErrorCode.TURN_CANCELLED.value,
                "message": "已停止生成",
                "message_id": assistant_message_id,
                "response_plan": response_plan.model_dump(mode="json"),
            },
        )
        now = utc_now_iso()
        await self._chat_repo.update_turn(
            turn["turn_id"],
            status="cancelled",
            assistant_message_id=assistant_message_id,
            error_code=ErrorCode.TURN_CANCELLED.value,
            events=events,
            experience=turn.get("experience") or {},
            updated_at=now,
            ended_at=now,
        )
        if root_span_id:
            await self._trace.end_span(root_span_id, status=TraceSpanStatus.FAILED)
        await self._trace.end_trace(turn["trace_id"], status=TraceStatus.FAILED)

    async def _persist_assistant_message(
        self,
        turn: dict[str, Any],
        text: str,
        metadata: dict[str, Any],
        root_span_id: str | None,
    ) -> str:
        message_id = new_id("msg")
        span_id = await self._trace.start_span(
            turn["trace_id"],
            span_type=TraceSpanType.MESSAGE_PERSIST_ASSISTANT,
            name="persist assistant message",
            parent_span_id=root_span_id,
            metadata={"message_id": message_id},
        )
        now = utc_now_iso()
        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        session_id = None
        if user_message and isinstance(user_message.get("content"), dict):
            session_id = user_message["content"].get("session_id")
        voice_reply = metadata.get("voice_reply") if isinstance(metadata, dict) else None
        voice_reply = voice_reply if isinstance(voice_reply, dict) else {}
        content_type = "audio" if voice_reply.get("should_render") else "text"
        await self._chat_repo.insert_message(
            message_id=message_id,
            conversation_id=turn["conversation_id"],
            turn_id=turn["turn_id"],
            author_type="assistant",
            author_id=turn["member_id"],
            content_type=content_type,
            content_text=text,
            content={
                "type": content_type,
                "text": text,
                "session_id": session_id,
                **metadata,
            },
            trace_id=turn["trace_id"],
            voice_profile_id=str(voice_reply.get("voice_profile_id"))
            if voice_reply.get("voice_profile_id")
            else None,
            voice_render_job_id=str(voice_reply.get("render_job_id"))
            if voice_reply.get("render_job_id")
            else None,
            audio_uri=str(voice_reply.get("audio_uri")) if voice_reply.get("audio_uri") else None,
            audio_content_type=str(voice_reply.get("audio_content_type"))
            if voice_reply.get("audio_content_type")
            else None,
            voice_metadata=voice_reply,
            created_at=now,
        )
        if voice_reply.get("render_job_id") and self._voice is not None:
            await self._voice.attach_message(
                render_job_id=str(voice_reply["render_job_id"]),
                message_id=message_id,
                trace_id=turn["trace_id"],
            )
        await self._chat_repo.touch_conversation(turn["conversation_id"], now)
        await self._trace.end_span(span_id, output_data={"message_id": message_id})
        return message_id

    async def _emit_and_record(
        self,
        turn_id: str,
        trace_id: str,
        events: list[dict[str, Any]],
        event_type: ChatEventType,
        payload: dict[str, Any] | None = None,
    ) -> ChatEvent:
        event = self._runtime.event(
            event_type,
            turn_id=turn_id,
            trace_id=trace_id,
            payload=payload,
        )
        sequence = await self._record_event(event, events)
        await self._events.append(turn_id, sequence, event)
        return event

    async def _record_event(self, event: ChatEvent, events: list[dict[str, Any]]) -> int:
        event_data = event.model_dump(mode="json")
        events.append(event_data)
        sequence = await self._chat_repo.next_event_sequence(event.turn_id)
        await self._chat_repo.insert_event(
            event_id=new_id("evt"),
            turn_id=event.turn_id,
            sequence=sequence,
            event_type=event.event.value,
            trace_id=event.trace_id,
            payload=event_data,
            created_at=event.timestamp.isoformat(),
        )
        return sequence

    async def _finalize_created_cancel(self, turn: dict[str, Any]) -> None:
        summary = self._style_visible_text(turn, "已停止生成。")
        response_plan = self._composer.response_plan_for_status(
            summary=summary,
            task_status={"status": "cancelled", "finish_reason": "cancelled"},
        )
        if self._chat_experience is not None:
            turn["experience"] = await self._chat_experience.mark_cancelled(
                turn=turn,
                partial_text="",
            )
            response_plan = self._composer.response_plan_for_recovery(
                summary=summary,
                error_code=ErrorCode.TURN_CANCELLED.value,
                recoverable=True,
                suggested_next_actions=turn["experience"].get("suggested_next_actions", []),
                base_plan=response_plan,
            )
        response_plan = self._with_experience_payload(turn, response_plan)
        response_plan = await self._decorate_response_plan(
            turn,
            response_plan,
            assistant_text=summary,
        )
        event = self._runtime.event(
            ChatEventType.TURN_CANCELLED,
            turn_id=turn["turn_id"],
            trace_id=turn["trace_id"],
            payload={
                "code": ErrorCode.TURN_CANCELLED.value,
                "message": summary,
                "response_plan": response_plan.model_dump(mode="json"),
            },
        )
        event_data = event.model_dump(mode="json")
        cancelled = await self._chat_repo.cancel_created_turn(
            turn["turn_id"],
            error_code=ErrorCode.TURN_CANCELLED.value,
            error_message=summary,
            events=[event_data],
            updated_at=utc_now_iso(),
        )
        if cancelled:
            await self._chat_repo.update_turn(
                turn["turn_id"],
                experience=turn.get("experience") or {},
                updated_at=utc_now_iso(),
            )
        if cancelled:
            sequence = await self._record_event(event, [])
            await self._events.append(turn["turn_id"], sequence, event)
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
        await self._events.mark_completed(turn["turn_id"])

    def _with_experience_payload(
        self,
        turn: dict[str, Any],
        response_plan: ResponsePlan,
    ) -> ResponsePlan:
        experience = dict(turn.get("experience") or {})
        if not experience:
            return response_plan
        structured = {
            **response_plan.structured_payload,
            "experience": redact(experience),
        }
        follow_ups = list(response_plan.follow_up_options)
        for option in experience.get("suggested_next_actions", []):
            if isinstance(option, str) and option not in follow_ups:
                follow_ups.append(option)
        return response_plan.model_copy(
            update={
                "structured_payload": structured,
                "follow_up_options": follow_ups,
            }
        )

    def _style_visible_text(
        self,
        turn: dict[str, Any],
        text: str,
        *,
        response_plan: ResponsePlan | None = None,
    ) -> str:
        return self._composer.style_text(
            text,
            ui_mode=self._ui_mode_for_turn(turn),
            response_plan=response_plan,
        )

    def _ui_mode_for_turn(self, turn: dict[str, Any]) -> str | None:
        experience = turn.get("experience") or {}
        client_context = experience.get("client_context")
        if not isinstance(client_context, dict):
            return None
        ui_mode = client_context.get("ui_mode")
        if ui_mode is None:
            return None
        return str(ui_mode)

    async def _decorate_response_plan(
        self,
        turn: dict[str, Any],
        response_plan: ResponsePlan,
        *,
        assistant_text: str,
    ) -> ResponsePlan:
        if self._persona_heart is None:
            return response_plan
        return await self._persona_heart.decorate_response_plan(
            turn=turn,
            response_plan=response_plan,
            assistant_text=assistant_text,
        )

    async def _decorate_voice_reply(
        self,
        *,
        turn: dict[str, Any],
        assistant_text: str,
        response_plan: ResponsePlan,
        root_span_id: str | None,
    ) -> dict[str, Any]:
        if self._voice is None:
            return {
                "requested": False,
                "should_render": False,
                "reason": "voice_service_unavailable",
            }
        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        user_text = str(user_message.get("content_text") if user_message else "")
        persona, heart = await self._voice_context(turn)
        risk_level = str((response_plan.structured_payload or {}).get("risk_level") or "R1")
        voice_reply = await self._voice.resolve_voice_reply(
            turn=turn,
            user_text=user_text,
            assistant_text=assistant_text,
            response_plan=response_plan.model_dump(mode="json"),
            persona=persona,
            heart=heart,
            risk_level=risk_level,
            trace_id=turn["trace_id"],
        )
        payload = voice_reply.model_dump(mode="json")
        if root_span_id:
            span_id = await self._trace.start_span(
                turn["trace_id"],
                span_type=TraceSpanType.VOICE_RENDER,
                name="voice reply decision",
                parent_span_id=root_span_id,
                metadata={
                    "turn_id": turn["turn_id"],
                    "requested": payload.get("requested"),
                    "should_render": payload.get("should_render"),
                    "reason": payload.get("reason"),
                },
            )
            await self._trace.end_span(
                span_id,
                output_data={
                    "requested": payload.get("requested"),
                    "should_render": payload.get("should_render"),
                    "reason": payload.get("reason"),
                    "voice_profile_id": payload.get("voice_profile_id"),
                },
            )
        return payload

    async def _repair_voice_capability_refusal(
        self,
        *,
        turn: dict[str, Any],
        response_plan: ResponsePlan,
        assistant_text: str,
    ) -> str:
        if self._voice is None:
            return assistant_text
        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        user_text = str(user_message.get("content_text") if user_message else "")
        if not _looks_like_voice_reply_request(user_text):
            return assistant_text
        lowered = assistant_text.lower()
        refusal_markers = (
            "只能发文字",
            "没办法用声音",
            "不能用声音",
            "无法用声音",
            "不能发语音",
            "无法发语音",
            "can't send voice",
            "cannot send voice",
        )
        if not any(marker in lowered for marker in refusal_markers):
            return assistant_text
        member_name = "小耀" if str(turn.get("member_id")) == "mem_xiaoyao" else "我"
        return f"可以，我用{member_name}自己的声音回复你。"

    async def _voice_context(self, turn: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        persona: dict[str, Any] = {}
        heart: dict[str, Any] = {}
        if self._persona_heart is None:
            return persona, heart
        try:
            member = await self._members.get_member(turn["member_id"])
            if member and member.get("persona_profile_id"):
                profile = await self._persona_heart.get_profile(str(member["persona_profile_id"]))
                persona = profile.model_dump(mode="json") if hasattr(profile, "model_dump") else dict(profile)
        except Exception:
            persona = {}
        try:
            state = await self._persona_heart.heart_state(
                turn["member_id"],
                text=None,
                trace_id=turn.get("trace_id"),
            )
            heart = (
                state.model_dump(mode="json")
                if hasattr(state, "model_dump")
                else dict(state)
            )
        except Exception:
            heart = {}
        return persona, heart

    def _with_voice_reply(self, response_plan: ResponsePlan, voice_reply: dict[str, Any]) -> ResponsePlan:
        if not voice_reply:
            return response_plan
        structured = {
            **response_plan.structured_payload,
            "voice_reply": voice_reply,
            "voice_reply_requested": bool(voice_reply.get("requested")),
            "voice_reply_rendered": bool(voice_reply.get("should_render")),
        }
        return response_plan.model_copy(update={"structured_payload": structured})

    def _model_messages(
        self,
        context: ContextPacket,
        user_text: str,
        *,
        channel_profile: str | None = None,
        delivery_mode: str | None = None,
        sender_label: str | None = None,
        turn_id: str | None = None,
    ) -> list[dict[str, str]]:
        return self._model_coordinator.model_messages(
            context,
            user_text,
            channel_profile=channel_profile,
            delivery_mode=delivery_mode,
            sender_label=sender_label,
            turn_id=turn_id,
        )

    async def _create_conversation(self, member: dict[str, Any], title: str) -> str:
        conversation_id = new_id("conv")
        now = utc_now_iso()
        await self._chat_repo.create_conversation(
            conversation_id=conversation_id,
            organization_id=member["organization_id"],
            title=title,
            primary_member_id=member["member_id"],
            participants=[
                {"type": "user", "id": DEFAULT_USER_ID},
                {"type": "member", "id": member["member_id"]},
            ],
            created_at=now,
        )
        return conversation_id

    async def _root_span_id(self, trace_id: str) -> str | None:
        row = await self._db.fetch_one(
            "SELECT root_span_id FROM traces WHERE trace_id = ?",
            (trace_id,),
        )
        return row["root_span_id"] if row else None

    def _route_error_code(
        self,
        available_brains: list[dict[str, Any]],
        privacy_level: str,
    ) -> ErrorCode:
        return self._privacy.model_route_error(available_brains, privacy_level)

    def _clarification_from_brain(
        self,
        turn: dict[str, Any],
        brain_decision: Any,
    ) -> ClarificationDecision | None:
        data = dict(brain_decision.clarification or {})
        if not data.get("needs_clarification"):
            return None
        now = utc_now_iso()
        return ClarificationDecision(
            clarification_id=new_id("clarify"),
            turn_id=turn["turn_id"],
            conversation_id=turn["conversation_id"],
            needs_clarification=True,
            reason=str(data.get("reason") or "clarification_required"),
            clarification_type=str(data.get("clarification_type") or "missing_goal"),
            blocking_level=str(data.get("blocking_level") or "requires_answer"),
            questions=[str(item) for item in data.get("questions", [])][:3],
            can_answer_partially=bool(data.get("safe_partial_answer_allowed", False)),
            trace_id=turn["trace_id"],
            created_at=now,
            updated_at=now,
        )

    def _task_mode_from_brain(self, mode: str | None) -> TaskMode:
        if mode == TaskMode.DIRECT_WITH_MEMORY.value:
            return TaskMode.DIRECT_WITH_MEMORY
        if mode == TaskMode.WORKFLOW.value:
            return TaskMode.WORKFLOW
        if mode == TaskMode.AGENT.value:
            return TaskMode.AGENT
        if mode == TaskMode.SUPERVISOR.value:
            return TaskMode.SUPERVISOR
        return TaskMode.DIRECT

    def _intent_creates_task(self, intent: str) -> bool:
        return self._task_coordinator.intent_creates_task(intent)

    async def _enabled_office_skill_id(self, office_request: OfficeChatRequest) -> str | None:
        if self._skill_plugins is None:
            return None
        preferred_bundle = preferred_office_bundle_id(office_request)
        try:
            for skill in await self._skill_plugins.list_skills(status="enabled"):
                if skill.bundle_id == preferred_bundle and skill.status == "enabled":
                    return str(skill.skill_id)
        except Exception:
            return None
        return None

    async def _office_skill_has_grant(
        self,
        skill_id: str,
        tool_name: str,
        member_id: str,
    ) -> bool:
        if self._skill_governance is None:
            return True
        try:
            for grant in await self._skill_governance.list_grants(skill_id):
                if grant.status != "active":
                    continue
                if grant.subject_type != "member" or grant.subject_id != member_id:
                    continue
                if tool_name in set(grant.allowed_tools):
                    return True
        except Exception:
            return False
        return False

    async def _latest_office_artifact_id(
        self,
        conversation_id: str,
        document_type: str,
    ) -> str | None:
        if self._task_engine is None:
            return None
        content_marker = {
            "word": "wordprocessingml.document",
            "excel": "spreadsheetml.sheet",
            "ppt": "presentationml.presentation",
        }.get(document_type)
        try:
            for task in await self._task_engine.list_tasks(limit=50):
                if str(task.conversation_id or "") != conversation_id:
                    continue
                for artifact in reversed(await self._task_engine.artifacts(task.task_id)):
                    if content_marker and content_marker in str(artifact.content_type or ""):
                        return str(artifact.artifact_id)
        except Exception:
            return None
        return None

    def _office_missing_capability_text(
        self,
        office_request: OfficeChatRequest,
        reason: str,
    ) -> str:
        doc_name = _office_doc_visible_name(office_request.document_type)
        action = "编辑" if office_request.operation == "edit" else "生成"
        source_ref = f"clawhub:official/office/{_office_package_ref_suffix(office_request)}"
        if reason == "missing_enabled_skill":
            return (
                f"你是想{action}{doc_name}，但对应 Office Skill 还没装好。\n"
                "我先把这步按住，没有假装已经生成。\n"
                f"可以用 CLI 装上：cycber skills install {source_ref} --enable --grant-default。"
            )
        return (
            f"你是想{action}{doc_name}，Skill 已经找到了，但当前成员还没授权"
            f" `{preferred_office_tool_name(office_request)}`。\n"
            "没有授权我先不写文件，免得把边界踩歪。"
        )

    def _office_next_actions(self, office_request: OfficeChatRequest, reason: str) -> list[str]:
        source_ref = f"clawhub:official/office/{_office_package_ref_suffix(office_request)}"
        if reason == "missing_enabled_skill":
            return [f"cycber skills install {source_ref} --enable --grant-default"]
        return [
            f"cycber skills grant <skill_id> --tool {preferred_office_tool_name(office_request)}"
        ]

    def _office_task_reply(
        self,
        office_request: OfficeChatRequest,
        task: Any,
        artifacts: list[Any],
    ) -> str:
        doc_name = _office_doc_visible_name(office_request.document_type)
        action = "编辑" if office_request.operation == "edit" else "生成"
        if task.status.value != "completed":
            if task.status.value == "waiting_approval":
                return (
                    f"{doc_name}{action}任务已经起好了，但还在等确认；"
                    "你点头前我不会写入或改动文件。"
                )
            if task.status.value == "failed":
                return (
                    f"{doc_name}{action}任务这次没跑完。"
                    "你可以让我缩小范围、换内容，或者看一下失败原因再来一遍。"
                )
            return (
                f"{doc_name}{action}任务已经起步，当前状态是 {task.status.value}，"
                "我会按真实状态继续告诉你。"
            )
        office_artifact = _first_office_artifact(artifacts, office_request.document_type)
        if office_artifact is None:
            return (
                f"{doc_name}{action}任务已经跑完，但没找到对应的文件结果。"
                "我不会把这当成真正完成，还是得回头看一下 Skill 输出。"
            )
        detail = _office_reply_detail(office_request)
        summary = _office_content_summary(office_request)
        next_hint = _office_next_edit_hint(office_request.document_type)
        return (
            f"{doc_name}已经{action}完成，文件：{office_artifact.display_name}。"
            f"{detail}"
            f"{summary}"
            f"{next_hint}"
        )


def _session_id_from_message(message: dict[str, Any] | None) -> str | None:
    if not message:
        return None
    content = message.get("content")
    if isinstance(content, dict):
        value = content.get("session_id")
        return str(value) if value else None
    return None


def _phase52_deploy_or_install_explain_only(text: str) -> bool:
    clean = text.strip()
    lowered = clean.lower()
    direct_only = any(
        marker in clean
        for marker in ["只解释", "只给方案", "不要执行", "不要创建任务", "不要调用工具"]
    )
    deploy_or_install = any(
        marker in clean or marker in lowered
        for marker in ["部署", "安装", "跑起来", "github", "git 仓库", "git仓库", "install"]
    )
    return direct_only and deploy_or_install


def _direct_route_reply(
    route_type: str,
    user_text: str,
) -> tuple[str, str, dict[str, Any]] | None:
    if route_type == "download_topic":
        text = (
            "可以补下载端点说明，但我不会触发真实下载。建议把 artifact 下载设计成只读接口："
            "先校验成员对该任务的访问权限，再按 artifact id 读取元数据和文件流；响应头设置"
            "准确的文件名、content type 和长度，并留下一条记录。这样用户拿到的是已生成"
            "结果文件，不会因为一句“下载端点”就让浏览器跑出去。"
        )
        return text, "download_topic_explanation", {"download_topic": {"real_download": False}}
    if route_type == "skill_mcp_concept":
        text = (
            "方法包更像“做事说明书”：定义什么时候用、需要哪些工具、权限和步骤。"
            "MCP 更像“外部工具插座”：把浏览器、数据库、SaaS 或本地服务以统一协议接进来。"
            "简单说，方法包决定怎么做，MCP 提供能调用的外部能力；两者都应该经过权限和"
            "安全检查，而不是绕过系统直接执行。"
        )
        return text, "skill_mcp_concept", {"concept_answer": {"task_created": False}}
    return None


def _host_filesystem_list_reply(result: dict[str, Any]) -> str:
    location = _host_filesystem_label(str(result.get("location") or "home"))
    items = list(result.get("items") or [])
    if not items:
        return f"我看了一下{location}，没有可展示的文件或文件夹。"
    visible = items[:10]
    names = []
    for item in visible:
        name = str(item.get("name") or "")
        kind = "文件夹" if item.get("type") == "directory" else "文件"
        names.append(f"{name}（{kind}）")
    suffix = "；结果已截断。" if result.get("truncated") else "。"
    hidden = int((result.get("redaction_summary") or {}).get("hidden_items_skipped") or 0)
    redacted = int((result.get("redaction_summary") or {}).get("sensitive_names_redacted") or 0)
    privacy_note = ""
    if hidden or redacted:
        privacy_note = f" 另外有 {hidden + redacted} 项因隐藏或敏感命名没有直接展示。"
    return f"我看了一下{location}，找到 {len(items)} 项：{'; '.join(names)}{suffix}{privacy_note}"


def _host_filesystem_list_error_reply(location: str, exc: AppError) -> str:
    label = _host_filesystem_label(location)
    reason = str((exc.details or {}).get("reason") or exc.message)
    if reason in {"host_fs_sensitive_path_denied", "host_fs_outside_allowed_roots"}:
        return f"这个位置不能直接查看：{label} 不在当前允许的只读目录边界内。"
    if reason == "host_fs_path_traversal_denied":
        return "这个路径包含越界片段，安全策略已拒绝查看。"
    return f"我没能查看{label}：{exc.message}"


def _host_filesystem_label(location: str) -> str:
    return {
        "desktop": "桌面",
        "downloads": "下载目录",
        "documents": "文档目录",
        "home": "用户主目录",
        "authorized": "授权目录",
    }.get(location, "该目录")


def _browser_read_page_reply(result: dict[str, Any]) -> str:
    title = _clean_browser_text(str(result.get("title") or "")) or "未识别标题"
    status = result.get("http_status")
    content = _browser_visible_text(
        str(result.get("content_preview") or result.get("snapshot") or "")
    )
    if not content:
        return (
            f"我打开了这个网页，HTTP 状态是 {status or '未知'}，标题是《{title}》。"
            "页面没有返回可提取的正文文本，可能主要依赖脚本渲染或访问受限。"
        )
    preview = _truncate_browser_text(content, 360)
    return (
        f"我打开并读取了这个网页，HTTP 状态是 {status or '未知'}，标题是《{title}》。"
        f"页面可见内容大致是：{preview}"
    )


def _browser_read_page_error_reply(exc: AppError) -> str:
    details = exc.details or {}
    reason_codes = details.get("reason_codes") or []
    blocked_reason = str(details.get("blocked_reason") or "")
    if blocked_reason == "metadata_url" or "browser_metadata_url_denied" in reason_codes:
        return "这个链接指向云元数据或本机敏感地址，安全策略已拒绝访问。"
    if blocked_reason == "private_network" or "browser_private_network_denied" in reason_codes:
        return "这个链接指向当前不允许访问的私有网络地址，安全策略已拒绝访问。"
    if blocked_reason == "unsupported_scheme":
        return "这个链接不是可安全访问的 http/https 地址，所以没有打开。"
    if "task_binding_required" in reason_codes:
        return "当前浏览器只读策略还没有生效，所以这次没有打开网页；需要刷新只读访问配置后重试。"
    return f"我没能打开这个网页：{exc.message}"


def _browser_read_page_payload(result: dict[str, Any]) -> dict[str, Any]:
    content = _browser_visible_text(
        str(result.get("content_preview") or result.get("snapshot") or "")
    )
    return {
        "status": result.get("action_status") or "completed",
        "url": result.get("url"),
        "title": _clean_browser_text(str(result.get("title") or "")) or None,
        "http_status": result.get("http_status"),
        "content_preview": _truncate_browser_text(content, 1000) if content else None,
        "browser_evidence_id": result.get("browser_evidence_id"),
        "backend": result.get("backend"),
        "redaction_summary": result.get("redaction_summary"),
        "untrusted_external_content": True,
    }


def _terminal_command_reply(command: str, result: dict[str, Any]) -> str:
    output = _clean_terminal_output(str(result.get("output_preview") or ""))
    exit_code = result.get("exit_code")
    if output:
        return (
            f"命令已在受控终端沙箱里执行完成，退出码是 {exit_code}。"
            f"输出摘要：{_truncate_browser_text(output, 500)}"
        )
    return f"命令已在受控终端沙箱里执行完成，退出码是 {exit_code}，没有可展示输出。"


def _terminal_command_error_reply(command: str, exc: AppError) -> str:
    del command
    if exc.code in {
        ErrorCode.TOOL_APPROVAL_REQUIRED.value,
        ErrorCode.TOOL_PERMISSION_DENIED.value,
        ErrorCode.TOOL_OUTPUT_BLOCKED.value,
        ErrorCode.SAFETY_BLOCKED.value,
    }:
        return "这条系统命令没有执行：终端安全策略认为它需要确认、越界，或风险过高。"
    return f"这条系统命令没有执行：{exc.message}"


def _clean_terminal_output(value: str) -> str:
    return re.sub(r"\s+", " ", str(redact(value))).strip()


def _browser_visible_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg|canvas)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|section|article|header|footer|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return _clean_browser_text(html.unescape(text))


def _clean_browser_text(value: str) -> str:
    value = re.sub(r"\s+", " ", value or "")
    return value.strip(" \t\r\n")


def _truncate_browser_text(value: str, limit: int) -> str:
    clean = _clean_browser_text(value)
    if len(clean) <= limit:
        return clean
    return f"{clean[: max(0, limit - 1)].rstrip()}…"


def _strategy_advice_fallback_text(user_text: str) -> str:
    del user_text
    return (
        "建议默认采用“快回归优先，关键链路再上真实模型”的策略：日常提交跑定向回归，"
        "保证速度和开发反馈；合并前跑核心聊天、任务、Skill、权限的组合用例，保证覆盖率；"
        "真实模型测试放到 nightly 或 release profile，只覆盖高价值样例，控制成本。"
        "默认阈值可以是：本地规则/假模型每次必跑，真实模型只跑主链路和最近改动相关场景。"
    )


def _office_doc_visible_name(document_type: str) -> str:
    return {"word": "Word 文档", "excel": "Excel 表格", "ppt": "PPT 演示稿"}.get(
        document_type,
        "Office 文件",
    )


def _office_reply_detail(office_request: OfficeChatRequest) -> str:
    if office_request.operation == "edit":
        return "我已基于上一版生成了新的文件，没有覆盖原文件。"
    if office_request.document_type == "excel":
        return "我把输入数据落到了 Data sheet，并附上了摘要 sheet。"
    if office_request.document_type == "ppt":
        if office_request.requested_pages_or_sheets:
            return f"我按你要的 {office_request.requested_pages_or_sheets} 页组织了标题页和正文页。"
        return "我整理了标题页、进展、风险和下一步页面。"
    return "我整理了进展、风险与下一步计划，并保留了表格结构。"


def _office_next_edit_hint(document_type: str) -> str:
    if document_type == "excel":
        return "下一步可以继续让我新增利润率 sheet，或把图表改成月度趋势。"
    if document_type == "ppt":
        return "下一步可以继续让我加一页风险，或按新的汇报对象改写。"
    return "下一步可以继续让我补风险、下一步章节，或把语气改得更正式。"


def _office_content_summary(office_request: OfficeChatRequest) -> str:
    topic = office_request.topic.strip()
    if office_request.operation == "edit":
        return "这次编辑生成的是新版本，原文件没有被覆盖。"
    if office_request.document_type == "excel":
        return f"我已按“{topic}”整理数据、汇总和基础分析。"
    if office_request.document_type == "ppt":
        page_hint = (
            f"{office_request.requested_pages_or_sheets} 页"
            if office_request.requested_pages_or_sheets
            else "多页"
        )
        return f"我已按“{topic}”组织成 {page_hint} 演示结构。"
    return f"我已按“{topic}”整理完成事项、风险和下一步。"


def _office_package_ref_suffix(office_request: OfficeChatRequest) -> str:
    bundle = preferred_office_bundle_id(office_request)
    return bundle.removeprefix("clawhub-").replace("analysis-workbook", "analysis-workbook")


def _office_artifact_refs(artifacts: list[Any], document_type: str) -> list[dict[str, Any]]:
    marker = {
        "word": "wordprocessingml.document",
        "excel": "spreadsheetml.sheet",
        "ppt": "presentationml.presentation",
    }.get(document_type)
    refs: list[dict[str, Any]] = []
    for artifact in artifacts:
        content_type = str(getattr(artifact, "content_type", "") or "")
        if marker and marker not in content_type:
            continue
        metadata = getattr(artifact, "metadata", {}) or {}
        if metadata.get("copied_for_office_edit"):
            continue
        artifact_id = str(getattr(artifact, "artifact_id", "") or "")
        if not artifact_id:
            continue
        refs.append(
            {
                "artifact_id": artifact_id,
                "display_name": str(getattr(artifact, "display_name", "") or "office-file"),
                "content_type": content_type,
                "size_bytes": getattr(artifact, "size_bytes", None),
                "checksum": getattr(artifact, "checksum", None),
                "download_url": f"/api/artifacts/{artifact_id}/download",
            }
        )
    return refs


def _first_office_artifact(artifacts: list[Any], document_type: str) -> Any | None:
    marker = {
        "word": "wordprocessingml.document",
        "excel": "spreadsheetml.sheet",
        "ppt": "presentationml.presentation",
    }.get(document_type)
    office_artifacts = [
        artifact
        for artifact in artifacts
        if marker and marker in str(getattr(artifact, "content_type", "") or "")
    ]
    for artifact in reversed(office_artifacts):
        metadata = getattr(artifact, "metadata", {}) or {}
        if not metadata.get("copied_for_office_edit"):
            return artifact
    return office_artifacts[-1] if office_artifacts else (artifacts[0] if artifacts else None)


def _title_from_text(text: str) -> str:
    clean = str(redact(text)).strip().replace("\n", " ")
    if len(clean) <= 18:
        return clean or "新的对话"
    return f"{clean[:18]}..."


def _channel_profile_for_turn(turn: dict[str, Any]) -> str:
    experience = turn.get("experience") if isinstance(turn, dict) else {}
    client_context = experience.get("client_context") if isinstance(experience, dict) else {}
    if isinstance(client_context, dict) and client_context.get("ui_mode"):
        return str(client_context["ui_mode"])
    channel = experience.get("channel") if isinstance(experience, dict) else None
    return str(channel or "local")


def _prompt_payload_from_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    keys = [
        "prompt_assembly_version",
        "prompt_snapshot_id",
        "stable_prompt_hash",
        "dynamic_context_hash",
        "trusted_context_hash",
        "history_context_hash",
        "current_message_hash",
        "untrusted_context_hash",
        "prompt_section_ids",
        "prompt_sections",
        "prompt_mode",
        "channel_profile",
        "delivery_mode",
    ]
    return {key: metadata[key] for key in keys if key in metadata}


def _looks_like_voice_reply_request(text: str) -> bool:
    if not text:
        return False
    patterns = (
        r"用(?:声音|语音|语言)回复",
        r"用(?:你的|你)?(?:声音|语音)回",
        r"(?:发|回)语音",
        r"语音回复",
        r"声音回复",
        r"请(?:用|以)(?:声音|语音|语言)",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _event_from_persisted(row: dict[str, Any]) -> ChatEvent:
    return ChatEvent(**row["payload"])
