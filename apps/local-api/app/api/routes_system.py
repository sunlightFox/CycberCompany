from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_registry
from app.schemas.system import (
    BootstrapStatus,
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
    channel_runtime = await registry.channel_ingress_runtime.diagnostic()
    mcp_runtime = await registry.mcp_service.runtime_diagnostic()
    return RuntimeTopologyResponse(
        items=[
            RuntimeTopologyComponent(
                name="session",
                runtime="session_runtime",
                dependencies=["chat_service", "turn_execution_manager"],
                status="implemented_with_fallback",
                details=session_runtime,
            ),
            RuntimeTopologyComponent(
                name="channel_ingress",
                runtime="channel_ingress_runtime",
                dependencies=["channel_session_router", "session_runtime"],
                status="implemented_with_fallback",
                details=channel_runtime,
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
                status="implemented_with_fallback",
                details=task_runtime,
            ),
            RuntimeTopologyComponent(
                name="tool",
                runtime="tool_runtime",
                dependencies=[
                    "tool_dispatcher",
                    "tool_safety_bridge",
                    "tool_terminal_runtime",
                    "tool_mcp_runtime",
                ],
                status="implemented_with_fallback",
                details=tool_runtime,
            ),
            RuntimeTopologyComponent(
                name="mcp",
                runtime="mcp_runtime",
                dependencies=[
                    "mcp_connection_runtime",
                    "mcp_policy_runtime",
                    "mcp_call_runtime",
                ],
                status="implemented_with_fallback",
                details=mcp_runtime,
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
