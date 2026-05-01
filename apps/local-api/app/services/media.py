from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core_types import (
    ErrorCode,
    MediaAnalysis,
    MediaAsset,
    MediaDerivative,
    MediaEditPlan,
    RiskLevel,
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
    MediaImportArtifactRequest,
    MediaOperationResponse,
    MediaProbeRequest,
    MediaRenderEditRequest,
    MediaSceneDetectRequest,
    MediaTimelineRequest,
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
        if media_type not in {"video", "audio", "image"}:
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
        derivative_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in derivatives:
            derivative_by_media.setdefault(str(item["media_id"]), []).append(item)
        analysis_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in analyses:
            analysis_by_media.setdefault(str(item["media_id"]), []).append(item)
        edit_by_media: dict[str, list[dict[str, Any]]] = {}
        for item in edit_plans:
            edit_by_media.setdefault(str(item["media_id"]), []).append(item)
        return [
            _redacted_dict(
                {
                    "media": asset,
                    "derivatives": derivative_by_media.get(str(asset["media_id"]), []),
                    "analysis": analysis_by_media.get(str(asset["media_id"]), []),
                    "edit_plans": edit_by_media.get(str(asset["media_id"]), []),
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
        media = await self.get_media(media_id)
        transcript = (
            "本地转写模型未配置；当前仅记录转写契约和可恢复 degraded 状态。"
            if request.provider == "local"
            else "外部转写 provider 未启用；需要资产授权和审批后才能调用。"
        )
        artifact = await self._artifacts.write_text(
            task_id=media.task_id,
            organization_id=media.organization_id,
            display_name="transcript.txt",
            content=transcript,
            artifact_type="transcript",
            subdir="media/transcripts",
            sensitivity=media.sensitivity,
            metadata={
                "media_id": media.media_id,
                "provider": request.provider,
                "status": "degraded",
            },
            trace_id=trace_id,
        )
        derivative = await self._write_derivative(
            media,
            artifact,
            derivative_type="transcript",
            metadata={"provider": request.provider, "degraded": True},
            trace_id=trace_id,
        )
        analysis = await self._write_analysis(
            media,
            analysis_type="transcript",
            segments=[],
            transcript_artifact_id=artifact.artifact_id,
            evidence_artifact_ids=[artifact.artifact_id],
            metadata={"provider": request.provider, "status": "degraded"},
            trace_id=trace_id,
        )
        return MediaOperationResponse(
            media=media,
            derivatives=[derivative],
            analysis=analysis,
            artifacts=[artifact],
            status="degraded",
            message="转写 provider 未启用，已记录可恢复转写占位",
            degraded_reason="transcription_provider_unavailable",
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
    return "unknown"


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
