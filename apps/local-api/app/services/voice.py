from __future__ import annotations

import base64
import asyncio
import hashlib
import io
import json
import math
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from core_types import ErrorCode, RiskLevel, TraceSpanStatus, TraceSpanType
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.chat_repo import ChatRepository
from app.db.repositories.member_repo import MemberRepository
from app.db.repositories.voice_repo import VoiceRepository
from app.schemas.voice import VoiceReplyPlanResponse
from app.services.audit import AuditEventService
from app.services.secrets import SecretStore


@dataclass(frozen=True)
class VoiceRenderRequest:
    organization_id: str
    member_id: str
    conversation_id: str | None
    turn_id: str | None
    text: str
    voice_profile: dict[str, Any]
    persona: dict[str, Any]
    heart: dict[str, Any]
    response_plan: dict[str, Any]
    voice_style_plan: dict[str, Any]
    risk_level: str
    trace_id: str | None
    message_id: str | None = None


@dataclass(frozen=True)
class VoiceRenderResult:
    render_job: dict[str, Any]
    voice_reply: dict[str, Any]


class VoiceProvider(Protocol):
    async def render(self, request: VoiceRenderRequest) -> tuple[bytes, dict[str, Any]]:
        ...


class EdgeVoiceProvider:
    def __init__(self) -> None:
        self._backend = "edge"

    async def render(self, request: VoiceRenderRequest) -> tuple[bytes, dict[str, Any]]:
        edge_adapter = _import_edge_tts()
        if edge_adapter is not None:
            return await edge_adapter(request)
        audio = _synthesize_wave(request.text, voice_key=request.voice_profile["provider_voice_id"])
        return audio, {"backend": self._backend, "fallback": True}


