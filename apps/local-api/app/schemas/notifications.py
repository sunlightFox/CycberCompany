from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    EntityId,
    InboundMessage,
    NotificationChannel,
    NotificationDeliveryAttempt,
    NotificationMessage,
)
from pydantic import Field


class NotificationChannelCreateRequest(ApiModel):
    provider: str = "local_mock"
    display_name: str = Field(default="本地通知箱", min_length=1)
    channel_type: str = "local_inbox"
    sensitivity: str = "medium"
    policy: dict[str, Any] = Field(default_factory=dict)
    provider_config: dict[str, Any] = Field(default_factory=dict)
    created_by_member_id: EntityId = "mem_xiaoyao"
    secret_value: str | None = None
    asset_id: EntityId | None = None
    create_asset: bool = True


class NotificationChannelUpdateRequest(ApiModel):
    display_name: str | None = Field(default=None, min_length=1)
    status: str | None = None
    sensitivity: str | None = None
    policy: dict[str, Any] | None = None
    provider_config: dict[str, Any] | None = None


class NotificationChannelListResponse(ApiModel):
    items: list[NotificationChannel] = Field(default_factory=list)


class NotificationChannelTestRequest(ApiModel):
    recipient: str = "user_local_owner"
    subject: str = "通知网关测试"
    body: str = "这是一条测试通知。"


class NotificationMessageCreateRequest(ApiModel):
    channel_id: EntityId
    message_type: str = "system_degraded"
    recipient: str = "user_local_owner"
    subject: str | None = None
    body: str
    task_id: EntityId | None = None
    scheduled_task_id: EntityId | None = None
    scheduled_run_id: EntityId | None = None
    approval_id: EntityId | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    send_immediately: bool = True


class NotificationMessageListResponse(ApiModel):
    items: list[NotificationMessage] = Field(default_factory=list)


class NotificationDeliveryAttemptListResponse(ApiModel):
    items: list[NotificationDeliveryAttempt] = Field(default_factory=list)


class InboundMessageCreateRequest(ApiModel):
    channel_id: EntityId
    sender_ref: str = "user_local_owner"
    content: str
    provider_message_id: str | None = None
    received_at: str | None = None


class InboundMessageResponse(InboundMessage):
    pass
