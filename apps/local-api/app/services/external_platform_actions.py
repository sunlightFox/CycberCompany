from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core_types import (
    AccountAssetCandidate,
    ApprovalDetail,
    AssetCategory,
    ErrorCode,
    ExternalPlatformActionIntent,
    ExternalPlatformActionPlan,
    ExternalPlatformExecution,
    ExternalPlatformPlanEvent,
    ExternalPlatformTarget,
    RiskLevel,
    TaskMode,
    TraceSpanStatus,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.asset_repo import AssetRepository
from app.db.repositories.external_platform_adapter_repo import ExternalPlatformAdapterRepository
from app.db.repositories.external_platform_repo import ExternalPlatformRepository
from app.schemas.assets import AssetHandleValidateRequest, AssetQueryRequest
from app.schemas.assets import AssetCreateRequest, CapabilityGrantCreateRequest
from app.schemas.browser import BrowserProfileCreateRequest, BrowserSessionCreateRequest
from app.schemas.external_platform import (
    ExternalPlatformAccountCandidatesRequest,
    ExternalPlatformAccountCandidatesResponse,
    ExternalPlatformActionPlanCreateRequest,
    ExternalPlatformActionPlanResponse,
    ExternalPlatformIntentResolveRequest,
    ExternalPlatformIntentResolveResponse,
    ExternalPlatformPlanClarifyRequest,
    ExternalPlatformPlanExecuteRequest,
    ExternalPlatformTargetCreateRequest,
)
from app.schemas.skill_governance import SkillGrantCreateRequest
from app.schemas.skills import BundleInstallRequest
from app.schemas.tasks import TaskCreateRequest
from app.services.approvals import ApprovalService
from app.services.asset import AssetService
from app.services.asset_broker import AssetBrokerService
from app.services.artifacts import ArtifactStore
from app.services.audit import AuditEventService
from app.services.browser_sessions import BrowserSessionService
from app.services.capability import CapabilityGraphService
from app.services.external_platform_providers import (
    FAKE_PROVIDER_TARGET,
    XIAOHONGSHU_BROWSER_TARGET,
    ExternalPlatformProviderRegistry,
    ProviderExecutionRequest,
    ProviderInfo,
    default_external_platform_provider_registry,
)
from app.services.safety_policy import RuntimeSafetyPolicyService
from app.services.skill_governance import SkillGovernanceService
from app.services.skill_plugin import SkillPluginService
from app.services.skill_repositories import SkillRepositoryService
from app.services.tasks import TaskEngine

ACTION_MARKERS = {
    "comment_content": ["评论", "留言", "回复", "comment", "reply"],
    "publish_content": [
        "发布",
        "发一篇文章",
        "发文章",
        "发动态",
        "发帖",
        "发到",
        "同步公告",
        "publish",
        "post",
    ],
    "send_message": ["发消息", "私信", "发送", "send message", "message"],
    "read_status": ["查看", "读取", "查询状态", "read", "status"],
}

CONTENT_MARKERS = [
    "内容：",
    "内容:",
    "正文：",
    "正文:",
    "这段内容：",
    "这段内容:",
    "文章：",
    "文章:",
]

HIGH_RISK_ACTIONS = {
    "publish_content",
    "comment_content",
    "send_message",
    "edit_content",
    "delete_content",
}


class ExternalPlatformActionService:
    def __init__(
        self,
        *,
        repo: ExternalPlatformRepository,
        adapter_repo: ExternalPlatformAdapterRepository,
        asset_repo: AssetRepository,
        asset_service: AssetService,
        asset_broker: AssetBrokerService,
        capability_service: CapabilityGraphService,
        browser_session_service: BrowserSessionService,
        artifact_store: ArtifactStore,
        task_engine: TaskEngine,
        approval_service: ApprovalService,
        trace_service: TraceService,
        audit_service: AuditEventService,
        provider_registry: ExternalPlatformProviderRegistry | None = None,
        safety_policy_service: RuntimeSafetyPolicyService | None = None,
        skill_plugin_service: SkillPluginService | None = None,
        skill_governance_service: SkillGovernanceService | None = None,
        skill_repository_service: SkillRepositoryService | None = None,
    ) -> None:
        self._repo = repo
        self._adapter_repo = adapter_repo
        self._asset_repo = asset_repo
        self._assets = asset_service
        self._asset_broker = asset_broker
        self._capability = capability_service
        self._browser_sessions = browser_session_service
        self._artifacts = artifact_store
        self._tasks = task_engine
        self._approvals = approval_service
        self._trace = trace_service
        self._audit = audit_service
        self._providers = provider_registry or default_external_platform_provider_registry()
        self._safety_policy = safety_policy_service
        self._skills = skill_plugin_service
        self._skill_governance = skill_governance_service
        self._skill_repositories = skill_repository_service

    async def ensure_seeded_targets(self, *, trace_id: str | None = None) -> None:
        now = utc_now_iso()
        for target_seed, target_id in (
            (FAKE_PROVIDER_TARGET, "ept_fake_platform"),
            (XIAOHONGSHU_BROWSER_TARGET, "ept_social_xiaohongshu"),
        ):
            existing = await self._repo.get_target_by_key(str(target_seed["platform_key"]))
            if existing is not None:
                continue
            target = {
                **target_seed,
                "target_id": target_id,
                "organization_id": "org_default",
                "status": "active",
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
            await self._repo.upsert_target(target)

    def list_providers(self) -> list[ProviderInfo]:
        return self._providers.list()

    async def create_target(
        self,
        request: ExternalPlatformTargetCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformTarget:
        _reject_inline_secret_config(request.metadata)
        now = utc_now_iso()
        data = {
            **request.model_dump(mode="json"),
            "target_id": new_id("ept"),
            "organization_id": "org_default",
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.upsert_target(data)
        await self._audit.write_event(
            actor_type="system",
            action="external_platform.target.upserted",
            object_type="external_platform_target",
            object_id=data["target_id"],
            summary="外部平台 target 已配置",
            risk_level=RiskLevel.R1,
            payload={"platform_key": request.platform_key, "status": request.status},
            trace_id=trace_id,
        )
        target = await self._repo.get_target_by_key(request.platform_key)
        if target is None:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "外部平台 target 创建后无法读取",
                status_code=500,
            )
        return ExternalPlatformTarget(**target)

    async def list_targets(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ExternalPlatformTarget]:
        await self.ensure_seeded_targets()
        return [
            ExternalPlatformTarget(**row)
            for row in await self._repo.list_targets(status=status, limit=limit)
        ]

    async def resolve_intent(
        self,
        request: ExternalPlatformIntentResolveRequest,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformIntentResolveResponse:
        await self.ensure_seeded_targets(trace_id=trace_id)
        span_id = await self._start_span(
            trace_id,
            "external_platform.intent.resolve",
            input_data={"text": str(redact(request.text)), "member_id": request.member_id},
        )
        try:
            targets = await self._repo.list_targets(
                organization_id=request.organization_id,
                status="active",
            )
            match = _match_target(request.text, targets)
            action_type, action_score = _detect_action_type(request.text)
            content = _extract_content(request.text, action_type)
            redaction_summary = _redaction_summary(request.text)
            missing_fields: list[str] = []
            if match is None:
                missing_fields.append("platform")
            if action_type == "unknown":
                missing_fields.append("action_type")
            if action_type in {"publish_content", "comment_content", "send_message"} and not content:
                missing_fields.append("content")
            confidence = round(
                0.25
                + (0.35 if match else 0)
                + action_score
                + (0.15 if content else 0),
                2,
            )
            status = (
                "resolved"
                if not missing_fields and confidence >= 0.75
                else "clarification_needed"
            )
            now = utc_now_iso()
            intent_id = new_id("epai")
            platform_key = str(match["platform_key"]) if match else None
            platform_hint = str(match["matched_alias"]) if match else None
            constraints = {
                **request.constraints,
                "requires_external_state_change": action_type in HIGH_RISK_ACTIONS,
                "redaction": redaction_summary,
                "sensitive_content_detected": redaction_summary["redaction_count"] > 0,
            }
            data = {
                "intent_id": intent_id,
                "organization_id": request.organization_id,
                "member_id": request.member_id,
                "conversation_id": request.conversation_id,
                "turn_id": request.turn_id,
                "trace_id": trace_id,
                "platform_hint": platform_hint,
                "platform_key": platform_key,
                "action_type": action_type,
                "content_redacted": str(redact(content or request.text)),
                "content_summary": _content_summary(content or request.text),
                "target_hint": _target_hint(request.text),
                "constraints": constraints,
                "confidence": confidence,
                "status": status,
                "missing_fields": missing_fields,
                "resolver_evidence": {
                    "target_match": redact(match or {}),
                    "action_score": action_score,
                    "platform_from_target_alias": bool(match),
                    "missing_fields": missing_fields,
                    "content_hash": _stable_hash(content or request.text),
                    "redaction_summary": redaction_summary,
                },
                "created_at": now,
                "updated_at": now,
            }
            await self._repo.insert_intent(data)
            await self._audit.write_event(
                actor_type="member",
                actor_id=request.member_id,
                action="external_platform.intent.resolved",
                object_type="external_platform_action_intent",
                object_id=intent_id,
                summary="外部平台动作意图已解析",
                risk_level=RiskLevel.R2,
                payload={
                    "intent_id": intent_id,
                    "platform_key": platform_key,
                    "action_type": action_type,
                    "status": status,
                    "missing_fields": missing_fields,
                },
                trace_id=trace_id,
            )
            await self._end_span(
                span_id,
                output_data={
                    "intent_id": intent_id,
                    "status": status,
                    "platform_key": platform_key,
                    "action_type": action_type,
                },
            )
            intent = ExternalPlatformActionIntent(**data)
            return ExternalPlatformIntentResolveResponse(
                intent=intent,
                message=_intent_message(intent),
                next_step=(
                    "create_action_plan"
                    if intent.status == "resolved"
                    else "ask_user_for_missing_fields"
                ),
            )
        except Exception as exc:
            await self._end_span(
                span_id,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": getattr(exc, "code", ErrorCode.INTERNAL_ERROR.value)},
            )
            raise

    async def account_candidates(
        self,
        request: ExternalPlatformAccountCandidatesRequest,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformAccountCandidatesResponse:
        intent = await self._maybe_get_intent(request.intent_id)
        platform_key = request.platform_key or (intent.platform_key if intent else None)
        action_type = request.action_type or (intent.action_type if intent else None)
        member_id = request.member_id or (intent.member_id if intent else "mem_xiaoyao")
        conversation_id = request.conversation_id or (intent.conversation_id if intent else None)
        if not platform_key:
            return ExternalPlatformAccountCandidatesResponse(
                intent_id=request.intent_id,
                status="missing_platform",
                message="还缺少平台信息，暂时不能查找账号资产。",
                recovery_options=["先配置 platform target", "补充平台名称或别名"],
            )
        if not action_type:
            return ExternalPlatformAccountCandidatesResponse(
                intent_id=request.intent_id,
                platform_key=platform_key,
                status="missing_action_type",
                message="还缺少动作类型，暂时不能查找账号资产。",
                recovery_options=["说明要发布、发送还是只读查询"],
            )
        candidates = await self._account_candidates(
            platform_key=platform_key,
            action_type=action_type,
            member_id=member_id,
            conversation_id=conversation_id,
            keywords=request.keywords,
            trace_id=trace_id,
        )
        status = (
            "no_account"
            if not candidates
            else "single_candidate"
            if len(candidates) == 1
            else "multiple_candidates"
        )
        message = {
            "no_account": "没有找到可用于该平台和动作的账号资产。",
            "single_candidate": "找到 1 个可用账号，会在提交前继续要求确认。",
            "multiple_candidates": "找到多个可用账号，需要先选择一个。",
        }[status]
        return ExternalPlatformAccountCandidatesResponse(
            intent_id=request.intent_id,
            platform_key=platform_key,
            action_type=action_type,
            candidates=candidates,
            status=status,
            message=message,
            recovery_options=(
                ["创建 account 资产并授予该成员对应能力", "换用已授权账号"]
                if status == "no_account"
                else []
            ),
        )

    async def create_plan(
        self,
        request: ExternalPlatformActionPlanCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformActionPlanResponse:
        intent = await self._get_intent(request.intent_id)
        request_metadata = {**request.metadata, **_plan_create_metadata(request)}
        if intent.status != "resolved":
            plan = await self._insert_plan_for_intent(
                intent,
                status="awaiting_intent_clarification",
                execution_mode=request.execution_mode,
                trace_id=trace_id,
                failure_reason="intent_missing_fields",
                evidence={"missing_fields": intent.missing_fields},
                metadata=request_metadata,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="这个外部平台动作还缺少关键信息，不能创建执行计划。",
                next_step="ask_user_for_missing_fields",
            )
        if intent.constraints.get("sensitive_content_detected"):
            plan = await self._insert_plan_for_intent(
                intent,
                status="blocked",
                execution_mode=request.execution_mode,
                trace_id=trace_id,
                failure_reason="sensitive_content_blocked",
                evidence={
                    "blocked_by": "redaction_policy",
                    "redaction": intent.constraints.get("redaction", {}),
                },
                metadata=request_metadata,
            )
            await self._plan_event(
                plan.plan_id,
                "plan.blocked",
                {"reason": "sensitive_content_blocked"},
                trace_id=trace_id,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="内容里包含疑似敏感凭据，已阻断外部平台动作。",
                next_step="remove_sensitive_content_and_retry",
            )
        target = await self._target_for_intent(intent)
        if target is None:
            plan = await self._insert_plan_for_intent(
                intent,
                status="awaiting_target",
                execution_mode=request.execution_mode,
                trace_id=trace_id,
                failure_reason="target_not_found",
                metadata=request_metadata,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="没有找到匹配的平台 target。",
                next_step="configure_platform_target",
            )
        if intent.action_type not in target.supported_actions:
            plan = await self._insert_plan_for_intent(
                intent,
                status="failed",
                execution_mode=request.execution_mode,
                trace_id=trace_id,
                failure_reason="unsupported_action",
                evidence={"supported_actions": target.supported_actions},
                metadata=request_metadata,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="这个平台 target 暂不支持该动作。",
                next_step="choose_supported_action",
            )
        phase99_validation = _phase99_real_xiaohongshu_validation(
            intent=intent,
            execution_mode=request.execution_mode,
            metadata=request_metadata,
        )
        if phase99_validation is not None:
            plan = await self._insert_plan_for_intent(
                intent,
                status="awaiting_clarification",
                execution_mode=request.execution_mode,
                trace_id=trace_id,
                failure_reason=phase99_validation["failure_reason"],
                evidence={
                    "missing_fields": phase99_validation["missing_fields"],
                    "phase99_real_xiaohongshu_required": True,
                },
                metadata=request_metadata,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message=phase99_validation["message"],
                next_step="ask_user_for_missing_fields",
            )
        candidates = await self._account_candidates(
            platform_key=str(intent.platform_key),
            action_type=intent.action_type,
            member_id=request.member_id or intent.member_id,
            conversation_id=request.conversation_id or intent.conversation_id,
            trace_id=trace_id,
        )
        if not candidates:
            plan = await self._insert_plan_for_intent(
                intent,
                status="awaiting_account",
                execution_mode=request.execution_mode,
                trace_id=trace_id,
                failure_reason="no_account_asset_candidate",
                evidence={"account_candidates": []},
                metadata=request_metadata,
            )
            await self._plan_event(
                plan.plan_id,
                "plan.awaiting_account",
                {"platform_key": intent.platform_key, "action_type": intent.action_type},
                trace_id=trace_id,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="没有找到可用账号资产，因此不会声称已登录或已发布。",
                next_step="configure_account_asset_or_grant_permission",
            )
        selected = _select_candidate(
            candidates,
            selected_asset_id=request.selected_asset_id,
            selected_handle_id=request.selected_handle_id,
        )
        if selected is None and len(candidates) > 1:
            plan = await self._insert_plan_for_intent(
                intent,
                status="awaiting_clarification",
                execution_mode=request.execution_mode,
                trace_id=trace_id,
                evidence={
                    "account_candidates": [
                        item.model_dump(mode="json") for item in candidates
                    ]
                },
                metadata=request_metadata,
            )
            await self._plan_event(
                plan.plan_id,
                "plan.awaiting_clarification",
                {
                    "candidate_count": len(candidates),
                    "candidate_asset_ids": [c.asset_id for c in candidates],
                },
                trace_id=trace_id,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="找到多个账号候选，需要你先选择一个账号。",
                next_step="clarify_account_candidate",
            )
        selected = selected or candidates[0]
        plan = await self._insert_plan_for_intent(
            intent,
            status="draft",
            execution_mode=request.execution_mode,
            trace_id=trace_id,
            target_id=target.target_id,
            selected=selected,
            risk_level=_risk_for_action(target, intent.action_type),
            evidence={"account_candidates": [item.model_dump(mode="json") for item in candidates]},
            metadata=request_metadata,
        )
        return await self._bind_selected_account(
            plan.plan_id,
            selected,
            target=target,
            trace_id=trace_id,
        )

    async def get_plan(self, plan_id: str) -> ExternalPlatformActionPlanResponse:
        return await self._response_for_plan(
            plan_id,
            message="外部平台动作计划已读取。",
            next_step=None,
        )

    async def clarify_plan(
        self,
        plan_id: str,
        request: ExternalPlatformPlanClarifyRequest,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformActionPlanResponse:
        plan = await self._get_plan(plan_id)
        intent = await self._get_intent(plan.intent_id)
        target = await self._target_for_intent(intent)
        if target is None:
            raise AppError(ErrorCode.NOT_FOUND, "平台 target 不存在", status_code=404)
        candidates = await self._account_candidates(
            platform_key=str(intent.platform_key),
            action_type=intent.action_type,
            member_id=intent.member_id,
            conversation_id=intent.conversation_id,
            trace_id=trace_id,
        )
        selected = _select_candidate(
            candidates,
            selected_asset_id=request.selected_asset_id,
            selected_handle_id=request.selected_handle_id,
            selected_display_name=request.selected_display_name,
            text=request.text,
        )
        if selected is None:
            await self._plan_event(
                plan.plan_id,
                "clarification.unmatched",
                {"candidate_count": len(candidates), "text": request.text},
                trace_id=trace_id,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="还不能唯一确定账号，请用账号显示名或序号再说明一次。",
                next_step="clarify_account_candidate",
            )
        await self._repo.update_plan(
            plan.plan_id,
            {
                "selected_asset_id": selected.asset_id,
                "selected_handle_id": selected.handle_id,
                "status": "draft",
                "risk_level": _risk_for_action(target, intent.action_type),
                "evidence": {
                    **plan.evidence,
                    "account_candidates": [item.model_dump(mode="json") for item in candidates],
                    "selected_candidate": selected.model_dump(mode="json"),
                    "clarification": {
                        "superseded_previous_asset_id": plan.selected_asset_id,
                        "source": "user_clarification",
                    },
                },
                "updated_at": utc_now_iso(),
            },
        )
        await self._plan_event(
            plan.plan_id,
            "clarification.applied",
            {
                "selected_asset_id": selected.asset_id,
                "superseded_previous_asset_id": plan.selected_asset_id,
            },
            trace_id=trace_id,
        )
        return await self._bind_selected_account(
            plan.plan_id,
            selected,
            target=target,
            trace_id=trace_id,
        )

    async def execute_plan(
        self,
        plan_id: str,
        request: ExternalPlatformPlanExecuteRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformActionPlanResponse:
        request = request or ExternalPlatformPlanExecuteRequest()
        plan = await self._get_plan(plan_id)
        if plan.status == "completed":
            return await self._response_for_plan(
                plan_id,
                message="这个外部平台动作计划已经完成。",
                next_step=None,
            )
        if plan.status in {"awaiting_account", "awaiting_clarification", "awaiting_target"}:
            return await self._response_for_plan(
                plan_id,
                message="这个计划还缺少账号或平台信息，不能执行。",
                next_step=plan.status,
            )
        if plan.status == "blocked":
            return await self._response_for_plan(
                plan_id,
                message="这个计划被安全策略阻断，不能执行。",
                next_step="remove_sensitive_content_and_retry",
            )
        if plan.status == "cancelled":
            return await self._response_for_plan(
                plan_id,
                message="这个计划已经取消，不能继续执行旧的 pending action。",
                next_step=None,
            )
        if _risk_order(plan.risk_level) >= 3 and plan.approval_id:
            approval = await self._approvals.get(plan.approval_id)
            if approval.status == "pending" and not request.force:
                return await self._response_for_plan(
                    plan_id,
                    message="真正提交前仍在等待审批，不会自动发布。",
                    next_step="approve_or_deny_pending_action",
                )
            if approval.status == "denied":
                await self._repo.update_plan(
                    plan_id,
                    {
                        "status": "cancelled",
                        "failure_reason": "approval_denied",
                        "updated_at": utc_now_iso(),
                    },
                )
                await self._plan_event(
                    plan_id,
                    "plan.cancelled",
                    {"reason": "approval_denied"},
                    trace_id=trace_id,
                )
                return await self._response_for_plan(
                    plan_id,
                    message="审批已拒绝，计划已取消，未执行外部发布。",
                    next_step=None,
                )
            if approval.status not in {"approved", "edited"} and not request.force:
                return await self._response_for_plan(
                    plan_id,
                    message="审批状态还不能释放这个外部平台动作。",
                    next_step="approve_or_deny_pending_action",
                )
        await self._validate_selected_handle(plan, trace_id=trace_id)
        await self._repo.update_plan(
            plan_id,
            {"status": "running", "updated_at": utc_now_iso()},
        )
        await self._plan_event(
            plan_id,
            "plan.running",
            {"executor": request.executor or plan.execution_mode},
            trace_id=trace_id,
        )
        provider_key = request.executor or plan.execution_mode
        provider = self._providers.get(provider_key)
        result = await provider.execute(
            ProviderExecutionRequest(plan=plan, repo=self._repo, trace_id=trace_id)
        )
        await self._repo.update_plan(
            plan_id,
            {
                "status": result.status,
                "failure_reason": result.failure_reason,
                "evidence": {
                    **plan.evidence,
                    "provider_result": result.evidence,
                    "provider_registry": {
                        "provider_key": provider.info.provider_key,
                        "execution_modes": provider.info.execution_modes,
                        "real_external_platform_integration": (
                            provider.info.real_external_platform_integration
                        ),
                    },
                    "rollback": {"external_state_change": True, "manual_review_required": True},
                },
                "updated_at": utc_now_iso(),
            },
        )
        await self._plan_event(
            plan_id,
            f"plan.{result.status}",
            result.evidence,
            trace_id=trace_id,
        )
        return await self._response_for_plan(
            plan_id,
            message=result.message,
            next_step=result.next_step
            if result.next_step is not None
            else (None if result.status == "completed" else "retry_or_refresh_account"),
        )

    async def _insert_plan_for_intent(
        self,
        intent: ExternalPlatformActionIntent,
        *,
        status: str,
        execution_mode: str,
        trace_id: str | None,
        target_id: str | None = None,
        selected: AccountAssetCandidate | None = None,
        risk_level: str | None = None,
        failure_reason: str | None = None,
        evidence: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExternalPlatformActionPlan:
        plan_id = new_id("epap")
        now = utc_now_iso()
        target = await self._target_for_intent(intent)
        risk = risk_level or (_risk_for_action(target, intent.action_type) if target else "R1")
        steps = _steps_for_action(
            action_type=intent.action_type,
            risk_level=risk,
            execution_mode=execution_mode,
        )
        data = {
            "plan_id": plan_id,
            "intent_id": intent.intent_id,
            "organization_id": intent.organization_id,
            "member_id": intent.member_id,
            "conversation_id": intent.conversation_id,
            "trace_id": trace_id or intent.trace_id,
            "platform_key": intent.platform_key,
            "target_id": target_id or (target.target_id if target else None),
            "selected_asset_id": selected.asset_id if selected else None,
            "selected_handle_id": selected.handle_id if selected else None,
            "action_type": intent.action_type,
            "execution_mode": execution_mode,
            "steps": steps,
            "status": status,
            "risk_level": risk,
            "content_summary": intent.content_summary,
            "failure_reason": failure_reason,
            "evidence": _safe_evidence(
                {
                    "intent_ref": intent.intent_id,
                    "redaction": intent.constraints.get("redaction", {}),
                    **_phase99_plan_bootstrap(
                        intent=intent,
                        selected=selected,
                        risk_level=risk,
                        metadata=metadata or {},
                    ),
                    **(evidence or {}),
                }
            ),
            "metadata": redact(metadata or {}),
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_plan(data)
        await self._plan_event(
            plan_id,
            "plan.created",
            {
                "status": status,
                "platform_key": intent.platform_key,
                "action_type": intent.action_type,
                "risk_level": risk,
            },
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="external_platform.plan.created",
            object_type="external_platform_action_plan",
            object_id=plan_id,
            summary="外部平台动作计划已创建",
            risk_level=_risk_enum(risk),
            payload={"plan_id": plan_id, "status": status, "action_type": intent.action_type},
            trace_id=trace_id,
        )
        return ExternalPlatformActionPlan(**data)

    async def _bind_selected_account(
        self,
        plan_id: str,
        selected: AccountAssetCandidate,
        *,
        target: ExternalPlatformTarget,
        trace_id: str | None,
    ) -> ExternalPlatformActionPlanResponse:
        plan = await self._get_plan(plan_id)
        steps = _steps_for_action(
            action_type=plan.action_type,
            risk_level=plan.risk_level,
            execution_mode=plan.execution_mode,
        )
        browser_task_required = plan.execution_mode == "browser"
        phase99_context = await self._ensure_phase99_content_platform_context(
            plan=plan,
            target=target,
            selected=selected,
            trace_id=trace_id,
        )
        metadata_update = {
            **plan.metadata,
            **{
                key: value
                for key, value in phase99_context.items()
                if key
                in {
                    "browser_session_handle_id",
                    "browser_profile_id",
                    "browser_session_id",
                    "session_bootstrap_status",
                    "login_path",
                }
            },
        }
        skip_approval = _should_skip_test_account_approval(
            selected=selected,
            target=target,
            action_type=plan.action_type,
        )
        update: dict[str, Any] = {
            "selected_asset_id": selected.asset_id,
            "selected_handle_id": selected.handle_id,
            "steps": steps,
            "evidence": {
                **plan.evidence,
                "selected_candidate": selected.model_dump(mode="json"),
                "selected_account_summary": _selected_account_summary(selected),
                "safety": {
                    "external_state_change": plan.action_type in HIGH_RISK_ACTIONS,
                    "requires_approval": _risk_order(plan.risk_level) >= 3 and not skip_approval,
                    "approval_before_submit": not skip_approval,
                },
                **{
                    key: value
                    for key, value in phase99_context.items()
                    if key not in {"task_id", "browser_session_handle_id", "browser_profile_id", "browser_session_id", "session_bootstrap_status", "login_path"}
                },
            },
            "metadata": metadata_update,
            "updated_at": utc_now_iso(),
        }
        if phase99_context.get("task_id"):
            update["task_id"] = phase99_context["task_id"]
        if skip_approval:
            task_id = str(update.get("task_id") or "")
            update.update(
                {
                    "task_id": task_id,
                    "status": "ready",
                    "metadata": {
                        **metadata_update,
                        "test_account_approval_bypass": True,
                    },
                    "evidence": {
                        **update["evidence"],
                        "approval_bypass": {
                            "policy_source": "test_account_whitelist",
                            "selected_asset_id": selected.asset_id,
                            "provider_key": selected.provider_key,
                        },
                    },
                }
            )
            await self._repo.update_plan(plan.plan_id, update)
            await self._plan_event(
                plan.plan_id,
                "plan.ready",
                {
                    "risk_level": plan.risk_level,
                    "approval_profile": "test_account_whitelist",
                    "task_id": task_id,
                },
                trace_id=trace_id,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="测试账号命中外部平台白名单，已跳过审批并准备自动执行。",
                next_step="execute_action_plan",
            )
        if _risk_order(plan.risk_level) >= 3:
            approval_required = True
            if self._safety_policy is not None:
                policy = await self._safety_policy.get_policy(
                    organization_id=plan.organization_id
                )
                approval_required = not policy.should_skip_approval(
                    action=f"external_platform.{plan.action_type}",
                    risk_level=_risk_enum(plan.risk_level),
                    action_category="network_write",
                    payload={
                        "platform_key": plan.platform_key,
                        "target_id": target.target_id,
                        "action_type": plan.action_type,
                        "content_summary": plan.content_summary,
                    },
                )
            if not approval_required:
                if browser_task_required and not update.get("task_id"):
                    update["task_id"] = await self._ensure_plan_task_id(
                        plan=plan,
                        target=target,
                        selected=selected,
                        trace_id=trace_id,
                    )
                update["status"] = "ready"
                await self._repo.update_plan(plan.plan_id, update)
                await self._plan_event(
                    plan.plan_id,
                    "plan.ready",
                    {"risk_level": plan.risk_level, "approval_profile": "balanced_personal"},
                    trace_id=trace_id,
                )
                return await self._response_for_plan(
                    plan.plan_id,
                    message="外部平台动作计划已准备好，当前个人审批策略不要求额外确认。",
                    next_step="execute_action_plan",
                )
            task_id = str(update.get("task_id") or "")
            if not task_id:
                task_id = await self._ensure_plan_task_id(
                    plan=plan,
                    target=target,
                    selected=selected,
                    trace_id=trace_id,
                )
            approval = await self._approvals.create_approval(
                task_id=task_id,
                organization_id=plan.organization_id,
                requested_action=f"external_platform.{plan.action_type}",
                risk_level=_risk_enum(plan.risk_level),
                summary=(
                    f"准备使用 {selected.display_name} 在 {target.display_name} "
                    f"执行 {plan.action_type}，提交前需要确认。"
                ),
                payload={
                    "external_platform_plan_id": plan.plan_id,
                    "platform_key": plan.platform_key,
                    "target_id": target.target_id,
                    "action_type": plan.action_type,
                    "account_asset_id": selected.asset_id,
                    "asset_handle_id": selected.handle_id,
                    "content_summary": plan.content_summary,
                    "execution_mode": plan.execution_mode,
                    "secret_material_visible": False,
                },
                trace_id=trace_id,
            )
            update.update(
                {
                    "task_id": task_id,
                    "approval_id": approval.approval_id,
                    "status": "awaiting_approval",
                }
            )
            await self._repo.update_plan(plan.plan_id, update)
            await self._plan_event(
                plan.plan_id,
                "approval.required",
                {
                    "approval_id": approval.approval_id,
                    "task_id": task_id,
                    "risk_level": plan.risk_level,
                },
                trace_id=trace_id,
            )
            return await self._response_for_plan(
                plan.plan_id,
                message="外部平台动作计划已准备好，真正提交前正在等待审批。",
                next_step="approve_or_deny_pending_action",
            )
        update["status"] = "ready"
        if browser_task_required and not update.get("task_id"):
            update["task_id"] = await self._ensure_plan_task_id(
                plan=plan,
                target=target,
                selected=selected,
                trace_id=trace_id,
            )
        await self._repo.update_plan(plan.plan_id, update)
        await self._plan_event(
            plan.plan_id,
            "plan.ready",
            {"risk_level": plan.risk_level},
            trace_id=trace_id,
        )
        return await self._response_for_plan(
            plan.plan_id,
            message="低风险外部平台动作计划已准备好。",
            next_step="execute_action_plan",
        )

    async def _ensure_plan_task_id(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        target: ExternalPlatformTarget,
        selected: AccountAssetCandidate,
        trace_id: str | None,
    ) -> str:
        if plan.task_id:
            return plan.task_id
        task = await self._tasks.create_task(
            TaskCreateRequest(
                conversation_id=plan.conversation_id,
                owner_member_id=plan.member_id,
                goal=f"外部平台动作计划：{target.display_name} {plan.action_type}",
                mode_hint=TaskMode.WORKFLOW,
                success_criteria=["外部平台动作按受控流程执行", "所有证据必须脱敏"],
                constraints={
                    "external_platform_action": True,
                    "plan_id": plan.plan_id,
                    "platform_key": plan.platform_key,
                    "action_type": plan.action_type,
                    "selected_asset_id": selected.asset_id,
                },
                resource_handle_ids=[selected.handle_id] if selected.handle_id else [],
                planner_context={
                    "external_platform_action": {
                        "plan_id": plan.plan_id,
                        "platform_key": plan.platform_key,
                        "action_type": plan.action_type,
                        "privacy": "redacted_content_only",
                    }
                },
                auto_start=False,
            ),
            trace_id=trace_id,
        )
        return task.task_id

    async def _ensure_phase99_content_platform_context(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        target: ExternalPlatformTarget,
        selected: AccountAssetCandidate,
        trace_id: str | None,
    ) -> dict[str, Any]:
        if plan.execution_mode != "browser" or plan.action_type != "publish_content":
            return {}
        adapter = await self._adapter_repo.find_active_adapter(
            organization_id="org_default",
            platform_key=str(plan.platform_key or ""),
            action_type=plan.action_type,
            adapter_type="browser",
        )
        task_id = await self._ensure_plan_task_id(
            plan=plan,
            target=target,
            selected=selected,
            trace_id=trace_id,
        )
        binding = await self._resolve_external_platform_skill_binding(
            owner_member_id=plan.member_id,
            adapter=adapter,
            trace_id=trace_id,
        )
        requires_real_browser = _phase99_real_xiaohongshu_required(plan)
        result: dict[str, Any] = {
            "task_id": task_id,
            "content_platform": {
                "provider_type": "social_platform_provider",
                "request_type": _content_platform_request_type(plan.action_type),
                "platform_key": plan.platform_key,
                "publish_surface": str(plan.metadata.get("publish_surface") or "text_note"),
                "session_strategy": "persistent_session_preferred",
                "secret_material_visible": False,
            },
            "post_draft": _phase99_post_draft(plan),
            "publish_candidate": _phase99_publish_candidate(
                plan=plan,
                target=target,
                selected=selected,
            ),
            "deliverable": {
                "status": "planned",
                "requires_visible_proof": True,
                "requires_comment_visible_proof": requires_real_browser,
            },
            "recovery_evidence": {
                "status": "not_triggered",
                "resume_strategy": "human_resume_real_browser_flow",
            },
        }
        if requires_real_browser:
            result.update(
                await self._ensure_browser_session_context(
                    plan=plan,
                    selected=selected,
                    adapter=adapter,
                    trace_id=trace_id,
                )
            )
        if not binding.get("ready"):
            result["external_platform_skill"] = binding
            result["content_platform_skill"] = binding
            return result
        skill_run = await self._run_external_platform_skill(
            skill_id=str(binding["skill_id"]),
            task_id=task_id,
            owner_member_id=plan.member_id,
            input_data=_phase99_skill_input(plan=plan, selected=selected, target=target, adapter=adapter),
            trace_id=trace_id,
        )
        workflow_spec = await self._extract_external_platform_workflow_spec(
            skill_run_artifact_ids=list(skill_run.artifact_ids),
            trace_id=trace_id,
        )
        skill_result = {
            **binding,
            "skill_run_id": skill_run.skill_run_id,
            "status": skill_run.status,
            "artifact_ids": list(skill_run.artifact_ids),
            "workflow_spec": workflow_spec,
        }
        result["external_platform_skill"] = skill_result
        result["content_platform_skill"] = skill_result
        result["skill_run"] = {
            "skill_run_id": skill_run.skill_run_id,
            "status": skill_run.status,
            "artifact_ids": list(skill_run.artifact_ids),
            "output_redacted": redact(skill_run.output_redacted),
        }
        return result

    async def _resolve_external_platform_skill_binding(
        self,
        *,
        owner_member_id: str,
        adapter: dict[str, Any] | None,
        trace_id: str | None,
    ) -> dict[str, Any]:
        manifest = adapter.get("manifest") if isinstance(adapter, dict) else {}
        manifest = manifest if isinstance(manifest, dict) else {}
        binding = manifest.get("skill_binding") if isinstance(manifest.get("skill_binding"), dict) else {}
        repository_id = str(binding.get("repository_id") or "clawhub").strip() or "clawhub"
        package_ref = str(binding.get("package_ref") or "").strip()
        source_policy = str(binding.get("source_policy") or "").strip() or "repository_with_fixture_fallback"
        capabilities = [str(item) for item in binding.get("capabilities", []) if str(item).strip()]
        if not package_ref:
            return {
                "enabled": True,
                "ready": False,
                "provider_type": "external_platform_skill",
                "repository_id": repository_id,
                "package_ref": package_ref or None,
                "capabilities": capabilities,
                "blocked_reason": "external_platform_skill_binding_missing",
            }
        if self._skills is None or self._skill_governance is None or self._skill_repositories is None:
            return {
                "enabled": True,
                "ready": False,
                "provider_type": "external_platform_skill",
                "repository_id": repository_id,
                "package_ref": package_ref,
                "capabilities": capabilities,
                "blocked_reason": "external_platform_skill_services_unavailable",
            }
        fixture_path = _fixture_path_for_skill_binding(repository_id=repository_id, binding=binding)
        source_uri = f"{repository_id}:{package_ref}"
        try:
            await self._skill_repositories.ensure_configured(trace_id=trace_id)
            try:
                await self._skill_repositories.refresh_repository(repository_id, trace_id=trace_id)
            except Exception:
                pass
            prefer_fixture = (
                source_policy == "repository_with_fixture_fallback"
                and fixture_path is not None
                and fixture_path.exists()
            )
            install_request = BundleInstallRequest(
                source_type="local_directory" if prefer_fixture else "repository_ref",
                source_uri=str(fixture_path) if prefer_fixture else source_uri,
                requested_by_member_id=owner_member_id,
                idempotency_key=(
                    f"phase99:external-platform-skill:{fixture_path.name}"
                    if prefer_fixture
                    else f"phase99:external-platform-skill:{source_uri}"
                ),
            )
            bundle, skills, _preview = await self._skills.install_bundle(
                install_request,
                trace_id=trace_id,
            )
            if bundle.status != "enabled":
                bundle = await self._skills.enable_bundle(
                    bundle.bundle_id,
                    actor_member_id=owner_member_id,
                    trace_id=trace_id,
                )
            bound_skill = skills[0] if skills else None
            if bound_skill is None:
                return {
                    "enabled": True,
                    "ready": False,
                    "provider_type": "external_platform_skill",
                    "repository_id": repository_id,
                    "package_ref": package_ref,
                    "capabilities": capabilities,
                    "bundle_id": bundle.bundle_id,
                    "blocked_reason": "external_platform_bundle_has_no_skills",
                }
            skill = await self._skills.get_skill(bound_skill.skill_id)
            grants = await self._skill_governance.list_grants(skill.skill_id)
            if not any(item.status == "active" and item.subject_id == owner_member_id for item in grants):
                await self._skill_governance.create_grant(
                    skill.skill_id,
                    SkillGrantCreateRequest(
                        subject_id=owner_member_id,
                        created_by_member_id=owner_member_id,
                    ),
                    trace_id=trace_id,
                )
            return {
                "enabled": True,
                "ready": True,
                "provider_type": "external_platform_skill",
                "repository_id": repository_id,
                "package_ref": package_ref,
                "capabilities": capabilities,
                "bundle_id": bundle.bundle_id,
                "skill_id": skill.skill_id,
                "selection_reason": "manifest_skill_binding",
                "source_policy": source_policy,
            }
        except Exception as exc:
            return {
                "enabled": True,
                "ready": False,
                "provider_type": "external_platform_skill",
                "repository_id": repository_id,
                "package_ref": package_ref,
                "capabilities": capabilities,
                "blocked_reason": str(getattr(exc, "code", "skill_binding_failed")),
                "error_summary": str(redact(str(exc)))[:160],
                "source_policy": source_policy,
            }

    async def _ensure_browser_session_context(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        selected: AccountAssetCandidate,
        adapter: dict[str, Any] | None,
        trace_id: str | None,
    ) -> dict[str, Any]:
        existing = await self._find_browser_session_context(
            account_asset_id=selected.asset_id
        )
        if existing is not None:
            try:
                session = await self._browser_sessions.get_session(existing["browser_session_id"])
            except Exception:
                session = None
            if session is not None and (
                session.health_status in {"login_required", "session_expired", "recovery_required", "degraded"}
                or session.login_state in {"login_required", "expired", "recovery_required", "degraded"}
                or session.status in {"expired", "recovery_required", "degraded", "revoked", "cleared"}
            ):
                existing = None
        if existing is None:
            existing = await self._create_browser_session_context(
                plan=plan,
                selected=selected,
                adapter=adapter,
                trace_id=trace_id,
            )
            bootstrap_status = "created"
        else:
            existing["handle_id"] = await self._issue_browser_session_handle(
                plan=plan,
                session_asset_id=existing["asset_id"],
                selected=selected,
                trace_id=trace_id,
            )
            bootstrap_status = "reused"
        return {
            "browser_session_handle_id": existing["handle_id"],
            "browser_profile_id": existing["browser_profile_id"],
            "browser_session_id": existing["browser_session_id"],
            "session_bootstrap_status": bootstrap_status,
            "login_path": "session_reuse",
            "browser_session": {
                "asset_id": existing["asset_id"],
                "handle_id": existing["handle_id"],
                "browser_profile_id": existing["browser_profile_id"],
                "browser_session_id": existing["browser_session_id"],
                "bootstrap_status": bootstrap_status,
                "login_domain": existing["login_domain"],
                "secret_material_visible": False,
            },
        }

    async def _find_browser_session_context(
        self,
        *,
        account_asset_id: str,
    ) -> dict[str, str] | None:
        assets = await self._asset_repo.list_assets(
            organization_id="org_default",
            asset_type=AssetCategory.ACCOUNT.value,
            status="active",
            limit=200,
        )
        for asset in assets:
            if asset.get("provider") != "browser_session":
                continue
            metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
            config = asset.get("config") if isinstance(asset.get("config"), dict) else {}
            linked_account_id = str(
                metadata.get("linked_account_asset_id")
                or config.get("linked_account_asset_id")
                or ""
            ).strip()
            browser_profile_id = str(
                config.get("browser_profile_id") or config.get("profile_id") or ""
            ).strip()
            browser_session_id = str(config.get("browser_session_id") or "").strip()
            if (
                linked_account_id == account_asset_id
                and browser_profile_id
                and browser_session_id
            ):
                return {
                    "asset_id": str(asset["asset_id"]),
                    "handle_id": "",
                    "browser_profile_id": browser_profile_id,
                    "browser_session_id": browser_session_id,
                    "login_domain": str(config.get("login_domain") or "").strip(),
                }
        return None

    async def _create_browser_session_context(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        selected: AccountAssetCandidate,
        adapter: dict[str, Any] | None,
        trace_id: str | None,
    ) -> dict[str, str]:
        account_asset = await self._asset_repo.get_asset(selected.asset_id)
        if account_asset is None:
            raise AppError(
                ErrorCode.ASSET_NOT_FOUND,
                "selected account asset missing while bootstrapping browser session",
                status_code=404,
        )
        account_config = (
            account_asset.get("config") if isinstance(account_asset.get("config"), dict) else {}
        )
        username = str(
            account_config.get("username") or selected.display_name or "external_platform_browser_user"
        ).strip()
        login_domain = _browser_login_domain(plan=plan, adapter=adapter)
        session_asset = await self._assets.create_asset(
            AssetCreateRequest(
                asset_type=AssetCategory.ACCOUNT,
                display_name=f"{selected.display_name} browser session",
                provider="browser_session",
                sensitivity="high",
                config={
                    "platform": plan.platform_key,
                    "username": username,
                    "auth_type": "cookie_session",
                    "login_domain": login_domain,
                    "linked_account_asset_id": selected.asset_id,
                },
                owner_scope_type="member",
                owner_scope_id=plan.member_id,
                visibility="private",
                risk_level=RiskLevel.R3,
                summary_text=f"Persistent browser session for {selected.display_name}",
                capabilities=["read", "interact", "capture", "download"],
                metadata={
                    "platform": plan.platform_key,
                    "linked_account_asset_id": selected.asset_id,
                    "linked_account_handle_id": selected.handle_id,
                    "session_role": "persistent_browser_session",
                },
            ),
            trace_id=trace_id,
        )
        for action in ("read", "interact"):
            await self._capability.create_grant(
                CapabilityGrantCreateRequest(
                    subject_type="member",
                    subject_id=plan.member_id,
                    object_type="asset",
                    object_id=session_asset.asset_id,
                    action=action,
                    effect="allow",
                    risk_level=RiskLevel.R2 if action == "read" else RiskLevel.R3,
                    source_type="external_platform_session_bootstrap",
                    source_id=plan.plan_id,
                ),
                trace_id=trace_id,
            )
        profile = await self._browser_sessions.create_profile(
            BrowserProfileCreateRequest(
                display_name=f"{selected.display_name} browser profile",
                profile_type="task_isolated",
                storage_backend="local_encrypted",
                sensitivity="high",
                allowed_domains=[],
                execution_backend=(
                    "local_cdp"
                    if str(plan.metadata.get("provider_mode") or "").strip().lower() == "local_cdp"
                    else "playwright_ephemeral"
                ),
                browser_family="edge",
                identity_binding_status=(
                    "pending"
                    if str(plan.metadata.get("provider_mode") or "").strip().lower() == "local_cdp"
                    else "unbound"
                ),
                login_capture_mode="manual_handoff",
                metadata={
                    "platform": plan.platform_key,
                    "linked_account_asset_id": selected.asset_id,
                    "linked_account_handle_id": selected.handle_id,
                    "bootstrap_plan_id": plan.plan_id,
                },
                created_by_member_id=plan.member_id,
            ),
            trace_id=trace_id,
        )
        session = await self._browser_sessions.create_session(
            profile.browser_profile_id,
            BrowserSessionCreateRequest(
                asset_id=session_asset.asset_id,
                login_domain=login_domain,
                auth_type="cookie_session",
                sensitivity="high",
                session_metadata={
                    "platform": plan.platform_key,
                    "linked_account_asset_id": selected.asset_id,
                    "bootstrap_plan_id": plan.plan_id,
                },
                created_by_member_id=plan.member_id,
                reuse_policy={"strategy": "persistent_session_preferred"},
                execution_backend=(
                    "local_cdp"
                    if str(plan.metadata.get("provider_mode") or "").strip().lower() == "local_cdp"
                    else "playwright_ephemeral"
                ),
                identity_source=(
                    "local_edge_cdp"
                    if str(plan.metadata.get("provider_mode") or "").strip().lower() == "local_cdp"
                    else "playwright_ephemeral"
                ),
                browser_family="edge",
                identity_binding_status=(
                    "pending"
                    if str(plan.metadata.get("provider_mode") or "").strip().lower() == "local_cdp"
                    else "unbound"
                ),
                login_capture_mode="manual_handoff",
            ),
            trace_id=trace_id,
        )
        return {
            "asset_id": session_asset.asset_id,
            "handle_id": await self._issue_browser_session_handle(
                plan=plan,
                session_asset_id=session_asset.asset_id,
                selected=selected,
                trace_id=trace_id,
            ),
            "browser_profile_id": profile.browser_profile_id,
            "browser_session_id": session.browser_session_id,
            "login_domain": login_domain,
        }

    async def _issue_browser_session_handle(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        session_asset_id: str,
        selected: AccountAssetCandidate,
        trace_id: str | None,
    ) -> str:
        response = await self._asset_broker.query(
            AssetQueryRequest(
                subject_type="member",
                subject_id=plan.member_id,
                task_id=plan.task_id,
                asset_type=AssetCategory.ACCOUNT,
                requested_actions=["read", "interact"],
                keywords=["browser session", selected.display_name, str(plan.platform_key or "")],
                context={
                    "external_platform_action": True,
                    "platform_key": plan.platform_key,
                    "browser_session_asset_id": session_asset_id,
                    "linked_account_asset_id": selected.asset_id,
                    "secret_material_requested": False,
                },
            ),
            trace_id=trace_id,
        )
        issued = next((item for item in response.handles if item.asset_id == session_asset_id), None)
        if issued is None:
            raise AppError(
                ErrorCode.INTERNAL_ERROR,
                "browser session handle bootstrap failed",
                status_code=500,
            )
        return issued.handle_id

    async def _run_external_platform_skill(
        self,
        *,
        skill_id: str,
        task_id: str,
        owner_member_id: str,
        input_data: dict[str, Any],
        trace_id: str | None,
    ):
        assert self._skills is not None
        return await self._skills.run_skill(
            skill_id,
            task_id=task_id,
            step_id=new_id("epskill"),
            owner_member_id=owner_member_id,
            input_data=input_data,
            matched_reason="external_platform_skill_binding",
            confidence=1.0,
            trace_id=trace_id,
        )

    async def _extract_external_platform_workflow_spec(
        self,
        *,
        skill_run_artifact_ids: list[str],
        trace_id: str | None,
    ) -> dict[str, Any] | None:
        del trace_id
        for artifact_id in skill_run_artifact_ids:
            artifact, path = await self._artifacts.open_download(artifact_id)
            if not str(artifact.display_name).endswith(".workflow.json"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else None
        return None

    async def _account_candidates(
        self,
        *,
        platform_key: str,
        action_type: str,
        member_id: str,
        conversation_id: str | None,
        keywords: list[str] | None = None,
        trace_id: str | None,
    ) -> list[AccountAssetCandidate]:
        query = AssetQueryRequest(
            subject_type="member",
            subject_id=member_id,
            conversation_id=conversation_id,
            asset_type=AssetCategory.ACCOUNT,
            requested_actions=_requested_actions_for_account_query(action_type),
            keywords=[platform_key, *(keywords or [])],
            context={
                "external_platform_action": True,
                "platform_key": platform_key,
                "action_type": action_type,
                "secret_material_requested": False,
            },
        )
        response = await self._asset_broker.query(
            query,
            trace_id=trace_id,
            raise_on_denied=False,
        )
        candidates: list[AccountAssetCandidate] = []
        for handle in response.handles:
            asset = await self._asset_repo.get_asset(handle.asset_id)
            if asset is None:
                continue
            candidates.append(_candidate_from_asset_and_handle(asset, handle, platform_key))
        await self._audit.write_event(
            actor_type="system",
            actor_id=member_id,
            action="external_platform.account_candidates.resolved",
            object_type="external_platform_account_candidates",
            object_id=platform_key,
            summary="外部平台账号候选已通过 Asset Broker 查询",
            risk_level=RiskLevel.R2,
            payload={
                "platform_key": platform_key,
                "action_type": action_type,
                "candidate_count": len(candidates),
                "secret_material_visible": False,
            },
            trace_id=trace_id,
        )
        return candidates

    async def _validate_selected_handle(
        self,
        plan: ExternalPlatformActionPlan,
        *,
        trace_id: str | None,
    ) -> None:
        if not plan.selected_handle_id:
            raise AppError(
                ErrorCode.ASSET_HANDLE_INVALID,
                "外部平台动作计划缺少受控资产句柄",
                status_code=409,
            )
        await self._asset_broker.validate_handle(
            plan.selected_handle_id,
            AssetHandleValidateRequest(
                subject_type="member",
                subject_id=plan.member_id,
                action=plan.action_type,
                conversation_id=plan.conversation_id,
                task_id=None,
                approval_id=plan.approval_id,
            ),
            trace_id=trace_id,
        )

    async def _response_for_plan(
        self,
        plan_id: str,
        *,
        message: str,
        next_step: str | None,
    ) -> ExternalPlatformActionPlanResponse:
        plan = await self._get_plan(plan_id)
        intent = await self._get_intent(plan.intent_id)
        target = await self._target_for_intent(intent)
        approval: ApprovalDetail | None = None
        if plan.approval_id:
            approval = await self._approvals.get(plan.approval_id)
        return ExternalPlatformActionPlanResponse(
            plan=plan,
            intent=intent,
            target=target,
            approval=_approval_public(approval) if approval else None,
            candidates=[
                AccountAssetCandidate(**item)
                for item in plan.evidence.get("account_candidates", [])
                if isinstance(item, dict)
            ],
            executions=[
                ExternalPlatformExecution(**row)
                for row in await self._repo.list_executions(plan.plan_id)
            ],
            events=[
                ExternalPlatformPlanEvent(**row)
                for row in await self._repo.list_plan_events(plan.plan_id)
            ],
            message=message,
            next_step=next_step,
        )

    async def _plan_event(
        self,
        plan_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> None:
        plan_row = await self._repo.get_plan(plan_id)
        organization_id = plan_row["organization_id"] if plan_row else "org_default"
        await self._repo.insert_event(
            {
                "event_id": new_id("epevt"),
                "plan_id": plan_id,
                "organization_id": organization_id,
                "event_type": event_type,
                "payload": payload,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )

    async def _maybe_get_intent(self, intent_id: str | None) -> ExternalPlatformActionIntent | None:
        if not intent_id:
            return None
        return await self._get_intent(intent_id)

    async def _get_intent(self, intent_id: str) -> ExternalPlatformActionIntent:
        row = await self._repo.get_intent(intent_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "外部平台动作意图不存在", status_code=404)
        return ExternalPlatformActionIntent(**row)

    async def _get_plan(self, plan_id: str) -> ExternalPlatformActionPlan:
        row = await self._repo.get_plan(plan_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "外部平台动作计划不存在", status_code=404)
        return ExternalPlatformActionPlan(**row)

    async def _target_for_intent(
        self,
        intent: ExternalPlatformActionIntent,
    ) -> ExternalPlatformTarget | None:
        if not intent.platform_key:
            return None
        target = await self._repo.get_target_by_key(intent.platform_key)
        return ExternalPlatformTarget(**target) if target else None

    async def _start_span(
        self,
        trace_id: str | None,
        span_type: str,
        *,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        if trace_id is None:
            return None
        return await self._trace.start_span(
            trace_id,
            span_type=span_type,
            name=span_type,
            input_data=redact(input_data or {}),
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


def _match_target(text: str, targets: list[dict[str, Any]]) -> dict[str, Any] | None:
    lowered = text.lower()
    matches: list[dict[str, Any]] = []
    for target in targets:
        aliases = [
            str(target.get("platform_key") or ""),
            str(target.get("display_name") or ""),
            *[str(alias) for alias in target.get("aliases", [])],
        ]
        for alias in aliases:
            if alias and alias.lower() in lowered:
                matches.append(
                    {
                        "target_id": target["target_id"],
                        "platform_key": target["platform_key"],
                        "display_name": target["display_name"],
                        "matched_alias": alias,
                    }
                )
                break
    if len(matches) == 1:
        return matches[0]
    return None


def _detect_action_type(text: str) -> tuple[str, float]:
    lowered = text.lower()
    for action_type in ("comment_content", "publish_content", "send_message", "read_status"):
        markers = ACTION_MARKERS[action_type]
        if any(marker.lower() in lowered for marker in markers):
            return action_type, 0.25
    return "unknown", 0.0


def _extract_content(text: str, action_type: str) -> str | None:
    if action_type not in {"publish_content", "comment_content", "send_message"}:
        return None
    for marker in CONTENT_MARKERS:
        if marker in text:
            value = text.split(marker, 1)[1].strip()
            return value or None
    quoted = re.findall(r"[“\"']([^”\"']{3,})[”\"']", text)
    if quoted:
        return quoted[-1].strip()
    return None


def _target_hint(text: str) -> str | None:
    match = re.search(r"(栏目|主页|后台|话题|频道)[:：]?\s*([^\s，。,.]{1,40})", text)
    return str(redact(match.group(0))) if match else None


def _content_summary(text: str) -> str:
    redacted = str(redact(text)).strip()
    compact = re.sub(r"\s+", " ", redacted)
    return compact[:240]


def _redaction_summary(text: str) -> dict[str, Any]:
    redacted = str(redact(text))
    changed = redacted != text
    return {
        "policy": "trace_service.redact",
        "redaction_count": 1 if changed else 0,
        "content_hash": _stable_hash(text),
        "redacted_hash": _stable_hash(redacted),
    }


def _intent_message(intent: ExternalPlatformActionIntent) -> str:
    if intent.status == "resolved":
        return "已解析为通用外部平台动作意图，下一步可创建受控 action plan。"
    missing = "、".join(intent.missing_fields) or "关键信息"
    return f"还缺少 {missing}，需要先澄清后再创建计划。"


def _steps_for_action(
    *,
    action_type: str,
    risk_level: str,
    execution_mode: str,
) -> list[dict[str, Any]]:
    base = [
        {
            "step_type": "resolve_account_handle",
            "risk": "R1",
            "executor": "asset_broker",
            "requires_approval": False,
        },
        {
            "step_type": "prepare_content",
            "risk": "R2",
            "executor": "orchestrator",
            "requires_approval": False,
        },
    ]
    if action_type == "publish_content":
        base.append(
            {
                "step_type": "submit_publish",
                "risk": risk_level,
                "executor": execution_mode,
                "requires_approval": _risk_order(risk_level) >= 3,
                "required_capability": "publish_content",
            }
        )
    elif action_type == "comment_content":
        base.extend(
            [
                {
                    "step_type": "locate_post",
                    "risk": "R2",
                    "executor": "browser",
                    "requires_approval": False,
                },
                {
                    "step_type": "open_comment_box",
                    "risk": "R2",
                    "executor": "browser",
                    "requires_approval": False,
                },
                {
                    "step_type": "submit_comment",
                    "risk": risk_level,
                    "executor": execution_mode,
                    "requires_approval": _risk_order(risk_level) >= 3,
                    "required_capability": "comment_content",
                },
            ]
        )
    elif action_type == "send_message":
        base.append(
            {
                "step_type": "submit_message",
                "risk": risk_level,
                "executor": execution_mode,
                "requires_approval": _risk_order(risk_level) >= 3,
                "required_capability": "send_message",
            }
        )
    else:
        base.append(
            {
                "step_type": "read_status",
                "risk": risk_level,
                "executor": execution_mode,
                "requires_approval": False,
                "required_capability": "read_status",
            }
        )
    return base


def _candidate_from_asset_and_handle(
    asset: dict[str, Any],
    handle: Any,
    platform_key: str,
) -> AccountAssetCandidate:
    config = asset.get("config") or {}
    provider_key = str(config.get("platform") or asset.get("provider") or platform_key)
    return AccountAssetCandidate(
        asset_id=asset["asset_id"],
        handle_id=handle.handle_id,
        provider_key=provider_key,
        display_name=str(asset.get("display_name") or handle.summary),
        owner_scope=str(asset.get("owner_scope_type") or "member"),
        capabilities=[str(item) for item in asset.get("capabilities", [])],
        allowed_actions=[str(item) for item in handle.allowed_actions],
        approval_required_actions=[str(item) for item in handle.approval_required_actions],
        sensitivity=str(asset.get("sensitivity") or "medium"),
        risk_level=(
            handle.risk_level.value
            if hasattr(handle.risk_level, "value")
            else str(handle.risk_level)
        ),
        selection_reason=(
            "platform_key/action capability matched through Asset Broker; "
            "secret material remains hidden"
        ),
        secret_material_visible=False,
        evidence={
            "asset_handle_id": handle.handle_id,
            "platform_key": platform_key,
            "has_secret": bool(asset.get("secret_ref")),
            "asset_metadata": redact(asset.get("metadata") or {}),
            "secret_material_visible": False,
        },
    )


def _select_candidate(
    candidates: list[AccountAssetCandidate],
    *,
    selected_asset_id: str | None = None,
    selected_handle_id: str | None = None,
    selected_display_name: str | None = None,
    text: str | None = None,
) -> AccountAssetCandidate | None:
    if not candidates:
        return None
    if selected_asset_id:
        for candidate in candidates:
            if candidate.asset_id == selected_asset_id:
                return candidate
    if selected_handle_id:
        for candidate in candidates:
            if candidate.handle_id == selected_handle_id:
                return candidate
    haystack = " ".join(value for value in [selected_display_name, text] if value).lower()
    if haystack:
        for index, candidate in enumerate(candidates, start=1):
            if str(index) in haystack or candidate.display_name.lower() in haystack:
                return candidate
    return None


def _risk_for_action(target: ExternalPlatformTarget | None, action_type: str) -> str:
    if target is not None:
        value = target.risk_defaults.get(action_type)
        if value:
            return _normalize_risk(value)
    if action_type == "publish_content":
        return "R4"
    if action_type == "comment_content":
        return "R3"
    if action_type == "send_message":
        return "R3"
    return "R1"


def _requested_actions_for_account_query(action_type: str) -> list[str]:
    if action_type in {"publish_content", "comment_content"}:
        return ["login", action_type]
    return [action_type]


def _content_platform_request_type(action_type: str) -> str:
    if action_type in {"publish_content", "comment_content"}:
        return "content_platform_publish_request"
    if action_type == "read_status":
        return "content_platform_insight_request"
    return "content_platform_review_request"


def _phase99_post_draft(plan: ExternalPlatformActionPlan) -> dict[str, Any]:
    body = str(plan.metadata.get("publish_text") or plan.content_summary or "").strip()
    title = str(plan.metadata.get("title") or body[:30] or "小红书内容草稿").strip()
    tags = [str(item) for item in plan.metadata.get("tags", []) if str(item).strip()]
    media_artifact_ids = [
        str(item) for item in plan.metadata.get("media_artifact_ids", []) if str(item).strip()
    ]
    return {
        "title": title,
        "body": body,
        "tags": tags,
        "publish_surface": str(plan.metadata.get("publish_surface") or "text_note"),
        "media_artifact_ids": media_artifact_ids,
        "comment_text": str(plan.metadata.get("comment_text") or "").strip() or None,
        "provider_mode": str(plan.metadata.get("provider_mode") or "").strip() or None,
    }


def _phase99_publish_candidate(
    *,
    plan: ExternalPlatformActionPlan,
    target: ExternalPlatformTarget,
    selected: AccountAssetCandidate,
) -> dict[str, Any]:
    media_artifact_ids = [
        str(item) for item in plan.metadata.get("media_artifact_ids", []) if str(item).strip()
    ]
    return {
        "platform_key": plan.platform_key,
        "platform_profile": "social_platform_provider",
        "action_type": plan.action_type,
        "publish_surface": str(plan.metadata.get("publish_surface") or "text_note"),
        "requires_playwright": plan.execution_mode == "browser",
        "requires_visible_proof": True,
        "requires_comment_visible_proof": bool(plan.metadata.get("comment_text")),
        "media_upload_required": bool(media_artifact_ids),
        "media_artifact_ids": media_artifact_ids,
        "risk_level": plan.risk_level,
        "approval_required": _risk_order(plan.risk_level) >= 3,
        "selected_account": _selected_account_summary(selected),
        "target_display_name": target.display_name,
    }


def _phase99_skill_input(
    *,
    plan: ExternalPlatformActionPlan,
    selected: AccountAssetCandidate,
    target: ExternalPlatformTarget,
    adapter: dict[str, Any] | None,
) -> dict[str, Any]:
    draft = _phase99_post_draft(plan)
    manifest = adapter.get("manifest") if isinstance(adapter, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    publish_flow = manifest.get("publish_flow") if isinstance(manifest.get("publish_flow"), dict) else {}
    publish_flow = dict(publish_flow)
    comment_flow = manifest.get("comment_flow") if isinstance(manifest.get("comment_flow"), dict) else {}
    comment_flow = dict(comment_flow)
    login_flow = manifest.get("login_flow") if isinstance(manifest.get("login_flow"), dict) else {}
    login_flow = dict(login_flow)
    publish_selectors = publish_flow.get("selectors") if isinstance(publish_flow.get("selectors"), dict) else {}
    comment_selectors = comment_flow.get("selectors") if isinstance(comment_flow.get("selectors"), dict) else {}
    login_selectors = login_flow.get("selectors") if isinstance(login_flow.get("selectors"), dict) else {}
    skill_binding = manifest.get("skill_binding") if isinstance(manifest.get("skill_binding"), dict) else {}
    capabilities = [str(item) for item in skill_binding.get("capabilities", []) if str(item).strip()]
    adapter_metadata = adapter.get("metadata") if isinstance(adapter, dict) else {}
    adapter_metadata = adapter_metadata if isinstance(adapter_metadata, dict) else {}
    real_browser_workflow = bool(
        adapter_metadata.get("real_platform_integration") or manifest.get("real_site_flow")
    )
    return {
        "platform_key": plan.platform_key,
        "platform_display_name": target.display_name,
        "request_type": _content_platform_request_type(plan.action_type),
        "action_type": plan.action_type,
        "account_display_name": selected.display_name,
        "publish_surface": draft["publish_surface"],
        "title": draft["title"],
        "body": draft["body"],
        "tags": draft["tags"],
        "comment_text": draft["comment_text"],
        "media_artifact_ids": draft["media_artifact_ids"],
        "selected_asset_id": selected.asset_id,
        "selected_handle_id": selected.handle_id,
        "workflow_capabilities_json": json.dumps(capabilities or ["publish_browser"], ensure_ascii=False),
        "real_browser_workflow_json": "true" if real_browser_workflow else "false",
        "login_url": str(login_flow.get("login_url") or publish_flow.get("login_url") or ""),
        "post_login_wait_text": str(login_flow.get("post_login_wait_text") or ""),
        "post_login_wait_url": str(login_flow.get("post_login_wait_url") or ""),
        "login_username_selector": str(login_selectors.get("username") or ""),
        "login_password_selector": str(login_selectors.get("password") or ""),
        "login_submit_selector": str(login_selectors.get("submit") or login_selectors.get("form") or ""),
        "publish_url": str(publish_flow.get("start_url") or manifest.get("start_url") or ""),
        "publish_wait_until": str(publish_flow.get("wait_until") or "domcontentloaded"),
        "publish_success_text": str(publish_flow.get("publish_success_text") or ""),
        "upload_success_text": str(publish_flow.get("upload_success_text") or "upload complete"),
        "publish_title_selector": str(publish_selectors.get("title") or ""),
        "publish_body_selector": str(publish_selectors.get("body") or ""),
        "publish_submit_selector": str(publish_selectors.get("submit") or ""),
        "publish_form_selector": str(publish_selectors.get("form") or ""),
        "publish_upload_selector": str(publish_selectors.get("upload") or publish_selectors.get("image_upload") or ""),
        "target_post_url": str(
            plan.metadata.get("target_post_url")
            or publish_flow.get("target_post_url")
            or ((publish_flow.get("verify") or {}).get("expected_url") if isinstance(publish_flow.get("verify"), dict) else "")
            or ""
        ),
        "comment_start_url": str(
            comment_flow.get("start_url")
            or comment_flow.get("target_post_url")
            or (((comment_flow.get("verify") or {}).get("expected_url")) if isinstance(comment_flow.get("verify"), dict) else "")
            or ""
        ),
        "comment_success_text": str(comment_flow.get("comment_success_text") or ""),
        "comment_recheck_wait_text": str(comment_flow.get("recheck_wait_text") or ""),
        "comment_box_selector": str(comment_selectors.get("comment_box") or ""),
        "comment_input_selector": str(comment_selectors.get("comment_input") or ""),
        "comment_submit_selector": str(comment_selectors.get("comment_submit") or ""),
        "comment_form_selector": str(comment_selectors.get("comment_form") or ""),
    }


def _phase99_plan_bootstrap(
    *,
    intent: ExternalPlatformActionIntent,
    selected: AccountAssetCandidate | None,
    risk_level: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    body = str(metadata.get("publish_text") or intent.content_summary or "").strip()
    title = str(metadata.get("title") or body[:30] or "小红书内容草稿").strip()
    tags = [str(item) for item in metadata.get("tags", []) if str(item).strip()]
    media_artifact_ids = [
        str(item) for item in metadata.get("media_artifact_ids", []) if str(item).strip()
    ]
    requires_real_completion = _phase99_real_xiaohongshu_metadata(
        platform_key=intent.platform_key,
        action_type=intent.action_type,
        execution_mode="browser",
        metadata=metadata,
    )
    return {
        "content_platform_request": {
            "request_type": _content_platform_request_type(intent.action_type),
            "platform_key": intent.platform_key,
            "publish_surface": str(metadata.get("publish_surface") or "text_note"),
        },
        "post_draft": {
            "title": title,
            "body": body,
            "tags": tags,
            "publish_surface": str(metadata.get("publish_surface") or "text_note"),
            "media_artifact_ids": media_artifact_ids,
            "comment_text": str(metadata.get("comment_text") or "").strip() or None,
        },
        "publish_candidate": {
            "platform_key": intent.platform_key,
            "action_type": intent.action_type,
            "risk_level": risk_level,
            "selected_asset_id": selected.asset_id if selected else None,
            "requires_playwright": metadata.get("provider_mode") == "playwright",
            "media_upload_required": bool(media_artifact_ids),
        },
        "engagement_snapshot": {
            "status": "pending",
            "comment_requested": bool(metadata.get("comment_text")),
        },
        "recovery_evidence": {
            "status": "not_triggered",
            "known_failure_reasons": [
                "login_required",
                "login_verification_required",
                "playwright_required",
                "session_expired",
                "selector_drift",
                "image_upload_failed",
                "published_post_identity_missing",
                "publish_recheck_missing",
                "comment_submit_missing",
                "comment_recheck_missing",
            ],
        },
        "deliverable": {
            "status": "planned",
            "phase": "phase99",
            "requires_comment_visible_proof": requires_real_completion,
        },
    }


def _phase99_real_xiaohongshu_validation(
    *,
    intent: ExternalPlatformActionIntent,
    execution_mode: str,
    metadata: dict[str, Any],
) -> dict[str, Any] | None:
    if not _phase99_real_xiaohongshu_metadata(
        platform_key=intent.platform_key,
        action_type=intent.action_type,
        execution_mode=execution_mode,
        metadata=metadata,
    ):
        return None
    provider_mode = str(metadata.get("provider_mode") or "").strip().lower()
    if provider_mode and provider_mode not in {"playwright", "local_cdp"}:
        return {
            "failure_reason": "browser_provider_mode_invalid",
            "missing_fields": ["provider_mode"],
            "message": "phase99 xiaohongshu real publishing requires provider_mode=local_cdp or playwright.",
        }
    require_full_comment_flow = bool(
        metadata.get("require_full_comment_flow")
        or metadata.get("publish_and_comment_both_required")
    )
    comment_text = str(metadata.get("comment_text") or "").strip()
    if require_full_comment_flow and not comment_text:
        return {
            "failure_reason": "comment_text_required",
            "missing_fields": ["comment_text"],
            "message": "phase99 xiaohongshu full publish+comment flow requires a first-comment draft.",
        }
    return None


def _browser_login_domain(
    *,
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any] | None,
) -> str:
    manifest = adapter.get("manifest") if isinstance(adapter, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    login_flow = manifest.get("login_flow") if isinstance(manifest.get("login_flow"), dict) else {}
    publish_flow = manifest.get("publish_flow") if isinstance(manifest.get("publish_flow"), dict) else {}
    for value in (
        str(plan.metadata.get("target_post_url") or "").strip(),
        str(login_flow.get("login_url") or "").strip(),
        str(publish_flow.get("start_url") or manifest.get("start_url") or "").strip(),
    ):
        if not value:
            continue
        host = urlsplit(value).hostname
        if host:
            return host
    return "www.xiaohongshu.com"


def _fixture_path_for_skill_binding(
    *,
    repository_id: str,
    binding: dict[str, Any],
) -> Path | None:
    fixture_bundle_id = str(binding.get("fixture_bundle_id") or "").strip()
    if fixture_bundle_id:
        return (
            Path.cwd()
            / "config"
            / "skill-repositories"
            / "fixtures"
            / fixture_bundle_id
        )
    package_ref = str(binding.get("package_ref") or "").strip()
    if not package_ref:
        return None
    slug = package_ref.replace("/", "-").replace(":", "-")
    return (
        Path.cwd()
        / "config"
        / "skill-repositories"
        / "fixtures"
        / f"{repository_id}-{slug}"
    )


def _phase99_real_xiaohongshu_metadata(
    *,
    platform_key: str | None,
    action_type: str | None,
    execution_mode: str,
    metadata: dict[str, Any],
) -> bool:
    if (
        str(platform_key or "") != "social_xiaohongshu"
        or str(action_type or "") != "publish_content"
        or execution_mode != "browser"
    ):
        return False
    return any(
        [
            str(metadata.get("provider_mode") or "").strip().lower() == "playwright",
            bool(str(metadata.get("title") or "").strip()),
            bool(metadata.get("tags")),
            bool(str(metadata.get("publish_surface") or "").strip()),
            bool(metadata.get("media_artifact_ids")),
            "comment_text" in metadata,
        ]
    )


def _phase99_real_xiaohongshu_required(plan: ExternalPlatformActionPlan) -> bool:
    return _phase99_real_xiaohongshu_metadata(
        platform_key=plan.platform_key,
        action_type=plan.action_type,
        execution_mode=plan.execution_mode,
        metadata=plan.metadata,
    )


def _plan_create_metadata(request: ExternalPlatformActionPlanCreateRequest) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    if request.publish_text:
        metadata["publish_text"] = request.publish_text
    if request.title:
        metadata["title"] = request.title
    if request.tags:
        metadata["tags"] = [str(item) for item in request.tags if str(item).strip()]
    if request.publish_surface:
        metadata["publish_surface"] = request.publish_surface
    if request.media_artifact_ids:
        metadata["media_artifact_ids"] = [str(item) for item in request.media_artifact_ids]
    if request.comment_text:
        metadata["comment_text"] = request.comment_text
    if request.target_post_hint:
        metadata["target_post_hint"] = request.target_post_hint
    if request.target_post_selector:
        metadata["target_post_selector"] = request.target_post_selector
    if request.target_post_url:
        metadata["target_post_url"] = request.target_post_url
    if request.published_post_ref:
        metadata["published_post_ref"] = request.published_post_ref
    if request.provider_mode:
        metadata["provider_mode"] = request.provider_mode
    if request.require_full_comment_flow:
        metadata["require_full_comment_flow"] = True
    if request.publish_text or request.comment_text or request.title or request.media_artifact_ids:
        metadata["verification_mode"] = "visible_text"
    return metadata


def _should_skip_test_account_approval(
    *,
    selected: AccountAssetCandidate,
    target: ExternalPlatformTarget,
    action_type: str,
) -> bool:
    if action_type not in {"publish_content", "comment_content"}:
        return False
    evidence = selected.evidence if isinstance(selected.evidence, dict) else {}
    metadata = evidence.get("asset_metadata") if isinstance(evidence.get("asset_metadata"), dict) else {}
    if metadata.get("auto_execute_whitelisted_real_accounts") is True:
        return True
    whitelist = metadata.get("real_platform_auto_execute_whitelist")
    if isinstance(whitelist, list) and str(target.platform_key or "") in {
        str(item) for item in whitelist if str(item).strip()
    }:
        return True
    if metadata.get("test_account_auto_approve_external_actions") is True:
        return True
    provider_key = str(selected.provider_key or "").lower()
    display_name = str(selected.display_name or "")
    return (
        str(target.platform_key or "").lower() == "social_xiaohongshu"
        and provider_key in {"xiaohongshu", "social_xiaohongshu"}
        and "测试" in display_name
        and str(selected.owner_scope or "") == "member"
    )


def _selected_account_summary(selected: AccountAssetCandidate) -> dict[str, Any]:
    provider_key = str(selected.provider_key or "")
    display_name = str(selected.display_name or "")
    masked_name = display_name[:1] + "***" if display_name else ""
    evidence = selected.evidence if isinstance(selected.evidence, dict) else {}
    asset_metadata = (
        evidence.get("asset_metadata") if isinstance(evidence.get("asset_metadata"), dict) else {}
    )
    return {
        "asset_id": selected.asset_id,
        "handle_id": selected.handle_id,
        "provider_key": provider_key,
        "display_name_masked": masked_name,
        "owner_scope": selected.owner_scope,
        "platform": asset_metadata.get("platform") or provider_key,
        "login_mode": asset_metadata.get("login_mode") or "password",
        "account_role": asset_metadata.get("account_role"),
        "environment": asset_metadata.get("environment"),
        "secret_material_visible": False,
    }


def _normalize_risk(value: str) -> str:
    lowered = str(value).lower()
    if lowered in {"high", "r4"}:
        return "R4"
    if lowered in {"medium", "r3"}:
        return "R3"
    if lowered in {"low", "r1"}:
        return "R1"
    if re.fullmatch(r"R[0-7]", str(value).upper()):
        return str(value).upper()
    return "R2"


def _risk_enum(value: str) -> RiskLevel:
    try:
        return RiskLevel(_normalize_risk(value))
    except ValueError:
        return RiskLevel.R2


def _risk_order(value: str) -> int:
    try:
        return int(_normalize_risk(value).removeprefix("R"))
    except ValueError:
        return 0


def _approval_public(approval: ApprovalDetail | None) -> dict[str, Any] | None:
    if approval is None:
        return None
    return {
        "approval_id": approval.approval_id,
        "task_id": approval.task_id,
        "requested_action": approval.requested_action,
        "risk_level": approval.risk_level.value
        if hasattr(approval.risk_level, "value")
        else str(approval.risk_level),
        "summary": approval.summary,
        "status": approval.status,
        "payload_redacted": redact(approval.payload_redacted),
    }


def _reject_inline_secret_config(value: dict[str, Any]) -> None:
    forbidden = {"token", "api_key", "password", "cookie", "private_key", "mnemonic", "secret"}
    if {str(key).lower() for key in value} & forbidden:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "外部平台 target metadata 不能包含明文 secret",
            status_code=422,
        )


def _safe_evidence(value: dict[str, Any]) -> dict[str, Any]:
    redacted = redact(value)
    return _restore_boolean_guard_fields(redacted) if isinstance(redacted, dict) else {}


def _restore_boolean_guard_fields(value: Any) -> Any:
    if isinstance(value, dict):
        restored: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"secret_material_visible", "secret_material_requested"}:
                restored[key] = False
            else:
                restored[key] = _restore_boolean_guard_fields(item)
        return restored
    if isinstance(value, list):
        return [_restore_boolean_guard_fields(item) for item in value]
    return value


def _stable_hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
