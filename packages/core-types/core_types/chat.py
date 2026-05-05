from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

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


class ChatContentPart(ApiModel):
    type: Literal[
        "text",
        "image",
        "audio",
        "file",
        "image_summary",
        "audio_transcript",
        "file_extract",
        "link",
        "artifact_ref",
        "task_ref",
        "approval_ref",
        "asset_ref",
    ]
    text: str | None = None
    uri: str | None = None
    name: str | None = None
    content_type: str | None = None
    ref_id: EntityId | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatContextRef(ApiModel):
    type: Literal["asset", "artifact", "task_replay", "url", "knowledge", "message", "turn"]
    ref_id: EntityId | None = None
    uri: str | None = None
    label: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatIngressMetadata(ApiModel):
    channel: str = "local"
    channel_message_id: str | None = None
    dedupe_key: str | None = None
    debounce_ms: int | None = None
    queue_policy: Literal["immediate", "collect", "followup", "steer", "interrupt"] = "immediate"
    collected_message_count: int | None = None
    collected_envelope_ids: list[EntityId] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class ChatMessageEnvelope(ApiModel):
    envelope_id: EntityId
    organization_id: EntityId = "org_default"
    turn_id: EntityId
    conversation_id: EntityId
    session_id: EntityId
    member_id: EntityId
    user_message_id: EntityId | None = None
    dedupe_key: str
    raw_payload_redacted: dict[str, Any] = Field(default_factory=dict)
    content_parts: list[ChatContentPart] = Field(default_factory=list)
    context_refs: list[ChatContextRef] = Field(default_factory=list)
    model_safe_text: str
    normalized_summary: dict[str, Any] = Field(default_factory=dict)
    ingress_metadata: ChatIngressMetadata = Field(default_factory=ChatIngressMetadata)
    status: str = "normalized"
    trace_id: EntityId | None = None
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None


class ChatInput(ApiModel):
    type: Literal["text", "multi_part"] = "text"
    text: str | None = None
    content_parts: list[ChatContentPart] = Field(default_factory=list)

    @model_validator(mode="after")
    def _requires_content(self) -> ChatInput:
        if (self.text or "").strip():
            return self
        if self.content_parts:
            return self
        raise ValueError("chat input requires text or content_parts")


class ChatTurnRequest(ApiModel):
    session_id: EntityId
    conversation_id: EntityId | None = None
    member_id: EntityId
    input: ChatInput
    attachments: list[Attachment] = Field(default_factory=list)
    context_refs: list[ChatContextRef] = Field(default_factory=list)
    ingress_metadata: ChatIngressMetadata = Field(default_factory=ChatIngressMetadata)
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
    queue_status: str | None = None
    envelope_id: EntityId | None = None


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
    recovery_stage: str = "task"
    error_signature: str | None = None
    action_result: dict[str, Any] = Field(default_factory=dict)
    diagnostic_payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    started_at: datetime | str
    completed_at: datetime | str | None = None


class ChatTurnQueueItem(ApiModel):
    queue_id: EntityId
    organization_id: EntityId = "org_default"
    turn_id: EntityId
    session_id: EntityId
    conversation_id: EntityId
    member_id: EntityId
    status: str
    queue_policy: str = "immediate"
    position: int = 0
    locked_by: str | None = None
    locked_until: datetime | str | None = None
    dedupe_key: str | None = None
    created_at: datetime | str
    updated_at: datetime | str
    started_at: datetime | str | None = None
    completed_at: datetime | str | None = None


class ChatContextCompaction(ApiModel):
    compaction_id: EntityId
    organization_id: EntityId = "org_default"
    turn_id: EntityId
    conversation_id: EntityId
    reason: str
    status: str
    token_estimate_before: int = 0
    token_estimate_after: int = 0
    summary: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: EntityId | None = None
    created_at: datetime | str
    completed_at: datetime | str | None = None
