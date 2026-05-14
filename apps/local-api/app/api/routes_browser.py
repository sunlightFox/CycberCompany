from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.browser import (
    BrowserEvidenceResponse,
    BrowserPageStateListResponse,
    BrowserProfileActionRequest,
    BrowserProfileBindLocalCdpRequest,
    BrowserProfileBootstrapLoginRequest,
    BrowserProfileCreateRequest,
    BrowserProfileEventListResponse,
    BrowserProfileListResponse,
    BrowserProfileResponse,
    BrowserProfileUpdateRequest,
    BrowserSessionHealthCheckRequest,
    BrowserSessionHealthCheckResponse,
    BrowserSessionLoginProbeRequest,
    BrowserSessionRestoreContextRequest,
    BrowserSessionRestoreContextResponse,
    BrowserSessionCreateRequest,
    BrowserSessionListResponse,
    BrowserSessionResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/browser", tags=["browser"])


@router.post("/profiles", response_model=BrowserProfileResponse)
async def create_browser_profile(
    payload: BrowserProfileCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileResponse:
    profile = await registry.browser_session_service.create_profile(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserProfileResponse(**profile.model_dump(mode="json"))


@router.get("/profiles", response_model=BrowserProfileListResponse)
async def list_browser_profiles(
    status: str | None = None,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileListResponse:
    return BrowserProfileListResponse(
        items=await registry.browser_session_service.list_profiles(status=status)
    )


@router.get("/profiles/{browser_profile_id}", response_model=BrowserProfileResponse)
async def get_browser_profile(
    browser_profile_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileResponse:
    profile = await registry.browser_session_service.get_profile(browser_profile_id)
    return BrowserProfileResponse(**profile.model_dump(mode="json"))


@router.patch("/profiles/{browser_profile_id}", response_model=BrowserProfileResponse)
async def update_browser_profile(
    browser_profile_id: str,
    payload: BrowserProfileUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileResponse:
    profile = await registry.browser_session_service.update_profile(
        browser_profile_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserProfileResponse(**profile.model_dump(mode="json"))


@router.post("/profiles/{browser_profile_id}/activate", response_model=BrowserProfileResponse)
async def activate_browser_profile(
    browser_profile_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileResponse:
    profile = await registry.browser_session_service.activate_profile(
        browser_profile_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserProfileResponse(**profile.model_dump(mode="json"))


@router.post("/profiles/{browser_profile_id}/bind-local-cdp", response_model=BrowserProfileResponse)
async def bind_browser_profile_local_cdp(
    browser_profile_id: str,
    payload: BrowserProfileBindLocalCdpRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileResponse:
    profile = await registry.browser_session_service.bind_local_cdp(
        browser_profile_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserProfileResponse(**profile.model_dump(mode="json"))


@router.post("/profiles/{browser_profile_id}/bootstrap-login", response_model=BrowserSessionResponse)
async def bootstrap_browser_profile_login(
    browser_profile_id: str,
    payload: BrowserProfileBootstrapLoginRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserSessionResponse:
    session = await registry.browser_session_service.bootstrap_login(
        browser_profile_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserSessionResponse(**session.model_dump(mode="json"))


@router.post("/profiles/{browser_profile_id}/pause", response_model=BrowserProfileResponse)
async def pause_browser_profile(
    browser_profile_id: str,
    payload: BrowserProfileActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileResponse:
    profile = await registry.browser_session_service.pause_profile(
        browser_profile_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserProfileResponse(**profile.model_dump(mode="json"))


@router.post("/profiles/{browser_profile_id}/revoke", response_model=BrowserProfileResponse)
async def revoke_browser_profile(
    browser_profile_id: str,
    payload: BrowserProfileActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileResponse:
    profile = await registry.browser_session_service.revoke_profile(
        browser_profile_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserProfileResponse(**profile.model_dump(mode="json"))


@router.post("/profiles/{browser_profile_id}/clear", response_model=BrowserProfileResponse)
async def clear_browser_profile(
    browser_profile_id: str,
    payload: BrowserProfileActionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileResponse:
    profile = await registry.browser_session_service.clear_profile(
        browser_profile_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserProfileResponse(**profile.model_dump(mode="json"))


@router.post("/profiles/{browser_profile_id}/sessions", response_model=BrowserSessionResponse)
async def create_browser_session(
    browser_profile_id: str,
    payload: BrowserSessionCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
    ) -> BrowserSessionResponse:
    session = await registry.browser_session_service.create_session(
        browser_profile_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return BrowserSessionResponse(**session.model_dump(mode="json"))


@router.get("/profiles/{browser_profile_id}/sessions", response_model=BrowserSessionListResponse)
async def list_browser_sessions(
    browser_profile_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserSessionListResponse:
    return BrowserSessionListResponse(
        items=await registry.browser_session_service.list_sessions(browser_profile_id)
    )


@router.post(
    "/sessions/{browser_session_id}/health-check",
    response_model=BrowserSessionHealthCheckResponse,
)
async def health_check_browser_session(
    browser_session_id: str,
    payload: BrowserSessionHealthCheckRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserSessionHealthCheckResponse:
    return await registry.browser_session_service.health_check_session(
        browser_session_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/sessions/{browser_session_id}/probe-login-state",
    response_model=BrowserSessionHealthCheckResponse,
)
async def probe_browser_session_login_state(
    browser_session_id: str,
    payload: BrowserSessionLoginProbeRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserSessionHealthCheckResponse:
    return await registry.browser_session_service.probe_login_state(
        browser_session_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/sessions/{browser_session_id}/restore-context",
    response_model=BrowserSessionRestoreContextResponse,
)
async def restore_browser_session_context(
    browser_session_id: str,
    payload: BrowserSessionRestoreContextRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserSessionRestoreContextResponse:
    return await registry.browser_session_service.restore_context(
        browser_session_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get(
    "/sessions/{browser_session_id}/page-states",
    response_model=BrowserPageStateListResponse,
)
async def list_browser_session_page_states(
    browser_session_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserPageStateListResponse:
    return BrowserPageStateListResponse(
        items=await registry.browser_session_service.list_page_states(
            browser_session_id=browser_session_id
        )
    )


@router.get("/profiles/{browser_profile_id}/events", response_model=BrowserProfileEventListResponse)
async def list_browser_profile_events(
    browser_profile_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserProfileEventListResponse:
    return BrowserProfileEventListResponse(
        items=await registry.browser_session_service.list_profile_events(browser_profile_id)
    )


@router.get("/evidence/{browser_evidence_id}", response_model=BrowserEvidenceResponse)
async def get_browser_evidence(
    browser_evidence_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrowserEvidenceResponse:
    evidence = await registry.browser_session_service.get_evidence(browser_evidence_id)
    return BrowserEvidenceResponse(**evidence.model_dump(mode="json"))
