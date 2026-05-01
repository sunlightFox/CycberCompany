from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_registry
from app.schemas.system import BootstrapStatus, DesignGapsResponse, RuntimeContractsResponse
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
