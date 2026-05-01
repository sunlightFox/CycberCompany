from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from core_types.common import ApiModel, EntityId


class NotificationChannel(ApiModel):
    channel_id: EntityId
    organization_id: EntityId
    asset_id: EntityId | None = None
    provider: str
    display_name: str
    channel_type: str
    status: str
    sensitivity: str = "medium"
    policy: dict[str, Any] = Field(default_factory=dict)
    provider_config: dict[str, Any] = Field(default_factory=dict)
    last_health_status: str | None = None
    last_error: str | None = None
    created_by_member_id: EntityId | None = None
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class NotificationMessage(ApiModel):
    notification_id: EntityId
    organization_id: EntityId
    channel_id: EntityId
    task_id: EntityId | None = None
    scheduled_task_id: EntityId | None = None
    scheduled_run_id: EntityId | None = None
    approval_id: EntityId | None = None
    message_type: str
    recipient: str
    status: str
    subject_redacted: str | None = None
    body_redacted: str
    dlp_summary: dict[str, Any] = Field(default_factory=dict)
    provider_message_id: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    next_retry_at: datetime | None = None
    failure_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    sent_at: datetime | None = None


class NotificationDeliveryAttempt(ApiModel):
    attempt_id: EntityId
    organization_id: EntityId
    notification_id: EntityId
    channel_id: EntityId
    provider: str
    attempt_index: int
    status: str
    request_summary: dict[str, Any] = Field(default_factory=dict)
    response_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_summary: str | None = None
    latency_ms: int = 0
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class InboundMessage(ApiModel):
    inbound_message_id: EntityId
    organization_id: EntityId
    channel_id: EntityId
    sender_ref: str
    provider_message_id: str | None = None
    received_at: datetime
    content_redacted: str
    parsed_intent: str
    binding_status: str
    matched_approval_id: EntityId | None = None
    matched_task_id: EntityId | None = None
    action_result: dict[str, Any] = Field(default_factory=dict)
    risk_summary: dict[str, Any] = Field(default_factory=dict)
    untrusted_external_content: bool = True
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class InboundMessageEvent(ApiModel):
    event_id: EntityId
    inbound_message_id: EntityId
    organization_id: EntityId
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_redacted: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | None = None


class NotificationSubscription(ApiModel):
    subscription_id: EntityId
    organization_id: EntityId
    channel_id: EntityId
    subject_type: str
    subject_id: EntityId | None = None
    event_types: list[str] = Field(default_factory=list)
    status: str
    policy: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
