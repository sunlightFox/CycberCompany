from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_registry
from app.schemas.voice import (
    MemberVoiceBindingCreateRequest,
    MemberVoiceBindingListResponse,
    MemberVoiceBindingResponse,
    VoiceProfileCreateRequest,
    VoiceProfileListResponse,
    VoiceProfileResponse,
    VoiceProfileUpdateRequest,
    VoiceRenderJobResponse,
    VoiceRenderPreviewRequest,
    VoiceRenderPreviewResponse,
    VoiceReplyPlanResponse,
)
from app.services.registry import ServiceRegistry

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("/profiles", response_model=VoiceProfileListResponse)
async def list_voice_profiles(
    registry: ServiceRegistry = Depends(get_registry),
) -> VoiceProfileListResponse:
    return VoiceProfileListResponse(
        items=[
            VoiceProfileResponse(**_public_voice_profile(row))
            for row in await registry.voice_service.list_profiles()
        ]
    )


@router.post("/profiles", response_model=VoiceProfileResponse)
async def create_voice_profile(
    payload: VoiceProfileCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> VoiceProfileResponse:
    profile = await registry.voice_service.create_profile(
        payload.model_dump(mode="json"),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return VoiceProfileResponse(**_public_voice_profile(profile))


@router.get("/profiles/{voice_profile_id}", response_model=VoiceProfileResponse)
async def get_voice_profile(
    voice_profile_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> VoiceProfileResponse:
    profile = await registry.voice_service.get_profile(voice_profile_id)
    return VoiceProfileResponse(**_public_voice_profile(profile))


@router.patch("/profiles/{voice_profile_id}", response_model=VoiceProfileResponse)
async def update_voice_profile(
    voice_profile_id: str,
    payload: VoiceProfileUpdateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> VoiceProfileResponse:
    profile = await registry.voice_service.update_profile(
        voice_profile_id,
        payload.model_dump(mode="json", exclude_unset=True),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return VoiceProfileResponse(**_public_voice_profile(profile))


@router.get("/members/{member_id}/bindings", response_model=MemberVoiceBindingListResponse)
async def list_member_voice_bindings(
    member_id: str,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemberVoiceBindingListResponse:
    binding = await registry.voice_service.get_member_binding(member_id)
    return MemberVoiceBindingListResponse(
        items=[MemberVoiceBindingResponse(**_public_voice_binding(binding))] if binding else []
    )


@router.post("/bindings", response_model=MemberVoiceBindingResponse)
async def create_member_voice_binding(
    payload: MemberVoiceBindingCreateRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> MemberVoiceBindingResponse:
    binding = await registry.voice_service.create_member_binding(
        payload.model_dump(mode="json"),
        trace_id=getattr(request.state, "trace_id", None),
    )
    return MemberVoiceBindingResponse(**_public_voice_binding(binding))


@router.post("/render-preview", response_model=VoiceRenderPreviewResponse)
async def render_preview(
    payload: VoiceRenderPreviewRequest,
    request: Request,
    registry: ServiceRegistry = Depends(get_registry),
) -> VoiceRenderPreviewResponse:
    response_plan = _preview_response_plan(payload)
    result = await registry.voice_service.render_voice_reply(
        turn={
            "organization_id": "org_default",
            "member_id": payload.member_id,
            "conversation_id": payload.conversation_id,
            "turn_id": payload.turn_id or "turn_preview",
        },
        user_text=payload.text,
        assistant_text=payload.text,
        response_plan=response_plan,
        persona=payload.persona,
        heart=payload.heart,
        risk_level=payload.risk_level,
        trace_id=getattr(request.state, "trace_id", None),
    )
    render_job = VoiceRenderJobResponse(**result.render_job) if result.render_job else None
    return VoiceRenderPreviewResponse(
        voice_reply=VoiceReplyPlanResponse(**result.voice_reply),
        render_job=render_job,
        message="语音预览已完成",
    )


def _public_voice_profile(row: dict) -> dict:
    data = dict(row)
    data.pop("secret_ref", None)
    return data


def _public_voice_binding(row: dict) -> dict:
    data = dict(row)
    data.pop("secret_ref", None)
    return data


def _preview_response_plan(payload: VoiceRenderPreviewRequest) -> dict:
    response_plan = dict(payload.response_plan or {})
    if payload.voice_profile_id:
        structured = dict(response_plan.get("structured_payload") or {})
        voice_reply = dict(structured.get("voice_reply") or {})
        voice_reply.update(
            {
                "requested": True,
                "voice_profile_id": payload.voice_profile_id,
            }
        )
        structured["voice_reply"] = voice_reply
        response_plan["structured_payload"] = structured
        response_plan["voice_reply_requested"] = True
    return response_plan
