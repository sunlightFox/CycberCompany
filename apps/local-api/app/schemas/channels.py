from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    ChannelAccount,
    ChannelAttachment,
    ChannelBindSession,
    ChannelDeliveryBinding,
    ChannelEvent,
    ChannelPairingRequest,
    EntityId,
)
from pydantic import Field


class ChannelBindStartRequest(ApiModel):
    provider: str = "wechat"
    requested_by_member_id: EntityId = "mem_xiaoyao"
    display_name_hint: str = Field(default="我的微信", min_length=1)
    policy: dict[str, Any] = Field(default_factory=dict)


class ChannelBindStartResponse(ChannelBindSession):
    qr: dict[str, Any] = Field(default_factory=dict)
    poll_after_ms: int = 1500


class ChannelBindStatusResponse(ChannelBindSession):
    events: list[dict[str, Any]] = Field(default_factory=list)
    qr: dict[str, Any] = Field(default_factory=dict)
    poll_after_ms: int = 1500


class ChannelBindFinalizeResponse(ApiModel):
    bind_session: ChannelBindSession
    asset: dict[str, Any]
    channel: dict[str, Any]
    account: ChannelAccount


class ChannelBindCancelResponse(ChannelBindSession):
    pass


class ChannelRevokeResponse(ApiModel):
    channel_id: EntityId
    asset_id: EntityId | None = None
    status: str
    revoked_handles: int = 0


class ChannelAccountListResponse(ApiModel):
    items: list[ChannelAccount] = Field(default_factory=list)


class ChannelInboundWechatRequest(ApiModel):
    provider: str = "wechat"
    channel_account_id: EntityId | None = None
    channel_id: EntityId | None = None
    provider_event_id: str | None = None
    source: dict[str, Any] = Field(default_factory=dict)
    message: dict[str, Any] = Field(default_factory=dict)
    received_at: str | None = None
    raw_event: dict[str, Any] = Field(default_factory=dict)


class ChannelInboundWechatResponse(ApiModel):
    event: ChannelEvent
    notification_inbound: dict[str, Any] | None = None
    status: str
    turn_id: EntityId | None = None
    delivery_binding_id: EntityId | None = None
    chat_turns_created: int = 0
    delivery_status: str | None = None
    diagnostic: dict[str, Any] = Field(default_factory=dict)


class ChannelEventListResponse(ApiModel):
    items: list[ChannelEvent] = Field(default_factory=list)


class ChannelProviderHealthResponse(ApiModel):
    provider: str
    enabled: bool
    reachable: bool
    login_state: str
    version: str | None = None
    last_error_code: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ChannelPeerSessionResponse(ApiModel):
    channel_peer_session_id: EntityId
    organization_id: EntityId
    channel_account_id: EntityId
    channel_peer_id: EntityId | None = None
    channel_id: EntityId | None = None
    provider: str
    peer_ref_redacted: str
    peer_type: str
    conversation_id: EntityId | None = None
    session_id: EntityId
    member_id: EntityId
    peer_state_ref: str | None = None
    pairing_status: str
    allow_inbound: bool = False
    allow_outbound: bool = False
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    last_inbound_at: str | None = None
    last_outbound_at: str | None = None
    created_at: str
    updated_at: str


class ChannelPeerListResponse(ApiModel):
    items: list[ChannelPeerSessionResponse] = Field(default_factory=list)


class ChannelPairingRequestListResponse(ApiModel):
    items: list[ChannelPairingRequest] = Field(default_factory=list)


class ChannelPairingDecisionRequest(ApiModel):
    member_id: EntityId = "mem_xiaoyao"
    reason: str | None = None


class ChannelPairingDecisionResponse(ApiModel):
    pairing_request: ChannelPairingRequest
    peer_session: ChannelPeerSessionResponse | None = None


class ChannelPeerRevokeRequest(ApiModel):
    member_id: EntityId = "mem_xiaoyao"
    reason: str | None = None


class ChannelPeerRevokeResponse(ApiModel):
    peer_session: ChannelPeerSessionResponse
    status: str


