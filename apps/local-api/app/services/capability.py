from __future__ import annotations

import hashlib
import json
from typing import Any

from core_types import (
    CapabilityDecision,
    CapabilityEdge,
    CapabilityObject,
    CapabilityRequest,
    CapabilitySubject,
    ErrorCode,
    RiskLevel,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.asset_repo import AssetRepository
from app.db.repositories.member_repo import MemberRepository
from app.schemas.assets import CapabilityGrantCreateRequest, CapabilityGrantUpdateRequest
from app.services.audit import AuditEventService


class CapabilityGraphService:
    def __init__(
        self,
        *,
        repo: AssetRepository,
        member_repo: MemberRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._members = member_repo
        self._trace = trace_service
        self._audit = audit_service

    async def create_grant(
        self,
        request: CapabilityGrantCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> CapabilityEdge:
        if request.source_type == "task_temporary_grant" and not request.valid_to:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "临时 task grant 必须提供 valid_to",
                status_code=422,
            )
        organization_id = await self._organization_for_subject(
            request.subject_type,
            request.subject_id,
            request.object_id,
        )
        edge_id = new_id("edge")
        now = utc_now_iso()
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.CAPABILITY_EDGE_CREATE,
            "create capability edge",
            metadata={"edge_id": edge_id, "action": request.action},
        )
        data = {
            "edge_id": edge_id,
            "organization_id": organization_id,
            **request.model_dump(mode="json"),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_capability_edge(data)
        await self._end_span(span_id, output_data={"edge_id": edge_id})
        await self._audit.write_event(
            actor_type="system",
            action="capability.edge.created",
            object_type="capability_edge",
            object_id=edge_id,
            summary="授权边已创建",
            risk_level=request.risk_level,
            payload={"edge_id": edge_id, "effect": request.effect, "action": request.action},
            trace_id=trace_id,
        )
        if request.effect in {"deny", "approval_required"}:
            await self._revoke_handles_for_edge(
                data,
                reason="capability_created",
                trace_id=trace_id,
            )
        edge = await self._repo.get_capability_edge(edge_id)
        if edge is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "授权创建后无法读取", status_code=500)
        return CapabilityEdge(**edge)

    async def list_grants(
        self,
        *,
        subject_type: str | None = None,
        subject_id: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        limit: int = 100,
    ) -> list[CapabilityEdge]:
        rows = await self._repo.list_capability_edges(
            subject_type=subject_type,
            subject_id=subject_id,
            object_type=object_type,
            object_id=object_id,
            include_inactive=True,
            limit=limit,
        )
        return [CapabilityEdge(**row) for row in rows]

    async def update_grant(
        self,
        edge_id: str,
        request: CapabilityGrantUpdateRequest,
        *,
        trace_id: str | None = None,
    ) -> CapabilityEdge:
        existing = await self._repo.get_capability_edge(edge_id)
        if existing is None:
            raise AppError(ErrorCode.NOT_FOUND, "授权不存在", status_code=404)
        fields = request.model_dump(exclude_unset=True, mode="json")
        fields["updated_at"] = utc_now_iso()
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.CAPABILITY_EDGE_UPDATE,
            "update capability edge",
            metadata={"edge_id": edge_id},
        )
        await self._repo.update_capability_edge(edge_id, fields)
        await self._end_span(span_id, output_data={"changed_fields": sorted(fields)})
        await self._audit.write_event(
            actor_type="system",
            action="capability.edge.updated",
            object_type="capability_edge",
            object_id=edge_id,
            summary="授权边已更新",
            risk_level=RiskLevel.R2,
            payload={"edge_id": edge_id, "changed_fields": sorted(fields)},
            trace_id=trace_id,
        )
        if _grant_change_requires_revoke(fields):
            await self._revoke_handles_for_edge(
                existing,
                reason="capability_updated",
                trace_id=trace_id,
            )
        updated = await self._repo.get_capability_edge(edge_id)
        if updated is None:
            raise AppError(ErrorCode.NOT_FOUND, "授权不存在", status_code=404)
        return CapabilityEdge(**updated)

    async def delete_grant(self, edge_id: str, *, trace_id: str | None = None) -> CapabilityEdge:
        existing = await self._repo.get_capability_edge(edge_id)
        if existing is None:
            raise AppError(ErrorCode.NOT_FOUND, "授权不存在", status_code=404)
        updated = await self.update_grant(
            edge_id,
            CapabilityGrantUpdateRequest(status="deleted"),
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="capability.edge.deleted",
            object_type="capability_edge",
            object_id=edge_id,
            summary="授权边已删除",
            risk_level=RiskLevel.R2,
            payload={"edge_id": edge_id},
            trace_id=trace_id,
        )
        return updated

    async def decide(
        self,
        request: CapabilityRequest,
        *,
        trace_id: str | None = None,
    ) -> CapabilityDecision:
        organization_id = await self._organization_for_subject(
            request.subject.subject_type,
            request.subject.subject_id,
            request.object.object_id,
        )
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.CAPABILITY_DECISION,
            "decide capability",
            input_data=request.model_dump(mode="json"),
        )
        try:
            decision = await self._decide_inner(
                organization_id=organization_id,
                request=request,
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                output_data={
                    "decision_id": decision.decision_id,
                    "allowed": decision.allowed,
                    "approval_required": decision.approval_required,
                    "reason": decision.reason,
                },
            )
            return decision
        except Exception as exc:
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.CAPABILITY_DENIED.value},
                error_code=ErrorCode.CAPABILITY_DENIED.value,
            )
            if isinstance(exc, AppError):
                raise
            raise

    async def _decide_inner(
        self,
        *,
        organization_id: str,
        request: CapabilityRequest,
        trace_id: str | None,
    ) -> CapabilityDecision:
        asset = await self._repo.get_asset(request.object.object_id)
        if asset is not None and asset["status"] in {"disabled", "archived", "deleted"}:
            return await self._record_decision(
                organization_id=organization_id,
                request=request,
                trace_id=trace_id,
                decision="deny",
                risk_level=RiskLevel(asset.get("risk_level", RiskLevel.R2.value)),
                approval_required=False,
                reason=f"asset_{asset['status']}",
                policy_sources=[f"asset:{asset['asset_id']}:status"],
            )

        subject_keys = await self._subject_keys(request.subject, organization_id)
        edges: list[dict[str, Any]] = []
        for subject_type, subject_id in subject_keys:
            edges.extend(
                await self._repo.list_capability_edges(
                    organization_id=organization_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    object_type=request.object.object_type,
                    object_id=request.object.object_id,
                    action=request.action,
                    include_inactive=False,
                    limit=200,
                )
            )
        policies = (
            await self._repo.list_policies(asset_id=request.object.object_id, action=request.action)
            if asset is not None
            else []
        )
        policy_candidates = _policy_candidates(policies)
        edge_candidates = _edge_candidates(edges)
        active_policies = [
            item for item in policy_candidates if _condition_allows(item.get("condition", {}))
        ]
        active_edges = [
            item for item in edge_candidates if _condition_allows(item.get("condition", {}))
        ]
        active = [*active_policies, *active_edges]
        if not active:
            return await self._record_decision(
                organization_id=organization_id,
                request=request,
                trace_id=trace_id,
                decision="deny",
                risk_level=RiskLevel.R1,
                approval_required=False,
                reason="no_matching_grant",
                policy_sources=[],
            )
        deny = [item for item in active if item["effect"] == "deny"]
        if deny:
            return await self._record_decision(
                organization_id=organization_id,
                request=request,
                trace_id=trace_id,
                decision="deny",
                risk_level=_max_risk(deny),
                approval_required=False,
                reason="deny_policy_matched",
                policy_sources=[item["source"] for item in deny],
            )
        approval = [
            item
            for item in active
            if item["effect"] == "approval_required" and item["source"].startswith("edge:")
        ]
        allow = [item for item in active_edges if item["effect"] == "allow"]
        policy_approval = [
            item for item in active_policies if item["effect"] == "approval_required"
        ]
        if approval or (allow and policy_approval):
            approval_sources = [*approval, *policy_approval]
            return await self._record_decision(
                organization_id=organization_id,
                request=request,
                trace_id=trace_id,
                decision="approval_required",
                risk_level=_max_risk(approval_sources),
                approval_required=True,
                reason="approval_policy_matched",
                policy_sources=[item["source"] for item in approval_sources],
            )
        if allow:
            return await self._record_decision(
                organization_id=organization_id,
                request=request,
                trace_id=trace_id,
                decision="allow",
                risk_level=_max_risk(allow),
                approval_required=False,
                reason="allow_policy_matched",
                policy_sources=[item["source"] for item in allow],
            )
        return await self._record_decision(
            organization_id=organization_id,
            request=request,
            trace_id=trace_id,
            decision="deny",
            risk_level=RiskLevel.R1,
            approval_required=False,
            reason="no_allow_policy",
            policy_sources=[],
        )

    async def _record_decision(
        self,
        *,
        organization_id: str,
        request: CapabilityRequest,
        trace_id: str | None,
        decision: str,
        risk_level: RiskLevel,
        approval_required: bool,
        reason: str,
        policy_sources: list[str],
    ) -> CapabilityDecision:
        decision_id = new_id("capdec")
        allowed = decision in {"allow", "approval_required"}
        now = utc_now_iso()
        await self._repo.insert_decision_log(
            {
                "decision_id": decision_id,
                "organization_id": organization_id,
                "trace_id": trace_id,
                "subject_type": request.subject.subject_type,
                "subject_id": request.subject.subject_id,
                "object_type": request.object.object_type,
                "object_id": request.object.object_id,
                "action": request.action,
                "context_hash": _hash_context(request.context),
                "decision": decision,
                "risk_level": risk_level.value,
                "approval_required": approval_required,
                "reason": reason,
                "policy_sources": policy_sources,
                "created_at": now,
            }
        )
        return CapabilityDecision(
            decision_id=decision_id,
            allowed=allowed,
            risk_level=risk_level,
            approval_required=approval_required,
            reason=reason,
            policy_sources=policy_sources,
            blocked_actions=[] if allowed else [request.action],
        )

    async def _revoke_handles_for_edge(
        self,
        edge: dict[str, Any],
        *,
        reason: str,
        trace_id: str | None,
    ) -> None:
        if edge.get("object_type") != "asset":
            return
        asset_id = str(edge["object_id"])
        action = str(edge.get("action") or "*")
        subject_type = str(edge.get("subject_type") or "")
        subject_id = str(edge.get("subject_id") or "")
        subject_filter_type = subject_type if subject_type == "member" else None
        subject_filter_id = subject_id if subject_type == "member" else None
        handles = await self._repo.list_active_handles(
            asset_id=asset_id,
            subject_type=subject_filter_type,
            subject_id=subject_filter_id,
            limit=500,
        )
        affected = [
            handle for handle in handles if _handle_includes_action(handle, action)
        ]
        if not affected:
            return
        now = utc_now_iso()
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.ASSET_HANDLE_REVOKE,
            "revoke handles after capability change",
            metadata={
                "edge_id": edge.get("edge_id"),
                "asset_id": asset_id,
                "action": action,
                "handle_count": len(affected),
            },
        )
        for handle in affected:
            await self._repo.update_handle_status(
                handle["handle_id"],
                status="revoked",
                revoked_at=now,
            )
            await self._repo.insert_handle_event(
                {
                    "event_id": new_id("ahe"),
                    "organization_id": handle["organization_id"],
                    "handle_id": handle["handle_id"],
                    "event_type": "revoked",
                    "reason": reason,
                    "actor_type": "system",
                    "actor_id": None,
                    "trace_id": trace_id,
                    "metadata": {
                        "edge_id": edge.get("edge_id"),
                        "asset_id": asset_id,
                        "action": action,
                    },
                    "created_at": now,
                }
            )
        await self._audit.write_event(
            actor_type="system",
            action="asset.handle.revoked",
            object_type="capability_edge",
            object_id=str(edge.get("edge_id")),
            summary="授权变更已撤销受影响资产句柄",
            risk_level=RiskLevel.R2,
            payload={
                "edge_id": edge.get("edge_id"),
                "asset_id": asset_id,
                "action": action,
                "handle_ids": [handle["handle_id"] for handle in affected],
                "reason": reason,
            },
            trace_id=trace_id,
        )
        await self._end_span(span_id, output_data={"revoked_count": len(affected)})

    async def _organization_for_subject(
        self,
        subject_type: str,
        subject_id: str,
        fallback_asset_id: str | None = None,
    ) -> str:
        if subject_type == "member":
            member = await self._members.get_member(subject_id)
            if member is not None:
                return str(member["organization_id"])
        if fallback_asset_id:
            asset = await self._repo.get_asset(fallback_asset_id)
            if asset is not None:
                return str(asset["organization_id"])
        return "org_default"

    async def _subject_keys(
        self,
        subject: CapabilitySubject,
        organization_id: str,
    ) -> list[tuple[str, str]]:
        keys = [(subject.subject_type, subject.subject_id), ("organization", organization_id)]
        if subject.subject_type == "member":
            member = await self._members.get_member(subject.subject_id)
            if member:
                if member.get("department_id"):
                    keys.append(("department", str(member["department_id"])))
                if member.get("role_id"):
                    keys.append(("role", str(member["role_id"])))
        return keys

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: TraceSpanType,
        name: str,
        *,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=name,
            input_data=input_data,
            metadata=metadata,
        )

    async def _end_span(
        self,
        span_id: str | None,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                status=status,
                output_data=redact(output_data or {}),
                error_code=error_code,
            )


