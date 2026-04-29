from __future__ import annotations

import json

from core_types import ChatEvent, ErrorCode
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_registry
from app.core.errors import AppError
from app.schemas.brain import BrainDecisionResponse
from app.schemas.chat import (
    ChatClarificationDecisionResponse,
    ChatPersistedEvent,
    ChatPersistedEventsResponse,
    ChatTurnDetail,
    ChatTurnRequest,
    ChatTurnResponse,
    ConversationDetail,
    ConversationListItem,
    ConversationListResponse,
    ConversationWorkingStateResponse,
    DialogueStateResponse,
    LowConfidenceDecisionReviewResponse,
    SemanticIntentCandidatesResponse,
    SemanticReviewEventsResponse,
    SemanticReviewResponse,
)
from app.schemas.design_alignment import (
    ResponseQualityEvaluationResponse,
    TonePolicyResolutionResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    registry: ServiceRegistry = Depends(get_registry),
) -> ConversationListResponse:
    rows = await registry.chat.list_conversations()
    return ConversationListResponse(items=[ConversationListItem(**row) for row in rows])


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ConversationDetail:
    row = await registry.chat.get_conversation(conversation_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)
    return ConversationDetail(**row)


@router.get(
    "/conversations/{conversation_id}/working-state",
    response_model=ConversationWorkingStateResponse,
)
async def get_conversation_working_state(
    conversation_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ConversationWorkingStateResponse:
    if await registry.chat.get_conversation(conversation_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)
    state = await registry.chat_experience_service.get_working_state(conversation_id)
    if state is None:
        raise AppError(ErrorCode.NOT_FOUND, "对话工作态不存在", status_code=404)
    return ConversationWorkingStateResponse(**state)


@router.get(
    "/conversations/{conversation_id}/dialogue-state",
    response_model=DialogueStateResponse,
)
async def get_conversation_dialogue_state(
    conversation_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> DialogueStateResponse:
    if await registry.chat.get_conversation(conversation_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在", status_code=404)
    state = await registry.brain_decision_service.get_dialogue_state(conversation_id)
    if state is None:
        raise AppError(ErrorCode.NOT_FOUND, "对话语义状态不存在", status_code=404)
    return DialogueStateResponse(**state)


@router.get("/turns/{turn_id}", response_model=ChatTurnDetail)
async def get_turn(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChatTurnDetail:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    turn = dict(turn)
    turn.pop("events", None)
    return ChatTurnDetail(**turn)


@router.get("/turns/{turn_id}/clarification", response_model=ChatClarificationDecisionResponse)
async def get_turn_clarification(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChatClarificationDecisionResponse:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    decision = await registry.chat_experience_service.get_clarification(turn_id)
    if decision is not None:
        return ChatClarificationDecisionResponse(**decision)
    return ChatClarificationDecisionResponse(
        turn_id=turn_id,
        conversation_id=turn["conversation_id"],
        needs_clarification=False,
        reason="not_evaluated",
        blocking_level="none",
    )


@router.get("/turns/{turn_id}/brain-decision", response_model=BrainDecisionResponse)
async def get_turn_brain_decision(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> BrainDecisionResponse:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    decision = await registry.brain_decision_service.get_by_turn(turn_id)
    if decision is None:
        raise AppError(ErrorCode.NOT_FOUND, "brain decision 不存在", status_code=404)
    return BrainDecisionResponse(**decision)


@router.get(
    "/turns/{turn_id}/semantic-intents",
    response_model=SemanticIntentCandidatesResponse,
)
async def get_turn_semantic_intents(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SemanticIntentCandidatesResponse:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    rows = await registry.brain_decision_service.list_semantic_intents(turn_id)
    return SemanticIntentCandidatesResponse(items=rows)


@router.get(
    "/turns/{turn_id}/low-confidence-review",
    response_model=LowConfidenceDecisionReviewResponse,
)
async def get_turn_low_confidence_review(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> LowConfidenceDecisionReviewResponse:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    review = await registry.brain_decision_service.get_low_confidence_review(turn_id)
    if review is None:
        raise AppError(ErrorCode.NOT_FOUND, "low confidence review 不存在", status_code=404)
    return LowConfidenceDecisionReviewResponse(**review)


@router.get(
    "/turns/{turn_id}/semantic-review",
    response_model=SemanticReviewResponse,
)
async def get_turn_semantic_review(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SemanticReviewResponse:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    review = await registry.brain_decision_service.get_semantic_review(turn_id)
    if review is None:
        raise AppError(ErrorCode.NOT_FOUND, "semantic review 不存在", status_code=404)
    return SemanticReviewResponse(**review)


@router.get(
    "/turns/{turn_id}/semantic-review-events",
    response_model=SemanticReviewEventsResponse,
)
async def get_turn_semantic_review_events(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> SemanticReviewEventsResponse:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    rows = await registry.brain_decision_service.list_semantic_review_events(turn_id)
    return SemanticReviewEventsResponse(items=rows)


@router.get(
    "/turns/{turn_id}/tone-policy",
    response_model=TonePolicyResolutionResponse,
)
async def get_turn_tone_policy(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> TonePolicyResolutionResponse:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    return await registry.persona_heart_service.get_tone_policy_for_turn(turn_id)


@router.get(
    "/turns/{turn_id}/response-quality",
    response_model=ResponseQualityEvaluationResponse,
)
async def get_turn_response_quality(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ResponseQualityEvaluationResponse:
    turn = await registry.chat.get_turn(turn_id)
    if turn is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    return await registry.persona_heart_service.get_response_quality_for_turn(turn_id)


@router.get("/turns/{turn_id}/events", response_model=ChatPersistedEventsResponse)
async def get_turn_events(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChatPersistedEventsResponse:
    if await registry.chat.get_turn(turn_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)
    rows = await registry.chat.list_events(turn_id)
    return ChatPersistedEventsResponse(items=[ChatPersistedEvent(**row) for row in rows])


@router.post("/turn", response_model=ChatTurnResponse)
async def create_turn(
    request: ChatTurnRequest,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChatTurnResponse:
    return await registry.chat_service.create_turn(request)


@router.get("/stream/{turn_id}")
async def stream_turn_events(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> StreamingResponse:
    if await registry.chat.get_turn(turn_id) is None:
        raise AppError(ErrorCode.NOT_FOUND, "turn 不存在", status_code=404)

    async def event_source():
        async for event in registry.chat_service.stream_turn_events(turn_id):
            yield _sse(event)

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.post("/turns/{turn_id}/cancel", response_model=ChatTurnResponse)
async def cancel_turn(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChatTurnResponse:
    return await registry.chat_service.cancel_turn(turn_id)


@router.post("/turns/{turn_id}/retry", response_model=ChatTurnResponse)
async def retry_turn(
    turn_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> ChatTurnResponse:
    return await registry.chat_service.retry_turn(turn_id)


def _sse(event: ChatEvent) -> str:
    return (
        f"event: {event.event.value}\n"
        f"data: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
    )
