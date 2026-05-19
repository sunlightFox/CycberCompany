from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

from chat_runtime import canonical_route_name
from core_types import ChatEvent, ChatEventType, ErrorCode, TaskMode
from response_composer import (
    ResponseComposer,
    canonical_action_status,
    mirrored_status_payload,
    normalize_action_status_semantics,
)
from trace_service import redact

from app.core.errors import AppError
from app.services.chat_intent_router import (
    ChatRouteDecision,
    OfficeChatRequest,
    office_skill_input,
    preferred_office_tool_name,
)
from app.services.office_productivity import office_request_from_chat_request


class ChatDirectRoutesRuntime:
    """Compatibility runtime for readonly/direct routes extracted from chat.py."""

    def __init__(
        self,
        *,
        composer: ResponseComposer,
        tool_runtime: Any | None,
        readonly_execution: Any,
        task_engine: Any | None,
        capability_boundary: Any,
        task_coordinator: Any,
        approval_service: Any | None,
        turn_recovery: Any | None,
        emit_and_record: Callable[..., Awaitable[ChatEvent]],
        complete_without_model: Callable[..., AsyncIterator[ChatEvent]],
        fail_turn: Callable[..., AsyncIterator[ChatEvent]],
        response_plan_for_status: Callable[..., Any],
        response_plan_for_action_status: Callable[..., Any],
        action_status_facts_for_turn: Callable[..., dict[str, Any]],
        enabled_office_skill_id: Callable[[OfficeChatRequest], Awaitable[str | None]],
        office_skill_has_grant: Callable[[str, str, str], Awaitable[bool]],
        latest_office_artifact_id: Callable[[str, str], Awaitable[str | None]],
        office_missing_capability_text: Callable[[OfficeChatRequest, str], str],
        office_next_actions: Callable[[OfficeChatRequest, str], list[str]],
        office_task_reply: Callable[[OfficeChatRequest, Any, list[Any]], str],
        office_artifact_refs: Callable[[list[Any], str], list[dict[str, Any]]],
        recover_task_in_turn: Callable[..., Awaitable[Any]],
        host_filesystem_list_reply: Callable[[dict[str, Any]], str],
        host_filesystem_list_error_reply: Callable[[str, AppError], str],
        browser_read_page_reply: Callable[[dict[str, Any]], str],
        browser_read_page_error_reply: Callable[[AppError], str],
        browser_read_page_payload: Callable[[dict[str, Any]], dict[str, Any]],
        terminal_command_reply: Callable[[str, dict[str, Any]], str],
        terminal_command_error_reply: Callable[[str, AppError], str],
        record_failure_experience: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._composer = composer
        self._tool_runtime = tool_runtime
        self._readonly_execution = readonly_execution
        self._task_engine = task_engine
        self._capability_boundary = capability_boundary
        self._task_coordinator = task_coordinator
        self._approval_service = approval_service
        self._turn_recovery = turn_recovery
        self._emit_and_record = emit_and_record
        self._complete_without_model = complete_without_model
        self._fail_turn = fail_turn
        self._response_plan_for_status = response_plan_for_status
        self._response_plan_for_action_status = response_plan_for_action_status
        self._action_status_facts_for_turn = action_status_facts_for_turn
        self._enabled_office_skill_id = enabled_office_skill_id
        self._office_skill_has_grant = office_skill_has_grant
        self._latest_office_artifact_id = latest_office_artifact_id
        self._office_missing_capability_text = office_missing_capability_text
        self._office_next_actions = office_next_actions
        self._office_task_reply = office_task_reply
        self._office_artifact_refs = office_artifact_refs
        self._recover_task_in_turn = recover_task_in_turn
        self._host_filesystem_list_reply = host_filesystem_list_reply
        self._host_filesystem_list_error_reply = host_filesystem_list_error_reply
        self._browser_read_page_reply = browser_read_page_reply
        self._browser_read_page_error_reply = browser_read_page_error_reply
        self._browser_read_page_payload = browser_read_page_payload
        self._terminal_command_reply = terminal_command_reply
        self._terminal_command_error_reply = terminal_command_error_reply
        self._record_failure_experience = record_failure_experience

    def _tool_call_value(self, tool_call: Any, field: str, default: Any = None) -> Any:
        if tool_call is None:
            return default
        if isinstance(tool_call, dict):
            return tool_call.get(field, default)
        return getattr(tool_call, field, default)

    def _approval_value(self, approval: Any, field: str, default: Any = None) -> Any:
        if approval is None:
            return default
        if isinstance(approval, dict):
            return approval.get(field, default)
        return getattr(approval, field, default)

    def _artifact_refs(self, artifacts: list[Any] | None) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for artifact in list(artifacts or []):
            if isinstance(artifact, dict):
                artifact_id = artifact.get("artifact_id")
                kind = artifact.get("kind")
                uri = artifact.get("artifact_uri")
            else:
                artifact_id = getattr(artifact, "artifact_id", None)
                kind = getattr(artifact, "kind", None)
                uri = getattr(artifact, "artifact_uri", None)
            refs.append(
                {
                    "artifact_id": artifact_id,
                    "kind": kind,
                    "artifact_uri": uri,
                }
            )
        return refs

    def _single_turn_tool_result_context(
        self,
        *,
        response: Any,
        tool_name: str,
        source_type: str,
        trusted_level: str,
        summary: str,
        trace_id: str | None,
        evidence_refs: list[dict[str, Any]] | None = None,
        status: str | None = None,
        failure_summary: str | None = None,
    ) -> dict[str, Any]:
        result = (
            dict(response.result)
            if isinstance(getattr(response, "result", None), dict)
            else {}
        )
        tool_call = getattr(response, "tool_call", None)
        approval = getattr(response, "approval", None)
        tool_call_status = self._tool_call_value(tool_call, "status")
        tool_call_id = self._tool_call_value(tool_call, "tool_call_id")
        approval_id = self._approval_value(approval, "approval_id")
        approval_state = dict(result.get("approval_state") or {})
        if not approval_state:
            approval_state = {
                "status": (
                    "required"
                    if approval is not None or str(tool_call_status or "") == "approval_required"
                    else "not_required"
                ),
                "approval_id": approval_id,
            }
        tool_evidence_refs = evidence_refs
        if tool_evidence_refs is None:
            raw_evidence = result.get("evidence_refs")
            if isinstance(raw_evidence, list):
                tool_evidence_refs = list(raw_evidence)
            elif isinstance(result.get("browser_page_state"), dict):
                tool_evidence_refs = list(
                    result["browser_page_state"].get("evidence_refs") or []
                )
            else:
                tool_evidence_refs = []
        if source_type == "browser_snapshot":
            browser_evidence_id = str(result.get("browser_evidence_id") or "").strip()
            if browser_evidence_id:
                normalized_refs: list[dict[str, Any]] = []
                for item in list(tool_evidence_refs or []):
                    if isinstance(item, dict):
                        normalized = dict(item)
                        normalized.setdefault("browser_evidence_id", browser_evidence_id)
                        normalized_refs.append(normalized)
                tool_evidence_refs = normalized_refs or [
                    {"type": "browser_evidence", "browser_evidence_id": browser_evidence_id}
                ]
        execution_semantics = dict(result.get("execution_semantics") or {})
        if not execution_semantics:
            execution_semantics = {
                "mode": "single_turn_tool_loop",
                "source_type": source_type,
            }
        artifact_refs = self._artifact_refs(getattr(response, "artifacts", None))
        normalized_status = canonical_action_status(
            status
            or str(result.get("status") or result.get("action_status") or "")
            or ("waiting_for_approval" if approval is not None else None)
            or str(tool_call_status or "")
            or "completed"
        )
        retryable = result.get("retryable")
        if retryable is None:
            retryable = normalized_status == "failed_with_reason"
        semantics = normalize_action_status_semantics(
            {
                "status": normalized_status,
                "scope": "direct_tool",
                "evidence_summary": summary,
                "evidence_refs": list(tool_evidence_refs or []),
                "approval_state": approval_state,
                "artifact_refs": artifact_refs,
                "failure_summary": failure_summary,
                "tool_ref": {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                },
            },
            default_status=normalized_status,
            scope="direct_tool",
        )
        return {
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "status": semantics["status"],
            "execution_semantics": execution_semantics,
            "evidence_refs": list(tool_evidence_refs or []),
            "approval_state": approval_state,
            "retryable": bool(retryable),
            "artifact_refs": artifact_refs,
            "trusted_level": trusted_level,
            "failure_summary": failure_summary,
            "action_status_semantics": semantics,
            "trace_ref": {
                "kind": "tool_call",
                "tool_call_id": tool_call_id,
                "trace_scope": "chat_turn" if trace_id else "tool_runtime",
            },
            "source_type": source_type,
            "sanitized_summary": summary,
        }

    def _with_single_turn_tool_payload(
        self,
        response_plan: Any,
        *,
        route: str,
        reason_code: str,
        tool_name: str,
        response: Any,
        source_type: str,
        trusted_level: str,
        summary: str,
        trace_id: str | None,
        evidence_refs: list[dict[str, Any]] | None = None,
        status: str | None = None,
        failure_summary: str | None = None,
        extra_payload: dict[str, Any] | None = None,
        task_created: bool,
    ) -> Any:
        tool_result_context = self._single_turn_tool_result_context(
            response=response,
            tool_name=tool_name,
            source_type=source_type,
            trusted_level=trusted_level,
            summary=summary,
            trace_id=trace_id,
            evidence_refs=evidence_refs,
            status=status,
            failure_summary=failure_summary,
        )
        route_semantics = {
            "route": route,
            "route_taxonomy": canonical_route_name(route),
            "model_called": False,
            "task_created": task_created,
            "tool_created": True,
            "tool_loop": True,
            "tool_name": tool_name,
            "tool_call_id": tool_result_context["tool_call_id"],
            "approval_state": tool_result_context["approval_state"],
            "evidence_refs": tool_result_context["evidence_refs"],
            "trusted_level": tool_result_context["trusted_level"],
            "artifact_refs": tool_result_context["artifact_refs"],
            "reason_code": reason_code,
        }
        action_status_semantics = normalize_action_status_semantics(
            tool_result_context.get("action_status_semantics") or tool_result_context,
            default_status=str(tool_result_context.get("status") or "requested"),
            scope="direct_tool",
        )
        return response_plan.model_copy(
            update={
                "structured_payload": {
                    **dict(response_plan.structured_payload or {}),
                    "tool_result_context": tool_result_context,
                    "action_status_semantics": action_status_semantics,
                    "tool_status_semantics": mirrored_status_payload(
                        action_status_semantics,
                        extra=tool_result_context,
                    ),
                    "route_semantics": route_semantics,
                    **dict(extra_payload or {}),
                }
            }
        )

    def _with_single_turn_tool_error_payload(
        self,
        response_plan: Any,
        *,
        route: str,
        reason_code: str,
        tool_name: str,
        source_type: str,
        trusted_level: str,
        summary: str,
        failure_summary: str,
        status: str,
        retryable: bool,
        task_created: bool,
        extra_payload: dict[str, Any] | None = None,
    ) -> Any:
        semantics = normalize_action_status_semantics(
            {
                "status": status,
                "scope": "direct_tool",
                "evidence_summary": summary,
                "failure_summary": failure_summary,
                "approval_state": {"status": "not_required", "approval_id": None},
                "tool_ref": {"tool_name": tool_name},
            },
            default_status=status,
            scope="direct_tool",
        )
        tool_result_context = {
            "tool_name": tool_name,
            "tool_call_id": None,
            "status": semantics["status"],
            "execution_semantics": {
                "mode": "single_turn_tool_loop",
                "source_type": source_type,
            },
            "evidence_refs": [],
            "approval_state": {"status": "not_required", "approval_id": None},
            "retryable": retryable,
            "artifact_refs": [],
            "trusted_level": trusted_level,
            "failure_summary": failure_summary,
            "action_status_semantics": semantics,
            "trace_ref": {
                "kind": "tool_call",
                "tool_call_id": None,
                "trace_scope": "chat_turn",
            },
            "source_type": source_type,
            "sanitized_summary": summary,
        }
        return response_plan.model_copy(
            update={
                "structured_payload": {
                    **dict(response_plan.structured_payload or {}),
                    "tool_result_context": tool_result_context,
                    "action_status_semantics": semantics,
                    "tool_status_semantics": mirrored_status_payload(
                        semantics,
                        extra=tool_result_context,
                    ),
                    "route_semantics": {
                        "route": route,
                        "route_taxonomy": canonical_route_name(route),
                        "model_called": False,
                        "task_created": task_created,
                        "tool_created": True,
                        "tool_loop": True,
                        "tool_name": tool_name,
                        "tool_call_id": None,
                        "approval_state": tool_result_context["approval_state"],
                        "evidence_refs": [],
                        "trusted_level": trusted_level,
                        "artifact_refs": [],
                        "reason_code": reason_code,
                    },
                    **dict(extra_payload or {}),
                }
            }
        )

    async def handle_host_filesystem_list(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        if self._tool_runtime is None:
            text = "当前本机文件列表工具不可用；我没有查看目录，也不会假装已经看过。"
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="host.fs.list",
                next_actions=["检查工具注册", "稍后重试"],
                safety_notice="没有读取文件内容，也没有执行文件修改。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="system_filesystem_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        location = str(metadata.get("location") or "home")
        limit = metadata.get("limit") or 50
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": "system_filesystem_read",
                "reason_codes": ["host_filesystem_list_readonly"],
            },
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {"mode": TaskMode.DIRECT.value, "needs_tool": True},
        )
        from app.schemas.tasks import ToolExecuteRequest

        try:
            response = await self._tool_runtime.execute(
                ToolExecuteRequest(
                    turn_id=turn["turn_id"],
                    conversation_id=turn["conversation_id"],
                    session_id=turn.get("session_id"),
                    channel="local",
                    member_id=turn["member_id"],
                    tool_name="host.fs.list",
                    args={"location": location, "limit": limit},
                    idempotency_key=f"chat:{turn['turn_id']}:host.fs.list:{location}",
                ),
                trace_id=trace_id,
            )
        except AppError as exc:
            text = self._host_filesystem_list_error_reply(location, exc)
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="host.fs.list",
                next_actions=["换成桌面、下载、文档或主目录", "确认目录授权后重试"],
                safety_notice="请求被目录边界策略拦截；没有读取文件内容或修改文件。",
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "host_filesystem_list": {
                            "location": location,
                            "status": "blocked",
                            "error_code": exc.code,
                            "details": exc.details,
                        },
                    },
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="system_filesystem_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        result = response.result
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TOOL_COMPLETED,
            {
                "tool_call_id": response.tool_call.tool_call_id,
                "tool_name": "host.fs.list",
                "risk_level": response.tool_call.risk_level.value
                if hasattr(response.tool_call.risk_level, "value")
                else str(response.tool_call.risk_level),
            },
        )
        text = self._host_filesystem_list_reply(result)
        response_plan = self._response_plan_for_status(
            turn,
            summary=text,
            task_status={"status": "not_created", "reason": "readonly_host_filesystem_list"},
            tool_notice="只列出目录项元数据，没有读取文件内容、递归扫描或修改文件。",
        )
        host_evidence_refs = list(result.get("evidence_refs") or [])
        response_plan = self._with_single_turn_tool_payload(
            response_plan,
            route="host_filesystem_list",
            reason_code="host_filesystem_list_readonly",
            tool_name="host.fs.list",
            response=response,
            source_type="host_filesystem_readonly",
            trusted_level="local_runtime",
            summary=text,
            trace_id=trace_id,
            evidence_refs=host_evidence_refs,
            extra_payload={"host_filesystem_list": result},
            task_created=False,
        )
        async for event in self._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent="system_filesystem_read",
            mode=TaskMode.DIRECT.value,
            response_plan=response_plan,
        ):
            yield event

    async def handle_browser_read_page(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        url = str(metadata.get("url") or "").strip()
        if self._tool_runtime is None:
            text = "当前浏览器只读工具不可用；我没有打开网页，也不会假装已经看过。"
            response_plan = self._response_plan_for_action_status(
                turn,
                facts=self._action_status_facts_for_turn(
                    turn,
                    status="blocked",
                    route="browser_read_page",
                    action_label="查看网页",
                    failure_reason="browser_snapshot_unavailable",
                    evidence_summary=text,
                ),
                task_status={"status": "blocked_by_boundary", "reason": "browser_snapshot_unavailable"},
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "route_semantics": {
                            "route": "browser_read_page",
                            "model_called": False,
                            "task_created": False,
                            "tool_created": False,
                        },
                    }
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="browser_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        if not url:
            text = "我没有识别到可查看的链接；请把完整的 http 或 https 链接发给我。"
            response_plan = self._response_plan_for_action_status(
                turn,
                facts=self._action_status_facts_for_turn(
                    turn,
                    status="blocked",
                    route="browser_read_page",
                    action_label="查看网页",
                    failure_reason="missing_url",
                    evidence_summary=text,
                ),
                task_status={"status": "blocked_by_boundary", "reason": "missing_url"},
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "route_semantics": {
                            "route": "browser_read_page",
                            "model_called": False,
                            "task_created": False,
                            "tool_created": False,
                        },
                    }
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="browser_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {"intent": "browser_read", "reason_codes": ["browser_read_page_readonly"]},
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {"mode": TaskMode.DIRECT.value, "needs_tool": True},
        )
        from app.schemas.tasks import ToolExecuteRequest

        try:
            response = await self._tool_runtime.execute(
                ToolExecuteRequest(
                    turn_id=turn["turn_id"],
                    conversation_id=turn["conversation_id"],
                    session_id=turn.get("session_id"),
                    channel="local",
                    member_id=turn["member_id"],
                    tool_name="browser.snapshot",
                    args={
                        "url": url,
                        "intent": "readonly_page_summary",
                        "provider_mode": "auto",
                    },
                    idempotency_key=f"chat:{turn['turn_id']}:browser.snapshot",
                ),
                trace_id=trace_id,
            )
        except AppError as exc:
            text = self._browser_read_page_error_reply(exc)
            response_plan = self._response_plan_for_action_status(
                turn,
                facts=self._action_status_facts_for_turn(
                    turn,
                    status="blocked",
                    route="browser_read_page",
                    action_label="查看网页",
                    target=url,
                    failure_reason=str(exc.code),
                    evidence_summary=text,
                    tool_created=True,
                ),
                task_status={"status": "blocked_by_boundary", "reason": "browser_read_page_error"},
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "browser_read_page": {
                            "status": "blocked_by_boundary",
                            "error_code": exc.code,
                            "details": exc.details,
                        },
                    },
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="browser_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        if response.approval is not None or str(self._tool_call_value(response.tool_call, "status") or "") == "approval_required":
            text = "这一步已经到确认边界，当前还没有执行网页读取；你确认后我再继续。"
            approval_payload = (
                response.approval.model_dump(mode="json")
                if hasattr(response.approval, "model_dump")
                else dict(response.approval or {})
            )
            response_plan = self._response_plan_for_status(
                turn,
                summary=text,
                task_status={"status": "waiting_for_approval", "reason": "browser_read_page_approval_pending"},
                approval_prompt=approval_payload,
                safety_notice="当前尚未真正打开或读取网页内容。",
            )
            response_plan = self._with_single_turn_tool_payload(
                response_plan,
                route="browser_read_page",
                reason_code="browser_read_page_readonly",
                tool_name="browser.snapshot",
                response=response,
                source_type="browser_snapshot",
                trusted_level="untrusted_external_content",
                summary=text,
                trace_id=trace_id,
                status="waiting_for_approval",
                failure_summary="awaiting approval before browser execution",
                extra_payload={
                    "browser_read_page": {
                        "status": "waiting_for_approval",
                        "approval_id": self._approval_value(response.approval, "approval_id"),
                    }
                },
                task_created=False,
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="browser_read",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        result = response.result
        page_state = (
            result.get("browser_page_state")
            if isinstance(result.get("browser_page_state"), dict)
            else {}
        )
        page_status = str(page_state.get("status") or "completed")
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TOOL_COMPLETED,
            {
                "tool_call_id": response.tool_call.tool_call_id,
                "tool_name": "browser.snapshot",
                "risk_level": response.tool_call.risk_level.value
                if hasattr(response.tool_call.risk_level, "value")
                else str(response.tool_call.risk_level),
            },
        )
        text = self._browser_read_page_reply(result)
        response_plan = self._response_plan_for_action_status(
            turn,
            facts=self._action_status_facts_for_turn(
                turn,
                status="completed",
                route="browser_read_page",
                action_label="查看网页",
                target=str(redact(url)),
                detail_status=page_status,
                evidence_summary=text,
                tool_created=True,
            ),
            task_status={"status": "not_created", "reason": "readonly_browser_page_read"},
        )
        browser_payload = self._browser_read_page_payload(result)
        response_plan = self._with_single_turn_tool_payload(
            response_plan,
            route="browser_read_page",
            reason_code="browser_read_page_readonly",
            tool_name="browser.snapshot",
            response=response,
            source_type="browser_snapshot",
            trusted_level="untrusted_external_content",
            summary=text,
            trace_id=trace_id,
            evidence_refs=list(browser_payload.get("evidence_refs") or []),
            extra_payload={"browser_read_page": browser_payload},
            task_created=False,
        )
        async for event in self._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent="browser_read",
            mode=TaskMode.DIRECT.value,
            response_plan=response_plan,
        ):
            yield event

    async def handle_browser_search_readonly(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        route_decision: ChatRouteDecision,
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        query = str(route_decision.metadata.get("query") or "").strip() or str(
            route_decision.safe_user_summary or ""
        )
        require_citation = bool(route_decision.metadata.get("require_citation"))
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {"mode": TaskMode.DIRECT.value, "needs_tool": True},
        )
        result = await self._readonly_execution.browser_search(
            member_id=str(turn["member_id"]),
            turn_id=str(turn["turn_id"]),
            trace_id=trace_id,
            query=query,
            require_citation=require_citation,
        )
        response_plan = self._response_plan_for_status(
            turn,
            summary=result.visible_summary,
            task_status={"status": "not_created", "reason": route_decision.reason_code},
            safety_notice="本次是只读浏览器搜索，没有创建任务，也没有执行提交或下载动作。",
        )
        if result.tool_calls:
            tool_response = type(
                "_BrowserSearchToolResponse",
                (),
                {
                    "result": {
                        "status": result.status,
                        "evidence_refs": result.evidence_refs,
                        "execution_semantics": {
                            "mode": "single_turn_tool_loop",
                            "source_type": "browser_search",
                        },
                        "approval_state": {"status": "not_required", "approval_id": None},
                        "retryable": False,
                    },
                    "tool_call": type(
                        "_BrowserSearchToolCall",
                        (),
                        result.tool_calls[0],
                    )(),
                    "approval": None,
                    "artifacts": [],
                },
            )()
            response_plan = self._with_single_turn_tool_payload(
                response_plan,
                route=route_decision.route_type,
                reason_code=route_decision.reason_code,
                tool_name="browser.search",
                response=tool_response,
                source_type="browser_search",
                trusted_level="untrusted_external_content",
                summary=result.visible_summary,
                trace_id=trace_id,
                evidence_refs=list(result.evidence_refs or []),
                extra_payload={
                    "browser_workflow_result": result.model_dump(mode="json"),
                    "evidence_refs": result.evidence_refs,
                },
                task_created=False,
            )
            yield await self._emit_and_record(
                turn["turn_id"],
                turn["trace_id"],
                events,
                ChatEventType.TOOL_COMPLETED,
                {
                    "tool_call_id": result.tool_calls[0].get("tool_call_id"),
                    "tool_name": "browser.search",
                    "risk_level": "R2",
                    "evidence_refs": result.evidence_refs,
                },
            )
        else:
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "browser_workflow_result": result.model_dump(mode="json"),
                        "evidence_refs": result.evidence_refs,
                        "route_semantics": {
                            "route": route_decision.route_type,
                            "route_taxonomy": canonical_route_name(route_decision.route_type),
                            "model_called": False,
                            "task_created": False,
                            "tool_created": False,
                            "tool_loop": False,
                            "reason_code": route_decision.reason_code,
                        },
                    }
                }
            )
        async for event in self._complete_without_model(
            turn,
            events,
            result.visible_summary,
            root_span_id,
            intent="browser_search",
            mode=TaskMode.DIRECT.value,
            response_plan=response_plan,
        ):
            yield event

    async def handle_desktop_capability_boundary(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        route_decision: ChatRouteDecision,
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        del trace_id
        boundary = self._capability_boundary.desktop_native_boundary()
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {"mode": TaskMode.DIRECT.value, "needs_tool": False},
        )
        response_plan = self._composer.response_plan_for_tool_boundary(
            summary=boundary.message,
            required_capability="desktop",
            next_actions=["改用浏览器路径", "只生成操作方案", "检查可用工具"],
            safety_notice="当前没有执行任何桌面窗口或系统原生控制动作。",
        )
        response_plan = response_plan.model_copy(
            update={
                "structured_payload": {
                    **dict(response_plan.structured_payload or {}),
                    "capability_boundary": boundary.model_dump(mode="json"),
                    "route_semantics": {
                        "route": route_decision.route_type,
                        "route_taxonomy": canonical_route_name(route_decision.route_type),
                        "model_called": False,
                        "task_created": False,
                        "tool_created": False,
                        "reason_code": route_decision.reason_code,
                    },
                }
            }
        )
        async for event in self._complete_without_model(
            turn,
            events,
            boundary.message,
            root_span_id,
            intent="capability_boundary",
            mode=TaskMode.DIRECT.value,
            response_plan=response_plan,
        ):
            yield event

    async def handle_terminal_readonly_command(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        metadata: dict[str, Any],
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        if self._tool_runtime is None or self._task_engine is None:
            text = "当前终端工具不可用；我没有执行系统命令，也不会假装已经执行。"
            response_plan = self._response_plan_for_action_status(
                turn,
                facts=self._action_status_facts_for_turn(
                    turn,
                    status="blocked",
                    route="terminal_readonly_command",
                    action_label="执行只读命令",
                    failure_reason="terminal_unavailable",
                    evidence_summary=text,
                ),
                task_status={"status": "blocked_by_boundary", "reason": "terminal_unavailable"},
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "route_semantics": {
                            "route": "terminal_readonly_command",
                            "model_called": False,
                            "task_created": False,
                            "tool_created": False,
                        },
                    }
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="terminal_readonly_command",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        command = str(metadata.get("command") or "").strip()
        execution_command = command
        if not command:
            text = "我没拿到可执行的系统命令，所以没有运行。"
            response_plan = self._response_plan_for_action_status(
                turn,
                facts=self._action_status_facts_for_turn(
                    turn,
                    status="blocked",
                    route="terminal_readonly_command",
                    action_label="执行只读命令",
                    failure_reason="missing_command",
                    evidence_summary=text,
                ),
                task_status={"status": "blocked_by_boundary", "reason": "missing_command"},
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "route_semantics": {
                            "route": "terminal_readonly_command",
                            "model_called": False,
                            "task_created": False,
                            "tool_created": False,
                        },
                    }
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="terminal_readonly_command",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": "terminal_readonly_command",
                "reason_codes": ["terminal_readonly_command"],
            },
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.MODE_SELECTED,
            {"mode": TaskMode.WORKFLOW.value, "needs_tool": True},
        )
        from app.schemas.tasks import TaskCreateRequest, ToolExecuteRequest

        task = await self._task_engine.create_task(
            TaskCreateRequest(
                conversation_id=turn["conversation_id"],
                owner_member_id=turn["member_id"],
                goal=command,
                mode_hint=TaskMode.WORKFLOW,
                planner_context={
                    "intent": {
                        "primary_intent": "terminal_readonly_command",
                        "reason_codes": ["terminal_readonly_command"],
                    },
                    "route": "terminal_readonly_command",
                    "command": command,
                },
                auto_start=False,
                client_request_id=f"chat:{turn['turn_id']}:terminal-readonly",
            ),
            trace_id=trace_id,
        )
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TASK_CREATED,
            {"task_id": task.task_id, "title": task.title, "status": task.status.value},
        )
        if command.lower() == "pwd":
            text = f"当前工作目录是：{Path.cwd()}"
            response_plan = self._response_plan_for_action_status(
                turn,
                facts=self._action_status_facts_for_turn(
                    turn,
                    status="completed",
                    route="terminal_readonly_command",
                    action_label="执行只读命令",
                    evidence_summary=text,
                ),
                task_status={"status": "completed_with_evidence", "reason": "terminal_readonly_command"},
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "route_semantics": {
                            "route": "terminal_readonly_command",
                            "model_called": False,
                            "task_created": True,
                            "tool_created": False,
                        },
                    }
                }
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="terminal_readonly_command",
                mode=TaskMode.WORKFLOW.value,
                response_plan=response_plan,
            ):
                yield event
            return
        try:
            response = await self._tool_runtime.execute(
                ToolExecuteRequest(
                    task_id=task.task_id,
                    turn_id=turn["turn_id"],
                    conversation_id=turn["conversation_id"],
                    session_id=turn.get("session_id"),
                    channel="local",
                    member_id=turn["member_id"],
                    tool_name="terminal.run",
                    args={"command": execution_command, "chat_readonly_command": True},
                    idempotency_key=f"chat:{turn['turn_id']}:terminal.run:{execution_command}",
                ),
                trace_id=trace_id,
            )
        except AppError as exc:
            text = self._terminal_command_error_reply(command, exc)
            if self._record_failure_experience is not None:
                await self._record_failure_experience(
                    member_id=turn["member_id"],
                    failure_class="tool_execution_error",
                    summary_text=text,
                    reason_code=exc.code,
                    conversation_id=turn.get("conversation_id"),
                    turn_id=turn.get("turn_id"),
                    task_id=task.task_id,
                    trace_id=trace_id,
                    impact_scope="direct_route_terminal",
                    severity="medium",
                    evidence_refs=[
                        {"type": "task", "task_id": task.task_id},
                        {"type": "tool", "tool_name": "terminal.run"},
                    ],
                    source_payload={"command": command, "details": exc.details},
                )
            response_plan = self._response_plan_for_status(
                turn,
                summary=text,
                task_status={"status": "failed_with_reason", "reason": "terminal_readonly_command_error"},
                tool_notice="当前没有拿到这条命令的成功结果，也没有把它说成已完成。",
            )
            response_plan = self._with_single_turn_tool_error_payload(
                response_plan,
                route="terminal_readonly_command",
                reason_code="terminal_readonly_command",
                tool_name="terminal.run",
                source_type="terminal_readonly",
                trusted_level="local_runtime",
                summary=text,
                failure_summary=str(exc.message),
                status="timeout" if exc.code == ErrorCode.TOOL_TIMEOUT.value else "blocked",
                retryable=exc.code == ErrorCode.TOOL_TIMEOUT.value,
                task_created=True,
                extra_payload={
                    "terminal_route": {
                        "command": command,
                        "status": "failed_with_reason",
                        "error_code": exc.code,
                        "details": exc.details,
                    },
                },
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="terminal_readonly_command",
                mode=TaskMode.WORKFLOW.value,
                response_plan=response_plan,
            ):
                yield event
            return
        if response.approval is not None or str(self._tool_call_value(response.tool_call, "status") or "") == "approval_required":
            text = "这条系统命令已经到确认边界，当前还没有执行；你确认后我再继续。"
            approval_payload = (
                response.approval.model_dump(mode="json")
                if hasattr(response.approval, "model_dump")
                else dict(response.approval or {})
            )
            response_plan = self._response_plan_for_status(
                turn,
                summary=text,
                task_status={"status": "waiting_for_approval", "reason": "terminal_readonly_command_approval_pending", "task_id": task.task_id},
                approval_prompt=approval_payload,
                tool_notice="当前还没有真正执行系统命令。",
            )
            response_plan = self._with_single_turn_tool_payload(
                response_plan,
                route="terminal_readonly_command",
                reason_code="terminal_readonly_command",
                tool_name="terminal.run",
                response=response,
                source_type="terminal_readonly",
                trusted_level="local_runtime",
                summary=text,
                trace_id=trace_id,
                status="waiting_for_approval",
                failure_summary="awaiting approval before terminal execution",
                extra_payload={
                    "terminal_route": {
                        "command": command,
                        "status": "waiting_for_approval",
                        "task_id": task.task_id,
                        "approval_id": self._approval_value(response.approval, "approval_id"),
                    }
                },
                task_created=True,
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="terminal_readonly_command",
                mode=TaskMode.WORKFLOW.value,
                response_plan=response_plan,
            ):
                yield event
            return
        result = response.result
        yield await self._emit_and_record(
            turn["turn_id"],
            turn["trace_id"],
            events,
            ChatEventType.TOOL_COMPLETED,
            {
                "tool_call_id": response.tool_call.tool_call_id,
                "tool_name": "terminal.run",
                "risk_level": response.tool_call.risk_level.value
                if hasattr(response.tool_call.risk_level, "value")
                else str(response.tool_call.risk_level),
            },
        )
        text = self._terminal_command_reply(command, result)
        response_plan = self._response_plan_for_action_status(
            turn,
            facts=self._action_status_facts_for_turn(
                turn,
                status="completed",
                route="terminal_readonly_command",
                action_label="执行只读命令",
                target=command,
                detail_status="completed",
                evidence_summary=text,
                task_created=True,
                tool_created=True,
            ),
            task_status={"status": "completed_with_evidence", "reason": "terminal_readonly_command"},
        )
        response_plan = self._with_single_turn_tool_payload(
            response_plan,
            route="terminal_readonly_command",
            reason_code="terminal_readonly_command",
            tool_name="terminal.run",
            response=response,
            source_type="terminal_readonly",
            trusted_level="local_runtime",
            summary=text,
            trace_id=trace_id,
            extra_payload={
                "terminal_route": {
                    "command": command,
                    "status": "completed_with_evidence",
                    "tool_call_id": response.tool_call.tool_call_id,
                    "task_id": task.task_id,
                    "output_preview": str(result.get("output_preview") or "")[:1000],
                    "sandbox_profile": result.get("sandbox_profile"),
                    "backend_status": result.get("backend_status"),
                    "fallback_chain": result.get("fallback_chain"),
                    "degraded_reason": result.get("degraded_reason"),
                    "resource_usage": result.get("resource_usage"),
                    "cleanup": result.get("cleanup"),
                    "dlp_report_id": result.get("dlp_report_id"),
                }
            },
            task_created=True,
        )
        async for event in self._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent="terminal_readonly_command",
            mode=TaskMode.WORKFLOW.value,
            response_plan=response_plan,
        ):
            yield event

    async def handle_office_chat_request(
        self,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        user_text: str,
        office_request: OfficeChatRequest,
        root_span_id: str | None,
        *,
        trace_id: str | None,
    ) -> AsyncIterator[ChatEvent]:
        turn_id = turn["turn_id"]
        if self._task_engine is None:
            text = "我识别到这是 Office 文件任务，但当前任务引擎不可用；没有生成文件。"
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability="office_document",
                next_actions=["检查任务引擎", "稍后重试"],
                safety_notice="没有执行任何文件写入动作。",
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="office_document_request",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return
        skill_id = await self._enabled_office_skill_id(office_request)
        tool_name = preferred_office_tool_name(office_request)
        missing_reason = None
        if skill_id is None:
            missing_reason = "missing_enabled_skill"
        elif not await self._office_skill_has_grant(skill_id, tool_name, str(turn["member_id"])):
            missing_reason = "missing_skill_grant"
        if missing_reason is not None:
            text = self._office_missing_capability_text(office_request, missing_reason)
            response_plan = self._composer.response_plan_for_tool_boundary(
                summary=text,
                required_capability=f"office.{office_request.document_type}.{office_request.operation}",
                next_actions=self._office_next_actions(office_request, missing_reason),
                safety_notice="Office 文件这步还没真正走完，我先不把结果说满。",
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **dict(response_plan.structured_payload or {}),
                        "office_route": {
                            "document_type": office_request.document_type,
                            "operation": office_request.operation,
                            "missing_reason": missing_reason,
                            "tool_name": tool_name,
                        },
                    },
                }
            )
            yield await self._emit_and_record(
                turn_id,
                turn["trace_id"],
                events,
                ChatEventType.INTENT_DETECTED,
                {
                    "intent": "office_document_request",
                    "reason_codes": ["office_document_hard_route", missing_reason],
                },
            )
            async for event in self._complete_without_model(
                turn,
                events,
                text,
                root_span_id,
                intent="office_document_request",
                mode=TaskMode.DIRECT.value,
                response_plan=response_plan,
            ):
                yield event
            return

        from app.schemas.tasks import TaskCreateRequest

        source_artifact_id = await self._latest_office_artifact_id(
            str(turn["conversation_id"]),
            office_request.document_type,
        )
        task = await self._task_engine.create_task(
            TaskCreateRequest(
                conversation_id=turn["conversation_id"],
                owner_member_id=turn["member_id"],
                goal=user_text,
                domain="productivity",
                domain_request=office_request_from_chat_request(
                    office_request,
                    goal=user_text,
                ).model_dump(mode="json"),
                mode_hint=TaskMode.AGENT,
                office_request=office_request_from_chat_request(office_request, goal=user_text),
                constraints={
                    "skill_id": skill_id,
                    "skill_input": office_skill_input(
                        office_request,
                        source_artifact_id=source_artifact_id,
                    ),
                    "office_request": office_request_from_chat_request(
                        office_request,
                        goal=user_text,
                    ).model_dump(mode="json"),
                    "office_chat_request": office_request.__dict__,
                },
                planner_context={
                    "intent": {
                        "primary_intent": "office_document_request",
                        "reason_codes": ["office_document_hard_route"],
                    },
                    "route": "office_document_hard_route",
                },
                auto_start=True,
                client_request_id=f"chat:{turn_id}:office-task",
            ),
            trace_id=trace_id,
        )
        yield await self._emit_and_record(
            turn_id,
            turn["trace_id"],
            events,
            ChatEventType.INTENT_DETECTED,
            {
                "intent": "office_document_request",
                "reason_codes": ["office_document_hard_route", "office_skill_auto_execute"],
            },
        )
        yield await self._emit_and_record(
            turn_id,
            turn["trace_id"],
            events,
            ChatEventType.TASK_CREATED,
            {"task_id": task.task_id, "title": task.title, "status": task.status.value},
        )
        artifacts = await self._task_engine.artifacts(task.task_id)
        recovery = await self._recover_task_in_turn(turn, events, task, root_span_id)
        task = recovery.task
        artifacts = await self._task_engine.artifacts(task.task_id)
        office_artifact_refs = self._office_artifact_refs(artifacts, office_request.document_type)
        office_reply = self._office_task_reply(office_request, task, artifacts)
        text = f"{recovery.response_prefix}{office_reply}"
        presentation = self._task_coordinator.present_task_status(task)
        response_plan = self._response_plan_for_status(
            turn,
            summary=text,
            task_status={
                **presentation.task_status,
                "artifact_count": len(artifacts),
                "office_document_type": office_request.document_type,
                "office_operation": office_request.operation,
            },
            safety_notice=presentation.safety_notice,
            tool_notice=presentation.tool_notice,
        )
        if recovery.recovery_payload.get("attempt_count"):
            if self._turn_recovery is not None:
                response_plan = self._turn_recovery.response_plan_for_task(
                    summary=text,
                    task_status={
                        **presentation.task_status,
                        "artifact_count": len(artifacts),
                        "office_document_type": office_request.document_type,
                        "office_operation": office_request.operation,
                    },
                    recovery_payload=recovery.recovery_payload,
                    safety_notice=presentation.safety_notice,
                    tool_notice=presentation.tool_notice,
                )
        response_plan = response_plan.model_copy(
            update={
                "artifact_refs": office_artifact_refs,
                "structured_payload": {
                    **dict(response_plan.structured_payload or {}),
                    "office_route": {
                        "document_type": office_request.document_type,
                        "operation": office_request.operation,
                        "artifact_count": len(artifacts),
                        "artifacts": office_artifact_refs,
                        "status": task.status.value,
                    },
                    "recovery": recovery.recovery_payload,
                },
            }
        )
        if recovery.recovery_payload.get("status") == "exhausted":
            async for event in self._fail_turn(
                turn,
                events,
                ErrorCode.TASK_STEP_FAILED,
                text,
                root_span_id,
                persist_assistant=True,
                response_plan=response_plan,
            ):
                yield event
            return
        async for event in self._complete_without_model(
            turn,
            events,
            text,
            root_span_id,
            intent="office_document_request",
            mode=TaskMode.WORKFLOW.value,
            response_plan=response_plan,
        ):
            yield event
