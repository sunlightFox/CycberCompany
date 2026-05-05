from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from core_types import ApiModel


class VoiceProfileCreateRequest(ApiModel):
    display_name: str = Field(min_length=1)
    provider: Literal["edge", "hailuo_ai"] = "edge"
    provider_voice_id: str = Field(min_length=1)
    output_format: str = "wav"
    sample_text: str | None = None
    sample_audio_uri: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secret: str | None = None
    secret_ref: str | None = None
    status: str = "active"


class VoiceProfileUpdateRequest(ApiModel):
    display_name: str | None = None
    provider_voice_id: str | None = None
    output_format: str | None = None
    sample_text: str | None = None
    sample_audio_uri: str | None = None
    config: dict[str, Any] | None = None
    secret: str | None = None
    secret_ref: str | None = None
    status: str | None = None


class VoiceProfileResponse(ApiModel):
    voice_profile_id: str
    organization_id: str
    display_name: str
    provider: str
    provider_voice_id: str
    output_format: str
    sample_text: str | None = None
    sample_audio_uri: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    has_secret: bool = False
    status: str
    created_at: str
    updated_at: str


class VoiceProfileListResponse(ApiModel):
    items: list[VoiceProfileResponse] = Field(default_factory=list)


class MemberVoiceBindingCreateRequest(ApiModel):
    member_id: str
    voice_profile_id: str
    binding_scope: str = "default"
    reply_mode: str = "explicit_request_only"
    priority: int = 0
    status: str = "active"


class MemberVoiceBindingResponse(ApiModel):
    binding_id: str
    organization_id: str
    member_id: str
    voice_profile_id: str
    binding_scope: str
    reply_mode: str
    priority: int
    status: str
    voice_display_name: str
    provider: str
    provider_voice_id: str
    output_format: str
    voice_config: dict[str, Any] = Field(default_factory=dict)
    has_secret: bool = False
    created_at: str
    updated_at: str


class MemberVoiceBindingListResponse(ApiModel):
    items: list[MemberVoiceBindingResponse] = Field(default_factory=list)


class VoiceReplyPlanResponse(ApiModel):
    requested: bool = False
    should_render: bool = False
    reason: str
    provider: str | None = None
    voice_profile_id: str | None = None
    binding_id: str | None = None
    output_format: str | None = None
    voice_style_plan: dict[str, Any] = Field(default_factory=dict)
    audio_uri: str | None = None
    audio_content_type: str | None = None
    render_job_id: str | None = None


class VoiceRenderJobResponse(ApiModel):
    render_job_id: str
    organization_id: str
    member_id: str
    conversation_id: str | None = None
    turn_id: str | None = None
    message_id: str | None = None
    voice_profile_id: str
    provider: str
    provider_voice_id: str
    status: str
    source_text_hash: str
    source_text_preview: str
    voice_style_plan: dict[str, Any] = Field(default_factory=dict)
    output_uri: str | None = None
    output_content_type: str | None = None
    output_size_bytes: int | None = None
    checksum: str | None = None
    provider_job_id: str | None = None
    provider_response: dict[str, Any] = Field(default_factory=dict)
    degraded_reason: str | None = None
    trace_id: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class VoiceRenderPreviewRequest(ApiModel):
    member_id: str
    text: str = Field(min_length=1)
    conversation_id: str | None = None
    turn_id: str | None = None
    voice_profile_id: str | None = None
    response_plan: dict[str, Any] = Field(default_factory=dict)
    persona: dict[str, Any] = Field(default_factory=dict)
    heart: dict[str, Any] = Field(default_factory=dict)
    risk_level: str = "R1"


class VoiceRenderPreviewResponse(ApiModel):
    voice_reply: VoiceReplyPlanResponse
    render_job: VoiceRenderJobResponse | None = None
    message: str
