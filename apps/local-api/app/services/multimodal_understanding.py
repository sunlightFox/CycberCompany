from __future__ import annotations

import csv
import io
import json
import re
import struct
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from core_types import Attachment, ChatContentPart, TraceSpanStatus, TraceSpanType
from trace_service import TraceService, redact

from app.db.repositories.channel_repo import ChannelRepository
from app.schemas.media import MediaSTTRequest, MediaSummarizeRequest
from app.services.media import MediaService
from app.services.memory import MemoryService

try:  # pragma: no cover - optional dependency guard
    from docx import Document
except Exception:  # pragma: no cover - optional dependency guard
    Document = None

try:  # pragma: no cover - optional dependency guard
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional dependency guard
    PdfReader = None

MAX_EXTRACT_CHARS = 6000
MAX_PREVIEW_CHARS = 1200


@dataclass(slots=True)
class AttachmentUnderstandingResult:
    attachment_id: str
    channel_attachment_id: str
    attachment_type: str
    status: Literal["understood", "degraded"]
    summary_text: str
    memory_text: str | None
    content_part: ChatContentPart
    metadata_patch: dict[str, Any]
    source_stub: dict[str, Any]
    analysis_artifact_id: str | None = None
    media_io_request_id: str | None = None
    memory_candidate_ids: list[str] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)

    def to_ingress_payload(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "channel_attachment_id": self.channel_attachment_id,
            "attachment_type": self.attachment_type,
            "status": self.status,
            "summary_text": str(redact(self.summary_text)),
            "analysis_artifact_id": self.analysis_artifact_id,
            "media_io_request_id": self.media_io_request_id,
            "memory_candidate_ids": list(self.memory_candidate_ids),
            "memory_ids": list(self.memory_ids),
        }


@dataclass(slots=True)
class MultimodalUnderstandingResult:
    content_parts: list[ChatContentPart]
    attachments: list[AttachmentUnderstandingResult]
    normalized_summary: dict[str, Any]
    ingress_payload: dict[str, Any]

    @property
    def understood_attachment_count(self) -> int:
        return sum(1 for item in self.attachments if item.status == "understood")

    @property
    def degraded_attachment_count(self) -> int:
        return sum(1 for item in self.attachments if item.status == "degraded")

    @property
    def memory_candidate_count(self) -> int:
        return sum(len(item.memory_candidate_ids) for item in self.attachments)


@dataclass(slots=True)
class AudioUnderstanding:
    summary_text: str
    status: Literal["understood", "degraded"]
    analysis_artifact_id: str | None = None
    io_request_id: str | None = None
    degradation_reason: str | None = None