def _edge_candidates(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "effect": edge["effect"],
            "risk_level": edge["risk_level"],
            "condition": {**edge.get("condition", {}), "valid_to": edge.get("valid_to")},
            "source": f"edge:{edge['edge_id']}",
        }
        for edge in edges
    ]


def _policy_candidates(policies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "effect": policy["effect"],
            "risk_level": policy["risk_level"],
            "condition": policy.get("condition", {}),
            "source": f"asset_policy:{policy['policy_id']}",
        }
        for policy in policies
    ]


def _condition_allows(condition: dict[str, Any]) -> bool:
    now = utc_now_iso()
    valid_to = condition.get("valid_to")
    if isinstance(valid_to, str) and valid_to <= now:
        return False
    return True


def _grant_change_requires_revoke(fields: dict[str, Any]) -> bool:
    changed = set(fields) - {"updated_at"}
    revocation_fields = {
        "effect",
        "risk_level",
        "approval_policy",
        "approval_policy_json",
        "condition",
        "condition_json",
        "priority",
        "status",
        "valid_from",
        "valid_to",
    }
    return bool(changed & revocation_fields)


def _handle_includes_action(handle: dict[str, Any], action: str) -> bool:
    if action == "*":
        return True
    actions = set(handle.get("allowed_actions", []))
    actions.update(handle.get("approval_required_actions", []))
    return action in actions


def _max_risk(items: list[dict[str, Any]]) -> RiskLevel:
    order = {risk.value: index for index, risk in enumerate(RiskLevel)}
    value = max((item["risk_level"] for item in items), key=lambda risk: order.get(risk, 0))
    return RiskLevel(value)


def _hash_context(context: dict[str, Any]) -> str:
    raw = json.dumps(redact(context), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def capability_request(
    *,
    subject_type: str,
    subject_id: str,
    object_type: str,
    object_id: str,
    action: str,
    context: dict[str, Any] | None = None,
) -> CapabilityRequest:
    return CapabilityRequest(
        subject=CapabilitySubject(subject_type=subject_type, subject_id=subject_id),
        object=CapabilityObject(object_type=object_type, object_id=object_id),
        action=action,
        context=context or {},
    )
