from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from core_types import (
    ErrorCode,
    MCPContentSanitizationReport,
    MCPLifecycleEvent,
    MCPOutputTaintRecord,
    MCPProtocolValidationReport,
    MCPRuntimeProfile,
    RiskLevel,
)
from trace_service import redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.skill_mcp_repo import SkillMcpRepository

MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_FAILURE_CIRCUIT_THRESHOLD = 2
MCP_DEFAULT_TIMEOUT_SECONDS = 15
MCP_MAX_RESOURCE_PREVIEW_BYTES = 4096

_INJECTION_RE = re.compile(
    r"(?i)(ignore (all )?(previous|system|developer)|system prompt|developer message|"
    r"bypass (safety|approval|policy)|直接执行|忽略.*安全|覆盖.*系统)"
)
_HIGH_RISK_ACTION_RE = re.compile(
    r"(?i)(terminal\.run|curl\s+-x\s+post|invoke-webrequest|delete|transfer|payment|"
    r"wallet|private[_-]?key|api[_-]?key|token|cookie|mnemonic|删除|转账|支付|私钥)"
)


class MCPRuntimeProfileService:
    def __init__(self, repo: SkillMcpRepository) -> None:
        self._repo = repo

    async def create_profile(
        self,
        *,
        server_id: str,
        organization_id: str,
        display_name: str,
        transport: str,
        command: str | None,
        args: list[str],
        env_refs: list[str],
        permission: dict[str, Any],
        trust_level: str,
        command_allowed: bool,
        env_refs_only: bool,
        no_inline_secret: bool,
        trace_id: str | None,
    ) -> MCPRuntimeProfile:
        now = utc_now_iso()
        reason_codes = ["mcp_runtime_profile_created"]
        if transport != "stdio":
            reason_codes.append("mcp_transport_not_supported")
        if not command:
            reason_codes.append("mcp_stdio_command_required")
        if command and not command_allowed:
            reason_codes.append("mcp_command_not_allowlisted")
        if not env_refs_only:
            reason_codes.append("mcp_env_refs_must_not_inline_env")
        if not no_inline_secret:
            reason_codes.append("mcp_inline_secret_denied")
        status = "active" if len(reason_codes) == 1 else "denied"
        profile = MCPRuntimeProfile(
            profile_id=new_id("mcprp"),
            organization_id=organization_id,
            server_id=server_id,
            transport=transport,
            command_policy={
                "display_name": display_name,
                "command_present": bool(command),
                "command_allowed": command_allowed,
                "command_hash": _hash_text(command or ""),
            },
            args_policy={
                "args_schema": "list_of_strings",
                "arg_count": len(args),
                "inline_secret": "deny",
                "args_hash": _hash_json(args),
            },
            env_policy={
                "mode": "env_refs_only",
                "env_ref_count": len(env_refs),
                "env_refs_only": env_refs_only,
                "no_inline_secret": no_inline_secret,
            },
            member_scope_policy=redact(permission),
            network_policy="local_stdio_only",
            filesystem_policy={"server_filesystem": "no_direct_access_from_core"},
            sandbox_backend="stdio_policy_guard",
            timeout_policy={
                "request_timeout_seconds": MCP_DEFAULT_TIMEOUT_SECONDS,
                "startup_timeout_seconds": MCP_DEFAULT_TIMEOUT_SECONDS,
                "circuit_breaker_threshold": MCP_FAILURE_CIRCUIT_THRESHOLD,
            },
            resource_trust_policy="always_untrusted_external_content",
            prompt_trust_policy="template_only_never_system_instruction",
            status=status,
            reason_codes=reason_codes,
            trace_id=trace_id,
            created_at=now,
            updated_at=now,
        )
        await self._repo.upsert_mcp_runtime_profile(profile.model_dump(mode="json"))
        return profile

    async def get_profile(self, server_id: str) -> MCPRuntimeProfile | None:
        row = await self._repo.latest_mcp_runtime_profile(server_id)
        return MCPRuntimeProfile(**row) if row else None