class MultimodalUnderstandingService:
    def __init__(
        self,
        *,
        channel_repo: ChannelRepository,
        memory_service: MemoryService,
        media_service: MediaService,
        data_dir: Path,
        trace_service: TraceService,
    ) -> None:
        self._channel_repo = channel_repo
        self._memory = memory_service
        self._media = media_service
        self._data_dir = data_dir
        self._trace = trace_service
        self._blob_root = (data_dir / "channel-attachments" / "wechat").resolve()

    async def understand_wechat_attachments(
        self,
        *,
        account: dict[str, Any],
        session: dict[str, Any],
        channel_event_id: str,
        normalized: dict[str, Any],
        attachments: list[Attachment],
        trace_id: str | None,
        root_span_id: str | None = None,
    ) -> MultimodalUnderstandingResult:
        span_id = None
        if trace_id is not None:
            span_id = await self._trace.start_span(
                trace_id,
                span_type=TraceSpanType.CONTENT_NORMALIZE,
                name="wechat multimodal understanding",
                parent_span_id=root_span_id,
                input_data={
                    "channel_event_id": channel_event_id,
                    "attachment_count": len(attachments),
                    "message_type": normalized.get("message_type"),
                },
            )
        try:
            understood: list[AttachmentUnderstandingResult] = []
            content_parts: list[ChatContentPart] = []
            for attachment in attachments:
                result = await self._understand_single_attachment(
                    account=account,
                    session=session,
                    channel_event_id=channel_event_id,
                    normalized=normalized,
                    attachment=attachment,
                    trace_id=trace_id,
                    root_span_id=span_id,
                )
                understood.append(result)
                content_parts.append(result.content_part)
            normalized_summary = {
                "understanding_status": _overall_status(understood),
                "understood_attachment_count": sum(
                    1 for item in understood if item.status == "understood"
                ),
                "degraded_attachment_count": sum(
                    1 for item in understood if item.status == "degraded"
                ),
                "memory_candidate_count": sum(1 for item in understood if item.memory_text),
            }
            ingress_payload = {
                "status": normalized_summary["understanding_status"],
                "understood_attachment_count": normalized_summary["understood_attachment_count"],
                "degraded_attachment_count": normalized_summary["degraded_attachment_count"],
                "memory_candidate_count": normalized_summary["memory_candidate_count"],
                "attachments": [item.to_ingress_payload() for item in understood],
            }
            result = MultimodalUnderstandingResult(
                content_parts=content_parts,
                attachments=understood,
                normalized_summary=normalized_summary,
                ingress_payload=ingress_payload,
            )
            if span_id is not None:
                await self._trace.end_span(
                    span_id,
                    output_data={
                        "understanding_status": normalized_summary["understanding_status"],
                        "understood_attachment_count": normalized_summary[
                            "understood_attachment_count"
                        ],
                        "degraded_attachment_count": normalized_summary[
                            "degraded_attachment_count"
                        ],
                        "memory_candidate_count": normalized_summary["memory_candidate_count"],
                    },
                )
            return result
        except Exception as exc:
            if span_id is not None:
                await self._trace.end_span(
                    span_id,
                    status=TraceSpanStatus.FAILED,
                    output_data={"error": str(redact(str(exc)))},
                )
            raise

    async def commit_after_turn(
        self,
        result: MultimodalUnderstandingResult,
        *,
        account: dict[str, Any],
        session: dict[str, Any],
        channel_event_id: str,
        conversation_id: str | None,
        turn_id: str,
        message_id: str,
        trace_id: str | None,
    ) -> MultimodalUnderstandingResult:
        for item in result.attachments:
            metadata = dict(item.metadata_patch)
            if item.status == "understood" and item.memory_text:
                source = {
                    **item.source_stub,
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "channel_event_id": channel_event_id,
                    "trace_id": trace_id,
                }
                memory_result = await self._memory.record_multimodal_attachment(
                    summary_text=item.memory_text,
                    organization_id=str(account["organization_id"]),
                    member_id=str(session["member_id"]),
                    source=source,
                    trace_id=trace_id,
                    root_span_id=None,
                    status=item.status,
                )
                item.memory_candidate_ids.extend(
                    candidate.candidate_id for candidate in memory_result.candidates
                )
                item.memory_ids.extend(memory.memory_id for memory in memory_result.memories)
                metadata["memory_candidate_ids"] = list(item.memory_candidate_ids)
                metadata["memory_ids"] = list(item.memory_ids)
                if memory_result.reason:
                    metadata["memory_reason"] = memory_result.reason
                metadata["memory_status"] = (
                    "written" if memory_result.memories else "candidate"
                    if memory_result.candidates
                    else "skipped"
                )
            metadata["understanding_status"] = item.status
            metadata["understanding_summary"] = str(redact(item.summary_text))
            metadata["analysis_artifact_id"] = item.analysis_artifact_id
            metadata.setdefault("memory_candidate_ids", list(item.memory_candidate_ids))
            metadata.setdefault("memory_ids", list(item.memory_ids))
            metadata["channel_event_id"] = channel_event_id
            metadata["conversation_id"] = conversation_id
            metadata["turn_id"] = turn_id
            metadata["message_id"] = message_id
            await self._channel_repo.update_attachment(
                item.channel_attachment_id,
                {"metadata": metadata},
            )
            if item.media_io_request_id:
                await self._media.record_chat_binding(
                    media_id=str(item.source_stub.get("media_id") or "") or None,
                    io_request_id=item.media_io_request_id,
                    channel="wechat",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    message_id=message_id,
                    channel_event_id=channel_event_id,
                    channel_attachment_id=item.channel_attachment_id,
                    binding_type="attachment_understanding",
                    status=item.status,
                    evidence={
                        "summary_text": item.summary_text,
                        "analysis_artifact_id": item.analysis_artifact_id,
                        "attachment_type": item.attachment_type,
                    },
                    trace_id=trace_id,
                )
            item.metadata_patch = metadata
        return result

    async def _understand_single_attachment(
        self,
        *,
        account: dict[str, Any],
        session: dict[str, Any],
        channel_event_id: str,
        normalized: dict[str, Any],
        attachment: Attachment,
        trace_id: str | None,
        root_span_id: str | None,
    ) -> AttachmentUnderstandingResult:
        attachment_id = str(attachment.attachment_id or "")
        channel_attachment_id = str(
            attachment.metadata.get("channel_attachment_id") or attachment_id
        )
        attachment_type = str(
            attachment.metadata.get("attachment_type")
            or _attachment_type_from_attachment(attachment)
        )
        analysis_artifact_id = attachment.metadata.get("artifact_id")
        summary_text = ""
        memory_text: str | None = None
        status: Literal["understood", "degraded"] = "degraded"
        degradation_reason: str | None = None
        content_part_type: Literal["image_summary", "audio_transcript", "file_extract"]
        media_io_request_id = attachment.metadata.get("media_io_request_id")
        source_stub = {
            "type": "multimodal_attachment",
            "conversation_id": session.get("conversation_id"),
            "turn_id": None,
            "message_id": None,
            "channel_event_id": channel_event_id,
            "channel_attachment_id": channel_attachment_id,
            "media_id": attachment.metadata.get("media_id"),
            "artifact_id": analysis_artifact_id,
            "attachment_type": attachment_type,
            "trace_id": trace_id,
        }
        media_id = attachment.metadata.get("media_id")
        if attachment_type == "audio":
            content_part_type = "audio_transcript"
            audio = await self._understand_audio(attachment=attachment, trace_id=trace_id)
            summary_text = audio.summary_text
            analysis_artifact_id = audio.analysis_artifact_id
            media_io_request_id = audio.io_request_id
            status = audio.status
            degradation_reason = audio.degradation_reason
            memory_text = (
                _memory_text_from_extracted(summary_text) if status == "understood" else None
            )
        elif attachment_type in {"image", "video", "document"} and media_id:
            content_part_type = "image_summary" if attachment_type == "image" else "file_extract"
            response = await self._media.summarize(
                str(media_id),
                MediaSummarizeRequest(provider="local", summary_type=attachment_type),
                trace_id=trace_id,
            )
            summary_text = (
                response.summaries[0].summary_text
                if response.summaries
                else str(response.evidence.get("summary_preview") or response.message or "")
            )
            analysis_artifact_id = (
                response.artifacts[0].artifact_id if response.artifacts else None
            )
            media_io_request_id = response.evidence.get("io_request_id")
            if attachment_type == "image":
                status = "degraded"
                degradation_reason = response.degraded_reason or "vision_provider_unavailable"
                memory_text = None
            else:
                status = "understood" if response.status == "completed" else "degraded"
                degradation_reason = response.degraded_reason
                memory_text = (
                    _memory_text_from_extracted(summary_text) if status == "understood" else None
                )
        elif attachment_type == "image":
            content_part_type = "image_summary"
            summary_text = _summarize_image(
                attachment=attachment,
                normalized=normalized,
                blob_root=self._blob_root,
                blob_uri=str(attachment.uri or ""),
            )
            status = "degraded"
            degradation_reason = "vision_provider_unavailable"
        else:
            content_part_type = "file_extract"
            extracted = await self._extract_file_text(attachment, trace_id=trace_id)
            if extracted:
                status = "understood"
                summary_text = f"文件内容摘录：{extracted}"
                memory_text = _memory_text_from_extracted(extracted)
            else:
                summary_text = _unsupported_file_summary(attachment, normalized=normalized)
                degradation_reason = "unsupported_file_format"
        source_stub["artifact_id"] = analysis_artifact_id
        source_stub["media_io_request_id"] = media_io_request_id
        metadata_patch = {
            **dict(attachment.metadata),
            "untrusted_external_content": True,
            "source": "wechat",
            "attachment_type": attachment_type,
            "understanding_status": status,
            "understanding_summary": str(redact(summary_text)),
            "degradation_reason": degradation_reason,
            "analysis_artifact_id": analysis_artifact_id,
            "media_id": attachment.metadata.get("media_id"),
            "media_io_request_id": media_io_request_id,
        }
        part = ChatContentPart(
            type=content_part_type,
            text=str(redact(summary_text)),
            name={
                "image_summary": "图片内容线索",
                "audio_transcript": "语音内容线索",
                "file_extract": "文件内容摘录",
            }[content_part_type],
            metadata={
                "source": "wechat",
                "channel_attachment_id": channel_attachment_id,
                "attachment_type": attachment_type,
                "understanding_status": status,
                "degradation_reason": degradation_reason,
                "analysis_artifact_id": analysis_artifact_id,
                "media_id": attachment.metadata.get("media_id"),
                "media_io_request_id": media_io_request_id,
                "untrusted_external_content": True,
            },
        )
        return AttachmentUnderstandingResult(
            attachment_id=attachment_id,
            channel_attachment_id=channel_attachment_id,
            attachment_type=attachment_type,
            status=status,
            summary_text=summary_text,
            memory_text=memory_text,
            content_part=part,
            metadata_patch=metadata_patch,
            source_stub=source_stub,
            analysis_artifact_id=str(analysis_artifact_id) if analysis_artifact_id else None,
            media_io_request_id=str(media_io_request_id) if media_io_request_id else None,
        )

    async def _understand_audio(
        self,
        *,
        attachment: Attachment,
        trace_id: str | None,
    ) -> AudioUnderstanding:
        transcript = _transcript_from_attachment_metadata(attachment)
        audio_details = _audio_details_for_attachment(
            blob_root=self._blob_root,
            blob_uri=str(attachment.uri or ""),
        )
        if transcript:
            summary = f"语音转成文字：{transcript}"
            if audio_details:
                summary = f"{summary}（音频基础信息：{audio_details}。）"
            return AudioUnderstanding(summary_text=summary, status="understood")
        media_id = attachment.metadata.get("media_id")
        if not media_id:
            summary = "语音内容线索：我收到了这段语音，但现在还没有可用的转写文字。"
            if audio_details:
                summary = f"{summary}音频基础信息：{audio_details}。"
            return AudioUnderstanding(
                summary_text=summary,
                status="degraded",
                degradation_reason="transcription_media_missing",
            )
        try:
            response = await self._media.stt(
                str(media_id),
                MediaSTTRequest(provider="local"),
                trace_id=trace_id,
            )
        except Exception:
            summary = "语音内容线索：我收到了这段语音，但现在还没有可用的转写文字。"
            if audio_details:
                summary = f"{summary}音频基础信息：{audio_details}。"
            return AudioUnderstanding(
                summary_text=summary,
                status="degraded",
                degradation_reason="transcription_provider_unavailable",
            )
        artifact_id = response.transcripts[0].artifact_id if response.transcripts else None
        io_request_id = response.evidence.get("io_request_id") if isinstance(response.evidence, dict) else None
        transcript_text = (
            response.transcripts[0].summary_text
            if response.transcripts
            else str(response.evidence.get("transcript_preview") or response.message or "")
        )
        if response.status == "degraded":
            summary = (
                f"语音转成文字：{transcript}"
                if transcript and transcript != "语音转写已完成，但没有返回可读文本。"
                else "语音内容线索：我收到了这段语音，但现在还没有可用的转写文字。"
            )
            if audio_details:
                summary = f"{summary}音频基础信息：{audio_details}。"
            return AudioUnderstanding(
                summary_text=summary,
                status="degraded",
                analysis_artifact_id=artifact_id,
                io_request_id=io_request_id,
                degradation_reason=response.degraded_reason
                or "transcription_provider_unavailable",
            )
        transcript = str(redact(transcript_text or "")).strip() or "语音转写已完成，但没有返回可读文本。"
        summary = f"语音转成文字：{transcript}"
        if audio_details:
            summary = f"{summary}（音频基础信息：{audio_details}。）"
        return AudioUnderstanding(
            summary_text=summary,
            status="understood",
            analysis_artifact_id=artifact_id,
            io_request_id=io_request_id,
        )

    async def _extract_file_text(
        self,
        attachment: Attachment,
        *,
        trace_id: str | None,
    ) -> str | None:
        path = _resolve_blob_path(self._blob_root, str(attachment.uri or ""))
        if path is None or not path.exists():
            return None
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md", ".json", ".csv"}:
            return _extract_text_file(path)
        if suffix == ".pdf":
            return _extract_pdf_text(path)
        if suffix == ".docx":
            return _extract_docx_text(path)
        return None


