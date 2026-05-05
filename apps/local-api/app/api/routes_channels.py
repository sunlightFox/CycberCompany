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
    ChannelEventListResponse,
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
    FeishuBindCallbackResponse,
    FeishuGatewayHealthResponse,
    FeishuGatewayPollResponse,
    FeishuInboundRequest,
    FeishuInboundResponse,
    FeishuMessageOperationRequest,
    FeishuMessageOperationResponse,
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
    provider: str | None = Query(default=None),
    status: str | None = Query(default=None),
    turn_id: str | None = Query(default=None),
    channel_event_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelDeliveryBindingListResponse:
    return ChannelDeliveryBindingListResponse(
        items=await registry.channels.list_delivery_bindings(
            provider=provider,
            status=status,
            turn_id=turn_id,
            channel_event_id=channel_event_id,
            limit=limit,
        )
    )


@router.get("/events", response_model=ChannelEventListResponse)
async def list_channel_events(
    provider: str | None = Query(default=None),
    status: str | None = Query(default=None),
    channel_event_id: str | None = Query(default=None),
    trace_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelEventListResponse:
    return ChannelEventListResponse(
        items=await registry.channels.list_events(
            provider=provider,
            status=status,
            channel_event_id=channel_event_id,
            trace_id=trace_id,
            limit=limit,
        )
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
    response = await registry.channel_binding_service.receive_wechat_inbound(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )
    if payload.provider == "wechat" and response.status == "received":
        binding_status = (
            str((response.notification_inbound or {}).get("binding_status") or "")
            if response.notification_inbound is not None
            else ""
        )
        if not binding_status or binding_status == "no_pending_action":
            routed = await registry.wechat_gateway_service.route_received_wechat_inbound(
                request=payload,
                event=response.event.model_dump(mode="json"),
                trace_id=getattr(request.state, "trace_id", None),
            )
            response = response.model_copy(
                update={
                    "turn_id": routed.get("turn_id"),
                    "delivery_binding_id": routed.get("delivery_binding_id"),
                    "chat_turns_created": int(routed.get("chat_turns_created") or 0),
                    "delivery_status": routed.get("delivery_status"),
                    "diagnostic": {"chat_route": routed},
                }
            )
    return response


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
    return await registry.wechat_gateway_service.gateway_health(
        worker_health=registry.background_worker_service.health(),
    )


@router.post("/inbound/feishu", response_model=FeishuInboundResponse)
async def receive_feishu_inbound(
    payload: FeishuInboundRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> FeishuInboundResponse:
    result = await registry.feishu_gateway_service.receive_event(
        event={**payload.raw_event, "received_at": payload.received_at}
        if payload.received_at
        else payload.raw_event,
        channel_account_id=payload.channel_account_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return FeishuInboundResponse(status=result.status, diagnostic={"poll_result": result.model_dump(mode="json")})


@router.get("/providers/feishu/health", response_model=ChannelProviderHealthResponse)
async def feishu_health(
    registry: ServiceRegistry = Depends(get_registry),
) -> ChannelProviderHealthResponse:
    return await registry.channel_binding_service.provider_health("feishu")


@router.get("/providers/feishu/gateway-health", response_model=FeishuGatewayHealthResponse)
async def feishu_gateway_health(
    registry: ServiceRegistry = Depends(get_registry),
) -> FeishuGatewayHealthResponse:
    return await registry.feishu_gateway_service.gateway_health(
        worker_health=registry.background_worker_service.health(),
    )


@router.post("/providers/feishu/poll-once", response_model=FeishuGatewayPollResponse)
async def feishu_poll_once(
    request: Request,
    limit: int | None = Query(default=None, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> FeishuGatewayPollResponse:
    return await registry.feishu_gateway_service.poll_once(
        trace_id=getattr(request.state, "trace_id", None),
        limit=limit,
    )


@router.post("/providers/feishu/deliver-due", response_model=FeishuGatewayPollResponse)
async def feishu_deliver_due(
    request: Request,
    limit: int = Query(default=20, ge=1, le=500),
    registry: ServiceRegistry = Depends(get_registry),
) -> FeishuGatewayPollResponse:
    return await registry.feishu_gateway_service.deliver_due(
        trace_id=getattr(request.state, "trace_id", None),
        limit=limit,
    )


@router.post("/providers/feishu/operation", response_model=FeishuMessageOperationResponse)
async def feishu_message_operation(
    payload: FeishuMessageOperationRequest,
    request: Request,
    operation: str = Query(..., pattern="^(recall|read|reaction|history)$"),
    registry: ServiceRegistry = Depends(get_registry),
    ) -> FeishuMessageOperationResponse:
    result = await registry.feishu_gateway_service.message_operation(
        channel_account_id=str(payload.channel_account_id),
        operation=operation,
        message_id=payload.message_id,
        emoji_type=payload.emoji_type,
        container_id=payload.container_id,
        container_id_type=payload.container_id_type,
        page_size=payload.page_size,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return FeishuMessageOperationResponse(**result)


@router.get("/inbound/feishu/bind-callback", response_model=FeishuBindCallbackResponse)
async def feishu_bind_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str = Query(..., min_length=1),
    tenant_key: str | None = Query(default=None),
    open_id: str | None = Query(default=None),
    registry: ServiceRegistry = Depends(get_registry),
) -> FeishuBindCallbackResponse:
    return await registry.channel_binding_service.confirm_feishu_bind_callback(
        bind_session_id=state,
        code=code,
        tenant_key=tenant_key,
        open_id=open_id,
        trace_id=getattr(request.state, "trace_id", None),
    )
