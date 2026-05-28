from __future__ import annotations

from pathlib import Path
from typing import Any

from trace_service import redact

from app.core.config import ChannelProviderSection
from app.services.channels import ChannelBindingService
from app.services.notifications import ProviderDeliveryResult
from app.services.voice import VoiceService


class ChannelBindingNotificationProvider:
    def __init__(self, channels: ChannelBindingService, *, voice_service: VoiceService) -> None:
        self._channels = channels
        self._voice = voice_service

    async def send(self, *, channel, message):  # type: ignore[no-untyped-def]
        provider_state_ref = None
        if isinstance(channel.provider_config, dict):
            provider_state_ref = channel.provider_config.get("provider_state_ref")
        voice_reply = message.metadata.get("voice_reply") if isinstance(message.metadata, dict) else None
        if (
            isinstance(voice_reply, dict)
            and voice_reply.get("requested")
            and not voice_reply.get("should_render")
            and not voice_reply.get("allow_text_fallback")
        ):
            return ProviderDeliveryResult(
                status="rejected",
                error_code="message_rejected",
                error_summary=str(voice_reply.get("reason") or "voice reply was not rendered"),
                response_summary={
                    "retryable": False,
                    "delivery_kind": "audio",
                    "reason": "voice_reply_not_rendered",
                },
            )
        if isinstance(voice_reply, dict) and voice_reply.get("should_render"):
            render_job_id = voice_reply.get("render_job_id")
            if not render_job_id:
                return ProviderDeliveryResult(
                    status="rejected",
                    error_code="message_rejected",
                    error_summary="voice reply missing render_job_id",
                    response_summary={"retryable": False, "delivery_kind": "audio"},
                )
            try:
                audio_bytes, content_type, filename = await self._voice.load_render_job_audio(
                    str(render_job_id)
                )
            except Exception as exc:
                return ProviderDeliveryResult(
                    status="rejected",
                    error_code="message_rejected",
                    error_summary=str(redact(str(exc))),
                    response_summary={
                        "retryable": False,
                        "delivery_kind": "audio",
                        "reason": "voice_audio_unavailable",
                    },
                )
            result = await self._channels.send_channel_audio(
                provider=channel.provider,
                provider_state_ref=provider_state_ref,
                recipient=message.recipient,
                audio_bytes=audio_bytes,
                content_type=content_type,
                filename=filename,
            )
            return ProviderDeliveryResult(
                status=result.status,
                provider_message_id=result.provider_message_id,
                response_summary=result.response_summary,
                error_code=result.error_code,
                error_summary=result.error_summary,
            )
        result = await self._channels.send_channel_text(
            provider=channel.provider,
            provider_state_ref=provider_state_ref,
            recipient=message.recipient,
            text=message.body_redacted,
        )
        attachments = notification_attachments(message.metadata)
        if result.status == "sent" and attachments:
            attachment_results: list[dict[str, Any]] = []
            for attachment in attachments:
                artifact_id = str(attachment.get("artifact_id") or "")
                if not artifact_id:
                    continue
                file_result = await self._channels.send_channel_file(
                    provider=channel.provider,
                    provider_state_ref=provider_state_ref,
                    recipient=message.recipient,
                    artifact_id=artifact_id,
                    content_type=maybe_str(attachment.get("content_type")),
                    filename=maybe_str(attachment.get("display_name")),
                )
                item = {
                    "artifact_id": artifact_id,
                    "status": file_result.status,
                    "filename": attachment.get("display_name"),
                }
                if file_result.error_code:
                    item["error_code"] = file_result.error_code
                if file_result.error_summary:
                    item["error_summary"] = file_result.error_summary
                attachment_results.append(item)
            attachment_status = "sent"
            if attachment_results and any(item["status"] != "sent" for item in attachment_results):
                attachment_status = (
                    "failed_all"
                    if all(item["status"] != "sent" for item in attachment_results)
                    else "degraded"
                )
            response_summary = {
                **dict(result.response_summary or {}),
                "delivery_kind": "text_with_attachment",
                "attachment_delivery_status": attachment_status,
                "attachment_results": attachment_results,
            }
        else:
            response_summary = result.response_summary
        return ProviderDeliveryResult(
            status=result.status,
            provider_message_id=result.provider_message_id,
            response_summary=response_summary,
            error_code=result.error_code,
            error_summary=result.error_summary,
        )


def default_channel_state_dir(data_dir: Path, provider: str) -> Path:
    return data_dir / "channel-providers" / provider


def normalize_channel_provider_variants(
    provider_configs: dict[str, ChannelProviderSection],
    *,
    data_dir: Path,
    primary_provider: str,
    mock_provider: str,
) -> dict[str, ChannelProviderSection]:
    normalized = dict(provider_configs)
    if primary_provider not in normalized:
        normalized[primary_provider] = ChannelProviderSection()
    if mock_provider not in normalized:
        normalized[mock_provider] = ChannelProviderSection(enabled=True, test_only=True)
    for provider in (primary_provider, mock_provider):
        config = normalized[provider]
        if config.state_dir is None:
            normalized[provider] = config.model_copy(
                update={"state_dir": default_channel_state_dir(data_dir, provider)}
            )
    return normalized


def build_channel_notification_providers(
    providers: tuple[str, ...],
    channels: ChannelBindingService,
    *,
    voice_service: VoiceService,
) -> dict[str, object]:
    provider = ChannelBindingNotificationProvider(channels, voice_service=voice_service)
    return {name: provider for name in providers}


def notification_attachments(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get("attachments")
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def maybe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