def _overall_status(items: list[AttachmentUnderstandingResult]) -> str:
    understood = sum(1 for item in items if item.status == "understood")
    degraded = sum(1 for item in items if item.status == "degraded")
    if understood and degraded:
        return "mixed"
    if understood:
        return "understood"
    if degraded:
        return "degraded"
    return "none"


def _attachment_type_from_attachment(attachment: Attachment) -> str:
    content_type = str(attachment.content_type or "").lower()
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("audio/"):
        return "audio"
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("text/") or content_type.startswith("application/pdf"):
        return "document"
    return "file"


def _summarize_image(
    *,
    attachment: Attachment,
    normalized: dict[str, Any],
    blob_root: Path,
    blob_uri: str,
) -> str:
    name = str(redact(attachment.name or "图片"))
    content_type = str(attachment.content_type or "image/*")
    image_details = _image_details_for_attachment(blob_root=blob_root, blob_uri=blob_uri)
    hint = _ascii_hint_for_attachment(blob_root=blob_root, blob_uri=blob_uri)
    parts = [f"图片内容线索：收到一张图片，文件名 {name}，格式 {content_type}"]
    if image_details:
        parts.append(f"基础信息 {image_details}")
    size_bytes = attachment.metadata.get("size_bytes")
    if size_bytes is not None:
        parts.append(f"大小 {size_bytes} 字节")
    if hint:
        parts.append(f"图里可读到的文本线索 {hint}")
    parts.append("现在还不能完整看清画面细节或图片里的文字，不能凭空补细节。")
    return "，".join(parts)


