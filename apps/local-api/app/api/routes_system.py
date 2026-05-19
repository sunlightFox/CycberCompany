from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_registry
from app.schemas.system import (
    BootstrapStatus,
    MaturityDashboardResponse,
    ChatMainlineObservabilityResponse,
    ChatMainlineReadinessResponse,
    DesignGapsResponse,
    RuntimeContractsResponse,
    RuntimeTopologyComponent,
    RuntimeTopologyResponse,
    SessionRuntimeResponse,
    ToolRuntimeResponse,
)
from app.services.bootstrap import (
    DEFAULT_BRAIN_ID,
    DEFAULT_CONVERSATION_ID,
    DEFAULT_MEMBER_ID,
    DEFAULT_ORGANIZATION_ID,
    WELCOME_MESSAGE_ID,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/system", tags=["system"])


def _cleanup_details(
    *,
    role: str,
    allowed_to_grow: bool,
    host_files: list[str],
    delegates_to: list[str] | None = None,
    notes: list[str] | None = None,
    public_shell_retained: bool | None = None,
    internal_compat_removed: bool | None = None,
    retained_only_for_api_or_fixture_contract: bool | None = None,
    size_budget_lines: int | None = None,
    current_size_lines: int | None = None,
    growth_gate: str | None = None,
    ownership_split_status: str | None = None,
) -> dict[str, Any]:
    payload = {
        "role": role,
        "allowed_to_grow": allowed_to_grow,
        "host_files": host_files,
        "delegates_to": delegates_to or [],
        "notes": notes or [],
    }
    if public_shell_retained is not None:
        payload["public_shell_retained"] = public_shell_retained
    if internal_compat_removed is not None:
        payload["internal_compat_removed"] = internal_compat_removed
    if retained_only_for_api_or_fixture_contract is not None:
        payload["retained_only_for_api_or_fixture_contract"] = (
            retained_only_for_api_or_fixture_contract
        )
    if size_budget_lines is not None:
        payload["size_budget_lines"] = size_budget_lines
    if current_size_lines is not None:
        payload["current_size_lines"] = current_size_lines
    if growth_gate is not None:
        payload["growth_gate"] = growth_gate
    if ownership_split_status is not None:
        payload["ownership_split_status"] = ownership_split_status
    return payload


@router.get("/bootstrap-status", response_model=BootstrapStatus)
async def bootstrap_status(registry: ServiceRegistry = Depends(get_registry)) -> BootstrapStatus:
    return BootstrapStatus(
        shell_registered=await _exists(
            registry,
            "SELECT 1 FROM shells WHERE shell_id = ?",
            (registry.config.app.default_shell,),
        ),
        organization_ready=await _exists(
            registry,
            "SELECT 1 FROM organizations WHERE organization_id = ?",
            (DEFAULT_ORGANIZATION_ID,),
        ),
        default_brain_ready=await _exists(
            registry,
            "SELECT 1 FROM brains WHERE brain_id = ?",
            (DEFAULT_BRAIN_ID,),
        ),
        default_member_ready=await _exists(
            registry,
            "SELECT 1 FROM members WHERE member_id = ?",
            (DEFAULT_MEMBER_ID,),
        ),
        default_conversation_ready=await _exists(
            registry,
            "SELECT 1 FROM conversations WHERE conversation_id = ?",
            (DEFAULT_CONVERSATION_ID,),
        ),
        welcome_message_ready=await _exists(
            registry,
            "SELECT 1 FROM messages WHERE message_id = ?",
            (WELCOME_MESSAGE_ID,),
        ),
    )


@router.get("/runtime-contracts", response_model=RuntimeContractsResponse)
async def runtime_contracts(
    registry: ServiceRegistry = Depends(get_registry),
) -> RuntimeContractsResponse:
    return RuntimeContractsResponse(items=await registry.runtime_contract_service.list_contracts())


@router.get("/design-gaps", response_model=DesignGapsResponse)
async def design_gaps(
    registry: ServiceRegistry = Depends(get_registry),
) -> DesignGapsResponse:
    return DesignGapsResponse(items=await registry.runtime_contract_service.list_design_gaps())


@router.get("/runtime-topology", response_model=RuntimeTopologyResponse)
async def runtime_topology(
    registry: ServiceRegistry = Depends(get_registry),
) -> RuntimeTopologyResponse:
    session_runtime = await registry.session_runtime.diagnostic()
    task_runtime = registry.task_engine.runtime_diagnostic()
    tool_runtime = await registry.tool_runtime.diagnostic()
    memory_runtime = registry.memory_service.runtime_diagnostic()
    chat_runtime = registry.chat_runtime.diagnostic()
    channel_runtime = await registry.channel_ingress_runtime.diagnostic()
    channel_session_semantics = registry.channel_session_semantics_runtime.runtime_diagnostic()
    mcp_runtime = await registry.mcp_service.runtime_diagnostic()
    skill_runtime = await registry.skill_plugin_service.runtime_diagnostic()
    browser_workflow_runtime = registry.browser_workflow_runtime.diagnostic()
    chat_hook_runtime = registry.chat_hook_runtime.runtime_diagnostic()
    readiness = await registry.chat_mainline_readiness_service.diagnostic()
    phase85 = dict(
        dict(readiness.get("phase_readiness") or {}).get("phase85_execution_batches") or {}
    )
    phase85_details = dict(phase85.get("details") or {})
    phase91 = dict(
        dict(readiness.get("phase_readiness") or {}).get("phase91_host_decomposition_governance")
        or {}
    )
    phase91_details = dict(phase91.get("details") or {})
    phase91_components = {
        str(item.get("component") or ""): dict(item)
        for item in list(phase91_details.get("host_components") or [])
    }

    def _phase91_cleanup(component: str) -> dict[str, Any]:
        return dict(phase91_components.get(component) or {})
    return RuntimeTopologyResponse(
        items=[
            RuntimeTopologyComponent(
                name="session",
                runtime="session_runtime",
                dependencies=["chat_runtime", "turn_execution_manager"],
                status="runtime_native",
                details={
                    **session_runtime,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["session_runtime.py"],
                        delegates_to=["chat_runtime"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="chat_runtime",
                runtime="chat_runtime",
                dependencies=[
                    "chat_turn_execution_orchestrator",
                    "chat_turn_finalize_service",
                    "chat_model_execution_service",
                    "chat_direct_routes_runtime",
                    "turn_execution_manager",
                ],
                status="runtime_native",
                details={
                    **chat_runtime,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["runtime.py"],
                        delegates_to=[
                            "chat_turn_execution_orchestrator",
                            "chat_turn_finalize_service",
                            "chat_model_execution_service",
                            "chat_direct_routes_runtime",
                        ],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="chat_service",
                runtime="chat_service",
                dependencies=["chat_runtime"],
                status="compat_shell",
                details={
                    "runtime": "chat_service",
                    "maturity": "compat_shell",
                    "cleanup": _cleanup_details(
                        role="compat_shell",
                        allowed_to_grow=False,
                        host_files=["chat.py"],
                        delegates_to=["chat_runtime"],
                        notes=["retain as dependency host and compat facade only"],
                        public_shell_retained=True,
                        internal_compat_removed=True,
                        retained_only_for_api_or_fixture_contract=True,
                        size_budget_lines=_phase91_cleanup("chat_service").get("size_budget_lines"),
                        current_size_lines=_phase91_cleanup("chat_service").get("current_size_lines"),
                        growth_gate=_phase91_cleanup("chat_service").get("growth_gate"),
                        ownership_split_status=_phase91_cleanup("chat_service").get("ownership_split_status"),
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="natural_chat",
                runtime="natural_chat_surface",
                dependencies=["pending_action_resolution", "action_resolution_copy"],
                status="helper",
                details={
                    "runtime": "natural_chat_surface",
                    "maturity": "retained_helper",
                    "cleanup": _cleanup_details(
                        role="helper",
                        allowed_to_grow=False,
                        host_files=["natural_chat.py"],
                        delegates_to=[
                            "pending_action_resolution",
                            "action_resolution_copy",
                            "natural_chat_surface",
                        ],
                        notes=["runtime-facing natural chat surface should stay separate from pending resolution parsing"],
                        size_budget_lines=_phase91_cleanup("natural_chat").get("size_budget_lines"),
                        current_size_lines=_phase91_cleanup("natural_chat").get("current_size_lines"),
                        growth_gate=_phase91_cleanup("natural_chat").get("growth_gate"),
                        ownership_split_status=_phase91_cleanup("natural_chat").get("ownership_split_status"),
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="brain_decision",
                runtime="brain_decision_service",
                dependencies=[
                    "brain_route_decider",
                    "brain_mode_decider",
                    "brain_context_decider",
                    "brain_clarification_decider",
                ],
                status="helper",
                details={
                    "runtime": "brain_decision_service",
                    "maturity": "retained_helper",
                    "cleanup": _cleanup_details(
                        role="helper",
                        allowed_to_grow=False,
                        host_files=["brain_decision.py"],
                        delegates_to=[
                            "brain_route_decider",
                            "brain_mode_decider",
                            "brain_context_decider",
                            "brain_clarification_decider",
                        ],
                        notes=["brain decision host should remain an orchestrator, not a decision monolith"],
                        size_budget_lines=_phase91_cleanup("brain_decision").get("size_budget_lines"),
                        current_size_lines=_phase91_cleanup("brain_decision").get("current_size_lines"),
                        growth_gate=_phase91_cleanup("brain_decision").get("growth_gate"),
                        ownership_split_status=_phase91_cleanup("brain_decision").get("ownership_split_status"),
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="chat_hook_runtime",
                runtime="chat_hook_runtime",
                dependencies=[
                    "chat_runtime",
                    "tool_runtime",
                    "memory_service",
                    "chat_response_coordinator",
                ],
                status="runtime_native",
                details={
                    **chat_hook_runtime,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["chat_hook_runtime.py"],
                        delegates_to=[
                            "chat_runtime",
                            "tool_runtime",
                            "memory_service",
                            "channel_ingress_runtime",
                        ],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="channel_ingress",
                runtime="channel_ingress_runtime",
                dependencies=[
                    "channel_session_router",
                    "channel_session_semantics",
                    "session_runtime",
                    "chat_hook_runtime",
                ],
                status="runtime_native",
                details={
                    **channel_runtime,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["channel_ingress_runtime.py"],
                        delegates_to=["chat_hook_runtime", "session_runtime"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="channel_session_semantics",
                runtime="channel_session_semantics",
                dependencies=["channel_peer_sessions", "chat_ingress_metadata"],
                status="runtime_native",
                details={
                    **channel_session_semantics,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["channel_session_semantics.py"],
                        delegates_to=["channel_ingress_runtime"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="channel_stream_bridge",
                runtime="channel_stream_bridge",
                dependencies=["response_plan.plain_text"],
                status="runtime_native",
                details={
                    "runtime": "channel_stream_bridge",
                    "final_text_source": "response_plan_plain_text",
                    "fallback_source": "content_text",
                    "cleanup": _cleanup_details(
                        role="final_visible_delivery_bridge",
                        allowed_to_grow=False,
                        host_files=["channel_stream_bridge.py"],
                        delegates_to=["chat_response_coordinator"],
                        notes=["channel final visible text must come from response_plan.plain_text"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="turn_ledger",
                runtime="turn_ledger",
                dependencies=["chat_turn_ledgers", "chat_runtime"],
                status="runtime_native",
                details={
                    "runtime": "turn_ledger",
                    "ledger_native": True,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["chat_run_ledger.py"],
                        delegates_to=["chat_runtime"],
                        notes=["authoritative turn execution ledger for replay and diagnostics"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="memory_service",
                runtime="memory_service",
                dependencies=["context_gateway", "chat_run_ledger", "/api/memory/search"],
                status="runtime_native",
                details={
                    **memory_runtime,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["memory.py"],
                        delegates_to=["context_gateway", "chat_run_ledger"],
                        notes=["phase92 canonical long-term memory recall owner"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="run_ledger",
                runtime="run_ledger",
                dependencies=["chat_run_ledgers", "turn_ledger", "memory"],
                status="runtime_native",
                details={
                    "runtime": "run_ledger",
                    "ledger_native": True,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["chat_run_ledger.py"],
                        delegates_to=["chat_runtime", "memory_service"],
                        notes=["shared run timeline for chat replay, memory audit, and readiness"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="wechat_gateway",
                runtime="wechat_gateway",
                dependencies=[
                    "channel_session_context",
                    "channel_stream_bridge",
                    "channel_approval_bridge",
                    "channel_ingress_runtime",
                ],
                status="compat_shell",
                details={
                    **registry.channel_gateway("wechat").runtime_diagnostic(),
                    "cleanup": _cleanup_details(
                        role="compat_shell",
                        allowed_to_grow=False,
                        host_files=["wechat_gateway.py"],
                        delegates_to=[
                            "channel_ingress_runtime",
                            "channel_session_context",
                            "channel_stream_bridge",
                            "channel_approval_bridge",
                        ],
                        public_shell_retained=True,
                        internal_compat_removed=True,
                        retained_only_for_api_or_fixture_contract=True,
                        size_budget_lines=_phase91_cleanup("wechat_gateway").get("size_budget_lines"),
                        current_size_lines=_phase91_cleanup("wechat_gateway").get("current_size_lines"),
                        growth_gate=_phase91_cleanup("wechat_gateway").get("growth_gate"),
                        ownership_split_status=_phase91_cleanup("wechat_gateway").get("ownership_split_status"),
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="feishu_gateway",
                runtime="feishu_gateway",
                dependencies=[
                    "channel_session_context",
                    "channel_stream_bridge",
                    "channel_approval_bridge",
                    "channel_ingress_runtime",
                ],
                status="compat_shell",
                details={
                    **registry.channel_gateway("feishu").runtime_diagnostic(),
                    "cleanup": _cleanup_details(
                        role="compat_shell",
                        allowed_to_grow=False,
                        host_files=["feishu_gateway.py"],
                        delegates_to=[
                            "channel_ingress_runtime",
                            "channel_session_context",
                            "channel_stream_bridge",
                            "channel_approval_bridge",
                        ],
                        public_shell_retained=True,
                        internal_compat_removed=True,
                        retained_only_for_api_or_fixture_contract=True,
                        size_budget_lines=_phase91_cleanup("feishu_gateway").get("size_budget_lines"),
                        current_size_lines=_phase91_cleanup("feishu_gateway").get("current_size_lines"),
                        growth_gate=_phase91_cleanup("feishu_gateway").get("growth_gate"),
                        ownership_split_status=_phase91_cleanup("feishu_gateway").get("ownership_split_status"),
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="task",
                runtime="task_runtime",
                dependencies=[
                    "task_planning_runtime",
                    "task_workflow_runtime",
                    "task_agent_runtime",
                    "task_resume_runtime",
                ],
                status="runtime_native",
                details={
                    **task_runtime,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["tasks.py"],
                        delegates_to=[
                            "task_planning_runtime",
                            "task_workflow_runtime",
                            "task_agent_runtime",
                            "task_resume_runtime",
                        ],
                        notes=[
                            "task_agent_runtime is the authoritative phase96 agent loop owner",
                            "task runtime host remains large and should not absorb chat-quality logic",
                        ],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="tool",
                runtime="tool_runtime",
                dependencies=[
                    "tool_dispatcher",
                    "tool_safety_bridge",
                    "tool_terminal_runtime",
                    "terminal_queue_service",
                    "tool_mcp_runtime",
                ],
                status="implemented_with_fallback",
                details={
                    **tool_runtime,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["tools.py"],
                        delegates_to=[
                            "tool_dispatcher",
                            "tool_terminal_runtime",
                            "tool_browser_runtime",
                            "tool_mcp_runtime",
                        ],
                        notes=["tools.py is a legacy host and must not keep growing route-specific main logic"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="browser_workflow",
                runtime="browser_workflow_runtime",
                dependencies=[
                    "browser_intent_resolver",
                    "browser_plan_runtime",
                    "browser_session_runtime",
                    "browser_page_state_runtime",
                    "browser_replay_store",
                    "browser_executor",
                ],
                status="runtime_native",
                details={
                    **browser_workflow_runtime,
                    "cleanup": _cleanup_details(
                        role="runtime_native",
                        allowed_to_grow=True,
                        host_files=["browser_workflow_runtime.py"],
                        delegates_to=[
                            "browser_intent_resolver",
                            "browser_plan_runtime",
                            "browser_session_runtime",
                            "browser_page_state_runtime",
                            "browser_replay_store",
                        ],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="mcp",
                runtime="mcp_runtime",
                dependencies=[
                    "mcp_connection_runtime",
                    "mcp_policy_runtime",
                    "mcp_call_runtime",
                    "mcp_conversation_bridge",
                    "mcp_event_bridge",
                ],
                status="implemented_with_fallback",
                details={
                    **mcp_runtime,
                    "cleanup": _cleanup_details(
                        role="compat_shell",
                        allowed_to_grow=False,
                        host_files=["mcp.py"],
                        delegates_to=[
                            "mcp_connection_runtime",
                            "mcp_policy_runtime",
                            "mcp_call_runtime",
                            "mcp_conversation_bridge",
                            "mcp_event_bridge",
                        ],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="skill",
                runtime="skill_runtime",
                dependencies=[
                    "skill_installer",
                    "skill_registry",
                    "skill_runtime",
                    "skill_eval_runtime",
                ],
                status="compat_bridge",
                details={
                    **skill_runtime,
                    "cleanup": _cleanup_details(
                        role="compat_shell",
                        allowed_to_grow=False,
                        host_files=["skill_plugin.py"],
                        delegates_to=[
                            "skill_installer",
                            "skill_registry",
                            "skill_runtime",
                            "skill_eval_runtime",
                        ],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="chat_context_helper",
                runtime="chat_context_coordinator",
                dependencies=["chat_safety"],
                status="helper",
                details={
                    "runtime": "chat_context_coordinator",
                    "maturity": "retained_helper",
                    "cleanup": _cleanup_details(
                        role="helper",
                        allowed_to_grow=False,
                        host_files=["chat_context.py"],
                        delegates_to=["context_redaction_summary"],
                        notes=["retain only while context redaction diagnostics still need a named helper"],
                        public_shell_retained=True,
                        internal_compat_removed=True,
                        retained_only_for_api_or_fixture_contract=True,
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="chat_model_helper",
                runtime="chat_model_coordinator",
                dependencies=["chat_model_execution"],
                status="helper",
                details={
                    "runtime": "chat_model_coordinator",
                    "maturity": "retained_helper",
                    "cleanup": _cleanup_details(
                        role="helper",
                        allowed_to_grow=False,
                        host_files=["chat_model.py"],
                        delegates_to=["chat_model_execution"],
                        notes=[
                            "prompt assembly helper retained; must not become a second model runtime host",
                            "legacy chat_model_orchestration passthrough removed",
                        ],
                        public_shell_retained=True,
                        internal_compat_removed=True,
                        retained_only_for_api_or_fixture_contract=True,
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="chat_memory_helper",
                runtime="chat_memory_coordinator",
                dependencies=["memory_service"],
                status="helper",
                details={
                    "runtime": "chat_memory_coordinator",
                    "maturity": "retained_helper",
                    "cleanup": _cleanup_details(
                        role="helper",
                        allowed_to_grow=False,
                        host_files=["chat_memory.py"],
                        delegates_to=["memory_service"],
                        notes=["retain only for direct memory-command boundaries and notices"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="chat_response_helper",
                runtime="chat_response_coordinator",
                dependencies=["chat_visible_guard", "channel_stream_bridge"],
                status="helper",
                details={
                    "runtime": "chat_response_coordinator",
                    "maturity": "retained_helper",
                    "cleanup": _cleanup_details(
                        role="helper",
                        allowed_to_grow=False,
                        host_files=["chat_response.py"],
                        delegates_to=["response_composer_finalize_chain"],
                        notes=[
                            "formal visible-output coordinator; keep, but do not turn into a generic chat host",
                            "legacy finalize compat passthrough removed; finalize now stays on the main coordinator/finalize chain",
                        ],
                        public_shell_retained=True,
                        internal_compat_removed=True,
                        retained_only_for_api_or_fixture_contract=True,
                    ),
                    "visible_authority": "response_plan_plain_text",
                    "response_filter_standardized": True,
                },
            ),
            RuntimeTopologyComponent(
                name="chat_execution_batches",
                runtime="chat_execution_batches_control_plane",
                dependencies=[
                    "chat_mainline_readiness",
                    "release_gate_runtime",
                    "runtime_topology",
                ],
                status="control_plane_native",
                details={
                    "runtime": "chat_execution_batches_control_plane",
                    "execution_batches_version": phase85_details.get("execution_batches_version"),
                    "next_batch": phase85_details.get("next_batch"),
                    "covered_batches": phase85_details.get("covered_batches") or [],
                    "blocked_batches": phase85_details.get("blocked_batches") or [],
                    "compat_cleanup_window": phase85_details.get("compat_cleanup_window") or {},
                    "recommended_pr_order": phase85_details.get("recommended_pr_order") or [],
                    "cleanup": _cleanup_details(
                        role="control_plane_native",
                        allowed_to_grow=True,
                        host_files=["chat_mainline_readiness.py"],
                        delegates_to=["release.py", "runtime-topology", "docs/开发计划/85-第八十五阶段-聊天主链路实施任务拆解.md"],
                        notes=["authoritative implementation-batch control plane for phases 77-85"],
                    ),
                },
            ),
            RuntimeTopologyComponent(
                name="release",
                runtime="release_gate_runtime",
                dependencies=["release_report_builder"],
                status="implemented_with_fallback",
                details=registry.release_gate_runtime.diagnostic(),
            ),
            RuntimeTopologyComponent(
                name="skill_promotion",
                runtime="skill_promotion_runtime",
                dependencies=["skill_candidate_extractor"],
                status="implemented_with_fallback",
                details=registry.skill_promotion_runtime.diagnostic(),
            ),
        ]
    )


@router.get("/chat-mainline-readiness", response_model=ChatMainlineReadinessResponse)
async def chat_mainline_readiness(
    registry: ServiceRegistry = Depends(get_registry),
) -> ChatMainlineReadinessResponse:
    return ChatMainlineReadinessResponse(
        **(await registry.chat_mainline_readiness_service.diagnostic())
    )


@router.get("/chat-mainline-observability", response_model=ChatMainlineObservabilityResponse)
async def chat_mainline_observability(
    registry: ServiceRegistry = Depends(get_registry),
) -> ChatMainlineObservabilityResponse:
    return ChatMainlineObservabilityResponse(
        **(await registry.chat_mainline_readiness_service.mainline_observability())
    )


@router.get("/maturity-dashboard", response_model=MaturityDashboardResponse)
async def maturity_dashboard(
    registry: ServiceRegistry = Depends(get_registry),
) -> MaturityDashboardResponse:
    return MaturityDashboardResponse(
        **(await registry.chat_mainline_readiness_service.maturity_dashboard())
    )


@router.get("/session-runtime", response_model=SessionRuntimeResponse)
async def session_runtime(
    registry: ServiceRegistry = Depends(get_registry),
) -> SessionRuntimeResponse:
    return SessionRuntimeResponse(**await registry.session_runtime.diagnostic())


@router.get("/tool-runtime", response_model=ToolRuntimeResponse)
async def tool_runtime(
    registry: ServiceRegistry = Depends(get_registry),
) -> ToolRuntimeResponse:
    return ToolRuntimeResponse(**await registry.tool_runtime.diagnostic())


@router.get("/background-workers/health")
async def background_worker_health(
    registry: ServiceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    return registry.background_worker_service.health()


@router.post("/background-workers/tick")
async def background_worker_tick(
    worker_name: str | None = Query(default=None),
    registry: ServiceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    return await registry.background_worker_service.manual_tick(worker_name=worker_name)


async def _exists(registry: ServiceRegistry, sql: str, params: tuple[str, ...]) -> bool:
    row = await registry.db.fetch_one(sql, params)
    return row is not None
