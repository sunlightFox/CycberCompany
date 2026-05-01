from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_registry
from app.schemas.notifications import (
    InboundMessageCreateRequest,
    InboundMessageResponse,
    NotificationChannelCreateRequest,
    NotificationChannelListResponse,
    NotificationChannelTestRequest,
    NotificationChannelUpdateRequest,
    NotificationDeliveryAttemptListResponse,
    NotificationMessageCreateRequest,
    NotificationMessageListResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/notification", tags=["notification"])


@router.post("/channels")
async def create_channel(
    payload: NotificationChannelCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.notification_gateway_service.create_channel(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/channels", response_model=NotificationChannelListResponse)
async def list_channels(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> NotificationChannelListResponse:
    return NotificationChannelListResponse(
        items=await registry.notification_gateway_service.list_channels(
            status=status,
            limit=limit,
        )
    )


@router.patch("/channels/{channel_id}")
async def update_channel(
    channel_id: str,
    payload: NotificationChannelUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.notification_gateway_service.update_channel(
        channel_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: str,
    payload: NotificationChannelTestRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.notification_gateway_service.test_channel(
        channel_id,
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.post("/messages")
async def create_message(
    payload: NotificationMessageCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.notification_gateway_service.create_message(
        payload,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get("/messages", response_model=NotificationMessageListResponse)
async def list_messages(
    channel_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    registry: ServiceRegistry = Depends(get_registry),
) -> NotificationMessageListResponse:
    return NotificationMessageListResponse(
        items=await registry.notification_gateway_service.list_messages(
            channel_id=channel_id,
            status=status,
            limit=limit,
        )
    )


@router.post("/messages/{notification_id}/retry")
async def retry_message(
    notification_id: str,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
):
    return await registry.notification_gateway_service.retry_message(
        notification_id,
        trace_id=getattr(request.state, "trace_id", None),
    )


@router.get(
    "/messages/{notification_id}/attempts",
    response_model=NotificationDeliveryAttemptListResponse,
)
async def list_attempts(
    notification_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> NotificationDeliveryAttemptListResponse:
    return NotificationDeliveryAttemptListResponse(
        items=await registry.notification_gateway_service.list_attempts(notification_id)
    )


@router.post("/inbound", response_model=InboundMessageResponse)
async def receive_inbound(
    payload: InboundMessageCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> InboundMessageResponse:
    return InboundMessageResponse(
        **(
            await registry.notification_gateway_service.receive_inbound(
                payload,
                trace_id=getattr(request.state, "trace_id", None),
            )
        ).model_dump(mode="json")
    )


@router.get("/inbound/{inbound_message_id}", response_model=InboundMessageResponse)
async def get_inbound(
    inbound_message_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> InboundMessageResponse:
    return InboundMessageResponse(
        **(
            await registry.notification_gateway_service.get_inbound(inbound_message_id)
        ).model_dump(mode="json")
    )