def _unsupported_file_summary(
    attachment: Attachment,
    *,
    normalized: dict[str, Any],
) -> str:
    name = str(redact(attachment.name or "文件"))
    content_type = str(attachment.content_type or "application/octet-stream")
    message_type = str(normalized.get("message_type") or "media")
    return (
        f"文件内容线索：收到 {name}（{content_type}，{message_type}），"
        "这个格式现在只能安全保存，暂时不能打开阅读正文。"
    )


def _transcript_from_attachment_metadata(attachment: Attachment) -> str | None:
    for key in (
        "transcript_text",
        "transcript",
        "recognized_text",
        "asr_text",
        "voice_text",
        "speech_text",
    ):
        value = attachment.metadata.get(key)
        if isinstance(value, str) and value.strip():
            return str(redact(value.strip()))[:MAX_PREVIEW_CHARS]
    return None


def _memory_text_from_extracted(text: str) -> str:
    cleaned = " ".join(str(redact(text)).split())
    return cleaned[:MAX_PREVIEW_CHARS]


def _image_details_for_attachment(*, blob_root: Path, blob_uri: str) -> str | None:
    path = _resolve_blob_path(blob_root, blob_uri)
    if path is None or not path.exists():
        return None
    try:
        raw = path.read_bytes()[:4096]
    except Exception:
        return None
    detected = _detect_image_dimensions(raw)
    if detected is None:
        return None
    image_format, width, height = detected
    return f"{image_format} {width}x{height}"


