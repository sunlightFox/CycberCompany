from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from core_types.common import ApiModel, EntityId
from core_types.enums import ChatEventType


class ClientContext(ApiModel):
    timezone: str = "Asia/Shanghai"
    locale: str = "zh-CN"
    ui_mode: str | None = None


class Attachment(ApiModel):
    attachment_id: EntityId | None = None
    name: str | None = None
    content_type: str | None = None
    uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatInput(ApiModel):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1)


class ChatTurnRequest(ApiModel):
    session_id: EntityId
    conversation_id: EntityId | None = None
    member_id: EntityId
    input: ChatInput
    attachments: list[Attachment] = Field(default_factory=list)
    client_context: ClientContext = Field(default_factory=ClientContext)


class ChatTurnResponse(ApiModel):
    turn_id: EntityId
    conversation_id: EntityId
    message_id: EntityId
    assistant_message_id: EntityId | None = None
    task_id: EntityId | None = None
    trace_id: EntityId
    status: str
    stream_url: str | None = None


class ChatEvent(ApiModel):
    event: ChatEventType
    turn_id: EntityId
    trace_id: EntityId | None = None
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatTurnRecoveryAttempt(ApiModel):
    recovery_attempt_id: EntityId
    organization_id: EntityId = "org_default"
    turn_id: EntityId
    task_id: EntityId | None = None
    attempt_index: int
    failure_type: str
    root_cause: str
    recovery_action: str
    status: str
    diagnostic_payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    started_at: datetime | str
    completed_at: datetime | str | None = None
