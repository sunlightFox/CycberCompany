from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import AppError
from app.services.artifacts import ArtifactStore


async def channel_outbound_attachment_selection(
    *,
    artifacts: ArtifactStore,
    turn: dict[str, Any],
    message: dict[str, Any],
    user_text: str,
    final_text: str,
) -> dict[str, Any]:
    del turn
    response_plan = message.get("content", {}).get("response_plan")
    response_plan = response_plan if isinstance(response_plan, dict) else {}
    structured = response_plan.get("structured_payload")
    structured = structured if isinstance(structured, dict) else {}
    refs = response_plan.get("artifact_refs")
    refs = refs if isinstance(refs, list) else []
    candidates = await _resolve_attachment_candidates(
        artifacts=artifacts,
        refs=refs,
        task_id=_attachment_task_id(structured),
    )
    scene = _attachment_scene(structured, candidates)
    explicit_request = _looks_like_attachment_request(user_text)
    reply_implies_document = _reply_mentions_generated_document(final_text)
    completed_summary = _attachment_completed_summary(structured)
    completed_summary_implies_document = _reply_mentions_generated_document(completed_summary)
    reason_codes: list[str] = []
    if explicit_request:
        reason_codes.append("explicit_attachment_request")
    if scene != "generic":
        reason_codes.append(f"scene:{scene}")
    if reply_implies_document:
        reason_codes.append("reply_mentions_generated_document")
    if completed_summary_implies_document:
        reason_codes.append("completed_summary_mentions_generated_document")
    should_send = bool(candidates) and (
        explicit_request
        or scene in {"office_document", "office_text"}
        or reply_implies_document
        or completed_summary_implies_document
    )
    selected = _sort_attachment_candidates(candidates, user_text=user_text, scene=scene)
    suppressed = [
        {
            "artifact_id": item.get("artifact_id"),
            "display_name": item.get("display_name"),
            "reason": "filtered_after_sort",
        }
        for item in candidates
        if item not in selected
    ]
    if not should_send:
        suppressed.extend(
            {
                "artifact_id": item.get("artifact_id"),
                "display_name": item.get("display_name"),
                "reason": "delivery_not_triggered",
            }
            for item in selected
        )
        selected = []
    return {
        "should_send_attachments": should_send,
        "selected_attachments": selected,
        "selection_reason_codes": reason_codes,
        "suppressed_attachments": suppressed,
        "scene": scene,
        "explicit_request_detected": explicit_request,
    }