class MCPLifecycleManager:
    def __init__(self, repo: SkillMcpRepository) -> None:
        self._repo = repo

    async def start_requested(
        self,
        server: dict[str, Any],
        *,
        operation: str,
        trace_id: str | None,
    ) -> None:
        if server.get("circuit_state") == "open" or int(
            server.get("consecutive_failure_count") or 0
        ) >= MCP_FAILURE_CIRCUIT_THRESHOLD:
            await self._record_event(
                server,
                event_type="server.circuit_opened",
                current_status="circuit_open",
                circuit_state="open",
                payload={"operation": operation, "reason": "consecutive_failures"},
                trace_id=trace_id,
            )
            await self._repo.update_mcp_server(
                server["server_id"],
                {
                    "status": "degraded",
                    "lifecycle_status": "circuit_open",
                    "circuit_state": "open",
                    "updated_at": utc_now_iso(),
                },
            )
            raise AppError(
                ErrorCode.MCP_CONNECT_FAILED,
                "MCP server circuit breaker is open",
                status_code=409,
                details={"server_id": server["server_id"], "operation": operation},
            )
        await self._repo.update_mcp_server(
            server["server_id"],
            {
                "lifecycle_status": "starting",
                "circuit_state": "closed",
                "updated_at": utc_now_iso(),
            },
        )
        await self._record_event(
            server,
            event_type="server.start_requested",
            current_status="starting",
            circuit_state="closed",
            payload={"operation": operation},
            trace_id=trace_id,
        )

    async def started(self, server: dict[str, Any], *, trace_id: str | None) -> None:
        await self._record_event(
            server,
            event_type="server.started",
            current_status="started",
            circuit_state="closed",
            payload={},
            trace_id=trace_id,
        )

    async def ready(self, server: dict[str, Any], *, operation: str, trace_id: str | None) -> None:
        now = utc_now_iso()
        await self._repo.update_mcp_server(
            server["server_id"],
            {
                "lifecycle_status": "ready",
                "circuit_state": "closed",
                "consecutive_failure_count": 0,
                "last_health_check_at": now,
                "updated_at": now,
            },
        )
        await self._record_event(
            server,
            event_type="server.health_checked",
            current_status="ready",
            circuit_state="closed",
            payload={"operation": operation, "healthy": True},
            trace_id=trace_id,
        )

    async def failed(
        self,
        server: dict[str, Any],
        *,
        operation: str,
        error: Exception,
        trace_id: str | None,
    ) -> None:
        failures = int(server.get("consecutive_failure_count") or 0) + 1
        circuit_state = "open" if failures >= MCP_FAILURE_CIRCUIT_THRESHOLD else "closed"
        lifecycle_status = "circuit_open" if circuit_state == "open" else "failed"
        now = utc_now_iso()
        await self._repo.update_mcp_server(
            server["server_id"],
            {
                "lifecycle_status": lifecycle_status,
                "circuit_state": circuit_state,
                "consecutive_failure_count": failures,
                "last_health_check_at": now,
                "last_error_code": getattr(error, "code", ErrorCode.MCP_CONNECT_FAILED.value),
                "last_error_summary": str(redact(str(error))),
                "updated_at": now,
            },
        )
        await self._record_event(
            server,
            event_type="server.failed",
            current_status=lifecycle_status,
            circuit_state=circuit_state,
            payload={
                "operation": operation,
                "error": str(redact(str(error))),
                "consecutive_failure_count": failures,
            },
            trace_id=trace_id,
        )
        if circuit_state == "open":
            await self._record_event(
                {**server, "lifecycle_status": lifecycle_status},
                event_type="server.circuit_opened",
                current_status="circuit_open",
                circuit_state="open",
                payload={"operation": operation, "consecutive_failure_count": failures},
                trace_id=trace_id,
            )

    async def stopped(self, server: dict[str, Any], *, trace_id: str | None) -> None:
        await self._repo.update_mcp_server(
            server["server_id"],
            {"lifecycle_status": "stopped", "updated_at": utc_now_iso()},
        )
        await self._record_event(
            server,
            event_type="server.stopped",
            current_status="stopped",
            circuit_state=str(server.get("circuit_state") or "closed"),
            payload={"cleanup": "transport_closed"},
            trace_id=trace_id,
        )

    async def list_events(self, server_id: str) -> list[MCPLifecycleEvent]:
        return [
            MCPLifecycleEvent(**row)
            for row in await self._repo.list_mcp_lifecycle_events(server_id)
        ]

    async def _record_event(
        self,
        server: dict[str, Any],
        *,
        event_type: str,
        current_status: str,
        circuit_state: str,
        payload: dict[str, Any],
        trace_id: str | None,
    ) -> None:
        await self._repo.insert_mcp_lifecycle_event(
            {
                "lifecycle_event_id": new_id("mcple"),
                "organization_id": str(server.get("organization_id") or "org_default"),
                "server_id": server["server_id"],
                "profile_id": server.get("runtime_profile_id"),
                "event_type": event_type,
                "previous_status": server.get("lifecycle_status"),
                "current_status": current_status,
                "circuit_state": circuit_state,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )


class MCPProtocolValidator:
    def __init__(self, repo: SkillMcpRepository) -> None:
        self._repo = repo

    async def validate_initialize(
        self,
        *,
        server_id: str,
        organization_id: str,
        result: dict[str, Any],
        trace_id: str | None,
    ) -> MCPProtocolValidationReport:
        protocol_version = result.get("protocolVersion") or result.get("protocol_version")
        issue_codes: list[str] = []
        if not protocol_version:
            issue_codes.append("protocol_version_missing")
        elif str(protocol_version) != MCP_PROTOCOL_VERSION:
            issue_codes.append("protocol_version_incompatible")
        if not isinstance(result.get("serverInfo"), dict):
            issue_codes.append("server_info_missing")
        if not isinstance(result.get("capabilities"), dict):
            issue_codes.append("capabilities_missing")
        fatal_issue_codes = [
            code for code in issue_codes if code != "server_info_missing"
        ]
        report = await self._insert_report(
            organization_id=organization_id,
            server_id=server_id,
            operation="initialize",
            protocol_version=str(protocol_version) if protocol_version else None,
            schema_valid=not issue_codes,
            capability_valid="capabilities_missing" not in issue_codes,
            issue_codes=issue_codes,
            sanitized_payload=_safe_protocol_payload(result),
            trace_id=trace_id,
        )
        if fatal_issue_codes:
            raise AppError(
                ErrorCode.MCP_CONNECT_FAILED,
                "MCP initialize response failed validation",
                status_code=502,
                details={
                    "issue_codes": fatal_issue_codes,
                    "validation_report_id": report.validation_report_id,
                },
            )
        return report

    async def validate_list_response(
        self,
        *,
        server_id: str,
        organization_id: str,
        operation: str,
        items: list[dict[str, Any]],
        trace_id: str | None,
    ) -> MCPProtocolValidationReport:
        issue_codes: list[str] = []
        seen: set[str] = set()
        key = "uri" if operation == "resources/list" else "name"
        for item in items:
            item_id = str(item.get(key) or "")
            if not item_id:
                issue_codes.append(f"{operation.replace('/', '_')}_missing_{key}")
                continue
            if item_id in seen:
                issue_codes.append(f"{operation.replace('/', '_')}_duplicate_{key}")
            seen.add(item_id)
            if operation == "tools/list" and not _valid_tool_schema(item):
                issue_codes.append("tool_schema_invalid")
            if operation == "prompts/list" and not _valid_prompt_schema(item):
                issue_codes.append("prompt_schema_invalid")
        return await self._insert_report(
            organization_id=organization_id,
            server_id=server_id,
            operation=operation,
            protocol_version=MCP_PROTOCOL_VERSION,
            schema_valid=not issue_codes,
            capability_valid=True,
            issue_codes=sorted(set(issue_codes)),
            sanitized_payload={"item_count": len(items), "item_ids": sorted(seen)[:20]},
            trace_id=trace_id,
        )

    async def validate_tool_call_result(
        self,
        *,
        server_id: str,
        organization_id: str,
        mcp_call_id: str,
        result: dict[str, Any],
        trace_id: str | None,
    ) -> MCPProtocolValidationReport:
        issue_codes: list[str] = []
        if not isinstance(result, dict):
            issue_codes.append("tool_call_result_not_object")
        if "content" in result and not isinstance(result["content"], list):
            issue_codes.append("tool_call_content_not_list")
        report = await self._insert_report(
            organization_id=organization_id,
            server_id=server_id,
            mcp_call_id=mcp_call_id,
            operation="tools/call",
            protocol_version=MCP_PROTOCOL_VERSION,
            schema_valid=not issue_codes,
            capability_valid=True,
            issue_codes=issue_codes,
            sanitized_payload=_safe_protocol_payload(result),
            trace_id=trace_id,
        )
        if issue_codes:
            raise AppError(
                ErrorCode.MCP_TOOL_CALL_FAILED,
                "MCP tools/call response failed validation",
                status_code=502,
                details={
                    "issue_codes": issue_codes,
                    "validation_report_id": report.validation_report_id,
                },
            )
        return report

    async def list_reports(self, server_id: str) -> list[MCPProtocolValidationReport]:
        return [
            MCPProtocolValidationReport(**row)
            for row in await self._repo.list_mcp_protocol_validation_reports(server_id)
        ]

    async def _insert_report(
        self,
        *,
        organization_id: str,
        server_id: str,
        operation: str,
        protocol_version: str | None,
        schema_valid: bool,
        capability_valid: bool,
        issue_codes: list[str],
        sanitized_payload: dict[str, Any],
        trace_id: str | None,
        mcp_call_id: str | None = None,
    ) -> MCPProtocolValidationReport:
        report = MCPProtocolValidationReport(
            validation_report_id=new_id("mcpvr"),
            organization_id=organization_id,
            server_id=server_id,
            mcp_call_id=mcp_call_id,
            operation=operation,
            protocol_version=protocol_version,
            schema_valid=schema_valid,
            capability_valid=capability_valid,
            validation_status="passed" if schema_valid and capability_valid else "failed",
            issue_codes=issue_codes,
            sanitized_payload=redact(sanitized_payload),
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        await self._repo.insert_mcp_protocol_validation_report(report.model_dump(mode="json"))
        return report


class MCPContentSanitizer:
    def __init__(self, repo: SkillMcpRepository) -> None:
        self._repo = repo

    async def sanitize(
        self,
        *,
        organization_id: str,
        server_id: str,
        source_type: str,
        source_id: str | None,
        value: Any,
        mime_type: str | None = None,
        dlp_report_id: str | None = None,
        trace_id: str | None = None,
    ) -> MCPContentSanitizationReport:
        text = _stringify(value)
        encoded = text.encode("utf-8")
        preview = str(redact(text[:MCP_MAX_RESOURCE_PREVIEW_BYTES]))
        report = MCPContentSanitizationReport(
            sanitization_report_id=new_id("mcpsan"),
            organization_id=organization_id,
            server_id=server_id,
            source_type=source_type,
            source_id=source_id,
            trust_level=(
                "mcp_prompt_template"
                if source_type == "prompt"
                else "untrusted_external_content"
            ),
            content_hash="sha256:" + hashlib.sha256(encoded).hexdigest(),
            size_bytes=len(encoded),
            mime_type=mime_type,
            injection_detected=bool(_INJECTION_RE.search(text)),
            dlp_report_id=dlp_report_id,
            sanitized_preview=preview,
            metadata={
                "untrusted_external_content": True,
                "never_system_instruction": source_type == "prompt",
                "truncated": len(encoded) > MCP_MAX_RESOURCE_PREVIEW_BYTES,
            },
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        await self._repo.insert_mcp_content_sanitization_report(report.model_dump(mode="json"))
        return report

    async def list_reports(self, server_id: str) -> list[MCPContentSanitizationReport]:
        return [
            MCPContentSanitizationReport(**row)
            for row in await self._repo.list_mcp_content_sanitization_reports(server_id)
        ]


class MCPOutputActionGuard:
    def __init__(self, repo: SkillMcpRepository) -> None:
        self._repo = repo

    async def record_taint(
        self,
        *,
        organization_id: str,
        server_id: str,
        mcp_call_id: str | None,
        tool_call_id: str | None,
        value: Any,
        target_action: str | None,
        target_risk_level: RiskLevel,
        trace_id: str | None,
    ) -> MCPOutputTaintRecord:
        text = _stringify(value)
        reason_codes = ["mcp_output_untrusted"]
        guard_decision = "allow_untrusted_context"
        if _INJECTION_RE.search(text):
            reason_codes.append("prompt_injection_detected")
            guard_decision = "manual_review_required"
        if _HIGH_RISK_ACTION_RE.search(text) or _risk_order(target_risk_level) >= 4:
            reason_codes.append("high_risk_action_requires_clean_source")
            guard_decision = "approval_or_deny"
        record = MCPOutputTaintRecord(
            taint_record_id=new_id("mcptaint"),
            organization_id=organization_id,
            server_id=server_id,
            mcp_call_id=mcp_call_id,
            tool_call_id=tool_call_id,
            taint_source="mcp_tool_output",
            target_action=target_action,
            target_risk_level=target_risk_level,
            guard_decision=guard_decision,
            reason_codes=sorted(set(reason_codes)),
            source_refs=[
                {"type": "mcp_call", "id": mcp_call_id},
                {"type": "tool_call", "id": tool_call_id},
            ],
            policy_snapshot={
                "untrusted_output_marker": True,
                "high_risk_action_requires_clean_source": True,
                "direct_tool_execution": "deny",
            },
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        await self._repo.insert_mcp_output_taint_record(record.model_dump(mode="json"))
        return record

    async def list_records(self, server_id: str) -> list[MCPOutputTaintRecord]:
        return [
            MCPOutputTaintRecord(**row)
            for row in await self._repo.list_mcp_output_taint_records(server_id)
        ]


def filter_valid_tools(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "")
        if not name or name in seen or not _valid_tool_schema(item):
            continue
        seen.add(name)
        valid.append(item)
    return valid


def filter_valid_prompts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        name = str(item.get("name") or "")
        if not name or name in seen or not _valid_prompt_schema(item):
            continue
        seen.add(name)
        valid.append(item)
    return valid


def filter_valid_resources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        uri = str(item.get("uri") or "")
        if not uri or uri in seen:
            continue
        seen.add(uri)
        valid.append(item)
    return valid


def _valid_tool_schema(item: dict[str, Any]) -> bool:
    schema = item.get("inputSchema") or item.get("input_schema") or {}
    return isinstance(item.get("name"), str) and bool(item["name"]) and isinstance(schema, dict)


def _valid_prompt_schema(item: dict[str, Any]) -> bool:
    arguments = item.get("arguments") or item.get("argumentsSchema") or {}
    return isinstance(item.get("name"), str) and bool(item["name"]) and isinstance(
        arguments,
        (dict, list),
    )


def _safe_protocol_payload(value: dict[str, Any]) -> dict[str, Any]:
    safe = redact(value)
    if not isinstance(safe, dict):
        return {}
    return {
        "keys": sorted(str(key) for key in safe.keys()),
        "protocolVersion": safe.get("protocolVersion") or safe.get("protocol_version"),
        "serverInfo": safe.get("serverInfo"),
        "item_count": _payload_item_count(safe),
    }


def _payload_item_count(value: dict[str, Any]) -> int:
    for key in ("tools", "resources", "prompts", "content"):
        if isinstance(value.get(key), list):
            return len(value[key])
    return 0


def _hash_text(value: str) -> str | None:
    if not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    raw = json.dumps(redact(value), ensure_ascii=False, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _stringify(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _risk_order(risk: RiskLevel) -> int:
    return int(risk.value.removeprefix("R"))
