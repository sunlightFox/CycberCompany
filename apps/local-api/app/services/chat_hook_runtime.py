from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from core_types import ErrorCode, RiskLevel, TraceSpanStatus, TraceSpanType
from trace_service import redact

from app.core.errors import AppError
from app.services.audit import AuditEventService
from app.services.chat_visible_guard import visible_text_guard

ChatHookStage = Literal[
    "before_ingress",
    "after_context_build",
    "before_route_select",
    "before_model_call",
    "before_tool_call",
    "after_tool_call",
    "before_finalize",
    "before_memory_write",
]
HookHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None] | dict[str, Any] | None]

_ALL_STAGES: tuple[ChatHookStage, ...] = (
    "before_ingress",
    "after_context_build",
    "before_route_select",
    "before_model_call",
    "before_tool_call",
    "after_tool_call",
    "before_finalize",
    "before_memory_write",
)
_BLOCKING_STAGES: set[str] = {"before_tool_call", "before_finalize", "before_memory_write"}
_ADVISORY_ONLY_STAGES: set[str] = {
    "before_ingress",
    "after_context_build",
    "before_route_select",
    "before_model_call",
}
_ALLOWED_REWRITE_KEYS: dict[str, set[str]] = {
    "before_ingress": {
        "raw_payload",
        "queue_policy",
        "dedupe_key",
        "channel_account_id",
        "channel_peer_id_redacted",
        "channel_thread_id",
        "delivery_mode",
        "source_timestamp",
        "trusted_level",
        "audit_metadata",
    },
    "before_tool_call": {"args", "idempotency_key"},
    "after_tool_call": {
        "result",
        "artifacts",
        "trusted_level",
        "trace_ref",
        "evidence_summary",
    },
    "before_finalize": {
        "plain_text",
        "summary",
        "response_plan",
        "channel_render_hints",
    },
    "before_memory_write": {"source", "archive_hint", "supersede_hint", "annotations"},
}


@dataclass(frozen=True)
class RegisteredChatHook:
    name: str
    stage: ChatHookStage
    handler: HookHandler
    builtin: bool = False


