from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from core_types import ErrorCode, RiskLevel, TaskArtifact, TraceSpanStatus, TraceSpanType
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.task_repo import TaskRepository
from app.services.audit import AuditEventService


class ArtifactStore:
    def __init__(
        self,
        *,
        root_dir: Path,
        repo: TaskRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._root = root_dir
        self._repo = repo
        self._trace = trace_service
        self._audit = audit_service

    def task_dir(self, task_id: str) -> Path:
        return (self._root / task_id).resolve()

    async def write_text(
        self,
        *,
        task_id: str,
        organization_id: str,
        display_name: str,
        content: str,
        artifact_type: str = "text",
        subdir: str = "outputs",
        step_id: str | None = None,
        tool_call_id: str | None = None,
        sensitivity: str = "low",
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> TaskArtifact:
        span_id = await self._start_span(
            trace_id,
            metadata={"task_id": task_id, "display_name": display_name},
        )
        try:
            safe_name = _safe_filename(display_name)
            target_dir = (self.task_dir(task_id) / subdir).resolve()
            if self.task_dir(task_id) not in [target_dir, *target_dir.parents]:
                raise AppError(
                    ErrorCode.ARTIFACT_WRITE_FAILED,
                    "artifact path is invalid",
                    status_code=422,
                )
            _mkdir_p(target_dir)
            path = (target_dir / safe_name).resolve()
            if target_dir not in [path, *path.parents]:
                raise AppError(
                    ErrorCode.ARTIFACT_WRITE_FAILED,
                    "artifact 文件名不合法",
                    status_code=422,
                )
            redacted_content = str(redact(content))
            _write_text(path, redacted_content)
            raw = _read_bytes(path)
            checksum = "sha256:" + hashlib.sha256(raw).hexdigest()
            artifact_id = new_id("art")
            created_at = utc_now_iso()
            uri = f"artifact://{task_id}/{subdir}/{safe_name}"
            data = {
                "artifact_id": artifact_id,
                "organization_id": organization_id,
                "task_id": task_id,
                "step_id": step_id,
                "tool_call_id": tool_call_id,
                "artifact_type": artifact_type,
                "display_name": safe_name,
                "uri": uri,
                "content_type": "text/plain; charset=utf-8",
                "size_bytes": len(raw),
                "checksum": checksum,
                "sensitivity": sensitivity,
                "metadata": redact(metadata or {}),
                "created_at": created_at,
            }
            await self._repo.insert_artifact(data)
            await self._audit.write_event(
                actor_type="system",
                action="artifact.created",
                object_type="task_artifact",
                object_id=artifact_id,
                summary="task artifact created",
                risk_level=RiskLevel.R1,
                payload={
                    "artifact_id": artifact_id,
                    "task_id": task_id,
                    "uri": uri,
                    "checksum": checksum,
                },
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                output_data={"artifact_id": artifact_id, "checksum": checksum},
            )
            return TaskArtifact(**data)
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def write_bytes(
        self,
        *,
        task_id: str,
        organization_id: str,
        display_name: str,
        content: bytes,
        artifact_type: str = "file",
        content_type: str = "application/octet-stream",
        subdir: str = "outputs",
        step_id: str | None = None,
        tool_call_id: str | None = None,
        sensitivity: str = "low",
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> TaskArtifact:
        span_id = await self._start_span(
            trace_id,
            metadata={"task_id": task_id, "display_name": display_name},
        )
        try:
            safe_name = _safe_filename(display_name)
            target_dir = (self.task_dir(task_id) / subdir).resolve()
            if self.task_dir(task_id) not in [target_dir, *target_dir.parents]:
                raise AppError(
                    ErrorCode.ARTIFACT_WRITE_FAILED,
                    "artifact path is invalid",
                    status_code=422,
                )
            _mkdir_p(target_dir)
            path = (target_dir / safe_name).resolve()
            if target_dir not in [path, *path.parents]:
                raise AppError(
                    ErrorCode.ARTIFACT_WRITE_FAILED,
                    "artifact 文件名不合法",
                    status_code=422,
                )
            _write_bytes(path, content)
            checksum = "sha256:" + hashlib.sha256(content).hexdigest()
            artifact_id = new_id("art")
            created_at = utc_now_iso()
            uri = f"artifact://{task_id}/{subdir}/{safe_name}"
            data = {
                "artifact_id": artifact_id,
                "organization_id": organization_id,
                "task_id": task_id,
                "step_id": step_id,
                "tool_call_id": tool_call_id,
                "artifact_type": artifact_type,
                "display_name": safe_name,
                "uri": uri,
                "content_type": content_type,
                "size_bytes": len(content),
                "checksum": checksum,
                "sensitivity": sensitivity,
                "metadata": redact(metadata or {}),
                "created_at": created_at,
            }
            await self._repo.insert_artifact(data)
            await self._audit.write_event(
                actor_type="system",
                action="artifact.created",
                object_type="task_artifact",
                object_id=artifact_id,
                summary="task artifact created",
                risk_level=RiskLevel.R1,
                payload={
                    "artifact_id": artifact_id,
                    "task_id": task_id,
                    "uri": uri,
                    "checksum": checksum,
                },
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                output_data={"artifact_id": artifact_id, "checksum": checksum},
            )
            return TaskArtifact(**data)
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def read_preview(
        self,
        artifact_id: str,
        *,
        limit: int = 2000,
    ) -> tuple[TaskArtifact, str]:
        row = await self._repo.get_artifact(artifact_id)
        if row is None:
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "artifact not found", status_code=404)
        artifact = TaskArtifact(**row)
        path = self._path_from_uri(artifact.uri)
        if not _path_exists(path):
            raise AppError(
                ErrorCode.ARTIFACT_NOT_FOUND,
                "artifact file not found",
                status_code=404,
                details={"artifact_id": artifact_id},
            )
        raw = _read_bytes(path)
        try:
            preview = raw[:limit].decode("utf-8")
        except UnicodeDecodeError:
            preview = raw[: min(limit, 512)].hex()
        return artifact, str(redact(preview))[:limit]

    async def open_download(self, artifact_id: str) -> tuple[TaskArtifact, Path]:
        row = await self._repo.get_artifact(artifact_id)
        if row is None:
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "artifact not found", status_code=404)
        artifact = TaskArtifact(**row)
        path = self.path_for_artifact(artifact)
        if not _path_exists(path) or not _is_file(path):
            raise AppError(
                ErrorCode.ARTIFACT_NOT_FOUND,
                "artifact file not found",
                status_code=404,
                details={"artifact_id": artifact_id},
            )
        return artifact, path

    async def get_artifact(self, artifact_id: str) -> TaskArtifact:
        row = await self._repo.get_artifact(artifact_id)
        if row is None:
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "artifact not found", status_code=404)
        return TaskArtifact(**row)

    async def list_task_artifacts(self, task_id: str) -> list[TaskArtifact]:
        return [TaskArtifact(**row) for row in await self._repo.list_artifacts(task_id)]

    def resolve_task_relative_path(self, task_id: str, value: str) -> Path:
        root = self.task_dir(task_id)
        path = (root / value).resolve()
        if root not in [path, *path.parents]:
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "file path escapes task artifact directory",
                status_code=403,
            )
        if _is_sensitive_path(path):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "禁止访问敏感系统路径",
                status_code=403,
            )
        return path

    def _path_from_uri(self, uri: str) -> Path:
        if not uri.startswith("artifact://"):
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "不支持的工件 URI", status_code=404)
        relative = uri.removeprefix("artifact://")
        task_id, _, rest = relative.partition("/")
        return (self.task_dir(task_id) / rest).resolve()

    def path_for_artifact(self, artifact: TaskArtifact) -> Path:
        path = self._path_from_uri(artifact.uri)
        root = self.task_dir(artifact.task_id)
        if root not in [path, *path.parents]:
            raise AppError(
                ErrorCode.ARTIFACT_NOT_FOUND,
                "artifact path is invalid",
                status_code=404,
            )
        return path

    async def _start_span(self, trace_id: str | None, *, metadata: dict[str, Any]) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.ARTIFACT_WRITE,
            name="write task artifact",
            metadata=metadata,
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                status=status,
                output_data=redact(output_data or {}),
            )


