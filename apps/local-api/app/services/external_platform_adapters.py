from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from core_types import (
    ErrorCode,
    ExternalPlatformActionPlan,
    ExternalPlatformAdapter,
    ExternalPlatformAdapterDriftEvent,
    ExternalPlatformAdapterExecution,
    ExternalPlatformAdapterStep,
    ExternalPlatformAdapterVersion,
    RiskLevel,
)
from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.external_platform_adapter_repo import (
    ExternalPlatformAdapterRepository,
)
from app.db.repositories.external_platform_repo import ExternalPlatformRepository
from app.schemas.assets import AssetResolveForToolRequest
from app.schemas.browser import BrowserSessionHealthCheckRequest
from app.schemas.external_platform_adapters import (
    ExternalPlatformAdapterCompileRequest,
    ExternalPlatformAdapterCreateRequest,
    ExternalPlatformAdapterExecuteRequest,
    ExternalPlatformAdapterPlanResponse,
    ExternalPlatformAdapterResponse,
    ExternalPlatformAdapterResumeRequest,
    ExternalPlatformAdapterValidateResponse,
    ExternalPlatformDiscoveryResult,
)
from app.schemas.tasks import ToolExecuteRequest
from app.services.approvals import ApprovalService
from app.services.asset_broker import AssetBrokerService
from app.services.audit import AuditEventService
from app.services.browser_policy import (
    browser_action_policy,
    browser_backend_capabilities,
    browser_execution_summary,
    browser_session_preflight,
)
from app.services.browser_sessions import BrowserSessionService
from app.services.external_platform_discovery import (
    DiscoveryCandidate,
    ExternalPlatformDiscoveryService,
)
from app.services.tools import ToolRuntime

ADAPTER_STATUSES = {"active", "disabled", "degraded", "test_only"}
ADAPTER_TYPES = {"browser", "mcp"}
STEP_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "challenge_detected",
    "drift_detected",
    "awaiting_human",
}
SENSITIVE_KEY_EXACT = {"secret", "token", "cookie", "password", "private_key", "mnemonic"}
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(token|password|cookie|private[_-]?key|mnemonic)\s*[:=]|sk-[A-Za-z0-9_-]{12,}",
    re.IGNORECASE,
)
DEFAULT_CHALLENGE_AUTO_CHECK_SELECTORS = (
    "input[type='checkbox'][name*='agree' i]",
    "input[type='checkbox'][id*='agree' i]",
    "input[type='checkbox'][name*='protocol' i]",
    "input[type='checkbox'][id*='protocol' i]",
)
DEFAULT_CHALLENGE_AUTO_CLICK_SELECTORS = (
    "button:has-text('同意')",
    "button:has-text('同意并继续')",
    "button:has-text('确认')",
    "button:has-text('继续')",
    "button:has-text('我知道了')",
    "button:has-text('知道了')",
    "button:has-text('开始验证')",
    "button:has-text('去验证')",
    "label:has-text('同意')",
    "[role='button']:has-text('同意')",
)