class ChatHookRuntime:
    def __init__(
        self,
        *,
        trace_service: Any,
        audit_service: AuditEventService,
        chat_run_ledger_service: Any | None = None,
    ) -> None:
        self._trace = trace_service
        self._audit = audit_service
        self._chat_run_ledger_service = chat_run_ledger_service
        self._hooks: dict[str, list[RegisteredChatHook]] = {stage: [] for stage in _ALL_STAGES}
        self._last_results: dict[str, Any] = {}
        self.register_hook(
            stage="before_tool_call",
            name="builtin.before_tool_call.guard",
            handler=self._builtin_before_tool_call,
            builtin=True,
        )
        self.register_hook(
            stage="after_tool_call",
            name="builtin.after_tool_call.sanitize",
            handler=self._builtin_after_tool_call,
            builtin=True,
        )
        self.register_hook(
            stage="before_finalize",
            name="builtin.before_finalize.visible_guard",
            handler=self._builtin_before_finalize,
            builtin=True,
        )
        self.register_hook(
            stage="before_memory_write",
            name="builtin.before_memory_write.source_guard",
            handler=self._builtin_before_memory_write,
            builtin=True,
        )

    def register_hook(
        self,
        *,
        stage: ChatHookStage,
        name: str,
        handler: HookHandler,
        builtin: bool = False,
    ) -> None:
        self._hooks[stage].append(
            RegisteredChatHook(name=name, stage=stage, handler=handler, builtin=builtin)
        )

    async def run_before_ingress(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        return await self._run_stage("before_ingress", hook_input)

    async def run_after_context_build(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        return await self._run_stage("after_context_build", hook_input)

    async def run_before_route_select(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        return await self._run_stage("before_route_select", hook_input)

    async def run_before_model_call(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        return await self._run_stage("before_model_call", hook_input)

    async def run_before_tool_call(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        return await self._run_stage("before_tool_call", hook_input)

    async def run_after_tool_call(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        return await self._run_stage("after_tool_call", hook_input)

    async def run_before_finalize(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        return await self._run_stage("before_finalize", hook_input)

    async def run_before_memory_write(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        return await self._run_stage("before_memory_write", hook_input)

    def runtime_diagnostic(self) -> dict[str, Any]:
        return {
            "runtime": "chat_hook_runtime",
            "supported_stages": list(_ALL_STAGES),
            "fail_closed_default": True,
            "registered_hooks": {
                stage: [hook.name for hook in hooks]
                for stage, hooks in self._hooks.items()
            },
            "blocked_stages": sorted(_BLOCKING_STAGES),
            "last_results": redact(self._last_results),
        }

    async def _run_stage(self, stage: ChatHookStage, hook_input: dict[str, Any]) -> dict[str, Any]:
        normalized_input = self._normalize_input(stage, hook_input)
        aggregated = self._base_result()
        for hook in self._hooks.get(stage, []):
            started = time.perf_counter()
            trace_id = normalized_input.get("trace_id")
            span_id = None
            if trace_id:
                span_id = await self._trace.start_span(
                    trace_id,
                    span_type=TraceSpanType.SAFETY_EVALUATE,
                    name=f"chat hook {stage}:{hook.name}",
                    input_data={
                        "hook_stage": stage,
                        "payload": redact(normalized_input.get("payload") or {}),
                    },
                    metadata={
                        "hook_name": hook.name,
                        "hook_stage": stage,
                    },
                )
            fail_closed_applied = False
            try:
                raw_output = hook.handler(normalized_input)
                if hasattr(raw_output, "__await__"):
                    raw_output = await raw_output  # type: ignore[assignment]
                hook_result = self._normalize_output(stage, raw_output or {})
            except Exception as exc:
                fail_closed_applied = True
                hook_result = {
                    **self._base_result(),
                    "status": "failed",
                    "reason_code": "hook_execution_failed",
                    "blocked": stage in _BLOCKING_STAGES,
                    "audit_annotations": {
                        "exception_type": exc.__class__.__name__,
                        "message": str(redact(str(exc))),
                    },
                }
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            hook_result["trace_annotations"] = {
                **dict(hook_result.get("trace_annotations") or {}),
                "hook_name": hook.name,
                "hook_stage": stage,
                "duration_ms": duration_ms,
                "fail_closed_applied": fail_closed_applied,
            }
            if span_id is not None:
                await self._trace.end_span(
                    span_id,
                    status=(
                        TraceSpanStatus.FAILED
                        if hook_result["status"] in {"blocked", "failed"}
                        else TraceSpanStatus.COMPLETED
                    ),
                    output_data=redact(
                        {
                            "hook_name": hook.name,
                            "status": hook_result["status"],
                            "reason_code": hook_result["reason_code"],
                            "blocked": hook_result["blocked"],
                        }
                    ),
                    error_code=(
                        ErrorCode.TOOL_PERMISSION_DENIED.value if hook_result["blocked"] else None
                    ),
                )
            await self._audit.write_event(
                actor_type="system",
                action=f"chat.hook.{stage}",
                object_type="chat_hook",
                object_id=hook.name,
                summary=f"chat hook {stage} executed",
                risk_level=RiskLevel.R1 if not hook_result["blocked"] else RiskLevel.R3,
                payload={
                    "hook_name": hook.name,
                    "hook_stage": stage,
                    "status": hook_result["status"],
                    "reason_code": hook_result["reason_code"],
                    "blocked": hook_result["blocked"],
                    "fail_closed_applied": fail_closed_applied,
                    "audit_annotations": hook_result.get("audit_annotations") or {},
                },
                trace_id=trace_id,
            )
            if self._chat_run_ledger_service is not None:
                await self._chat_run_ledger_service.record_hook_execution(
                    turn_id=normalized_input.get("turn_id"),
                    trace_id=trace_id,
                    hook_stage=stage,
                    hook_name=hook.name,
                    status=str(hook_result["status"]),
                    reason_code=str(hook_result.get("reason_code") or ""),
                    blocked=bool(hook_result["blocked"]),
                    payload={
                        "trace_annotations": hook_result.get("trace_annotations") or {},
                        "audit_annotations": hook_result.get("audit_annotations") or {},
                    },
                )
            aggregated = self._merge_results(aggregated, hook_result)
            if aggregated["blocked"]:
                break
        self._last_results[stage] = {
            "status": aggregated["status"],
            "reason_code": aggregated["reason_code"],
            "blocked": aggregated["blocked"],
        }
        return aggregated

    def _normalize_input(self, stage: ChatHookStage, hook_input: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(hook_input or {})
        normalized.setdefault("trace_id", None)
        normalized.setdefault("conversation_id", None)
        normalized.setdefault("turn_id", None)
        normalized.setdefault("member_id", None)
        normalized.setdefault("session_id", None)
        normalized.setdefault("channel", None)
        normalized["hook_stage"] = stage
        normalized["payload"] = dict(normalized.get("payload") or {})
        return normalized

    def _normalize_output(self, stage: ChatHookStage, raw_output: dict[str, Any]) -> dict[str, Any]:
        result = {
            **self._base_result(),
            **dict(raw_output or {}),
        }
        status = str(result.get("status") or "pass")
        if status not in {"pass", "advisory", "rewritten", "blocked", "failed"}:
            raise AppError(ErrorCode.VALIDATION_ERROR, "hook status 非法", status_code=422)
        result["status"] = status
        result["reason_code"] = str(result.get("reason_code") or "")
        result["blocked"] = bool(result.get("blocked") or status == "blocked")
        result["advisory_payload"] = dict(result.get("advisory_payload") or {})
        result["rewritten_payload"] = dict(result.get("rewritten_payload") or {})
        result["trace_annotations"] = dict(result.get("trace_annotations") or {})
        result["audit_annotations"] = dict(result.get("audit_annotations") or {})
        if result["rewritten_payload"]:
            disallowed = set(result["rewritten_payload"]) - _ALLOWED_REWRITE_KEYS.get(stage, set())
            if disallowed:
                raise AppError(
                    ErrorCode.VALIDATION_ERROR,
                    f"hook rewrite 字段越权: {sorted(disallowed)}",
                    status_code=422,
                )
        if stage in _ADVISORY_ONLY_STAGES and result["blocked"]:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                f"{stage} 只能 advisory，不能阻断主链",
                status_code=422,
            )
        return result

    def _merge_results(self, base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        merged = {
            **base,
            "advisory_payload": {
                **dict(base.get("advisory_payload") or {}),
                **dict(extra.get("advisory_payload") or {}),
            },
            "rewritten_payload": {
                **dict(base.get("rewritten_payload") or {}),
                **dict(extra.get("rewritten_payload") or {}),
            },
            "trace_annotations": {
                **dict(base.get("trace_annotations") or {}),
                **dict(extra.get("trace_annotations") or {}),
            },
            "audit_annotations": {
                **dict(base.get("audit_annotations") or {}),
                **dict(extra.get("audit_annotations") or {}),
            },
        }
        if extra.get("blocked"):
            merged["status"] = "blocked"
            merged["blocked"] = True
            merged["reason_code"] = extra.get("reason_code") or merged.get("reason_code")
        elif extra.get("status") == "rewritten":
            merged["status"] = "rewritten"
            merged["reason_code"] = extra.get("reason_code") or merged.get("reason_code")
        elif extra.get("status") == "advisory" and merged["status"] == "pass":
            merged["status"] = "advisory"
            merged["reason_code"] = extra.get("reason_code") or merged.get("reason_code")
        elif extra.get("status") == "failed" and merged["status"] == "pass":
            merged["status"] = "failed"
            merged["reason_code"] = extra.get("reason_code") or merged.get("reason_code")
        return merged

    @staticmethod
    def _base_result() -> dict[str, Any]:
        return {
            "status": "pass",
            "reason_code": "",
            "advisory_payload": {},
            "blocked": False,
            "rewritten_payload": {},
            "trace_annotations": {},
            "audit_annotations": {},
        }

    async def _builtin_before_tool_call(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        payload = dict(hook_input.get("payload") or {})
        tool_name = str(payload.get("tool_name") or "")
        args = dict(payload.get("args") or {})
        task_id = payload.get("task_id")
        approval_id = payload.get("approval_id")
        risk_level = str(args.get("risk_level") or payload.get("risk_level") or "").upper()
        high_risk = (
            risk_level in {"R4", "R5"}
            or bool(args.get("write"))
            or bool(args.get("mutating"))
            or any(
                marker in tool_name.lower()
                for marker in ("delete", "write", "mutate", "install", "download", "send")
            )
        )
        if high_risk and not task_id and not approval_id:
            return {
                "status": "blocked",
                "reason_code": "hook_block_high_risk_tool_without_task_or_approval",
                "blocked": True,
                "audit_annotations": {"tool_name": tool_name},
            }
        return {"status": "pass"}

    async def _builtin_after_tool_call(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        payload = dict(hook_input.get("payload") or {})
        result = _sanitize_tool_result(payload.get("result") or {})
        if result != payload.get("result"):
            return {
                "status": "rewritten",
                "reason_code": "tool_result_sanitized",
                "rewritten_payload": {"result": result},
            }
        return {"status": "pass"}

    async def _builtin_before_finalize(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        payload = dict(hook_input.get("payload") or {})
        plain_text = visible_text_guard(str(payload.get("plain_text") or ""))
        summary = visible_text_guard(str(payload.get("summary") or plain_text))
        if plain_text != payload.get("plain_text") or summary != payload.get("summary"):
            rewritten_plan = dict(payload.get("response_plan") or {})
            rewritten_plan["plain_text"] = plain_text
            rewritten_plan["summary"] = summary
            return {
                "status": "rewritten",
                "reason_code": "visible_text_guard_applied",
                "rewritten_payload": {
                    "plain_text": plain_text,
                    "summary": summary,
                    "response_plan": rewritten_plan,
                },
            }
        return {"status": "pass"}

    async def _builtin_before_memory_write(self, hook_input: dict[str, Any]) -> dict[str, Any]:
        payload = dict(hook_input.get("payload") or {})
        source = dict(payload.get("source") or {})
        missing = [
            key
            for key in ("type", "conversation_id", "captured_at")
            if not source.get(key)
        ]
        if missing:
            return {
                "status": "blocked",
                "reason_code": "memory_source_minimum_fields_missing",
                "blocked": True,
                "audit_annotations": {"missing_fields": missing},
            }
        return {"status": "pass"}


def _sanitize_tool_result(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in {"stdout", "stderr", "content", "html", "raw_output"}:
                sanitized[key] = _sanitize_large_text(str(item))
            else:
                sanitized[key] = _sanitize_tool_result(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_tool_result(item) for item in value]
    if isinstance(value, str):
        return _sanitize_large_text(value)
    return redact(value)


def _sanitize_large_text(text: str) -> str:
    visible = visible_text_guard(str(text))
    if len(visible) > 1200:
        return f"{visible[:1200].rstrip()}...[truncated]"
    return visible
