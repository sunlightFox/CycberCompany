from __future__ import annotations

import json
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
from app.services.action_result_summary import summarize_completed_action_result
from app.services.asset_broker import AssetBrokerService
from app.services.audit import AuditEventService
from app.services.brain_decision import BrainDecisionService
from app.services.chat_capability_boundary import ChatCapabilityBoundaryService
from app.services.chat_context import ChatContextCoordinator
from app.services.chat_continuation import ChatContinuationCoordinator, ContinuationEvaluation
from app.services.chat_experience import ChatExperienceService
from app.services.chat_facade_shell import ChatFacadeShellMixin, DEFAULT_USER_ID
from app.services.failure_experience import FailureExperienceService
from app.services.chat_hook_runtime import ChatHookRuntime
from app.services.chat_ingress import ChatContentNormalizer, ChatIngressService
from app.services.chat_intent_router import ChatIntentRouter, ChatRouteDecision, office_skill_input
from app.services.chat_prompt_options import phase89_heuristic_runtime as _phase89_heuristic_runtime
from app.services.chat_runtime_host_helpers import (
    browser_capability_explanation_reply as _browser_capability_explanation_reply,
    browser_read_page_error_reply as _browser_read_page_error_reply,
    browser_read_page_payload as _browser_read_page_payload,
    browser_read_page_reply as _browser_read_page_reply,
    browser_visible_text as _browser_visible_text,
    content_payload as _content_payload,
    context_compaction_summary as _context_compaction_summary,
    debounce_delay_seconds as _debounce_delay_seconds,
    deterministic_boundary_reply as _deterministic_boundary_reply,
    deterministic_no_model_reply as _deterministic_no_model_reply,
    error_signature as _error_signature,
    event_from_persisted as _event_from_persisted,
    grouped_presence_runtime as _grouped_presence_runtime,
    host_filesystem_list_error_reply as _host_filesystem_list_error_reply,
    host_filesystem_label as _host_filesystem_label,
    host_filesystem_list_reply as _host_filesystem_list_reply,
    office_artifact_refs as _office_artifact_refs,
    presence_advisory_state as _presence_advisory_state,
    presence_response_driving_state as _presence_response_driving_state,
    presence_rollout_state as _presence_rollout_state,
    prompt_payload_from_metadata as _prompt_payload_from_metadata,
    queue_payload as _queue_payload,
    reply_option_items as _reply_option_items,
    strategy_advice_fallback_text as _strategy_advice_fallback_text,
    terminal_command_error_reply as _terminal_command_error_reply,
    terminal_command_reply as _terminal_command_reply,
    truncate_browser_text as _truncate_browser_text,
)
from app.services.chat_memory import ChatMemoryCoordinator
from app.services.chat_model import ChatModelCoordinator
from app.services.chat_model_execution import ChatModelExecutionService
from app.services.chat_pending_state import active_pending_clarification
from app.services.chat_privacy import ChatPrivacyCoordinator
from app.services.chat_quality import ChatQualityPolicy
from app.services.chat_quality_shadow import ChatQualityShadowService
from app.services.chat_readonly_execution import ChatReadonlyExecutionService
from app.services.browser_research_assessor import BrowserResearchAssessor
from app.services.browser_research_renderer import BrowserResearchRenderer
from app.services.browser_research_runtime import BrowserResearchRuntime
from app.services.browser_search_capability import BrowserSearchCapabilityAdapter
from app.services.chat_response import ChatResponseCoordinator
from app.services.chat_route_resolution import ChatRouteResolutionService
from app.services.chat_safety import ChatTurnAccessPolicy
from app.services.chat_steering import ChatSteeringCoordinator
from app.services.chat_visible_guard import preserve_visible_reply_contract
from app.services.chat_tasks import ChatTaskCoordinator, ChatTurnOrchestrator
from app.services.chat_direct_routes_runtime import ChatDirectRoutesRuntime
from app.services.chat_action_state import normalize_chat_action_state
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