def _safe_filename(value: str) -> str:
    name = Path(value).name.strip().replace("\\", "_").replace("/", "_")
    return name or "artifact.txt"


def _is_sensitive_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    forbidden_names = {".env", ".env.local", "master.key", "local_secrets.json"}
    return "secrets" in parts or path.name.lower() in forbidden_names


def _mkdir_p(path: Path) -> None:
    os.makedirs(_fs_path(path), exist_ok=True)


def _write_text(path: Path, content: str) -> None:
    with open(_fs_path(path), "w", encoding="utf-8") as handle:
        handle.write(content)


def _write_bytes(path: Path, content: bytes) -> None:
    with open(_fs_path(path), "wb") as handle:
        handle.write(content)


def _read_bytes(path: Path) -> bytes:
    with open(_fs_path(path), "rb") as handle:
        return handle.read()


def _path_exists(path: Path) -> bool:
    return os.path.exists(_fs_path(path))


def _is_file(path: Path) -> bool:
    return os.path.isfile(_fs_path(path))


def _fs_path(path: Path) -> str:
    raw = str(path)
    if os.name != "nt" or raw.startswith("\\\\?\\") or len(raw) < 240:
        return raw
    normalized = raw.replace("/", "\\")
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized.lstrip("\\")
    return "\\\\?\\" + normalized
