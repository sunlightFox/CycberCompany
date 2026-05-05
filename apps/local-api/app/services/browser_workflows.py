from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from core_types import (
    BrowserWorkflowCandidate,
    BrowserWorkflowDiscoveryResult,
    BrowserWorkflowEvent,
    BrowserWorkflowExecution,
    BrowserWorkflowIntent,
    BrowserWorkflowPlan,
    BrowserWorkflowStep,
    ErrorCode,
    RiskLevel,
    TaskMode,
)
from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.browser_workflow_repo import BrowserWorkflowRepository
from app.db.repositories.task_repo import TaskRepository
from app.schemas.browser_workflows import (
    BrowserWorkflowExecuteRequest,
    BrowserWorkflowIntentResolveRequest,
    BrowserWorkflowIntentResolveResponse,
    BrowserWorkflowPlanCreateRequest,
    BrowserWorkflowPlanResponse,
    BrowserWorkflowReplayResponse,
    BrowserWorkflowResumeRequest,
)
from app.schemas.tasks import TaskCreateRequest, ToolExecuteRequest
from app.services.approvals import ApprovalService
from app.services.audit import AuditEventService
from app.services.safety_policy import RuntimeSafetyPolicyService
from app.services.tasks import TaskEngine
from app.services.tools import ToolRuntime

SUPPORTED_ACTION_TYPES = {
    "fill_form",
    "extract_data",
    "download_report",
    "upload_file",
    "admin_update",
    "book_or_order",
    "qa_test",
    "multi_step_workflow",
}

APPROVAL_REQUIRED_ACTIONS = {
    "fill_form",
    "upload_file",
    "admin_update",
    "book_or_order",
    "multi_step_workflow",
}

CHALLENGE_MARKERS = (
    "captcha",
    "验证码",
    "二次验证",
    "risk check",
    "risk control",
    "风控",
    "人机验证",
    "安全验证",
    "verify you are human",
    "uac",
    "payment challenge",
    "支付验证",
)

NEXT_MARKERS = ("下一步", "继续", "continue", "next", "proceed")
ENTRY_MARKERS = (
    "发布",
    "发文",
    "写文章",
    "创作",
    "新建",
    "创建",
    "新增",
    "编辑",
    "open",
    "new",
    "create",
    "compose",
    "post",
)
SUBMIT_MARKERS = (
    "提交",
    "保存",
    "确认",
    "发布",
    "发送",
    "预约",
    "下单",
    "submit",
    "save",
    "confirm",
    "book",
    "order",
)
DOWNLOAD_MARKERS = ("下载", "报表", "download", "report", ".csv", ".xlsx", ".pdf")


