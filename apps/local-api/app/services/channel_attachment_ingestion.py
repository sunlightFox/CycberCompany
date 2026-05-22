from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from core_types import Attachment, ErrorCode, RiskLevel, TraceSpanStatus, TraceSpanType
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.channel_repo import ChannelRepository
from app.services.channel_connectors import ChannelConnectorRegistry

DEFAULT_MAX_ATTACHMENT_BYTES = 10_485_760


class ChannelAttachmentIngestionService:
    """Download channel attachments, persist blobs, and expose artifact evidence."""

    def __init__(
        self,
        *,
        repo: ChannelRepository,
        connectors: ChannelConnectorRegistry,
        data_dir: Path,
        trace_service: TraceService,
        max_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
    ) -> None:
        self._repo = repo
        self._connectors = connectors
        self._blob_root = (data_dir / "channel-attachments").resolve()
        self._trace = trace_service
        self._max_bytes = max_bytes

    async def process_attachments(
        self,
        *,
        provider: str,
        account: dict[str, Any],
        session: dict[str, Any],
        provider_state: dict[str, Any] | None,
        channel_event_id: str,
        normalized: dict[str, Any],
        trace_id: str | None,
    ) -> list[Attachment]:
        outputs: list[Attachment] = []
        items = [item for item in normalized.get("attachments") or [] if isinstance(item, dict)]
        if not items:
            return outputs
        span_id = await self._start_span(
            trace_id,
            provider=provider,
            channel_event_id=channel_event_id,
            attachment_count=len(items),
        )
        try:
            for item in items:
                result = await self._process_one(
                    provider=provider,
                    account=account,
                    session=session,
                    provider_state=provider_state,
                    channel_event_id=channel_event_id,
                    normalized=normalized,
                    attachment=item,
                    trace_id=trace_id,
                )
                if result is not None:
                    outputs.append(result)
            await self._end_span(
                span_id,
                output_data={
                    "processed_count": len(outputs),
                    "channel_event_id": channel_event_id,
                },
            )
            return outputs
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def _process_one(
        self,
        *,
        provider: str,
        account: dict[str, Any],
        session: dict[str, Any],
        provider_state: dict[str, Any] | None,
        channel_event_id: str,
        normalized: dict[str, Any],
        attachment: dict[str, Any],
        trace_id: str | None,
    ) -> Attachment | None:
        attachment_id = new_id("chatt")
        now = utc_now_iso()
        attachment_type = _attachment_type(attachment)
        provider_ref = _hash_value(_attachment_ref(attachment))
        content_type = str(attachment.get("content_type") or _guess_content_type(attachment) or "application/octet-stream")
        display_name = str(attachment.get("name") or attachment.get("filename") or f"{provider}-{attachment_type}.bin")
        size_hint = attachment.get("size_bytes")
        try:
            content = await self._connectors.get(provider).download_media(
                provider_state=provider_state,
                event=normalized.get("raw_event") if isinstance(normalized.get("raw_event"), dict) else normalized,
                attachment=attachment,
            )
            if len(content) > self._max_bytes:
                raise AppError(
                    ErrorCode.TOOL_PERMISSION_DENIED,
                    "channel attachment exceeds size limit",
                    status_code=413,
                )
            blob_ref = self._write_blob(
                provider=provider,
                account_id=str(account["channel_account_id"]),
                channel_event_id=channel_event_id,
                attachment_id=attachment_id,
                content=content,
                display_name=display_name,
            )
            artifact_id = await self._ensure_source_artifact(
                provider=provider,
                account=account,
                session=session,
                display_name=display_name,
                content_type=content_type,
                size_bytes=len(content),
                blob_ref=blob_ref,
            )
            await self._repo.insert_attachment(
                {
                    "channel_attachment_id": attachment_id,
                    "organization_id": account["organization_id"],
                    "channel_event_id": channel_event_id,
                    "channel_account_id": account["channel_account_id"],
                    "channel_peer_session_id": session["channel_peer_session_id"],
                    "provider": provider,
                    "provider_attachment_ref_redacted": provider_ref,
                    "attachment_type": attachment_type,
                    "display_name_redacted": str(redact(display_name)),
                    "content_type": content_type,
                    "size_bytes": len(content),
                    "artifact_id": artifact_id,
                    "blob_ref": blob_ref,
                    "media_id": None,
                    "status": "ready",
                    "failure_reason": None,
                    "metadata": {
                        "provider": provider,
                        "source": f"{provider}_gateway",
                        "source_boundary": "channel_attachment_blob",
                        "storage": "channel_attachment_blob",
                        "untrusted_external_content": True,
                        "coalesced_index": attachment.get("coalesced_index"),
                        "original_name_redacted": str(redact(display_name)),
                    },
                    "trace_id": trace_id,
                    "created_at": now,
                    "updated_at": utc_now_iso(),
                }
            )
            return Attachment(
                attachment_id=attachment_id,
                name=str(redact(display_name)),
                content_type=content_type,
                uri=blob_ref,
                metadata={
                    "provider": provider,
                    "channel_attachment_id": attachment_id,
                    "artifact_id": artifact_id,
                    "attachment_type": attachment_type,
                    "storage": "channel_attachment_blob",
                    "source": f"{provider}_gateway",
                    "source_boundary": "channel_attachment_blob",
                    "untrusted_external_content": True,
                    "size_bytes": len(content),
                    "coalesced_index": attachment.get("coalesced_index"),
                },
            )
        except Exception as exc:
            existing = await self._repo.get_attachment_by_provider_ref(
                channel_event_id=channel_event_id,
                provider_attachment_ref_redacted=provider_ref,
            )
            if existing is not None and existing.get("blob_ref"):
                return Attachment(
                    attachment_id=existing["channel_attachment_id"],
                    name=existing.get("display_name_redacted"),
                    content_type=existing.get("content_type"),
                    uri=existing.get("blob_ref"),
                    metadata={
                        "provider": provider,
                        "channel_attachment_id": existing["channel_attachment_id"],
                        "artifact_id": existing.get("artifact_id"),
                        "attachment_type": existing["attachment_type"],
                        "storage": "channel_attachment_blob",
                        "source": f"{provider}_gateway",
                        "source_boundary": "channel_attachment_blob",
                        "untrusted_external_content": True,
                        "degraded": existing.get("status") == "degraded",
                    },
                )
            await self._repo.insert_attachment(
                {
                    "channel_attachment_id": attachment_id,
                    "organization_id": account["organization_id"],
                    "channel_event_id": channel_event_id,
                    "channel_account_id": account["channel_account_id"],
                    "channel_peer_session_id": session["channel_peer_session_id"],
                    "provider": provider,
                    "provider_attachment_ref_redacted": provider_ref,
                    "attachment_type": attachment_type,
                    "display_name_redacted": str(redact(display_name)),
                    "content_type": content_type,
                    "size_bytes": int(size_hint) if size_hint else None,
                    "status": "failed",
                    "failure_reason": str(redact(str(exc))),
                    "metadata": {
                        "provider": provider,
                        "source": f"{provider}_gateway",
                        "source_boundary": "channel_attachment_blob",
                        "untrusted_external_content": True,
                    },
                    "trace_id": trace_id,
                    "created_at": now,
                    "updated_at": utc_now_iso(),
                }
            )
            return None

    def _write_blob(
        self,
        *,
        provider: str,
        account_id: str,
        channel_event_id: str,
        attachment_id: str,
        content: bytes,
        display_name: str,
    ) -> str:
        suffix = Path(display_name).suffix[:16]
        digest = hashlib.sha256(content).hexdigest()
        storage_event_id = _hash_value(channel_event_id).removeprefix("sha256:")[:24]
        storage_attachment_id = _hash_value(attachment_id).removeprefix("sha256:")[:24]
        target_dir = self._blob_root / provider / account_id / storage_event_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{storage_attachment_id}{suffix}"
        _write_bytes(target, content)
        _write_text(
            target.with_suffix(target.suffix + ".json"),
            json.dumps(
                {
                    "attachment_id": attachment_id,
                    "sha256": digest,
                    "size_bytes": len(content),
                    "display_name_redacted": str(redact(display_name)),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return f"channel-attachment://{provider}/{account_id}/{storage_event_id}/{storage_attachment_id}"

    async def _ensure_source_artifact(
        self,
        *,
        provider: str,
        account: dict[str, Any],
        session: dict[str, Any],
        display_name: str,
        content_type: str,
        size_bytes: int,
        blob_ref: str,
    ) -> str:
        task_id = await self._ensure_channel_attachment_task(provider=provider, account=account, session=session)
        artifact_id = f"art_channel_{hashlib.sha256(blob_ref.encode('utf-8')).hexdigest()[:24]}"
        await self._repo.raw_execute(
            """
            INSERT OR IGNORE INTO task_artifacts (
              artifact_id, task_id, organization_id, artifact_type, display_name,
              uri, content_type, size_bytes, checksum, sensitivity,
              metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'high', ?, ?)
            """,
            (
                artifact_id,
                task_id,
                account["organization_id"],
                f"{provider}_channel_attachment",
                str(redact(display_name)),
                blob_ref,
                content_type,
                size_bytes,
                json.dumps(
                    {
                        "source": f"{provider}_gateway",
                        "source_boundary": "channel_attachment_blob",
                        "untrusted_external_content": True,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                utc_now_iso(),
            ),
        )
        return artifact_id

    async def _ensure_channel_attachment_task(
        self,
        *,
        provider: str,
        account: dict[str, Any],
        session: dict[str, Any],
    ) -> str:
        task_id = f"tsk_channel_media_{session['channel_peer_session_id']}"
        now = utc_now_iso()
        await self._repo.raw_execute(
            """
            INSERT OR IGNORE INTO tasks (
              task_id, organization_id, conversation_id, owner_member_id,
              title, goal, mode, status, risk_level, success_criteria_json,
              plan_json, budget_json, preflight_json, artifact_plan_json,
              retry_policy_json, progress_json, result_json, trace_id,
              created_at, updated_at, parent_task_id, host_member_id,
              collaboration_plan_id, supervisor_mode
            ) VALUES (?, ?, ?, ?, ?, ?, 'workflow', 'completed', 'R2', '[]',
              '{}', '{}', '{}', '{}', '{}', '{}', '{}', NULL,
              ?, ?, NULL, NULL, NULL, NULL)
            """,
            (
                task_id,
                account["organization_id"],
                session.get("conversation_id"),
                session["member_id"],
                f"{provider} channel attachments",
                f"Safely stage inbound {provider} channel attachments without executing content.",
                now,
                now,
            ),
        )
        return task_id

    async def _start_span(
        self,
        trace_id: str | None,
        *,
        provider: str,
        channel_event_id: str,
        attachment_count: int,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=TraceSpanType.CONTENT_NORMALIZE,
            name="channel attachment ingestion",
            input_data={
                "provider": provider,
                "channel_event_id": channel_event_id,
                "attachment_count": attachment_count,
            },
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(span_id, status=status, output_data=redact(output_data or {}))


def _attachment_type(attachment: dict[str, Any]) -> str:
    explicit = str(attachment.get("attachment_type") or attachment.get("type") or "").lower()
    content_type = str(attachment.get("content_type") or "").lower()
    if explicit in {"image", "audio", "video", "document", "file"}:
        return "document" if explicit == "file" and _looks_like_document(content_type, attachment) else explicit
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("audio/"):
        return "audio"
    if content_type.startswith("video/"):
        return "video"
    if _looks_like_document(content_type, attachment):
        return "document"
    return "file"


def _attachment_ref(attachment: dict[str, Any]) -> str:
    return str(
        attachment.get("file_key")
        or attachment.get("media_id")
        or attachment.get("file_id")
        or attachment.get("attachment_id")
        or attachment.get("url")
        or attachment.get("name")
        or "attachment"
    )


def _looks_like_document(content_type: str, attachment: dict[str, Any]) -> bool:
    name = str(attachment.get("name") or attachment.get("filename") or "").lower()
    return (
        content_type.startswith("text/")
        or content_type
        in {
            "application/pdf",
            "application/json",
            "text/csv",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        or Path(name).suffix.lower() in {".txt", ".md", ".json", ".csv", ".pdf", ".docx", ".xlsx"}
    )


def _guess_content_type(attachment: dict[str, Any]) -> str | None:
    suffix = Path(str(attachment.get("name") or attachment.get("filename") or "")).suffix.lower()
    return {
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".json": "application/json",
        ".csv": "text/csv",
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(suffix)


def _hash_value(value: str | None) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _write_bytes(path: Path, content: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())


def _write_text(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
