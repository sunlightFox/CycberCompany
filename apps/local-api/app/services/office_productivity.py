from __future__ import annotations

from typing import Any

from core_types import (
    ApprovalState,
    ArtifactEvidence,
    CalendarAction,
    DeckOutline,
    Deliverable,
    DocumentChangeSet,
    DocumentSuiteProvider,
    FinalResult,
    MailDraft,
    OfficeTaskRequest,
    ProviderCapabilityProfile,
    SheetUpdateSummary,
    TaskArtifact,
)
from trace_service import redact

from app.services.chat_intent_router import OfficeChatRequest, parse_office_chat_request

LOCAL_OFFICE_PROVIDER = DocumentSuiteProvider(
    provider_ref="local.office_suite",
    display_name="Local Office Suite",
    supported_objects=["document", "spreadsheet", "deck", "mail", "calendar"],
    supported_actions=[
        "generate",
        "edit",
        "draft",
        "plan",
        "send",
        "share",
        "delete",
        "overwrite",
        "modify_shared",
    ],
    risk_actions=[
        "send_mail",
        "share_document",
        "overwrite_source",
        "delete_document",
        "modify_shared_file",
    ],
    collaboration_features=["draft_artifact", "approval_gate", "typed_evidence"],
    collaboration_modes=["single_user", "approval_gate", "artifact_handoff"],
)


def office_request_from_task_fields(
    *,
    goal: str,
    domain: str | None = None,
    domain_request: dict[str, Any] | None = None,
    office_request: OfficeTaskRequest | dict[str, Any] | None,
    constraints: dict[str, Any] | None,
) -> OfficeTaskRequest | None:
    if domain == "productivity" and isinstance(domain_request, dict):
        normalized_domain_request = dict(domain_request)
        if normalized_domain_request.get("request_type"):
            return OfficeTaskRequest(**normalized_domain_request)
    if isinstance(office_request, OfficeTaskRequest):
        return office_request
    if isinstance(office_request, dict) and office_request.get("request_type"):
        return OfficeTaskRequest(**office_request)
    constraint_request = dict((constraints or {}).get("office_request") or {})
    if constraint_request.get("request_type"):
        return OfficeTaskRequest(**constraint_request)
    parsed = parse_office_chat_request(goal)
    if parsed is None:
        return None
    return office_request_from_chat_request(parsed, goal=goal)


def office_request_from_chat_request(
    request: OfficeChatRequest,
    *,
    goal: str,
) -> OfficeTaskRequest:
    request_type = {
        "word": "document",
        "excel": "spreadsheet",
        "ppt": "deck",
    }.get(request.document_type, "document")
    return OfficeTaskRequest(
        request_type=request_type,
        operation=request.operation,
        title=request.topic or goal[:80],
        summary=request.content or goal[:240],
        content=request.content or goal,
        source_artifact_id=request.edit_target_artifact_id,
        metadata={
            "document_type": request.document_type,
            "slide_count": request.requested_pages_or_sheets if request.document_type == "ppt" else None,
            "sheet_count": request.requested_pages_or_sheets if request.document_type == "excel" else None,
            "summary": request.content,
        },
    )


def office_profile_for_task_request(
    *,
    goal: str,
    domain: str | None = None,
    domain_request: dict[str, Any] | None = None,
    office_request: OfficeTaskRequest | dict[str, Any] | None,
    constraints: dict[str, Any] | None,
) -> dict[str, Any]:
    resolved = office_request_from_task_fields(
        goal=goal,
        domain=domain,
        domain_request=domain_request,
        office_request=office_request,
        constraints=constraints,
    )
    if resolved is None:
        return {"enabled": False}
    high_risk_actions = list(resolved.high_risk_actions)
    operation = str(resolved.operation or "generate")
    request_type = str(resolved.request_type or "document")
    if operation == "send":
        high_risk_actions.append("send_mail")
    if operation == "share":
        high_risk_actions.append("share_document")
    if operation == "delete":
        high_risk_actions.append("delete_document")
    if operation == "overwrite":
        high_risk_actions.append("overwrite_source")
    if operation == "modify_shared":
        high_risk_actions.append("modify_shared_file")
    if bool((constraints or {}).get("overwrite_source")):
        high_risk_actions.append("overwrite_source")
    if bool((constraints or {}).get("modify_shared_file")):
        high_risk_actions.append("modify_shared_file")
    high_risk_actions = sorted({item for item in high_risk_actions if item})
    tool_name = office_tool_name_for_request_type(request_type, operation)
    risk_level = "R2"
    if "delete_document" in high_risk_actions:
        risk_level = "R5"
    elif high_risk_actions:
        risk_level = "R4"
    return {
        "enabled": True,
        "domain": "productivity",
        "request": resolved.model_dump(mode="json"),
        "request_type": request_type,
        "object_type": request_type,
        "operation": operation,
        "provider_ref": resolved.provider_ref or LOCAL_OFFICE_PROVIDER.provider_ref,
        "high_risk_actions": high_risk_actions,
        "tool_name": tool_name,
        "risk_level": risk_level,
        "provider_capability_profile": provider_capability_profile_for_office_request(
            request=resolved
        ).model_dump(mode="json"),
    }