class AutonomousBrowserWorkflowService:
    def __init__(
        self,
        *,
        repo: BrowserWorkflowRepository,
        task_repo: TaskRepository,
        task_engine: TaskEngine,
        tool_runtime: ToolRuntime,
        approval_service: ApprovalService,
        audit_service: AuditEventService,
        safety_policy_service: RuntimeSafetyPolicyService | None = None,
    ) -> None:
        self._repo = repo
        self._task_repo = task_repo
        self._tasks = task_engine
        self._tools = tool_runtime
        self._approvals = approval_service
        self._audit = audit_service
        self._safety_policy = safety_policy_service

    async def resolve_intent(
        self,
        request: BrowserWorkflowIntentResolveRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowIntentResolveResponse:
        now = utc_now_iso()
        target_url = _normalize_url(request.target_url) or _extract_url(request.text)
        action_type = _normalize_action_type(request.action_type) or _classify_action(
            request.text,
            request.constraints,
        )
        missing_fields: list[str] = []
        if not target_url:
            missing_fields.append("target_url")
        account_candidates = request.constraints.get("account_candidates") or []
        if (
            isinstance(account_candidates, list)
            and len(account_candidates) > 1
            and not request.constraints.get("selected_account_id")
            and not request.constraints.get("session_handle_id")
        ):
            missing_fields.append("account")
        status = "clarification_needed" if missing_fields else "resolved"
        target_key = _host(target_url) if target_url else None
        intent_data = {
            "intent_id": new_id("bwint"),
            "organization_id": request.organization_id,
            "member_id": request.member_id,
            "conversation_id": request.conversation_id,
            "turn_id": request.turn_id,
            "trace_id": trace_id,
            "natural_language_goal": str(redact(request.text)),
            "action_type": action_type,
            "target_url": target_url,
            "target_key": target_key,
            "content_summary": request.content_summary or _content_summary(request.text),
            "constraints": redact(request.constraints),
            "missing_fields": missing_fields,
            "status": status,
            "confidence": 0.86 if status == "resolved" else 0.45,
            "resolver_evidence": {
                "resolver": "rule_based_autonomous_browser_workflow",
                "target_url_detected": bool(target_url),
                "action_type_detected": action_type,
                "account_ambiguous": "account" in missing_fields,
            },
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_intent(intent_data)
        intent = BrowserWorkflowIntent(**intent_data)
        if "target_url" in missing_fields:
            message = "我需要先知道目标网站或网页地址，然后才能自动观察和规划浏览器操作。"
            next_step = "ask_target_url"
        elif "account" in missing_fields:
            message = "这个目标有多个可用账号，请先告诉我要用哪个账号。"
            next_step = "ask_account"
        else:
            message = "目标已明确，我可以创建浏览器工作流计划。"
            next_step = "create_plan"
        return BrowserWorkflowIntentResolveResponse(
            intent=intent,
            message=message,
            next_step=next_step,
        )

    async def create_plan(
        self,
        request: BrowserWorkflowPlanCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowPlanResponse:
        intent = await self._repo.get_intent(request.intent_id)
        if intent is None:
            raise AppError(ErrorCode.NOT_FOUND, "浏览器工作流意图不存在", status_code=404)
        now = utc_now_iso()
        action_type = _normalize_action_type(request.action_type) or intent["action_type"]
        target_url = _normalize_url(request.target_url) or intent.get("target_url")
        target_key = _host(target_url) if target_url else intent.get("target_key")
        status = "awaiting_intent_clarification" if intent["status"] != "resolved" else "planned"
        merged_constraints = {
            **dict(intent.get("constraints") or {}),
            **dict(request.constraints or {}),
        }
        task_id = None
        if status == "planned":
            task = await self._tasks.create_task(
                TaskCreateRequest(
                    conversation_id=intent.get("conversation_id"),
                    owner_member_id=str(intent.get("member_id") or "mem_xiaoyao"),
                    goal=request.goal
                    or intent["natural_language_goal"]
                    or f"Autonomous browser workflow: {action_type}",
                    mode_hint=TaskMode.WORKFLOW,
                    success_criteria=[
                        "observe target page",
                        "execute low risk browser actions",
                        "stop before approval boundary",
                        "record replay evidence",
                    ],
                    constraints={
                        "phase": "autonomous_browser_workflow",
                        "target_url": target_url,
                        "action_type": action_type,
                    },
                    auto_start=False,
                ),
                trace_id=trace_id,
            )
            task_id = task.task_id
        plan_data = {
            "plan_id": new_id("bwplan"),
            "intent_id": request.intent_id,
            "organization_id": intent.get("organization_id") or "org_default",
            "member_id": intent.get("member_id") or "mem_xiaoyao",
            "conversation_id": intent.get("conversation_id"),
            "task_id": task_id,
            "trace_id": trace_id,
            "action_type": action_type,
            "target_url": target_url,
            "target_key": target_key,
            "goal": request.goal or intent["natural_language_goal"],
            "status": status,
            "risk_level": _risk_for_action(action_type),
            "current_url": target_url,
            "content_summary": request.content_summary or intent.get("content_summary"),
            "form_data": redact(request.form_data),
            "file_refs": redact(request.file_refs),
            "steps": [{"step_type": "observe", "status": "planned"}],
            "approval_binding": {},
            "evidence": {},
            "metadata": redact(
                {
                    "source": "autonomous_browser_workflow",
                    "max_steps": request.max_steps,
                    "constraints": merged_constraints,
                    "session_handle_id": merged_constraints.get("session_handle_id"),
                    "browser_session_handle_id": merged_constraints.get(
                        "browser_session_handle_id"
                    ),
                }
            ),
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_plan(plan_data)
        await self._event(
            plan_data,
            event_type="plan.created",
            payload={"status": status, "action_type": action_type},
            trace_id=trace_id,
        )
        return await self._response(
            plan_data["plan_id"],
            message=(
                "已创建通用浏览器工作流计划。"
                if status == "planned"
                else "还缺少目标信息，暂不进入浏览器探索。"
            ),
            next_step="execute" if status == "planned" else "resolve_missing_fields",
        )

    async def get_plan(self, plan_id: str) -> BrowserWorkflowPlanResponse:
        return await self._response(plan_id, message="浏览器工作流计划已读取。")

    async def execute_plan(
        self,
        plan_id: str,
        request: BrowserWorkflowExecuteRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowPlanResponse:
        payload = request or BrowserWorkflowExecuteRequest()
        plan = await self._require_plan(plan_id)
        await self._apply_execution_options(plan, payload, trace_id=trace_id)
        plan = await self._require_plan(plan_id)
        if plan["status"].startswith("awaiting_"):
            return await self._response(
                plan_id,
                message="还缺少必要信息，暂不进入浏览器操作。",
                next_step=plan["status"],
            )
        if not plan.get("target_url"):
            await self._repo.update_plan(
                plan_id,
                {
                    "status": "awaiting_target",
                    "failure_reason": "target_url_missing",
                    "updated_at": utc_now_iso(),
                },
            )
            return await self._response(
                plan_id,
                message="我需要先知道目标网站或网页地址。",
                next_step="ask_target_url",
            )
        latest = await self._repo.latest_execution(plan_id)
        if latest and latest["status"] == "awaiting_approval" and not payload.force_discovery:
            return await self._response(
                plan_id,
                execution=latest,
                message=latest.get("user_visible_message")
                or "草稿已准备好，确认后我再提交。",
                next_step="awaiting_approval",
            )
        execution = await self._start_execution(plan, trace_id=trace_id)
        try:
            if plan["action_type"] == "extract_data":
                return await self._execute_extract(plan, execution, trace_id=trace_id)
            if plan["action_type"] == "download_report":
                return await self._execute_download(plan, execution, trace_id=trace_id)
            if plan["action_type"] == "upload_file":
                return await self._execute_upload_boundary(plan, execution, trace_id=trace_id)
            if plan["action_type"] == "qa_test":
                return await self._execute_qa(plan, execution, trace_id=trace_id)
            return await self._execute_mutating_workflow(
                plan,
                execution,
                max_steps=payload.max_steps,
                trace_id=trace_id,
            )
        except AppError:
            raise
        except Exception as exc:
            await self._finish_execution(
                execution,
                status="failed",
                failure_reason="workflow_execution_failed",
                message="浏览器工作流执行失败，已停止在外部状态变更前。",
                result={"error": str(redact(str(exc)))},
                trace_id=trace_id,
            )
            raise

    async def resume_after_human(
        self,
        plan_id: str,
        request: BrowserWorkflowResumeRequest | None = None,
        *,
        trace_id: str | None = None,
    ) -> BrowserWorkflowPlanResponse:
        payload = request or BrowserWorkflowResumeRequest()
        plan = await self._require_plan(plan_id)
        approval_id = payload.approval_id or plan.get("approval_id")
        latest = await self._repo.latest_execution(plan_id)
        if not approval_id and latest and latest.get("status") == "challenge_detected":
            resolution = dict(payload.human_resolution or {})
            current_url = (
                _normalize_url(str(resolution.get("current_url") or ""))
                or _normalize_url(str(resolution.get("resolved_url") or ""))
                or plan.get("current_url")
                or plan.get("target_url")
            )
            metadata = {
                **dict(plan.get("metadata") or {}),
                "human_resolution": redact(resolution),
                "runtime_options": {
                    **dict(plan.get("metadata", {}).get("runtime_options") or {}),
                    "provider_mode": payload.provider_mode or "auto",
                    "viewport_profile": payload.viewport_profile or "desktop",
                    "action_strategy": payload.action_strategy or "css",
                },
            }
            await self._repo.update_plan(
                plan_id,
                {
                    "status": "human_resolved",
                    "current_url": current_url,
                    "metadata": metadata,
                    "failure_reason": None,
                    "updated_at": utc_now_iso(),
                },
            )
            await self._event(
                {**plan, "current_url": current_url, "metadata": metadata},
                execution_id=latest["execution_id"],
                event_type="workflow.human_resolved",
                payload={"challenge_resume": True},
                trace_id=trace_id,
            )
            return await self.execute_plan(
                plan_id,
                BrowserWorkflowExecuteRequest(
                    force_discovery=True,
                    provider_mode=payload.provider_mode,
                    viewport_profile=payload.viewport_profile,
                    action_strategy=payload.action_strategy,
                ),
                trace_id=trace_id,
            )
        if not approval_id:
            return await self._response(
                plan_id,
                message="还没有可继续的审批。",
                next_step="execute",
            )
        approval = await self._approvals.get(approval_id)
        if approval.status not in {"approved", "edited"}:
            return await self._response(
                plan_id,
                message="我还在等待你的确认。",
                next_step="awaiting_approval",
            )
        execution = latest or await self._start_execution(plan, trace_id=trace_id)
        if plan["action_type"] == "upload_file":
            tool_response = await self._tool(
                plan,
                "browser.upload",
                {
                    "url": plan.get("current_url") or plan["target_url"],
                    "selector": (
                        plan.get("metadata", {}).get("upload_selector")
                        or "input[type=file]"
                    ),
                    "artifact_id": _first_artifact_id(plan.get("file_refs") or []),
                    "asset_handle_id": _first_asset_handle_id(plan.get("file_refs") or []),
                },
                approval_id=approval_id,
                trace_id=trace_id,
            )
        else:
            selector = str(
                plan.get("metadata", {}).get("submit_selector")
                or plan.get("metadata", {}).get("form_selector")
                or "form"
            )
            tool_response = await self._tool(
                plan,
                "browser.submit",
                {
                    "url": plan.get("current_url") or plan["target_url"],
                    "selector": selector,
                },
                approval_id=approval_id,
                trace_id=trace_id,
            )
        result = tool_response.result
        status = str(result.get("action_status") or "")
        if status not in {"completed", "http_error"}:
            await self._finish_execution(
                execution,
                status="failed",
                failure_reason="post_approval_action_failed",
                message="确认后执行提交时失败，未记录为成功。",
                result=result,
                trace_id=trace_id,
            )
            return await self._response(
                plan_id,
                execution=await self._repo.latest_execution(plan_id),
                message="确认后执行提交时失败，未记录为成功。",
                next_step="recover_or_retry",
            )
        evidence_refs = [_evidence_ref(result)]
        await self._learn_candidate(plan, evidence_refs=evidence_refs, trace_id=trace_id)
        await self._finish_execution(
            execution,
            status="completed",
            message="已按确认完成浏览器操作。",
            result={
                "url": result.get("url"),
                "http_status": result.get("http_status"),
                "post_id": result.get("post_id"),
                "browser_evidence_id": result.get("browser_evidence_id"),
            },
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        await self._repo.update_plan(
            plan_id,
            {
                "status": "completed",
                "evidence": {
                    **dict(plan.get("evidence") or {}),
                    "post_submit": evidence_refs,
                },
                "updated_at": utc_now_iso(),
            },
        )
        await self._event(
            plan,
            execution_id=execution["execution_id"],
            event_type="workflow.completed",
            payload={"approval_id": approval_id, "status": "completed"},
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        return await self._response(
            plan_id,
            execution=await self._repo.latest_execution(plan_id),
            message="已按确认完成浏览器操作。",
            next_step=None,
        )

    async def replay(self, plan_id: str) -> BrowserWorkflowReplayResponse:
        plan = await self._require_plan(plan_id)
        executions = await self._repo.list_executions(plan_id)
        events = await self._repo.list_events(plan_id)
        candidates = await self._repo.list_candidates_for_plan(plan)
        return BrowserWorkflowReplayResponse(
            plan=BrowserWorkflowPlan(**plan),
            executions=[BrowserWorkflowExecution(**item) for item in executions],
            events=[BrowserWorkflowEvent(**item) for item in events],
            candidates=[BrowserWorkflowCandidate(**item) for item in candidates],
            redaction_summary={
                "cookies_visible": False,
                "tokens_visible": False,
                "passwords_visible": False,
                "selectors_user_visible": False,
                "local_sensitive_paths_visible": False,
                "phase54_frame_tab_console_network_summarized": True,
            },
        )

    async def _apply_execution_options(
        self,
        plan: dict[str, Any],
        request: BrowserWorkflowExecuteRequest,
        *,
        trace_id: str | None,
    ) -> None:
        runtime_options = {
            "provider_mode": request.provider_mode or "auto",
            "viewport_profile": request.viewport_profile or "desktop",
            "action_strategy": request.action_strategy or "css",
            "wait_until": request.wait_until,
        }
        await self._repo.update_plan(
            plan["plan_id"],
            {
                "metadata": {
                    **dict(plan.get("metadata") or {}),
                    "runtime_options": redact(runtime_options),
                    "phase": "phase54_browser_workflow_resilience",
                },
                "trace_id": trace_id or plan.get("trace_id"),
                "updated_at": utc_now_iso(),
            },
        )

    async def _execute_extract(
        self,
        plan: dict[str, Any],
        execution: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> BrowserWorkflowPlanResponse:
        tool_response = await self._tool(
            plan,
            "browser.extract",
            {"url": plan["target_url"]},
            trace_id=trace_id,
        )
        result = tool_response.result
        evidence_refs = [_evidence_ref(result)]
        if _challenge_reason(str(result.get("snapshot") or result.get("content_preview") or "")):
            return await self._stop_with_status(
                plan,
                execution,
                status="challenge_detected",
                failure_reason="challenge_detected",
                message="遇到验证码/二次验证，需要你本人处理后我继续。",
                evidence_refs=evidence_refs,
                result=result,
                trace_id=trace_id,
            )
        extracted = result.get("extracted_data") or {}
        if not extracted:
            return await self._stop_with_status(
                plan,
                execution,
                status="discovery_failed",
                failure_reason="extractable_data_not_found",
                message="我没有稳定识别到可抽取的数据结构。",
                evidence_refs=evidence_refs,
                result=result,
                trace_id=trace_id,
            )
        await self._learn_candidate(plan, evidence_refs=evidence_refs, trace_id=trace_id)
        await self._finish_execution(
            execution,
            status="completed",
            message="已抽取页面数据并写入证据。",
            result={
                "extracted_data": extracted,
                "browser_evidence_id": result.get("browser_evidence_id"),
            },
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        await self._repo.update_plan(
            plan["plan_id"],
            {
                "status": "completed",
                "evidence": {"extraction": evidence_refs, "row_count": _row_count(extracted)},
                "updated_at": utc_now_iso(),
            },
        )
        return await self._response(
            plan["plan_id"],
            execution=await self._repo.latest_execution(plan["plan_id"]),
            discovery=_discovery(
                plan,
                status="completed",
                message="已抽取页面数据并写入证据。",
                evidence_refs=evidence_refs,
            ),
            message="已抽取页面数据并写入证据。",
        )

    async def _execute_download(
        self,
        plan: dict[str, Any],
        execution: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> BrowserWorkflowPlanResponse:
        snapshot = await self._snapshot(plan, trace_id=trace_id, label="download_discovery")
        evidence_refs = [_evidence_ref(snapshot)]
        html = _snapshot_text(snapshot)
        challenge = _challenge_reason(html)
        if challenge:
            return await self._stop_with_status(
                plan,
                execution,
                status="challenge_detected",
                failure_reason=challenge,
                message="遇到验证码/二次验证，需要你本人处理后我继续。",
                evidence_refs=evidence_refs,
                result=snapshot,
                trace_id=trace_id,
            )
        parser = _WorkflowHtmlParser.from_text(html)
        download_url = (
            str(plan.get("metadata", {}).get("constraints", {}).get("download_url") or "")
            or _find_download_url(parser, str(snapshot.get("url") or plan["target_url"]))
        )
        if not download_url:
            return await self._stop_with_status(
                plan,
                execution,
                status="discovery_failed",
                failure_reason="download_link_not_found",
                message="我没有稳定识别到报表下载入口。",
                evidence_refs=evidence_refs,
                result=snapshot,
                trace_id=trace_id,
            )
        tool_response = await self._tool(
            plan,
            "browser.download",
            {
                "url": download_url,
                "display_name": _safe_download_name(download_url),
                "workflow_low_risk_download": True,
            },
            trace_id=trace_id,
        )
        if tool_response.approval is not None:
            return await self._approval_from_tool_response(
                plan,
                execution,
                tool_response,
                evidence_refs=evidence_refs,
                trace_id=trace_id,
            )
        result = tool_response.result
        evidence_refs.append(_evidence_ref(result))
        await self._learn_candidate(plan, evidence_refs=evidence_refs, trace_id=trace_id)
        await self._finish_execution(
            execution,
            status="completed",
            message="已下载报表并写入任务工件。",
            result={
                "download": result.get("download") or result.get("artifact"),
                "browser_evidence_id": result.get("browser_evidence_id"),
            },
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        await self._repo.update_plan(
            plan["plan_id"],
            {
                "status": "completed",
                "evidence": {"download": evidence_refs},
                "updated_at": utc_now_iso(),
            },
        )
        return await self._response(
            plan["plan_id"],
            execution=await self._repo.latest_execution(plan["plan_id"]),
            discovery=_discovery(
                plan,
                status="completed",
                message="已下载报表并写入任务工件。",
                evidence_refs=evidence_refs,
            ),
            message="已下载报表并写入任务工件。",
        )

    async def _execute_upload_boundary(
        self,
        plan: dict[str, Any],
        execution: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> BrowserWorkflowPlanResponse:
        if not _has_upload_ref(plan.get("file_refs") or []):
            await self._finish_execution(
                execution,
                status="awaiting_file",
                failure_reason="upload_file_ref_missing",
                message="上传文件需要先选择任务工件或文件资产，我不会读取任意本地路径。",
                result={"file_ref_required": True},
                trace_id=trace_id,
            )
            await self._repo.update_plan(
                plan["plan_id"],
                {
                    "status": "awaiting_file",
                    "failure_reason": "upload_file_ref_missing",
                    "updated_at": utc_now_iso(),
                },
            )
            return await self._response(
                plan["plan_id"],
                execution=await self._repo.latest_execution(plan["plan_id"]),
                message="上传文件需要先选择任务工件或文件资产，我不会读取任意本地路径。",
                next_step="provide_file_asset_or_artifact",
            )
        snapshot = await self._snapshot(plan, trace_id=trace_id, label="upload_precheck")
        evidence_refs = [_evidence_ref(snapshot)]
        html = _snapshot_text(snapshot)
        challenge = _challenge_reason(html)
        if challenge:
            return await self._stop_with_status(
                plan,
                execution,
                status="challenge_detected",
                failure_reason=challenge,
                message="遇到验证码/二次验证，需要你本人处理后我继续。",
                evidence_refs=evidence_refs,
                result=snapshot,
                trace_id=trace_id,
            )
        parser = _WorkflowHtmlParser.from_text(html)
        upload_selector = _find_upload_selector(parser)
        if not upload_selector:
            return await self._stop_with_status(
                plan,
                execution,
                status="discovery_failed",
                failure_reason="upload_control_not_found",
                message="我没有稳定识别到文件上传控件。",
                evidence_refs=evidence_refs,
                result=snapshot,
                trace_id=trace_id,
            )
        await self._repo.update_plan(
            plan["plan_id"],
            {
                "current_url": str(snapshot.get("url") or plan["target_url"]),
                "metadata": {
                    **dict(plan.get("metadata") or {}),
                    "upload_selector": upload_selector,
                },
                "updated_at": utc_now_iso(),
            },
        )
        approval = await self._create_boundary_approval(
            plan,
            action_name="browser.upload",
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        return await self._mark_awaiting_approval(
            plan,
            execution,
            approval_id=approval.approval_id,
            evidence_refs=evidence_refs,
            message="文件已定位到上传控件，还没有上传。确认后我再提交。",
            trace_id=trace_id,
        )

    async def _execute_qa(
        self,
        plan: dict[str, Any],
        execution: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> BrowserWorkflowPlanResponse:
        snapshot = await self._snapshot(plan, trace_id=trace_id, label="qa_observe")
        evidence_refs = [_evidence_ref(snapshot)]
        html = _snapshot_text(snapshot)
        challenge = _challenge_reason(html)
        if challenge:
            return await self._stop_with_status(
                plan,
                execution,
                status="challenge_detected",
                failure_reason=challenge,
                message="遇到验证码/二次验证，需要你本人处理后我继续。",
                evidence_refs=evidence_refs,
                result=snapshot,
                trace_id=trace_id,
            )
        parser = _WorkflowHtmlParser.from_text(html)
        result = {
            "title": snapshot.get("title"),
            "url": snapshot.get("url"),
            "form_count": len(parser.forms),
            "link_count": len(parser.links),
            "table_count": len(parser.tables),
            "assertions": _qa_assertions(plan, html, parser),
        }
        await self._learn_candidate(plan, evidence_refs=evidence_refs, trace_id=trace_id)
        await self._finish_execution(
            execution,
            status="completed",
            message="已完成页面 QA 观察并写入 replay 证据。",
            result=result,
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        await self._repo.update_plan(
            plan["plan_id"],
            {"status": "completed", "evidence": {"qa": evidence_refs}, "updated_at": utc_now_iso()},
        )
        return await self._response(
            plan["plan_id"],
            execution=await self._repo.latest_execution(plan["plan_id"]),
            discovery=_discovery(
                plan,
                status="completed",
                message="已完成页面 QA 观察并写入 replay 证据。",
                evidence_refs=evidence_refs,
            ),
            message="已完成页面 QA 观察并写入 replay 证据。",
        )

    async def _execute_mutating_workflow(
        self,
        plan: dict[str, Any],
        execution: dict[str, Any],
        *,
        max_steps: int,
        trace_id: str | None,
    ) -> BrowserWorkflowPlanResponse:
        candidate = None
        if plan.get("target_key"):
            candidate = await self._repo.find_candidate(
                organization_id=plan["organization_id"],
                host=plan["target_key"],
                action_type=plan["action_type"],
            )
        current_url = str(plan.get("current_url") or plan["target_url"])
        filled_keys: set[str] = set()
        evidence_refs: list[dict[str, Any]] = []
        selector_manifest: dict[str, str] = {}
        waited_for_js = False
        stable_after_wait = False
        submit_selector_after_wait: str | None = None
        tried_mobile = str(
            plan.get("metadata", {}).get("runtime_options", {}).get("viewport_profile") or ""
        ).lower() == "mobile"
        opened_entry = False
        if candidate and candidate.get("manifest"):
            selector_manifest.update(
                dict(candidate["manifest"].get("field_selectors") or {})
            )
            await self._repo.update_plan(
                plan["plan_id"],
                {
                    "metadata": {
                        **dict(plan.get("metadata") or {}),
                        "candidate_reused": True,
                        "candidate_id": candidate["candidate_id"],
                    },
                    "updated_at": utc_now_iso(),
                },
            )
            plan = await self._require_plan(plan["plan_id"])
        for index in range(max_steps):
            snapshot = await self._snapshot(
                {**plan, "current_url": current_url},
                trace_id=trace_id,
                label=f"workflow_observe_{index + 1}",
            )
            evidence_refs.append(_evidence_ref(snapshot))
            html = _snapshot_text(snapshot)
            challenge = _challenge_reason(html)
            if challenge:
                return await self._stop_with_status(
                    plan,
                    execution,
                    status="challenge_detected",
                    failure_reason=challenge,
                    message="遇到验证码/二次验证，需要你本人处理后我继续。",
                    evidence_refs=evidence_refs,
                    result=snapshot,
                    trace_id=trace_id,
                )
            parser = _WorkflowHtmlParser.from_text(html)
            form_data = dict(plan.get("form_data") or {})
            fills = _field_fill_actions(
                parser,
                form_data,
                filled_keys=filled_keys,
                selector_manifest=selector_manifest,
            )
            for key, selector, value, label, tool_name in fills:
                tool_response = await self._tool(
                    plan,
                    tool_name,
                    {"url": current_url, "selector": selector, "value": str(value)},
                    trace_id=trace_id,
                )
                result = tool_response.result
                filled_keys.add(key)
                selector_manifest[key] = selector
                await self._insert_step(
                    plan,
                    step_order=len(await self._repo.list_steps(plan["plan_id"])) + 1,
                    step_type="fill_field",
                    tool_name=tool_name,
                    selector=selector,
                    label=label,
                    status="completed",
                    input_redacted={"field": key, "value_preview": str(redact(str(value)))[:80]},
                    output_redacted={"action_status": result.get("action_status")},
                    evidence_refs=[_evidence_ref(result)],
                    tool_call_id=tool_response.tool_call.tool_call_id,
                    trace_id=trace_id,
                )
            remaining = [key for key in form_data if key not in filled_keys]
            if remaining and stable_after_wait:
                return await self._stop_with_status(
                    plan,
                    execution,
                    status="drift_detected" if candidate else "discovery_failed",
                    failure_reason="dynamic_controls_not_found_after_wait",
                    message="页面加载后仍没有稳定识别到表单控件，已停止在提交前。",
                    evidence_refs=evidence_refs,
                    result=snapshot,
                    trace_id=trace_id,
                )
            if remaining and not fills:
                entry_selector = None if opened_entry else _find_entry_selector(parser)
                if entry_selector:
                    clicked = await self._tool(
                        plan,
                        "browser.click",
                        {"url": current_url, "selector": entry_selector},
                        trace_id=trace_id,
                    )
                    opened_entry = True
                    current_url = str(clicked.result.get("url") or current_url)
                    await self._repo.update_plan(
                        plan["plan_id"],
                        {
                            "current_url": current_url,
                            "metadata": {
                                **dict(plan.get("metadata") or {}),
                                "entry_selector": entry_selector,
                                "entry_opened": True,
                                "last_tab_summary": clicked.result.get("tab_summary"),
                                "last_frame_summary": clicked.result.get("frame_summary"),
                            },
                            "updated_at": utc_now_iso(),
                        },
                    )
                    plan = await self._require_plan(plan["plan_id"])
                    await self._insert_step(
                        plan,
                        step_order=len(await self._repo.list_steps(plan["plan_id"])) + 1,
                        step_type="open_entry",
                        tool_name="browser.click",
                        selector=entry_selector,
                        label="entry",
                        status="completed",
                        output_redacted={
                            "url": clicked.result.get("url"),
                            "tab_summary": clicked.result.get("tab_summary"),
                            "frame_summary": clicked.result.get("frame_summary"),
                        },
                        evidence_refs=[_evidence_ref(clicked.result)],
                        tool_call_id=clicked.tool_call.tool_call_id,
                        trace_id=trace_id,
                    )
                    clicked_html = _snapshot_text(clicked.result)
                    clicked_parser = _WorkflowHtmlParser.from_text(clicked_html)
                    clicked_fills = _field_fill_actions(
                        clicked_parser,
                        form_data,
                        filled_keys=filled_keys,
                        selector_manifest=selector_manifest,
                    )
                    if clicked_fills:
                        snapshot = clicked.result
                        html = clicked_html
                        parser = clicked_parser
                        fills = clicked_fills
                        submit_selector_after_wait = _form_selector(clicked_parser)
                        stable_after_wait = True
                    else:
                        continue
                if not waited_for_js:
                    waited = await self._tool(
                        plan,
                        "browser.wait",
                        {
                            "url": current_url,
                            "selector": (
                                "form, input, textarea, select, "
                                "[contenteditable='true'], button"
                            ),
                            "timeout_seconds": 3,
                        },
                        trace_id=trace_id,
                    )
                    waited_for_js = True
                    await self._repo.update_execution(
                        execution["execution_id"],
                        {
                            "status": "waiting_for_js",
                            "result": {"wait_evidence": _evidence_ref(waited.result)},
                            "updated_at": utc_now_iso(),
                        },
                    )
                    await self._insert_step(
                        plan,
                        step_order=len(await self._repo.list_steps(plan["plan_id"])) + 1,
                        step_type="wait_for_js",
                        tool_name="browser.wait",
                        selector="form,input,textarea,select,[contenteditable]",
                        label="wait_for_dynamic_dom",
                        status="completed",
                        output_redacted={
                            "action_status": waited.result.get("action_status"),
                            "degraded_reason": waited.result.get("degraded_reason"),
                        },
                        evidence_refs=[_evidence_ref(waited.result)],
                        tool_call_id=waited.tool_call.tool_call_id,
                        trace_id=trace_id,
                    )
                    wait_snapshot = waited.result
                    wait_html = _snapshot_text(wait_snapshot)
                    wait_parser = _WorkflowHtmlParser.from_text(wait_html)
                    wait_fills = _field_fill_actions(
                        wait_parser,
                        form_data,
                        filled_keys=filled_keys,
                        selector_manifest=selector_manifest,
                    )
                    if wait_fills:
                        snapshot = wait_snapshot
                        html = wait_html
                        parser = wait_parser
                        fills = wait_fills
                        stable_after_wait = True
                        submit_selector_after_wait = _form_selector(wait_parser)
                        for key, selector, value, label, tool_name in fills:
                            tool_response = await self._tool(
                                plan,
                                tool_name,
                                {"url": current_url, "selector": selector, "value": str(value)},
                                trace_id=trace_id,
                            )
                            result = tool_response.result
                            filled_keys.add(key)
                            selector_manifest[key] = selector
                            await self._insert_step(
                                plan,
                                step_order=len(await self._repo.list_steps(plan["plan_id"])) + 1,
                                step_type="fill_field",
                                tool_name=tool_name,
                                selector=selector,
                                label=label,
                                status="completed",
                                input_redacted={
                                    "field": key,
                                    "value_preview": str(redact(str(value)))[:80],
                                },
                                output_redacted={"action_status": result.get("action_status")},
                                evidence_refs=[_evidence_ref(result)],
                                tool_call_id=tool_response.tool_call.tool_call_id,
                                trace_id=trace_id,
                            )
                        remaining = [key for key in form_data if key not in filled_keys]
                    else:
                        continue
                    continue
                if not tried_mobile:
                    metadata = dict(plan.get("metadata") or {})
                    runtime = {
                        **dict(metadata.get("runtime_options") or {}),
                        "viewport_profile": "mobile",
                    }
                    await self._repo.update_plan(
                        plan["plan_id"],
                        {
                            "metadata": {
                                **metadata,
                                "runtime_options": runtime,
                                "mobile_fallback_used": True,
                            },
                            "updated_at": utc_now_iso(),
                        },
                    )
                    tried_mobile = True
                    plan = await self._require_plan(plan["plan_id"])
                    await self._event(
                        plan,
                        execution_id=execution["execution_id"],
                        event_type="workflow.mobile_viewport_retry",
                        payload={"viewport_profile": "mobile"},
                        trace_id=trace_id,
                    )
                    continue
            next_selector = _find_next_selector(parser)
            if remaining and next_selector:
                clicked = await self._tool(
                    plan,
                    "browser.click",
                    {"url": current_url, "selector": next_selector},
                    trace_id=trace_id,
                )
                current_url = str(clicked.result.get("url") or current_url)
                await self._insert_step(
                    plan,
                    step_order=len(await self._repo.list_steps(plan["plan_id"])) + 1,
                    step_type="navigate_next",
                    tool_name="browser.click",
                    selector=next_selector,
                    label="next",
                    status="completed",
                    output_redacted={"url": clicked.result.get("url")},
                    evidence_refs=[_evidence_ref(clicked.result)],
                    tool_call_id=clicked.tool_call.tool_call_id,
                    trace_id=trace_id,
                )
                continue
            if remaining:
                return await self._stop_with_status(
                    plan,
                    execution,
                    status="drift_detected" if candidate else "discovery_failed",
                    failure_reason="form_controls_not_found",
                    message="我没有稳定识别到剩余表单控件，已停止在提交前。",
                    evidence_refs=evidence_refs,
                    result=snapshot,
                    trace_id=trace_id,
                )
            submit_selector = _form_selector(parser)
            if not submit_selector and stable_after_wait:
                submit_selector = submit_selector_after_wait
            if not submit_selector:
                return await self._stop_with_status(
                    plan,
                    execution,
                    status="drift_detected" if candidate else "discovery_failed",
                    failure_reason="submit_control_not_found",
                    message="我没有稳定识别到最终提交控件，已停止在提交前。",
                    evidence_refs=evidence_refs,
                    result=snapshot,
                    trace_id=trace_id,
                )
            metadata = {
                **dict(plan.get("metadata") or {}),
                "field_selectors": selector_manifest,
                "submit_selector": submit_selector,
                "form_selector": submit_selector,
                "candidate_reused": bool(candidate),
            }
            await self._repo.update_plan(
                plan["plan_id"],
                {
                    "current_url": str(snapshot.get("url") or current_url),
                    "metadata": metadata,
                    "evidence": {
                        **dict(plan.get("evidence") or {}),
                        "pre_submit": evidence_refs,
                    },
                    "updated_at": utc_now_iso(),
                },
            )
            plan = await self._require_plan(plan["plan_id"])
            approval = await self._create_boundary_approval(
                plan,
                action_name="browser.submit",
                evidence_refs=evidence_refs,
                trace_id=trace_id,
            )
            await self._insert_step(
                plan,
                step_order=len(await self._repo.list_steps(plan["plan_id"])) + 1,
                step_type="approval_boundary",
                tool_name="browser.submit",
                selector=submit_selector,
                label="submit",
                status="awaiting_approval",
                risk_level=plan.get("risk_level") or "R5",
                requires_approval=True,
                input_redacted={"approval_payload_hash": _payload_hash(plan)},
                evidence_refs=evidence_refs[-2:],
                approval_id=approval.approval_id,
                trace_id=trace_id,
            )
            return await self._mark_awaiting_approval(
                plan,
                execution,
                approval_id=approval.approval_id,
                evidence_refs=evidence_refs,
                message="草稿已填好，还没有提交。确认后我再继续。",
                trace_id=trace_id,
            )
        return await self._stop_with_status(
            plan,
            execution,
            status="discovery_failed",
            failure_reason="workflow_step_limit_reached",
            message="浏览器探索达到步数上限，已停止在外部状态变更前。",
            evidence_refs=evidence_refs,
            result={"max_steps": max_steps},
            trace_id=trace_id,
        )

    async def _snapshot(
        self,
        plan: dict[str, Any],
        *,
        trace_id: str | None,
        label: str,
    ) -> dict[str, Any]:
        args = {
            "url": plan.get("current_url") or plan.get("target_url"),
            "display_name": label,
        }
        args.update(_session_args(plan))
        args.update(_browser_runtime_args(plan))
        response = await self._tools.execute(
            ToolExecuteRequest(
                task_id=plan.get("task_id"),
                member_id=str(plan.get("member_id") or "mem_xiaoyao"),
                tool_name="browser.snapshot",
                args=args,
            ),
            trace_id=trace_id,
        )
        return response.result

    async def _tool(
        self,
        plan: dict[str, Any],
        tool_name: str,
        args: dict[str, Any],
        *,
        approval_id: str | None = None,
        trace_id: str | None = None,
    ) -> Any:
        merged_args = {**args, **_session_args(plan), **_browser_runtime_args(plan)}
        return await self._tools.execute(
            ToolExecuteRequest(
                task_id=plan.get("task_id"),
                member_id=str(plan.get("member_id") or "mem_xiaoyao"),
                tool_name=tool_name,
                args=merged_args,
                approval_id=approval_id,
            ),
            trace_id=trace_id,
        )

    async def _start_execution(
        self,
        plan: dict[str, Any],
        *,
        trace_id: str | None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        execution = {
            "execution_id": new_id("bwexe"),
            "plan_id": plan["plan_id"],
            "organization_id": plan["organization_id"],
            "member_id": plan["member_id"],
            "action_type": plan["action_type"],
            "status": "exploring",
            "result": {},
            "evidence_refs": [],
            "trace_id": trace_id,
            "started_at": now,
            "created_at": now,
            "updated_at": now,
        }
        await self._repo.insert_execution(execution)
        await self._repo.update_plan(
            plan["plan_id"],
            {"status": "exploring", "trace_id": trace_id, "updated_at": now},
        )
        await self._event(
            plan,
            execution_id=execution["execution_id"],
            event_type="workflow.execution_started",
            payload={"action_type": plan["action_type"]},
            trace_id=trace_id,
        )
        return execution

    async def _finish_execution(
        self,
        execution: dict[str, Any],
        *,
        status: str,
        message: str,
        result: dict[str, Any] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        failure_reason: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        await self._repo.update_execution(
            execution["execution_id"],
            {
                "status": status,
                "result": redact(result or {}),
                "evidence_refs": redact(evidence_refs or []),
                "failure_reason": failure_reason,
                "user_visible_message": message,
                "completed_at": utc_now_iso()
                if status not in {"awaiting_approval", "exploring"}
                else None,
                "trace_id": trace_id,
                "updated_at": utc_now_iso(),
            },
        )

    async def _stop_with_status(
        self,
        plan: dict[str, Any],
        execution: dict[str, Any],
        *,
        status: str,
        failure_reason: str,
        message: str,
        evidence_refs: list[dict[str, Any]],
        result: dict[str, Any],
        trace_id: str | None,
    ) -> BrowserWorkflowPlanResponse:
        await self._finish_execution(
            execution,
            status=status,
            failure_reason=failure_reason,
            message=message,
            result=result,
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        await self._repo.update_plan(
            plan["plan_id"],
            {
                "status": status,
                "failure_reason": failure_reason,
                "evidence": {**dict(plan.get("evidence") or {}), status: evidence_refs},
                "updated_at": utc_now_iso(),
            },
        )
        await self._event(
            plan,
            execution_id=execution["execution_id"],
            event_type=f"workflow.{status}",
            payload={"failure_reason": failure_reason},
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        if status in {"drift_detected", "discovery_failed"}:
            await self._learn_candidate(
                plan,
                evidence_refs=evidence_refs,
                trace_id=trace_id,
                failed=True,
            )
        return await self._response(
            plan["plan_id"],
            execution=await self._repo.latest_execution(plan["plan_id"]),
            discovery=_discovery(
                plan,
                status=status,
                message=message,
                evidence_refs=evidence_refs,
                failure_reason=failure_reason,
            ),
            message=message,
            next_step="human_resolution_required"
            if status == "challenge_detected"
            else "provide_stable_target_or_adapter",
        )

    async def _create_boundary_approval(
        self,
        plan: dict[str, Any],
        *,
        action_name: str,
        evidence_refs: list[dict[str, Any]],
        trace_id: str | None,
    ) -> Any:
        payload = _approval_payload(plan, action_name=action_name, evidence_refs=evidence_refs)
        approval = await self._approvals.create_approval(
            task_id=str(plan["task_id"]),
            organization_id=str(plan["organization_id"]),
            requested_action=action_name,
            risk_level=RiskLevel(plan.get("risk_level") or "R5"),
            summary=_approval_summary(plan),
            payload=payload,
            trace_id=trace_id,
        )
        await self._repo.update_plan(
            plan["plan_id"],
            {
                "approval_id": approval.approval_id,
                "approval_binding": payload,
                "updated_at": utc_now_iso(),
            },
        )
        return approval

    async def _mark_awaiting_approval(
        self,
        plan: dict[str, Any],
        execution: dict[str, Any],
        *,
        approval_id: str,
        evidence_refs: list[dict[str, Any]],
        message: str,
        trace_id: str | None,
    ) -> BrowserWorkflowPlanResponse:
        await self._finish_execution(
            execution,
            status="awaiting_approval",
            message=message,
            result={"approval_id": approval_id, "pre_submit_evidence_count": len(evidence_refs)},
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        await self._repo.update_plan(
            plan["plan_id"],
            {
                "status": "awaiting_approval",
                "approval_id": approval_id,
                "evidence": {
                    **dict(plan.get("evidence") or {}),
                    "before_approval": evidence_refs,
                },
                "updated_at": utc_now_iso(),
            },
        )
        await self._event(
            plan,
            execution_id=execution["execution_id"],
            event_type="workflow.awaiting_approval",
            payload={"approval_id": approval_id},
            evidence_refs=evidence_refs,
            trace_id=trace_id,
        )
        return await self._response(
            plan["plan_id"],
            execution=await self._repo.latest_execution(plan["plan_id"]),
            discovery=_discovery(
                plan,
                status="awaiting_approval",
                message=message,
                evidence_refs=evidence_refs,
            ),
            message=message,
            next_step="awaiting_approval",
        )

    async def _approval_from_tool_response(
        self,
        plan: dict[str, Any],
        execution: dict[str, Any],
        tool_response: Any,
        *,
        evidence_refs: list[dict[str, Any]],
        trace_id: str | None,
    ) -> BrowserWorkflowPlanResponse:
        approval = tool_response.approval
        await self._repo.update_plan(
            plan["plan_id"],
            {
                "approval_id": approval.approval_id,
                "status": "awaiting_approval",
                "updated_at": utc_now_iso(),
            },
        )
        return await self._mark_awaiting_approval(
            plan,
            execution,
            approval_id=approval.approval_id,
            evidence_refs=evidence_refs,
            message="该浏览器动作需要确认，确认后我再继续。",
            trace_id=trace_id,
        )

    async def _learn_candidate(
        self,
        plan: dict[str, Any],
        *,
        evidence_refs: list[dict[str, Any]],
        trace_id: str | None,
        failed: bool = False,
    ) -> BrowserWorkflowCandidate | None:
        host = str(plan.get("target_key") or _host(plan.get("target_url")) or "")
        if not host:
            return None
        now = utc_now_iso()
        manifest = {
            "target_url": plan.get("target_url"),
            "action_type": plan.get("action_type"),
            "field_selectors": dict(plan.get("metadata", {}).get("field_selectors") or {}),
            "submit_selector": plan.get("metadata", {}).get("submit_selector"),
            "entry_selector": plan.get("metadata", {}).get("entry_selector"),
            "runtime_options": dict(plan.get("metadata", {}).get("runtime_options") or {}),
            "mobile_fallback_used": bool(plan.get("metadata", {}).get("mobile_fallback_used")),
            "frame_or_tab_capable": True,
            "selector_strategy": "role_or_text_label_then_css",
            "wait_conditions": {
                "dynamic_dom": "form/input/textarea/select/contenteditable/button",
                "load_state": dict(plan.get("metadata", {}).get("runtime_options") or {}).get(
                    "wait_until",
                    "domcontentloaded",
                ),
            },
            "success_validation": {"evidence": "post_action_snapshot_or_artifact"},
            "failure_markers": list(CHALLENGE_MARKERS),
            "source": "autonomous_browser_workflow",
            "phase": "phase54_browser_workflow_resilience",
        }
        candidate = await self._repo.upsert_candidate(
            {
                "candidate_id": new_id("bwcand"),
                "organization_id": plan.get("organization_id") or "org_default",
                "target_key": plan.get("target_key"),
                "host": host,
                "action_type": plan.get("action_type"),
                "status": "test_only",
                "source": "autonomous_browser_workflow",
                "manifest": manifest,
                "evidence_refs": evidence_refs,
                "success_count": 0 if failed else 1,
                "failure_count": 1 if failed else 0,
                "confidence": 0.82 if not failed else 0.35,
                "last_plan_id": plan.get("plan_id"),
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )
        if plan.get("task_id") and not failed:
            await self._task_repo.insert_task_reflection_candidate(
                {
                    "candidate_id": new_id("trc"),
                    "organization_id": plan.get("organization_id") or "org_default",
                    "task_id": plan["task_id"],
                    "candidate_type": "skill_candidate",
                    "status": "candidate",
                    "confidence": 0.82,
                    "summary": "自主浏览器工作流候选 Skill/Adapter",
                    "payload": {
                        "source": "autonomous_browser_workflow",
                        "workflow_candidate_id": candidate["candidate_id"],
                        "action_type": plan.get("action_type"),
                        "target_key": host,
                    },
                    "source_refs": evidence_refs,
                    "risk_level": plan.get("risk_level") or "R3",
                    "trace_id": trace_id,
                    "created_at": now,
                }
            )
        await self._audit.write_event(
            actor_type="system",
            action="browser_workflow.candidate_learned",
            object_type="browser_workflow_candidate",
            object_id=candidate["candidate_id"],
            summary="自主浏览器工作流候选已沉淀",
            risk_level=RiskLevel.R1,
            payload={
                "action_type": plan.get("action_type"),
                "host": host,
                "status": candidate.get("status"),
                "recommended": candidate.get("recommended"),
            },
            trace_id=trace_id,
        )
        return BrowserWorkflowCandidate(**candidate)

    async def _insert_step(
        self,
        plan: dict[str, Any],
        *,
        step_order: int,
        step_type: str,
        tool_name: str | None = None,
        selector: str | None = None,
        label: str | None = None,
        status: str = "planned",
        risk_level: str = "R1",
        requires_approval: bool = False,
        input_redacted: dict[str, Any] | None = None,
        output_redacted: dict[str, Any] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        approval_id: str | None = None,
        tool_call_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        now = utc_now_iso()
        await self._repo.insert_step(
            {
                "step_id": new_id("bwstep"),
                "plan_id": plan["plan_id"],
                "step_order": step_order,
                "step_type": step_type,
                "tool_name": tool_name,
                "selector": selector,
                "label": label,
                "status": status,
                "risk_level": risk_level,
                "requires_approval": requires_approval,
                "input_redacted": redact(input_redacted or {}),
                "output_redacted": redact(output_redacted or {}),
                "evidence_refs": redact(evidence_refs or []),
                "approval_id": approval_id,
                "tool_call_id": tool_call_id,
                "trace_id": trace_id,
                "created_at": now,
                "updated_at": now,
            }
        )

    async def _event(
        self,
        plan: dict[str, Any],
        *,
        event_type: str,
        payload: dict[str, Any],
        execution_id: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        trace_id: str | None = None,
    ) -> None:
        await self._repo.insert_event(
            {
                "event_id": new_id("bwevt"),
                "plan_id": plan["plan_id"],
                "organization_id": plan.get("organization_id") or "org_default",
                "execution_id": execution_id,
                "event_type": event_type,
                "payload_redacted": redact(payload),
                "evidence_refs": redact(evidence_refs or []),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )

    async def _require_plan(self, plan_id: str) -> dict[str, Any]:
        plan = await self._repo.get_plan(plan_id)
        if plan is None:
            raise AppError(ErrorCode.NOT_FOUND, "浏览器工作流计划不存在", status_code=404)
        return plan

    async def _response(
        self,
        plan_id: str,
        *,
        message: str,
        next_step: str | None = None,
        execution: dict[str, Any] | None = None,
        discovery: BrowserWorkflowDiscoveryResult | None = None,
    ) -> BrowserWorkflowPlanResponse:
        plan = await self._require_plan(plan_id)
        steps = await self._repo.list_steps(plan_id)
        candidates = await self._repo.list_candidates_for_plan(plan)
        latest = execution or await self._repo.latest_execution(plan_id)
        return BrowserWorkflowPlanResponse(
            plan=BrowserWorkflowPlan(**plan),
            execution=BrowserWorkflowExecution(**latest) if latest else None,
            discovery=discovery,
            steps=[BrowserWorkflowStep(**item) for item in steps],
            candidate=BrowserWorkflowCandidate(**candidates[0]) if candidates else None,
            message=message,
            next_step=next_step,
        )


@dataclass
class _HtmlControl:
    tag: str
    attrs: dict[str, str]
    label: str = ""

    @property
    def input_type(self) -> str:
        return (self.attrs.get("type") or "").lower()


@dataclass
class _HtmlForm:
    attrs: dict[str, str]
    controls: list[_HtmlControl] = field(default_factory=list)


class _WorkflowHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[_HtmlForm] = []
        self.links: list[dict[str, str]] = []
        self.buttons: list[dict[str, str]] = []
        self.tables: list[list[list[str]]] = []
        self.labels_by_for: dict[str, str] = {}
        self._form_stack: list[_HtmlForm] = []
        self._link_stack: list[dict[str, Any]] = []
        self._button_stack: list[dict[str, Any]] = []
        self._label_stack: list[dict[str, Any]] = []
        self._table_stack: list[list[list[str]]] = []
        self._row_stack: list[list[str]] = []
        self._cell_parts: list[str] | None = None

    @classmethod
    def from_text(cls, text: str) -> _WorkflowHtmlParser:
        parser = cls()
        parser.feed(text)
        parser.close()
        return parser

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): value or "" for key, value in attrs}
        if tag == "form":
            self._form_stack.append(_HtmlForm(attrs=values))
        if tag == "label":
            self._label_stack.append({"attrs": values, "parts": []})
        if tag in {"input", "textarea", "select"} or values.get("contenteditable") == "true":
            control = _HtmlControl(tag=tag, attrs=values)
            if self._label_stack:
                control.label = _compact_text("".join(self._label_stack[-1]["parts"]))
            if self._form_stack:
                self._form_stack[-1].controls.append(control)
            else:
                self.forms.append(_HtmlForm(attrs={}, controls=[control]))
        if tag == "button":
            self._button_stack.append({"attrs": values, "parts": []})
            if self._form_stack and values.get("name"):
                self._form_stack[-1].controls.append(_HtmlControl(tag=tag, attrs=values))
        if tag == "a":
            self._link_stack.append({"attrs": values, "parts": []})
        if tag == "table":
            self._table_stack.append([])
        if tag == "tr":
            self._row_stack.append([])
        if tag in {"td", "th"}:
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "form" and self._form_stack:
            self.forms.append(self._form_stack.pop())
        if tag == "label" and self._label_stack:
            item = self._label_stack.pop()
            label_text = _compact_text("".join(item["parts"]))
            target = item["attrs"].get("for")
            if target and label_text:
                self.labels_by_for[target] = label_text
        if tag == "button" and self._button_stack:
            item = self._button_stack.pop()
            attrs = dict(item["attrs"])
            attrs["text"] = _compact_text("".join(item["parts"]))
            self.buttons.append(attrs)
        if tag == "a" and self._link_stack:
            item = self._link_stack.pop()
            attrs = dict(item["attrs"])
            attrs["text"] = _compact_text("".join(item["parts"]))
            self.links.append(attrs)
        if tag in {"td", "th"} and self._cell_parts is not None:
            if self._row_stack:
                self._row_stack[-1].append(_compact_text("".join(self._cell_parts)))
            self._cell_parts = None
        if tag == "tr" and self._row_stack:
            row = self._row_stack.pop()
            if self._table_stack and any(row):
                self._table_stack[-1].append(row)
        if tag == "table" and self._table_stack:
            table = self._table_stack.pop()
            if table:
                self.tables.append(table)

    def handle_data(self, data: str) -> None:
        if self._link_stack:
            self._link_stack[-1]["parts"].append(data)
        if self._button_stack:
            self._button_stack[-1]["parts"].append(data)
        if self._label_stack:
            self._label_stack[-1]["parts"].append(data)
        if self._cell_parts is not None:
            self._cell_parts.append(data)


def _normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not text.startswith(("http://", "https://")):
        return None
    return text


def _extract_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s，。)）]+", text)
    return _normalize_url(match.group(0)) if match else None


def _host(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.netloc.lower() or None


def _normalize_action_type(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    return text if text in SUPPORTED_ACTION_TYPES else None


def _classify_action(text: str, constraints: dict[str, Any]) -> str:
    explicit = _normalize_action_type(str(constraints.get("action_type") or ""))
    if explicit:
        return explicit
    lowered = text.lower()
    if any(token in lowered for token in ("提取", "抓取", "抽取", "extract", "scrape", "table")):
        return "extract_data"
    if any(token in lowered for token in ("下载", "报表", "download", "report")):
        return "download_report"
    if any(token in lowered for token in ("上传", "upload")):
        return "upload_file"
    if any(token in lowered for token in ("预约", "预订", "下单", "票", "book", "order", "ticket")):
        return "book_or_order"
    if any(
        token in lowered
        for token in ("后台", "状态", "修改", "admin", "saas", "crm", "update")
    ):
        return "admin_update"
    if any(token in lowered for token in ("测试", "检查", "验证页面", "qa", "test")):
        return "qa_test"
    if any(token in lowered for token in ("填写", "填表", "报名", "form", "apply")):
        return "fill_form"
    return "multi_step_workflow"


def _content_summary(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", "", text).strip()
    return str(redact(cleaned))[:200]


def _risk_for_action(action_type: str) -> str:
    if action_type in {"extract_data", "qa_test"}:
        return "R2"
    if action_type == "download_report":
        return "R3"
    if action_type in {"upload_file", "admin_update", "book_or_order"}:
        return "R5"
    return "R4"


def _session_args(plan: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(plan.get("metadata") or {})
    args: dict[str, Any] = {}
    for key in ("session_handle_id", "browser_session_handle_id"):
        value = metadata.get(key) or metadata.get("constraints", {}).get(key)
        if value:
            args[key] = value
    return args


def _browser_runtime_args(plan: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(plan.get("metadata") or {})
    runtime = {
        **dict(metadata.get("runtime_options") or {}),
        **dict(metadata.get("constraints", {}).get("browser_runtime") or {}),
    }
    args: dict[str, Any] = {}
    for key in ("provider_mode", "viewport_profile", "action_strategy", "wait_until"):
        value = runtime.get(key)
        if value:
            args[key] = value
    return args


def _snapshot_text(result: dict[str, Any]) -> str:
    return str(result.get("snapshot") or result.get("content_preview") or "")


def _challenge_reason(text: str) -> str | None:
    lowered = text.lower()
    for marker in CHALLENGE_MARKERS:
        if marker.lower() in lowered:
            return "challenge_detected"
    if "请登录" in text or "登录" in text and "密码" in text:
        return "challenge_detected"
    return None


def _field_fill_actions(
    parser: _WorkflowHtmlParser,
    form_data: dict[str, Any],
    *,
    filled_keys: set[str],
    selector_manifest: dict[str, str],
) -> list[tuple[str, str, Any, str, str]]:
    actions: list[tuple[str, str, Any, str, str]] = []
    for key, value in form_data.items():
        if key in filled_keys:
            continue
        selector = selector_manifest.get(key)
        label = key
        if selector and _selector_exists(parser, selector):
            control = _control_for_selector(parser, selector)
            actions.append((key, selector, value, label, _tool_for_control(control)))
            continue
        control = _find_control(parser, key, value)
        if control is None:
            continue
        selector = _control_selector(control)
        if selector:
            actions.append(
                (
                    key,
                    selector,
                    value,
                    _control_label(control, parser) or key,
                    _tool_for_control(control),
                )
            )
    return actions


def _find_control(
    parser: _WorkflowHtmlParser,
    key: str,
    value: Any | None = None,
) -> _HtmlControl | None:
    wanted = _norm(key)
    wanted_value = _norm(str(value or ""))
    fallback: _HtmlControl | None = None
    for form in parser.forms:
        for control in form.controls:
            if (control.attrs.get("type") or "").lower() in {"hidden", "submit", "button"}:
                continue
            haystack = " ".join(
                [
                    control.attrs.get("name", ""),
                    control.attrs.get("id", ""),
                    control.attrs.get("placeholder", ""),
                    control.attrs.get("aria-label", ""),
                    control.attrs.get("data-field", ""),
                    control.attrs.get("data-label", ""),
                    _control_label(control, parser),
                ]
            )
            if wanted and wanted in _norm(haystack):
                if control.input_type in {"radio", "checkbox"} and wanted_value:
                    option_text = " ".join(
                        [
                            control.attrs.get("value", ""),
                            control.attrs.get("id", ""),
                            _control_label(control, parser),
                        ]
                    )
                    if wanted_value in _norm(option_text):
                        return control
                    fallback = fallback or control
                    continue
                return control
    return fallback


def _control_for_selector(
    parser: _WorkflowHtmlParser,
    selector: str,
) -> _HtmlControl | None:
    for form in parser.forms:
        for control in form.controls:
            if _control_selector(control) == selector:
                return control
    return None


def _selector_exists(parser: _WorkflowHtmlParser, selector: str) -> bool:
    for form in parser.forms:
        for control in form.controls:
            if _control_selector(control) == selector:
                return True
    return selector == "form" and bool(parser.forms)


def _control_selector(control: _HtmlControl) -> str | None:
    browser_selector = control.attrs.get("data-browser-selector")
    if browser_selector:
        return browser_selector
    control_id = control.attrs.get("id")
    if control_id:
        return f"#{control_id}"
    name = control.attrs.get("name")
    if name:
        return f"[name='{name}']"
    return None


def _control_label(control: _HtmlControl, parser: _WorkflowHtmlParser | None = None) -> str:
    label_for_id = (
        parser.labels_by_for.get(control.attrs.get("id", ""))
        if parser is not None and control.attrs.get("id")
        else ""
    )
    return (
        control.label
        or label_for_id
        or control.attrs.get("aria-label")
        or control.attrs.get("placeholder")
        or control.attrs.get("data-label")
        or control.attrs.get("name")
        or control.attrs.get("id")
        or control.tag
    )


def _tool_for_control(control: _HtmlControl | None) -> str:
    if control is None:
        return "browser.fill"
    if control.tag == "select":
        return "browser.select"
    if control.input_type in {"checkbox", "radio"}:
        return "browser.check"
    return "browser.fill"


def _find_next_selector(parser: _WorkflowHtmlParser) -> str | None:
    for link in parser.links:
        text = f"{link.get('text', '')} {link.get('id', '')} {link.get('href', '')}".lower()
        if any(marker in text for marker in NEXT_MARKERS):
            if link.get("id"):
                return f"#{link['id']}"
    return None


def _find_entry_selector(parser: _WorkflowHtmlParser) -> str | None:
    for item in [*parser.buttons, *parser.links]:
        text = " ".join(
            [
                item.get("text", ""),
                item.get("id", ""),
                item.get("name", ""),
                item.get("aria-label", ""),
                item.get("href", ""),
                item.get("class", ""),
            ]
        ).lower()
        if any(marker.lower() in text for marker in ENTRY_MARKERS):
            if item.get("id"):
                return f"#{item['id']}"
            if item.get("name"):
                return f"[name='{item['name']}']"
            label = item.get("text") or item.get("aria-label")
            if label:
                return f"text={label}"
    return None


def _form_selector(parser: _WorkflowHtmlParser) -> str | None:
    for form in parser.forms:
        if _form_has_submit(form, parser):
            form_id = form.attrs.get("id")
            return f"#{form_id}" if form_id else "form"
    if parser.forms:
        form_id = parser.forms[0].attrs.get("id")
        return f"#{form_id}" if form_id else "form"
    return None


def _form_has_submit(form: _HtmlForm, parser: _WorkflowHtmlParser) -> bool:
    for control in form.controls:
        if (control.attrs.get("type") or "").lower() in {"submit", "button"}:
            return True
    for button in parser.buttons:
        text = " ".join([button.get("text", ""), button.get("id", ""), button.get("name", "")])
        if any(marker in text.lower() for marker in SUBMIT_MARKERS):
            return True
    return False


def _find_upload_selector(parser: _WorkflowHtmlParser) -> str | None:
    for form in parser.forms:
        for control in form.controls:
            if (control.attrs.get("type") or "").lower() == "file":
                return _control_selector(control)
    return None


def _find_download_url(parser: _WorkflowHtmlParser, base_url: str) -> str | None:
    for link in parser.links:
        text = f"{link.get('text', '')} {link.get('href', '')}".lower()
        if any(marker in text for marker in DOWNLOAD_MARKERS) and link.get("href"):
            return urljoin(base_url, link["href"])
    return None


def _qa_assertions(
    plan: dict[str, Any],
    html: str,
    parser: _WorkflowHtmlParser,
) -> list[dict[str, Any]]:
    checks = plan.get("metadata", {}).get("constraints", {}).get("assertions") or []
    if not isinstance(checks, list):
        checks = []
    assertions: list[dict[str, Any]] = []
    for item in checks[:20]:
        text = str(item.get("text") if isinstance(item, dict) else item)
        assertions.append({"text": text, "passed": text in html})
    if not assertions:
        assertions.append({"name": "page_loaded", "passed": bool(html)})
        assertions.append(
            {"name": "controls_detected", "passed": bool(parser.forms or parser.links)}
        )
    return assertions


def _row_count(extracted: Any) -> int:
    if isinstance(extracted, dict):
        rows = extracted.get("rows")
        if isinstance(rows, list):
            return len(rows)
        tables = extracted.get("tables")
        if isinstance(tables, list):
            return sum(len(table.get("rows", [])) for table in tables if isinstance(table, dict))
    return 0


def _evidence_ref(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "browser_evidence_id": result.get("browser_evidence_id"),
        "url": result.get("url"),
        "action_status": result.get("action_status"),
        "artifact_id": result.get("artifact_id")
        or (result.get("artifact") or {}).get("artifact_id")
        or (result.get("download") or {}).get("artifact_id"),
        "summary": result.get("evidence_summary"),
        "frame_summary": result.get("frame_summary"),
        "tab_summary": result.get("tab_summary"),
        "console_summary": result.get("console_summary"),
        "network_summary": result.get("network_summary"),
    }


def _discovery(
    plan: dict[str, Any],
    *,
    status: str,
    message: str,
    evidence_refs: list[dict[str, Any]],
    failure_reason: str | None = None,
) -> BrowserWorkflowDiscoveryResult:
    return BrowserWorkflowDiscoveryResult(
        discovery_id=new_id("bwdisc"),
        plan_id=plan["plan_id"],
        action_type=plan["action_type"],
        target_url=plan.get("target_url"),
        status=status,
        learned_workflow_manifest={
            "action_type": plan.get("action_type"),
            "target_key": plan.get("target_key"),
            "source": "autonomous_browser_workflow",
        },
        confidence=0.8 if failure_reason is None else 0.35,
        evidence_refs=redact(evidence_refs),
        failure_reason=failure_reason,
        user_visible_message=message,
        candidate_id=plan.get("metadata", {}).get("candidate_id"),
    )


def _approval_payload(
    plan: dict[str, Any],
    *,
    action_name: str,
    evidence_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    form_data = redact(plan.get("form_data") or {})
    file_refs = redact(plan.get("file_refs") or [])
    return {
        "action_type": plan.get("action_type"),
        "requested_action": action_name,
        "target_url": plan.get("current_url") or plan.get("target_url"),
        "account_session_summary": _session_summary(plan),
        "content_summary": plan.get("content_summary"),
        "form_summary": _summarize_mapping(form_data),
        "file_summary": _summarize_files(file_refs),
        "before_submit_snapshot_evidence": evidence_refs[-3:],
        "workflow_plan_id": plan.get("plan_id"),
        "content_hash": _payload_hash(plan),
        "payload_hash": _payload_hash(plan),
    }


def _approval_summary(plan: dict[str, Any]) -> str:
    action_type = str(plan.get("action_type") or "browser_workflow")
    return f"确认后将执行浏览器工作流外部状态变更：{action_type}"


def _session_summary(plan: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(plan.get("metadata") or {})
    constraints = dict(metadata.get("constraints") or {})
    return {
        "session_handle_present": bool(
            metadata.get("session_handle_id")
            or metadata.get("browser_session_handle_id")
            or constraints.get("session_handle_id")
            or constraints.get("browser_session_handle_id")
        ),
        "selected_account_id": constraints.get("selected_account_id"),
        "secret_material_visible": False,
    }


def _summarize_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"field_count": 0}
    return {
        "field_count": len(value),
        "fields": sorted(str(key) for key in value)[:20],
    }


def _summarize_files(value: Any) -> dict[str, Any]:
    if not isinstance(value, list):
        return {"file_count": 0}
    return {
        "file_count": len(value),
        "refs": [
            {
                "artifact_id": item.get("artifact_id"),
                "asset_handle_id": item.get("asset_handle_id"),
            }
            for item in value
            if isinstance(item, dict)
        ][:10],
    }


def _payload_hash(plan: dict[str, Any]) -> str:
    raw = repr(
        {
            "action_type": plan.get("action_type"),
            "target_url": plan.get("current_url") or plan.get("target_url"),
            "form_data": redact(plan.get("form_data") or {}),
            "file_refs": redact(plan.get("file_refs") or []),
        }
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _has_upload_ref(file_refs: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(item, dict) and (item.get("artifact_id") or item.get("asset_handle_id"))
        for item in file_refs
    )


def _first_artifact_id(file_refs: list[dict[str, Any]]) -> str | None:
    for item in file_refs:
        if isinstance(item, dict) and item.get("artifact_id"):
            return str(item["artifact_id"])
    return None


def _first_asset_handle_id(file_refs: list[dict[str, Any]]) -> str | None:
    for item in file_refs:
        if isinstance(item, dict) and item.get("asset_handle_id"):
            return str(item["asset_handle_id"])
    return None


def _safe_download_name(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1] or "report.bin"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name.split("?", 1)[0])[:80] or "report.bin"


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())
