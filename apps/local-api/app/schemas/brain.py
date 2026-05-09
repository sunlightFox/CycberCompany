from __future__ import annotations

from typing import Any

from core_types import (
    ApiModel,
    BrainDecisionBundle,
    BrainProvider,
    ContextDecision,
    DialogueState,
    IntentDecision,
    LowConfidenceDecisionReview,
    ModeDecision,
    SemanticIntentCandidate,
    SemanticReviewResult,
)
from pydantic import Field, field_validator


class BrainCreateRequest(ApiModel):
    display_name: str = Field(min_length=1)
    provider: BrainProvider = BrainProvider.OPENAI_COMPATIBLE
    endpoint: str | None = None
    model_name: str = Field(min_length=1)
    api_key: str | None = None
    api_key_ref: str | None = None
    is_local: bool = True
    context_window: int = Field(default=8192, ge=1024)
    supports_tools: bool = False
    supports_vision: bool = False
    supports_audio: bool = False
    cost_policy: dict[str, Any] = Field(default_factory=dict)
    privacy_policy: dict[str, Any] = Field(default_factory=dict)
    default_temperature: float = Field(default=0.3, ge=0, le=2)
    default_top_p: float = Field(default=0.9, ge=0, le=1)
    default_max_output_tokens: int = Field(default=1024, ge=1)
    timeout_seconds: int = Field(default=180, ge=1)
    retry_count: int = Field(default=1, ge=0, le=3)
    allow_fallback: bool = True
    allow_cloud: bool = False
    streaming_supported: bool = True
    protocol_family: str = "auto"
    request_format: str = "chat_completions"
    response_format: str = "auto"
    supports_stream: bool = True
    verify_capabilities: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("endpoint must be an http or https URL")
        return value


class BrainUpdateRequest(ApiModel):
    display_name: str | None = Field(default=None, min_length=1)
    provider: BrainProvider | None = None
    endpoint: str | None = None
    model_name: str | None = Field(default=None, min_length=1)
    api_key: str | None = None
    api_key_ref: str | None = None
    is_local: bool | None = None
    context_window: int | None = Field(default=None, ge=1024)
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_audio: bool | None = None
    cost_policy: dict[str, Any] | None = None
    privacy_policy: dict[str, Any] | None = None
    default_temperature: float | None = Field(default=None, ge=0, le=2)
    default_top_p: float | None = Field(default=None, ge=0, le=1)
    default_max_output_tokens: int | None = Field(default=None, ge=1)
    timeout_seconds: int | None = Field(default=None, ge=1)
    retry_count: int | None = Field(default=None, ge=0, le=3)
    allow_fallback: bool | None = None
    allow_cloud: bool | None = None
    streaming_supported: bool | None = None
    protocol_family: str | None = None
    request_format: str | None = None
    response_format: str | None = None
    supports_stream: bool | None = None
    verify_capabilities: dict[str, Any] | None = None
    enabled: bool | None = None

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("endpoint must be an http or https URL")
        return value


class BrainResponse(ApiModel):
    brain_id: str
    display_name: str
    provider: str
    endpoint: str | None = None
    model_name: str
    api_key_ref: str | None = None
    has_api_key: bool
    is_local: bool
    context_window: int | None = None
    supports_tools: bool
    supports_vision: bool
    supports_audio: bool
    cost_policy: dict[str, Any]
    privacy_policy: dict[str, Any]
    status: str
    default_temperature: float
    default_top_p: float
    default_max_output_tokens: int
    timeout_seconds: int
    retry_count: int
    allow_fallback: bool
    allow_cloud: bool
    streaming_supported: bool
    protocol_family: str = "auto"
    request_format: str = "chat_completions"
    response_format: str = "auto"
    supports_stream: bool = True
    verify_capabilities: dict[str, Any] = Field(default_factory=dict)
    last_verified_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    latency_ms: int | None = None
    created_at: str
    updated_at: str


class BrainListResponse(ApiModel):
    items: list[BrainResponse]


class BrainVerifyResponse(ApiModel):
    brain_id: str
    status: str
    latency_ms: int | None = None
    error_code: str | None = None
    message: str
    verify_capabilities: dict[str, Any] = Field(default_factory=dict)


class BrainDecisionPreviewRequest(ApiModel):
    text: str = Field(min_length=1)
    member_id: str = "mem_xiaoyao"
    conversation_id: str | None = None
    privacy_level: str = "medium"


class BrainDecisionResponse(BrainDecisionBundle):
    turn_id: str | None = None
    conversation_id: str | None = None
    member_id: str | None = None
    input_summary: str | None = None


class BrainDecisionPreviewResponse(ApiModel):
    brain_decision_id: str
    intent: IntentDecision
    mode: ModeDecision
    context: ContextDecision
    clarification: dict[str, Any] = Field(default_factory=dict)
    dialogue_state: DialogueState | None = None
    semantic_intent_candidates: list[SemanticIntentCandidate] = Field(default_factory=list)
    low_confidence_review: LowConfidenceDecisionReview | None = None
    semantic_review: SemanticReviewResult | None = None
    capability_snapshot: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    status: str
    trace_id: str | None = None
    created_at: str | None = None