class ExternalPlatformAdapterService:
    def __init__(
        self,
        *,
        repo: ExternalPlatformAdapterRepository,
        platform_repo: ExternalPlatformRepository,
        tool_runtime: ToolRuntime,
        approval_service: ApprovalService,
        audit_service: AuditEventService,
        asset_broker: AssetBrokerService,
        browser_session_service: BrowserSessionService,
    ) -> None:
        self._repo = repo
        self._platform_repo = platform_repo
        self._tools = tool_runtime
        self._approvals = approval_service
        self._audit = audit_service
        self._asset_broker = asset_broker
        self._browser_sessions = browser_session_service
        self._discovery = ExternalPlatformDiscoveryService(
            platform_repo=platform_repo,
            tool_runtime=tool_runtime,
        )

    async def register_adapter(
        self,
        request: ExternalPlatformAdapterCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformAdapterResponse:
        validation = self._validate_manifest(
            manifest=request.manifest,
            adapter_type=request.adapter_type,
            action_type=request.action_type,
            allowed_domains=request.allowed_domains,
            status=request.status,
        )
        fatal = [item for item in validation.issues if item.get("severity") == "fatal"]
        if fatal:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "外部平台 adapter manifest 含敏感材料或非法配置",
                status_code=422,
                details={"issues": fatal},
            )
        now = utc_now_iso()
        manifest = _redacted_dict(request.manifest)
        supported_actions = request.supported_actions or [request.action_type]
        allowed_domains = request.allowed_domains or _manifest_allowed_domains(manifest)
        data = {
            "adapter_id": new_id("epad"),
            "organization_id": request.organization_id,
            "platform_key": request.platform_key,
            "action_type": request.action_type,
            "adapter_type": request.adapter_type,
            "display_name": request.display_name,
            "status": request.status,
            "supported_actions": supported_actions,
            "required_asset_types": request.required_asset_types,
            "allowed_domains": allowed_domains,
            "manifest": manifest,
            "metadata": _redacted_dict(
                {
                    **request.metadata,
                    "phase": "phase50",
                    "real_platform_integration": bool(
                        request.metadata.get("real_platform_integration")
                    ),
                    "playwright_required": bool(request.metadata.get("playwright_required")),
                    "human_challenge_resume": bool(request.metadata.get("human_challenge_resume")),
                    "auto_execute_whitelisted_real_accounts": bool(
                        request.metadata.get("auto_execute_whitelisted_real_accounts")
                    ),
                    "secret_material_visible": False,
                }
            ),
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.upsert_adapter(data)
        adapter_row = await self._repo.get_adapter_by_key(
            organization_id=request.organization_id,
            platform_key=request.platform_key,
            action_type=request.action_type,
            adapter_type=request.adapter_type,
            display_name=request.display_name,
        )
        if adapter_row is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "adapter 注册后无法读取", status_code=500)
        checksum = _manifest_checksum(manifest)
        version_data = {
            "adapter_version_id": new_id("epadv"),
            "adapter_id": adapter_row["adapter_id"],
            "version": request.version,
            "manifest": manifest,
            "manifest_checksum": checksum,
            "status": request.status,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.upsert_version(version_data)
        version = await self._repo.latest_version(adapter_row["adapter_id"])
        await self._audit.write_event(
            actor_type="system",
            action="external_platform.adapter.registered",
            object_type="external_platform_adapter",
            object_id=adapter_row["adapter_id"],
            summary="外部平台 adapter manifest 已注册",
            risk_level=RiskLevel.R2,
            payload={
                "platform_key": request.platform_key,
                "action_type": request.action_type,
                "adapter_type": request.adapter_type,
                "status": request.status,
            },
            trace_id=trace_id,
        )
        return ExternalPlatformAdapterResponse(
            adapter=ExternalPlatformAdapter(**adapter_row),
            version=ExternalPlatformAdapterVersion(**version) if version else None,
            validation=ExternalPlatformAdapterValidateResponse(
                adapter_id=adapter_row["adapter_id"],
                valid=validation.valid,
                status=validation.status,
                issues=validation.issues,
                message=validation.message,
            ),
            message="adapter 已注册，manifest 已脱敏保存。",
        )

    async def list_adapters(
        self,
        *,
        platform_key: str | None = None,
        adapter_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[ExternalPlatformAdapter]:
        rows = await self._repo.list_adapters(
            platform_key=platform_key,
            adapter_type=adapter_type,
            status=status,
            limit=limit,
        )
        return [ExternalPlatformAdapter(**row) for row in rows]

    async def get_adapter(self, adapter_id: str) -> ExternalPlatformAdapterResponse:
        adapter = await self._adapter_row(adapter_id)
        version = await self._repo.latest_version(adapter_id)
        return ExternalPlatformAdapterResponse(
            adapter=ExternalPlatformAdapter(**adapter),
            version=ExternalPlatformAdapterVersion(**version) if version else None,
            message="adapter 可用。",
        )

    async def validate_adapter(self, adapter_id: str) -> ExternalPlatformAdapterValidateResponse:
        adapter = await self._adapter_row(adapter_id)
        validation = self._validate_manifest(
            manifest=adapter["manifest"],
            adapter_type=adapter["adapter_type"],
            action_type=adapter["action_type"],
            allowed_domains=adapter["allowed_domains"],
            status=adapter["status"],
        )
        return ExternalPlatformAdapterValidateResponse(
            adapter_id=adapter_id,
            valid=validation.valid,
            status=validation.status,
            issues=validation.issues,
            message=validation.message,
        )

    async def compile_plan(
        self,
        plan_id: str,
        request: ExternalPlatformAdapterCompileRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformAdapterPlanResponse:
        request = request or ExternalPlatformAdapterCompileRequest()
        plan = await self._plan(plan_id)
        adapter = await self._select_adapter(
            plan,
            adapter_id=request.adapter_id,
            adapter_type=request.adapter_type,
        )
        version = await self._repo.latest_version(adapter["adapter_id"])
        if version is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "adapter version 不存在",
                status_code=404,
                details={"reason_code": "adapter_version_missing"},
            )
        if request.force_recompile:
            await self._repo.delete_steps_for_plan_adapter(plan.plan_id, adapter["adapter_id"])
        existing = await self._repo.list_steps(plan.plan_id, adapter_id=adapter["adapter_id"])
        if not existing:
            step_specs = await self._compile_step_specs(plan, adapter)
            now = utc_now_iso()
            for spec in step_specs:
                await self._repo.insert_step(
                    {
                        "step_id": new_id("epads"),
                        "plan_id": plan.plan_id,
                        "adapter_id": adapter["adapter_id"],
                        "adapter_version_id": version["adapter_version_id"],
                        "step_name": spec["step_name"],
                        "executor": adapter["adapter_type"],
                        "tool_name": spec.get("tool_name"),
                        "risk_level": spec.get("risk_level", "R1"),
                        "requires_approval": spec.get("requires_approval", False),
                        "status": "planned",
                        "input_redacted": _redacted_dict(spec.get("input", {})),
                        "evidence": {
                            "compiled_from": "phase50_adapter_manifest",
                            "approval_before_submit": bool(spec.get("requires_approval")),
                        },
                        "approval_id": plan.approval_id if spec.get("requires_approval") else None,
                        "trace_id": trace_id or plan.trace_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            await self._platform_repo.update_plan(
                plan.plan_id,
                {
                    "evidence": {
                        **plan.evidence,
                        "adapter_compile": {
                            "adapter_id": adapter["adapter_id"],
                            "adapter_version_id": version["adapter_version_id"],
                            "step_count": len(step_specs),
                            "compile_status": "completed",
                            "secret_material_visible": False,
                        },
                    },
                    "metadata": {
                        **plan.metadata,
                        "phase50_adapter_compiled": True,
                        "adapter_type": adapter["adapter_type"],
                    },
                    "updated_at": utc_now_iso(),
                },
            )
        steps = await self._steps(plan.plan_id, adapter["adapter_id"])
        return await self._response(
            plan.plan_id,
            adapter=adapter,
            version=version,
            steps=steps,
            message="外部平台 action plan 已编译为 adapter steps。",
            next_step="execute_adapter",
        )

    async def execute_adapter(
        self,
        plan_id: str,
        request: ExternalPlatformAdapterExecuteRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformAdapterPlanResponse:
        request = request or ExternalPlatformAdapterExecuteRequest()
        plan = await self._plan(plan_id)
        requested_provider_mode = str(request.provider_mode or "").strip().lower()
        if requested_provider_mode and requested_provider_mode != str(
            plan.metadata.get("provider_mode") or ""
        ).strip().lower():
            updated_metadata = {**plan.metadata, "provider_mode": requested_provider_mode}
            await self._repo.update_plan(
                plan.plan_id,
                {"metadata": _redacted_dict(updated_metadata), "updated_at": utc_now_iso()},
            )
            plan = await self._plan(plan_id)
        if plan.status in {
            "awaiting_account",
            "awaiting_clarification",
            "awaiting_intent_clarification",
            "awaiting_target",
        }:
            return await self._response(
                plan_id,
                message="计划还缺少平台或账号信息，adapter 不会猜测执行。",
                next_step=plan.status,
            )
        discovery: ExternalPlatformDiscoveryResult | None = None
        try:
            adapter = await self._select_adapter(
                plan,
                adapter_id=request.adapter_id,
                adapter_type=request.adapter_type,
            )
        except AppError as exc:
            if not request.allow_discovery or not _is_adapter_not_configured(exc):
                raise
            discovered_adapter, discovery = await self._discover_adapter_for_plan(
                plan,
                trace_id=trace_id,
            )
            if discovered_adapter is None:
                return await self._response(
                    plan.plan_id,
                    discovery=discovery,
                    message=discovery.user_visible_message if discovery else "自动探索未完成。",
                    next_step=_discovery_next_step(discovery),
                )
            adapter = discovered_adapter
        version = await self._repo.latest_version(adapter["adapter_id"])
        if version is None:
            raise AppError(ErrorCode.NOT_FOUND, "adapter version 不存在", status_code=404)
        steps = await self._steps(plan.plan_id, adapter["adapter_id"])
        if not steps:
            compiled = await self.compile_plan(
                plan_id,
                ExternalPlatformAdapterCompileRequest(
                    adapter_id=adapter["adapter_id"],
                    adapter_type=adapter["adapter_type"],
                    force_recompile=bool(requested_provider_mode),
                ),
                trace_id=trace_id,
            )
            steps = compiled.steps
            plan = compiled.plan
        if any(step.requires_approval for step in steps) and not plan.task_id:
            return await self._fail_without_execution(
                plan,
                adapter=adapter,
                version=version,
                reason_code="adapter_task_binding_required",
                message="发布/提交类 adapter step 必须绑定任务和审批，未执行。",
                trace_id=trace_id,
            )
        execution = await self._start_or_resume_execution(
            plan=plan,
            adapter=adapter,
            version=version,
            force=request.force,
            trace_id=trace_id,
        )
        completed_step_ids: list[str] = []
        evidence_items: list[dict[str, Any]] = []
        current_url: str | None = None
        approval_id = request.approval_id or plan.approval_id
        runtime_flags = {
            "session_handle_present": bool(
                plan.metadata.get("browser_session_handle_id")
                or plan.metadata.get("session_handle_id")
            ),
            "session_authenticated": False,
            "login_attempted": False,
        }
        try:
            for step in steps:
                if step.status == "completed":
                    evidence_items.append(step.evidence)
                    current_url = _evidence_url(step.evidence) or current_url
                    if _step_marked_session_authenticated(step.evidence):
                        runtime_flags["session_authenticated"] = True
                    continue
                if step.status in STEP_TERMINAL_STATUSES and not request.force:
                    continue
                if (
                    runtime_flags["session_authenticated"]
                    and step.step_name
                    in {
                        "open_login_page",
                        "fill_login_username",
                        "fill_login_password",
                        "submit_login",
                        "detect_login_challenge",
                        "handoff_for_login",
                        "resume_after_login",
                    }
                ):
                    skipped = {
                        **step.evidence,
                        "status": "completed",
                        "step_name": step.step_name,
                        "skipped": True,
                        "skip_reason": "session_already_authenticated",
                        "session_authenticated": True,
                    }
                    await self._repo.update_step(
                        step.step_id,
                        {
                            "status": "completed",
                            "evidence": _redacted_dict(skipped),
                            "updated_at": utc_now_iso(),
                        },
                    )
                    evidence_items.append(_redacted_dict(skipped))
                    completed_step_ids.append(step.step_id)
                    continue
                if step.requires_approval:
                    approval_status = await self._approval_status(approval_id)
                    if approval_status == "denied":
                        await self._repo.update_step(
                            step.step_id,
                            {
                                "status": "cancelled",
                                "approval_id": approval_id,
                                "evidence": {
                                    **step.evidence,
                                    "failure_reason": "approval_denied",
                                    "external_submit_executed": False,
                                },
                                "updated_at": utc_now_iso(),
                            },
                        )
                        await self._finish_execution(
                            execution.adapter_execution_id,
                            status="cancelled",
                            evidence={
                                "failure_reason": "approval_denied",
                                "submit_executed": False,
                            },
                            error_code="APPROVAL_DENIED",
                        )
                        await self._platform_repo.update_plan(
                            plan.plan_id,
                            {
                                "status": "cancelled",
                                "failure_reason": "approval_denied",
                                "updated_at": utc_now_iso(),
                            },
                        )
                        return await self._response(
                            plan.plan_id,
                            adapter=adapter,
                            version=version,
                            execution=await self._execution(execution.adapter_execution_id),
                            message="审批已拒绝，adapter 已取消，未提交外部平台动作。",
                            next_step=None,
                        )
                    if approval_status != "approved":
                        await self._repo.update_step(
                            step.step_id,
                            {
                                "status": "awaiting_approval",
                                "approval_id": approval_id,
                                "evidence": {
                                    **step.evidence,
                                    "approval_required": True,
                                    "submit_executed": False,
                                },
                                "updated_at": utc_now_iso(),
                            },
                        )
                        await self._finish_execution(
                            execution.adapter_execution_id,
                            status="awaiting_approval",
                            evidence={
                                "approval_id": approval_id,
                                "step_id": step.step_id,
                                "submit_executed": False,
                            },
                        )
                        return await self._response(
                            plan.plan_id,
                            adapter=adapter,
                            version=version,
                            execution=await self._execution(execution.adapter_execution_id),
                            discovery=_discovery_with_status(
                                discovery,
                                status="awaiting_approval",
                                message="草稿已填好，还没有发布。确认后我再提交。",
                            ),
                            message="草稿已填好，还没有发布。确认后我再提交。",
                            next_step="approve_or_resume_after_human",
                        )
                await self._repo.update_step(
                    step.step_id,
                    {"status": "running", "updated_at": utc_now_iso()},
                )
                if step.step_name == "submit_login":
                    runtime_flags["login_attempted"] = True
                result = await self._execute_step(
                    plan=plan,
                    adapter=adapter,
                    step=step,
                    approval_id=approval_id if step.requires_approval else None,
                    current_url=current_url,
                    evidence_items=evidence_items,
                    trace_id=trace_id,
                )
                result = _enrich_step_result(plan=plan, adapter=adapter, step=step, result=result)
                session_probe_state = _session_probe_state(step=step, result=result)
                if session_probe_state == "authenticated":
                    runtime_flags["session_authenticated"] = True
                    result = await self._mark_browser_session_health(
                        plan=plan,
                        result=result,
                        observed_status="ready",
                        failure_reason=None,
                        recovery_hint=None,
                    )
                elif session_probe_state == "login_required":
                    if step.step_name != "check_login_state":
                        result = await self._mark_browser_session_health(
                            plan=plan,
                            result=result,
                            observed_status="login_required",
                            failure_reason="session_not_authenticated",
                            recovery_hint="fallback_to_password_login",
                        )
                if step.step_name == "handoff_for_login":
                    await self._finish_execution(
                        execution.adapter_execution_id,
                        status="awaiting_human",
                        evidence={
                            "step_id": step.step_id,
                            "login_handoff": True,
                            "human_intervention_required": True,
                            "resume_token": plan.plan_id,
                            "resume_action": "resume_after_login",
                        },
                        error_code="LOGIN_HANDOFF_REQUIRED",
                        error_summary="Continue login in the bound local browser, then resume.",
                    )
                    await self._repo.update_step(
                        step.step_id,
                        {
                            "status": "awaiting_human",
                            "tool_call_id": result.get("tool_call_id"),
                            "mcp_call_id": result.get("mcp_call_id"),
                            "evidence": result,
                            "updated_at": utc_now_iso(),
                        },
                    )
                    await self._platform_repo.update_plan(
                        plan.plan_id,
                        {
                            "status": "awaiting_human",
                            "failure_reason": "login_handoff_required",
                            "updated_at": utc_now_iso(),
                        },
                    )
                    return await self._response(
                        plan.plan_id,
                        adapter=adapter,
                        version=version,
                        execution=await self._execution(execution.adapter_execution_id),
                        discovery=_discovery_with_status(
                            discovery,
                            status="awaiting_human",
                            failure_reason="login_handoff_required",
                            message="The bound browser is ready for manual login. Finish login there, then resume.",
                        ),
                        message="The bound browser is ready for manual login. Finish login there, then resume.",
                        next_step="resume_after_login",
                    )
                identity_problem = _missing_real_xiaohongshu_post_identity(
                    plan=plan,
                    adapter=adapter,
                    step=step,
                    result=result,
                )
                if identity_problem is not None:
                    await self._finish_execution(
                        execution.adapter_execution_id,
                        status="awaiting_human",
                        evidence={
                            "step_id": step.step_id,
                            "identity_problem": identity_problem,
                            "human_intervention_required": True,
                            "resume_token": plan.plan_id,
                            "resume_action": "human_resume_real_browser_flow",
                        },
                        error_code="PUBLISHED_POST_IDENTITY_MISSING",
                        error_summary=identity_problem["message"],
                    )
                    await self._platform_repo.update_plan(
                        plan.plan_id,
                        {
                            "status": "awaiting_human",
                            "failure_reason": identity_problem["reason_code"],
                            "evidence": {
                                **plan.evidence,
                                "adapter_execution": {
                                    "status": "awaiting_human",
                                    "reason_code": identity_problem["reason_code"],
                                    "human_intervention_required": True,
                                    "resume_token": plan.plan_id,
                                },
                            },
                            "updated_at": utc_now_iso(),
                        },
                    )
                    return await self._response(
                        plan.plan_id,
                        adapter=adapter,
                        version=version,
                        execution=await self._execution(execution.adapter_execution_id),
                        discovery=_discovery_with_status(
                            discovery,
                            status="awaiting_human",
                            failure_reason=identity_problem["reason_code"],
                            message=identity_problem["message"],
                        ),
                        message=identity_problem["message"],
                        next_step="human_resume_real_browser_flow",
                    )
                backend_problem = _browser_backend_failure(
                    plan=plan,
                    adapter=adapter,
                    step=step,
                    result=result,
                )
                if backend_problem is not None:
                    required_backend = str(backend_problem.get("required_backend") or "browser").lower()
                    await self._record_drift_or_challenge(
                        plan=plan,
                        adapter=adapter,
                        step=step,
                        drift_type=f"{required_backend}_required",
                        status="failed",
                        evidence={**result, "backend_requirement": backend_problem},
                        trace_id=trace_id,
                    )
                    await self._finish_execution(
                        execution.adapter_execution_id,
                        status="failed",
                        evidence={"step_id": step.step_id, "backend_requirement": backend_problem},
                        error_code=f"{required_backend.upper()}_REQUIRED",
                        error_summary=backend_problem["message"],
                    )
                    await self._platform_repo.update_plan(
                        plan.plan_id,
                        {
                            "status": "failed",
                            "failure_reason": f"{required_backend}_required",
                            "updated_at": utc_now_iso(),
                        },
                    )
                    return await self._response(
                        plan.plan_id,
                        adapter=adapter,
                        version=version,
                        execution=await self._execution(execution.adapter_execution_id),
                        discovery=_discovery_with_status(
                            discovery,
                            status="failed",
                            failure_reason=f"{required_backend}_required",
                            message=backend_problem["message"],
                        ),
                        message=backend_problem["message"],
                        next_step=f"retry_with_{required_backend}",
                    )
                current_url = _evidence_url(result) or current_url
                challenge = self._detect_challenge(adapter["manifest"], result, step=step)
                if challenge is not None:
                    remediated = await self._attempt_challenge_auto_remediation(
                        plan=plan,
                        adapter=adapter,
                        step=step,
                        result=result,
                        challenge=challenge,
                        trace_id=trace_id,
                    )
                    if remediated is not None:
                        result = remediated
                        challenge = self._detect_challenge(adapter["manifest"], result, step=step)
                        current_url = _evidence_url(result) or current_url
                    if challenge is None:
                        await self._repo.update_step(
                            step.step_id,
                            {
                                "status": "completed",
                                "approval_id": approval_id if step.requires_approval else step.approval_id,
                                "tool_call_id": result.get("tool_call_id"),
                                "mcp_call_id": result.get("mcp_call_id"),
                                "evidence": result,
                                "updated_at": utc_now_iso(),
                            },
                        )
                        completed_step_ids.append(step.step_id)
                        evidence_items.append(result)
                        continue
                    if step.step_name == "check_login_state" and challenge.get(
                        "reason_code"
                    ) in {"login_verification_required", "login_required"}:
                        result = _annotate_result(
                            result,
                            {
                                "login_state_detected": "login_required",
                                "login_fallback_required": True,
                                "session_reused": False,
                            },
                        )
                        evidence_items.append(result)
                        await self._repo.update_step(
                            step.step_id,
                            {
                                "status": "completed",
                                "tool_call_id": result.get("tool_call_id"),
                                "mcp_call_id": result.get("mcp_call_id"),
                                "evidence": result,
                                "updated_at": utc_now_iso(),
                            },
                        )
                        completed_step_ids.append(step.step_id)
                        continue
                    if runtime_flags["login_attempted"] and challenge.get("reason_code") == "login_verification_required":
                        challenge = {
                            **challenge,
                            "reason_code": "password_login_failed",
                            "message": "Password login did not reach an authenticated publish state.",
                        }
                    if _challenge_waits_for_human(plan=plan, adapter=adapter):
                        await self._record_drift_or_challenge(
                            plan=plan,
                            adapter=adapter,
                            step=step,
                            drift_type=challenge["drift_type"],
                            status="awaiting_human",
                            evidence={**result, "challenge": challenge, "resume_token": plan.plan_id},
                            trace_id=trace_id,
                        )
                        await self._finish_execution(
                            execution.adapter_execution_id,
                            status="awaiting_human",
                            evidence={
                                "step_id": step.step_id,
                                "challenge": challenge,
                                "human_intervention_required": True,
                                "resume_token": plan.plan_id,
                                "resume_action": "human_resume_real_browser_flow",
                            },
                            error_code=challenge["reason_code"].upper(),
                            error_summary=challenge["message"],
                        )
                        await self._platform_repo.update_plan(
                            plan.plan_id,
                            {
                                "status": "awaiting_human",
                                "failure_reason": challenge["reason_code"],
                                "evidence": {
                                    **plan.evidence,
                                    "adapter_execution": {
                                        "status": "awaiting_human",
                                        "reason_code": challenge["reason_code"],
                                        "human_intervention_required": True,
                                        "resume_token": plan.plan_id,
                                    },
                                },
                                "updated_at": utc_now_iso(),
                            },
                        )
                        return await self._response(
                            plan.plan_id,
                            adapter=adapter,
                            version=version,
                            execution=await self._execution(execution.adapter_execution_id),
                            discovery=_discovery_with_status(
                                discovery,
                                status="awaiting_human",
                                failure_reason=challenge["reason_code"],
                                message="检测到登录验证或风控提示，已保留现场并等待人工接管后恢复。",
                            ),
                            message="检测到登录验证或风控提示，已保留现场并等待人工接管后恢复。",
                            next_step="human_resume_real_browser_flow",
                        )
                    await self._record_drift_or_challenge(
                        plan=plan,
                        adapter=adapter,
                        step=step,
                        drift_type=challenge["drift_type"],
                        status=challenge["status"],
                        evidence={**result, "challenge": challenge},
                        trace_id=trace_id,
                    )
                    await self._finish_execution(
                        execution.adapter_execution_id,
                        status=challenge["status"],
                        evidence={
                            "step_id": step.step_id,
                            "challenge": challenge,
                            "submit_executed": step.step_name == "submit_publish",
                        },
                        error_code=challenge["reason_code"],
                        error_summary=challenge["message"],
                    )
                    await self._platform_repo.update_plan(
                        plan.plan_id,
                        {
                            "status": "failed",
                            "failure_reason": challenge["reason_code"],
                            "evidence": {
                                **plan.evidence,
                                "adapter_execution": {
                                    "status": challenge["status"],
                                    "reason_code": challenge["reason_code"],
                                },
                            },
                            "updated_at": utc_now_iso(),
                        },
                    )
                    return await self._response(
                        plan.plan_id,
                        adapter=adapter,
                        version=version,
                        execution=await self._execution(execution.adapter_execution_id),
                        discovery=_discovery_with_status(
                            discovery,
                            status=challenge["status"],
                            failure_reason=challenge["reason_code"],
                            message=challenge["message"],
                        ),
                        message=challenge["message"],
                        next_step="retry_or_refresh_adapter",
                    )
                drift = self._detect_drift(result)
                if drift is not None:
                    await self._record_drift_or_challenge(
                        plan=plan,
                        adapter=adapter,
                        step=step,
                        drift_type=drift["drift_type"],
                        status="drift_detected",
                        evidence={**result, "drift": drift},
                        trace_id=trace_id,
                    )
                    await self._finish_execution(
                        execution.adapter_execution_id,
                        status="drift_detected",
                        evidence={"step_id": step.step_id, "drift": drift},
                        error_code=drift["reason_code"],
                        error_summary=drift["message"],
                    )
                    await self._platform_repo.update_plan(
                        plan.plan_id,
                        {
                            "status": "failed",
                            "failure_reason": drift["reason_code"],
                            "updated_at": utc_now_iso(),
                        },
                    )
                    return await self._response(
                        plan.plan_id,
                        adapter=adapter,
                        version=version,
                        execution=await self._execution(execution.adapter_execution_id),
                        discovery=_discovery_with_status(
                            discovery,
                            status="drift_detected",
                            failure_reason=drift["reason_code"],
                            message=drift["message"],
                        ),
                        message=drift["message"],
                        next_step="refresh_adapter_manifest",
                    )
                await self._repo.update_step(
                    step.step_id,
                    {
                        "status": "completed",
                        "approval_id": approval_id if step.requires_approval else step.approval_id,
                        "tool_call_id": result.get("tool_call_id"),
                        "mcp_call_id": result.get("mcp_call_id"),
                        "evidence": result,
                        "updated_at": utc_now_iso(),
                    },
                )
                completed_step_ids.append(step.step_id)
                evidence_items.append(result)
            final_evidence = _final_execution_evidence(
                plan=plan,
                adapter=adapter,
                evidence_items=evidence_items,
                completed_step_ids=completed_step_ids,
            )
            final_status, final_failure_reason, next_step = _final_plan_outcome(
                plan=plan,
                adapter=adapter,
                final_evidence=final_evidence,
            )
            await self._finish_execution(
                execution.adapter_execution_id,
                status=final_status,
                evidence=final_evidence,
                error_code=None if final_status == "completed" else "ADAPTER_VERIFY_INCOMPLETE",
            )
            await self._platform_repo.update_plan(
                plan.plan_id,
                {
                    "status": final_status,
                    "failure_reason": final_failure_reason,
                    "evidence": {
                        **plan.evidence,
                        "adapter_execution": final_evidence,
                        "deliverable": final_evidence.get("deliverable", {}),
                        "engagement_snapshot": final_evidence.get("engagement_snapshot", {}),
                        "recovery_evidence": final_evidence.get("recovery_evidence", {}),
                    },
                    "updated_at": utc_now_iso(),
                },
            )
            if final_status == "completed":
                await self._mark_candidate_success(adapter)
            return await self._response(
                plan.plan_id,
                adapter=adapter,
                version=version,
                execution=await self._execution(execution.adapter_execution_id),
                discovery=_discovery_with_status(
                    discovery,
                    status=final_status,
                    message=(
                        "已按你的确认完成发布。"
                        if final_status == "completed"
                        else "草稿流程执行完毕，但还需要人工确认外部页面状态。"
                    ),
                ),
                message=(
                    "已按你的确认完成发布，并保存了验证证据。"
                    if final_status == "completed"
                    else "草稿流程执行完毕，但缺少足够的发布验证证据，已标记 degraded。"
                ),
                next_step=next_step,
            )
        except Exception as exc:
            await self._finish_execution(
                execution.adapter_execution_id,
                status="failed",
                evidence={"error": str(redact(str(exc))), "completed_steps": completed_step_ids},
                error_code=str(getattr(exc, "code", ErrorCode.TOOL_EXECUTION_FAILED.value)),
                error_summary=str(redact(str(exc))),
            )
            if isinstance(exc, AppError):
                raise
            raise AppError(
                ErrorCode.TOOL_EXECUTION_FAILED,
                "adapter 执行失败",
                status_code=500,
            ) from exc

    async def discover_adapter(
        self,
        plan_id: str,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformAdapterPlanResponse:
        plan = await self._plan(plan_id)
        if plan.status in {
            "awaiting_account",
            "awaiting_clarification",
            "awaiting_intent_clarification",
            "awaiting_target",
        }:
            return await self._response(
                plan_id,
                message="计划还缺少平台或账号信息，不会猜测进入浏览器探索。",
                next_step=plan.status,
            )
        adapter, discovery = await self._discover_adapter_for_plan(plan, trace_id=trace_id)
        version = await self._repo.latest_version(adapter["adapter_id"]) if adapter else None
        return await self._response(
            plan.plan_id,
            adapter=adapter,
            version=version,
            discovery=discovery,
            message=discovery.user_visible_message,
            next_step="execute_adapter" if adapter else _discovery_next_step(discovery),
        )

    async def resume_after_human(
        self,
        plan_id: str,
        request: ExternalPlatformAdapterResumeRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> ExternalPlatformAdapterPlanResponse:
        request = request or ExternalPlatformAdapterResumeRequest()
        plan = await self._plan(plan_id)
        adapter = await self._select_adapter(
            plan,
            adapter_id=request.adapter_id,
            adapter_type=request.adapter_type,
        )
        login_completed = bool(request.human_resolution.get("login_completed"))
        for step in await self._steps(plan.plan_id, adapter["adapter_id"]):
            if step.status in {"awaiting_human", "challenge_detected", "drift_detected"}:
                status = "completed" if login_completed and step.step_name == "handoff_for_login" else "planned"
                await self._repo.update_step(
                    step.step_id,
                    {
                        "status": status,
                        "evidence": {
                            **step.evidence,
                            "human_resolution": _redacted_dict(request.human_resolution),
                        },
                        "updated_at": utc_now_iso(),
                    },
                )
        return await self.execute_adapter(
            plan_id,
            ExternalPlatformAdapterExecuteRequest(
                adapter_id=request.adapter_id,
                adapter_type=request.adapter_type,
                approval_id=request.approval_id,
                force=True,
            ),
            trace_id=trace_id,
        )

    async def _discover_adapter_for_plan(
        self,
        plan: ExternalPlatformActionPlan,
        *,
        trace_id: str | None,
    ) -> tuple[dict[str, Any] | None, ExternalPlatformDiscoveryResult]:
        if not plan.platform_key:
            discovery = ExternalPlatformDiscoveryResult(
                discovery_id=new_id("epdisc"),
                plan_id=plan.plan_id,
                platform_key="",
                action_type=plan.action_type,
                status="failed",
                failure_reason="platform_missing",
                user_visible_message="还缺少平台信息，我不会猜测要发布到哪里。",
            )
            return None, discovery
        candidate = await self._discovery.discover_browser_adapter(plan, trace_id=trace_id)
        discovery = candidate.result
        await self._persist_discovery_result(plan, candidate, trace_id=trace_id)
        if candidate.manifest is None or discovery.status != "draft_prepared":
            return None, discovery
        response = await self.register_adapter(
            ExternalPlatformAdapterCreateRequest(
                platform_key=str(plan.platform_key),
                adapter_type="browser",
                action_type=plan.action_type,
                display_name="Autonomous discovery adapter",
                status="test_only",
                supported_actions=[plan.action_type],
                required_asset_types=["account"],
                allowed_domains=candidate.allowed_domains or [],
                manifest=candidate.manifest,
                version="0.1.0-autonomous",
                metadata={
                    "source": "autonomous_discovery",
                    "candidate_adapter": True,
                    "candidate_kind": "external_platform_adapter",
                    "success_count": 0,
                    "discovery_id": discovery.discovery_id,
                    "plan_id": plan.plan_id,
                    "state": "test_only",
                    "auto_enable_after_successes": 2,
                },
                organization_id=plan.organization_id,
            ),
            trace_id=trace_id or plan.trace_id,
        )
        adapter = await self._repo.get_adapter(response.adapter.adapter_id)
        if adapter is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "discovery adapter 注册后无法读取")
        discovery.adapter_id = adapter["adapter_id"]
        discovery.learned_adapter_manifest = {
            **discovery.learned_adapter_manifest,
            "adapter_id": adapter["adapter_id"],
            "status": adapter["status"],
        }
        await self._persist_discovery_result(plan, candidate, trace_id=trace_id)
        return adapter, discovery

    async def _persist_discovery_result(
        self,
        plan: ExternalPlatformActionPlan,
        candidate: DiscoveryCandidate,
        *,
        trace_id: str | None,
    ) -> None:
        discovery = candidate.result
        status = plan.status
        failure_reason = plan.failure_reason
        if discovery.status in {"failed", "challenge_detected", "drift_detected"}:
            status = "failed"
            failure_reason = discovery.failure_reason
        elif discovery.status == "draft_prepared":
            failure_reason = None
            if status == "failed":
                status = "awaiting_approval" if plan.approval_id else "draft"
        await self._platform_repo.update_plan(
            plan.plan_id,
            {
                "status": status,
                "failure_reason": failure_reason,
                "evidence": {
                    **plan.evidence,
                    "autonomous_browser_discovery": _redacted_dict(
                        discovery.model_dump(mode="json")
                    ),
                },
                "metadata": {
                    **plan.metadata,
                    "autonomous_browser_discovery": {
                        "enabled": True,
                        "status": discovery.status,
                        "source": "autonomous_discovery",
                        "discovery_id": discovery.discovery_id,
                        "candidate_adapter": bool(candidate.manifest),
                    },
                },
                "trace_id": trace_id or plan.trace_id,
                "updated_at": utc_now_iso(),
            },
        )

    async def _mark_candidate_success(self, adapter: dict[str, Any]) -> None:
        metadata = dict(adapter.get("metadata") or {})
        if metadata.get("source") != "autonomous_discovery":
            return
        success_count = int(metadata.get("success_count") or 0) + 1
        metadata["success_count"] = success_count
        metadata["last_success_at"] = utc_now_iso()
        if success_count >= 2:
            metadata["enable_recommended"] = True
            metadata["recommendation_reason"] = "same_platform_action_succeeded_twice"
        await self._repo.update_adapter(
            adapter["adapter_id"],
            {
                "metadata": _redacted_dict(metadata),
                "updated_at": utc_now_iso(),
            },
        )

    async def _adapter_row(self, adapter_id: str) -> dict[str, Any]:
        adapter = await self._repo.get_adapter(adapter_id)
        if adapter is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "adapter 不存在",
                status_code=404,
                details={"reason_code": "adapter_not_found"},
            )
        return adapter

    async def _select_adapter(
        self,
        plan: ExternalPlatformActionPlan,
        *,
        adapter_id: str | None,
        adapter_type: str | None,
    ) -> dict[str, Any]:
        adapter: dict[str, Any] | None
        if adapter_id:
            adapter = await self._adapter_row(adapter_id)
        else:
            if not plan.platform_key:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    "计划缺少 platform_key，无法选择 adapter",
                    status_code=422,
                    details={"reason_code": "platform_missing"},
                )
            adapter = await self._repo.find_active_adapter(
                organization_id=plan.organization_id,
                platform_key=plan.platform_key,
                action_type=plan.action_type,
                adapter_type=adapter_type,
            )
            if adapter is None:
                raise AppError(
                    ErrorCode.NOT_FOUND,
                    "未配置可用 external platform adapter",
                    status_code=404,
                    details={
                        "reason_code": "adapter_not_configured",
                        "platform_key": plan.platform_key,
                        "action_type": plan.action_type,
                        "adapter_type": adapter_type,
                    },
                )
        if adapter is None:
            raise AppError(ErrorCode.NOT_FOUND, "adapter 不存在", status_code=404)
        if adapter["status"] not in {"active", "test_only"}:
            raise AppError(
                ErrorCode.SAFETY_BLOCKED,
                "adapter 未处于可执行状态",
                status_code=409,
                details={"reason_code": f"adapter_{adapter['status']}"},
            )
        if (
            adapter["platform_key"] != plan.platform_key
            or adapter["action_type"] != plan.action_type
        ):
            raise AppError(
                ErrorCode.CONFLICT,
                "adapter 与 action plan 的平台或动作不匹配",
                status_code=409,
                details={"reason_code": "adapter_plan_mismatch"},
            )
        return adapter

    async def _plan(self, plan_id: str) -> ExternalPlatformActionPlan:
        row = await self._platform_repo.get_plan(plan_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "外部平台 action plan 不存在", status_code=404)
        return ExternalPlatformActionPlan(**row)

    async def _steps(self, plan_id: str, adapter_id: str) -> list[ExternalPlatformAdapterStep]:
        return [
            ExternalPlatformAdapterStep(**row)
            for row in await self._repo.list_steps(plan_id, adapter_id=adapter_id)
        ]

    async def _execution(self, execution_id: str) -> ExternalPlatformAdapterExecution | None:
        row = await self._repo.get_execution(execution_id)
        return ExternalPlatformAdapterExecution(**row) if row else None

    async def _compile_step_specs(
        self,
        plan: ExternalPlatformActionPlan,
        adapter: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if adapter["adapter_type"] == "browser":
            return await self._compile_browser_steps(plan, adapter)
        return await self._compile_mcp_steps(adapter)

    async def _compile_browser_steps(
        self,
        plan: ExternalPlatformActionPlan,
        adapter: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if _is_real_xiaohongshu_flow(plan, adapter) and plan.action_type == "publish_content":
            return await _compile_real_xiaohongshu_steps(
                plan,
                adapter,
                browser_sessions=self._browser_sessions,
            )
        manifest = adapter["manifest"]
        flow = _action_flow(manifest, plan.action_type)
        selectors = _manifest_selectors(flow)
        start_url = str(flow.get("start_url") or manifest.get("start_url") or "").strip()
        if not start_url:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "browser adapter manifest 缺少 start_url",
                status_code=422,
                details={"reason_code": "adapter_start_url_missing"},
            )
        content = _content_for_plan(plan, flow)
        session_handle_id = _browser_session_handle(plan, flow, manifest)

        def browser_input(values: dict[str, Any]) -> dict[str, Any]:
            if session_handle_id:
                values["session_handle_id"] = session_handle_id
            if plan.metadata.get("test_account_approval_bypass"):
                values["test_account_approval_bypass"] = True
            provider_mode = str(plan.metadata.get("provider_mode") or "").strip()
            if provider_mode:
                values["provider_mode"] = provider_mode
            return values

        steps: list[dict[str, Any]] = []
        login_flow = _login_flow(manifest, flow)
        if login_flow:
            login_url = str(login_flow.get("login_url") or "").strip() or start_url
            login_selectors = _manifest_selectors(login_flow)
            steps.extend(
                [
                    {
                        "step_name": "login_state_check",
                        "tool_name": "browser.snapshot",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input({"url": login_url, "challenge_check": True}),
                    },
                    {
                        "step_name": "open_login_page",
                        "tool_name": "browser.open",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input({"url": login_url}),
                    },
                ]
            )
            if login_selectors.get("username"):
                steps.append(
                    {
                        "step_name": "fill_login_username",
                        "tool_name": "browser.fill",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input(
                            {
                                "url": login_url,
                                "selector": login_selectors["username"],
                                "value_from": "account_username",
                            }
                        ),
                    }
                )
            if login_selectors.get("password"):
                steps.append(
                    {
                        "step_name": "fill_login_password",
                        "tool_name": "browser.fill",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input(
                            {
                                "url": login_url,
                                "selector": login_selectors["password"],
                                "value_from": "account_secret",
                            }
                        ),
                    }
                )
            if login_selectors.get("submit") or login_selectors.get("form"):
                steps.append(
                    {
                        "step_name": "submit_login",
                        "tool_name": "browser.submit",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input(
                            {
                                "url": login_url,
                                "selector": login_selectors.get("submit") or login_selectors.get("form"),
                                "action": "external_platform_login_submit",
                            }
                        ),
                    }
                )
        steps.append(
            {
                "step_name": "navigate_action_page",
                "tool_name": "browser.open",
                "risk_level": "R2",
                "requires_approval": False,
                "input": browser_input({"url": start_url}),
            }
        )
        if plan.action_type == "publish_content" and selectors.get("title"):
            steps.append(
                {
                    "step_name": "fill_title",
                    "tool_name": "browser.fill",
                    "risk_level": "R2",
                    "requires_approval": False,
                    "input": browser_input(
                        {
                            "url": start_url,
                            "selector": selectors["title"],
                            "value": content["title"],
                        }
                    ),
                }
            )
        if plan.action_type == "publish_content" and selectors.get("body"):
            steps.append(
                {
                    "step_name": "fill_body",
                    "tool_name": "browser.fill",
                    "risk_level": "R2",
                    "requires_approval": False,
                    "input": browser_input(
                        {
                            "url": start_url,
                            "selector": selectors["body"],
                            "value": content["body"],
                        }
                    ),
                }
            )
        if plan.action_type == "publish_content" and selectors.get("tags") and content.get("tags"):
            steps.append(
                {
                    "step_name": "fill_tags",
                    "tool_name": "browser.fill",
                    "risk_level": "R2",
                    "requires_approval": False,
                    "input": browser_input(
                        {
                            "url": start_url,
                            "selector": selectors["tags"],
                            "value": ", ".join(content["tags"]),
                        }
                    ),
                }
            )
        target_post_url = str(
            plan.metadata.get("target_post_url")
            or flow.get("target_post_url")
            or (flow.get("verify") or {}).get("expected_url")
            or start_url
        )
        action_url = start_url if plan.action_type == "publish_content" else target_post_url
        if plan.action_type == "comment_content":
            if selectors.get("target_post"):
                steps.append(
                    {
                        "step_name": "locate_post",
                        "tool_name": "browser.click",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input({"url": start_url, "selector": selectors["target_post"]}),
                    }
                )
            else:
                steps.append(
                    {
                        "step_name": "locate_post",
                        "tool_name": "browser.open",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input({"url": target_post_url}),
                    }
                )
            if selectors.get("comment_box"):
                steps.append(
                    {
                        "step_name": "open_comment_box",
                        "tool_name": "browser.click",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input({"url": target_post_url, "selector": selectors["comment_box"]}),
                    }
                )
            if selectors.get("comment_input"):
                steps.append(
                    {
                        "step_name": "fill_comment",
                        "tool_name": "browser.fill",
                        "risk_level": "R2",
                        "requires_approval": False,
                        "input": browser_input(
                            {
                                "url": target_post_url,
                                "selector": selectors["comment_input"],
                                "value": content["comment_text"],
                            }
                        ),
                    }
                )
        submit_step_name = "submit_comment" if plan.action_type == "comment_content" else "submit_publish"
        submit_action = (
            "external_platform_comment_submit"
            if plan.action_type == "comment_content"
            else "external_platform_publish_submit"
        )
        submit_selector = (
            selectors.get("comment_submit") or selectors.get("comment_form")
            if plan.action_type == "comment_content"
            else selectors.get("submit") or selectors.get("form")
        )
        steps.extend(
            [
                {
                    "step_name": "pre_submit_snapshot",
                    "tool_name": "browser.snapshot",
                    "risk_level": "R2",
                    "requires_approval": False,
                    "input": browser_input({"url": action_url, "evidence": "pre_submit_snapshot"}),
                },
                {
                    "step_name": submit_step_name,
                    "tool_name": "browser.submit",
                    "risk_level": "R3" if plan.action_type == "comment_content" else "R5",
                    "requires_approval": _approval_required_for_plan(plan),
                    "input": browser_input(
                        {
                            "url": action_url,
                            "selector": submit_selector,
                            "action": submit_action,
                        }
                    ),
                },
                {
                    "step_name": "verify_result",
                    "tool_name": "browser.snapshot",
                    "risk_level": "R2",
                    "requires_approval": False,
                    "input": browser_input(
                        {
                            "url": str(
                                (flow.get("verify") or {}).get("expected_url")
                                or flow.get("verify_url")
                                or action_url
                            ),
                            "expected_text": (
                                content["comment_text"]
                                if plan.action_type == "comment_content"
                                else content["body"]
                            ),
                            "verification": _redacted_dict(flow.get("verify") or {}),
                        }
                    ),
                },
            ]
        )
        return steps

    async def _compile_mcp_steps(self, adapter: dict[str, Any]) -> list[dict[str, Any]]:
        manifest = adapter["manifest"]
        tool_map = dict(manifest.get("tool_map") or {})
        if not tool_map.get("submit"):
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "MCP adapter manifest 缺少 submit tool_map",
                status_code=422,
                details={"reason_code": "mcp_submit_tool_missing"},
            )
        steps: list[dict[str, Any]] = []
        if tool_map.get("prepare"):
            await self._tools.get_tool(str(tool_map["prepare"]))
            steps.append(
                {
                    "step_name": "prepare_publish",
                    "tool_name": str(tool_map["prepare"]),
                    "risk_level": "R2",
                    "requires_approval": False,
                    "input": {"phase50_adapter_step": "prepare_publish"},
                }
            )
        await self._tools.get_tool(str(tool_map["submit"]))
        steps.append(
            {
                "step_name": "submit_publish",
                "tool_name": str(tool_map["submit"]),
                "risk_level": "R4",
                "requires_approval": True,
                "input": {"phase50_adapter_step": "submit_publish"},
            }
        )
        if tool_map.get("verify"):
            await self._tools.get_tool(str(tool_map["verify"]))
            steps.append(
                {
                    "step_name": "verify_publish",
                    "tool_name": str(tool_map["verify"]),
                    "risk_level": "R2",
                    "requires_approval": False,
                    "input": {"phase50_adapter_step": "verify_publish"},
                }
            )
        return steps

    async def _start_execution(
        self,
        plan: ExternalPlatformActionPlan,
        adapter: dict[str, Any],
        version: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> ExternalPlatformAdapterExecution:
        now = utc_now_iso()
        data = {
            "adapter_execution_id": new_id("epadx"),
            "plan_id": plan.plan_id,
            "adapter_id": adapter["adapter_id"],
            "adapter_version_id": version["adapter_version_id"],
            "status": "running",
            "executor": adapter["adapter_type"],
            "started_at": now,
            "completed_at": None,
            "evidence": {
                "plan_id": plan.plan_id,
                "adapter_id": adapter["adapter_id"],
                "adapter_version_id": version["adapter_version_id"],
                "secret_material_visible": False,
            },
            "trace_id": trace_id or plan.trace_id,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_execution(data)
        return ExternalPlatformAdapterExecution(**data)

    async def _start_or_resume_execution(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        adapter: dict[str, Any],
        version: dict[str, Any],
        force: bool,
        trace_id: str | None,
    ) -> ExternalPlatformAdapterExecution:
        if force:
            rows = await self._repo.list_executions(plan.plan_id)
            if rows:
                latest = ExternalPlatformAdapterExecution(**rows[-1])
                if latest.status == "awaiting_human":
                    await self._repo.update_execution(
                        latest.adapter_execution_id,
                        {
                            "status": "running",
                            "completed_at": None,
                            "updated_at": utc_now_iso(),
                        },
                    )
                    resumed = await self._execution(latest.adapter_execution_id)
                    if resumed is not None:
                        return resumed
        return await self._start_execution(plan, adapter, version, trace_id=trace_id)

    async def _finish_execution(
        self,
        execution_id: str,
        *,
        status: str,
        evidence: dict[str, Any],
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        await self._repo.update_execution(
            execution_id,
            {
                "status": status,
                "completed_at": utc_now_iso(),
                "evidence": _redacted_dict(evidence),
                "error_code": error_code,
                "error_summary": str(redact(error_summary)) if error_summary else None,
                "updated_at": utc_now_iso(),
            },
        )

    async def _approval_status(self, approval_id: str | None) -> str:
        if not approval_id:
            return "missing"
        approval = await self._approvals.get(approval_id)
        if approval.status in {"approved", "edited"}:
            return "approved"
        return str(approval.status)

    async def _execute_step(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        adapter: dict[str, Any],
        step: ExternalPlatformAdapterStep,
        approval_id: str | None,
        current_url: str | None,
        evidence_items: list[dict[str, Any]],
        trace_id: str | None,
    ) -> dict[str, Any]:
        if not step.tool_name:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "adapter step 缺少 tool_name",
                status_code=422,
            )
        args = dict(step.input_redacted)
        if adapter["adapter_type"] == "browser":
            args = _resolve_runtime_browser_refs(args, evidence_items=evidence_items)
            args = self._browser_args(args, current_url=current_url)
            args = await self._inject_runtime_browser_values(
                plan=plan,
                step=step,
                args=args,
                trace_id=trace_id,
            )
        if adapter["adapter_type"] == "mcp":
            args = {
                **args,
                "text": plan.content_summary or plan.action_type,
                "platform_key": plan.platform_key,
                "action_type": plan.action_type,
                "content_summary": plan.content_summary,
            }
        response = await self._tools.execute(
            ToolExecuteRequest(
                task_id=plan.task_id,
                member_id=plan.member_id,
                tool_name=step.tool_name,
                args=args,
                approval_id=approval_id,
                idempotency_key=f"phase50:{plan.plan_id}:{step.step_id}",
            ),
            trace_id=trace_id or plan.trace_id,
        )
        tool_call = response.tool_call
        result = _redacted_dict(response.result)
        mcp_call_id = _mcp_call_id(result)
        return {
            "plan_id": plan.plan_id,
            "adapter_id": adapter["adapter_id"],
            "step_id": step.step_id,
            "step_name": step.step_name,
            "executor": adapter["adapter_type"],
            "tool_name": step.tool_name,
            "tool_call_id": tool_call.tool_call_id,
            "mcp_call_id": mcp_call_id,
            "approval_id": approval_id if step.requires_approval else None,
            "input_redacted": _redacted_step_input(step=step, args=args),
            "output_redacted": result,
            "artifact_refs": [item.artifact_id for item in response.artifacts],
            "evidence_refs": _evidence_refs(result),
            "secret_material_visible": False,
        }

    def _browser_args(self, args: dict[str, Any], *, current_url: str | None) -> dict[str, Any]:
        url = str(args.get("url") or current_url or "").strip()
        if not url:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "browser adapter step 缺少 URL",
                status_code=422,
                details={"reason_code": "browser_url_missing"},
            )
        updated = {**args, "url": url}
        return {key: value for key, value in updated.items() if value is not None}

    async def _inject_runtime_browser_values(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        step: ExternalPlatformAdapterStep,
        args: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any]:
        value_from = str(args.get("value_from") or "").strip()
        if not value_from:
            return args
        resolved = await self._asset_broker.resolve_secret_for_tool(
            str(plan.selected_handle_id or ""),
            AssetResolveForToolRequest(
                subject_id=plan.member_id,
                action="login",
                tool_name=step.tool_name or "browser.fill",
                task_id=plan.task_id,
                conversation_id=plan.conversation_id,
                approval_id=plan.approval_id,
            ),
            trace_id=trace_id or plan.trace_id,
        )
        base_resource = resolved.resolved.resource
        resource = base_resource if isinstance(base_resource, dict) else {}
        config = resource.get("config") if isinstance(resource.get("config"), dict) else {}
        updated = dict(args)
        if value_from == "account_username":
            updated["value"] = str(config.get("username") or "")
        elif value_from == "account_secret":
            updated["value"] = resolved.secret_value or ""
        updated.pop("value_from", None)
        return updated

    async def _mark_browser_session_health(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        result: dict[str, Any],
        observed_status: str,
        failure_reason: str | None,
        recovery_hint: str | None,
    ) -> dict[str, Any]:
        browser_session_id = str(plan.metadata.get("browser_session_id") or "").strip()
        if not browser_session_id:
            return result
        try:
            health = await self._browser_sessions.health_check_session(
                browser_session_id,
                BrowserSessionHealthCheckRequest(
                    probe_type="adapter_runtime",
                    observed_status=observed_status,
                    failure_reason=failure_reason,
                    recovery_hint=recovery_hint,
                    evidence={
                        "step_name": result.get("step_name"),
                        "action_status": (
                            (result.get("output_redacted") or {}).get("action_status")
                            if isinstance(result.get("output_redacted"), dict)
                            else None
                        ),
                    },
                ),
                trace_id=plan.trace_id,
            )
        except Exception:
            return result
        return _annotate_result(
            result,
            {
                "browser_session_health": {
                    "browser_session_id": browser_session_id,
                    "health_status": health.browser_session.health_status,
                    "login_state": health.browser_session.login_state,
                    "probe_id": health.probe.probe_id,
                }
            },
        )

    async def _attempt_challenge_auto_remediation(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        adapter: dict[str, Any],
        step: ExternalPlatformAdapterStep,
        result: dict[str, Any],
        challenge: dict[str, str],
        trace_id: str | None,
    ) -> dict[str, Any] | None:
        if not _auto_challenge_remediation_enabled(plan=plan, adapter=adapter, challenge=challenge):
            return None
        current_url = _evidence_url(result) or str(step.input_redacted.get("url") or "").strip()
        if not current_url:
            return None
        attempts: list[dict[str, Any]] = []
        for action in _challenge_auto_actions(adapter["manifest"], current_url=current_url):
            remediation_result = await self._execute_browser_remediation_action(
                plan=plan,
                step=step,
                action=action,
                trace_id=trace_id,
            )
            attempts.append(remediation_result)
            output = remediation_result.get("output_redacted") if isinstance(remediation_result, dict) else {}
            if isinstance(output, dict) and str(output.get("action_status") or "").lower() == "completed":
                probe = await self._execute_browser_remediation_action(
                    plan=plan,
                    step=step,
                    action={
                        "tool_name": "browser.snapshot",
                        "args": {
                            "url": current_url,
                            "challenge_check": True,
                            "provider_mode": step.input_redacted.get("provider_mode"),
                            "playwright_required": step.input_redacted.get("playwright_required"),
                            "session_handle_id": step.input_redacted.get("session_handle_id"),
                            "success_selectors": step.input_redacted.get("success_selectors", []),
                            "authenticated_if_url_contains": step.input_redacted.get(
                                "authenticated_if_url_contains", []
                            ),
                            "not_authenticated_if_url_contains": step.input_redacted.get(
                                "not_authenticated_if_url_contains", []
                            ),
                        },
                    },
                    trace_id=trace_id,
                )
                probe = _annotate_result(
                    probe,
                    {
                        "challenge_auto_remediation": {
                            "attempted": True,
                            "resolved": self._detect_challenge(adapter["manifest"], probe, step=step)
                            is None,
                            "attempts": attempts,
                        }
                    },
                )
                if self._detect_challenge(adapter["manifest"], probe, step=step) is None:
                    return probe
        if attempts:
            return _annotate_result(
                result,
                {
                    "challenge_auto_remediation": {
                        "attempted": True,
                        "resolved": False,
                        "attempts": attempts,
                    }
                },
            )
        return None

    async def _execute_browser_remediation_action(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        step: ExternalPlatformAdapterStep,
        action: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, Any]:
        tool_name = str(action.get("tool_name") or "browser.click").strip() or "browser.click"
        args = dict(action.get("args") or {})
        args = self._browser_args(args, current_url=str(step.input_redacted.get("url") or "").strip())
        response = await self._tools.execute(
            ToolExecuteRequest(
                task_id=plan.task_id,
                member_id=plan.member_id,
                tool_name=tool_name,
                args=args,
                approval_id=plan.approval_id,
                idempotency_key=f"phase50:challenge-remediation:{plan.plan_id}:{step.step_id}:{_stable_json(args)}",
            ),
            trace_id=trace_id or plan.trace_id,
        )
        tool_call = response.tool_call
        result = _redacted_dict(response.result)
        return {
            "plan_id": plan.plan_id,
            "adapter_id": step.adapter_id,
            "step_id": step.step_id,
            "step_name": f"{step.step_name}:challenge_remediation",
            "executor": "browser",
            "tool_name": tool_name,
            "tool_call_id": tool_call.tool_call_id,
            "approval_id": None,
            "input_redacted": _redacted_dict(args),
            "output_redacted": result,
            "artifact_refs": [item.artifact_id for item in response.artifacts],
            "evidence_refs": _evidence_refs(result),
            "secret_material_visible": False,
        }

    async def _record_drift_or_challenge(
        self,
        *,
        plan: ExternalPlatformActionPlan,
        adapter: dict[str, Any],
        step: ExternalPlatformAdapterStep,
        drift_type: str,
        status: str,
        evidence: dict[str, Any],
        trace_id: str | None,
    ) -> None:
        await self._repo.update_step(
            step.step_id,
            {
                "status": status,
                "evidence": _redacted_dict(evidence),
                "updated_at": utc_now_iso(),
            },
        )
        await self._repo.insert_drift_event(
            {
                "drift_event_id": new_id("epadd"),
                "plan_id": plan.plan_id,
                "adapter_id": adapter["adapter_id"],
                "step_id": step.step_id,
                "drift_type": drift_type,
                "status": status,
                "evidence": _redacted_dict(evidence),
                "trace_id": trace_id or plan.trace_id,
                "created_at": utc_now_iso(),
            }
        )

    def _detect_challenge(
        self,
        manifest: dict[str, Any],
        result: dict[str, Any],
        *,
        step: ExternalPlatformAdapterStep,
    ) -> dict[str, str] | None:
        challenge = dict(manifest.get("challenge_detection") or {})
        texts = [str(item).lower() for item in challenge.get("any_text") or [] if str(item)]
        not_logged_in = [
            str(item).lower() for item in challenge.get("not_logged_in_text") or [] if str(item)
        ]
        serialized = json.dumps(result, ensure_ascii=False).lower()
        for text in not_logged_in:
            if text and text in serialized:
                return {
                    "drift_type": "login_required",
                    "status": "failed",
                    "reason_code": "login_required",
                    "message": "adapter 检测到未登录状态，已停止自动执行。",
                }
        for text in texts:
            if text and text in serialized:
                if step.step_name.startswith("login_") or step.step_name == "submit_login":
                    return {
                        "drift_type": "challenge_detected",
                        "status": "challenge_detected",
                        "reason_code": "login_verification_required",
                        "message": "adapter 检测到登录验证或风控提示，已 fail closed。",
                    }
                return {
                    "drift_type": "challenge_detected",
                    "status": "challenge_detected",
                    "reason_code": "adapter_challenge_detected",
                    "message": "adapter 检测到验证码、二次验证或风控提示，已 fail closed。",
                }
        return None

    def _detect_drift(self, result: dict[str, Any]) -> dict[str, str] | None:
        output = result.get("output_redacted") if isinstance(result, dict) else {}
        if not isinstance(output, dict):
            return None
        action_status = str(output.get("action_status") or "").lower()
        if action_status in {"not_found", "failed", "unsupported"}:
            return {
                "drift_type": "selector_or_page_drift",
                "reason_code": "adapter_drift_detected",
                "message": "adapter step 未找到预期页面元素或页面已漂移，已停止。",
            }
        return None

    async def _fail_without_execution(
        self,
        plan: ExternalPlatformActionPlan,
        *,
        adapter: dict[str, Any],
        version: dict[str, Any],
        reason_code: str,
        message: str,
        trace_id: str | None,
    ) -> ExternalPlatformAdapterPlanResponse:
        execution = await self._start_execution(plan, adapter, version, trace_id=trace_id)
        await self._finish_execution(
            execution.adapter_execution_id,
            status="failed",
            evidence={"reason_code": reason_code, "submit_executed": False},
            error_code=reason_code.upper(),
            error_summary=message,
        )
        return await self._response(
            plan.plan_id,
            adapter=adapter,
            version=version,
            execution=await self._execution(execution.adapter_execution_id),
            message=message,
            next_step="create_task_and_approval",
        )

    async def _response(
        self,
        plan_id: str,
        *,
        adapter: dict[str, Any] | None = None,
        version: dict[str, Any] | None = None,
        execution: ExternalPlatformAdapterExecution | None = None,
        steps: list[ExternalPlatformAdapterStep] | None = None,
        discovery: ExternalPlatformDiscoveryResult | None = None,
        message: str,
        next_step: str | None,
    ) -> ExternalPlatformAdapterPlanResponse:
        plan = await self._plan(plan_id)
        if adapter is not None and steps is None:
            steps = await self._steps(plan_id, adapter["adapter_id"])
        drift_events = [
            ExternalPlatformAdapterDriftEvent(**row)
            for row in await self._repo.list_drift_events(plan_id)
        ]
        return ExternalPlatformAdapterPlanResponse(
            plan=plan,
            adapter=ExternalPlatformAdapter(**adapter) if adapter else None,
            version=ExternalPlatformAdapterVersion(**version) if version else None,
            execution=execution,
            steps=steps or [],
            drift_events=drift_events,
            discovery=discovery,
            message=message,
            next_step=next_step,
        )

    def _validate_manifest(
        self,
        *,
        manifest: dict[str, Any],
        adapter_type: str,
        action_type: str,
        allowed_domains: list[str],
        status: str,
    ) -> ExternalPlatformAdapterValidateResponse:
        issues: list[dict[str, Any]] = []
        if adapter_type not in ADAPTER_TYPES:
            issues.append({"severity": "fatal", "code": "adapter_type_invalid"})
        if status not in ADAPTER_STATUSES:
            issues.append({"severity": "fatal", "code": "adapter_status_invalid"})
        if action_type not in {"publish_content", "comment_content"}:
            issues.append({"severity": "warning", "code": "action_not_phase50_primary"})
        issues.extend(_secret_manifest_issues(manifest))
        if adapter_type == "browser":
            flow = _action_flow(manifest, action_type)
            domains = allowed_domains or _manifest_allowed_domains(manifest)
            if not domains:
                issues.append({"severity": "error", "code": "allowed_domains_missing"})
            if not (flow.get("start_url") or manifest.get("start_url")):
                issues.append({"severity": "error", "code": "start_url_missing"})
            selectors = _manifest_selectors(flow)
            if not (selectors.get("submit") or selectors.get("form")):
                issues.append({"severity": "error", "code": "submit_selector_missing"})
            if (
                action_type == "publish_content"
                and bool(manifest.get("real_site_flow"))
            ):
                comment_flow = manifest.get("comment_flow")
                if isinstance(comment_flow, dict):
                    comment_selectors = _manifest_selectors(comment_flow)
                    if not (
                        comment_selectors.get("comment_submit")
                        or comment_selectors.get("comment_form")
                        or comment_selectors.get("submit")
                        or comment_selectors.get("form")
                    ):
                        issues.append(
                            {"severity": "warning", "code": "comment_submit_selector_missing"}
                        )
        if adapter_type == "mcp":
            tool_map = manifest.get("tool_map") or {}
            if not isinstance(tool_map, dict) or not tool_map.get("submit"):
                issues.append({"severity": "error", "code": "mcp_submit_tool_missing"})
        valid = not any(item.get("severity") in {"fatal", "error"} for item in issues)
        return ExternalPlatformAdapterValidateResponse(
            valid=valid,
            status="valid" if valid else "invalid",
            issues=issues,
            message="adapter manifest 可用。" if valid else "adapter manifest 需要修正。",
        )


def _is_adapter_not_configured(exc: AppError) -> bool:
    details = getattr(exc, "details", None)
    return (
        exc.code == ErrorCode.NOT_FOUND.value
        and isinstance(details, dict)
        and details.get("reason_code") == "adapter_not_configured"
    )


def _discovery_with_status(
    discovery: ExternalPlatformDiscoveryResult | None,
    *,
    status: str,
    message: str,
    failure_reason: str | None = None,
) -> ExternalPlatformDiscoveryResult | None:
    if discovery is None:
        return None
    discovery.status = status
    discovery.user_visible_message = message
    if failure_reason is not None:
        discovery.failure_reason = failure_reason
    return discovery


def _discovery_next_step(discovery: ExternalPlatformDiscoveryResult | None) -> str | None:
    if discovery is None:
        return None
    if discovery.status == "challenge_detected":
        return "human_login_or_resume"
    if discovery.status in {"failed", "drift_detected"}:
        return "provide_publish_url_or_configure_adapter"
    if discovery.status == "draft_prepared":
        return "execute_adapter"
    if discovery.status == "awaiting_approval":
        return "approve_or_resume_after_human"
    return None


def _action_flow(manifest: dict[str, Any], action_type: str) -> dict[str, Any]:
    actions = manifest.get("actions")
    if isinstance(actions, dict) and isinstance(actions.get(action_type), dict):
        return dict(actions[action_type])
    if action_type == "comment_content":
        flow = manifest.get("comment_flow")
        if isinstance(flow, dict):
            return dict(flow)
    flow = manifest.get("publish_flow")
    return dict(flow) if isinstance(flow, dict) else dict(manifest)


def _login_flow(manifest: dict[str, Any], flow: dict[str, Any]) -> dict[str, Any]:
    if isinstance(flow.get("login_flow"), dict):
        return dict(flow["login_flow"])
    if isinstance(manifest.get("login_flow"), dict):
        return dict(manifest["login_flow"])
    return {}


def _manifest_selectors(flow: dict[str, Any]) -> dict[str, str]:
    raw = flow.get("selectors") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items() if value is not None}


def _manifest_allowed_domains(manifest: dict[str, Any]) -> list[str]:
    domains = manifest.get("allowed_domains") or manifest.get("domains") or []
    return [str(item).lower() for item in domains if str(item).strip()]


_PUBLISHED_POST_URL_REF = "__published_post_url__"


def _browser_session_handle(
    plan: ExternalPlatformActionPlan,
    flow: dict[str, Any],
    manifest: dict[str, Any],
) -> str | None:
    for source in (plan.metadata, plan.evidence, flow, manifest):
        if not isinstance(source, dict):
            continue
        value = str(
            source.get("session_handle_id")
            or source.get("browser_session_handle_id")
            or ""
        ).strip()
        if value:
            return value
    return None


async def _browser_session_authenticated(
    plan: ExternalPlatformActionPlan,
    browser_sessions: BrowserSessionService,
) -> bool:
    browser_session_id = str(plan.metadata.get("browser_session_id") or "").strip()
    if not browser_session_id:
        return False
    try:
        session = await browser_sessions.get_session(browser_session_id)
    except AppError:
        return False
    return session.status == "active" and session.login_state == "authenticated"


async def _browser_session_preflight_for_plan(
    plan: ExternalPlatformActionPlan,
    browser_sessions: BrowserSessionService,
) -> dict[str, Any]:
    browser_session_id = str(plan.metadata.get("browser_session_id") or "").strip()
    if not browser_session_id:
        return browser_session_preflight(
            session_status=None,
            health_status=None,
            login_state=None,
            execution_backend=str(plan.metadata.get("provider_mode") or "playwright_ephemeral"),
            identity_binding_status=str(plan.metadata.get("identity_binding_status") or "unbound"),
            login_capture_mode=str(plan.metadata.get("login_capture_mode") or "manual_handoff"),
        )
    try:
        session = await browser_sessions.get_session(browser_session_id)
    except AppError:
        return browser_session_preflight(
            session_status="degraded",
            health_status="identity_unavailable",
            login_state="unknown",
            execution_backend=str(plan.metadata.get("provider_mode") or "playwright_ephemeral"),
            identity_binding_status=str(plan.metadata.get("identity_binding_status") or "unbound"),
            login_capture_mode=str(plan.metadata.get("login_capture_mode") or "manual_handoff"),
        )
    return browser_session_preflight(
        session_status=session.status,
        health_status=session.health_status,
        login_state=session.login_state,
        execution_backend=session.execution_backend,
        identity_binding_status=session.identity_binding_status,
        login_capture_mode=session.login_capture_mode,
    )


def _content_for_plan(plan: ExternalPlatformActionPlan, flow: dict[str, Any]) -> dict[str, Any]:
    body = str(
        plan.metadata.get("publish_text") or plan.content_summary or "外部平台发布内容"
    ).strip()
    title = str(flow.get("default_title") or body[:60] or "外部平台发布").strip()
    tags = flow.get("default_tags") or []
    comment_text = str(
        plan.metadata.get("comment_text") or plan.content_summary or flow.get("default_comment") or "已测试通过"
    ).strip()
    return {
        "title": title,
        "body": body,
        "tags": [str(item) for item in tags],
        "comment_text": comment_text,
    }


def _approval_required_for_plan(plan: ExternalPlatformActionPlan) -> bool:
    return not bool(plan.metadata.get("test_account_approval_bypass"))


def _workflow_skill_spec(
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any],
) -> dict[str, Any] | None:
    for key in ("external_platform_skill", "content_platform_skill"):
        evidence = plan.evidence.get(key) if isinstance(plan.evidence, dict) else None
        if not isinstance(evidence, dict):
            continue
        workflow = evidence.get("workflow_spec")
        if isinstance(workflow, dict):
            return dict(workflow)
    manifest = adapter.get("manifest") if isinstance(adapter.get("manifest"), dict) else {}
    workflow = manifest.get("workflow_spec") if isinstance(manifest, dict) else None
    return dict(workflow) if isinstance(workflow, dict) else None


def _is_real_xiaohongshu_flow(plan: ExternalPlatformActionPlan, adapter: dict[str, Any]) -> bool:
    metadata = adapter.get("metadata") if isinstance(adapter.get("metadata"), dict) else {}
    manifest = adapter.get("manifest") if isinstance(adapter.get("manifest"), dict) else {}
    workflow = _workflow_skill_spec(plan, adapter)
    return bool(
        plan.metadata.get("provider_mode") == "playwright"
        or metadata.get("real_platform_integration")
        or metadata.get("playwright_required")
        or manifest.get("real_site_flow")
        or (isinstance(workflow, dict) and workflow.get("real_browser_workflow"))
    )


def _challenge_waits_for_human(plan: ExternalPlatformActionPlan, adapter: dict[str, Any]) -> bool:
    metadata = adapter.get("metadata") if isinstance(adapter.get("metadata"), dict) else {}
    return _is_real_xiaohongshu_flow(plan, adapter) or bool(metadata.get("human_challenge_resume"))


async def _compile_real_xiaohongshu_steps(
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any],
    *,
    browser_sessions: BrowserSessionService,
) -> list[dict[str, Any]]:
    manifest = adapter["manifest"]
    workflow = _workflow_skill_spec(plan, adapter) or {}
    flow = dict(workflow.get("publish_flow") or _action_flow(manifest, plan.action_type))
    comment_flow = workflow.get("comment_flow")
    if isinstance(comment_flow, dict):
        comment_flow = dict(comment_flow)
    else:
        manifest_comment_flow = manifest.get("comment_flow")
        comment_flow = dict(manifest_comment_flow) if isinstance(manifest_comment_flow, dict) else {}
    login_flow = workflow.get("login_flow")
    login_flow = dict(login_flow) if isinstance(login_flow, dict) else _login_flow(manifest, flow)
    content = _content_for_plan(plan, flow)
    login_url = str(login_flow.get("login_url") or flow.get("login_url") or "").strip()
    start_url = str(flow.get("start_url") or manifest.get("start_url") or "").strip()
    target_post_url = str(
        plan.metadata.get("target_post_url")
        or flow.get("target_post_url")
        or (flow.get("verify") or {}).get("expected_url")
        or ""
    ).strip()
    login_selectors = _manifest_selectors(login_flow)
    selectors = _manifest_selectors(flow)
    comment_selectors = {**selectors, **_manifest_selectors(comment_flow)}
    comment_entry_url = str(
        comment_flow.get("start_url")
        or comment_flow.get("target_post_url")
        or (comment_flow.get("verify") or {}).get("expected_url")
        or target_post_url
    ).strip() or target_post_url
    comment_recheck_url = str(
        comment_flow.get("recheck_url")
        or comment_flow.get("target_post_url")
        or (comment_flow.get("verify") or {}).get("expected_url")
        or comment_entry_url
    ).strip() or target_post_url
    require_comment_flow = bool(
        plan.metadata.get("require_full_comment_flow")
        or plan.metadata.get("publish_and_comment_both_required")
    )
    if require_comment_flow and not target_post_url:
        if comment_entry_url == start_url:
            comment_entry_url = ""
        if comment_recheck_url == start_url:
            comment_recheck_url = ""
    provider_mode = str(plan.metadata.get("provider_mode") or "local_cdp").strip() or "local_cdp"
    if not login_url or not start_url:
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "小红书真实 Playwright 流缺少 login_url 或 start_url",
            status_code=422,
            details={"reason_code": "xiaohongshu_real_flow_missing_url"},
        )

    session_handle_id = _browser_session_handle(plan, flow, manifest)
    session_authenticated = await _browser_session_authenticated(plan, browser_sessions)
    session_preflight = await _browser_session_preflight_for_plan(plan, browser_sessions)
    session_bootstrap_status = str(plan.metadata.get("session_bootstrap_status") or "").strip()

    def step(
        step_name: str,
        tool_name: str,
        values: dict[str, Any],
        *,
        risk: str | None = None,
        approval: bool | None = None,
    ) -> dict[str, Any]:
        browser_policy = browser_action_policy(tool_name, values)
        payload = {
            **values,
            "provider_mode": provider_mode,
            "playwright_required": provider_mode == "playwright",
            "required_backend_capabilities": list(browser_policy.backend_capabilities),
            "session_state": session_preflight["session_state"],
            "login_reuse_allowed": session_preflight["login_reuse_allowed"],
        }
        if step_name in {
            "open_login_page",
            "fill_login_username",
            "fill_login_password",
            "submit_login",
            "detect_login_challenge",
        }:
            payload["allow_login_recovery"] = True
        if session_handle_id:
            payload["session_handle_id"] = session_handle_id
        step_risk = risk or browser_policy.default_risk_level.value
        step_requires_approval = (
            approval
            if approval is not None
            else (
                _approval_required_for_plan(plan)
                and any(
                    control in {"approval", "strong_approval"}
                    for control in browser_policy.required_controls
                )
            )
        )
        return {
            "step_name": step_name,
            "tool_name": tool_name,
            "risk_level": step_risk,
            "requires_approval": step_requires_approval,
            "input": payload,
        }

    publish_health_selectors = [
        value
        for value in (
            selectors.get("title"),
            selectors.get("body"),
            selectors.get("submit") or selectors.get("form"),
        )
        if value
    ]
    steps: list[dict[str, Any]] = []
    if session_handle_id and session_bootstrap_status == "reused":
        steps.extend(
            [
                step(
                    "resolve_browser_identity",
                    "browser.open",
                    {
                        "url": start_url,
                        "wait_until": str(flow.get("wait_until") or "domcontentloaded"),
                    },
                ),
                step(
                    "check_login_state",
                    "browser.snapshot",
                    {
                        "url": start_url,
                        "challenge_check": True,
                        "login_state_probe": True,
                        "success_selectors": publish_health_selectors,
                        "authenticated_if_url_contains": ["/publish", "/creator", "/notes"],
                        "not_authenticated_if_url_contains": ["/login"],
                        "session_authenticated_hint": session_authenticated,
                    },
                ),
            ]
        )
    if provider_mode == "local_cdp":
        steps.extend(
            [
                step("handoff_for_login", "browser.open", {"url": login_url}),
                step(
                    "resume_after_login",
                    "browser.snapshot",
                    {
                        "url": start_url,
                        "challenge_check": True,
                        "login_state_probe": True,
                        "success_selectors": publish_health_selectors,
                        "authenticated_if_url_contains": ["/publish", "/creator", "/notes"],
                        "not_authenticated_if_url_contains": ["/login"],
                    },
                ),
                step(
                    "open_publish_entry",
                    "browser.open",
                    {
                        "url": start_url,
                        "wait_until": str(flow.get("wait_until") or "domcontentloaded"),
                    },
                ),
                step(
                    "verify_publish_editor_loaded",
                    "browser.snapshot",
                    {
                        "url": start_url,
                        "selector_healthcheck": publish_health_selectors,
                        "publish_editor_check": True,
                    },
                ),
            ]
        )
    else:
        steps.extend(
            [
                step("open_login_page", "browser.open", {"url": login_url}),
                step(
                    "fill_login_username",
                    "browser.fill",
                    {
                        "url": login_url,
                        "selector": login_selectors.get("username"),
                        "value_from": "account_username",
                    },
                ),
                step(
                    "fill_login_password",
                    "browser.fill",
                    {
                        "url": login_url,
                        "selector": login_selectors.get("password"),
                        "value_from": "account_secret",
                    },
                ),
                step(
                    "submit_login",
                    "browser.submit",
                    {
                        "url": login_url,
                        "selector": login_selectors.get("submit") or login_selectors.get("form"),
                        "action": "external_platform_login_submit",
                        "wait_for_url": str(login_flow.get("post_login_wait_url") or "").strip() or None,
                        "wait_for_text": str(login_flow.get("post_login_wait_text") or "").strip() or None,
                    },
                ),
                step(
                    "detect_login_challenge",
                    "browser.snapshot",
                    {
                        "url": start_url,
                        "challenge_check": True,
                        "success_selectors": publish_health_selectors,
                    },
                ),
                step(
                    "open_publish_entry",
                    "browser.open",
                    {
                        "url": start_url,
                        "wait_until": str(flow.get("wait_until") or "domcontentloaded"),
                    },
                ),
                step(
                    "verify_publish_editor_loaded",
                    "browser.snapshot",
                    {
                        "url": start_url,
                        "selector_healthcheck": publish_health_selectors,
                        "publish_editor_check": True,
                    },
                ),
            ]
        )
    if selectors.get("title"):
        steps.append(step("fill_title", "browser.fill", {"url": start_url, "selector": selectors.get("title"), "value": content["title"]}))
    if selectors.get("body"):
        steps.append(step("fill_publish_content", "browser.fill", {"url": start_url, "selector": selectors.get("body"), "value": content["body"]}))
    upload_selector = selectors.get("upload") or selectors.get("image_upload")
    for index, artifact_id in enumerate(content.get("media_artifact_ids", []), start=1):
        if not upload_selector:
            break
        steps.append(
            step(
                f"upload_media_{index}",
                "browser.upload",
                {
                    "url": start_url,
                    "selector": upload_selector,
                    "artifact_id": artifact_id,
                    "proof_kind": "image_upload",
                },
                risk="R5",
                approval=False,
            )
        )
        steps.append(
            step(
                f"verify_media_upload_{index}",
                "browser.snapshot",
                {
                    "url": start_url,
                    "proof_kind": "image_upload",
                    "expected_text": str(flow.get("upload_success_text") or "upload complete"),
                },
            )
        )
    steps.extend(
        [
            step(
                "submit_publish",
                "browser.submit",
                {
                    "url": start_url,
                    "selector": selectors.get("submit") or selectors.get("form"),
                    "action": "external_platform_publish_submit",
                    "wait_for_url": str((flow.get("verify") or {}).get("expected_url") or "").strip() or None,
                    "wait_for_text": str(flow.get("publish_success_text") or "").strip() or None,
                },
                risk="R5",
                approval=_approval_required_for_plan(plan),
            ),
            step(
                "capture_post_url_or_post_id",
                "browser.snapshot",
                {"url": target_post_url or None, "capture_post_identity": True},
            ),
            step(
                "reopen_post_for_publish_recheck",
                "browser.open",
                {"url": _PUBLISHED_POST_URL_REF},
            ),
            step(
                "assert_post_content_visible",
                "browser.snapshot",
                {
                    "url": _PUBLISHED_POST_URL_REF,
                    "expected_text": content["body"],
                    "proof_kind": "publish_recheck",
                },
            ),
        ]
    )
    if require_comment_flow and comment_selectors.get("comment_box"):
        steps.append(
            step(
                "open_comment_box",
                "browser.click",
                {
                    "url": comment_entry_url or _PUBLISHED_POST_URL_REF,
                    "selector": comment_selectors.get("comment_box"),
                },
            )
        )
    if require_comment_flow and comment_selectors.get("comment_input"):
        steps.append(
            step(
                "fill_comment_content",
                "browser.fill",
                {
                    "url": comment_entry_url or _PUBLISHED_POST_URL_REF,
                    "selector": comment_selectors.get("comment_input"),
                    "value": content["comment_text"],
                },
            )
        )
    if require_comment_flow:
        steps.extend(
            [
                step(
                    "submit_comment",
                    "browser.submit",
                    {
                        "url": comment_entry_url or _PUBLISHED_POST_URL_REF,
                        "selector": comment_selectors.get("comment_submit")
                        or comment_selectors.get("comment_form")
                        or selectors.get("comment_submit")
                        or selectors.get("comment_form")
                        or selectors.get("submit")
                        or selectors.get("form"),
                        "action": "external_platform_comment_submit",
                        "wait_for_text": str(comment_flow.get("comment_success_text") or "").strip() or None,
                        "wait_for_url": str((comment_flow.get("verify") or {}).get("expected_url") or "").strip() or None,
                    },
                    risk="R3",
                    approval=_approval_required_for_plan(plan),
                ),
                step(
                    "reopen_post_for_comment_recheck",
                    "browser.open",
                    {
                        "url": comment_recheck_url or _PUBLISHED_POST_URL_REF,
                        "wait_until": str(comment_flow.get("recheck_wait_until") or "domcontentloaded"),
                    },
                ),
                step(
                    "assert_comment_visible",
                    "browser.snapshot",
                    {
                        "url": comment_recheck_url or _PUBLISHED_POST_URL_REF,
                        "expected_text": content["comment_text"],
                        "proof_kind": "comment_recheck",
                        "wait_for_text": str(comment_flow.get("recheck_wait_text") or "").strip() or None,
                    },
                ),
            ]
        )
    return steps


def _enrich_step_result(
    *,
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any],
    step: ExternalPlatformAdapterStep,
    result: dict[str, Any],
) -> dict[str, Any]:
    output = result.get("output_redacted") if isinstance(result, dict) else {}
    if not isinstance(output, dict):
        return result
    updated = dict(result)
    verification = dict(updated.get("verification") or {})
    content = json.dumps(output, ensure_ascii=False)
    expected_text = str(step.input_redacted.get("expected_text") or "").strip()
    proof_kind = str(step.input_redacted.get("proof_kind") or "").strip()
    if expected_text:
        confirmed = expected_text in content
        verification.update(
            {
                "status": "confirmed" if confirmed else "missing",
                "expected_text": str(redact(expected_text)),
                "visible_excerpt": str(redact(expected_text[:120])) if confirmed else None,
                "proof_source": "page_text",
            }
        )
        if proof_kind == "publish_recheck":
            verification["publish_visible_text_confirmed"] = confirmed
        if proof_kind == "comment_recheck":
            verification["comment_visible_text_confirmed"] = confirmed
    if step.step_name == "capture_post_url_or_post_id":
        current_url = str(output.get("url") or "").strip()
        post_id_match = re.search(r"(?:note-|explore/|notes/)([A-Za-z0-9_-]+)", content)
        published_post_url = str(plan.metadata.get("target_post_url") or "").strip()
        if not published_post_url and _looks_like_published_post_url(current_url):
            published_post_url = current_url
        if published_post_url:
            verification["published_post_url"] = published_post_url
        if post_id_match:
            verification["published_post_id"] = post_id_match.group(1)
    if _is_real_xiaohongshu_flow(plan, adapter):
        verification["playwright_backend_required"] = True
    if verification:
        updated["verification"] = _redacted_dict(verification)
    return updated