class HailuoVoiceProvider:
    def __init__(self, *, endpoint: str | None, api_key: str | None) -> None:
        self._endpoint = endpoint
        self._api_key = api_key

    async def render(self, request: VoiceRenderRequest) -> tuple[bytes, dict[str, Any]]:
        if not self._endpoint:
            raise AppError(
                ErrorCode.VOICE_PROVIDER_UNAVAILABLE,
                "海螺 AI TTS 未配置 endpoint",
                status_code=503,
            )
        payload = {
            "text": request.text,
            "voice_id": request.voice_profile["provider_voice_id"],
            "format": request.voice_profile.get("output_format") or "wav",
            "style": request.voice_profile.get("config", {}),
            "voice_style_plan": request.voice_style_plan,
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(self._endpoint, json=payload, headers=headers)
        if response.status_code >= 400:
            raise AppError(
                ErrorCode.VOICE_RENDER_FAILED,
                "海螺 AI TTS 请求失败",
                status_code=502,
                details={"status_code": response.status_code},
            )
        content_type = response.headers.get("content-type", "").lower()
        if "application/json" in content_type or response.text[:1] in {"{", "["}:
            data = response.json()
            audio_bytes, response_meta = _extract_hailuo_audio(data)
            return audio_bytes, {"backend": "hailuo_ai", "response": redact(response_meta)}
        return response.content, {"backend": "hailuo_ai", "content_type": content_type}


class VoiceService:
    def __init__(
        self,
        *,
        repo: VoiceRepository,
        chat_repo: ChatRepository,
        member_repo: MemberRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
        secret_store: SecretStore,
        data_dir: Path,
    ) -> None:
        self._repo = repo
        self._chat_repo = chat_repo
        self._members = member_repo
        self._trace = trace_service
        self._audit = audit_service
        self._secrets = secret_store
        self._data_dir = data_dir / "voice-renders"
        self._data_dir.mkdir(parents=True, exist_ok=True)

    async def list_profiles(self, organization_id: str = "org_default") -> list[dict[str, Any]]:
        return await self._repo.list_profiles(organization_id)

    async def get_profile(self, voice_profile_id: str) -> dict[str, Any]:
        profile = await self._repo.get_profile(voice_profile_id)
        if profile is None:
            raise AppError(ErrorCode.NOT_FOUND, "声音不存在", status_code=404)
        return profile

    async def create_profile(self, data: dict[str, Any], *, trace_id: str | None = None) -> dict[str, Any]:
        now = utc_now_iso()
        secret_ref = data.get("secret_ref")
        if data.get("secret"):
            secret_ref, _ = self._secrets.put_secret(str(data["secret"]))
        profile = {
            "voice_profile_id": new_id("vpr"),
            "organization_id": data.get("organization_id") or "org_default",
            "display_name": data["display_name"],
            "provider": data["provider"],
            "provider_voice_id": data["provider_voice_id"],
            "output_format": data.get("output_format") or "wav",
            "sample_text": data.get("sample_text"),
            "sample_audio_uri": data.get("sample_audio_uri"),
            "config": data.get("config") or {},
            "secret_ref": secret_ref,
            "status": data.get("status") or "active",
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_profile(profile)
        await self._audit.write_event(
            actor_type="system",
            action="voice.profile.created",
            object_type="voice_profile",
            object_id=profile["voice_profile_id"],
            summary="声音配置已创建",
            risk_level=RiskLevel.R1,
            payload={
                "voice_profile_id": profile["voice_profile_id"],
                "provider": profile["provider"],
                "provider_voice_id": profile["provider_voice_id"],
            },
            trace_id=trace_id,
        )
        return await self.get_profile(profile["voice_profile_id"])

    async def update_profile(
        self,
        voice_profile_id: str,
        data: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        profile = await self.get_profile(voice_profile_id)
        fields = dict(data)
        if fields.get("secret"):
            secret_ref, _ = self._secrets.put_secret(str(fields["secret"]))
            fields["secret_ref"] = secret_ref
        fields.pop("secret", None)
        await self._repo.update_profile(voice_profile_id, fields | {"updated_at": utc_now_iso()})
        await self._audit.write_event(
            actor_type="system",
            action="voice.profile.updated",
            object_type="voice_profile",
            object_id=voice_profile_id,
            summary="声音配置已更新",
            risk_level=RiskLevel.R1,
            payload={"voice_profile_id": voice_profile_id, "updated_fields": sorted(fields)},
            trace_id=trace_id,
        )
        return await self.get_profile(voice_profile_id)

    async def create_member_binding(
        self,
        data: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        member = await self._members.get_member(data["member_id"])
        if member is None:
            raise AppError(ErrorCode.NOT_FOUND, "成员不存在", status_code=404)
        profile = await self.get_profile(data["voice_profile_id"])
        now = utc_now_iso()
        binding = await self._repo.upsert_member_binding(
            {
                "binding_id": new_id("vbind"),
                "organization_id": member["organization_id"],
                "member_id": data["member_id"],
                "voice_profile_id": data["voice_profile_id"],
                "binding_scope": data.get("binding_scope") or "default",
                "reply_mode": data.get("reply_mode") or "explicit_request_only",
                "priority": int(data.get("priority") or 0),
                "status": data.get("status") or "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        binding["voice_display_name"] = profile["display_name"]
        await self._audit.write_event(
            actor_type="system",
            action="voice.binding.created",
            object_type="member_voice_binding",
            object_id=binding["binding_id"],
            summary="成员声音绑定已创建",
            risk_level=RiskLevel.R1,
            payload={
                "member_id": data["member_id"],
                "voice_profile_id": data["voice_profile_id"],
                "binding_scope": binding["binding_scope"],
                "reply_mode": binding["reply_mode"],
            },
            trace_id=trace_id,
        )
        return binding

    async def list_member_bindings(self, member_id: str) -> list[dict[str, Any]]:
        binding = await self._repo.get_member_binding(member_id)
        return [binding] if binding else []

    async def get_member_binding(self, member_id: str) -> dict[str, Any] | None:
        return await self._repo.get_member_binding(member_id)

    async def render_voice_reply(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        assistant_text: str,
        response_plan: dict[str, Any],
        persona: dict[str, Any] | None = None,
        heart: dict[str, Any] | None = None,
        risk_level: str = "R1",
        trace_id: str | None = None,
        message_id: str | None = None,
    ) -> VoiceRenderResult:
        decision = self._decide_voice_reply(
            user_text=user_text,
            assistant_text=assistant_text,
            response_plan=response_plan,
            risk_level=risk_level,
        )
        voice_reply = {
            "requested": decision.requested,
            "should_render": False,
            "reason": decision.reason,
            "provider": None,
            "voice_profile_id": None,
            "binding_id": None,
            "output_format": None,
            "voice_style_plan": {},
            "audio_uri": None,
            "audio_content_type": None,
            "render_job_id": None,
        }
        if not decision.requested:
            return VoiceRenderResult(render_job={}, voice_reply=voice_reply)

        profile, binding = await self._resolve_profile(turn["member_id"], decision.voice_profile_id)
        if profile is None:
            voice_reply["reason"] = "voice_profile_not_found"
            return VoiceRenderResult(render_job={}, voice_reply=voice_reply)
        if self._is_high_risk(response_plan, risk_level):
            voice_reply["reason"] = "high_risk_voice_blocked"
            return VoiceRenderResult(render_job={}, voice_reply=voice_reply)

        voice_style_plan = _build_voice_style_plan(
            assistant_text=assistant_text,
            persona=persona or {},
            heart=heart or {},
            response_plan=response_plan,
            profile=profile,
        )
        provider = self._provider_for_profile(profile)
        render_job = {
            "render_job_id": new_id("vrj"),
            "organization_id": turn.get("organization_id") or "org_default",
            "member_id": turn["member_id"],
            "conversation_id": turn.get("conversation_id"),
            "turn_id": turn["turn_id"],
            "message_id": message_id,
            "voice_profile_id": profile["voice_profile_id"],
            "provider": profile["provider"],
            "provider_voice_id": profile["provider_voice_id"],
            "status": "running",
            "source_text_hash": _hash_text(assistant_text),
            "source_text_preview": _truncate(assistant_text, 160),
            "voice_style_plan": voice_style_plan,
            "output_uri": None,
            "output_content_type": None,
            "output_size_bytes": None,
            "checksum": None,
            "provider_job_id": None,
            "provider_response": {},
            "degraded_reason": None,
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "completed_at": None,
        }
        await self._repo.insert_render_job(render_job)
        voice_reply.update(
            {
                "provider": profile["provider"],
                "voice_profile_id": profile["voice_profile_id"],
                "binding_id": binding["binding_id"] if binding else None,
                "output_format": profile["output_format"],
                "voice_style_plan": voice_style_plan,
                "render_job_id": render_job["render_job_id"],
            }
        )
        render_span = await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.VOICE_RENDER,
            name="render voice reply",
            metadata={
                "turn_id": turn["turn_id"],
                "member_id": turn["member_id"],
                "provider": profile["provider"],
                "voice_profile_id": profile["voice_profile_id"],
                "text_hash": render_job["source_text_hash"],
            },
        )
        try:
            audio_bytes, provider_response = await provider.render(
                VoiceRenderRequest(
                    organization_id=turn.get("organization_id") or "org_default",
                    member_id=turn["member_id"],
                    conversation_id=turn.get("conversation_id"),
                    turn_id=turn["turn_id"],
                    text=assistant_text,
                    voice_profile=profile,
                    persona=persona or {},
                    heart=heart or {},
                    response_plan=response_plan,
                    voice_style_plan=voice_style_plan,
                    risk_level=risk_level,
                    trace_id=trace_id,
                    message_id=message_id,
                )
            )
            output_format = str(provider_response.get("output_format") or profile["output_format"])
            output_content_type = str(
                provider_response.get("content_type") or _content_type(output_format)
            )
            output_path = self._output_path(render_job["render_job_id"], output_format)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(audio_bytes)
            output_uri = f"voice://{render_job['render_job_id']}/{output_path.name}"
            checksum = "sha256:" + hashlib.sha256(audio_bytes).hexdigest()
            now = utc_now_iso()
            render_job.update(
                {
                    "status": "completed",
                    "output_uri": output_uri,
                    "output_content_type": output_content_type,
                    "output_size_bytes": len(audio_bytes),
                    "checksum": checksum,
                    "provider_response": provider_response,
                    "updated_at": now,
                    "completed_at": now,
                }
            )
            await self._repo.update_render_job(
                render_job["render_job_id"],
                {
                    "message_id": message_id,
                    "status": "completed",
                    "output_uri": output_uri,
                    "output_content_type": render_job["output_content_type"],
                    "output_size_bytes": len(audio_bytes),
                    "checksum": checksum,
                    "provider_response": provider_response,
                    "updated_at": now,
                    "completed_at": now,
                },
            )
            await self._trace.end_span(
                render_span,
                output_data={
                    "render_job_id": render_job["render_job_id"],
                    "output_uri": output_uri,
                    "output_size_bytes": len(audio_bytes),
                },
            )
            await self._audit.write_event(
                actor_type="system",
                action="voice.render.completed",
                object_type="voice_render_job",
                object_id=render_job["render_job_id"],
                summary="语音回复已生成",
                risk_level=RiskLevel.R1,
                payload={
                    "render_job_id": render_job["render_job_id"],
                    "voice_profile_id": profile["voice_profile_id"],
                    "provider": profile["provider"],
                },
                trace_id=trace_id,
            )
            voice_reply.update(
                {
                    "should_render": True,
                    "reason": "rendered",
                    "output_format": _format_from_content_type(output_content_type, output_uri),
                    "audio_uri": output_uri,
                    "audio_content_type": render_job["output_content_type"],
                }
            )
            return VoiceRenderResult(render_job=render_job, voice_reply=voice_reply)
        except AppError as exc:
            now = utc_now_iso()
            render_job.update(
                {
                    "status": "degraded",
                    "degraded_reason": exc.message,
                    "updated_at": now,
                }
            )
            await self._repo.update_render_job(
                render_job["render_job_id"],
                {
                    "status": "degraded",
                    "degraded_reason": exc.message,
                    "updated_at": now,
                },
            )
            await self._trace.end_span(
                render_span,
                status=TraceSpanStatus.FAILED if exc.status_code >= 500 else TraceSpanStatus.COMPLETED,
                output_data={"reason": exc.message, "code": exc.code},
            )
            voice_reply["reason"] = exc.message
            return VoiceRenderResult(render_job=render_job, voice_reply=voice_reply)
        except Exception as exc:
            now = utc_now_iso()
            render_job.update(
                {
                    "status": "failed",
                    "degraded_reason": str(exc),
                    "updated_at": now,
                }
            )
            await self._repo.update_render_job(
                render_job["render_job_id"],
                {
                    "status": "failed",
                    "degraded_reason": str(exc),
                    "updated_at": now,
                },
            )
            await self._trace.end_span(
                render_span,
                status=TraceSpanStatus.FAILED,
                output_data={"reason": str(exc)},
            )
            voice_reply["reason"] = "voice_render_failed"
            return VoiceRenderResult(render_job=render_job, voice_reply=voice_reply)

    async def attach_message(
        self,
        *,
        render_job_id: str,
        message_id: str,
        trace_id: str | None = None,
    ) -> None:
        job = await self._repo.get_render_job(render_job_id)
        if job is None:
            return
        span_id = None
        if trace_id:
            span_id = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.VOICE_ATTACH,
                name="attach voice reply to message",
                metadata={"render_job_id": render_job_id, "message_id": message_id},
            )
        await self._repo.update_render_job(
            render_job_id,
            {
                "message_id": message_id,
                "updated_at": utc_now_iso(),
            },
        )
        await self._audit.write_event(
            actor_type="system",
            action="voice.render.attached",
            object_type="voice_render_job",
            object_id=render_job_id,
            summary="语音回复已关联到消息",
            risk_level=RiskLevel.R1,
            payload={"message_id": message_id, "render_job_id": render_job_id},
            trace_id=trace_id,
        )
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                output_data={"render_job_id": render_job_id, "message_id": message_id},
            )

    async def load_render_job_audio(
        self,
        render_job_id: str,
    ) -> tuple[bytes, str | None, str | None]:
        job = await self._repo.get_render_job(render_job_id)
        if job is None:
            raise AppError(ErrorCode.NOT_FOUND, "语音渲染任务不存在", status_code=404)
        output_uri = str(job.get("output_uri") or "")
        if not output_uri:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "语音渲染结果不存在",
                status_code=404,
                details={"render_job_id": render_job_id},
            )
        output_path = self._output_path(
            render_job_id,
            _format_from_content_type(
                str(job.get("output_content_type") or ""),
                output_uri,
            ),
        )
        if not output_path.exists():
            raise AppError(
                ErrorCode.NOT_FOUND,
                "语音文件不存在",
                status_code=404,
                details={"render_job_id": render_job_id},
            )
        audio_bytes = await asyncio.to_thread(output_path.read_bytes)
        return audio_bytes, job.get("output_content_type"), output_path.name

    async def resolve_voice_reply(
        self,
        *,
        turn: dict[str, Any],
        user_text: str,
        assistant_text: str,
        response_plan: dict[str, Any],
        persona: dict[str, Any] | None,
        heart: dict[str, Any] | None,
        risk_level: str,
        trace_id: str | None,
    ) -> VoiceReplyPlanResponse:
        result = await self.render_voice_reply(
            turn=turn,
            user_text=user_text,
            assistant_text=assistant_text,
            response_plan=response_plan,
            persona=persona,
            heart=heart,
            risk_level=risk_level,
            trace_id=trace_id,
        )
        return VoiceReplyPlanResponse(**result.voice_reply)

    def _provider_for_profile(self, profile: dict[str, Any]) -> VoiceProvider:
        provider = str(profile["provider"])
        if provider == "edge":
            return EdgeVoiceProvider()
        if provider == "hailuo_ai":
            secret = self._secrets.get_secret(profile.get("secret_ref"))
            endpoint = str(profile.get("config", {}).get("endpoint") or "")
            return HailuoVoiceProvider(endpoint=endpoint or None, api_key=secret)
        raise AppError(
            ErrorCode.VOICE_PROVIDER_UNAVAILABLE,
            f"不支持的语音 provider: {provider}",
            status_code=400,
        )

    async def _resolve_profile(
        self,
        member_id: str,
        requested_voice_profile_id: str | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        binding = await self._repo.get_member_binding(member_id)
        if requested_voice_profile_id:
            profile = await self._repo.get_profile(requested_voice_profile_id)
            if profile is not None:
                return profile, binding
        if binding:
            profile = await self._repo.get_profile(binding["voice_profile_id"])
            if profile is not None:
                return profile, binding
        profile = await self._repo.get_default_profile(provider="edge")
        if profile is not None:
            return profile, binding
        return None, binding

    def _output_path(self, render_job_id: str, output_format: str) -> Path:
        suffix = output_format.lstrip(".") or "wav"
        return self._data_dir / render_job_id / f"voice.{suffix}"

    def _decide_voice_reply(
        self,
        *,
        user_text: str,
        assistant_text: str,
        response_plan: dict[str, Any],
        risk_level: str,
    ) -> VoiceReplyPlanResponse:
        requested = _explicit_voice_request(user_text, response_plan)
        requested_voice_profile_id = _requested_voice_profile_id(response_plan)
        reason = "voice_not_requested"
        if requested and risk_level in {"R5", "R6", "R7"}:
            reason = "high_risk_voice_blocked"
            return VoiceReplyPlanResponse(
                requested=True,
                should_render=False,
                reason=reason,
                voice_profile_id=requested_voice_profile_id,
            )
        if requested:
            reason = "voice_request_detected"
            return VoiceReplyPlanResponse(
                requested=True,
                should_render=False,
                reason=reason,
                voice_profile_id=requested_voice_profile_id,
            )
        if _response_plan_voice_requested(response_plan):
            return VoiceReplyPlanResponse(
                requested=True,
                should_render=False,
                reason="voice_plan_requested",
                voice_profile_id=requested_voice_profile_id,
            )
        return VoiceReplyPlanResponse(requested=False, should_render=False, reason=reason)

    def _is_high_risk(self, response_plan: dict[str, Any], risk_level: str) -> bool:
        if risk_level in {"R5", "R6", "R7"}:
            return True
        if response_plan.get("safety_notice") or response_plan.get("approval_prompt"):
            return True
        structured = response_plan.get("structured_payload")
        if isinstance(structured, dict) and structured.get("risk_level") in {"R5", "R6", "R7"}:
            return True
        return False


def _explicit_voice_request(user_text: str, response_plan: dict[str, Any]) -> bool:
    plan_voice = _response_plan_voice_requested(response_plan)
    if plan_voice:
        return True
    text = user_text.strip()
    patterns = [
        r"用(?:声音|语音|语言)回复",
        r"用(?:你的|你)?(?:声音|语音)回",
        r"(?:发|回)语音",
        r"语音回复",
        r"声音回复",
        r"请(?:用|以)(?:声音|语音|语言)",
        r"念给我",
        r"读出来",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _response_plan_voice_requested(response_plan: dict[str, Any]) -> bool:
    structured = response_plan.get("structured_payload")
    if isinstance(structured, dict):
        voice = structured.get("voice_reply")
        if isinstance(voice, dict) and bool(voice.get("requested")):
            return True
    return bool(response_plan.get("voice_reply_requested"))


def _requested_voice_profile_id(response_plan: dict[str, Any]) -> str | None:
    structured = response_plan.get("structured_payload")
    if isinstance(structured, dict):
        voice = structured.get("voice_reply")
        if isinstance(voice, dict) and voice.get("voice_profile_id"):
            return str(voice["voice_profile_id"])
    return None


def _build_voice_style_plan(
    *,
    assistant_text: str,
    persona: dict[str, Any],
    heart: dict[str, Any],
    response_plan: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    tone_policy = dict(persona.get("tone_policy") or {})
    heart_pace = str(heart.get("preferred_pace") or "normal")
    response_style = str(response_plan.get("style") or "result_first")
    punct = _punctuation_profile(assistant_text)
    base_speed = float(tone_policy.get("conciseness", 0.72))
    warmth = float(tone_policy.get("warmth", 0.68))
    directness = float(tone_policy.get("directness", 0.78))
    if heart_pace == "concise":
        base_speed += 0.08
    elif heart_pace in {"slow_and_clear", "step_by_step"}:
        base_speed -= 0.08
    if str(heart.get("mood") or "") in {"anxious", "frustrated"}:
        warmth += 0.06
        base_speed -= 0.05
    if response_style in {"safety_boundary", "failure_recovery"}:
        warmth = min(warmth, 0.55)
        directness = max(directness, 0.82)
    segments = _segment_text(assistant_text)
    pause_ms = {
        "comma": 180 if punct["comma"] else 120,
        "period": 260 if punct["period"] else 180,
        "question": 220 if punct["question"] else 160,
        "exclamation": 240 if punct["exclamation"] else 180,
    }
    return {
        "provider": profile["provider"],
        "voice_profile_id": profile["voice_profile_id"],
        "voice_name": profile["display_name"],
        "tone_mode": response_plan.get("tone_mode") or persona.get("default_mode") or "default",
        "speaking_speed": round(max(0.78, min(base_speed, 1.18)), 3),
        "warmth": round(max(0.0, min(warmth, 1.0)), 3),
        "directness": round(max(0.0, min(directness, 1.0)), 3),
        "mood": heart.get("mood") or "steady",
        "preferred_pace": heart_pace,
        "punctuation": punct,
        "pause_ms": pause_ms,
        "segments": segments,
        "phrasing_guidance": _phrasing_guidance(persona, heart, response_plan),
    }


def _phrasing_guidance(
    persona: dict[str, Any],
    heart: dict[str, Any],
    response_plan: dict[str, Any],
) -> list[str]:
    hints = list(persona.get("style_principles") or [])
    if heart.get("deescalation_required"):
        hints.append("deescalate")
    if response_plan.get("safety_notice"):
        hints.append("safety_boundary")
    if response_plan.get("tool_notice"):
        hints.append("clear_tool_boundary")
    return list(dict.fromkeys(str(item) for item in hints if item))


def _segment_text(text: str) -> list[str]:
    chunks = [part.strip() for part in re.split(r"[。！？!?；;]\s*", text) if part.strip()]
    if chunks:
        return chunks[:12]
    return [text.strip()] if text.strip() else []


def _punctuation_profile(text: str) -> dict[str, int]:
    return {
        "comma": text.count("，") + text.count(","),
        "period": text.count("。") + text.count("."),
        "question": text.count("？") + text.count("?"),
        "exclamation": text.count("！") + text.count("!"),
    }


def _content_type(output_format: str) -> str:
    fmt = output_format.lstrip(".").lower()
    if fmt in {"wav", "wave"}:
        return "audio/wav"
    if fmt == "mp3":
        return "audio/mpeg"
    if fmt == "m4a":
        return "audio/mp4"
    return f"audio/{fmt or 'wav'}"


def _format_from_content_type(content_type: str, output_uri: str) -> str:
    lowered = content_type.lower()
    if "mpeg" in lowered:
        return "mp3"
    if "wav" in lowered:
        return "wav"
    if output_uri.endswith(".mp3"):
        return "mp3"
    if output_uri.endswith(".wav"):
        return "wav"
    if output_uri.endswith(".m4a"):
        return "m4a"
    return "wav"


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _truncate(text: str, limit: int) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[:limit]}..."


def _synthesize_wave(text: str, *, voice_key: str) -> bytes:
    sample_rate = 16000
    duration = max(1.1, min(6.0, 0.8 + len(text) / 40))
    base_freq = 180 + (sum(ord(ch) for ch in voice_key) % 80)
    amplitude = 0.18
    samples = int(sample_rate * duration)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for index in range(samples):
            wave_pos = index / sample_rate
            value = math.sin(2 * math.pi * base_freq * wave_pos)
            envelope = 1.0 - min(0.6, wave_pos / duration)
            sample = int(32767 * amplitude * envelope * value)
            frames.extend(int(sample).to_bytes(2, "little", signed=True))
        wav_file.writeframes(bytes(frames))
    return buffer.getvalue()


def _import_edge_tts():
    try:
        import edge_tts  # type: ignore
    except Exception:
        return None

    async def _render(request: VoiceRenderRequest) -> tuple[bytes, dict[str, Any]]:
        voice = request.voice_profile["provider_voice_id"]
        rate = request.voice_style_plan.get("speaking_speed") if hasattr(request, "voice_style_plan") else None
        speed = int((float(rate) - 1.0) * 100) if rate is not None else 0
        communicate = edge_tts.Communicate(
            request.text,
            voice=voice,
            rate=f"{speed:+d}%" if speed else "0%",
        )
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        audio = b"".join(chunks)
        if not audio:
            return _synthesize_wave(
                request.text,
                voice_key=request.voice_profile["provider_voice_id"],
            ), {
                "backend": "edge_tts",
                "voice": voice,
                "content_type": "audio/wav",
                "output_format": "wav",
                "fallback": True,
                "fallback_reason": "edge_tts_empty_audio",
            }
        return audio, {
            "backend": "edge_tts",
            "voice": voice,
            "content_type": "audio/mpeg",
            "output_format": "mp3",
        }

    return _render


def _extract_hailuo_audio(payload: Any) -> tuple[bytes, dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("audio"), str):
            return base64.b64decode(payload["audio"]), payload
        if isinstance(payload.get("audio_base64"), str):
            return base64.b64decode(payload["audio_base64"]), payload
        data = payload.get("data")
        if isinstance(data, dict):
            if isinstance(data.get("audio"), str):
                return base64.b64decode(data["audio"]), payload
            if isinstance(data.get("audio_base64"), str):
                return base64.b64decode(data["audio_base64"]), payload
        if isinstance(payload.get("url"), str):
            raise AppError(
                ErrorCode.VOICE_RENDER_FAILED,
                "海螺 AI 返回了音频 URL，但当前实现未配置下载逻辑",
                status_code=502,
            )
    raise AppError(
        ErrorCode.VOICE_RENDER_FAILED,
        "海螺 AI TTS 返回无法解析的音频内容",
        status_code=502,
    )
