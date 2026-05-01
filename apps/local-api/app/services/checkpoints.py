from __future__ import annotations

import hashlib
import shutil
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from core_types import (
    CheckpointItem,
    ErrorCode,
    RiskLevel,
    RollbackEvent,
    RollbackItem,
    TaskCheckpoint,
    TraceSpanStatus,
    TraceSpanType,
)
from safety_service import SafetyService
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.checkpoint_repo import CheckpointRepository
from app.db.repositories.task_repo import TaskRepository
from app.services.artifacts import ArtifactStore
from app.services.audit import AuditEventService

MAX_CHECKPOINT_ITEM_BYTES = 1_000_000
MAX_CHECKPOINT_TOTAL_BYTES = 5_000_000
CHECKPOINT_TTL_DAYS = 14


class CheckpointService:
    def __init__(
        self,
        *,
        repo: CheckpointRepository,
        task_repo: TaskRepository,
        artifact_store: ArtifactStore,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._tasks = task_repo
        self._artifacts = artifact_store
        self._trace = trace_service
        self._audit = audit_service
        self._safety = SafetyService()
        self._rollback_notification_callback: (
            Callable[..., Awaitable[Any]] | None
        ) = None

    def set_rollback_notification_callback(
        self,
        callback: Callable[..., Awaitable[Any]],
    ) -> None:
        self._rollback_notification_callback = callback

    async def create_checkpoint(
        self,
        *,
        task_id: str,
        paths: list[str],
        checkpoint_type: str = "manual",
        scope: str = "task_artifacts",
        step_id: str | None = None,
        tool_call_id: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> TaskCheckpoint:
        task = await self._get_task(task_id)
        if not paths:
            raise AppError(ErrorCode.VALIDATION_ERROR, "checkpoint paths 不能为空", status_code=422)
        checkpoint_id = new_id("chk")
        now = utc_now_iso()
        expires_at = (utc_now() + timedelta(days=CHECKPOINT_TTL_DAYS)).isoformat()
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.TASK_REPLAY,
            "create task checkpoint",
            {"task_id": task_id, "checkpoint_id": checkpoint_id},
        )
        unique_paths = list(dict.fromkeys(paths))
        total_size = 0
        restorable = True
        try:
            checkpoint_data = {
                "checkpoint_id": checkpoint_id,
                "organization_id": task["organization_id"],
                "task_id": task_id,
                "step_id": step_id,
                "tool_call_id": tool_call_id,
                "checkpoint_type": checkpoint_type,
                "scope": scope,
                "status": "creating",
                "item_count": 0,
                "size_bytes": 0,
                "restorable": True,
                "policy_snapshot": _checkpoint_policy_snapshot(scope),
                "metadata": redact({"reason": reason, **(metadata or {})}),
                "expires_at": expires_at,
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
            await self._repo.insert_checkpoint(checkpoint_data)
            for raw_path in unique_paths:
                item_data = await self._create_item_snapshot(
                    checkpoint_id=checkpoint_id,
                    organization_id=task["organization_id"],
                    task_id=task_id,
                    raw_path=raw_path,
                    trace_id=trace_id,
                    created_at=now,
                )
                total_size += int(item_data.get("before_size_bytes") or 0)
                restorable = restorable and bool(item_data.get("restorable", True))
                if total_size > MAX_CHECKPOINT_TOTAL_BYTES:
                    raise AppError(
                        ErrorCode.ARTIFACT_WRITE_FAILED,
                        "checkpoint 超出任务快照配额",
                        status_code=413,
                        details={"reason_code": "checkpoint_total_quota_exceeded"},
                    )
                await self._repo.insert_item(item_data)
            status = "ready" if restorable else "partial"
            await self._repo.update_checkpoint(
                checkpoint_id,
                {
                    "status": status,
                    "item_count": len(unique_paths),
                    "size_bytes": total_size,
                    "restorable": restorable,
                    "updated_at": utc_now_iso(),
                },
            )
            await self._tasks.insert_event(
                {
                    "event_id": new_id("tevt"),
                    "organization_id": task["organization_id"],
                    "task_id": task_id,
                    "step_id": step_id,
                    "event_type": "checkpoint.created",
                    "payload": {
                        "checkpoint_id": checkpoint_id,
                        "item_count": len(unique_paths),
                        "rollback_available": restorable,
                    },
                    "payload_redacted": {
                        "checkpoint_id": checkpoint_id,
                        "item_count": len(unique_paths),
                        "rollback_available": restorable,
                    },
                    "trace_id": trace_id,
                    "created_at": utc_now_iso(),
                }
            )
            await self._audit.write_event(
                actor_type="system",
                action="checkpoint.created",
                object_type="task_checkpoint",
                object_id=checkpoint_id,
                summary="任务 checkpoint 已创建",
                risk_level=RiskLevel.R1,
                payload={
                    "task_id": task_id,
                    "checkpoint_id": checkpoint_id,
                    "item_count": len(unique_paths),
                },
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"checkpoint_id": checkpoint_id})
            return await self.get_checkpoint(checkpoint_id)
        except Exception as exc:
            await self._repo.update_checkpoint(
                checkpoint_id,
                {
                    "status": "failed",
                    "failure_reason": str(redact(str(exc))),
                    "restorable": False,
                    "updated_at": utc_now_iso(),
                },
            )
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def finalize_checkpoint(self, checkpoint_id: str) -> TaskCheckpoint:
        checkpoint = await self.get_checkpoint(checkpoint_id)
        restorable = True
        for item in await self._repo.list_items(checkpoint_id):
            path = self._path_from_target_uri(item["target_uri"], checkpoint.task_id)
            after_exists = path.exists() and path.is_file()
            after_checksum = _checksum(path) if after_exists else None
            after_size = path.stat().st_size if after_exists else 0
            restorable = restorable and bool(item.get("restorable", True))
            await self._repo.update_item(
                item["checkpoint_item_id"],
                {
                    "after_exists": after_exists,
                    "after_checksum": after_checksum,
                    "after_size_bytes": after_size,
                    "updated_at": utc_now_iso(),
                },
            )
        await self._repo.update_checkpoint(
            checkpoint_id,
            {
                "status": "ready" if restorable else "partial",
                "restorable": restorable,
                "updated_at": utc_now_iso(),
            },
        )
        return await self.get_checkpoint(checkpoint_id)

    async def rollback(
        self,
        checkpoint_id: str,
        *,
        requested_by: str,
        reason: str | None,
        trace_id: str | None,
    ) -> tuple[RollbackEvent, list[RollbackItem]]:
        checkpoint = await self.get_checkpoint(checkpoint_id)
        if checkpoint.status not in {"ready", "partial", "rolled_back"}:
            raise AppError(
                ErrorCode.TASK_STATE_INVALID,
                "checkpoint 当前状态不可回滚",
                status_code=409,
                details={"status": checkpoint.status},
            )
        rollback_id = new_id("rb")
        now = utc_now_iso()
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.TASK_REPLAY,
            "rollback task checkpoint",
            {"checkpoint_id": checkpoint_id, "rollback_id": rollback_id},
        )
        event_data = {
            "rollback_id": rollback_id,
            "organization_id": checkpoint.organization_id,
            "checkpoint_id": checkpoint_id,
            "task_id": checkpoint.task_id,
            "requested_by": requested_by,
            "reason": reason,
            "status": "running",
            "restored_items": 0,
            "skipped_items": 0,
            "conflict_items": 0,
            "policy_snapshot": checkpoint.policy_snapshot,
            "trace_id": trace_id,
            "created_at": now,
        }
        await self._repo.insert_rollback_event(event_data)
        restored = 0
        skipped = 0
        conflicts = 0
        rollback_items: list[RollbackItem] = []
        try:
            for item in await self._repo.list_items(checkpoint_id):
                item_result = await self._rollback_item(rollback_id, item, trace_id=trace_id)
                rollback_items.append(item_result)
                if item_result.status == "restored":
                    restored += 1
                elif item_result.status == "conflict":
                    conflicts += 1
                else:
                    skipped += 1
            status = "completed" if conflicts == 0 else "completed_with_conflicts"
            completed_at = utc_now_iso()
            await self._repo.update_rollback_event(
                rollback_id,
                {
                    "status": status,
                    "restored_items": restored,
                    "skipped_items": skipped,
                    "conflict_items": conflicts,
                    "completed_at": completed_at,
                },
            )
            await self._repo.update_checkpoint(
                checkpoint_id,
                {"status": "rolled_back", "updated_at": completed_at},
            )
            await self._tasks.insert_event(
                {
                    "event_id": new_id("tevt"),
                    "organization_id": checkpoint.organization_id,
                    "task_id": checkpoint.task_id,
                    "step_id": checkpoint.step_id,
                    "event_type": "checkpoint.rollback_completed",
                    "payload": {
                        "checkpoint_id": checkpoint_id,
                        "rollback_id": rollback_id,
                        "status": status,
                        "restored_items": restored,
                        "conflict_items": conflicts,
                    },
                    "payload_redacted": {
                        "checkpoint_id": checkpoint_id,
                        "rollback_id": rollback_id,
                        "status": status,
                        "restored_items": restored,
                        "conflict_items": conflicts,
                    },
                    "trace_id": trace_id,
                    "created_at": completed_at,
                }
            )
            await self._audit.write_event(
                actor_type="user",
                actor_id=requested_by,
                action="checkpoint.rollback",
                object_type="task_checkpoint",
                object_id=checkpoint_id,
                summary="任务 checkpoint 已回滚",
                risk_level=RiskLevel.R2,
                payload={
                    "rollback_id": rollback_id,
                    "status": status,
                    "restored_items": restored,
                    "conflict_items": conflicts,
                },
                trace_id=trace_id,
            )
            await self._notify_rollback_summary(
                task_id=checkpoint.task_id,
                checkpoint_id=checkpoint_id,
                rollback_id=rollback_id,
                status=status,
                restored_items=restored,
                conflict_items=conflicts,
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"rollback_id": rollback_id})
            event = await self._repo.get_rollback_event(rollback_id)
            if event is None:
                raise AppError(ErrorCode.INTERNAL_ERROR, "rollback event 无法读取", status_code=500)
            return RollbackEvent(**event), rollback_items
        except Exception:
            await self._repo.update_rollback_event(
                rollback_id,
                {"status": "failed", "completed_at": utc_now_iso()},
            )
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def _notify_rollback_summary(
        self,
        *,
        task_id: str,
        checkpoint_id: str,
        rollback_id: str,
        status: str,
        restored_items: int,
        conflict_items: int,
        trace_id: str | None,
    ) -> None:
        if self._rollback_notification_callback is None:
            return
        try:
            await self._rollback_notification_callback(
                task_id=task_id,
                checkpoint_id=checkpoint_id,
                rollback_id=rollback_id,
                status=status,
                restored_items=restored_items,
                conflict_items=conflict_items,
                trace_id=trace_id,
            )
        except Exception:
            checkpoint = await self.get_checkpoint(checkpoint_id)
            await self._tasks.insert_event(
                {
                    "event_id": new_id("tevt"),
                    "organization_id": checkpoint.organization_id,
                    "task_id": task_id,
                    "step_id": checkpoint.step_id,
                    "event_type": "checkpoint.rollback_notification_failed",
                    "payload": {
                        "checkpoint_id": checkpoint_id,
                        "rollback_id": rollback_id,
                        "status": status,
                    },
                    "payload_redacted": {
                        "checkpoint_id": checkpoint_id,
                        "rollback_id": rollback_id,
                        "status": status,
                    },
                    "trace_id": trace_id,
                    "created_at": utc_now_iso(),
                }
            )

    async def list_checkpoints(self, task_id: str) -> list[TaskCheckpoint]:
        await self._get_task(task_id)
        return [
            TaskCheckpoint(**row)
            for row in await self._repo.list_checkpoints(task_id)
        ]

    async def get_checkpoint(self, checkpoint_id: str) -> TaskCheckpoint:
        row = await self._repo.get_checkpoint(checkpoint_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "checkpoint 不存在", status_code=404)
        return TaskCheckpoint(**row)

    async def checkpoint_detail(
        self,
        checkpoint_id: str,
    ) -> tuple[TaskCheckpoint, list[CheckpointItem]]:
        checkpoint = await self.get_checkpoint(checkpoint_id)
        return checkpoint, [
            CheckpointItem(**row) for row in await self._repo.list_items(checkpoint_id)
        ]

    async def list_items(self, checkpoint_id: str) -> list[CheckpointItem]:
        await self.get_checkpoint(checkpoint_id)
        return [CheckpointItem(**row) for row in await self._repo.list_items(checkpoint_id)]

    async def list_rollback_events(self, task_id: str) -> list[RollbackEvent]:
        await self._get_task(task_id)
        return [
            RollbackEvent(**row)
            for row in await self._repo.list_rollback_events(task_id)
        ]

    async def replay_checkpoint_data(
        self,
        task_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        checkpoints = []
        for row in await self._repo.list_checkpoints(task_id):
            items = await self._repo.list_items(row["checkpoint_id"])
            checkpoints.append(redact({**row, "items": items}))
        rollback_events = []
        for row in await self._repo.list_rollback_events(task_id):
            items = await self._repo.list_rollback_items(row["rollback_id"])
            rollback_events.append(redact({**row, "items": items}))
        return checkpoints, rollback_events

    async def expire_due_checkpoints(
        self,
        *,
        limit: int = 100,
        trace_id: str | None = None,
    ) -> int:
        expired = await self._repo.expire_due_checkpoints(
            now=utc_now_iso(),
            updated_at=utc_now_iso(),
            limit=limit,
        )
        if expired:
            await self._audit.write_event(
                actor_type="system",
                action="checkpoint.expire_due",
                object_type="task_checkpoint",
                object_id="batch",
                summary="过期 checkpoint 已标记",
                risk_level=RiskLevel.R1,
                payload={"expired_count": expired},
                trace_id=trace_id,
            )
        return expired

    async def _create_item_snapshot(
        self,
        *,
        checkpoint_id: str,
        organization_id: str,
        task_id: str,
        raw_path: str,
        trace_id: str | None,
        created_at: str,
    ) -> dict[str, Any]:
        normalized_path = _normalize_checkpoint_path(raw_path, task_id)
        path = self._artifacts.resolve_task_relative_path(task_id, normalized_path)
        root = self._artifacts.task_dir(task_id)
        relative = _relative_to_task(path, root)
        target_uri = f"artifact://{task_id}/{relative}"
        exists_before = path.exists() and path.is_file()
        before_checksum = _checksum(path) if exists_before else None
        before_size = path.stat().st_size if exists_before else 0
        if before_size > MAX_CHECKPOINT_ITEM_BYTES:
            raise AppError(
                ErrorCode.ARTIFACT_WRITE_FAILED,
                "checkpoint item 超出单文件快照配额",
                status_code=413,
                details={"reason_code": "checkpoint_item_quota_exceeded"},
            )
        snapshot_artifact_id = None
        snapshot_uri = None
        restorable = True
        sensitivity = "low"
        if exists_before:
            data = path.read_bytes()
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text and self._safety.classify_chat_input(text).sensitivity_hits:
                sensitivity = "high"
                restorable = False
            else:
                artifact = await self._artifacts.write_bytes(
                    task_id=task_id,
                    organization_id=organization_id,
                    display_name=_snapshot_name(relative),
                    content=data,
                    artifact_type="checkpoint_snapshot",
                    content_type="application/octet-stream",
                    subdir=f"checkpoints/{checkpoint_id}",
                    step_id=None,
                    tool_call_id=None,
                    sensitivity=sensitivity,
                    metadata={
                        "checkpoint_id": checkpoint_id,
                        "target_uri": target_uri,
                        "checkpoint_snapshot": True,
                    },
                    trace_id=trace_id,
                )
                snapshot_artifact_id = artifact.artifact_id
                snapshot_uri = artifact.uri
        return {
            "checkpoint_item_id": new_id("chki"),
            "checkpoint_id": checkpoint_id,
            "organization_id": organization_id,
            "task_id": task_id,
            "target_uri": target_uri,
            "target_path_redacted": f"artifact://{task_id}/{relative}",
            "item_type": "file",
            "exists_before": exists_before,
            "before_checksum": before_checksum,
            "before_size_bytes": before_size,
            "snapshot_artifact_id": snapshot_artifact_id,
            "snapshot_uri": snapshot_uri,
            "content_type": "application/octet-stream" if exists_before else None,
            "sensitivity": sensitivity,
            "restorable": restorable,
            "metadata": {"raw_path": str(redact(normalized_path))},
            "created_at": created_at,
            "updated_at": created_at,
        }

    async def _rollback_item(
        self,
        rollback_id: str,
        item: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> RollbackItem:
        del trace_id
        path = self._path_from_target_uri(item["target_uri"], item["task_id"])
        current_exists = path.exists() and path.is_file()
        current_checksum = _checksum(path) if current_exists else None
        expected_checksum = item.get("after_checksum")
        expected_exists = item.get("after_exists")
        action = "restore" if item["exists_before"] else "delete_created"
        status = "restored"
        reason = None
        restored_checksum = None

        if (
            item["exists_before"]
            and current_exists
            and current_checksum == item.get("before_checksum")
        ):
            status = "skipped"
            reason = "already_restored"
            restored_checksum = current_checksum
        elif not item["exists_before"] and not current_exists:
            status = "skipped"
            reason = "already_absent"
        elif not item.get("restorable", True):
            status = "skipped"
            reason = "checkpoint_item_not_restorable"
        elif expected_exists is not None and bool(expected_exists) != current_exists:
            status = "conflict"
            reason = "current_exists_changed"
        elif expected_checksum and current_checksum != expected_checksum:
            status = "conflict"
            reason = "current_checksum_changed"
        elif item["exists_before"]:
            if not item.get("snapshot_uri"):
                status = "skipped"
                reason = "snapshot_missing"
            else:
                snapshot_path = self._path_from_snapshot_uri(item["snapshot_uri"], item["task_id"])
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(snapshot_path, path)
                restored_checksum = _checksum(path)
        else:
            if current_exists:
                path.unlink()
            restored_checksum = None

        data = {
            "rollback_item_id": new_id("rbi"),
            "rollback_id": rollback_id,
            "checkpoint_item_id": item["checkpoint_item_id"],
            "organization_id": item["organization_id"],
            "task_id": item["task_id"],
            "target_uri": item["target_uri"],
            "action": action,
            "status": status,
            "reason": reason,
            "before_checksum": item.get("before_checksum"),
            "current_checksum": current_checksum,
            "restored_checksum": restored_checksum,
            "created_at": utc_now_iso(),
        }
        await self._repo.insert_rollback_item(data)
        return RollbackItem(**data)

    async def _get_task(self, task_id: str) -> dict[str, Any]:
        task = await self._tasks.get_task(task_id)
        if task is None:
            raise AppError(ErrorCode.NOT_FOUND, "任务不存在", status_code=404)
        return task

    def _path_from_target_uri(self, uri: str, task_id: str) -> Path:
        prefix = f"artifact://{task_id}/"
        if not uri.startswith(prefix):
            raise AppError(
                ErrorCode.TOOL_PERMISSION_DENIED,
                "checkpoint URI 不合法",
                status_code=403,
            )
        return self._artifacts.resolve_task_relative_path(task_id, uri.removeprefix(prefix))

    def _path_from_snapshot_uri(self, uri: str, task_id: str) -> Path:
        prefix = f"artifact://{task_id}/"
        if not uri.startswith(prefix):
            raise AppError(ErrorCode.ARTIFACT_NOT_FOUND, "snapshot URI 不合法", status_code=404)
        return self._artifacts.resolve_task_relative_path(task_id, uri.removeprefix(prefix))

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        input_data: dict[str, Any],
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            input_data=redact(input_data),
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


def rollback_availability_for_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name in {"file.write", "file.delete", "file.move", "file.copy"}:
        paths = [str(args.get("path") or "")]
        if tool_name in {"file.move", "file.copy"} and args.get("destination"):
            paths.append(str(args["destination"]))
        return {
            "rollback_available": True,
            "checkpoint_required": tool_name in {"file.write", "file.delete", "file.move"},
            "scope": "task_artifacts",
            "paths": [path for path in paths if path],
        }
    return {
        "rollback_available": False,
        "checkpoint_required": False,
        "reason": "external_or_non_workspace_side_effect",
        "compensating_action": "manual_review",
    }


def _checkpoint_policy_snapshot(scope: str) -> dict[str, Any]:
    return {
        "allowed_roots": ["artifact://{task_id}/**"],
        "denied_roots": ["**/.env", "**/secrets/**", "~/.ssh/**"],
        "scope": scope,
        "rollback_mode": "copy_restore",
        "max_item_bytes": MAX_CHECKPOINT_ITEM_BYTES,
        "max_total_bytes": MAX_CHECKPOINT_TOTAL_BYTES,
        "ttl_days": CHECKPOINT_TTL_DAYS,
    }


def _relative_to_task(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "checkpoint 路径不能逃逸任务工件目录",
            status_code=403,
        ) from exc


def _snapshot_name(relative: str) -> str:
    safe = relative.replace("\\", "/").replace("/", "__").strip("._")
    return safe or "snapshot.bin"


def _normalize_checkpoint_path(value: str, task_id: str) -> str:
    prefix = f"artifact://{task_id}/"
    if value.startswith(prefix):
        return value.removeprefix(prefix)
    if value.startswith("artifact://"):
        raise AppError(
            ErrorCode.TOOL_PERMISSION_DENIED,
            "checkpoint URI 不属于当前任务",
            status_code=403,
        )
    return value


def _checksum(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