def _annotate_result(result: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    updated = dict(result)
    output = updated.get("output_redacted")
    if isinstance(output, dict):
        updated["output_redacted"] = {**output, **values}
    else:
        updated["output_redacted"] = dict(values)
    return updated


def _resolve_runtime_browser_refs(
    args: dict[str, Any],
    *,
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    published_post_url = _published_post_url_from_evidence(evidence_items)
    if not published_post_url:
        return args
    updated = dict(args)
    for key in ("url", "wait_for_url"):
        if str(updated.get(key) or "").strip() == _PUBLISHED_POST_URL_REF:
            updated[key] = published_post_url
    return updated


def _published_post_url_from_evidence(evidence_items: list[dict[str, Any]]) -> str | None:
    for item in reversed(evidence_items):
        if not isinstance(item, dict):
            continue
        verification = item.get("verification")
        if not isinstance(verification, dict):
            continue
        value = str(verification.get("published_post_url") or "").strip()
        if value:
            return value
    return None


def _step_marked_session_authenticated(evidence: dict[str, Any]) -> bool:
    output = evidence.get("output_redacted") if isinstance(evidence, dict) else {}
    return isinstance(output, dict) and str(output.get("login_state_detected") or "") == "authenticated"


def _session_probe_state(
    *,
    step: ExternalPlatformAdapterStep,
    result: dict[str, Any],
) -> str | None:
    relevant = step.step_name in {
        "check_login_state",
        "resume_after_login",
        "detect_login_challenge",
        "verify_publish_editor_loaded",
    }
    if not relevant:
        return None
    output = result.get("output_redacted")
    if not isinstance(output, dict):
        return None
    content = json.dumps(output, ensure_ascii=False).lower()
    url = str(output.get("url") or "").lower()
    input_data = step.input_redacted if isinstance(step.input_redacted, dict) else {}
    not_authenticated_urls = [
        str(item).lower()
        for item in input_data.get("not_authenticated_if_url_contains", [])
        if str(item).strip()
    ]
    if any(marker in url for marker in not_authenticated_urls):
        return "login_required"
    if output.get("challenge_detected") or output.get("login_required"):
        return "login_required"
    success_selectors = [
        str(item) for item in input_data.get("success_selectors", []) if str(item).strip()
    ]
    if any(_selector_visible_in_content(selector, content) for selector in success_selectors):
        return "authenticated"
    authenticated_urls = [
        str(item).lower()
        for item in input_data.get("authenticated_if_url_contains", [])
        if str(item).strip()
    ]
    if any(marker in url for marker in authenticated_urls):
        return "authenticated"
    return "authenticated" if input_data.get("session_authenticated_hint") else None


def _selector_visible_in_content(selector: str, content: str) -> bool:
    value = selector.strip().lower()
    if not value:
        return False
    if value in content:
        return True
    if value.startswith("#"):
        ident = value[1:]
        return f'id="{ident}"' in content or f"id='{ident}'" in content
    if value.startswith("."):
        klass = value[1:]
        return f'class="{klass}"' in content or f"class='{klass}'" in content
    return False


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _auto_challenge_remediation_enabled(
    *,
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any],
    challenge: dict[str, str],
) -> bool:
    if challenge.get("drift_type") != "challenge_detected":
        return False
    manifest = adapter.get("manifest") if isinstance(adapter, dict) else {}
    manifest = manifest if isinstance(manifest, dict) else {}
    detection = manifest.get("challenge_detection") if isinstance(manifest.get("challenge_detection"), dict) else {}
    metadata = adapter.get("metadata") if isinstance(adapter, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    if "auto_remediation" in detection:
        return bool(detection.get("auto_remediation"))
    if "auto_challenge_remediation" in metadata:
        return bool(metadata.get("auto_challenge_remediation"))
    return _is_real_xiaohongshu_flow(plan, adapter)


def _challenge_auto_actions(
    manifest: dict[str, Any],
    *,
    current_url: str,
) -> list[dict[str, Any]]:
    detection = manifest.get("challenge_detection") if isinstance(manifest.get("challenge_detection"), dict) else {}
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(tool_name: str, selector: str, *, value: str | None = None) -> None:
        normalized = f"{tool_name}|{selector}|{value or ''}"
        if not selector or normalized in seen:
            return
        seen.add(normalized)
        args: dict[str, Any] = {"url": current_url, "selector": selector}
        if tool_name == "browser.check":
            args["value"] = value or "true"
        actions.append({"tool_name": tool_name, "args": args})

    for selector in DEFAULT_CHALLENGE_AUTO_CHECK_SELECTORS:
        add("browser.check", selector, value="true")
    for selector in DEFAULT_CHALLENGE_AUTO_CLICK_SELECTORS:
        add("browser.click", selector)
    for selector in detection.get("auto_check_selectors", []) or []:
        add("browser.check", str(selector).strip(), value="true")
    for selector in detection.get("auto_click_selectors", []) or []:
        add("browser.click", str(selector).strip())
    for raw_action in detection.get("auto_actions", []) or []:
        if not isinstance(raw_action, dict):
            continue
        tool_name = str(raw_action.get("tool_name") or "browser.click").strip() or "browser.click"
        args = raw_action.get("args") if isinstance(raw_action.get("args"), dict) else {}
        selector = str(args.get("selector") or raw_action.get("selector") or "").strip()
        if not selector:
            continue
        value = str(args.get("value") or raw_action.get("value") or "").strip() or None
        add(tool_name, selector, value=value)
    return actions


def _browser_backend_failure(
    *,
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any],
    step: ExternalPlatformAdapterStep,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if adapter.get("adapter_type") != "browser":
        return None
    required_backend = str(plan.metadata.get("provider_mode") or "").strip().lower()
    if not required_backend:
        required_backend = "local_cdp" if _is_real_xiaohongshu_flow(plan, adapter) else ""
    if required_backend not in {"playwright", "local_cdp"}:
        return None
    output = result.get("output_redacted") if isinstance(result, dict) else {}
    if not isinstance(output, dict):
        return None
    backend = str(output.get("backend") or "").lower()
    backend_status = str(output.get("backend_status") or "").lower()
    action_status = str(output.get("action_status") or "").lower()
    if backend == required_backend and backend_status == "available" and action_status == "completed":
        return None
    return {
        "required_backend": required_backend,
        "actual_backend": backend or "unknown",
        "message": f"Real Xiaohongshu execution requires {required_backend}; current browser backend did not match.",
    }


def _missing_real_xiaohongshu_post_identity(
    *,
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any],
    step: ExternalPlatformAdapterStep,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if not _is_real_xiaohongshu_flow(plan, adapter) or step.step_name != "capture_post_url_or_post_id":
        return None
    verification = result.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    published_post_url = str(verification.get("published_post_url") or "").strip()
    published_post_id = str(verification.get("published_post_id") or "").strip()
    if published_post_url or published_post_id:
        return None
    return {
        "reason_code": "published_post_identity_missing",
        "message": "The publish step completed, but the system could not confirm the post URL or post id for comment follow-up.",
    }


def _looks_like_published_post_url(url: str) -> bool:
    normalized = str(url or "").strip().lower()
    return "/notes/" in normalized or "/explore/" in normalized or "note-" in normalized


def _manifest_checksum(manifest: dict[str, Any]) -> str:
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _secret_manifest_issues(value: Any, path: str = "manifest") -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            child_path = f"{path}.{key_text}"
            selector_key = "selector" in key_lower
            in_selector_block = ".selectors." in child_path.lower()
            if key_lower in SENSITIVE_KEY_EXACT and not selector_key and not in_selector_block:
                issues.append(
                    {
                        "severity": "fatal",
                        "code": "inline_secret_key_denied",
                        "path": child_path,
                    }
                )
            issues.extend(_secret_manifest_issues(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            issues.extend(_secret_manifest_issues(item, f"{path}[{index}]"))
    elif isinstance(value, str) and SENSITIVE_VALUE_PATTERN.search(value):
        issues.append(
            {"severity": "fatal", "code": "inline_secret_value_denied", "path": path}
        )
    return issues


def _redacted_dict(value: Any) -> dict[str, Any]:
    redacted = redact(value)
    return redacted if isinstance(redacted, dict) else {"value": redacted}


def _redacted_step_input(*, step: ExternalPlatformAdapterStep, args: dict[str, Any]) -> dict[str, Any]:
    payload = dict(args)
    if step.step_name.endswith("password") and "value" in payload:
        payload["value"] = "[REDACTED_SECRET]"
    return _redacted_dict(payload)


def _mcp_call_id(result: dict[str, Any]) -> str | None:
    if result.get("mcp_call_id"):
        return str(result["mcp_call_id"])
    response = result.get("response")
    if isinstance(response, dict) and response.get("mcp_call_id"):
        return str(response["mcp_call_id"])
    output = result.get("output_redacted")
    if isinstance(output, dict):
        response = output.get("response")
        if isinstance(response, dict) and response.get("mcp_call_id"):
            return str(response["mcp_call_id"])
    return None


def _evidence_refs(result: dict[str, Any]) -> dict[str, Any]:
    output = result.get("output_redacted") or result if isinstance(result, dict) else result
    if not isinstance(output, dict):
        return {}
    refs = {
        "browser_evidence_id": output.get("browser_evidence_id"),
        "artifact_id": output.get("artifact_id"),
        "url": output.get("url"),
        "title": output.get("title"),
        "http_status": output.get("http_status"),
        "mcp_call_id": output.get("mcp_call_id"),
    }
    if isinstance(output.get("response"), dict) and output["response"].get("mcp_call_id"):
        refs["mcp_call_id"] = output["response"].get("mcp_call_id")
    return {key: value for key, value in refs.items() if value is not None}


def _evidence_url(evidence: dict[str, Any]) -> str | None:
    refs = evidence.get("evidence_refs")
    if isinstance(refs, dict) and refs.get("url"):
        return str(refs["url"])
    output = evidence.get("output_redacted") or evidence
    if isinstance(output, dict) and output.get("url"):
        return str(output["url"])
    return None


def _final_execution_evidence(
    *,
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any],
    evidence_items: list[dict[str, Any]],
    completed_step_ids: list[str],
) -> dict[str, Any]:
    refs: list[dict[str, Any]] = []
    completed_step_names = {
        str(item.get("step_name") or "")
        for item in evidence_items
        if isinstance(item, dict) and item.get("step_name")
    }
    step_outcome_counts: dict[str, int] = {}
    publish_recheck = {"status": "missing", "visible_excerpt": None}
    comment_recheck = {"status": "missing", "visible_excerpt": None}
    published_post_url: str | None = None
    published_post_id: str | None = None
    latest_session_state = "active"
    latest_backend = str(plan.metadata.get("provider_mode") or "playwright_ephemeral")
    challenge_signal: dict[str, Any] | None = None
    for item in evidence_items:
        raw_refs = item.get("evidence_refs")
        if isinstance(raw_refs, dict):
            refs.append({key: value for key, value in raw_refs.items() if value is not None})
        output = item.get("output_redacted") if isinstance(item, dict) else {}
        if isinstance(output, dict):
            status_key = str(output.get("action_status") or item.get("status") or "completed")
            step_outcome_counts[status_key] = step_outcome_counts.get(status_key, 0) + 1
            latest_backend = str(output.get("backend") or latest_backend or "playwright_ephemeral")
            if output.get("session_state"):
                latest_session_state = str(output.get("session_state"))
            if output.get("challenge_detected") or output.get("login_required"):
                challenge_signal = {
                    "status": "challenge_detected",
                    "reason_code": str(
                        output.get("challenge_reason_code")
                        or output.get("degraded_reason")
                        or "login_verification_required"
                    ),
                }
        verification_payload = item.get("verification")
        verification_data = (
            verification_payload if isinstance(verification_payload, dict) else {}
        )
        if verification_data.get("published_post_url") and not published_post_url:
            published_post_url = str(verification_data["published_post_url"])
        if verification_data.get("published_post_id") and not published_post_id:
            published_post_id = str(verification_data["published_post_id"])
        if verification_data.get("publish_visible_text_confirmed") is True:
            publish_recheck = {
                "status": "confirmed",
                "visible_excerpt": verification_data.get("visible_excerpt"),
            }
        if verification_data.get("comment_visible_text_confirmed") is True:
            comment_recheck = {
                "status": "confirmed",
                "visible_excerpt": verification_data.get("visible_excerpt"),
            }
        if verification_data.get("status") == "confirmed":
            if plan.action_type == "publish_content" and publish_recheck["status"] != "confirmed":
                publish_recheck = {
                    "status": "confirmed",
                    "visible_excerpt": verification_data.get("visible_excerpt"),
                }
            if plan.action_type == "comment_content" and comment_recheck["status"] != "confirmed":
                comment_recheck = {
                    "status": "confirmed",
                    "visible_excerpt": verification_data.get("visible_excerpt"),
                }
    serialized = json.dumps({"refs": refs, "items": evidence_items}, ensure_ascii=False).lower()
    require_comment_flow = bool(
        plan.metadata.get("require_full_comment_flow")
        or plan.metadata.get("publish_and_comment_both_required")
    )
    if _is_real_xiaohongshu_flow(plan, adapter):
        verification = (
            publish_recheck["status"] == "confirmed"
            and bool(published_post_url or published_post_id)
            and (
                not require_comment_flow
                or comment_recheck["status"] == "confirmed"
            )
        )
    else:
        verification = (
            publish_recheck["status"] == "confirmed"
            or comment_recheck["status"] == "confirmed"
            or any(
                marker in serialized
                for marker in (
                    "published post_id",
                    "post_id=",
                    "comment success",
                    "publish success",
                )
            )
        )
    publish_and_comment_both_confirmed = (
        publish_recheck["status"] == "confirmed" and comment_recheck["status"] == "confirmed"
    )
    deliverable = {
        "status": "completed" if verification else "incomplete",
        "post_draft": {
            "title": str(plan.metadata.get("title") or ""),
            "body": str(plan.metadata.get("publish_text") or plan.content_summary or ""),
            "tags": [str(item) for item in plan.metadata.get("tags", []) if str(item).strip()],
            "media_artifact_ids": [
                str(item) for item in plan.metadata.get("media_artifact_ids", []) if str(item).strip()
            ],
        },
        "publish_candidate": {
            "published_post_url": published_post_url,
            "published_post_id": published_post_id,
            "requires_playwright": _is_real_xiaohongshu_flow(plan, adapter),
        },
    }
    engagement_snapshot = {
        "status": "completed" if comment_recheck["status"] == "confirmed" else "pending",
        "comment_text": str(plan.metadata.get("comment_text") or "").strip() or None,
        "comment_visible_text_confirmed": comment_recheck["status"] == "confirmed",
    }
    recovery_reasons: list[str] = []
    if publish_recheck["status"] != "confirmed":
        recovery_reasons.append("publish_recheck_missing")
    if _is_real_xiaohongshu_flow(plan, adapter) and not (published_post_url or published_post_id):
        recovery_reasons.append("published_post_identity_missing")
    if require_comment_flow and _is_real_xiaohongshu_flow(plan, adapter) and "submit_comment" not in completed_step_names:
        recovery_reasons.append("comment_submit_missing")
    if require_comment_flow and plan.metadata.get("comment_text") and comment_recheck["status"] != "confirmed":
        recovery_reasons.append("comment_recheck_missing")
    verification_evidence = {
        "visible_text_confirmation": {
            "publish": publish_recheck,
            "comment": comment_recheck,
        },
        "url_identity_confirmation": {
            "published_post_url": published_post_url,
            "published_post_id": published_post_id,
            "status": "confirmed" if (published_post_url or published_post_id) else "missing",
        },
        "artifact_confirmation": {
            "artifact_refs_present": bool(refs),
            "status": "confirmed" if refs else "missing",
        },
        "challenge_signal": challenge_signal or {"status": "not_detected"},
        "recovery_evidence": {
            "status": "not_triggered" if verification else "required",
            "reason_codes": recovery_reasons,
            "resume_token": plan.plan_id if _is_real_xiaohongshu_flow(plan, adapter) else None,
        },
    }
    execution_summary = browser_execution_summary(
        session_context={"session_state": latest_session_state},
        action_status="completed" if verification else ("awaiting_human" if _is_real_xiaohongshu_flow(plan, adapter) else "degraded"),
        degraded_reason=None if verification else (recovery_reasons[0] if recovery_reasons else "verification_missing"),
        challenge_reason_code=(
            str((challenge_signal or {}).get("reason_code"))
            if isinstance(challenge_signal, dict) and challenge_signal.get("reason_code")
            else None
        ),
        verification_evidence={
            "status": "confirmed" if verification else "missing",
            "present": verification,
        },
        next_step="human_resume_real_browser_flow"
        if _is_real_xiaohongshu_flow(plan, adapter) and not verification
        else "manual_verify_external_state"
        if not verification
        else None,
    )
    execution_summary["step_outcome_counts"] = step_outcome_counts or {"completed": len(completed_step_ids)}
    execution_summary["backend"] = latest_backend
    execution_summary["backend_capabilities"] = browser_backend_capabilities(latest_backend)
    return _redacted_dict(
        {
            "plan_id": plan.plan_id,
            "adapter_id": adapter["adapter_id"],
            "adapter_type": adapter["adapter_type"],
            "platform_key": plan.platform_key,
            "action_type": plan.action_type,
            "completed_step_ids": completed_step_ids,
            "evidence_refs": refs,
            "published_post_url": published_post_url,
            "published_post_id": published_post_id,
            "publish_recheck": publish_recheck,
            "comment_recheck": comment_recheck,
            "publish_visible_text_confirmed": publish_recheck["status"] == "confirmed",
            "comment_visible_text_confirmed": comment_recheck["status"] == "confirmed",
            "publish_and_comment_both_confirmed": publish_and_comment_both_confirmed,
            "proof_source": "page_text",
            "playwright_backend_required": _is_real_xiaohongshu_flow(plan, adapter),
            "human_intervention_required": _is_real_xiaohongshu_flow(plan, adapter) and not verification,
            "resume_token": plan.plan_id if _is_real_xiaohongshu_flow(plan, adapter) else None,
            "verification_evidence_present": verification,
            "verification_evidence": verification_evidence,
            "browser_execution_summary": execution_summary,
            "session_state": latest_session_state,
            "backend_capabilities": browser_backend_capabilities(latest_backend),
            "deliverable": deliverable,
            "engagement_snapshot": engagement_snapshot,
            "recovery_evidence": {
                "status": "not_triggered" if verification else "required",
                "reason_codes": recovery_reasons,
                "resume_token": plan.plan_id if _is_real_xiaohongshu_flow(plan, adapter) else None,
            },
            "external_state_change": True,
            "secret_material_visible": False,
            "redaction_policy": "trace_service.redact",
        }
    )


def _final_plan_outcome(
    *,
    plan: ExternalPlatformActionPlan,
    adapter: dict[str, Any],
    final_evidence: dict[str, Any],
) -> tuple[str, str | None, str | None]:
    if final_evidence.get("verification_evidence_present"):
        return "completed", None, None
    if _is_real_xiaohongshu_flow(plan, adapter):
        recovery = final_evidence.get("recovery_evidence")
        reason_codes = recovery.get("reason_codes") if isinstance(recovery, dict) else []
        failure_reason = next(
            (str(code) for code in reason_codes if str(code).strip()),
            "verification_missing",
        )
        return "awaiting_human", failure_reason, "human_resume_real_browser_flow"
    if plan.action_type == "publish_content":
        return "degraded", "publish_recheck_missing", "manual_verify_external_state"
    if plan.action_type == "comment_content":
        return "degraded", "comment_recheck_missing", "manual_verify_external_state"
    return "degraded", "verification_missing", "manual_verify_external_state"


def _content_for_plan(plan: ExternalPlatformActionPlan, flow: dict[str, Any]) -> dict[str, Any]:
    body = str(
        plan.metadata.get("publish_text") or plan.content_summary or "external platform publish content"
    ).strip()
    title = str(
        plan.metadata.get("title") or flow.get("default_title") or body[:60] or "external platform publish"
    ).strip()
    tags = plan.metadata.get("tags") or flow.get("default_tags") or []
    comment_text = str(
        plan.metadata.get("comment_text")
        or plan.content_summary
        or flow.get("default_comment")
        or "verified"
    ).strip()
    return {
        "title": title,
        "body": body,
        "tags": [str(item) for item in tags],
        "comment_text": comment_text,
        "publish_surface": str(plan.metadata.get("publish_surface") or "text_note"),
        "media_artifact_ids": [
            str(item) for item in plan.metadata.get("media_artifact_ids", []) if str(item).strip()
        ],
    }