async def _resolve_attachment_candidates(
    *,
    artifacts: ArtifactStore,
    refs: list[dict[str, Any]],
    task_id: str | None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        artifact_id = str(ref.get("artifact_id") or "")
        if not artifact_id or artifact_id in seen:
            continue
        candidate = await _resolve_attachment_candidate(artifacts=artifacts, ref=ref)
        if candidate is not None:
            candidates.append(candidate)
            seen.add(artifact_id)
    if candidates or not task_id:
        return candidates
    for artifact in await artifacts.list_task_artifacts(task_id):
        if artifact.artifact_id in seen:
            continue
        candidate = _candidate_from_artifact(
            artifact_id=artifact.artifact_id,
            display_name=artifact.display_name,
            content_type=artifact.content_type,
            artifact_type=artifact.artifact_type,
            created_at=artifact.created_at,
            download_url=f"/api/artifacts/{artifact.artifact_id}/download",
            metadata=artifact.metadata,
        )
        if candidate is not None:
            candidates.append(candidate)
            seen.add(artifact.artifact_id)
    return candidates


async def _resolve_attachment_candidate(
    *,
    artifacts: ArtifactStore,
    ref: dict[str, Any],
) -> dict[str, Any] | None:
    artifact_id = str(ref.get("artifact_id") or "")
    if not artifact_id:
        return None
    try:
        artifact = await artifacts.get_artifact(artifact_id)
    except AppError:
        return None
    return _candidate_from_artifact(
        artifact_id=artifact_id,
        display_name=str(ref.get("display_name") or artifact.display_name),
        content_type=str(ref.get("content_type") or artifact.content_type or ""),
        artifact_type=str(getattr(artifact, "artifact_type", "") or ""),
        created_at=str(getattr(artifact, "created_at", "") or ""),
        download_url=str(ref.get("download_url") or f"/api/artifacts/{artifact_id}/download"),
        metadata=artifact.metadata,
    )


def _candidate_from_artifact(
    *,
    artifact_id: str,
    display_name: str,
    content_type: str,
    artifact_type: str,
    created_at: str,
    download_url: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any] | None:
    suffix = Path(display_name).suffix.lower()
    allowed_suffixes = {".docx", ".xlsx", ".pptx", ".pdf", ".md", ".txt"}
    blocked_artifact_types = {
        "terminal_log",
        "checkpoint_snapshot",
        "screenshot",
        "download",
        "image",
        "audio",
        "video",
        "trace",
    }
    if suffix not in allowed_suffixes or artifact_type in blocked_artifact_types:
        return None
    name = display_name.lower()
    blocked_name_markers = (
        "terminal",
        "checkpoint",
        "screenshot",
        "debug",
        "trace",
        "diagnostic",
        "recovery",
        "transcript",
    )
    if any(marker in name for marker in blocked_name_markers):
        return None
    return {
        "artifact_id": artifact_id,
        "display_name": display_name,
        "content_type": content_type,
        "download_url": download_url,
        "delivery_role": _attachment_delivery_role(display_name, suffix=suffix),
        "artifact_type": artifact_type,
        "created_at": created_at,
        "metadata": metadata or {},
        "extension": suffix,
    }


def _attachment_task_id(structured: dict[str, Any]) -> str | None:
    for parent, child in (("task_status", "task_id"), ("office_productivity", "task_id")):
        value = structured.get(parent)
        if isinstance(value, dict) and value.get(child):
            return str(value[child])
    return None


def _attachment_scene(structured: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    route = structured.get("route_semantics")
    if isinstance(route, dict) and "office" in str(route.get("route") or ""):
        return "office_document" if any(_is_primary_attachment(item) for item in candidates) else "office_text"
    if isinstance(structured.get("office_productivity"), dict):
        return "office_document" if any(_is_primary_attachment(item) for item in candidates) else "office_text"
    if _reply_mentions_generated_document(_attachment_completed_summary(structured)):
        return "office_document" if any(_is_primary_attachment(item) for item in candidates) else "office_text"
    return "generic"


def _looks_like_attachment_request(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "发我文件",
        "把文件发我",
        "发我附件",
        "附件发来",
        "导出",
        "文件",
        "word",
        "excel",
        "pdf",
        "txt",
        "send me the file",
        "send the file",
        "send me the attachment",
        "export",
        "attachment",
    )
    return any(marker in lowered for marker in markers)


def _reply_mentions_generated_document(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "已产出文件",
        "已整理成文档",
        "已生成",
        "已输出结果文件",
        "文件已生成",
        "draft is ready",
        "document is ready",
        "file is ready",
    )
    return any(marker in lowered for marker in markers)


def _attachment_completed_summary(structured: dict[str, Any]) -> str:
    for key in ("action_status", "action_status_semantics"):
        value = structured.get(key)
        if isinstance(value, dict):
            return str(value.get("completed_summary") or "")
    return ""


def _sort_attachment_candidates(
    candidates: list[dict[str, Any]],
    *,
    user_text: str,
    scene: str,
) -> list[dict[str, Any]]:
    format_bonus = _requested_format_bonus(user_text)
    return sorted(
        candidates,
        key=lambda item: (
            format_bonus.get(str(item.get("extension") or ""), 0),
            _primary_rank(item),
            _role_rank(str(item.get("delivery_role") or "")),
            1 if scene == "office_document" and _is_primary_attachment(item) else 0,
            str(item.get("created_at") or ""),
            str(item.get("display_name") or ""),
        ),
        reverse=True,
    )


def _requested_format_bonus(text: str) -> dict[str, int]:
    lowered = text.lower()
    return {
        ".docx": 5 if "word" in lowered or "docx" in lowered else 0,
        ".xlsx": 5 if "excel" in lowered or "xlsx" in lowered else 0,
        ".pdf": 5 if "pdf" in lowered else 0,
        ".txt": 5 if "txt" in lowered else 0,
        ".md": 4 if "markdown" in lowered or "md" in lowered else 0,
    }


def _primary_rank(item: dict[str, Any]) -> int:
    return 2 if _is_primary_attachment(item) else 1


def _is_primary_attachment(item: dict[str, Any]) -> bool:
    suffix = str(item.get("extension") or "").lower()
    return suffix in {".docx", ".xlsx", ".pptx", ".pdf"}


def _role_rank(role: str) -> int:
    return {"primary": 3, "document": 2, "supplement": 1}.get(role, 0)


def _attachment_delivery_role(display_name: str, *, suffix: str) -> str:
    lowered = display_name.lower()
    if suffix in {".docx", ".xlsx", ".pptx", ".pdf"}:
        return "primary"
    if "summary" in lowered or "摘要" in lowered:
        return "document"
    return "supplement"
