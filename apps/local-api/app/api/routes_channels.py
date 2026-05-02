from __future__ import annotations

from core_types import ErrorCode
from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.schemas.channels import (
    ChannelAccountListResponse,
    ChannelAttachmentListResponse,
    ChannelBindFinalizeResponse,
    ChannelBindStartRequest,
    ChannelBindStartResponse,
    ChannelBindStatusResponse,
    ChannelDeliveryBindingListResponse,
    ChannelInboundWechatRequest,
    ChannelInboundWechatResponse,
    ChannelPairingDecisionRequest,
    ChannelPairingDecisionResponse,
    ChannelPairingRequestListResponse,
    ChannelPeerListResponse,
    ChannelPeerRevokeRequest,
    ChannelPeerRevokeResponse,
    ChannelPeerSessionResponse,
    ChannelProviderHealthResponse,
    ChannelRevokeResponse,
    WechatGatewayHealthResponse,
    WechatGatewayPollResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.post("/bind-sessions", response_model=ChannelBindStartResponse)
async def start_bind_session(
    payload: ChannelBindStartRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelBindStartResponse:
    return await registry.channel_binding_service.start_bind(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/bind-sessions/{bind_session_id}", response_model=ChannelBindStatusResponse)
async def get_bind_session(
    bind_session_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelBindStatusResponse:
    return await registry.channel_binding_service.get_bind_status(
        bind_session_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/bind-sessions/{bind_session_id}/finalize",
    response_model=ChannelBindFinalizeResponse,
)
async def finalize_bind_session(
    bind_session_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelBindFinalizeResponse:
    return await registry.channel_binding_service.finalize_bind(
        bind_session_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/bind-sessions/{bind_session_id}/cancel", response_model=ChannelBindStatusResponse)
async def cancel_bind_session(
    bind_session_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelBindStatusResponse:
    session = await registry.channel_binding_service.cancel_bind(
        bind_session_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ChannelBindStatusResponse(**session.model_dump(mode="json"), events=[])


@router.post("/{channel_id}/revoke", response_model=ChannelRevokeResponse)
async def revoke_channel(
    channel_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelRevokeResponse:
    return await registry.channel_binding_service.revoke_channel(
        channel_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/peers", response_model=ChannelPeerListResponse)
async def list_peers(
    provider: str | None = Query(default=None),
    pairing_status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelPeerListResponse:
    return ChannelPeerListResponse(
        items=[
            ChannelPeerSessionResponse(**item)
            for item in await registry.channels.list_peer_sessions(
                provider=provider,
                pairing_status=pairing_status,
                limit=limit,
            )
        ]
    )


@router.get("/peers/{peer_id}", response_model=ChannelPeerSessionResponse)
async def get_peer(
    peer_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelPeerSessionResponse:
    peer = await registry.channels.get_peer_session(peer_id)
    if peer is None:
        raise AppError(ErrorCode.NOT_FOUND, "微信 peer 会话不存在", status_code=404)
    return ChannelPeerSessionResponse(**peer)


@router.get("/pairing-requests", response_model=ChannelPairingRequestListResponse)
async def list_pairing_requests(
    status: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelPairingRequestListResponse:
    return ChannelPairingRequestListResponse(
        items=await registry.channels.list_pairing_requests(
            status=status,
            provider=provider,
            limit=limit,
        )
    )


@router.post(
    "/pairing-requests/{pairing_request_id}/approve",
    response_model=ChannelPairingDecisionResponse,
)
async def approve_pairing_request(
    pairing_request_id: str,
    payload: ChannelPairingDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelPairingDecisionResponse:
    return await registry.wechat_gateway_service.approve_pairing(
        pairing_request_id,
        member_id=payload.member_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post(
    "/pairing-requests/{pairing_request_id}/deny",
    response_model=ChannelPairingDecisionResponse,
)
async def deny_pairing_request(
    pairing_request_id: str,
    payload: ChannelPairingDecisionRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelPairingDecisionResponse:
    return await registry.wechat_gateway_service.deny_pairing(
        pairing_request_id,
        member_id=payload.member_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/peers/{peer_id}/revoke", response_model=ChannelPeerRevokeResponse)
async def revoke_peer(
    peer_id: str,
    payload: ChannelPeerRevokeRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelPeerRevokeResponse:
    return await registry.wechat_gateway_service.revoke_peer(
        peer_id,
        member_id=payload.member_id,
        reason=payload.reason,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/attachments", response_model=ChannelAttachmentListResponse)
async def list_channel_attachments(
    channel_event_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelAttachmentListResponse:
    return ChannelAttachmentListResponse(
        items=await registry.channels.list_attachments(
            channel_event_id=channel_event_id,
            limit=limit,
        )
    )


@router.get("/delivery-bindings", response_model=ChannelDeliveryBindingListResponse)
async def list_delivery_bindings(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelDeliveryBindingListResponse:
    return ChannelDeliveryBindingListResponse(
        items=await registry.channels.list_delivery_bindings(status=status, limit=limit)
    )


@router.get("/accounts", response_model=ChannelAccountListResponse)
async def list_accounts(
    provider: str | None = None,
    status: str | None = Query(default=None),
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelAccountListResponse:
    return ChannelAccountListResponse(
        items=await registry.channel_binding_service.list_accounts(
            provider=provider,
            status=status,
        )
    )


@router.post("/inbound/wechat", response_model=ChannelInboundWechatResponse)
async def receive_wechat_inbound(
    payload: ChannelInboundWechatRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelInboundWechatResponse:
    return await registry.channel_binding_service.receive_wechat_inbound(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/providers/wechat/health", response_model=ChannelProviderHealthResponse)
async def wechat_health(
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelProviderHealthResponse:
    return await registry.channel_binding_service.provider_health("wechat")


@router.post("/providers/wechat/poll-once", response_model=WechatGatewayPollResponse)
async def wechat_poll_once(
    request: Request,
    limit: int | None = Query(default=None, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> WechatGatewayPollResponse:
    return await registry.wechat_gateway_service.poll_once(
        trace_id=getattr(request.state, "trace_id", None),
        limit=limit,
    )


@router.post("/providers/wechat/deliver-due", response_model=WechatGatewayPollResponse)
async def wechat_deliver_due(
    request: Request,
    limit: int = Query(default=20, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> WechatGatewayPollResponse:
    return await registry.wechat_gateway_service.deliver_due(
        trace_id=getattr(request.state, "trace_id", None),
        limit=limit,
    )


@router.get("/providers/wechat/gateway-health", response_model=WechatGatewayHealthResponse)
async def wechat_gateway_health(
    registry: ServiceRegistry = Depends(get_registry),
) -> WechatGatewayHealthResponse:
    return await registry.wechat_gateway_service.gateway_health()
