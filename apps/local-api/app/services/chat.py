from __future__ import annotations

# ruff: noqa: E501
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from brain import ModelRouter
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
from response_composer import (
    ComposeRequest,
    ResponseComposer,
    canonical_action_status,
    mirrored_status_payload,
    normalize_action_status_semantics,
    status_reason_codes,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.brain_repo import BrainRepository
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.member_repo import MemberRepository
from app.db.session import Database
from app.schemas.chat_quality import (
    ActionDialogueFacts,
    ConversationUnderstandingRequest,
    PresenceStateRequest,
    ResponsePolicyRequest,
)
from app.services.action_dialogue_mapper import ActionDialogueMapperService
from app.services.asset_broker import AssetBrokerService
from app.services.audit import AuditEventService
from app.services.brain_decision import BrainDecisionService
from app.services.chat_capability_boundary import ChatCapabilityBoundaryService
from app.services.chat_context import ChatContextCoordinator
from app.services.chat_continuation import ChatContinuationCoordinator, ContinuationEvaluation
from app.services.chat_experience import ChatExperienceService, ClarificationDecision
from app.services.failure_experience import FailureExperienceService
from app.services.chat_hook_runtime import ChatHookRuntime
from app.services.chat_ingress import ChatContentNormalizer, ChatIngressService
from app.services.chat_intent_router import (
    ChatIntentRouter,
    ChatRouteDecision,
    OfficeChatRequest,
    office_skill_input,
    preferred_office_bundle_id,
    preferred_office_tool_name,
)
from app.services.chat_prompt_options import (
    phase89_heuristic_runtime as _phase89_heuristic_runtime,
)
from app.services.chat_runtime_host_helpers import (
    browser_read_page_error_reply as _browser_read_page_error_reply,
    browser_read_page_payload as _browser_read_page_payload,
    browser_read_page_reply as _browser_read_page_reply,
    browser_visible_text as _browser_visible_text,
    channel_profile_for_turn as _channel_profile_for_turn,
    clean_browser_text as _clean_browser_text,
    content_payload as _content_payload,
    context_compaction_summary as _context_compaction_summary,
    debounce_delay_seconds as _debounce_delay_seconds,
    deterministic_boundary_reply as _deterministic_boundary_reply,
    deterministic_no_model_reply as _deterministic_no_model_reply,
    direct_route_reply as _direct_route_reply,
    error_signature as _error_signature,
    event_from_persisted as _event_from_persisted,
    grouped_presence_runtime as _grouped_presence_runtime,
    host_filesystem_list_error_reply as _host_filesystem_list_error_reply,
    host_filesystem_label as _host_filesystem_label,
    host_filesystem_list_reply as _host_filesystem_list_reply,
    first_office_artifact as _first_office_artifact,
    message_user_text as _message_user_text,
    model_failure_type as _model_failure_type,
    office_artifact_refs as _office_artifact_refs,
    office_content_summary as _office_content_summary,
    office_doc_visible_name as _office_doc_visible_name,
    office_next_edit_hint as _office_next_edit_hint,
    office_package_ref_suffix as _office_package_ref_suffix,
    office_reply_detail as _office_reply_detail,
    phase52_deploy_or_install_explain_only as _phase52_deploy_or_install_explain_only,
    presence_advisory_state as _presence_advisory_state,
    presence_response_driving_state as _presence_response_driving_state,
    presence_rollout_state as _presence_rollout_state,
    prompt_payload_from_metadata as _prompt_payload_from_metadata,
    queue_lock_until as _queue_lock_until,
    queue_payload as _queue_payload,
    reply_option_items as _reply_option_items,
    request_text as _request_text,
    session_id_from_message as _session_id_from_message,
    strategy_advice_fallback_text as _strategy_advice_fallback_text,
    terminal_command_error_reply as _terminal_command_error_reply,
    terminal_command_reply as _terminal_command_reply,
    title_from_text as _title_from_text,
    truncate_browser_text as _truncate_browser_text,
)
from app.services.chat_memory import ChatMemoryCoordinator
from app.services.chat_model import ChatModelCoordinator
from app.services.chat_model_execution import ChatModelExecutionService
from app.services.chat_pending_state import (
    active_pending_clarification,
)
from app.services.chat_privacy import ChatPrivacyCoordinator
from app.services.chat_quality import ChatQualityPolicy
from app.services.chat_quality_shadow import ChatQualityShadowService
from app.services.chat_readonly_execution import ChatReadonlyExecutionService
from app.services.chat_response import ChatResponseCoordinator
from app.services.chat_route_resolution import ChatRouteResolutionService
from app.services.chat_safety import ChatTurnAccessPolicy
from app.services.chat_steering import ChatSteeringCoordinator
from app.services.chat_tasks import ChatTaskCoordinator, ChatTurnOrchestrator
from app.services.chat_direct_routes_runtime import ChatDirectRoutesRuntime
from app.services.chat_turn_execution import ChatTurnExecutionOrchestrator
from app.services.chat_turn_finalize import ChatTurnFinalizeService
from app.services.chat_turn_input_facts import (
    looks_like_explicit_continuation as _looks_like_explicit_continuation,
    needs_recent_history_lookup as _needs_recent_history_lookup,
    looks_like_execution_state_explanation_request as _looks_like_execution_state_explanation_request,
    looks_like_latest_instruction_override as _looks_like_latest_instruction_override,
    looks_like_plain_analysis_request as _looks_like_plain_analysis_request,
    looks_like_short_followup as _looks_like_short_followup,
    format_sensitive_chat_request as _format_sensitive_chat_request,
    looks_like_voice_reply_request as _looks_like_voice_reply_request,
)
from app.services.context_budget import ContextBudgetService
from app.services.context_visibility import ContextVisibilityService
from app.services.context_gateway import RuntimeContextGateway
from app.services.conversation_understanding_runtime import ConversationUnderstandingRuntimeService
from app.services.memory import MemoryCommandResult, MemoryService
from app.services.model_fallback_runtime import ModelFallbackRuntime
from app.services.model_gateway import ModelProtocolGateway
from app.services.model_routing import ModelRoutingService
from app.services.natural_chat import (
    NaturalChatActionGateway,
    pending_action_from_approval,
    reset_visible_redaction_profile,
    response_plan_for_pending_action,
    set_visible_redaction_profile,
)
from app.services.presence_state import PresenceStateResolverService
from app.services.response_policy import ResponsePolicyService
from app.services.safety_policy import RuntimeSafetyPolicyService
from app.services.secrets import SecretStore
from app.services.session_context import SessionContextCuratorService
from app.services.silent_continuity import SilentContinuityService
from app.services.turn_events import TurnEventStore
from app.services.turn_execution import TurnExecutionManager
from app.services.turn_recovery import TurnRecoveryResult, TurnRecoveryService

DEFAULT_USER_ID = "user_local_owner"


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
        chat_quality_shadow_service: ChatQualityShadowService | None = None,
        conversation_understanding_service: ConversationUnderstandingRuntimeService | None = None,
        presence_state_service: PresenceStateResolverService | None = None,
        session_context_service: SessionContextCuratorService | None = None,
        response_policy_service: ResponsePolicyService | None = None,
        action_dialogue_mapper_service: ActionDialogueMapperService | None = None,
        silent_continuity_service: SilentContinuityService | None = None,
        chat_run_ledger_service: Any | None = None,
        failure_experience_service: FailureExperienceService | None = None,
        chat_hook_runtime: ChatHookRuntime | None = None,
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
        self._chat_quality_shadow = chat_quality_shadow_service
        self._conversation_understanding = conversation_understanding_service
        self._presence_state = presence_state_service
        self._session_context = session_context_service
        self._response_policy_runtime = response_policy_service
        self._action_dialogue_mapper = action_dialogue_mapper_service
        self._silent_continuity = silent_continuity_service
        self._chat_run_ledger_service = chat_run_ledger_service
        self._failure_experience = failure_experience_service
        self._chat_hook_runtime = chat_hook_runtime
        self._context_budget_runtime = ContextBudgetService()
        self._context_visibility_runtime = ContextVisibilityService()
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
        self._runtime_impl = ChatRuntime(self)
        self._model_router = ModelRouter()
        self._model_coordinator = ChatModelCoordinator()
        self._model_gateway = ModelProtocolGateway(
            secret_store=secret_store,
            client_cls=OpenAICompatibleClient,
        )
        self._model_fallback_runtime = ModelFallbackRuntime()
        self._privacy = ChatPrivacyCoordinator(model_coordinator=self._model_coordinator)
        self._composer = ResponseComposer()
        self._quality = ChatQualityPolicy(composer=self._composer)
        self._memory_coordinator = ChatMemoryCoordinator()
        self._task_coordinator = ChatTaskCoordinator()
        self._context_coordinator = ChatContextCoordinator()
        self._continuation = ChatContinuationCoordinator()
        self._steering = ChatSteeringCoordinator()
        self._response_coordinator = ChatResponseCoordinator()
        self._turn_orchestrator = ChatTurnOrchestrator()
        self._turn_execution_orchestrator = ChatTurnExecutionOrchestrator()
        self._turn_finalize = ChatTurnFinalizeService()
        self._model_execution = ChatModelExecutionService()
        self._access_policy = ChatTurnAccessPolicy()
        self._intent_router = ChatIntentRouter()
        self._capability_boundary = ChatCapabilityBoundaryService()
        self._route_resolution = ChatRouteResolutionService()
        self._readonly_execution = ChatReadonlyExecutionService(tool_runtime=tool_runtime)
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
            failure_experience_service=failure_experience_service,
            context_budget_service=self._context_budget_runtime,
            context_visibility_service=self._context_visibility_runtime,
        )
        # Phase 1 compatibility layer: readonly/direct route bodies live here now,
        # while chat.py keeps route dispatch and runtime wiring only.
        self._direct_routes_runtime = ChatDirectRoutesRuntime(
            composer=self._composer,
            tool_runtime=self._tool_runtime,
            readonly_execution=self._readonly_execution,
            task_engine=self._task_engine,
            capability_boundary=self._capability_boundary,
            task_coordinator=self._task_coordinator,
            approval_service=self._approval_service,
            turn_recovery=self._turn_recovery,
            emit_and_record=self._emit_and_record,
            complete_without_model=self._runtime_impl._complete_without_model,
            fail_turn=self._fail_turn,
            response_plan_for_status=self._response_plan_for_status,
            response_plan_for_action_status=self._response_plan_for_action_status,
            action_status_facts_for_turn=self._action_status_facts_for_turn,
            enabled_office_skill_id=self._enabled_office_skill_id,
            office_skill_has_grant=self._office_skill_has_grant,
            latest_office_artifact_id=self._latest_office_artifact_id,
            office_missing_capability_text=self._office_missing_capability_text,
            office_next_actions=self._office_next_actions,
            office_task_reply=self._office_task_reply,
            office_artifact_refs=_office_artifact_refs,
            recover_task_in_turn=self._recover_task_in_turn,
            host_filesystem_list_reply=_host_filesystem_list_reply,
            host_filesystem_list_error_reply=_host_filesystem_list_error_reply,
            browser_read_page_reply=_browser_read_page_reply,
            browser_read_page_error_reply=_browser_read_page_error_reply,
            browser_read_page_payload=_browser_read_page_payload,
            terminal_command_reply=_terminal_command_reply,
            terminal_command_error_reply=_terminal_command_error_reply,
            record_failure_experience=(
                failure_experience_service.record_failure
                if failure_experience_service is not None
                else None
            ),
        )
        self._execution = TurnExecutionManager(self._runtime_impl.run_turn)
        self._runtime_impl._bind_context(self)

    async def create_turn(
        self,
        request: ChatTurnRequest,
        *,
        retry_of_turn_id: str | None = None,
    ) -> ChatTurnResponse:
        return await self._runtime_impl.create_turn(
            request,
            retry_of_turn_id=retry_of_turn_id,
        )

    async def stream_turn_events(self, turn_id: str) -> AsyncIterator[ChatEvent]:
        async for event in self._runtime_impl.stream_turn_events(turn_id):
            yield event

    async def run_turn(self, turn_id: str) -> None:
        await self._runtime_impl.run_turn(turn_id)

    async def recover_incomplete_turns(self) -> int:
        return await self._runtime_impl.recover_turns()

    async def cancel_turn(self, turn_id: str) -> ChatTurnResponse:
        return await self._runtime_impl.cancel_turn(turn_id)

    async def retry_turn(self, turn_id: str) -> ChatTurnResponse:
        return await self._runtime_impl.retry_turn(turn_id)

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
        return self._runtime_impl.placeholder_events(turn_id)

    async def _chat_payloads_for_turn(self, turn: dict[str, Any]) -> dict[str, Any]:
        envelope = await self._chat_repo.get_message_envelope_by_turn(turn["turn_id"])
        queue_item = await self._chat_repo.get_queue_item_by_turn(turn["turn_id"])
        payloads: dict[str, Any] = {}
        if envelope is not None:
            payloads["content"] = _content_payload(envelope)
        if queue_item is not None:
            payloads["queue"] = _queue_payload(queue_item)
        presence_runtime = turn.get("presence_runtime")
        if not presence_runtime:
            stored_presence = await self._chat_repo.get_turn_presence_state(turn["turn_id"])
            if stored_presence is not None:
                presence_runtime = {
                    "understanding": stored_presence.get("understanding") or {},
                    "presence_state": stored_presence.get("presence_state") or {},
                    "session_context": stored_presence.get("session_context") or {},
                    "response_policy": stored_presence.get("response_policy") or {},
                    "action_dialogue": stored_presence.get("action_dialogue") or {},
                }
        if presence_runtime:
            payloads["presence_runtime"] = _grouped_presence_runtime(
                {
                    **dict(presence_runtime),
                    "current_user_text": str(turn.get("current_user_text") or ""),
                }
            )
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
        async for event in self._maybe_record_context_compaction_impl(
            turn,
            context,
            context_filter_summary,
            root_span_id,
            emit,
        ):
            yield event

    async def _maybe_record_context_compaction_impl(
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
        if self._silent_continuity is not None:
            await self._silent_continuity.capture_compaction(
                turn=turn,
                summary_text=summary,
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
        async for event in self._model_execution.call_model(
            self,
            turn,
            events,
            context,
            user_text,
            brain,
            model_params,
            root_span_id,
            cancel_token,
            intent=intent,
            mode=mode,
            fallback_used=fallback_used,
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
            async for model_event in self._model_gateway.stream_chat(brain, request, token):
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
            prompt_payload = _prompt_payload_from_metadata(prompt_metadata)
            if not prompt_payload and mode in {
                TaskMode.DIRECT.value,
                TaskMode.DIRECT_WITH_MEMORY.value,
            }:
                prompt_payload = {
                    "prompt_profile": "plain_chat",
                    "prompt_mode": "minimal" if mode == TaskMode.DIRECT.value else "full",
                }
            response_plan = response_plan.model_copy(
                update={
                    **self._response_coordinator.normalize_plan_text(response_plan, text),
                    "structured_payload": {
                        **response_plan.structured_payload,
                        **prompt_payload,
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
        text = self._response_coordinator.final_text(response_plan, text)
        if intent == "boundary_question":
            boundary_notice = (
                "我是本地智能体成员，不是真人，也没有隐藏账号或绕过系统的能力；"
                "不可能私下替你登录别人的账号，或者跳过授权直接拿到隐藏入口。"
                "登录、工具、文件、浏览器和外部动作都得先走安全流程，"
                "该确认的地方我会先停一下，并把可做与不可做的边界说清楚。"
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
        response_plan, shadow_trace = self._decorate_chat_quality_shadow(
            turn,
            response_plan,
            assistant_text=text,
            turn_status="completed",
            clarification_decision=(
                clarification_decision.as_payload() if clarification_decision is not None else None
            ),
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
        response_plan, text = await self._apply_before_finalize_hook(
            turn=turn,
            response_plan=response_plan,
            assistant_text=text,
            turn_status="completed",
        )
        response_plan = self._response_coordinator.finalize_plan(
            response_plan,
            text,
            authoritative_text=text,
            response_filter=merged_filter,
        )
        text = response_plan.plain_text
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
                "chat_quality_shadow": shadow_trace,
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
        if self._silent_continuity is not None:
            user_message = await self._chat_repo.get_message(turn["user_message_id"])
            user_text = str(user_message.get("content_text") if user_message else "")
            await self._silent_continuity.capture_turn(
                turn=turn,
                user_text=user_text,
                assistant_text=text,
                presence_payload=dict(turn.get("presence_runtime") or {}),
                response_plan=response_plan.model_dump(mode="json"),
                status="completed",
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

    async def _apply_before_finalize_hook(
        self,
        *,
        turn: dict[str, Any],
        response_plan: ResponsePlan,
        assistant_text: str,
        turn_status: str,
    ) -> tuple[ResponsePlan, str]:
        if self._chat_hook_runtime is None:
            return response_plan, assistant_text
        hook_result = await self._chat_hook_runtime.run_before_finalize(
            {
                "trace_id": turn.get("trace_id"),
                "conversation_id": turn.get("conversation_id"),
                "turn_id": turn.get("turn_id"),
                "member_id": turn.get("member_id"),
                "session_id": turn.get("session_id"),
                "channel": "local",
                "payload": {
                    "plain_text": assistant_text,
                    "summary": response_plan.summary,
                    "response_plan": response_plan.model_dump(mode="json"),
                    "turn_status": turn_status,
                },
            }
        )
        if hook_result.get("blocked"):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "最终回复被 hook 治理阻断",
                status_code=422,
                details={"reason_code": hook_result.get("reason_code")},
            )
        rewritten = dict(hook_result.get("rewritten_payload") or {})
        active_text = str(rewritten.get("plain_text") or assistant_text)
        active_plan = response_plan
        if "response_plan" in rewritten and isinstance(rewritten["response_plan"], dict):
            active_plan = response_plan.model_copy(update=rewritten["response_plan"])
        elif "summary" in rewritten:
            active_plan = response_plan.model_copy(
                update={"summary": str(rewritten.get("summary") or active_text)}
            )
        return active_plan, active_text

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
        async for event in self._turn_finalize.fail_turn(
            self,
            turn,
            events,
            code,
            message,
            root_span_id,
            persist_assistant=persist_assistant,
            response_plan=response_plan,
        ):
            yield event

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
        response_plan = self._response_plan_for_status(
            turn,
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
        response_plan, _ = self._decorate_chat_quality_shadow(
            turn,
            response_plan,
            assistant_text=text,
            turn_status="cancelled",
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
        if self._chat_run_ledger_service is not None:
            await self._chat_run_ledger_service.record_channel_delivery(
                turn_id=turn["turn_id"],
                trace_id=turn.get("trace_id"),
                message_id=message_id,
                channel="local",
                summary=text[:400],
            )
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
        event = self._runtime_impl.event(
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
        if self._chat_run_ledger_service is not None:
            await self._chat_run_ledger_service.record_chat_event(
                turn_id=event.turn_id,
                trace_id=event.trace_id,
                event_type=event.event.value,
                payload=event.payload or {},
                created_at=event.timestamp.isoformat(),
            )
        return sequence

    async def _finalize_created_cancel(self, turn: dict[str, Any]) -> None:
        summary = self._style_visible_text(turn, "已停止生成。")
        response_plan = self._response_plan_for_status(
            turn,
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
        response_plan, _ = self._decorate_chat_quality_shadow(
            turn,
            response_plan,
            assistant_text=summary,
            turn_status="cancelled",
        )
        event = self._runtime_impl.event(
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

    def _presence_runtime_inputs(
        self,
        turn: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        presence_runtime = dict(turn.get("presence_runtime") or {})
        return (
            dict(presence_runtime.get("response_policy") or {}),
            dict(presence_runtime.get("session_context") or {}),
            dict(presence_runtime.get("action_dialogue") or {}),
        )

    def _response_plan_for_status(
        self,
        turn: dict[str, Any],
        *,
        summary: str,
        task_status: dict[str, Any] | None = None,
        approval_prompt: dict[str, Any] | None = None,
        artifact_refs: list[dict[str, Any]] | None = None,
        safety_notice: str | None = None,
        memory_notice: str | None = None,
        tool_notice: str | None = None,
        trace_refs: list[dict[str, Any]] | None = None,
    ) -> ResponsePlan:
        response_policy, session_context, action_dialogue = self._presence_runtime_inputs(
            turn
        )
        return self._composer.response_plan_for_status(
            summary=summary,
            task_status=task_status,
            approval_prompt=approval_prompt,
            artifact_refs=artifact_refs,
            safety_notice=safety_notice,
            memory_notice=memory_notice,
            tool_notice=tool_notice,
            trace_refs=trace_refs,
            response_policy=response_policy,
            session_context=session_context,
            action_dialogue=action_dialogue,
        )

    def _response_plan_for_action_status(
        self,
        turn: dict[str, Any],
        *,
        facts: dict[str, Any],
        task_status: dict[str, Any] | None = None,
        trace_refs: list[dict[str, Any]] | None = None,
    ) -> ResponsePlan:
        response_policy, session_context, _ = self._presence_runtime_inputs(turn)
        return self._composer.response_plan_for_action_status(
            facts=facts,
            task_status=task_status,
            trace_refs=trace_refs,
            response_policy=response_policy,
            session_context=session_context,
        )

    def _build_action_dialogue_decision(
        self,
        turn: dict[str, Any],
        *,
        status: str,
        route: str,
        action_label: str,
        target: str = "",
        detail_status: str = "",
        failure_reason: str = "",
        evidence_summary: str = "",
        reply_options: list[str] | None = None,
        approval_pending: bool = False,
        task_created: bool = False,
        tool_created: bool = False,
    ) -> dict[str, Any]:
        if self._action_dialogue_mapper is None:
            return {}
        status = canonical_action_status(status, default="requested")
        decision = self._action_dialogue_mapper.map(
            ActionDialogueFacts(
                action_label=action_label,
                target=target,
                detail_status=detail_status,
                failure_reason=failure_reason,
                evidence_summary=evidence_summary,
                reply_options=list(reply_options or []),
                route_semantics={"route": route},
                natural_interaction={"status": status},
                task_status={"status": canonical_action_status(detail_status or status)},
                approval_pending=approval_pending or status == "waiting_for_approval",
                task_created=task_created,
                tool_created=tool_created,
            )
        )
        return decision.model_dump(mode="json")

    def _action_status_facts_for_turn(
        self,
        turn: dict[str, Any],
        *,
        status: str,
        route: str,
        action_label: str,
        target: str = "",
        detail_status: str = "",
        failure_reason: str = "",
        evidence_summary: str = "",
        reply_options: list[str] | None = None,
        approval_pending: bool = False,
        task_created: bool = False,
        tool_created: bool = False,
    ) -> dict[str, Any]:
        canonical_status = canonical_action_status(status, default="requested")
        detail_canonical = canonical_action_status(detail_status or canonical_status, default=canonical_status)
        reason_codes = status_reason_codes(
            "approval_required" if approval_pending else None,
            "task_created" if task_created else None,
            "tool_created" if tool_created else None,
            failure_reason if canonical_status in {"failed_with_reason", "blocked_by_boundary"} else None,
            "already_absent" if status == "already_absent" else None,
        )
        semantics = normalize_action_status_semantics(
            {
                "status": canonical_status,
                "scope": "workflow_summary",
                "reason_codes": reason_codes,
                "evidence_summary": evidence_summary,
                "approval_state": {
                    "status": "required" if approval_pending else "not_required",
                    "approval_id": None,
                },
                "task_ref": {
                    "status": detail_canonical,
                }
                if task_created
                else {},
                "tool_ref": {
                    "status": detail_canonical,
                }
                if tool_created
                else {},
                "failure_reason": failure_reason,
            },
            default_status=canonical_status,
            scope="workflow_summary",
        )
        return {
            "status": canonical_status,
            "action_type": route,
            "action_label": action_label,
            "target": target,
            "approval_required": approval_pending or canonical_status == "waiting_for_approval",
            "reply_options": list(reply_options or []),
            "reply_option_items": _reply_option_items(list(reply_options or [])),
            "detail_status": detail_canonical,
            "completed": detail_canonical == "completed_with_evidence" or canonical_status == "completed_with_evidence",
            "failed": detail_canonical == "failed_with_reason" or canonical_status in {"failed_with_reason", "blocked_by_boundary"},
            "failure_reason": failure_reason,
            "evidence_summary": evidence_summary,
            "route_semantics": {"route": route},
            "action_status_semantics": semantics,
            "action_dialogue": self._build_action_dialogue_decision(
                turn,
                status=canonical_status,
                route=route,
                action_label=action_label,
                target=target,
                detail_status=detail_canonical,
                failure_reason=failure_reason,
                evidence_summary=evidence_summary,
                reply_options=reply_options,
                approval_pending=approval_pending,
                task_created=task_created,
                tool_created=tool_created,
            ),
        }

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
            presence_runtime=dict(turn.get("presence_runtime") or {}),
            user_text=str(turn.get("current_user_text") or ""),
        )

    def _presence_failure_text(
        self,
        turn: dict[str, Any],
        code: ErrorCode,
        default_message: str,
    ) -> str:
        presence_runtime = dict(turn.get("presence_runtime") or {})
        response_policy = dict(presence_runtime.get("response_policy") or {})
        session_context = dict(presence_runtime.get("session_context") or {})
        latest_override = "latest_instruction_overrides_previous_goal" in [
            str(item) for item in session_context.get("current_open_loops") or []
        ]
        if code in {ErrorCode.MODEL_NOT_CONFIGURED, ErrorCode.MODEL_ROUTE_NOT_FOUND}:
            if latest_override:
                return "按你刚刚改的口径，我本来该直接接这句往下聊；只是我这边现在没有可用模型，没法正常展开。你可以稍后重试，或者先让我只给确定性的结论。"
            return "我这边现在没有可用模型，所以这题没法正常往下展开。你可以稍后重试，或者先让我只给确定性能说的部分。"
        if code == ErrorCode.CONTEXT_BUILD_FAILED:
            return "我刚才这一下没接稳，所以这轮没能顺着聊下去。你再发一句，我按你最新这句重接。"
        if code in {ErrorCode.TOOL_FAILED, ErrorCode.MCP_UNAVAILABLE, ErrorCode.TASK_STEP_FAILED}:
            prefix = "这一步刚才没跑通。"
            if str(response_policy.get("boundary_mode") or "") == "explicit_honest":
                return f"{prefix}{default_message}"
            return f"{prefix}我先把卡住的点说清楚：{default_message}"
        return default_message

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

    async def _build_presence_runtime_payload(
        self,
        *,
        turn: dict[str, Any],
        context: ContextPacket,
        user_text: str,
        privacy_level: str,
        brain_decision: Any | None,
    ) -> dict[str, Any]:
        if (
            self._conversation_understanding is None
            or self._presence_state is None
            or self._session_context is None
            or self._response_policy_runtime is None
            or self._action_dialogue_mapper is None
        ):
            return {}
        working_state = (
            await self._chat_experience.get_working_state(turn["conversation_id"])
            if self._chat_experience is not None
            else await self._chat_repo.get_working_state(turn["conversation_id"])
        ) or {}
        active_profile_row = await self._chat_repo.get_active_user_profile(turn["conversation_id"])
        user_profile = dict((active_profile_row or {}).get("profile_data") or {})
        latest_continuity = (
            await self._chat_repo.get_latest_continuity_snapshot(turn["conversation_id"])
        ) or {}
        active_commitments = await self._chat_repo.list_active_commitments(turn["conversation_id"])
        memory_candidates = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
            for item in list(context.memories or [])
        ]
        recent_messages = list(context.conversation.last_messages or [])
        pending_confirmation = dict(working_state.get("pending_confirmation") or {})
        pending_clarification = active_pending_clarification(
            working_state,
            session_id=str(turn.get("session_id") or "") or None,
        )
        understanding = self._conversation_understanding.analyze(
            ConversationUnderstandingRequest(
                turn_id=turn["turn_id"],
                conversation_id=turn["conversation_id"],
                member_id=turn["member_id"],
                user_text=user_text,
                message_type="multipart" if turn.get("has_multimodal_parts") else "text",
                channel_profile=self._shadow_channel_profile(turn, context),
                delivery_mode=str((turn.get("experience") or {}).get("client_context", {}).get("delivery_mode") or ""),
                sender_label=str(context.member.display_name or ""),
                has_multimodal_parts=bool(turn.get("has_multimodal_parts")),
                has_pending_action=bool(pending_confirmation),
                has_running_task=bool(working_state.get("candidate_actions")),
                latest_summary=str(context.conversation.recent_summary or ""),
                recent_messages=recent_messages,
                user_profile=user_profile,
                continuity_summary=str(latest_continuity.get("continuity_summary") or ""),
                trace_id=turn.get("trace_id"),
            )
        )
        presence_state = self._presence_state.resolve(
            PresenceStateRequest(
                turn_id=turn["turn_id"],
                conversation_id=turn["conversation_id"],
                member_id=turn["member_id"],
                user_text=user_text,
                understanding=understanding,
                recent_messages=recent_messages,
                working_state=working_state,
                memory_candidates=memory_candidates,
                user_profile=user_profile,
                latest_continuity=latest_continuity,
                trace_id=turn.get("trace_id"),
            )
        )
        session_context = self._session_context.curate(
            presence_state=presence_state,
            user_profile=user_profile,
            latest_continuity={
                **latest_continuity,
                "assistant_commitments": [
                    row.get("commitment_text")
                    for row in active_commitments
                    if row.get("commitment_text")
                ]
                or list(latest_continuity.get("assistant_commitments") or []),
            },
            recent_messages=recent_messages,
            memory_candidates=memory_candidates,
        )
        route_semantics = {
            "intent": (
                brain_decision.intent.primary_intent
                if brain_decision is not None
                else turn.get("intent")
            ),
            "mode": brain_decision.mode.mode if brain_decision is not None else turn.get("mode"),
            "route": (
                brain_decision.intent.primary_intent
                if brain_decision is not None
                else turn.get("intent")
            ),
        }
        action_dialogue = self._action_dialogue_mapper.map(
            ActionDialogueFacts(
                route_semantics=route_semantics,
                natural_interaction={
                    "status": "pending_action" if pending_confirmation else "none",
                    "questions": list(pending_confirmation.get("questions") or []),
                },
                task_status={
                    "status": (
                        "queued"
                        if working_state.get("candidate_actions")
                        else ""
                    ),
                },
                approval_pending=bool(pending_confirmation),
                task_created=bool(working_state.get("candidate_actions")),
            )
        )
        response_policy = self._response_policy_runtime.decide(
            ResponsePolicyRequest(
                understanding=understanding,
                presence_state=presence_state,
                response_plan={
                    "privacy_level": privacy_level,
                    "current_commitments": session_context.current_commitments,
                    "action_dialogue": action_dialogue.model_dump(mode="json"),
                },
                privacy_level=privacy_level,
            )
        )
        understanding_payload = understanding.model_dump(mode="json")
        presence_state_payload = presence_state.model_dump(mode="json")
        session_context_payload = session_context.model_dump(mode="json")
        response_policy_payload = response_policy.model_dump(mode="json")
        action_dialogue_payload = action_dialogue.model_dump(mode="json")
        rollout_state = _presence_rollout_state(
            understanding=understanding_payload,
            response_policy=response_policy_payload,
            action_dialogue=action_dialogue_payload,
            user_text=user_text,
        )
        response_policy_payload = {
            **response_policy_payload,
            "advisory_mode": rollout_state["advisory_mode"],
            "quality_takeover_scope": rollout_state["quality_takeover_scope"],
            "fallback_reason_codes": rollout_state["fallback_reason_codes"],
        }
        action_dialogue_payload = {
            **action_dialogue_payload,
            "quality_takeover_scope": rollout_state["quality_takeover_scope"],
        }
        response_driving_state = _presence_response_driving_state(
            pending_confirmation=pending_confirmation,
            working_state=working_state,
        )
        advisory_state = _presence_advisory_state(
            understanding=understanding_payload,
            presence_state=presence_state_payload,
            session_context=session_context_payload,
            response_policy=response_policy_payload,
            action_dialogue=action_dialogue_payload,
        )
        payload = {
            "understanding": understanding_payload,
            "presence_state": presence_state_payload,
            "session_context": session_context_payload,
            "response_policy": response_policy_payload,
            "action_dialogue": action_dialogue_payload,
            "context_budget": dict((turn.get("context_runtime") or {}).get("context_budget") or {}),
            "context_visibility": dict(
                (turn.get("context_runtime") or {}).get("context_visibility") or {}
            ),
            "advisory_mode": rollout_state["advisory_mode"],
            "quality_takeover_scope": rollout_state["quality_takeover_scope"],
            "fallback_reason_codes": rollout_state["fallback_reason_codes"],
            "quality_takeover_reason_fields": {
                "fallback_reason_codes": rollout_state["fallback_reason_codes"],
            },
            "response_driving_state": response_driving_state,
            "advisory_state": advisory_state,
            **_phase89_heuristic_runtime(
                user_text,
                pending_clarification_active=pending_clarification is not None,
                deterministic_boundary_reply=_deterministic_boundary_reply,
            ),
        }
        now = utc_now_iso()
        await self._chat_repo.upsert_turn_presence_state(
            {
                "presence_state_id": new_id("pres"),
                "turn_id": turn["turn_id"],
                "conversation_id": turn["conversation_id"],
                "understanding": payload["understanding"],
                "presence_state": payload["presence_state"],
                "session_context": payload["session_context"],
                "response_policy": payload["response_policy"],
                "action_dialogue": payload["action_dialogue"],
                "trace_id": turn.get("trace_id"),
                "created_at": now,
                "updated_at": now,
            }
        )
        return payload

    def _shadow_channel_profile(
        self,
        turn: dict[str, Any],
        context: ContextPacket,
    ) -> str | None:
        experience = dict(turn.get("experience") or {})
        client_context = experience.get("client_context")
        if isinstance(client_context, dict) and client_context.get("channel_profile"):
            return str(client_context["channel_profile"])
        source_ref = (
            context.workbench.source_refs[0]
            if context.workbench and context.workbench.source_refs
            else {}
        )
        content = dict(source_ref or {})
        if content.get("channel_profile"):
            return str(content["channel_profile"])
        return None

    def _decorate_chat_quality_shadow(
        self,
        turn: dict[str, Any],
        response_plan: ResponsePlan,
        *,
        assistant_text: str,
        turn_status: str | None = None,
        clarification_decision: dict[str, Any] | None = None,
    ) -> tuple[ResponsePlan, dict[str, Any]]:
        if self._chat_quality_shadow is None:
            return response_plan, {}
        plan, trace_payload = self._chat_quality_shadow.decorate_response_plan(
            response_plan=response_plan,
            assistant_text=assistant_text,
            shadow_state=turn.get("chat_quality_shadow"),
            privacy_level=str(turn.get("privacy_level") or ""),
            turn_status=turn_status,
            clarification_decision=clarification_decision,
        )
        return plan, trace_payload

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
                persona = (
                    profile.model_dump(mode="json")
                    if hasattr(profile, "model_dump")
                    else dict(profile)
                )
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

    def _with_voice_reply(
        self,
        response_plan: ResponsePlan,
        voice_reply: dict[str, Any],
    ) -> ResponsePlan:
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

    def _prompt_options_for_turn(
        self,
        *,
        turn: dict[str, Any],
        context: ContextPacket,
        user_text: str,
        intent: str,
        mode: str,
    ) -> dict[str, Any]:
        del turn
        explicit_continuation = _looks_like_explicit_continuation(user_text)
        latest_instruction_override = _looks_like_latest_instruction_override(user_text)
        plain_analysis_request = _looks_like_plain_analysis_request(user_text)
        needs_history_lookup = _needs_recent_history_lookup(user_text)
        short_followup = _looks_like_short_followup(user_text)
        recent_messages = list(getattr(context.conversation, "last_messages", []) or [])
        has_recent_history = bool(recent_messages)
        has_session_summary = bool(getattr(context.conversation, "recent_summary", None))
        plain_chat_intents = {
            "chat",
            "question_answer",
            "knowledge_answer",
            "complex_dialogue",
            "creative_writing",
            "memory_query",
            "memory_update",
            "memory_correction",
            "unknown",
        }
        richer_route_intents = {
            "task_request",
            "tool_request",
            "asset_management",
            "skill_request",
            "mcp_request",
            "system_settings",
            "system_filesystem_read",
            "browser_read",
            "boundary_question",
        }
        direct_plain_chat = (
            mode in {TaskMode.DIRECT.value, TaskMode.DIRECT_WITH_MEMORY.value}
            and intent in plain_chat_intents
        )
        strict_format_request = _format_sensitive_chat_request(user_text)
        direct_memory_route = (
            intent in {"memory_query", "memory_update", "memory_correction"}
            or mode == TaskMode.DIRECT_WITH_MEMORY.value
        )
        action_or_task_route = (
            mode not in {TaskMode.DIRECT.value, TaskMode.DIRECT_WITH_MEMORY.value}
            or intent in richer_route_intents
        )
        history_lookup_route = (
            (explicit_continuation and not latest_instruction_override)
            or needs_history_lookup
            or short_followup
        )

        if strict_format_request:
            return {
                "prompt_mode": "minimal",
                "prompt_profile": "strict_format",
                "dynamic_context_mode": "index",
                "include_dynamic_context": False,
                "include_trusted_context": False,
                "include_untrusted_context": False,
                "include_history": False,
                "include_session_summary": False,
                "recent_history_limit": 0,
            }

        if direct_memory_route:
            return {
                "prompt_mode": "full",
                "prompt_profile": "memory_snapshot",
                "dynamic_context_mode": "snapshot",
                "include_dynamic_context": True,
                "include_trusted_context": True,
                "include_untrusted_context": False,
                "include_history": has_recent_history or has_session_summary,
                "include_session_summary": has_session_summary,
                "recent_history_limit": 4,
            }

        if history_lookup_route:
            return {
                "prompt_mode": "full",
                "prompt_profile": "history_lookup",
                "dynamic_context_mode": "index",
                "include_dynamic_context": False,
                "include_trusted_context": False,
                "include_untrusted_context": False,
                "include_history": True,
                "include_session_summary": bool(has_session_summary and needs_history_lookup),
                "recent_history_limit": 4,
            }

        if (
            (direct_plain_chat or plain_analysis_request)
            and not history_lookup_route
        ):
            return {
                "prompt_mode": "minimal",
                "prompt_profile": "plain_chat",
                "dynamic_context_mode": "index",
                "include_dynamic_context": False,
                "include_trusted_context": False,
                "include_untrusted_context": False,
                "include_history": False,
                "include_session_summary": False,
                "recent_history_limit": 0,
            }

        if action_or_task_route:
            return {
                "prompt_mode": "full",
                "prompt_profile": "action_route",
                "dynamic_context_mode": "index",
                "include_dynamic_context": True,
                "include_trusted_context": True,
                "include_untrusted_context": True,
                "include_history": True,
                "include_session_summary": has_session_summary,
                "recent_history_limit": 10,
            }
        return {
            "prompt_mode": "full",
            "prompt_profile": "history_lookup" if has_recent_history else "plain_chat",
            "dynamic_context_mode": "index",
            "include_dynamic_context": False,
            "include_trusted_context": False,
            "include_untrusted_context": False,
            "include_history": has_recent_history,
            "include_session_summary": False,
            "recent_history_limit": 4,
        }

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

    def _message_user_text_from_message(self, message: dict[str, Any] | None) -> str:
        return _message_user_text(message)

    def _session_id_from_message(self, message: dict[str, Any] | None) -> str | None:
        return _session_id_from_message(message)

    def _content_payload_for_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return _content_payload(envelope)

    def _queue_payload_for_item(self, queue_item: dict[str, Any]) -> dict[str, Any]:
        return _queue_payload(queue_item)

    def _event_from_persisted_row(self, row: dict[str, Any]) -> ChatEvent:
        return _event_from_persisted(row)

    def _request_text_from_request(self, request: ChatTurnRequest) -> str:
        return _request_text(request)

    def _title_from_text(self, text: str) -> str:
        return _title_from_text(text)

    def _new_id(self, prefix: str) -> str:
        return new_id(prefix)

    def _debounce_delay_seconds(
        self,
        metadata: dict[str, Any],
        queue_policy: str,
    ) -> float:
        return _debounce_delay_seconds(metadata, queue_policy)

    def _queue_lock_until(self, seconds: int = 300) -> str:
        return _queue_lock_until(seconds)

    def _error_signature(self, stage: str, failure_type: str, root_cause: str) -> str:
        return _error_signature(stage, failure_type, root_cause)

    def _model_failure_type(self, error: ModelAdapterError | None) -> str:
        return _model_failure_type(error)

    def _context_redaction_summary(
        self,
        context: ContextPacket,
        *,
        sensitivity_hits: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        return self._context_coordinator.redaction_summary(
            context,
            sensitivity_hits=sensitivity_hits,
        )

    def _deterministic_boundary_reply_text(self, user_text: str) -> str | None:
        return _deterministic_boundary_reply(user_text)

    def _direct_route_reply_for_decision(
        self,
        route_type: str,
        user_text: str,
    ) -> tuple[str, str, dict[str, Any]] | None:
        return _direct_route_reply(route_type, user_text)

    def _phase52_deploy_or_install_explain_only(self, user_text: str) -> bool:
        return _phase52_deploy_or_install_explain_only(user_text)

    def _default_user_id(self) -> str:
        return DEFAULT_USER_ID

    def _pending_action_from_approval(
        self,
        approval: Any,
        *,
        session_id: str | None,
        source_turn_id: str,
    ) -> dict[str, Any]:
        return pending_action_from_approval(
            approval,
            session_id=session_id,
            source_turn_id=source_turn_id,
        )

    def _reply_option_items(self, options: list[str]) -> list[dict[str, str]]:
        return _reply_option_items(options)

    def _channel_profile_for_turn(self, turn: dict[str, Any]) -> str:
        return _channel_profile_for_turn(turn)

    def _prompt_payload_from_metadata(
        self,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return _prompt_payload_from_metadata(metadata)

    def _context_persona_payload(self, context: ContextPacket) -> dict[str, Any]:
        return (
            context.persona.model_dump(mode="json")
            if getattr(context, "persona", None) is not None
            and hasattr(context.persona, "model_dump")
            else {}
        )

    def _context_heart_payload(self, context: ContextPacket) -> dict[str, Any]:
        return (
            context.heart.model_dump(mode="json")
            if getattr(context, "heart", None) is not None
            and hasattr(context.heart, "model_dump")
            else {}
        )

    async def _execute_task_or_boundary(
        self,
        *,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        user_text: str,
        brain_decision: Any | None,
        privacy: Any,
        mode: TaskMode,
        session_id: str | None,
        root_span_id: str | None,
        intent: str,
    ) -> AsyncIterator[ChatEvent]:
        trace_id = turn["trace_id"]
        turn_id = turn["turn_id"]
        async def emit(event_type: ChatEventType, payload: dict[str, Any] | None = None) -> ChatEvent:
            return await self._emit_and_record(turn_id, trace_id, events, event_type, payload)
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
                        presence_runtime=dict(turn.get("presence_runtime") or {}),
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
                    response_plan = self._response_plan_for_action_status(
                        turn,
                        facts=self._action_status_facts_for_turn(
                            turn,
                            status="pending_action",
                            route=intent,
                            action_label=str(task.title or "这一步任务"),
                            target=str(task.title or ""),
                            reply_options=["只允许这一次", "拒绝", "修改目标为：..."],
                            approval_pending=True,
                            task_created=True,
                            detail_status=task.status.value,
                            evidence_summary="当前有一步操作需要你确认后才会继续。",
                        ),
                        task_status={
                            "task_id": task.task_id,
                            "status": task.status.value,
                            "mode": task.mode.value,
                        },
                    )
                    text = response_plan.plain_text or response_plan.summary or ""
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
                terminal_status = (
                    "failed"
                    if task.status.value in {"failed", "error", "cancelled"}
                    else ("completed" if task.status.value == "completed" else task.status.value)
                )
                response_plan = self._response_plan_for_action_status(
                    turn,
                    facts=self._action_status_facts_for_turn(
                        turn,
                        status=terminal_status,
                        route=intent,
                        action_label=str(task.title or "这一步任务"),
                        target=str(task.title or ""),
                        detail_status=task.status.value,
                        failure_reason=str(
                            presentation.safety_notice or presentation.tool_notice or ""
                        ),
                        evidence_summary=text,
                        task_created=True,
                    ),
                    task_status=presentation.task_status,
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
            async for event in self._runtime_impl._complete_without_model(
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
        async for event in self._runtime_impl._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent=intent,
            mode=mode.value,
            response_plan=response_plan,
        ):
            yield event

    async def _handle_missing_model_route(
        self,
        *,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        user_text: str,
        privacy: Any,
        brain_decision: Any | None,
        root_span_id: str | None,
        intent: str,
        mode: TaskMode,
    ) -> AsyncIterator[ChatEvent]:
        available_brains = await self._brains.list_routable_brains()
        route_resolution = self._privacy.model_route_resolution(
            available_brains,
            privacy.privacy_level,
        )
        code = ErrorCode(
            route_resolution.failure_code or ErrorCode.MODEL_ROUTE_NOT_FOUND.value
        )
        reason_codes = brain_decision.intent.reason_codes if brain_decision else []
        if code == ErrorCode.MODEL_NOT_CONFIGURED:
            deterministic_text = _deterministic_no_model_reply(user_text)
            if deterministic_text:
                response_plan = self._response_plan_for_status(
                    turn,
                    summary=deterministic_text,
                    safety_notice="当前没有可用模型；这次返回的是确定性说明，没有调用工具或创建任务。",
                )
                response_plan = response_plan.model_copy(
                    update={
                        "structured_payload": {
                            **response_plan.structured_payload,
                            "model_route_resolution": route_resolution.model_dump(mode="json"),
                            "route_semantics": {
                                "route": "deterministic_no_model_fallback",
                                "model_called": False,
                                "task_created": False,
                                "tool_created": False,
                                "model_not_required_reason": "deterministic_no_model_reply",
                            },
                        },
                    }
                )
                async for event in self._runtime_impl._complete_without_model(
                    turn,
                    events,
                    deterministic_text,
                    root_span_id,
                    intent=intent,
                    mode=mode.value,
                    response_plan=response_plan,
                ):
                    yield event
                return
        if (
            code == ErrorCode.MODEL_NOT_CONFIGURED
            and "phase51_advice_strategy_direct" in reason_codes
        ):
            text = _strategy_advice_fallback_text(user_text)
            response_plan = self._response_plan_for_status(
                turn,
                summary=text,
                task_status={"status": "not_created", "reason": "local_strategy_fallback"},
                safety_notice="没有可用模型时只给确定性建议；没有创建任务或调用工具。",
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "model_route_resolution": route_resolution.model_dump(mode="json"),
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
            async for event in self._runtime_impl._complete_without_model(
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
            response_plan = self._response_plan_for_status(
                turn,
                summary=boundary_text,
                safety_notice=boundary_text,
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "model_route_resolution": route_resolution.model_dump(mode="json"),
                    }
                }
            )
            async for event in self._runtime_impl._complete_without_model(
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
                object_id=turn["turn_id"],
                summary="高隐私输入阻止云端路由",
                risk_level=RiskLevel.R2,
                payload={"privacy_level": privacy.privacy_level},
                trace_id=turn["trace_id"],
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
            response_plan=self._response_plan_for_status(
                turn,
                summary=self._composer.compose_failure(code, "没有可用模型路由"),
            ).model_copy(
                update={
                    "structured_payload": {
                        "model_route_resolution": route_resolution.model_dump(mode="json"),
                        "route_semantics": {
                            "route": "model_route_unavailable",
                            "model_called": False,
                            "task_created": False,
                            "tool_created": False,
                        },
                    }
                }
            ),
        ):
            yield event

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