def office_tool_name_for_request_type(request_type: str, operation: str) -> str | None:
    mapping = {
        ("mail", "draft"): "office.mail.draft",
        ("mail", "send"): "office.mail.send",
        ("calendar", "plan"): "office.calendar.plan",
        ("calendar", "draft"): "office.calendar.plan",
        ("calendar", "schedule"): "office.calendar.plan",
        ("document", "share"): "office.document.share",
        ("document", "delete"): "office.document.delete",
        ("document", "overwrite"): "office.document.overwrite",
        ("document", "modify_shared"): "office.document.modify_shared",
        ("spreadsheet", "share"): "office.document.share",
        ("spreadsheet", "delete"): "office.document.delete",
        ("spreadsheet", "overwrite"): "office.document.overwrite",
        ("spreadsheet", "modify_shared"): "office.document.modify_shared",
        ("deck", "share"): "office.document.share",
        ("deck", "delete"): "office.document.delete",
        ("deck", "overwrite"): "office.document.overwrite",
        ("deck", "modify_shared"): "office.document.modify_shared",
    }
    return mapping.get((request_type, operation))


def office_tool_args(profile: dict[str, Any]) -> dict[str, Any]:
    request = dict(profile.get("request") or {})
    args = {
        "domain": profile.get("domain") or "productivity",
        "provider_ref": profile.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref,
        "request_type": profile.get("request_type"),
        "operation": profile.get("operation"),
        "title": request.get("title"),
        "summary": request.get("summary"),
        "content": request.get("content"),
        "recipients": list(request.get("recipients") or []),
        "attendees": list(request.get("attendees") or []),
        "share_targets": list(request.get("share_targets") or []),
        "scheduled_time": request.get("scheduled_time"),
        "source_artifact_id": request.get("source_artifact_id"),
        "artifact_refs": list(request.get("artifact_refs") or []),
        "high_risk_actions": list(profile.get("high_risk_actions") or []),
        "provider_capability_profile": dict(
            profile.get("provider_capability_profile") or {}
        ),
        "metadata": dict(request.get("metadata") or {}),
    }
    return {key: value for key, value in args.items() if value not in (None, [], {}, "")}


def provider_capability_profile_for_office_request(
    *,
    request: OfficeTaskRequest | dict[str, Any] | None = None,
) -> ProviderCapabilityProfile:
    request_payload = (
        request.model_dump(mode="json")
        if isinstance(request, OfficeTaskRequest)
        else dict(request or {})
    )
    supported_objects = list(LOCAL_OFFICE_PROVIDER.supported_objects)
    request_type = str(request_payload.get("request_type") or "").strip()
    if request_type and request_type not in supported_objects:
        supported_objects.append(request_type)
    supported_actions = list(LOCAL_OFFICE_PROVIDER.supported_actions)
    operation = str(request_payload.get("operation") or "").strip()
    if operation and operation not in supported_actions:
        supported_actions.append(operation)
    risk_actions = sorted(
        {
            *list(LOCAL_OFFICE_PROVIDER.risk_actions),
            *[
                str(item)
                for item in list(request_payload.get("high_risk_actions") or [])
                if str(item).strip()
            ],
        }
    )
    return ProviderCapabilityProfile(
        provider_ref=str(
            request_payload.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref
        ),
        provider_type=LOCAL_OFFICE_PROVIDER.provider_type,
        domain="productivity",
        display_name=LOCAL_OFFICE_PROVIDER.display_name,
        supported_objects=supported_objects,
        supported_actions=supported_actions,
        risk_actions=risk_actions,
        collaboration_features=list(LOCAL_OFFICE_PROVIDER.collaboration_features),
        collaboration_modes=list(LOCAL_OFFICE_PROVIDER.collaboration_modes),
        authorization_required=bool(request_payload.get("provider_ref")),
        metadata={"default_provider": LOCAL_OFFICE_PROVIDER.provider_ref},
    )


