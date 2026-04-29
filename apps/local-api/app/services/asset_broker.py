from __future__ import annotations

from datetime import timedelta
from typing import Any

from core_types import (
    AssetCategory,
    AssetHandleDetail,
    ErrorCode,
    RiskLevel,
    TraceSpanStatus,
    TraceSpanType,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now, utc_now_iso
from app.db.repositories.asset_repo import AssetRepository
from app.schemas.assets import (
    AssetHandleValidateRequest,
    AssetHandleValidateResponse,
    AssetQueryApiResponse,
    AssetQueryRequest,
    AssetResolveForToolRequest,
    AssetResolveForToolResponse,
)
from app.services.audit import AuditEventService
from app.services.capability import CapabilityGraphService, capability_request


class AssetBrokerService:
    def __init__(
        self,
        *,
        repo: AssetRepository,
        capability: CapabilityGraphService,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._capability = capability
        self._trace = trace_service
        self._audit = audit_service

    async def query(
        self,
        request: AssetQueryRequest,
        *,
        trace_id: str | None = None,
        raise_on_denied: bool = True,
    ) -> AssetQueryApiResponse:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.ASSET_QUERY,
            "query assets through broker",
            input_data=request.model_dump(mode="json"),
        )
        try:
            assets = await self._candidate_assets(request)
            handles: list[AssetHandleDetail] = []
            denied_assets: list[str] = []
            for asset in assets:
                handle = await self._issue_handle_for_asset(asset, request, trace_id=trace_id)
                if handle is None:
                    denied_assets.append(asset["asset_id"])
                else:
                    handles.append(handle)
            if not handles and denied_assets and raise_on_denied:
                raise AppError(
                    ErrorCode.ASSET_ACCESS_DENIED,
                    "当前主体没有可用资产授权",
                    status_code=403,
                    details={"asset_ids": denied_assets},
                )
            await self._end_span(
                span_id,
                output_data={
                    "handle_count": len(handles),
                    "denied_count": len(denied_assets),
                },
            )
            return AssetQueryApiResponse(handles=handles)
        except Exception as exc:
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.ASSET_ACCESS_DENIED)},
            )
            raise

    async def validate_handle(
        self,
        handle_id: str,
        request: AssetHandleValidateRequest,
        *,
        trace_id: str | None = None,
    ) -> AssetHandleValidateResponse:
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.ASSET_HANDLE_VALIDATE,
            "validate asset handle",
            metadata={"handle_id": handle_id, "action": request.action},
        )
        try:
            handle = await self._repo.get_handle(handle_id)
            if handle is None:
                raise AppError(ErrorCode.ASSET_HANDLE_INVALID, "资产句柄不存在", status_code=404)
            now = utc_now_iso()
            if handle["status"] != "active":
                raise AppError(
                    ErrorCode.ASSET_HANDLE_INVALID,
                    "资产句柄不可用",
                    status_code=400,
                    details={"status": handle["status"]},
                )
            if str(handle["expires_at"]) <= now:
                await self._repo.update_handle_status(handle_id, status="expired")
                await self._insert_event(
                    handle,
                    event_type="expired",
                    reason="ttl_expired",
                    trace_id=trace_id,
                )
                raise AppError(ErrorCode.ASSET_HANDLE_EXPIRED, "资产句柄已过期", status_code=400)
            subject_mismatch = (
                handle["subject_type"] != request.subject_type
                or handle["subject_id"] != request.subject_id
            )
            if subject_mismatch:
                raise AppError(
                    ErrorCode.ASSET_HANDLE_INVALID,
                    "资产句柄主体不匹配",
                    status_code=403,
                )
            if (
                handle.get("conversation_id")
                and handle.get("conversation_id") != request.conversation_id
            ):
                raise AppError(
                    ErrorCode.ASSET_HANDLE_INVALID,
                    "资产句柄会话上下文不匹配",
                    status_code=403,
                )
            if handle.get("task_id") and handle.get("task_id") != request.task_id:
                raise AppError(
                    ErrorCode.ASSET_HANDLE_INVALID,
                    "资产句柄任务上下文不匹配",
                    status_code=403,
                )
            action_requires_approval = request.action in handle["approval_required_actions"]
            if request.action not in handle["allowed_actions"] and not action_requires_approval:
                raise AppError(
                    ErrorCode.ASSET_HANDLE_INVALID,
                    "资产句柄不允许该动作",
                    status_code=403,
                )
            decision = await self._capability.decide(
                capability_request(
                    subject_type=request.subject_type,
                    subject_id=request.subject_id,
                    object_type="asset",
                    object_id=handle["asset_id"],
                    action=request.action,
                    context={
                        "conversation_id": request.conversation_id,
                        "task_id": request.task_id,
                        "handle_id": handle_id,
                    },
                ),
                trace_id=trace_id,
            )
            if not decision.allowed:
                await self._repo.update_handle_status(
                    handle_id,
                    status="revoked",
                    revoked_at=utc_now_iso(),
                )
                await self._insert_event(
                    handle,
                    event_type="revoked",
                    reason="capability_denied_at_validation",
                    trace_id=trace_id,
                )
                await self._audit.write_event(
                    actor_type="system",
                    action="asset.handle.revoked",
                    object_type="asset_handle",
                    object_id=handle_id,
                    summary="资产句柄授权校验失败并撤销",
                    risk_level=decision.risk_level,
                    payload={
                        "handle_id": handle_id,
                        "asset_id": handle["asset_id"],
                        "action": request.action,
                        "reason": decision.reason,
                    },
                    trace_id=trace_id,
                )
                raise AppError(
                    ErrorCode.ASSET_HANDLE_INVALID,
                    "资产句柄授权已失效",
                    status_code=403,
                )
            if action_requires_approval or decision.approval_required:
                raise AppError(
                    ErrorCode.APPROVAL_REQUIRED,
                    "该动作需要确认后才能执行",
                    status_code=409,
                )
            await self._insert_event(
                handle,
                event_type="validated",
                reason=request.action,
                trace_id=trace_id,
            )
            await self._end_span(span_id, output_data={"allowed": True})
            return AssetHandleValidateResponse(
                handle=AssetHandleDetail(**_handle_detail_data(handle)),
                allowed=True,
                action=request.action,
            )
        except Exception:
            await self._end_span(span_id, status=TraceSpanStatus.FAILED)
            raise

    async def revoke_handle(
        self,
        handle_id: str,
        *,
        trace_id: str | None = None,
        reason: str = "manual_revoke",
    ) -> AssetHandleDetail:
        handle = await self._repo.get_handle(handle_id)
        if handle is None:
            raise AppError(ErrorCode.ASSET_HANDLE_INVALID, "资产句柄不存在", status_code=404)
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.ASSET_HANDLE_REVOKE,
            "revoke asset handle",
            metadata={"handle_id": handle_id},
        )
        await self._repo.update_handle_status(
            handle_id,
            status="revoked",
            revoked_at=utc_now_iso(),
        )
        await self._insert_event(handle, event_type="revoked", reason=reason, trace_id=trace_id)
        await self._audit.write_event(
            actor_type="system",
            action="asset.handle.revoked",
            object_type="asset_handle",
            object_id=handle_id,
            summary="资产句柄已撤销",
            risk_level=RiskLevel.R1,
            payload={"handle_id": handle_id, "reason": reason},
            trace_id=trace_id,
        )
        await self._end_span(span_id, output_data={"handle_id": handle_id})
        updated = await self._repo.get_handle(handle_id)
        if updated is None:
            raise AppError(ErrorCode.ASSET_HANDLE_INVALID, "资产句柄不存在", status_code=404)
        return AssetHandleDetail(**_handle_detail_data(updated))

    async def resolve_for_tool(
        self,
        handle_id: str,
        request: AssetResolveForToolRequest,
        *,
        trace_id: str | None = None,
    ) -> AssetResolveForToolResponse:
        await self.validate_handle(
            handle_id,
            AssetHandleValidateRequest(
                subject_type="member",
                subject_id=request.subject_id,
                action=request.action,
                conversation_id=request.conversation_id,
                task_id=request.task_id,
            ),
            trace_id=trace_id,
        )
        handle = await self._repo.get_handle(handle_id)
        if handle is None:
            raise AppError(ErrorCode.ASSET_HANDLE_INVALID, "资产句柄不存在", status_code=404)
        asset = await self._repo.get_asset(handle["asset_id"])
        if asset is None or asset["status"] != "active":
            raise AppError(ErrorCode.ASSET_DISABLED, "资产不可用", status_code=403)
        resource = {
            "display_name": asset["display_name"],
            "provider": asset.get("provider"),
            "config": _minimal_config(asset, request.action),
            "sensitivity": asset.get("sensitivity"),
            "capabilities": asset.get("capabilities", []),
        }
        await self._insert_event(
            handle,
            event_type="resolved_for_tool",
            reason=f"{request.tool_name}:{request.action}",
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="member",
            actor_id=request.subject_id,
            action="asset.handle.resolved_for_tool",
            object_type="asset_handle",
            object_id=handle_id,
            summary="资产句柄已为工具解析最小资源",
            risk_level=RiskLevel(handle.get("risk_level", "R1")),
            payload={
                "handle_id": handle_id,
                "asset_id": handle["asset_id"],
                "tool_name": request.tool_name,
                "action": request.action,
                "has_secret": bool(asset.get("secret_ref")),
            },
            trace_id=trace_id,
        )
        return AssetResolveForToolResponse(
            handle_id=handle_id,
            asset_id=handle["asset_id"],
            asset_type=handle["asset_type"],
            action=request.action,
            tool_name=request.tool_name,
            member_id=request.subject_id,
            task_id=request.task_id,
            summary=handle["summary"],
            allowed_actions=handle["allowed_actions"],
            approval_required_actions=handle["approval_required_actions"],
            resource=redact(resource),
            has_secret=bool(asset.get("secret_ref")),
            expires_at=handle["expires_at"],
        )

    async def list_handle_events(self, handle_id: str) -> list[dict[str, Any]]:
        if await self._repo.get_handle(handle_id) is None:
            raise AppError(ErrorCode.ASSET_HANDLE_INVALID, "资产句柄不存在", status_code=404)
        return await self._repo.list_handle_events(handle_id)

    async def _candidate_assets(self, request: AssetQueryRequest) -> list[dict[str, Any]]:
        rows = await self._repo.list_assets(
            organization_id="org_default",
            asset_type=request.asset_type.value if request.asset_type else None,
            status="active",
            limit=100,
        )
        if not request.keywords:
            return rows
        keywords = [keyword.lower() for keyword in request.keywords]
        return [
            row
            for row in rows
            if any(
                keyword in str(row.get("display_name", "")).lower()
                or keyword in str(row.get("summary_text", "")).lower()
                for keyword in keywords
            )
        ]

    async def _issue_handle_for_asset(
        self,
        asset: dict[str, Any],
        request: AssetQueryRequest,
        *,
        trace_id: str | None,
    ) -> AssetHandleDetail | None:
        actions = request.requested_actions or ["read"]
        allowed: list[str] = []
        blocked: list[str] = []
        approval_required: list[str] = []
        policy_sources: list[str] = []
        max_risk = RiskLevel.R0
        for action in actions:
            decision = await self._capability.decide(
                capability_request(
                    subject_type=request.subject_type,
                    subject_id=request.subject_id,
                    object_type="asset",
                    object_id=asset["asset_id"],
                    action=action,
                    context={
                        **request.context,
                        "conversation_id": request.conversation_id,
                        "task_id": request.task_id,
                    },
                ),
                trace_id=trace_id,
            )
            policy_sources.extend(decision.policy_sources)
            max_risk = _max_risk(max_risk, decision.risk_level)
            if not decision.allowed:
                blocked.append(action)
            elif decision.approval_required:
                approval_required.append(action)
            else:
                allowed.append(action)
        if not allowed and not approval_required:
            await self._record_denied(asset, request, blocked=blocked, trace_id=trace_id)
            return None
        reusable = await self._find_reusable_handle(
            asset_id=asset["asset_id"],
            request=request,
            allowed_actions=allowed,
            approval_required_actions=approval_required,
            trace_id=trace_id,
        )
        if reusable is not None:
            return reusable
        handle_id = new_id("ah")
        issued_at = utc_now_iso()
        expires_at = (utc_now() + _ttl_for(asset, [*allowed, *approval_required])).isoformat()
        data = {
            "handle_id": handle_id,
            "organization_id": asset["organization_id"],
            "asset_id": asset["asset_id"],
            "asset_type": asset["asset_type"],
            "subject_type": request.subject_type,
            "subject_id": request.subject_id,
            "conversation_id": request.conversation_id,
            "task_id": request.task_id,
            "allowed_actions": allowed,
            "blocked_actions": blocked,
            "approval_required_actions": approval_required,
            "risk_level": max_risk.value,
            "summary_text": _asset_handle_summary(asset),
            "summary": _asset_handle_summary(asset),
            "policy_sources": sorted(set(policy_sources)),
            "status": "active",
            "issued_at": issued_at,
            "expires_at": expires_at,
            "revoked_at": None,
            "trace_id": trace_id,
        }
        span_id = await self._start_span(
            trace_id,
            TraceSpanType.ASSET_HANDLE_ISSUE,
            "issue asset handle",
            metadata={"asset_id": asset["asset_id"], "handle_id": handle_id},
        )
        await self._repo.insert_handle(data)
        await self._insert_event(data, event_type="issued", reason=None, trace_id=trace_id)
        await self._audit.write_event(
            actor_type="system",
            action="asset.handle.issued",
            object_type="asset_handle",
            object_id=handle_id,
            summary="资产句柄已发放",
            risk_level=max_risk,
            payload={
                "handle_id": handle_id,
                "asset_id": asset["asset_id"],
                "allowed_actions": allowed,
                "approval_required_actions": approval_required,
                "blocked_actions": blocked,
            },
            trace_id=trace_id,
        )
        await self._end_span(span_id, output_data={"handle_id": handle_id})
        return AssetHandleDetail(**_handle_detail_data(data))

    async def _find_reusable_handle(
        self,
        *,
        asset_id: str,
        request: AssetQueryRequest,
        allowed_actions: list[str],
        approval_required_actions: list[str],
        trace_id: str | None,
    ) -> AssetHandleDetail | None:
        now = utc_now_iso()
        handles = await self._repo.list_active_handles(
            asset_id=asset_id,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            limit=50,
        )
        for handle in handles:
            if str(handle["expires_at"]) <= now:
                await self._repo.update_handle_status(handle["handle_id"], status="expired")
                await self._insert_event(
                    handle,
                    event_type="expired",
                    reason="ttl_expired",
                    trace_id=trace_id,
                )
                continue
            if handle.get("conversation_id") != request.conversation_id:
                continue
            if handle.get("task_id") != request.task_id:
                continue
            if not set(allowed_actions).issubset(set(handle["allowed_actions"])):
                continue
            if not set(approval_required_actions).issubset(
                set(handle["approval_required_actions"])
            ):
                continue
            await self._insert_event(
                handle,
                event_type="reused",
                reason="matching_active_handle",
                trace_id=trace_id,
            )
            return AssetHandleDetail(**_handle_detail_data(handle))
        return None

    async def _record_denied(
        self,
        asset: dict[str, Any],
        request: AssetQueryRequest,
        *,
        blocked: list[str],
        trace_id: str | None,
    ) -> None:
        handle_id = new_id("ah_denied")
        await self._repo.insert_handle(
            {
                "handle_id": handle_id,
                "organization_id": asset["organization_id"],
                "asset_id": asset["asset_id"],
                "subject_type": request.subject_type,
                "subject_id": request.subject_id,
                "conversation_id": request.conversation_id,
                "task_id": request.task_id,
                "allowed_actions": [],
                "blocked_actions": blocked,
                "approval_required_actions": [],
                "risk_level": asset.get("risk_level", "R1"),
                "summary_text": _asset_handle_summary(asset),
                "policy_sources": [],
                "status": "denied",
                "issued_at": utc_now_iso(),
                "expires_at": utc_now_iso(),
                "trace_id": trace_id,
            }
        )
        await self._repo.insert_handle_event(
            {
                "event_id": new_id("ahe"),
                "organization_id": asset["organization_id"],
                "handle_id": handle_id,
                "event_type": "denied",
                "reason": "capability_denied",
                "actor_type": request.subject_type,
                "actor_id": request.subject_id,
                "trace_id": trace_id,
                "metadata": {"blocked_actions": blocked},
                "created_at": utc_now_iso(),
            }
        )

    async def _insert_event(
        self,
        handle: dict[str, Any],
        *,
        event_type: str,
        reason: str | None,
        trace_id: str | None,
    ) -> None:
        await self._repo.insert_handle_event(
            {
                "event_id": new_id("ahe"),
                "organization_id": handle["organization_id"],
                "handle_id": handle["handle_id"],
                "event_type": event_type,
                "reason": reason,
                "actor_type": handle.get("subject_type"),
                "actor_id": handle.get("subject_id"),
                "trace_id": trace_id,
                "metadata": {},
                "created_at": utc_now_iso(),
            }
        )

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
    ) -> None:
        if span_id is not None:
            await self._trace.end_span(
                span_id,
                status=status,
                output_data=redact(output_data or {}),
            )


