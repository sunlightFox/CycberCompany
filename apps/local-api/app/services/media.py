from __future__ import annotations

import asyncio
import io
import hashlib
import json
import shutil
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core_types import (
    ErrorCode,
    MediaAnalysis,
    MediaAsset,
    MediaChatBinding,
    MediaDerivative,
    MediaEditPlan,
    MediaIORecord,
    MediaMultimodalSummary,
    MediaProviderHealthRecord,
    RiskLevel,
    MediaSpeechRender,
    MediaSpeechTranscript,
    TaskArtifact,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.media_repo import MediaRepository
from app.db.repositories.task_repo import TaskRepository
from app.schemas.media import (
    MediaEditPlanCreateRequest,
    MediaEditPlanResponse,
    MediaExportArtifactRequest,
    MediaExtractAudioRequest,
    MediaExtractFramesRequest,
    MediaIORecordResponse,
    MediaImportArtifactRequest,
    MediaOperationResponse,
    MediaProbeRequest,
    MediaProviderHealthResponse,
    MediaRenderEditRequest,
    MediaSceneDetectRequest,
    MediaSTTRequest,
    MediaSummarizeRequest,
    MediaTimelineRequest,
    MediaTTSRequest,
    MediaTranscribeAudioRequest,
)
from app.services.artifacts import ArtifactStore
from app.services.audit import AuditEventService


@dataclass(frozen=True)
class RuntimeFileOutput:
    path: Path
    display_name: str
    content_type: str
    metadata: dict[str, Any]
    time_ms: int | None = None


class MediaRuntime:
    def __init__(
        self,
        *,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        timeout_seconds: int = 30,
        max_output_bytes: int = 80_000,
    ) -> None:
        self._ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
        self._ffprobe = ffprobe_path or shutil.which("ffprobe")
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes

    def status(self) -> dict[str, Any]:
        return {
            "backend": "ffmpeg",
            "ffmpeg_available": bool(self._ffmpeg),
            "ffprobe_available": bool(self._ffprobe),
            "degraded_reason": None
            if self._ffmpeg and self._ffprobe
            else "ffmpeg_or_ffprobe_missing",
        }

    async def probe(self, source: Path) -> dict[str, Any]:
        if not self._ffprobe:
            raise _media_unavailable("ffprobe_missing")
        completed = await self._run(
            [
                self._ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(source),
            ]
        )
        try:
            raw = json.loads(completed["stdout"] or "{}")
        except json.JSONDecodeError as exc:
            raise AppError(
                ErrorCode.MEDIA_RUNTIME_FAILED,
                "ffprobe 输出无法解析",
                status_code=502,
            ) from exc
        return _probe_summary(raw, backend="ffprobe")

    async def extract_frames(
        self,
        source: Path,
        work_dir: Path,
        request: MediaExtractFramesRequest,
    ) -> list[RuntimeFileOutput]:
        if not self._ffmpeg:
            raise _media_unavailable("ffmpeg_missing")
        work_dir.mkdir(parents=True, exist_ok=True)
        timestamps = request.timestamps_ms[: request.max_frames]
        if not timestamps:
            timestamps = [index * request.interval_ms for index in range(request.max_frames)]
        outputs: list[RuntimeFileOutput] = []
        for index, time_ms in enumerate(timestamps):
            target = work_dir / f"frame_{index:03d}.png"
            await self._run(
                [
                    self._ffmpeg,
                    "-y",
                    "-ss",
                    f"{max(time_ms, 0) / 1000:.3f}",
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    str(target),
                ]
            )
            outputs.append(
                RuntimeFileOutput(
                    path=target,
                    display_name=target.name,
                    content_type="image/png",
                    time_ms=time_ms,
                    metadata={"mode": request.mode, "frame_index": index},
                )
            )
        return outputs

    async def extract_audio(
        self,
        source: Path,
        work_dir: Path,
        request: MediaExtractAudioRequest,
    ) -> RuntimeFileOutput:
        if not self._ffmpeg:
            raise _media_unavailable("ffmpeg_missing")
        extension = "wav" if request.output_format not in {"mp3", "m4a"} else request.output_format
        target = work_dir / f"audio.{extension}"
        target.parent.mkdir(parents=True, exist_ok=True)
        codec_args = ["-acodec", "pcm_s16le"] if extension == "wav" else []
        await self._run([self._ffmpeg, "-y", "-i", str(source), "-vn", *codec_args, str(target)])
        return RuntimeFileOutput(
            path=target,
            display_name=target.name,
            content_type="audio/wav" if extension == "wav" else f"audio/{extension}",
            metadata={"output_format": extension},
        )

    async def render_edit(
        self,
        source: Path,
        work_dir: Path,
        edit_plan: MediaEditPlan,
    ) -> RuntimeFileOutput:
        if not self._ffmpeg:
            raise _media_unavailable("ffmpeg_missing")
        work_dir.mkdir(parents=True, exist_ok=True)
        operation = _first_trim_operation(edit_plan.operations)
        target = work_dir / "rendered.mp4"
        command = [self._ffmpeg, "-y"]
        if operation:
            command.extend(["-ss", f"{operation['source_start_ms'] / 1000:.3f}"])
        command.extend(["-i", str(source)])
        if operation:
            command.extend(["-to", f"{operation['source_end_ms'] / 1000:.3f}"])
        command.extend(["-c", "copy", str(target)])
        await self._run(command)
        return RuntimeFileOutput(
            path=target,
            display_name="rendered.mp4",
            content_type="video/mp4",
            metadata={"renderer": "ffmpeg", "operation_count": len(edit_plan.operations)},
        )

    async def _run(self, command: list[str]) -> dict[str, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            process.kill()
            raise AppError(ErrorCode.TOOL_TIMEOUT, "媒体后端执行超时", status_code=504) from exc
        stdout_text = stdout[: self._max_output].decode("utf-8", errors="replace")
        stderr_text = stderr[: self._max_output].decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise AppError(
                ErrorCode.MEDIA_RUNTIME_FAILED,
                "媒体后端执行失败",
                status_code=502,
                details={"stderr": str(redact(stderr_text))[:1000]},
            )
        return {"stdout": str(redact(stdout_text)), "stderr": str(redact(stderr_text))}


class FakeMediaRuntime(MediaRuntime):
    def __init__(self) -> None:
        pass

    def status(self) -> dict[str, Any]:
        return {
            "backend": "fake_media_runtime",
            "ffmpeg_available": True,
            "ffprobe_available": True,
        }

    async def probe(self, source: Path) -> dict[str, Any]:
        size = source.stat().st_size if source.exists() else 0
        return {
            "duration_ms": 42000,
            "width": 1280,
            "height": 720,
            "frame_rate": 30.0,
            "audio_streams": 1,
            "video_streams": 1,
            "format": {"duration": 42.0, "size": size},
            "backend": "fake_media_runtime",
        }

    async def extract_frames(
        self,
        source: Path,
        work_dir: Path,
        request: MediaExtractFramesRequest,
    ) -> list[RuntimeFileOutput]:
        del source
        work_dir.mkdir(parents=True, exist_ok=True)
        timestamps = request.timestamps_ms[: request.max_frames] or [
            index * request.interval_ms for index in range(min(request.max_frames, 3))
        ]
        outputs: list[RuntimeFileOutput] = []
        for index, time_ms in enumerate(timestamps):
            path = work_dir / f"frame_{index:03d}.png"
            path.write_bytes(_FAKE_PNG_BYTES)
            outputs.append(
                RuntimeFileOutput(
                    path=path,
                    display_name=path.name,
                    content_type="image/png",
                    time_ms=time_ms,
                    metadata={"mode": request.mode, "fake": True, "frame_index": index},
                )
            )
        return outputs

    async def extract_audio(
        self,
        source: Path,
        work_dir: Path,
        request: MediaExtractAudioRequest,
    ) -> RuntimeFileOutput:
        del source
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / "audio.wav"
        path.write_bytes(b"RIFF$\x00\x00\x00WAVEfmt ")
        return RuntimeFileOutput(
            path=path,
            display_name=path.name,
            content_type="audio/wav",
            metadata={"output_format": request.output_format, "fake": True},
        )

    async def render_edit(
        self,
        source: Path,
        work_dir: Path,
        edit_plan: MediaEditPlan,
    ) -> RuntimeFileOutput:
        del source
        work_dir.mkdir(parents=True, exist_ok=True)
        path = work_dir / "rendered.mp4"
        path.write_bytes(b"\x00\x00\x00 ftypisomphase43-rendered-video")
        return RuntimeFileOutput(
            path=path,
            display_name="rendered.mp4",
            content_type="video/mp4",
            metadata={
                "renderer": "fake_media_runtime",
                "operation_count": len(edit_plan.operations),
            },
        )


class MediaService:
    def __init__(
        self,
        *,
        repo: MediaRepository,
        task_repo: TaskRepository,
        artifact_store: ArtifactStore,
        trace_service: TraceService,
        audit_service: AuditEventService,
        runtime: MediaRuntime | None = None,
    ) -> None:
        self._repo = repo
        self._task_repo = task_repo
        self._artifacts = artifact_store
        self._trace = trace_service
        self._audit = audit_service
        self._runtime = runtime or MediaRuntime()

    def set_runtime(self, runtime: MediaRuntime) -> None:
        self._runtime = runtime

    def runtime_status(self) -> dict[str, Any]:
        return self._runtime.status()

    async def import_artifact(
        self,
        request: MediaImportArtifactRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        artifact = await self._artifact(request.artifact_id)
        if artifact.task_id != request.task_id:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "媒体输入必须来自同一任务 artifact",
                status_code=403,
            )
        media_type = request.media_type or _infer_media_type(artifact)
        if media_type not in {"video", "audio", "image", "document"}:
            raise AppError(
                ErrorCode.MEDIA_PLAN_INVALID,
                "artifact 不是受支持的媒体类型",
                status_code=422,
            )
        existing = await self._repo.get_asset_by_source(artifact.artifact_id)
        if existing is not None:
            return MediaOperationResponse(
                media=MediaAsset(**existing),
                status="ready",
                message="媒体 artifact 已登记",
                evidence={"idempotent": True},
            )
        now = utc_now_iso()
        data = {
            "media_id": new_id("med"),
            "organization_id": artifact.organization_id or "org_default",
            "task_id": artifact.task_id,
            "source_artifact_id": artifact.artifact_id,
            "media_type": media_type,
            "display_name": request.display_name or artifact.display_name,
            "uri": artifact.uri,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
            "checksum": artifact.checksum,
            "sensitivity": request.sensitivity or artifact.sensitivity,
            "status": "ready",
            "io_role": "input",
            "source_kind": "task_artifact",
            "privacy_level": _privacy_level_for_sensitivity(
                request.sensitivity or artifact.sensitivity
            ),
            "provider_status": "local",
            "replay_summary": {
                "source": "task_artifact",
                "media_type": media_type,
                "display_name": artifact.display_name,
            },
            "metadata": _redacted_dict({
                **request.metadata,
                "source": "task_artifact",
                "source_artifact_checksum": artifact.checksum,
            }),
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_asset(data)
        await self._audit.write_event(
            actor_type="system",
            action="media.imported",
            object_type="media_asset",
            object_id=str(data["media_id"]),
            summary="媒体 artifact 已登记",
            risk_level=RiskLevel.R2,
            payload=redact({"media_id": data["media_id"], "artifact_id": artifact.artifact_id}),
            trace_id=trace_id,
        )
        return MediaOperationResponse(
            media=MediaAsset(**data),
            status="ready",
            message="媒体 artifact 已登记",
            evidence={"source_artifact_id": artifact.artifact_id},
        )

    async def replay_task_media(self, task_id: str) -> list[dict[str, Any]]:
        assets = await self._repo.list_assets_by_task(task_id)
        derivatives = await self._repo.list_derivatives_by_task(task_id)
        analyses = await self._repo.list_analysis_by_task(task_id)
        edit_plans = await self._repo.list_edit_plans_by_task(task_id)
        io_records = await self._repo.list_io_records_by_task(task_id)
        transcripts = await self._repo.list_speech_transcripts_by_task(task_id)
        renders = await self._repo.list_speech_renders_by_task(task_id)
        summaries = await self._repo.list_multimodal_summaries_by_task(task_id)
        bindings = await self._repo.list_chat_bindings_by_task(task_id)
        derivative_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in derivatives:
            derivative_by_media.setdefault(str(item["media_id"]), []).append(item)
        analysis_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in analyses:
            analysis_by_media.setdefault(str(item["media_id"]), []).append(item)
        edit_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in edit_plans:
            edit_by_media.setdefault(str(item["media_id"]), []).append(item)
        io_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in io_records:
            io_by_media.setdefault(str(item.get("media_id") or item.get("io_request_id")), []).append(item)
        transcript_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in transcripts:
            transcript_by_media.setdefault(str(item["media_id"]), []).append(item)
        render_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in renders:
            render_by_media.setdefault(str(item.get("media_id") or item.get("task_id")), []).append(item)
        summary_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in summaries:
            summary_by_media.setdefault(str(item["media_id"]), []).append(item)
        binding_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in bindings:
            binding_by_media.setdefault(str(item.get("media_id") or item.get("io_request_id")), []).append(item)
        return [
            _redacted_dict(
                {
                    "media": asset,
                    "derivatives": derivative_by_media.get(str(asset["media_id"]), []),
                    "analysis": analysis_by_media.get(str(asset["media_id"]), []),
                    "edit_plans": edit_by_media.get(str(asset["media_id"]), []),
                    "io_records": io_by_media.get(str(asset["media_id"]), []),
                    "transcripts": transcript_by_media.get(str(asset["media_id"]), []),
                    "renders": render_by_media.get(str(asset["media_id"]), []),
                    "summaries": summary_by_media.get(str(asset["media_id"]), []),
                    "chat_bindings": binding_by_media.get(str(asset["media_id"]), []),
                    "source_boundary": "task_artifact_only",
                    "raw_media_content_included": False,
                }
            )
            for asset in assets
        ]

    async def get_media(self, media_id: str) -> MediaAsset:
        row = await self._repo.get_asset(media_id)
        if row is None:
            raise AppError(ErrorCode.MEDIA_ASSET_NOT_FOUND, "媒体不存在", status_code=404)
        return MediaAsset(**row)

    async def list_derivatives(self, media_id: str) -> list[MediaDerivative]:
        await self.get_media(media_id)
        return [MediaDerivative(**row) for row in await self._repo.list_derivatives(media_id)]

    async def probe(
        self,
        media_id: str,
        request: MediaProbeRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        del request
        media = await self.get_media(media_id)
        span_id = await self._start_span(trace_id, "media.probe", media)
        try:
            source = await self._source_path(media)
            probe = await self._runtime.probe(source)
            now = utc_now_iso()
            updates = {
                "duration_ms": probe.get("duration_ms"),
                "width": probe.get("width"),
                "height": probe.get("height"),
                "frame_rate": probe.get("frame_rate"),
                "audio_streams": probe.get("audio_streams", 0),
                "video_streams": probe.get("video_streams", 0),
                "status": "ready",
                "metadata": {**media.metadata, "probe": _safe_probe_metadata(probe)},
                "trace_id": trace_id,
                "updated_at": now,
            }
            await self._repo.update_asset(media.media_id, updates)
            media = await self.get_media(media.media_id)
            analysis = await self._write_analysis(
                media,
                analysis_type="probe",
                segments=[],
                metadata={"probe": _safe_probe_metadata(probe), "backend": probe.get("backend")},
                trace_id=trace_id,
            )
            await self._end_span(span_id, {"status": "completed"})
            return MediaOperationResponse(
                media=media,
                analysis=analysis,
                status="completed",
                message="媒体元信息分析完成",
                evidence={"backend": probe.get("backend", "ffprobe")},
            )
        except AppError as exc:
            if exc.code == ErrorCode.MEDIA_BACKEND_UNAVAILABLE.value:
                await self._end_span(span_id, {"status": "degraded", "reason": exc.message})
                return MediaOperationResponse(
                    media=media,
                    status="degraded",
                    message="本地媒体后端不可用，无法执行 probe",
                    degraded_reason=str(exc.details.get("reason") if exc.details else exc.message),
                    evidence={"backend_status": self.runtime_status()},
                )
            await self._end_span(span_id, {"status": "failed"}, status=TraceSpanStatus.FAILED)
            raise

    async def extract_frames(
        self,
        media_id: str,
        request: MediaExtractFramesRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        media = await self.get_media(media_id)
        try:
            source = await self._source_path(media)
            outputs = await self._runtime.extract_frames(
                source,
                self._work_dir(media.task_id, "frames"),
                request,
            )
        except AppError as exc:
            if exc.code == ErrorCode.MEDIA_BACKEND_UNAVAILABLE.value:
                return _degraded_response(media, "抽帧后端不可用", exc, self.runtime_status())
            raise
        artifacts, derivatives = await self._write_runtime_outputs(
            media,
            outputs,
            derivative_type="frame",
            artifact_type="image",
            subdir="media/frames",
            trace_id=trace_id,
        )
        analysis = await self._write_analysis(
            media,
            analysis_type="frame_summary",
            segments=[
                {
                    "start_ms": item.time_ms or 0,
                    "end_ms": item.time_ms or 0,
                    "summary": "抽帧预览，可供后续视觉摘要或剪辑计划引用",
                    "confidence": 0.5,
                    "evidence_artifact_ids": [artifact.artifact_id],
                }
                for item, artifact in zip(outputs, artifacts, strict=False)
            ],
            evidence_artifact_ids=[item.artifact_id for item in artifacts],
            metadata={"mode": request.mode, "backend": self.runtime_status()["backend"]},
            trace_id=trace_id,
        )
        return MediaOperationResponse(
            media=media,
            derivatives=derivatives,
            analysis=analysis,
            artifacts=artifacts,
            status="completed",
            message="抽帧完成",
            evidence={"frame_count": len(artifacts)},
        )

    async def extract_audio(
        self,
        media_id: str,
        request: MediaExtractAudioRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        media = await self.get_media(media_id)
        try:
            source = await self._source_path(media)
            output = await self._runtime.extract_audio(
                source,
                self._work_dir(media.task_id, "audio"),
                request,
            )
        except AppError as exc:
            if exc.code == ErrorCode.MEDIA_BACKEND_UNAVAILABLE.value:
                return _degraded_response(media, "抽音频后端不可用", exc, self.runtime_status())
            raise
        artifacts, derivatives = await self._write_runtime_outputs(
            media,
            [output],
            derivative_type="audio",
            artifact_type="audio",
            subdir="media/audio",
            trace_id=trace_id,
        )
        return MediaOperationResponse(
            media=media,
            derivatives=derivatives,
            artifacts=artifacts,
            status="completed",
            message="音频提取完成",
            evidence={"audio_artifact_id": artifacts[0].artifact_id},
        )

    async def transcribe_audio(
        self,
        media_id: str,
        request: MediaTranscribeAudioRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        return await self.stt(
            media_id,
            MediaSTTRequest(
                provider=request.provider,
                language=request.language,
                force=request.force,
            ),
            trace_id=trace_id,
        )

    async def stt(
        self,
        media_id: str,
        request: MediaSTTRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        media = await self.get_media(media_id)
        span_id = await self._start_span(trace_id, "media.stt", media)
        provider = _normalized_provider(request.provider, default="local")
        provider_health = await self._record_provider_health(
            capability="stt",
            provider_name=provider,
            provider_type="local" if provider in {"local", "test"} else "external",
            status="available" if provider in {"local", "test"} else "degraded",
            degraded_reason=None
            if provider in {"local", "test"}
            else "provider_unavailable",
            trace_id=trace_id,
        )
        try:
            source = await self._source_path(media)
            source_preview = await self._read_transcript_source(media, source)
            idempotency_key = _media_io_idempotency(
                media.media_id,
                "stt",
                provider,
                request.language,
                media.checksum or "",
                source_preview,
            )
            existing = await self._repo.get_io_request_by_idempotency(idempotency_key)
            if existing is not None:
                transcripts = await self._repo.get_speech_transcripts_for_io_request(
                    existing["io_request_id"]
                )
                artifacts = [
                    await self._artifact(row["artifact_id"])  # type: ignore[arg-type]
                    for row in transcripts
                    if row.get("artifact_id")
                ]
                analysis = await self._latest_transcript_analysis(media.media_id)
                await self._end_span(span_id, {"status": "completed", "idempotent": True})
                return MediaOperationResponse(
                    media=media,
                    analysis=analysis,
                    artifacts=artifacts,
                    io_records=[MediaIORecord(**existing)],
                    transcripts=[MediaSpeechTranscript(**row) for row in transcripts],
                    provider_health=[provider_health],
                    status=existing["status"],
                    message="语音转写记录已存在",
                    degraded_reason=existing.get("degraded_reason"),
                    evidence={
                        "io_request_id": existing["io_request_id"],
                        "idempotent": True,
                    },
                )
            if provider not in {"local", "test"}:
                result = await self._record_stt_io(
                    media=media,
                    provider_name=provider,
                    provider_health=provider_health,
                    request=request,
                    trace_id=trace_id,
                    transcript_text=None,
                    source_preview=source_preview,
                    status="degraded",
                    degraded_reason="provider_unavailable",
                )
                await self._end_span(span_id, {"status": "degraded"})
                return result
            transcript_text = await self._transcript_from_media_source(media, source)
            status = "completed" if provider == "test" and transcript_text else "degraded"
            result = await self._record_stt_io(
                media=media,
                provider_name=provider,
                provider_health=provider_health,
                request=request,
                trace_id=trace_id,
                transcript_text=transcript_text,
                source_preview=source_preview,
                status=status,
                degraded_reason=None if status == "completed" else "transcription_provider_unavailable",
            )
            await self._end_span(
                span_id,
                {"status": result.status, "io_request_id": result.evidence.get("io_request_id")},
            )
            return result
        except AppError as exc:
            if exc.code == ErrorCode.MEDIA_BACKEND_UNAVAILABLE.value:
                await self._end_span(span_id, {"status": "degraded"}, status=TraceSpanStatus.FAILED)
                return _degraded_response(media, "转写后端不可用", exc, self.runtime_status())
            await self._end_span(span_id, {"status": "failed"}, status=TraceSpanStatus.FAILED)
            raise

    async def tts(
        self,
        request: MediaTTSRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        provider = _normalized_provider(request.provider, default="local")
        source_text = str(redact(request.text))
        idempotency_key = _media_io_idempotency(
            request.task_id,
            "tts",
            provider,
            request.voice or "",
            request.output_format,
            source_text,
        )
        existing_io = await self._repo.get_io_request_by_idempotency(idempotency_key)
        provider_health = await self._record_provider_health(
            capability="tts",
            provider_name=provider,
            provider_type="local" if provider in {"local", "test"} else "external",
            status="available" if provider in {"local", "test"} else "degraded",
            degraded_reason=None
            if provider in {"local", "test"}
            else "provider_unavailable",
            trace_id=trace_id,
        )
        transcript_preview = _preview_text(source_text, 240)
        if existing_io is not None:
            output_artifact_id = existing_io.get("output_artifact_id")
            media = await self._repo.get_asset_by_source(output_artifact_id) if output_artifact_id else None
            artifact = await self._artifact(output_artifact_id) if output_artifact_id else None
            renders = await self._repo.get_speech_renders_for_io_request(existing_io["io_request_id"])
            render_records = [MediaSpeechRender(**row) for row in renders]
            media_asset = MediaAsset(**media) if media else None
            return MediaOperationResponse(
                media=media_asset,
                artifacts=[artifact] if artifact is not None else [],
                io_records=[MediaIORecord(**existing_io)],
                provider_health=[provider_health],
                renders=render_records,
                status=existing_io["status"],
                message="语音播报记录已存在",
                degraded_reason=existing_io.get("degraded_reason"),
                evidence={
                    "io_request_id": existing_io["io_request_id"],
                    "idempotent": True,
                },
            )
        io_request = await self._create_io_request(
            task_id=request.task_id,
            media_id=None,
            operation="tts",
            direction="output",
            provider_name=provider,
            status="degraded" if provider not in {"local", "test"} else "completed",
            degraded_reason=None if provider in {"local", "test"} else "provider_unavailable",
            trace_id=trace_id,
            summary={
                "text_preview": transcript_preview,
                "voice": request.voice,
                "output_format": request.output_format,
            },
            evidence={"provider_health": [provider_health.model_dump(mode="json")]},
            redaction_summary={
                "text_preview": transcript_preview,
                "source_text_redacted": True,
            },
            idempotency_key=idempotency_key,
        )
        if provider not in {"local", "test"}:
            return MediaOperationResponse(
                status="degraded",
                message="TTS provider 未启用",
                degraded_reason="provider_unavailable",
                io_records=[io_request],
                provider_health=[provider_health],
                evidence={"io_request_id": io_request.io_request_id},
            )
        content = _fake_wav_bytes(request.text, request.output_format)
        artifact = await self._artifacts.write_bytes(
            task_id=request.task_id,
            organization_id=request.organization_id or "org_default",
            display_name="speech.wav" if request.output_format == "wav" else f"speech.{request.output_format}",
            content=content,
            artifact_type="audio",
            content_type="audio/wav" if request.output_format == "wav" else f"audio/{request.output_format}",
            subdir="media/tts",
            sensitivity=request.sensitivity,
            metadata={
                "media_io_request_id": io_request.io_request_id,
                "provider": provider,
                "voice": request.voice,
                "output_format": request.output_format,
                "text_preview": transcript_preview,
                "redacted": True,
            },
            trace_id=trace_id,
        )
        media_import = await self.import_artifact(
            MediaImportArtifactRequest(
                task_id=request.task_id,
                artifact_id=artifact.artifact_id,
                media_type="audio",
                display_name=artifact.display_name,
                sensitivity=request.sensitivity,
                metadata={
                    "media_io_request_id": io_request.io_request_id,
                    "provider": provider,
                    "voice": request.voice,
                    "tts": True,
                },
            ),
            trace_id=trace_id,
        )
        media = media_import.media
        if media is None:
            raise AppError(ErrorCode.MEDIA_RUNTIME_FAILED, "TTS 媒体登记失败", status_code=500)
        await self._repo.update_asset(
            media.media_id,
            {
                "io_role": "output",
                "source_kind": "tts_render",
                "provider_status": provider,
                "replay_summary": {
                    "io_request_id": io_request.io_request_id,
                    "voice": request.voice,
                    "output_format": request.output_format,
                    "text_preview": transcript_preview,
                },
                "updated_at": utc_now_iso(),
            },
        )
        media = await self.get_media(media.media_id)
        render = await self._record_speech_render(
            media=media,
            io_request=io_request,
            provider_name=provider,
            artifact=artifact,
            voice=request.voice,
            output_format=request.output_format,
            source_text=request.text,
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="media.tts",
            object_type="media_asset",
            object_id=media.media_id,
            summary="媒体语音播报记录已生成",
            risk_level=RiskLevel.R2,
            payload=redact(
                {
                    "media_id": media.media_id,
                    "io_request_id": io_request.io_request_id,
                    "artifact_id": artifact.artifact_id,
                    "render_id": render.render_id,
                }
            ),
            trace_id=trace_id,
        )
        await self._repo.update_io_request(
            io_request.io_request_id,
            {
                "output_artifact_id": artifact.artifact_id,
                "status": "completed",
                "updated_at": utc_now_iso(),
                "summary": {
                    **io_request.summary,
                    "media_id": media.media_id,
                },
            },
        )
        updated_io = await self._repo.get_io_request_by_idempotency(io_request.idempotency_key or "")
        if updated_io is None:
            updated_io = io_request.model_dump(mode="json")
        span_id = await self._start_span(trace_id, "media.tts", media)
        await self._end_span(
            span_id,
            {"status": "completed", "io_request_id": io_request.io_request_id},
        )
        return MediaOperationResponse(
            media=media,
            artifacts=[artifact],
            io_records=[MediaIORecord(**updated_io)],
            provider_health=[provider_health],
            renders=[render],
            status="completed",
            message="语音播报已生成",
            evidence={
                "io_request_id": io_request.io_request_id,
                "artifact_id": artifact.artifact_id,
                "render_id": render.render_id,
            },
        )

    async def summarize(
        self,
        media_id: str,
        request: MediaSummarizeRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        media = await self.get_media(media_id)
        span_id = await self._start_span(trace_id, "media.summarize", media)
        provider = _normalized_provider(request.provider, default="local")
        idempotency_key = _media_io_idempotency(
            media.media_id,
            "summarize",
            provider,
            request.summary_type or media.media_type,
            media.checksum or "",
            media.source_artifact_id,
        )
        existing_io = await self._repo.get_io_request_by_idempotency(idempotency_key)
        provider_health = await self._record_provider_health(
            capability="summarize",
            provider_name=provider,
            provider_type="local" if provider in {"local", "test"} else "external",
            status="available" if provider in {"local", "test"} else "degraded",
            degraded_reason=None
            if provider in {"local", "test"}
            else "provider_unavailable",
            trace_id=trace_id,
        )
        if existing_io is not None:
            summary_rows = await self._repo.get_multimodal_summaries_for_io_request(
                existing_io["io_request_id"]
            )
            artifacts: list[TaskArtifact] = []
            output_artifact_id = existing_io.get("output_artifact_id")
            if output_artifact_id:
                artifacts.append(await self._artifact(output_artifact_id))
            await self._end_span(
                span_id,
                {"status": existing_io["status"], "io_request_id": existing_io["io_request_id"]},
            )
            return MediaOperationResponse(
                media=media,
                artifacts=artifacts,
                io_records=[MediaIORecord(**existing_io)],
                provider_health=[provider_health],
                summaries=[MediaMultimodalSummary(**row) for row in summary_rows],
                status=existing_io["status"],
                message="媒体摘要记录已存在",
                degraded_reason=existing_io.get("degraded_reason"),
                evidence={
                    "io_request_id": existing_io["io_request_id"],
                    "idempotent": True,
                },
            )
        try:
            summary_text, evidence_artifacts, status, degraded_reason = await self._summarize_media(
                media,
                provider=provider,
                summary_type=request.summary_type,
                trace_id=trace_id,
            )
            io_request = await self._create_io_request(
                task_id=media.task_id,
                media_id=media.media_id,
                operation="summarize",
                direction="input",
                provider_name=provider,
                status=status,
                degraded_reason=degraded_reason,
                trace_id=trace_id,
                summary={
                    "summary_type": request.summary_type or media.media_type,
                    "summary_preview": _preview_text(summary_text, 240),
                },
                evidence={
                    "provider_health": [provider_health.model_dump(mode="json")],
                    "evidence_artifact_ids": [item.artifact_id for item in evidence_artifacts],
                },
                redaction_summary={"summary_preview": _preview_text(summary_text, 240)},
                idempotency_key=idempotency_key,
            )
            summary_artifact = await self._artifacts.write_text(
                task_id=media.task_id,
                organization_id=media.organization_id,
                display_name=f"{media.media_id}-summary.txt",
                content=summary_text,
                artifact_type="summary",
                subdir="media/summaries",
                sensitivity=media.sensitivity,
                metadata={
                    "media_id": media.media_id,
                    "io_request_id": io_request.io_request_id,
                    "summary_type": request.summary_type or media.media_type,
                    "redacted": True,
                },
                trace_id=trace_id,
            )
            summary_record = await self._record_multimodal_summary(
                media=media,
                io_request=io_request,
                provider_name=provider,
                summary_type=request.summary_type or media.media_type,
                summary_text=summary_text,
                evidence_artifacts=evidence_artifacts,
                status=status,
                trace_id=trace_id,
            )
            await self._audit.write_event(
                actor_type="system",
                action="media.summarize",
                object_type="media_asset",
                object_id=media.media_id,
                summary="媒体摘要记录已生成",
                risk_level=RiskLevel.R2,
                payload=redact(
                    {
                        "media_id": media.media_id,
                        "io_request_id": io_request.io_request_id,
                        "summary_id": summary_record.summary_id,
                    }
                ),
                trace_id=trace_id,
            )
            await self._repo.update_io_request(
                io_request.io_request_id,
                {
                    "output_artifact_id": summary_artifact.artifact_id,
                    "status": status,
                    "degraded_reason": degraded_reason,
                    "summary": {
                        **io_request.summary,
                        "summary_artifact_id": summary_artifact.artifact_id,
                        "summary_id": summary_record.summary_id,
                    },
                    "updated_at": utc_now_iso(),
                },
            )
            await self._end_span(
                span_id,
                {"status": status, "io_request_id": io_request.io_request_id},
                status=TraceSpanStatus.COMPLETED if status == "completed" else TraceSpanStatus.COMPLETED,
            )
            updated_io = await self._repo.get_io_request_by_idempotency(io_request.idempotency_key or "")
            io_record = MediaIORecord(**(updated_io or io_request.model_dump(mode="json")))
            return MediaOperationResponse(
                media=media,
                artifacts=[summary_artifact],
                io_records=[io_record],
                provider_health=[provider_health],
                summaries=[summary_record],
                status=status,
                message="媒体摘要已生成" if status == "completed" else "媒体摘要已降级生成",
                degraded_reason=degraded_reason,
                evidence={
                    "io_request_id": io_request.io_request_id,
                    "summary_id": summary_record.summary_id,
                    "summary_artifact_id": summary_artifact.artifact_id,
                },
            )
        except AppError as exc:
            if exc.code == ErrorCode.MEDIA_BACKEND_UNAVAILABLE.value:
                await self._end_span(span_id, {"status": "degraded"}, status=TraceSpanStatus.FAILED)
                return _degraded_response(media, "摘要后端不可用", exc, self.runtime_status())
            await self._end_span(span_id, {"status": "failed"}, status=TraceSpanStatus.FAILED)
            raise

    async def list_io_records(self, media_id: str) -> MediaIORecordResponse:
        await self.get_media(media_id)
        return MediaIORecordResponse(
            items=[MediaIORecord(**row) for row in await self._repo.list_io_records(media_id)]
        )

    async def provider_health(self) -> MediaProviderHealthResponse:
        records = await self._repo.list_provider_health()
        if not records:
            for capability, provider_name in [
                ("stt", "local"),
                ("tts", "local"),
                ("summarize", "local"),
            ]:
                await self._record_provider_health(
                    capability=capability,
                    provider_name=provider_name,
                    provider_type="local",
                    status="available",
                    degraded_reason=None,
                    trace_id=None,
                )
            records = await self._repo.list_provider_health()
        return MediaProviderHealthResponse(
            items=[MediaProviderHealthRecord(**row) for row in records]
        )

    async def scene_detect(
        self,
        media_id: str,
        request: MediaSceneDetectRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        media = await self.get_media(media_id)
        duration = media.duration_ms or 42000
        segment_count = min(request.max_segments, max(1, min(3, duration // 10000 + 1)))
        step = max(1000, duration // segment_count)
        segments: list[dict[str, Any]] = [
            {
                "start_ms": index * step,
                "end_ms": min(duration, (index + 1) * step),
                "summary": f"场景 {index + 1}",
                "confidence": round(0.55 + index * 0.05, 2),
                "evidence_artifact_ids": [],
            }
            for index in range(segment_count)
        ]
        analysis = await self._write_analysis(
            media,
            analysis_type="scene",
            segments=segments,
            metadata={"threshold": request.threshold, "method": "deterministic_local"},
            trace_id=trace_id,
        )
        return MediaOperationResponse(
            media=media,
            analysis=analysis,
            status="completed",
            message="场景切分完成",
            evidence={"segment_count": len(segments)},
        )

    async def timeline(
        self,
        media_id: str,
        request: MediaTimelineRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        media = await self.get_media(media_id)
        scene = await self._repo.get_latest_analysis(media_id, "scene")
        frame_summary = await self._repo.get_latest_analysis(media_id, "frame_summary")
        segments = (scene or {}).get("segments") or [
            {
                "start_ms": 0,
                "end_ms": media.duration_ms or 0,
                "summary": "基于媒体元信息生成的基础时间线",
                "confidence": 0.45,
                "evidence_artifact_ids": [],
            }
        ]
        evidence_ids: list[str] = []
        if request.include_frames and frame_summary:
            evidence_ids.extend(frame_summary.get("evidence_artifact_ids", []))
        analysis = await self._write_analysis(
            media,
            analysis_type="timeline",
            segments=segments,
            evidence_artifact_ids=evidence_ids,
            metadata={
                "source": "probe_scene_frame_transcript_summary",
                "include_transcript": request.include_transcript,
                "include_frames": request.include_frames,
            },
            trace_id=trace_id,
        )
        return MediaOperationResponse(
            media=media,
            analysis=analysis,
            status="completed",
            message="时间线摘要已生成",
            evidence={"segment_count": len(segments), "evidence_artifact_ids": evidence_ids},
        )

    async def create_edit_plan(
        self,
        media_id: str,
        request: MediaEditPlanCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaEditPlanResponse:
        media = await self.get_media(media_id)
        operations = request.operations or _default_edit_operations(media)
        _validate_operations(media, operations)
        now = utc_now_iso()
        data = {
            "edit_plan_id": new_id("edl"),
            "media_id": media.media_id,
            "organization_id": media.organization_id,
            "task_id": media.task_id,
            "goal": str(redact(request.goal)),
            "output_profile": request.output_profile or {"container": "mp4"},
            "operations": operations,
            "status": "planned",
            "risk_level": "R3",
            "requires_approval": True,
            "evidence": {
                "source_checksum": media.checksum,
                "operation_count": len(operations),
                "render_requested": request.render,
            },
            "metadata": {"phase": "phase43"},
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_edit_plan(data)
        return MediaEditPlanResponse(
            edit_plan=MediaEditPlan(**data),
            media=media,
            message="剪辑计划已生成；尚未渲染或修改媒体文件",
            next_step="如需生成视频，请确认后执行 media.render_edit",
        )

    async def get_edit_plan(self, edit_plan_id: str) -> MediaEditPlan:
        row = await self._repo.get_edit_plan(edit_plan_id)
        if row is None:
            raise AppError(ErrorCode.MEDIA_PLAN_INVALID, "剪辑计划不存在", status_code=404)
        return MediaEditPlan(**row)

    async def render_edit(
        self,
        edit_plan_id: str,
        request: MediaRenderEditRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> MediaEditPlanResponse:
        del request
        edit_plan = await self.get_edit_plan(edit_plan_id)
        media = await self.get_media(edit_plan.media_id)
        _validate_operations(media, edit_plan.operations)
        try:
            source = await self._source_path(media)
            output = await self._runtime.render_edit(
                source,
                self._work_dir(media.task_id, "render"),
                edit_plan,
            )
        except AppError as exc:
            if exc.code == ErrorCode.MEDIA_BACKEND_UNAVAILABLE.value:
                await self._repo.update_edit_plan(
                    edit_plan.edit_plan_id,
                    {
                        "status": "degraded",
                        "evidence": {
                            **edit_plan.evidence,
                            "backend_status": self.runtime_status(),
                            "degraded_reason": (
                                exc.details.get("reason") if exc.details else exc.message
                            ),
                        },
                        "updated_at": utc_now_iso(),
                    },
                )
                return MediaEditPlanResponse(
                    edit_plan=await self.get_edit_plan(edit_plan.edit_plan_id),
                    media=media,
                    message="本地媒体后端不可用，无法渲染剪辑计划",
                    next_step="安装 ffmpeg/ffprobe 或配置媒体 provider 后重试",
                )
            raise
        artifact = await self._artifact_from_runtime_output(
            media,
            output,
            artifact_type="video",
            subdir="media/rendered",
            trace_id=trace_id,
        )
        rendered = await self.import_artifact(
            MediaImportArtifactRequest(
                task_id=media.task_id,
                artifact_id=artifact.artifact_id,
                media_type="video",
                display_name=artifact.display_name,
                sensitivity=media.sensitivity,
                metadata={"derived_from_media_id": media.media_id, "edit_plan_id": edit_plan_id},
            ),
            trace_id=trace_id,
        )
        rendered_media = rendered.media
        if rendered_media is None:
            raise AppError(ErrorCode.MEDIA_RUNTIME_FAILED, "渲染媒体登记失败", status_code=500)
        await self._write_derivative(
            media,
            artifact,
            derivative_type="rendered_video",
            metadata={"edit_plan_id": edit_plan_id, "rendered_media_id": rendered_media.media_id},
            trace_id=trace_id,
        )
        await self._repo.update_edit_plan(
            edit_plan.edit_plan_id,
            {
                "status": "rendered",
                "artifact_id": artifact.artifact_id,
                "rendered_media_id": rendered_media.media_id,
                "evidence": {
                    **edit_plan.evidence,
                    "output_artifact_id": artifact.artifact_id,
                    "renderer": output.metadata.get("renderer"),
                },
                "updated_at": utc_now_iso(),
            },
        )
        return MediaEditPlanResponse(
            edit_plan=await self.get_edit_plan(edit_plan.edit_plan_id),
            media=media,
            artifact=artifact,
            message="剪辑计划已渲染为新视频 artifact",
            next_step="可在任务工件中查看或继续导出",
        )

    async def export_artifact(
        self,
        media_id: str,
        request: MediaExportArtifactRequest,
        *,
        trace_id: str | None = None,
    ) -> MediaOperationResponse:
        media = await self.get_media(media_id)
        return MediaOperationResponse(
            media=media,
            status="prepared",
            message="媒体导出准备完成；本阶段不执行外部上传",
            evidence={
                "export_mode": request.export_mode,
                "destination": str(redact(request.destination or "")) or None,
                "external_upload": False,
                "trace_id": trace_id,
            },
        )

    async def _artifact(self, artifact_id: str) -> TaskArtifact:
        row = await self._task_repo.get_artifact(artifact_id)
        if row is None:
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "工件不存在", status_code=404)
        return TaskArtifact(**row)

    async def _source_path(self, media: MediaAsset) -> Path:
        artifact = await self._artifact(media.source_artifact_id)
        path = self._artifacts.path_for_artifact(artifact)
        if not path.exists() or not path.is_file():
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "媒体源文件不存在", status_code=404)
        if media.checksum:
            checksum = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if checksum != media.checksum:
                raise AppError(
                    ErrorCode.MEDIA_PLAN_INVALID,
                    "媒体源 artifact checksum 已变化，请重新导入并确认",
                    status_code=409,
                    details={
                        "media_id": media.media_id,
                        "source_artifact_id": media.source_artifact_id,
                        "expected_checksum": media.checksum,
                        "actual_checksum": checksum,
                    },
                )
        return path

    def _work_dir(self, task_id: str, subdir: str) -> Path:
        return self._artifacts.task_dir(task_id) / "_media_runtime" / subdir

    async def _write_runtime_outputs(
        self,
        media: MediaAsset,
        outputs: list[RuntimeFileOutput],
        *,
        derivative_type: str,
        artifact_type: str,
        subdir: str,
        trace_id: str | None,
    ) -> tuple[list[TaskArtifact], list[MediaDerivative]]:
        artifacts: list[TaskArtifact] = []
        derivatives: list[MediaDerivative] = []
        for output in outputs:
            artifact = await self._artifact_from_runtime_output(
                media,
                output,
                artifact_type=artifact_type,
                subdir=subdir,
                trace_id=trace_id,
            )
            derivative = await self._write_derivative(
                media,
                artifact,
                derivative_type=derivative_type,
                time_ms=output.time_ms,
                metadata=output.metadata,
                trace_id=trace_id,
            )
            artifacts.append(artifact)
            derivatives.append(derivative)
        return artifacts, derivatives

    async def _artifact_from_runtime_output(
        self,
        media: MediaAsset,
        output: RuntimeFileOutput,
        *,
        artifact_type: str,
        subdir: str,
        trace_id: str | None,
    ) -> TaskArtifact:
        return await self._artifacts.write_bytes(
            task_id=media.task_id,
            organization_id=media.organization_id,
            display_name=output.display_name,
            content=output.path.read_bytes(),
            artifact_type=artifact_type,
            content_type=output.content_type,
            subdir=subdir,
            sensitivity=media.sensitivity,
            metadata={
                **output.metadata,
                "media_id": media.media_id,
                "source_artifact_id": media.source_artifact_id,
                "derived": True,
            },
            trace_id=trace_id,
        )

    async def _write_derivative(
        self,
        media: MediaAsset,
        artifact: TaskArtifact,
        *,
        derivative_type: str,
        time_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> MediaDerivative:
        data = {
            "derivative_id": new_id("medd"),
            "media_id": media.media_id,
            "organization_id": media.organization_id,
            "task_id": media.task_id,
            "artifact_id": artifact.artifact_id,
            "derivative_type": derivative_type,
            "time_ms": time_ms,
            "metadata": metadata or {},
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_derivative(data)
        return MediaDerivative(**data)

    async def _write_analysis(
        self,
        media: MediaAsset,
        *,
        analysis_type: str,
        segments: list[dict[str, Any]],
        transcript_artifact_id: str | None = None,
        evidence_artifact_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> MediaAnalysis:
        now = utc_now_iso()
        data = {
            "analysis_id": new_id("mana"),
            "media_id": media.media_id,
            "organization_id": media.organization_id,
            "task_id": media.task_id,
            "analysis_type": analysis_type,
            "status": "completed",
            "model_route": None,
            "segments": redact(segments),
            "transcript_artifact_id": transcript_artifact_id,
            "evidence_artifact_ids": evidence_artifact_ids or [],
            "metadata": redact(metadata or {}),
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_analysis(data)
        return MediaAnalysis(**data)

    async def _start_span(
        self,
        trace_id: str | None,
        name: str,
        media: MediaAsset,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.TOOL_CALL,
            name=name,
            metadata={"media_id": media.media_id, "task_id": media.task_id},
        )

    async def _end_span(
        self,
        span_id: str | None,
        output_data: dict[str, Any],
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(span_id, status=status, output_data=redact(output_data))

    async def _record_provider_health(
        self,
        *,
        capability: str,
        provider_name: str,
        provider_type: str,
        status: str,
        degraded_reason: str | None,
        trace_id: str | None,
    ) -> MediaProviderHealthRecord:
        now = utc_now_iso()
        data = {
            "health_record_id": new_id("mph"),
            "organization_id": "org_default",
            "provider_name": provider_name,
            "capability": capability,
            "provider_type": provider_type,
            "status": status,
            "degraded_reason": degraded_reason,
            "evidence": _redacted_dict(
                {
                    "runtime": self.runtime_status(),
                    "cloud_provider_enabled": False,
                    "provider_secret_visible": False,
                }
            ),
            "redaction_summary": {
                "credentials_included": False,
                "local_path_included": False,
            },
            "trace_id": trace_id,
            "checked_at": now,
            "created_at": now,
        }
        await self._repo.insert_provider_health(data)
        return MediaProviderHealthRecord(**data)

    async def _create_io_request(
        self,
        *,
        task_id: str | None,
        media_id: str | None,
        operation: str,
        direction: str,
        provider_name: str,
        status: str,
        degraded_reason: str | None,
        trace_id: str | None,
        summary: dict[str, Any],
        evidence: dict[str, Any],
        redaction_summary: dict[str, Any],
        input_artifact_id: str | None = None,
        output_artifact_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> MediaIORecord:
        now = utc_now_iso()
        idem = idempotency_key or _media_io_idempotency(
            media_id or task_id or "global",
            operation,
            provider_name,
            direction,
            json.dumps(redact(summary), sort_keys=True, ensure_ascii=False),
        )
        existing = await self._repo.get_io_request_by_idempotency(idem)
        if existing is not None:
            return MediaIORecord(**existing)
        data = {
            "io_request_id": new_id("mio"),
            "organization_id": "org_default",
            "task_id": task_id,
            "media_id": media_id,
            "operation": operation,
            "direction": direction,
            "provider_name": provider_name,
            "status": status,
            "degraded_reason": degraded_reason,
            "input_artifact_id": input_artifact_id,
            "output_artifact_id": output_artifact_id,
            "summary": _redacted_dict(summary),
            "evidence": _redacted_dict(evidence),
            "redaction_summary": _redacted_dict(redaction_summary),
            "idempotency_key": idem,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_io_request(data)
        return MediaIORecord(**data)

    async def _record_stt_io(
        self,
        *,
        media: MediaAsset,
        provider_name: str,
        provider_health: MediaProviderHealthRecord,
        request: MediaSTTRequest,
        trace_id: str | None,
        transcript_text: str | None,
        source_preview: str,
        status: str,
        degraded_reason: str | None,
    ) -> MediaOperationResponse:
        preview = _preview_text(transcript_text or source_preview or _stt_degraded_text(), 240)
        content = transcript_text or _stt_degraded_text()
        artifact = await self._artifacts.write_text(
            task_id=media.task_id,
            organization_id=media.organization_id,
            display_name="transcript.txt",
            content=content,
            artifact_type="transcript",
            subdir="media/transcripts",
            sensitivity=media.sensitivity,
            metadata={
                "media_id": media.media_id,
                "provider": provider_name,
                "status": status,
                "transcript_preview": preview,
                "raw_transcript_visible": False,
            },
            trace_id=trace_id,
        )
        derivative = await self._write_derivative(
            media,
            artifact,
            derivative_type="transcript",
            metadata={"provider": provider_name, "status": status},
            trace_id=trace_id,
        )
        analysis = await self._write_analysis(
            media,
            analysis_type="transcript",
            segments=[],
            transcript_artifact_id=artifact.artifact_id,
            evidence_artifact_ids=[artifact.artifact_id],
            metadata={
                "provider": provider_name,
                "status": status,
                "degraded_reason": degraded_reason,
                "transcript_preview": preview,
            },
            trace_id=trace_id,
        )
        io_request = await self._create_io_request(
            task_id=media.task_id,
            media_id=media.media_id,
            operation="stt",
            direction="input",
            provider_name=provider_name,
            status=status,
            degraded_reason=degraded_reason,
            input_artifact_id=media.source_artifact_id,
            output_artifact_id=artifact.artifact_id,
            trace_id=trace_id,
            summary={
                "language": request.language,
                "transcript_preview": preview,
                "transcript_artifact_id": artifact.artifact_id,
            },
            evidence={
                "analysis_id": analysis.analysis_id,
                "derivative_id": derivative.derivative_id,
                "provider_health": [provider_health.model_dump(mode="json")],
            },
            redaction_summary={
                "transcript_preview": preview,
                "raw_transcript_included": False,
            },
            idempotency_key=_media_io_idempotency(
                media.media_id,
                "stt",
                provider_name,
                request.language,
                media.checksum or "",
                source_preview,
            ),
        )
        transcript_data = {
            "transcript_id": new_id("mst"),
            "io_request_id": io_request.io_request_id,
            "organization_id": media.organization_id,
            "task_id": media.task_id,
            "media_id": media.media_id,
            "artifact_id": artifact.artifact_id,
            "provider_name": provider_name,
            "language": request.language,
            "status": status,
            "transcript_preview": preview,
            "summary_text": preview,
            "confidence": 0.7 if status == "completed" else 0,
            "evidence": {
                "raw_transcript_visible": False,
                "provider_status": provider_health.status,
            },
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_speech_transcript(transcript_data)
        transcript = MediaSpeechTranscript(**transcript_data)
        await self._audit.write_event(
            actor_type="system",
            action="media.stt",
            object_type="media_asset",
            object_id=media.media_id,
            summary="媒体语音转写记录已生成",
            risk_level=RiskLevel.R2,
            payload=redact(
                {
                    "media_id": media.media_id,
                    "io_request_id": io_request.io_request_id,
                    "status": status,
                    "degraded_reason": degraded_reason,
                }
            ),
            trace_id=trace_id,
        )
        return MediaOperationResponse(
            media=media,
            derivatives=[derivative],
            analysis=analysis,
            artifacts=[artifact],
            io_records=[io_request],
            provider_health=[provider_health],
            transcripts=[transcript],
            status=status,
            message="语音转写已生成" if status == "completed" else "转写 provider 未启用，已记录可恢复转写占位",
            degraded_reason=degraded_reason,
            evidence={
                "io_request_id": io_request.io_request_id,
                "transcript_id": transcript.transcript_id,
                "transcript_artifact_id": artifact.artifact_id,
                "transcript_preview": preview,
            },
        )

    async def _record_speech_render(
        self,
        *,
        media: MediaAsset,
        io_request: MediaIORecord,
        provider_name: str,
        artifact: TaskArtifact,
        voice: str | None,
        output_format: str,
        source_text: str,
        trace_id: str | None,
    ) -> MediaSpeechRender:
        data = {
            "render_id": new_id("msr"),
            "io_request_id": io_request.io_request_id,
            "organization_id": media.organization_id,
            "task_id": media.task_id,
            "media_id": media.media_id,
            "artifact_id": artifact.artifact_id,
            "provider_name": provider_name,
            "voice": voice,
            "output_format": output_format,
            "status": "completed",
            "source_text_hash": _hash_text(str(redact(source_text))),
            "duration_ms": _estimate_tts_duration_ms(source_text),
            "evidence": {
                "artifact_id": artifact.artifact_id,
                "raw_text_visible": False,
            },
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_speech_render(data)
        return MediaSpeechRender(**data)

    async def _record_multimodal_summary(
        self,
        *,
        media: MediaAsset,
        io_request: MediaIORecord,
        provider_name: str,
        summary_type: str,
        summary_text: str,
        evidence_artifacts: list[TaskArtifact],
        status: str,
        trace_id: str | None,
    ) -> MediaMultimodalSummary:
        data = {
            "summary_id": new_id("mms"),
            "io_request_id": io_request.io_request_id,
            "organization_id": media.organization_id,
            "task_id": media.task_id,
            "media_id": media.media_id,
            "provider_name": provider_name,
            "summary_type": summary_type,
            "status": status,
            "summary_text": _preview_text(summary_text, 1200),
            "summary": {
                "summary_preview": _preview_text(summary_text, 240),
                "media_type": media.media_type,
                "raw_media_content_included": False,
            },
            "evidence_artifact_ids": [item.artifact_id for item in evidence_artifacts],
            "evidence": {
                "source_artifact_id": media.source_artifact_id,
                "raw_media_content_included": False,
            },
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_multimodal_summary(data)
        return MediaMultimodalSummary(**data)

    async def _summarize_media(
        self,
        media: MediaAsset,
        *,
        provider: str,
        summary_type: str | None,
        trace_id: str | None,
    ) -> tuple[str, list[TaskArtifact], str, str | None]:
        del trace_id
        source_artifact = await self._artifact(media.source_artifact_id)
        evidence_artifacts = [source_artifact]
        if provider not in {"local", "test"}:
            return (
                f"{media.media_type} 媒体已登记，但外部摘要 provider 未启用。",
                evidence_artifacts,
                "degraded",
                "provider_unavailable",
            )
        kind = summary_type or media.media_type
        if kind == "image" or media.media_type == "image":
            dimensions = (
                f"{media.width}x{media.height}"
                if media.width and media.height
                else "尺寸未知"
            )
            return (
                "图片摘要：收到一张受控 artifact 图片，"
                f"格式 {media.content_type or source_artifact.content_type or 'image/*'}，"
                f"尺寸 {dimensions}，大小 {media.size_bytes or source_artifact.size_bytes or 0} 字节。"
                "当前只注入基础视觉线索，不注入原始图片内容。",
                evidence_artifacts,
                "completed",
                None,
            )
        if kind == "video" or media.media_type == "video":
            timeline = await self._repo.get_latest_analysis(media.media_id, "timeline")
            frame_summary = await self._repo.get_latest_analysis(media.media_id, "frame_summary")
            extra_ids = []
            if timeline:
                extra_ids.extend(timeline.get("evidence_artifact_ids", []))
            if frame_summary:
                extra_ids.extend(frame_summary.get("evidence_artifact_ids", []))
            for artifact_id in sorted(set(str(item) for item in extra_ids)):
                try:
                    evidence_artifacts.append(await self._artifact(artifact_id))
                except AppError:
                    continue
            return (
                "视频摘要：媒体已登记为受控 video artifact，"
                f"时长 {media.duration_ms or '未知'} ms，"
                f"分辨率 {media.width or '未知'}x{media.height or '未知'}，"
                "摘要基于 probe/frame/timeline 证据生成，不包含原始视频内容。",
                evidence_artifacts,
                "completed",
                None,
            )
        if kind == "document" or media.media_type == "document":
            preview = await self._document_preview(source_artifact)
            return (
                f"文档摘要：{preview or '文档已安全保存，但没有可读文本摘录。'}",
                evidence_artifacts,
                "completed" if preview else "degraded",
                None if preview else "document_text_unavailable",
            )
        if media.media_type == "audio":
            latest = await self._repo.get_latest_analysis(media.media_id, "transcript")
            preview = ""
            if latest:
                preview = str((latest.get("metadata") or {}).get("transcript_preview") or "")
            return (
                "音频摘要：收到一段受控音频 artifact。"
                f"{'转写线索：' + preview if preview else '当前仅可提供元信息线索。'}",
                evidence_artifacts,
                "completed" if preview else "degraded",
                None if preview else "transcript_unavailable",
            )
        return (
            f"{media.media_type} 媒体已保存，当前只能记录受控摘要占位。",
            evidence_artifacts,
            "degraded",
            "unsupported_media_summary_type",
        )

    async def _document_preview(self, artifact: TaskArtifact) -> str | None:
        try:
            _, preview = await self._artifacts.read_preview(artifact.artifact_id, limit=1200)
        except Exception:
            return None
        clean = " ".join(str(redact(preview)).split())
        return clean[:800] if clean else None

    async def _read_transcript_source(self, media: MediaAsset, source: Path) -> str:
        metadata_text = _transcript_from_metadata(media.metadata)
        if metadata_text:
            return metadata_text
        del source
        return _preview_text(
            f"{media.display_name} {media.content_type or ''} {media.duration_ms or ''}",
            240,
        )

    async def _transcript_from_media_source(
        self,
        media: MediaAsset,
        source: Path,
    ) -> str | None:
        metadata_text = _transcript_from_metadata(media.metadata)
        if metadata_text:
            return metadata_text
        if media.media_type != "audio":
            return None
        del source
        duration = f"，时长 {media.duration_ms} ms" if media.duration_ms else ""
        return f"音频转写线索：{media.display_name}{duration}。本地测试转写 provider 仅返回受控摘要，不包含原始音频内容。"

    async def _latest_transcript_analysis(self, media_id: str) -> MediaAnalysis | None:
        row = await self._repo.get_latest_analysis(media_id, "transcript")
        return MediaAnalysis(**row) if row else None

    async def record_chat_binding(
        self,
        *,
        media_id: str | None,
        io_request_id: str | None,
        channel: str | None,
        conversation_id: str | None,
        turn_id: str | None,
        message_id: str | None,
        channel_event_id: str | None,
        channel_attachment_id: str | None,
        binding_type: str,
        status: str = "bound",
        evidence: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> MediaChatBinding:
        data = {
            "binding_id": new_id("mcb"),
            "organization_id": "org_default",
            "media_id": media_id,
            "io_request_id": io_request_id,
            "channel": channel,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "message_id": message_id,
            "channel_event_id": channel_event_id,
            "channel_attachment_id": channel_attachment_id,
            "binding_type": binding_type,
            "status": status,
            "evidence": _redacted_dict(evidence or {}),
            "trace_id": trace_id,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_chat_binding(data)
        return MediaChatBinding(**data)


def _media_unavailable(reason: str) -> AppError:
    return AppError(
        ErrorCode.MEDIA_BACKEND_UNAVAILABLE,
        "本地媒体后端不可用",
        status_code=503,
        details={"reason": reason},
    )


def _degraded_response(
    media: MediaAsset,
    message: str,
    exc: AppError,
    backend_status: dict[str, Any],
) -> MediaOperationResponse:
    return MediaOperationResponse(
        media=media,
        status="degraded",
        message=message,
        degraded_reason=str(exc.details.get("reason") if exc.details else exc.message),
        evidence={"backend_status": backend_status},
    )


def _redacted_dict(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact(value)
    return redacted if isinstance(redacted, dict) else {}


def _infer_media_type(artifact: TaskArtifact) -> str:
    content_type = (artifact.content_type or "").lower()
    name = artifact.display_name.lower()
    if content_type.startswith("video/") or name.endswith((".mp4", ".mov", ".webm", ".mkv")):
        return "video"
    if content_type.startswith("audio/") or name.endswith((".wav", ".mp3", ".m4a", ".aac")):
        return "audio"
    if content_type.startswith("image/") or name.endswith((".png", ".jpg", ".jpeg", ".webp")):
        return "image"
    if content_type.startswith("text/") or content_type.startswith("application/pdf") or name.endswith(
        (".pdf", ".docx", ".doc", ".txt", ".md", ".csv")
    ):
        return "document"
    return "unknown"


def _normalized_provider(value: str, *, default: str = "local") -> str:
    provider = str(value or default).strip().lower()
    return provider or default


def _privacy_level_for_sensitivity(sensitivity: str | None) -> str:
    value = str(sensitivity or "low").strip().lower()
    if value in {"critical", "secret", "private", "high", "restricted"}:
        return "high"
    if value in {"medium", "internal", "confidential"}:
        return "medium"
    return "standard"


def _preview_text(value: str, limit: int) -> str:
    text = " ".join(str(redact(value)).split())
    return text[:limit]


def _hash_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _estimate_tts_duration_ms(text: str) -> int:
    length = max(1, len(str(redact(text)).strip()))
    return min(180000, max(500, length * 75))


def _stt_degraded_text() -> str:
    return "语音转写已记录，但当前 provider 未启用，因此只保存受控摘要。"


def _media_io_idempotency(*parts: object) -> str:
    payload = json.dumps([str(part) for part in parts], sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _transcript_from_metadata(metadata: dict[str, Any]) -> str | None:
    for key in ("transcript_text", "transcript", "recognized_text", "asr_text", "voice_text"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _preview_text(value, 240)
    return None


def _fake_wav_bytes(text: str, output_format: str) -> bytes:
    del text
    if output_format != "wav":
        return b"FAKE_AUDIO_" + output_format.encode("utf-8")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


def _probe_summary(raw: dict[str, Any], *, backend: str) -> dict[str, Any]:
    raw_streams = raw.get("streams")
    streams: list[dict[str, Any]] = raw_streams if isinstance(raw_streams, list) else []
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    first_video = video_streams[0] if video_streams else {}
    raw_format = raw.get("format")
    duration = (
        _float_or_none(raw_format.get("duration")) if isinstance(raw_format, dict) else None
    )
    if duration is None:
        duration = _float_or_none(first_video.get("duration"))
    frame_rate = _frame_rate(first_video.get("avg_frame_rate") or first_video.get("r_frame_rate"))
    return {
        "duration_ms": int(duration * 1000) if duration is not None else None,
        "width": _int_or_none(first_video.get("width")),
        "height": _int_or_none(first_video.get("height")),
        "frame_rate": frame_rate,
        "audio_streams": len(audio_streams),
        "video_streams": len(video_streams),
        "format": raw.get("format", {}),
        "backend": backend,
    }


def _safe_probe_metadata(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "duration_ms": probe.get("duration_ms"),
        "width": probe.get("width"),
        "height": probe.get("height"),
        "frame_rate": probe.get("frame_rate"),
        "audio_streams": probe.get("audio_streams"),
        "video_streams": probe.get("video_streams"),
        "backend": probe.get("backend"),
    }


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _frame_rate(value: Any) -> float | None:
    text = str(value or "")
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        den = _float_or_none(denominator)
        num = _float_or_none(numerator)
        if num is not None and den is not None and den != 0:
            return round(num / den, 4)
    return _float_or_none(text)


def _default_edit_operations(media: MediaAsset) -> list[dict[str, Any]]:
    duration = media.duration_ms or 30000
    return [
        {
            "type": "trim",
            "source_start_ms": 0,
            "source_end_ms": min(duration, 30000),
            "reason": "默认保留开头片段作为安全剪辑计划",
        }
    ]


def _first_trim_operation(operations: list[dict[str, Any]]) -> dict[str, Any] | None:
    for operation in operations:
        if operation.get("type") == "trim":
            return operation
    return None


def _validate_operations(media: MediaAsset, operations: list[dict[str, Any]]) -> None:
    if not operations:
        raise AppError(ErrorCode.MEDIA_PLAN_INVALID, "剪辑计划至少需要一个操作", status_code=422)
    duration = media.duration_ms
    for operation in operations:
        op_type = str(operation.get("type") or "")
        if op_type not in {
            "trim",
            "concat",
            "crop",
            "scale",
            "mute",
            "audio_keep",
            "subtitle",
            "transcode",
        }:
            raise AppError(ErrorCode.MEDIA_PLAN_INVALID, "不支持的剪辑操作", status_code=422)
        if op_type == "trim":
            start = _int_or_none(operation.get("source_start_ms"))
            end = _int_or_none(operation.get("source_end_ms"))
            if start is None or end is None or start < 0 or end <= start:
                raise AppError(ErrorCode.MEDIA_PLAN_INVALID, "剪辑时间范围不合法", status_code=422)
            if duration is not None and end > duration:
                raise AppError(
                    ErrorCode.MEDIA_PLAN_INVALID,
                    "剪辑时间超出媒体时长",
                    status_code=422,
                )


_FAKE_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00"
    b"\x00\x04\x00\x01\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)