class WechatGatewayPollResponse(ApiModel):
    status: str
    processed_accounts: int = 0
    processed_events: int = 0
    created_pairing_requests: int = 0
    chat_turns_created: int = 0
    deliveries_sent: int = 0
    rejected_events: int = 0
    duplicate_events: int = 0
    media_attachments: int = 0
    failures: int = 0
    reliability_status: str = "ok"
    correlation: dict[str, Any] = Field(default_factory=dict)
    taxonomy: list[str] = Field(default_factory=list)
    failure_reason_codes: list[str] = Field(default_factory=list)
    turn_formation: dict[str, Any] = Field(default_factory=dict)
    delivery_binding: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class WechatGatewayHealthResponse(ApiModel):
    provider: str = "wechat"
    enabled: bool
    poll_enabled: bool
    service_available: bool = False
    connected: bool = False
    status: str = "disconnected"
    login_state: str = "unknown"
    connection_state: str = "unknown"
    automation_state: str = "unknown"
    active_accounts: int = 0
    pending_pairing_requests: int = 0
    pending_deliveries: int = 0
    last_poll_result: dict[str, Any] = Field(default_factory=dict)
    immediate_delivery: dict[str, Any] = Field(default_factory=dict)
    reliability_status: str = "ok"
    correlation: dict[str, Any] = Field(default_factory=dict)
    taxonomy: list[str] = Field(default_factory=list)
    failure_reason_codes: list[str] = Field(default_factory=list)
    turn_formation: dict[str, Any] = Field(default_factory=dict)
    delivery_binding: dict[str, Any] = Field(default_factory=dict)
    worker_health: dict[str, Any] = Field(default_factory=dict)
    provider_health: ChannelProviderHealthResponse


class FeishuGatewayPollResponse(ApiModel):
    status: str
    processed_accounts: int = 0
    processed_events: int = 0
    created_pairing_requests: int = 0
    chat_turns_created: int = 0
    deliveries_sent: int = 0
    rejected_events: int = 0
    duplicate_events: int = 0
    media_attachments: int = 0
    operations_recorded: int = 0
    failures: int = 0
    reliability_status: str = "ok"
    correlation: dict[str, Any] = Field(default_factory=dict)
    taxonomy: list[str] = Field(default_factory=list)
    failure_reason_codes: list[str] = Field(default_factory=list)
    turn_formation: dict[str, Any] = Field(default_factory=dict)
    delivery_binding: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class FeishuGatewayHealthResponse(ApiModel):
    provider: str = "feishu"
    enabled: bool
    poll_enabled: bool
    service_available: bool = False
    connected: bool = False
    status: str = "disconnected"
    login_state: str = "unknown"
    connection_state: str = "unknown"
    transport_mode: str = "websocket"
    active_accounts: int = 0
    pending_pairing_requests: int = 0
    pending_deliveries: int = 0
    connections: list[dict[str, Any]] = Field(default_factory=list)
    last_poll_result: dict[str, Any] = Field(default_factory=dict)
    reliability_status: str = "ok"
    correlation: dict[str, Any] = Field(default_factory=dict)
    taxonomy: list[str] = Field(default_factory=list)
    failure_reason_codes: list[str] = Field(default_factory=list)
    turn_formation: dict[str, Any] = Field(default_factory=dict)
    delivery_binding: dict[str, Any] = Field(default_factory=dict)
    worker_health: dict[str, Any] = Field(default_factory=dict)
    provider_health: ChannelProviderHealthResponse


class FeishuInboundRequest(ApiModel):
    provider: str = "feishu"
    channel_account_id: EntityId | None = None
    raw_event: dict[str, Any] = Field(default_factory=dict)
    received_at: str | None = None


class FeishuInboundResponse(ApiModel):
    status: str
    event: ChannelEvent | None = None
    notification_inbound: dict[str, Any] | None = None
    turn_id: EntityId | None = None
    delivery_binding_id: EntityId | None = None
    chat_turns_created: int = 0
    delivery_status: str | None = None
    diagnostic: dict[str, Any] = Field(default_factory=dict)


class FeishuBindCallbackResponse(ApiModel):
    status: str
    bind_session_id: EntityId
    provider: str = "feishu"
    provider_account_ref_redacted: str | None = None
    confirmed_at: str | None = None
    next_step: str = "finalize_bind_session"
    diagnostic: dict[str, Any] = Field(default_factory=dict)


class FeishuMessageOperationRequest(ApiModel):
    channel_account_id: EntityId
    message_id: str | None = None
    emoji_type: str | None = None
    container_id: str | None = None
    container_id_type: str = "chat"
    page_size: int = 20


class FeishuMessageOperationResponse(ApiModel):
    status: str
    provider_message_id: str | None = None
    response_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None


class ChannelAttachmentListResponse(ApiModel):
    items: list[ChannelAttachment] = Field(default_factory=list)


class ChannelDeliveryBindingListResponse(ApiModel):
    items: list[ChannelDeliveryBinding] = Field(default_factory=list)