def _asset_handle_summary(asset: dict[str, Any]) -> str:
    return asset.get("summary_text") or f"{asset['display_name']} ({asset['asset_type']})"


def _minimal_config(asset: dict[str, Any], action: str) -> dict[str, Any]:
    config = dict(asset.get("config", {}))
    allowed_keys = {
        "platform",
        "username",
        "auth_type",
        "network",
        "address",
        "source_type",
        "root_uri",
        "provider",
        "device_type",
        "model_name",
    }
    if action in {"read_knowledge", "index_knowledge"}:
        allowed_keys |= {"source_uri"}
    return {key: value for key, value in config.items() if key in allowed_keys}


def _ttl_for(asset: dict[str, Any], actions: list[str]) -> timedelta:
    asset_type = asset["asset_type"]
    if asset_type == AssetCategory.WALLET.value:
        return timedelta(minutes=5)
    if any(action in {"publish_post", "sign_transaction", "delete_content"} for action in actions):
        return timedelta(minutes=10)
    if any(action.startswith("task:") for action in actions):
        return timedelta(hours=2)
    return timedelta(minutes=30)


def _max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    order = {risk.value: index for index, risk in enumerate(RiskLevel)}
    return left if order[left.value] >= order[right.value] else right


def _handle_detail_data(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key != "summary_text"}