class ChatService(ChatFacadeShellMixin):
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
        external_platform_action_service: Any | None = None,
        external_platform_adapter_service: Any | None = None,
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
        self._external_platform_actions = external_platform_action_service
        self._external_platform_adapters = external_platform_adapter_service
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
                external_platform_action_service=external_platform_action_service,
                external_platform_adapter_service=external_platform_adapter_service,
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
        self._browser_search_capability = BrowserSearchCapabilityAdapter(tool_runtime=tool_runtime)
        self._browser_research_runtime = BrowserResearchRuntime(
            search_capability=self._browser_search_capability,
            assessor=BrowserResearchAssessor(),
            renderer=BrowserResearchRenderer(),
        )
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
            browser_research=self._browser_research_runtime,
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
            raise AppError(ErrorCode.NOT_FOUND, "collect turn not found", status_code=404)
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
            raise ModelAdapterError(ErrorCode.TURN_CANCELLED, "generation cancelled")
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

    def _turn_requires_model_evidence(self, turn: dict[str, Any]) -> bool:
        plan = dict(
            turn.get("turn_execution_plan")
            or (turn.get("experience") or {}).get("turn_execution_plan")
            or {}
        )
        return bool(dict(plan.get("model_policy") or {}).get("required"))

    def _turn_should_finalize_without_model_reply_via_model(self, turn: dict[str, Any]) -> bool:
        experience = dict(turn.get("experience") or {})
        client_context = experience.get("client_context")
        context_values: list[str] = []
        if isinstance(client_context, dict):
            context_values.extend(
                str(client_context.get(key) or "")
                for key in (
                    "ui_mode",
                    "channel_profile",
                    "delivery_mode",
                    "provider",
                    "channel",
                )
            )
        ingress_metadata = experience.get("ingress_metadata")
        if isinstance(ingress_metadata, dict):
            raw_payload = ingress_metadata.get("raw_payload")
            if isinstance(raw_payload, dict):
                context_values.extend(
                    str(raw_payload.get(key) or "")
                    for key in ("provider", "channel", "source_channel")
                )
        plan = dict(
            turn.get("turn_execution_plan")
            or experience.get("turn_execution_plan")
            or {}
        )
        delivery_requirements = plan.get("delivery_requirements")
        if isinstance(delivery_requirements, dict):
            context_values.append(str(delivery_requirements.get("channel") or ""))
        channel_hint = " ".join(context_values).lower()
        return "feishu" in channel_hint or "wechat" in channel_hint

    async def _finalize_without_model_visible_text(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        text: str,
        root_span_id: str | None,
        *,
        intent: str,
        mode: str,
        response_plan: ResponsePlan | None = None,
    ) -> str:
        if any(item.get("event_type") == ChatEventType.MODEL_COMPLETED.value for item in events):
            return text
        if not self._turn_should_finalize_without_model_reply_via_model(turn):
            return text
        available_brains = await self._brains.list_routable_brains()
        if not available_brains:
            return text
        brain = available_brains[0]
        evidence_only = (
            mode in {TaskMode.DIRECT.value, TaskMode.DIRECT_WITH_MEMORY.value}
            and intent
            in {
                "chat",
                "question_answer",
                "knowledge_answer",
                "complex_dialogue",
                "creative_writing",
                "natural_chat",
                "memory_update",
                "memory_correction",
                "memory_query",
                "boundary_question",
                "scheduled_task_request",
                "clarification",
            }
        )
        evidence_payload = (
            dict(response_plan.structured_payload or {})
            if response_plan is not None
            else {}
        )
        if response_plan is not None:
            evidence_payload.update(
                {
                    "task_status": response_plan.task_status,
                    "tool_notice": response_plan.tool_notice,
                    "safety_notice": response_plan.safety_notice,
                    "memory_notice": response_plan.memory_notice,
                    "artifact_refs": response_plan.artifact_refs,
                    "approval_prompt": response_plan.approval_prompt,
                }
            )
        evidence_preview = _json_preview(redact(evidence_payload), limit=9000)
        user_text = str(turn.get("current_user_text") or "")
        messages = [
            {
                "role": "system",
                "content": (
                    "你是聊天回合的最终回复整理模型。只基于候选回复和结构化证据组织最终可见回复，"
                    "不要发明已经完成的动作，不要把等待确认、失败、受阻说成已完成。"
                    "保留安全边界、审批状态、记忆写入结果、工具/浏览器证据和文件产物事实。"
                    "用户要求保留的关键词、数字、错误码、英文原文和边界词必须保留，"
                    "例如不下载、依据、风险、404、Friday、Tuesday、真实模型等不要改写丢失。"
                    "回复要适合飞书/微信聊天，中文自然、简洁，但信息完整。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户请求：{user_text[:3000]}\n"
                    f"候选回复：{str(text or '')[:5000]}\n"
                    f"路由：intent={intent}, mode={mode}\n"
                    f"结构化证据(JSON，已脱敏)：{evidence_preview}"
                ),
            },
        ]
        trace_id = turn["trace_id"]
        turn_id = turn["turn_id"]
        token = self._events.token_for(turn_id)
        span_id = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MODEL_CALL,
            name="finalize non-model route visible response",
            parent_span_id=root_span_id,
            metadata={
                "brain_id": brain.get("brain_id"),
                "evidence_role": "non_model_route_final_response",
                "intent": intent,
                "mode": mode,
            },
            input_data={
                "message_count": len(messages),
                "candidate_chars": len(str(text or "")),
                "evidence_chars": len(evidence_preview),
            },
        )
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        output_parts: list[str] = []
        completed = False
        started_emitted = False

        async def complete_finalizer_without_stream(reason: str) -> str:
            nonlocal completed, finish_reason, started_emitted, usage
            fallback_request = ModelChatRequest(
                model=str(brain["model_name"]),
                messages=messages,
                temperature=0.2,
                max_output_tokens=768,
                top_p=0.9,
                timeout_seconds=90,
                stream=False,
                trace_id=trace_id,
                turn_id=turn_id,
                route_id=f"route_{brain['brain_id']}:non-model-finalizer:fallback",
                privacy_level=turn.get("privacy_level") or "medium",
                first_token_timeout_seconds=30,
                retry_count=1,
                metadata={
                    "evidence_role": "non_model_route_final_response",
                    "source_route_intent": intent,
                    "source_route_mode": mode,
                    "fallback_reason": reason,
                },
            )
            await self._emit_and_record(
                turn_id,
                trace_id,
                events,
                ChatEventType.MODEL_FALLBACK,
                {
                    "brain_id": brain["brain_id"],
                    "evidence_role": "non_model_route_final_response",
                    "reason": reason,
                },
            )
            if not started_emitted:
                started_emitted = True
                await self._emit_and_record(
                    turn_id,
                    trace_id,
                    events,
                    ChatEventType.MODEL_STARTED,
                    {
                        "brain_id": brain["brain_id"],
                        "evidence_role": "non_model_route_final_response",
                        "fallback_mode": "non_stream",
                    },
                )
            result = await self._model_gateway.complete_chat(brain, fallback_request, token)
            usage.update(result.usage)
            finish_reason = result.finish_reason or "stop"
            completed = True
            await self._emit_and_record(
                turn_id,
                trace_id,
                events,
                ChatEventType.MODEL_COMPLETED,
                {
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "evidence_role": "non_model_route_final_response",
                    "fallback_mode": "non_stream",
                },
            )
            return str(result.text or "").strip()

        try:
            request = ModelChatRequest(
                model=str(brain["model_name"]),
                messages=messages,
                temperature=0.2,
                max_output_tokens=768,
                top_p=0.9,
                timeout_seconds=90,
                stream=True,
                trace_id=trace_id,
                turn_id=turn_id,
                route_id=f"route_{brain['brain_id']}:non-model-finalizer",
                privacy_level=turn.get("privacy_level") or "medium",
                first_token_timeout_seconds=30,
                retry_count=1,
                metadata={
                    "evidence_role": "non_model_route_final_response",
                    "source_route_intent": intent,
                    "source_route_mode": mode,
                },
            )
            async for model_event in self._model_gateway.stream_chat(brain, request, token):
                if model_event.event == "started":
                    started_emitted = True
                    await self._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_STARTED,
                        {
                            "brain_id": brain["brain_id"],
                            "evidence_role": "non_model_route_final_response",
                        },
                    )
                elif model_event.event == "delta":
                    usage.update(model_event.usage)
                    if model_event.text:
                        output_parts.append(model_event.text)
                elif model_event.event == "usage_delta":
                    usage.update(model_event.usage)
                elif model_event.event == "completed":
                    usage.update(model_event.usage)
                    finish_reason = model_event.finish_reason or "stop"
                    completed = True
                    await self._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_COMPLETED,
                        {
                            "finish_reason": finish_reason,
                            "usage": usage,
                            "evidence_role": "non_model_route_final_response",
                        },
                    )
                    break
                elif model_event.event == "cancelled":
                    token.cancel()
                    break
                elif model_event.event == "failed":
                    usage.update(model_event.usage)
                    fallback_text = await complete_finalizer_without_stream(
                        model_event.error_code or "stream_failed"
                    )
                    if fallback_text:
                        output_parts = [fallback_text]
                    break
            if token.cancelled:
                return text
            final_text = "".join(output_parts).strip()
            if not completed:
                fallback_text = await complete_finalizer_without_stream("stream_incomplete")
                if fallback_text:
                    final_text = fallback_text
            await self._trace.end_span(
                span_id,
                output_data={
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "text_chars": len(final_text),
                    "completed": completed,
                },
            )
            if evidence_only:
                return text
            return final_text if completed and final_text else text
        except Exception as exc:
            if not token.cancelled and not completed:
                try:
                    final_text = await complete_finalizer_without_stream(type(exc).__name__)
                    await self._trace.end_span(
                        span_id,
                        output_data={
                            "finish_reason": finish_reason,
                            "usage": usage,
                            "text_chars": len(final_text),
                            "completed": completed,
                            "fallback_after_exception": True,
                        },
                    )
                    if evidence_only:
                        return text
                    return final_text or text
                except Exception as fallback_exc:
                    exc = fallback_exc
            await self._trace.end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error": str(redact(str(exc)))[:500]},
            )
            return text

    async def _ensure_required_model_evidence(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        text: str,
        root_span_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        if any(item.get("event_type") == ChatEventType.MODEL_COMPLETED.value for item in events):
            return
        available_brains = await self._brains.list_routable_brains()
        if not available_brains:
            raise ModelAdapterError(
                ErrorCode.MODEL_ROUTE_NOT_FOUND,
                "附件 turn 要求真实模型证据，但没有可用模型",
            )
        brain = available_brains[0]
        envelope = await self._chat_repo.get_message_envelope_by_turn(turn["turn_id"])
        safe_text = str(
            (envelope or {}).get("model_safe_text")
            or turn.get("current_user_text")
            or ""
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是附件任务完成前的证据确认模型。只基于用户消息和附件摘录，"
                    "用一句中文确认你已看到附件事实，不要编造新内容。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户请求：{turn.get('current_user_text') or ''}\n"
                    f"附件摘录：{safe_text[:6000]}\n"
                    f"候选完成回复：{text[:1000]}"
                ),
            },
        ]
        trace_id = turn["trace_id"]
        turn_id = turn["turn_id"]
        token = self._events.token_for(turn_id)
        span_id = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MODEL_CALL,
            name="required model evidence for channel attachment turn",
            parent_span_id=root_span_id,
            metadata={
                "brain_id": brain.get("brain_id"),
                "evidence_role": "channel_attachment_completion_gate",
            },
            input_data={"message_count": len(messages)},
        )
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        try:
            request = ModelChatRequest(
                model=str(brain["model_name"]),
                messages=messages,
                temperature=0.0,
                max_output_tokens=96,
                top_p=0.9,
                timeout_seconds=60,
                stream=True,
                trace_id=trace_id,
                turn_id=turn_id,
                route_id=f"route_{brain['brain_id']}:attachment-evidence",
                privacy_level=turn.get("privacy_level") or "medium",
                first_token_timeout_seconds=30,
                retry_count=1,
            )
            output_parts: list[str] = []
            async for model_event in self._model_gateway.stream_chat(brain, request, token):
                if model_event.event == "started":
                    yield await self._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_STARTED,
                        {
                            "brain_id": brain["brain_id"],
                            "evidence_role": "channel_attachment_completion_gate",
                        },
                    )
                elif model_event.event == "delta":
                    usage.update(model_event.usage)
                    if model_event.text:
                        output_parts.append(model_event.text)
                elif model_event.event == "usage_delta":
                    usage.update(model_event.usage)
                elif model_event.event == "completed":
                    usage.update(model_event.usage)
                    finish_reason = model_event.finish_reason or "stop"
                    yield await self._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_COMPLETED,
                        {
                            "finish_reason": finish_reason,
                            "usage": usage,
                            "evidence_role": "channel_attachment_completion_gate",
                        },
                    )
                    break
                elif model_event.event == "cancelled":
                    token.cancel()
                    break
            if token.cancelled:
                raise ModelAdapterError(ErrorCode.TURN_CANCELLED, "generation cancelled")
            await self._trace.end_span(
                span_id,
                output_data={
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "text_chars": len("".join(output_parts).strip()),
                },
            )
        except Exception:
            await self._trace.end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def _append_channel_attachment_fact_footer(
        self,
        turn: dict[str, Any],
        text: str,
    ) -> tuple[str, dict[str, Any] | None]:
        text = _sanitize_channel_attachment_visible_text(text)
        envelope = await self._chat_repo.get_message_envelope_by_turn(turn["turn_id"])
        user_text = (
            str((envelope or {}).get("content_text") or "")
            if isinstance(envelope, dict)
            else ""
        )
        raw_payload = (
            ((envelope or {}).get("ingress_metadata") or {}).get("raw_payload") or {}
            if isinstance(envelope, dict)
            else {}
        )
        understanding = raw_payload.get("multimodal_understanding")
        if not isinstance(understanding, dict):
            return text, None
        facts = _channel_attachment_standard_facts(understanding)
        if not facts:
            return text, None
        if _channel_attachment_rename_suggestion_request(user_text):
            text = _channel_attachment_rename_suggestion(user_text, facts)
        missing = [fact for fact in facts if fact["value"] and fact["value"] not in text]
        prompt_hints = _channel_attachment_prompt_hints(user_text, text, facts)
        missing.extend(prompt_hints)
        if "附件" not in text and "文件" not in text:
            missing.append({"label": "依据", "value": "基于附件/文件"})
        if not missing:
            return text, {"required_facts": facts, "appended_facts": []}
        footer = "附件事实：" + "；".join(
            f"{item['label']}：{item['value']}" for item in missing
        ) + "。"
        joined = f"{text.rstrip()}\n\n{footer}" if text.strip() else footer
        return joined, {
            "required_facts": facts,
            "appended_facts": missing,
            "source": "channel_attachment_understanding",
        }

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
        emit_final_delta: bool = False,
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
        response_plan = response_plan.model_copy(
            update={
                "structured_payload": {
                    **response_plan.structured_payload,
                    "current_user_text": str(turn.get("current_user_text") or ""),
                    "session_context": dict((turn.get("presence_runtime") or {}).get("session_context") or {}),
                }
            }
        )
        response_plan = self._response_coordinator.finalize_plan(
            response_plan,
            text,
            authoritative_text=text,
            response_filter=merged_filter,
        )
        text = response_plan.plain_text
        if intent == "boundary_question":
            boundary_notice = (
                "我是本地智能体成员，不是真人，也没有隐藏账号或绕过系统的能力。"
                "我不能私下替你登录别人的账号，也不能跳过授权直接拿到隐藏入口。"
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
        text, attachment_evidence = await self._append_channel_attachment_fact_footer(turn, text)
        user_text = str(turn.get("current_user_text") or "")
        if not user_text:
            user_message = await self._chat_repo.get_message(turn["user_message_id"])
            user_text = str(user_message.get("content_text") if user_message else "")
            if user_text:
                turn["current_user_text"] = user_text
        normalized_text_patch = self._response_coordinator.normalize_plan_text(
            response_plan,
            text,
        )
        response_plan = response_plan.model_copy(
            update={
                **normalized_text_patch,
                "structured_payload": {
                    **response_plan.structured_payload,
                    "current_user_text": user_text,
                    "session_context": dict((turn.get("presence_runtime") or {}).get("session_context") or {}),
                    **(
                        {"attachment_evidence": attachment_evidence}
                        if attachment_evidence is not None
                        else {}
                    ),
                }
            }
        )
        response_plan = self._response_coordinator.finalize_plan(
            response_plan,
            text,
            authoritative_text=text,
            response_filter=merged_filter,
        )
        text = response_plan.plain_text
        if emit_final_delta and text:
            yield await self._emit_and_record(
                turn["turn_id"],
                turn["trace_id"],
                events,
                ChatEventType.RESPONSE_DELTA,
                {
                    "text": text,
                    "response_filter": merged_filter,
                },
            )
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
        user_message = await self._chat_repo.get_message(turn["user_message_id"])
        user_text = str(user_message.get("content_text") if user_message else "")
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
                    "user_text": user_text,
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
                "message": "generation cancelled",
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
        summary = self._style_visible_text(turn, "generation cancelled")
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
            user_text=str(turn.get("current_user_text") or ""),
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
            user_text=str(turn.get("current_user_text") or ""),
            task_status=(
                {
                    **dict(task_status or {}),
                    "status": canonical_action_status(
                        dict(task_status or {}).get("status"),
                        default=dict(task_status or {}).get("status") or "requested",
                    )
                    if dict(task_status or {}).get("status") not in {"paused", "waiting_approval"}
                    else dict(task_status or {}).get("status"),
                }
                if task_status
                else None
            ),
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
        completed_summary = summarize_completed_action_result(
            label=action_label,
            target=target,
            result_summary=evidence_summary,
        )
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
            "completed_summary": completed_summary,
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
        user_text = str(turn.get("current_user_text") or "")
        presence_runtime = dict(turn.get("presence_runtime") or {})
        session_context = dict(presence_runtime.get("session_context") or {})
        recent_messages = session_context.get("relevant_recent_messages")
        visible = self._composer.style_text(
            text,
            ui_mode=self._ui_mode_for_turn(turn),
            response_plan=response_plan,
            presence_runtime=presence_runtime,
            user_text=user_text,
        )
        return preserve_visible_reply_contract(
            visible,
            user_text=user_text,
            recent_messages=recent_messages if isinstance(recent_messages, list) else None,
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
                return "按你刚刚改的口径，我本来该顺着继续聊；只是现在没有可用模型，没法正常展开。"
            return "我这边现在没有可用模型，所以这题没法正常往下展开。"
        if code == ErrorCode.CONTEXT_BUILD_FAILED:
            return "我刚才这一轮没接稳，你再发一句，我按你最新那句重接。"
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
            "娌″姙娉曠敤澹伴煶",
            "不能用声音",
            "无法用声音",
            "不能发语音",
            "无法发语音",
            "can't send voice",
            "cannot send voice",
        )
        if not any(marker in lowered for marker in refusal_markers):
            return assistant_text
        member_name = "小遥" if str(turn.get("member_id")) == "mem_xiaoyao" else "我"
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
        deep_history_lookup = any(
            marker in user_text
            for marker in ("一开始", "最开始", "开头", "这段对话", "对话的变化", "多轮小结")
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
                "recent_history_limit": 20 if deep_history_lookup else 4,
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
                        "route_intent": intent,
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
            presentation = self._task_coordinator.present_task_status(task)
            pending_action = None
            approval = None
            if self._approval_service is not None and task.current_approval_id:
                approval = await self._approval_service.get(task.current_approval_id)
                pending_action = pending_action_from_approval(
                    approval,
                    session_id=session_id,
                    source_turn_id=turn_id,
                )
            normalized_state = normalize_chat_action_state(
                task=task,
                approval=approval,
                route_kind=intent,
                recovery_payload=recovery.recovery_payload,
                pending_action=pending_action,
            )
            if normalized_state["task_status"] == "waiting_approval":
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
                            status="waiting_for_approval",
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
                            "status": normalized_state["task_status"],
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
                if presentation.event_type is not None:
                    yield await emit(
                        presentation.event_type,
                        presentation.event_payload,
                    )
                text = f"{recovery.response_prefix}{presentation.text}"
                terminal_status = normalized_state["action_status"]
                response_plan = self._response_plan_for_action_status(
                    turn,
                    facts=self._action_status_facts_for_turn(
                        turn,
                        status=terminal_status,
                        route=intent,
                        action_label=str(task.title or "这一步任务"),
                        target=str(task.title or ""),
                        detail_status=normalized_state["task_status"],
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
                            "task_status_semantics": {
                                **presentation.task_status,
                                "status": normalized_state["task_status"],
                            },
                            "recovery": recovery.recovery_payload,
                        },
                    }
                )
                if normalized_state["should_fail_turn"]:
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
        recent_messages: list[dict[str, Any]] | None = None
        route_decision = self._intent_router.decide(user_text)
        if route_decision.office_request is not None:
            async for event in self._direct_routes_runtime.handle_office_chat_request(
                turn,
                events,
                user_text,
                route_decision.office_request,
                root_span_id,
                trace_id=turn.get("trace_id"),
            ):
                yield event
            return
        if route_decision.route_type == "host_filesystem_list":
            async for event in self._direct_routes_runtime.handle_host_filesystem_list(
                turn,
                events,
                route_decision.metadata,
                root_span_id,
                trace_id=turn.get("trace_id"),
            ):
                yield event
            return
        if route_decision.route_type == "browser_read_page":
            async for event in self._direct_routes_runtime.handle_browser_read_page(
                turn,
                events,
                route_decision.metadata,
                root_span_id,
                trace_id=turn.get("trace_id"),
            ):
                yield event
            return
        if route_decision.route_type in {
            "browser_search_readonly",
            "browser_search_with_citation",
        }:
            async for event in self._direct_routes_runtime.handle_browser_search_readonly(
                turn,
                events,
                route_decision,
                root_span_id,
                trace_id=turn.get("trace_id"),
            ):
                yield event
            return
        if route_decision.route_type == "terminal_readonly_command":
            async for event in self._direct_routes_runtime.handle_terminal_readonly_command(
                turn,
                events,
                route_decision.metadata,
                root_span_id,
                trace_id=turn.get("trace_id"),
            ):
                yield event
            return
        if code == ErrorCode.MODEL_NOT_CONFIGURED:
            recent_messages = await self._chat_repo.list_recent_messages(
                turn["conversation_id"],
                limit=12,
            )
            deterministic_text = _deterministic_no_model_reply(
                user_text,
                recent_messages=recent_messages,
            )
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
            text = _strategy_advice_fallback_text(
                user_text,
                recent_messages=recent_messages,
            )
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
                "我不是隐藏真人账号，也不会绕过系统替你登录或直接操作。"
                "涉及登录、工具、文件、浏览器和外部动作时，我会先走安全流程，"
                "该确认的地方会停住等你点头。"
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


def _channel_attachment_standard_facts(understanding: dict[str, Any]) -> list[dict[str, str]]:
    attachments = understanding.get("attachments") or []
    if not isinstance(attachments, list):
        return []
    facts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        summary = str(attachment.get("summary_text") or "")
        if "标准事实：" in summary:
            summary = summary.split("标准事实：", 1)[1]
        if "原始抽取：" in summary:
            summary = summary.split("原始抽取：", 1)[0]
        for match in re.finditer(r"(项目|预算|风险|截止日期|负责人)：([^；。\n]+)", summary):
            label = match.group(1).strip()
            value = match.group(2).strip()
            if not label or not value:
                continue
            key = (label, value)
            if key in seen:
                continue
            seen.add(key)
            facts.append({"label": label, "value": value})
    return facts


def _channel_attachment_prompt_hints(
    user_text: str,
    response_text: str,
    facts: list[dict[str, str]],
) -> list[dict[str, str]]:
    prompt = str(user_text or "")
    combined = f"{prompt}\n{response_text}"
    fact_map = {item["label"]: item["value"] for item in facts if item.get("label") and item.get("value")}
    owner = fact_map.get("负责人") or "负责人"
    deadline = fact_map.get("截止日期") or "截止日期"
    risk = fact_map.get("风险") or "附件风险"
    hints: list[dict[str, str]] = []
    if "下一步" not in response_text and any(marker in combined for marker in ("简短同步", "转发", "老板", "同步如下")):
        hints.append({"label": "下一步", "value": f"{owner}在{deadline}前跟进{risk}"})
    if any(
        marker in combined
        for marker in ("英文", "English", "english", "Attachment Facts", "Qingteng Plan", "Beta supplier")
    ) and "June" not in response_text:
        hints.append({"label": "English date", "value": "June 15 (6月15日)"})
    if any(
        marker in combined
        for marker in ("英文", "English", "english", "Attachment Facts", "Qingteng Plan", "Beta supplier")
    ) and "Qingteng" not in response_text:
        hints.append({"label": "English project", "value": "Qingteng Plan (青藤计划)"})
    return hints


def _sanitize_channel_attachment_visible_text(text: str) -> str:
    clean = str(text or "")
    return clean.replace("task-report.md", "报告文件").replace("task-report", "报告文件")


def _json_preview(payload: Any, *, limit: int) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str, sort_keys=True)
    except TypeError:
        text = str(payload)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated]"


def _channel_attachment_rename_suggestion_request(user_text: str) -> bool:
    text = str(user_text or "")
    return any(
        marker in text
        for marker in ("标准文件名", "文件名建议", "命名建议", "不要声称已经改名", "不要声称已改名", "不要真的改名")
    )


def _channel_attachment_rename_suggestion(
    user_text: str,
    facts: list[dict[str, str]],
) -> str:
    fact_map = {item["label"]: item["value"] for item in facts if item.get("label") and item.get("value")}
    suffix = ".docx" if ".docx" in user_text.lower() else ".xlsx" if ".xlsx" in user_text.lower() else ""
    parts = [
        fact_map.get("项目") or "附件",
        "复盘",
        f"{fact_map['预算']}元" if fact_map.get("预算") else "",
        fact_map.get("截止日期") or "",
        fact_map.get("负责人") or "",
    ]
    stem = "_".join(part for part in parts if part)
    return (
        f"建议文件名：{stem}{suffix}。\n"
        "这是基于附件内容给出的命名建议，我没有执行重命名或改动原文件。"
    )
