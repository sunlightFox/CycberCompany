from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class ChannelBindSession(ApiModel):
    bind_session_id: EntityId
    organization_id: EntityId
    provider: str
    requested_by_member_id: EntityId
    display_name_hint: str | None = None
    status: str
    qr_format: str | None = None
    qr_payload_ref: str | None = None
    qr_artifact_id: EntityId | None = None
    expires_at: datetime
    confirmed_at: datetime | None = None
    bound_asset_id: EntityId | None = None
    bound_channel_id: EntityId | None = None
    provider_account_ref_redacted: str | None = None
    provider_state_ref: str | None = None
    risk_level: str
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    provider_status: dict[str, Any] = Field(default_factory=dict)
    failure_reason: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class ChannelAccount(ApiModel):
    channel_account_id: EntityId
    organization_id: EntityId
    asset_id: EntityId
    channel_id: EntityId | None = None
    bind_session_id: EntityId | None = None
    provider: str
    account_ref_redacted: str
    display_name: str
    status: str
    capabilities: list[str] = Field(default_factory=list)
    provider_state_ref: str
    policy: dict[str, Any] = Field(default_factory=dict)
    last_seen_at: datetime | None = None
    last_verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChannelPeer(ApiModel):
    channel_peer_id: EntityId
    organization_id: EntityId
    channel_account_id: EntityId
    provider: str
    peer_ref_redacted: str
    peer_type: str
    display_name_redacted: str | None = None
    pairing_status: str
    allow_inbound: bool = False
    allow_outbound: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ChannelEvent(ApiModel):
    channel_event_id: EntityId
    organization_id: EntityId
    provider: str
    channel_account_id: EntityId | None = None
    channel_id: EntityId | None = None
    event_type: str
    provider_event_id_redacted: str | None = None
    payload_redacted: dict[str, Any] = Field(default_factory=dict)
    normalized_event: dict[str, Any] = Field(default_factory=dict)
    status: str
    trace_id: EntityId | None = None
    received_at: datetime
    created_at: datetime


class ChannelPeerSession(ApiModel):
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
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChannelPairingRequest(ApiModel):
    pairing_request_id: EntityId
    organization_id: EntityId
    channel_account_id: EntityId
    channel_peer_id: EntityId | None = None
    provider: str
    peer_ref_redacted: str
    peer_type: str
    display_name_redacted: str | None = None
    peer_state_ref: str | None = None
    status: str
    requested_member_id: EntityId
    decision_by_member_id: EntityId | None = None
    decision_reason: str | None = None
    expires_at: datetime | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None = None


class ChannelAttachment(ApiModel):
    channel_attachment_id: EntityId
    organization_id: EntityId
    channel_event_id: EntityId | None = None
    channel_account_id: EntityId
    channel_peer_session_id: EntityId | None = None
    provider: str
    provider_attachment_ref_redacted: str | None = None
    attachment_type: str
    display_name_redacted: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    artifact_id: EntityId | None = None
    blob_ref: str | None = None
    media_id: EntityId | None = None
    status: str
    failure_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime


class ChannelDeliveryBinding(ApiModel):
    channel_delivery_binding_id: EntityId
    organization_id: EntityId
    channel_account_id: EntityId
    channel_peer_session_id: EntityId | None = None
    channel_event_id: EntityId | None = None
    turn_id: EntityId | None = None
    message_id: EntityId | None = None
    notification_id: EntityId | None = None
    provider: str
    provider_message_id_redacted: str | None = None
    status: str
    attempts: int = 0
    failure_reason: str | None = None
    trace_id: EntityId | None = None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None = None