def _audio_details_for_attachment(*, blob_root: Path, blob_uri: str) -> str | None:
    path = _resolve_blob_path(blob_root, blob_uri)
    if path is None or not path.exists():
        return None
    suffix = path.suffix.lower()
    if suffix not in {".wav", ".wave"}:
        return None
    try:
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_rate = audio.getframerate()
            frame_count = audio.getnframes()
            duration = frame_count / sample_rate if sample_rate else 0.0
    except Exception:
        return None
    return (
        f"WAV，时长 {duration:.2f} 秒，采样率 {sample_rate} Hz，"
        f"{channels} 声道"
    )


def _detect_image_dimensions(raw: bytes) -> tuple[str, int, int] | None:
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24:
        width, height = struct.unpack(">II", raw[16:24])
        if width > 0 and height > 0:
            return ("PNG", width, height)
    if raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a"):
        if len(raw) >= 10:
            width, height = struct.unpack("<HH", raw[6:10])
            if width > 0 and height > 0:
                return ("GIF", width, height)
    if raw.startswith(b"\xff\xd8"):
        return _detect_jpeg_dimensions(raw)
    if raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return _detect_webp_dimensions(raw)
    return None


def _detect_jpeg_dimensions(raw: bytes) -> tuple[str, int, int] | None:
    index = 2
    while index + 9 < len(raw):
        if raw[index] != 0xFF:
            index += 1
            continue
        marker = raw[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA or index + 2 > len(raw):
            break
        segment_length = struct.unpack(">H", raw[index : index + 2])[0]
        if segment_length < 2 or index + segment_length > len(raw):
            break
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if index + 7 <= len(raw):
                height, width = struct.unpack(">HH", raw[index + 3 : index + 7])
                if width > 0 and height > 0:
                    return ("JPEG", width, height)
        index += segment_length
    return None


def _detect_webp_dimensions(raw: bytes) -> tuple[str, int, int] | None:
    if len(raw) < 30:
        return None
    chunk = raw[12:16]
    if chunk == b"VP8X" and len(raw) >= 30:
        width = 1 + int.from_bytes(raw[24:27], "little")
        height = 1 + int.from_bytes(raw[27:30], "little")
        if width > 0 and height > 0:
            return ("WEBP", width, height)
    if chunk == b"VP8 " and len(raw) >= 30:
        width = struct.unpack("<H", raw[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", raw[28:30])[0] & 0x3FFF
        if width > 0 and height > 0:
            return ("WEBP", width, height)
    if chunk == b"VP8L" and len(raw) >= 25:
        bits = int.from_bytes(raw[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        if width > 0 and height > 0:
            return ("WEBP", width, height)
    return None


def _ascii_hint_for_attachment(*, blob_root: Path, blob_uri: str) -> str | None:
    path = _resolve_blob_path(blob_root, blob_uri)
    if path is None or not path.exists():
        return None
    try:
        raw = path.read_bytes()[:1024]
    except Exception:
        return None
    matches = [
        bytes(match).decode("utf-8", errors="ignore").strip()
        for match in re.findall(rb"[ -~]{4,}", raw)
    ]
    hints = [item for item in matches if item and not item.lower().endswith(".json")]
    if not hints:
        return None
    return hints[0][:120]


def _resolve_blob_path(blob_root: Path, blob_uri: str) -> Path | None:
    parsed = urlparse(blob_uri)
    if parsed.scheme != "channel-attachment" or parsed.netloc != "wechat":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3:
        return None
    account_id, event_id, attachment_id = parts
    if any(part in {".", ".."} for part in parts):
        return None
    blob_root = blob_root.resolve()
    candidate_dir = (blob_root / account_id / event_id).resolve()
    if blob_root not in candidate_dir.parents and candidate_dir != blob_root:
        return None
    candidates = sorted(
        path
        for path in candidate_dir.glob(f"{attachment_id}*")
        if path.is_file() and not path.name.endswith(".json.json")
    )
    if not candidates:
        return None
    preferred = [path for path in candidates if not path.name.endswith(".json")]
    chosen = preferred[0] if preferred else candidates[0]
    resolved = chosen.resolve()
    if blob_root not in resolved.parents and resolved != blob_root:
        return None
    return resolved


def _extract_text_file(path: Path) -> str | None:
    raw = path.read_bytes()[:MAX_EXTRACT_CHARS * 4]
    if path.suffix.lower() == ".json":
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
            text = json.dumps(parsed, ensure_ascii=False, indent=2)
        except Exception:
            text = raw.decode("utf-8", errors="replace")
    elif path.suffix.lower() == ".csv":
        text = raw.decode("utf-8", errors="replace")
        try:
            rows = list(csv.reader(io.StringIO(text)))
            text = "\n".join(", ".join(row) for row in rows[:60])
        except Exception:
            pass
    else:
        text = raw.decode("utf-8", errors="replace")
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return None
    return cleaned[:MAX_EXTRACT_CHARS]


def _extract_pdf_text(path: Path) -> str | None:
    if PdfReader is None:
        return None
    try:
        reader = PdfReader(str(path))
    except Exception:
        return None
    chunks: list[str] = []
    for page in reader.pages[:3]:
        try:
            chunks.append(str(page.extract_text() or ""))
        except Exception:
            continue
    cleaned = " ".join(" ".join(chunks).split()).strip()
    return cleaned[:MAX_EXTRACT_CHARS] or None


def _extract_docx_text(path: Path) -> str | None:
    if Document is None:
        return None
    try:
        doc = Document(str(path))
    except Exception:
        return None
    paras = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
    cleaned = " ".join(" ".join(paras).split()).strip()
    return cleaned[:MAX_EXTRACT_CHARS] or None