def office_artifact_refs(artifacts: list[TaskArtifact]) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": artifact.artifact_id,
            "display_name": artifact.display_name,
            "artifact_type": artifact.artifact_type,
            "content_type": artifact.content_type,
        }
        for artifact in artifacts
    ]


def office_final_result(
    *,
    task_id: str,
    trace_id: str | None,
    task_status: str,
    profile: dict[str, Any],
    steps: list[dict[str, Any]],
    artifacts: list[TaskArtifact],
    approvals: list[dict[str, Any]],
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    request = dict(profile.get("request") or {})
    provider_ref = str(profile.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref)
    provider_capability_profile = dict(
        profile.get("provider_capability_profile")
        or provider_capability_profile_for_office_request(request=request).model_dump(mode="json")
    )
    approval_state = _office_approval_state(profile=profile, task_status=task_status, approvals=approvals)
    artifact_refs = office_artifact_refs(artifacts)
    evidence = _office_artifact_evidence(profile=profile, request=request, artifacts=artifacts)
    deliverable = Deliverable(
        deliverable_type=str(profile.get("request_type") or "office"),
        title=str(request.get("title") or _office_title_from_artifacts(artifacts, profile)),
        summary=_office_summary(profile=profile, request=request, artifacts=artifacts, task_status=task_status),
        provider_ref=provider_ref,
        task_id=task_id,
        trace_id=trace_id,
        source="office_productivity_phase100",
        artifact_refs=artifact_refs,
        evidence_refs=artifact_refs,
        approval_state=approval_state,
        metadata={
            "domain": profile.get("domain") or "productivity",
            "operation": profile.get("operation"),
            "high_risk_actions": list(profile.get("high_risk_actions") or []),
            "step_count": len(steps),
        },
    )
    final_result = FinalResult(
        summary=deliverable.summary,
        source="office_productivity_phase100",
        provider_ref=provider_ref,
        task_id=task_id,
        trace_id=trace_id,
        artifact_refs=artifact_refs,
        approval_state=approval_state,
        deliverable=deliverable,
        artifact_evidence=evidence,
        next_actions=_office_next_actions(task_status, approval_state.status, profile),
        metadata={
            "domain": profile.get("domain") or "productivity",
            "request_type": profile.get("request_type"),
            "operation": profile.get("operation"),
            "high_risk_actions": list(profile.get("high_risk_actions") or []),
            "provider_type": LOCAL_OFFICE_PROVIDER.provider_type,
        },
    )
    typed_payload = _typed_office_payload(profile=profile, request=request, artifacts=artifacts)
    result = {
        **raw_result,
        "domain": profile.get("domain") or "productivity",
        "domain_request": request,
        "request_type": profile.get("request_type"),
        "provider_capability_profile": provider_capability_profile,
        "summary": final_result.summary,
        "source": final_result.source,
        "provider_ref": provider_ref,
        "status": _office_result_status(task_status, approval_state.status),
        "approval_state": approval_state.model_dump(mode="json"),
        "deliverable": final_result.deliverable.model_dump(mode="json") if final_result.deliverable else {},
        "artifact_evidence": final_result.artifact_evidence.model_dump(mode="json") if final_result.artifact_evidence else {},
        "final_result": final_result.model_dump(mode="json"),
        "office_productivity": {
            "request": request,
            "request_type": profile.get("request_type"),
            "operation": profile.get("operation"),
            "high_risk_actions": list(profile.get("high_risk_actions") or []),
            "typed_output": typed_payload,
        },
    }
    if task_status != "completed":
        result["deliverable"]["metadata"]["task_status"] = task_status
    return result


def _office_approval_state(
    *,
    profile: dict[str, Any],
    task_status: str,
    approvals: list[dict[str, Any]],
) -> ApprovalState:
    artifact_refs = []
    if task_status == "waiting_approval":
        pending = next((item for item in reversed(approvals) if item.get("status") == "pending"), None)
        return ApprovalState(
            status="required",
            source="office_productivity_phase100",
            provider_ref=str(profile.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref),
            approval_id=pending.get("approval_id") if pending else None,
            task_id=pending.get("task_id") if pending else None,
            trace_id=pending.get("trace_id") if pending else None,
            requested_action=_requested_action(profile),
            risk_level=str(profile.get("risk_level") or "R4"),
            artifact_refs=artifact_refs,
            reason_codes=["high_risk_office_action"],
        )
    latest = approvals[-1] if approvals else None
    if latest is None:
        return ApprovalState(
            status="not_required",
            source="office_productivity_phase100",
            provider_ref=str(profile.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref),
            artifact_refs=artifact_refs,
        )
    if latest.get("status") in {"approved", "edited"}:
        return ApprovalState(
            status="approved",
            source="office_productivity_phase100",
            provider_ref=str(profile.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref),
            approval_id=latest.get("approval_id"),
            task_id=latest.get("task_id"),
            trace_id=latest.get("trace_id"),
            requested_action=_requested_action(profile),
            risk_level=str(profile.get("risk_level") or "R4"),
            artifact_refs=artifact_refs,
        )
    if latest.get("status") == "denied":
        return ApprovalState(
            status="denied",
            source="office_productivity_phase100",
            provider_ref=str(profile.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref),
            approval_id=latest.get("approval_id"),
            task_id=latest.get("task_id"),
            trace_id=latest.get("trace_id"),
            requested_action=_requested_action(profile),
            risk_level=str(profile.get("risk_level") or "R4"),
            artifact_refs=artifact_refs,
        )
    return ApprovalState(
        status="not_required",
        source="office_productivity_phase100",
        provider_ref=str(profile.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref),
        artifact_refs=artifact_refs,
    )


def _office_artifact_evidence(
    *,
    profile: dict[str, Any],
    request: dict[str, Any],
    artifacts: list[TaskArtifact],
) -> ArtifactEvidence:
    return ArtifactEvidence(
        evidence_type=str(profile.get("request_type") or "office"),
        summary=_office_summary(profile=profile, request=request, artifacts=artifacts, task_status="completed"),
        source="office_productivity_phase100",
        provider_ref=str(profile.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref),
        task_id=str(request.get("task_id") or "") or None,
        trace_id=str(request.get("trace_id") or "") or None,
        artifact_refs=office_artifact_refs(artifacts),
        metadata={
            "domain": profile.get("domain") or "productivity",
            "operation": profile.get("operation"),
        },
    )


def _office_title_from_artifacts(artifacts: list[TaskArtifact], profile: dict[str, Any]) -> str:
    if artifacts:
        return str(artifacts[-1].display_name or "office-deliverable")
    return f"{profile.get('request_type') or 'office'}-{profile.get('operation') or 'task'}"


def _office_summary(
    *,
    profile: dict[str, Any],
    request: dict[str, Any],
    artifacts: list[TaskArtifact],
    task_status: str,
) -> str:
    request_type = str(profile.get("request_type") or "office")
    operation = str(profile.get("operation") or "generate")
    title = str(request.get("title") or _office_title_from_artifacts(artifacts, profile))
    if task_status == "waiting_approval":
        return f"{title} 已准备好，但 `{_requested_action(profile)}` 仍在等待审批。"
    if task_status != "completed":
        return f"{title} 当前状态为 {task_status}，交付尚未完成。"
    if request_type == "mail" and operation == "draft":
        return f"邮件草稿已生成：{title}。"
    if request_type == "mail" and operation == "send":
        return f"邮件发送动作已通过受控流程记录：{title}。"
    if request_type == "calendar":
        return f"日程安排草案已生成：{title}。"
    if operation == "edit":
        return f"{title} 已完成修订，并生成新的可交付产物。"
    return f"{title} 已生成，并附带统一办公证据。"


def _office_next_actions(task_status: str, approval_status: str, profile: dict[str, Any]) -> list[str]:
    if task_status == "waiting_approval" or approval_status == "required":
        return ["审批通过后继续执行", "如需修改内容，可使用审批编辑载荷"]
    if str(profile.get("operation") or "") == "edit":
        return ["可继续追加修改", "可将新版本作为后续输入源"]
    return ["可继续发起修订", "可基于当前交付物继续生成后续办公结果"]


def _office_result_status(task_status: str, approval_status: str) -> str:
    if approval_status == "denied":
        return "blocked"
    if task_status == "waiting_approval" or approval_status == "required":
        return "waiting_input"
    if task_status == "failed":
        return "failed"
    if task_status == "completed":
        return "completed"
    return task_status or "completed"


def _requested_action(profile: dict[str, Any]) -> str:
    actions = list(profile.get("high_risk_actions") or [])
    if actions:
        return actions[0]
    return f"office.{profile.get('request_type')}.{profile.get('operation')}"


def _typed_office_payload(
    *,
    profile: dict[str, Any],
    request: dict[str, Any],
    artifacts: list[TaskArtifact],
) -> dict[str, Any]:
    artifact_refs = office_artifact_refs(artifacts)
    request_type = str(profile.get("request_type") or "office")
    operation = str(profile.get("operation") or "generate")
    if request_type == "document":
        return {
            "document_change_set": DocumentChangeSet(
                operation=operation,
                summary=_office_summary(profile=profile, request=request, artifacts=artifacts, task_status="completed"),
                section_titles=_section_titles(request),
                artifact_refs=artifact_refs,
                source_artifact_id=request.get("source_artifact_id"),
            ).model_dump(mode="json")
        }
    if request_type == "spreadsheet":
        return {
            "sheet_update_summary": SheetUpdateSummary(
                operation=operation,
                summary=_office_summary(profile=profile, request=request, artifacts=artifacts, task_status="completed"),
                sheet_names=_sheet_names(request),
                artifact_refs=artifact_refs,
                source_artifact_id=request.get("source_artifact_id"),
            ).model_dump(mode="json")
        }
    if request_type == "deck":
        return {
            "deck_outline": DeckOutline(
                operation=operation,
                summary=_office_summary(profile=profile, request=request, artifacts=artifacts, task_status="completed"),
                slide_titles=_slide_titles(request),
                artifact_refs=artifact_refs,
                source_artifact_id=request.get("source_artifact_id"),
            ).model_dump(mode="json")
        }
    if request_type == "mail":
        return {
            "mail_draft": MailDraft(
                operation=operation,
                subject=str(request.get("title") or "mail-draft"),
                summary=_office_summary(profile=profile, request=request, artifacts=artifacts, task_status="completed"),
                recipients=list(request.get("recipients") or []),
                artifact_refs=artifact_refs,
            ).model_dump(mode="json")
        }
    if request_type == "calendar":
        return {
            "calendar_action": CalendarAction(
                operation=operation,
                title=str(request.get("title") or "calendar-action"),
                summary=_office_summary(profile=profile, request=request, artifacts=artifacts, task_status="completed"),
                attendees=list(request.get("attendees") or []),
                scheduled_time=request.get("scheduled_time"),
                artifact_refs=artifact_refs,
            ).model_dump(mode="json")
        }
    return {}


def _section_titles(request: dict[str, Any]) -> list[str]:
    sections = list(request.get("metadata", {}).get("sections") or request.get("sections") or [])
    titles: list[str] = []
    for section in sections:
        if isinstance(section, dict):
            title = str(section.get("title") or "").strip()
            if title:
                titles.append(title)
    return titles


def _sheet_names(request: dict[str, Any]) -> list[str]:
    sheets = list(request.get("metadata", {}).get("sheets") or request.get("sheets") or [])
    names: list[str] = []
    for sheet in sheets:
        if isinstance(sheet, dict):
            name = str(sheet.get("name") or sheet.get("title") or "").strip()
            if name:
                names.append(name)
    return names


def _slide_titles(request: dict[str, Any]) -> list[str]:
    slides = list(request.get("metadata", {}).get("slides") or request.get("slides") or [])
    titles: list[str] = []
    for slide in slides:
        if isinstance(slide, dict):
            title = str(slide.get("title") or "").strip()
            if title:
                titles.append(title)
    return titles


def office_governance_result(
    *,
    tool_name: str,
    args: dict[str, Any],
    artifact: TaskArtifact | None,
) -> dict[str, Any]:
    summary = str(redact(args.get("summary") or args.get("title") or tool_name))[:240]
    provider_capability_profile = dict(
        args.get("provider_capability_profile")
        or provider_capability_profile_for_office_request(
            request={
                "provider_ref": args.get("provider_ref"),
                "request_type": args.get("request_type"),
                "operation": args.get("operation"),
                "high_risk_actions": list(args.get("high_risk_actions") or []),
            }
        ).model_dump(mode="json")
    )
    result = {
        "domain": str(args.get("domain") or "productivity"),
        "provider_ref": str(args.get("provider_ref") or LOCAL_OFFICE_PROVIDER.provider_ref),
        "provider_capability_profile": provider_capability_profile,
        "requested_action": tool_name,
        "status": "completed",
        "summary": summary,
    }
    if artifact is not None:
        result["artifact_id"] = artifact.artifact_id
        result["artifact_ref"] = {
            "artifact_id": artifact.artifact_id,
            "display_name": artifact.display_name,
            "artifact_type": artifact.artifact_type,
            "content_type": artifact.content_type,
        }
    return result
