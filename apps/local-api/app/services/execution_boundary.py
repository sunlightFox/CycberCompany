from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core_types import (
    ExecutionBoundaryDiagnostic,
    MCPProcessPolicyCheck,
    RiskLevel,
    TerminalSandboxProfile,
    ToolActionPolicy,
    ToolOutputDlpReport,
    ToolPolicyDecision,
)
from trace_service import TraceService, redact

from app.core.time import new_id, utc_now_iso
from app.db.repositories.execution_boundary_repo import ExecutionBoundaryRepository
from app.services.safety_policy import RuntimeSafetyPolicyService
from app.services.terminal_sandbox import (
    TerminalSandboxRequest,
    TerminalSandboxResult,
    TerminalSandboxRunner,
    command_network_policy,
    default_os_sandbox_backend,
)

DEFAULT_SANDBOX_PROFILE_ID = "task_artifact_policy_guard"

_RISK_VALUES = {risk.value: risk for risk in RiskLevel}
_SENSITIVE_ARG_PATTERNS = (
    r"(?i)(^|[\\/\s])\.env(\.local)?([\\/\s]|$)",
    r"(?i)master\.key",
    r"(?i)local_secrets\.json",
    r"(?i)(^|[\\/\s])\.ssh([\\/\s]|$)",
    r"(?i)c:\\users\\[^\\\s]+",
    r"(?i)c:\\windows",
    r"(?i)\\windows\\system32",
    r"(?i)(^|[\\/\s])(?:wallet|browser profiles?)([\\/\s]|$)",
    r"(?i)(^|[\s])/(etc|bin|sbin|usr|var|root)(/|\s|$)",
)
_TERMINAL_DESTRUCTIVE_MARKERS = (
    "remove-item",
    " rm ",
    "rm -",
    "del /",
    "format ",
    "shutdown",
    "reboot",
    "reg delete",
    "git reset --hard",
    "cipher ",
    "diskpart",
    "mkfs",
    "dd if=",
    "bcdedit",
    "takeown ",
    "icacls ",
)
_TERMINAL_MUTATION_MARKERS = (
    " set-item ",
    " new-item ",
    " move-item ",
    " copy-item ",
    "ren ",
    "rename-item",
    "chmod ",
    "chown ",
    "pip install",
    "npm install",
)
_TERMINAL_SYMLINK_MARKERS = (
    "mklink",
    "ln -s",
    "new-item",
    "itemtype symbolic",
    "symlink_to",
)
_DLP_PATTERNS = (
    (
        "api_key",
        RiskLevel.R5,
        re.compile(
            r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{12,}"
            r"|(?i:api[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+"
            r"|(?i:api[_-]?key)%3[dD][^&\s,;]+"
        ),
        "[REDACTED_API_KEY]",
    ),
    (
        "token",
        RiskLevel.R5,
        re.compile(r"(?i:token)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        "[REDACTED_TOKEN]",
    ),
    (
        "cookie",
        RiskLevel.R5,
        re.compile(r"(?i:cookie)\s*[:=]\s*['\"]?[^'\"\s,;]+"),
        "[REDACTED_COOKIE]",
    ),
    (
        "private_key",
        RiskLevel.R6,
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----"
            r"|(?i:private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;]+",
            re.S,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        "mnemonic",
        RiskLevel.R6,
        re.compile(r"\b(?:[a-z]{3,8}\s+){11,23}[a-z]{3,8}\b", re.I),
        "[REDACTED_MNEMONIC]",
    ),
    (
        "local_sensitive_path",
        RiskLevel.R5,
        re.compile(
            r"\b[A-Za-z]:\\Users\\[^\\\s]+(?:\\[^\s,;]+)*"
            r"|/(?:Users|home)/[^/\s]+(?:/[^\s,;]+)*"
            r"|(^|[\\/\s])\.ssh([\\/\s]|$)",
            re.I,
        ),
        "[REDACTED_LOCAL_PATH]",
    ),
)


@dataclass(frozen=True)
class DlpScanResult:
    report: ToolOutputDlpReport
    redacted_value: Any


class ExecutionBoundaryService:
    def __init__(
        self,
        *,
        repo: ExecutionBoundaryRepository,
        trace_service: TraceService,
        safety_policy_service: RuntimeSafetyPolicyService | None = None,
    ) -> None:
        self._repo = repo
        self._trace = trace_service
        self._safety_policy = safety_policy_service
        self._sandbox_runner = TerminalSandboxRunner()

    def set_terminal_sandbox_backend_override(self, backend: str | None) -> None:
        self._sandbox_runner.set_backend_override(backend)

    async def ensure_defaults(self, tools: list[dict[str, Any]]) -> None:
        await self.ensure_terminal_sandbox_profile()
        now = utc_now_iso()
        for tool in tools:
            policy = _policy_for_tool(tool, now)
            await self._repo.upsert_tool_action_policy(policy)

    async def ensure_terminal_sandbox_profile(self) -> TerminalSandboxProfile:
        existing = await self._repo.get_terminal_sandbox_profile(DEFAULT_SANDBOX_PROFILE_ID)
        if existing is not None and existing.get("os_sandbox_backend") in {
            "windows_job_object",
            "policy_guard",
            "windows_low_integrity",
            "container",
            "disabled",
        }:
            return TerminalSandboxProfile(**existing)
        now = utc_now_iso()
        data = _default_sandbox_profile(now)
        await self._repo.upsert_terminal_sandbox_profile(data)
        created = await self._repo.get_terminal_sandbox_profile(DEFAULT_SANDBOX_PROFILE_ID)
        return TerminalSandboxProfile(**(created or data))

    async def sandbox_status(self) -> dict[str, Any]:
        profile = await self.ensure_terminal_sandbox_profile()
        status = self._sandbox_runner.status(profile)
        latest = await self._repo.latest_execution_boundary_diagnostic(
            subject_type="terminal_sandbox_run"
        )
        return dict(
            redact(
                {
                    **status,
                    "profile_id": profile.profile_id,
                    "last_diagnostic_summary": (
                        latest.get("summary") if latest is not None else None
                    ),
                }
            )
        )

    async def list_policies(self) -> list[ToolActionPolicy]:
        return [
            ToolActionPolicy(**row)
            for row in await self._repo.list_tool_action_policies()
        ]

    async def list_decisions(self, tool_call_id: str) -> list[ToolPolicyDecision]:
        return [
            ToolPolicyDecision(**row)
            for row in await self._repo.list_tool_policy_decisions(tool_call_id)
        ]

    async def list_dlp_reports(self, tool_call_id: str) -> list[ToolOutputDlpReport]:
        return [
            ToolOutputDlpReport(**row)
            for row in await self._repo.list_dlp_reports_for_tool_call(tool_call_id)
        ]

    async def run_terminal_command(
        self,
        *,
        organization_id: str,
        task_id: str,
        command: str,
        cwd: Path,
        timeout_seconds: int | None,
        max_output_bytes: int | None,
        tool_call_id: str | None,
        trace_id: str | None,
    ) -> TerminalSandboxResult:
        profile = await self.ensure_terminal_sandbox_profile()
        timeout = min(int(timeout_seconds or profile.timeout_seconds), profile.timeout_seconds)
        output_limit = min(
            int(max_output_bytes or profile.max_output_bytes),
            profile.max_output_bytes,
        )
        result = await self._sandbox_runner.run(
            TerminalSandboxRequest(
                command=command,
                cwd=cwd,
                task_id=task_id,
                timeout_seconds=timeout,
                max_output_bytes=output_limit,
                profile=profile,
            )
        )
        await self.create_diagnostic(
            organization_id=organization_id,
            subject_type="terminal_sandbox_run",
            subject_id=tool_call_id or task_id,
            summary={
                "profile_id": profile.profile_id,
                **result.diagnostic_summary(),
            },
            status=(
                "timeout"
                if result.timed_out
                else "completed"
                if result.exit_code == 0
                else "failed"
            ),
            trace_id=trace_id,
        )
        return result

    async def decide_tool_action(
        self,
        *,
        organization_id: str,
        tool_name: str,
        source: str,
        requested_risk_level: RiskLevel,
        args: dict[str, Any],
        task_id: str | None,
        member_id: str | None,
        tool_call_id: str | None,
        trace_id: str | None,
    ) -> ToolPolicyDecision:
        policy = await self._policy_for_request(tool_name, source, requested_risk_level)
        command_policy = (
            self.classify_terminal_command(str(args.get("command") or ""))
            if tool_name == "terminal.run"
            else None
        )
        readonly_chat_terminal = bool(args.get("chat_readonly_command"))
        action_category = _action_category_for_request(tool_name, args, policy)
        terminal_network = (
            command_network_policy(str(args.get("command") or ""))
            if tool_name == "terminal.run"
            else None
        )
        if terminal_network is not None and terminal_network["category"] != "terminal_command":
            action_category = terminal_network["category"]
        requested = requested_risk_level
        policy_risk = policy.risk_level
        if (
            tool_name == "terminal.run"
            and readonly_chat_terminal
            and command_policy is not None
            and command_policy["reason"] == "sandboxed_terminal"
        ):
            policy_risk = requested
        if tool_name == "browser.download" and args.get("workflow_low_risk_download"):
            policy_risk = requested
        command_risk = requested if readonly_chat_terminal else (
            _risk_from_class(command_policy["command_class"])
            if command_policy is not None
            else RiskLevel.R1
        )
        effective_risk = _max_risk(_max_risk(requested, policy_risk), command_risk)
        reason_codes = ["tool_policy_checked"]
        required_controls: list[str] = []
        decision = "allow"

        if policy.status != "active":
            decision = "deny"
            reason_codes.append("policy_disabled")
        if policy.policy_id == "policy_unknown_tool":
            decision = "deny"
            reason_codes.append("unknown_tool_default_deny")
        if policy.requires_task_binding and not task_id:
            reason_codes.append("task_binding_required")
            if tool_name == "terminal.run":
                decision = "approval_required"
                required_controls.append("strong_approval")
            else:
                decision = "deny"
        if tool_name == "terminal.run" and args.get("cwd"):
            decision = "deny"
            reason_codes.append("terminal_custom_cwd_denied")
        if command_policy is not None:
            reason_codes.extend(command_policy["reason_codes"])
            if command_policy["decision"] == "deny":
                decision = "deny"
        serialized_args = json.dumps(args, ensure_ascii=False, default=str)
        if tool_name != "host.fs.list" and _contains_sensitive_path(serialized_args):
            decision = "deny"
            reason_codes.append("sensitive_path_denied")
        if tool_name == "terminal.run" and _contains_path_traversal(serialized_args):
            decision = "deny"
            reason_codes.append("terminal_path_traversal_denied")
        if tool_name != "host.fs.list" and _contains_denied_pattern(
            serialized_args,
            policy.deny_patterns,
        ):
            decision = "deny"
            reason_codes.append("policy_deny_pattern")
        if action_category in {"browser_submit", "browser_upload", "payment", "network_write"}:
            effective_risk = _max_risk(effective_risk, RiskLevel.R5)
            required_controls.append("approval")
            reason_codes.append(f"{action_category}_requires_approval")
        if _risk_order(effective_risk) >= _risk_order(RiskLevel.R5):
            required_controls.append("strong_approval")
            if decision == "allow":
                decision = "approval_required"
            reason_codes.append("r5_plus_requires_approval")
        elif _risk_order(effective_risk) >= _risk_order(RiskLevel.R3):
            required_controls.append("approval")
            if decision == "allow":
                decision = "approval_required"
            reason_codes.append("risk_requires_approval")

        active_policy = None
        if self._safety_policy is not None:
            active_policy = await self._safety_policy.get_policy(
                organization_id=organization_id
            )
        if (
            active_policy is not None
            and decision == "approval_required"
            and active_policy.should_skip_approval(
                action=tool_name,
                risk_level=effective_risk,
                action_category=action_category,
                payload=args,
                reason_codes=reason_codes,
                terminal_command_policy=command_policy if command_policy is not None else {},
            )
        ):
            decision = "allow"
            required_controls = active_policy.without_approval_controls(required_controls)
            reason_codes.append("balanced_personal_auto_approved")
        if (
            decision == "approval_required"
            and tool_name == "browser.submit"
            and bool(args.get("test_account_approval_bypass"))
            and str(args.get("action") or "").startswith("external_platform_")
        ):
            decision = "allow"
            required_controls = [
                control for control in required_controls if control not in {"approval", "strong_approval"}
            ]
            reason_codes.append("external_platform_test_account_auto_approved")

        sandbox_profile_id = (
            DEFAULT_SANDBOX_PROFILE_ID if tool_name.startswith("terminal.") else None
        )
        sandbox_status = None
        sandbox_profile = None
        if sandbox_profile_id:
            sandbox_profile = await self.ensure_terminal_sandbox_profile()
            sandbox_status = self._sandbox_runner.status(sandbox_profile)
        snapshot = {
            "action_category": action_category,
            "policy_id": policy.policy_id,
            "required_capabilities": policy.required_capabilities,
            "required_assets": policy.required_asset_kinds,
            "output_dlp_policy": policy.output_dlp_policy,
            "approval_profile": (
                active_policy.approval_profile
                if active_policy is not None
                else "balanced_personal"
            ),
            "sandbox_profile_id": sandbox_profile_id,
            "boundary": "phase27_execution_boundary",
            "os_sandbox_backend": (
                sandbox_status["active_backend"] if sandbox_status else None
            ),
            "backend_status": (
                "implemented_with_fallback"
                if sandbox_status and sandbox_status.get("fallback_reason")
                else "active"
                if sandbox_status
                else None
            ),
            "fallback_chain": (
                sandbox_status.get("fallback_chain") if sandbox_status else []
            ),
            "degraded_reason": (
                sandbox_status.get("fallback_reason") if sandbox_status else None
            ),
            "env_policy": (
                sandbox_profile.env_policy if sandbox_profile is not None else None
            ),
            "filesystem_policy": (
                sandbox_profile.filesystem_policy if sandbox_profile is not None else None
            ),
            "network_policy": (
                terminal_network if terminal_network is not None else policy.action_category
            ),
        }
        decision_model = ToolPolicyDecision(
            decision_id=new_id("tbnd"),
            organization_id=organization_id,
            tool_call_id=tool_call_id,
            task_id=task_id,
            member_id=member_id,
            tool_name=tool_name,
            policy_id=None if policy.policy_id == "policy_unknown_tool" else policy.policy_id,
            source=source,
            action_category=action_category,
            requested_risk_level=requested,
            effective_risk_level=effective_risk,
            decision=decision,
            reason_codes=sorted(set(reason_codes)),
            required_controls=sorted(set(required_controls)),
            policy_snapshot=redact(snapshot),
            sandbox_profile_id=sandbox_profile_id,
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        await self._repo.insert_tool_policy_decision(decision_model.model_dump(mode="json"))
        await self._trace_boundary(trace_id, decision_model)
        return decision_model

    def classify_terminal_command(self, command: str) -> dict[str, Any]:
        lowered = command.lower()
        wrapped = f" {lowered} "
        network = command_network_policy(command)
        network_class = "R5" if network["category"] != "terminal_command" else "R5"
        if _contains_sensitive_path(lowered):
            return {
                "decision": "deny",
                "reason": "sensitive_path",
                "reason_codes": ["terminal_sensitive_path_denied"],
                "command_class": "R7",
            }
        if _contains_path_traversal(command):
            return {
                "decision": "deny",
                "reason": "path_traversal",
                "reason_codes": ["terminal_path_traversal_denied"],
                "command_class": "R7",
            }
        if any(item in wrapped for item in _TERMINAL_SYMLINK_MARKERS) and any(
            item in wrapped for item in ("mklink", "ln -s", "symbolic", "symlink")
        ):
            return {
                "decision": "deny",
                "reason": "symlink_escape",
                "reason_codes": ["terminal_symlink_escape_denied"],
                "command_class": "R7",
            }
        if any(item in wrapped for item in _TERMINAL_DESTRUCTIVE_MARKERS):
            return {
                "decision": "deny",
                "reason": "destructive_command",
                "reason_codes": ["terminal_destructive_command_denied"],
                "command_class": "R6",
            }
        if any(item in wrapped for item in _TERMINAL_MUTATION_MARKERS):
            return {
                "decision": "allow",
                "reason": "mutation_requires_approval",
                "reason_codes": [
                    "terminal_mutation_requires_approval",
                    *network["reason_codes"],
                ],
                "command_class": "R5",
            }
        return {
            "decision": "allow",
            "reason": "sandboxed_terminal",
            "reason_codes": ["terminal_sandbox_profile_applied", *network["reason_codes"]],
            "command_class": network_class,
        }

    async def scan_output(
        self,
        *,
        organization_id: str,
        source_type: str,
        scan_target: str,
        value: Any,
        tool_call_id: str | None = None,
        mcp_call_id: str | None = None,
        task_id: str | None = None,
        source_id: str | None = None,
        trace_id: str | None = None,
    ) -> DlpScanResult:
        redacted_value = _redact_value(value)
        text = _stringify(value)
        findings, risk_level, redacted_preview = _scan_text(text)
        report = ToolOutputDlpReport(
            dlp_report_id=new_id("dlp"),
            organization_id=organization_id,
            tool_call_id=tool_call_id,
            mcp_call_id=mcp_call_id,
            task_id=task_id,
            source_type=source_type,
            source_id=source_id,
            scan_target=scan_target,
            findings=findings,
            redaction_count=sum(int(item.get("count", 0)) for item in findings),
            blocked=False,
            manual_review_required=_risk_order(risk_level) >= _risk_order(RiskLevel.R6),
            risk_level=risk_level,
            redacted_preview=redacted_preview[:500],
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        await self._repo.insert_dlp_report(report.model_dump(mode="json"))
        return DlpScanResult(report=report, redacted_value=redacted_value)

    async def check_mcp_process_policy(
        self,
        *,
        organization_id: str,
        server_id: str | None,
        display_name: str | None,
        command: str | None,
        command_allowed: bool,
        args_schema_valid: bool,
        env_refs_only: bool,
        no_inline_secret: bool,
        server_scope_valid: bool,
        member_scope_valid: bool,
        reason_codes: list[str],
        trace_id: str | None,
    ) -> MCPProcessPolicyCheck:
        passed = all(
            (
                command_allowed,
                args_schema_valid,
                env_refs_only,
                no_inline_secret,
                server_scope_valid,
                member_scope_valid,
            )
        )
        check = MCPProcessPolicyCheck(
            check_id=new_id("mcppol"),
            organization_id=organization_id,
            server_id=server_id,
            display_name=display_name,
            command=command,
            command_allowed=command_allowed,
            args_schema_valid=args_schema_valid,
            env_refs_only=env_refs_only,
            no_inline_secret=no_inline_secret,
            server_scope_valid=server_scope_valid,
            member_scope_valid=member_scope_valid,
            network_policy="local_stdio_only",
            safety_preflight="required",
            decision="allow" if passed else "deny",
            reason_codes=sorted(
                set(reason_codes or (["mcp_process_policy_passed"] if passed else []))
            ),
            policy_snapshot=redact(
                {
                    "command_allowlist": command_allowed,
                    "env_refs_only": env_refs_only,
                    "no_inline_secret": no_inline_secret,
                    "scope": "server_and_member",
                    "network_policy": "local_stdio_only",
                }
            ),
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        await self._repo.insert_mcp_process_policy_check(check.model_dump(mode="json"))
        return check

    async def create_diagnostic(
        self,
        *,
        organization_id: str,
        subject_type: str,
        subject_id: str | None,
        summary: dict[str, Any],
        status: str = "ok",
        trace_id: str | None = None,
    ) -> ExecutionBoundaryDiagnostic:
        diagnostic = ExecutionBoundaryDiagnostic(
            diagnostic_id=new_id("ebd"),
            organization_id=organization_id,
            subject_type=subject_type,
            subject_id=subject_id,
            summary=redact(summary),
            status=status,
            trace_id=trace_id,
            created_at=utc_now_iso(),
        )
        await self._repo.insert_execution_boundary_diagnostic(
            diagnostic.model_dump(mode="json")
        )
        return diagnostic

    async def get_diagnostic(self, diagnostic_id: str) -> ExecutionBoundaryDiagnostic | None:
        row = await self._repo.get_execution_boundary_diagnostic(diagnostic_id)
        return ExecutionBoundaryDiagnostic(**row) if row else None

    async def sandbox_profile(
        self,
        profile_id: str = DEFAULT_SANDBOX_PROFILE_ID,
    ) -> TerminalSandboxProfile | None:
        row = await self._repo.get_terminal_sandbox_profile(profile_id)
        return TerminalSandboxProfile(**row) if row else None

    async def _policy_for_request(
        self,
        tool_name: str,
        source: str,
        requested_risk_level: RiskLevel,
    ) -> ToolActionPolicy:
        existing = await self._repo.get_tool_action_policy(tool_name)
        if existing is not None:
            return ToolActionPolicy(**existing)
        now = utc_now_iso()
        if source in {"mcp", "skill"}:
            data = _dynamic_extension_policy(tool_name, source, requested_risk_level, now)
            await self._repo.upsert_tool_action_policy(data)
            row = await self._repo.get_tool_action_policy(tool_name)
            return ToolActionPolicy(**(row or data))
        return ToolActionPolicy(
            policy_id="policy_unknown_tool",
            tool_name=tool_name,
            source=source,
            action_category="unknown",
            risk_level=RiskLevel.R7,
            status="disabled",
        )

    async def _trace_boundary(
        self,
        trace_id: str | None,
        decision: ToolPolicyDecision,
    ) -> None:
        if trace_id is None:
            return
        span_id = await self._trace.start_span(
            trace_id,
            span_type="tool_call",
            name="execution boundary decision",
            metadata={
                "decision_id": decision.decision_id,
                "tool_call_id": decision.tool_call_id,
                "tool_name": decision.tool_name,
                "decision": decision.decision,
            },
        )
        await self._trace.end_span(
            span_id,
            output_data={
                "effective_risk_level": decision.effective_risk_level.value,
                "reason_codes": decision.reason_codes,
            },
        )


def _policy_for_tool(tool: dict[str, Any], now: str) -> dict[str, Any]:
    tool_name = str(tool["tool_name"])
    source = str(tool.get("source") or "builtin")
    risk = str((tool.get("risk_policy") or {}).get("default") or "R1")
    category = _category_from_tool_name(tool_name)
    taskless_read_tools = {"host.fs.list", "browser.snapshot", "browser.search"}
    requires_task = tool_name.startswith(
        ("file.", "browser.", "terminal.", "account.", "project.", "runtime.", "host.")
    ) and tool_name not in taskless_read_tools
    return {
        "policy_id": f"tap_{_safe_policy_id(tool_name)}",
        "tool_name": tool_name,
        "source": source,
        "action_category": category,
        "risk_level": risk,
        "allowed_scopes": (
            ["host_filesystem_metadata"]
            if tool_name == "host.fs.list"
            else (["browser_untrusted_readonly"] if tool_name in taskless_read_tools else [])
            or (["task_artifact"] if requires_task else ["local_backend"])
        ),
        "required_capabilities": _required_capabilities(tool_name),
        "required_asset_kinds": _required_asset_kinds(tool_name),
        "requires_task_binding": requires_task,
        "requires_approval_from": "R3" if _risk_order(_risk(risk)) >= 3 else None,
        "deny_patterns": list(_SENSITIVE_ARG_PATTERNS),
        "output_dlp_policy": {"scan": True, "redact": True, "manual_review_from": "R6"},
        "audit_level": "high" if _risk_order(_risk(risk)) >= 5 else "standard",
        "status": str(tool.get("status") or "active"),
        "created_at": now,
        "updated_at": now,
    }


def _dynamic_extension_policy(
    tool_name: str,
    source: str,
    requested_risk_level: RiskLevel,
    now: str,
) -> dict[str, Any]:
    return {
        "policy_id": f"tap_{_safe_policy_id(tool_name)}",
        "tool_name": tool_name,
        "source": source,
        "action_category": f"{source}_tool_call",
        "risk_level": requested_risk_level.value,
        "allowed_scopes": ["tool_runtime_only"],
        "required_capabilities": [f"{source}.execute"],
        "required_asset_kinds": [],
        "requires_task_binding": False,
        "requires_approval_from": "R3",
        "deny_patterns": list(_SENSITIVE_ARG_PATTERNS),
        "output_dlp_policy": {"scan": True, "redact": True, "untrusted_external_content": True},
        "audit_level": "high" if _risk_order(requested_risk_level) >= 5 else "standard",
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }


def _default_sandbox_profile(now: str) -> dict[str, Any]:
    backend = default_os_sandbox_backend()
    degraded_reason = None
    if backend != "windows_job_object":
        degraded_reason = "os_sandbox_fallback_policy_guard"
    elif os.name != "nt":
        degraded_reason = "windows_job_object_unavailable_on_non_windows"
    return {
        "profile_id": DEFAULT_SANDBOX_PROFILE_ID,
        "working_dir_policy": "task_artifact_sandbox_only",
        "allowed_executables": [
            "echo",
            "dir",
            "ls",
            "pwd",
            "type",
            "cat",
            "findstr",
            "rg",
            "git",
            "python",
            "py",
            "curl",
            "wget",
        ],
        "denied_executables": [
            "powershell",
            "cmd",
            "pwsh",
            "bash",
            "sh",
            "rm",
            "del",
            "format",
            "diskpart",
        ],
        "env_policy": {"inherit": "minimal", "secret_env": "deny", "inline_secret": "deny"},
        "network_policy": "external_network_requires_approval_or_deny",
        "filesystem_policy": {
            "root": "task_artifact_sandbox",
            "sensitive_paths": "deny",
            "system_paths": "deny",
            "path_traversal": "deny",
            "symlink_escape": "deny",
        },
        "timeout_seconds": 30,
        "max_output_bytes": 200000,
        "os_sandbox_backend": backend,
        "degraded_reason": degraded_reason,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }


def _category_from_tool_name(tool_name: str) -> str:
    if tool_name == "terminal.run":
        return "terminal_command"
    if tool_name.startswith("terminal."):
        return "terminal_control"
    if tool_name.startswith("project."):
        if tool_name == "project.clone":
            return "project_clone"
        if tool_name == "project.install_deps":
            return "project_dependency_install"
        if tool_name in {"project.run", "project.stop"}:
            return "managed_process"
        return "project_deployment"
    if tool_name.startswith("runtime."):
        return "portable_toolchain"
    if tool_name.startswith("host."):
        if tool_name == "host.fs.list":
            return "host_filesystem_read"
        return "host_install" if tool_name == "host.install_software" else "host_detect"
    if tool_name == "file.delete":
        return "file_delete"
    if tool_name.startswith("file."):
        if tool_name in {"file.write", "file.copy", "file.move"}:
            return "file_write"
        return "file_read"
    if tool_name.startswith("browser."):
        return "browser_download" if tool_name == "browser.download" else "browser_read"
    if tool_name.startswith("knowledge."):
        return "knowledge_read"
    if tool_name.startswith("memory."):
        return "memory_write" if tool_name != "memory.search" else "memory_read"
    if tool_name.startswith("asset."):
        return "asset_broker"
    if tool_name == "account.publish_post":
        return "account_external_post"
    if tool_name == "account.login":
        return "account_login"
    if tool_name.startswith("account."):
        return "account_draft"
    if tool_name.startswith("hardware."):
        return "hardware_read"
    if tool_name.startswith("mcp."):
        return "mcp_tool_call"
    return "tool_action"


def _action_category_for_request(
    tool_name: str,
    args: dict[str, Any],
    policy: ToolActionPolicy,
) -> str:
    action = str(args.get("action") or args.get("intent") or "").lower()
    destination = str(args.get("destination") or args.get("url") or "").lower()
    if tool_name in {"browser.open", "browser.search", "browser.snapshot", "browser.screenshot"}:
        return policy.action_category
    if tool_name == "browser.download":
        return "browser_download"
    if tool_name == "browser.submit":
        return "browser_submit"
    if tool_name.startswith("browser.") and any(
        marker in f"{action} {destination}"
        for marker in ("submit", "upload", "login", "payment", "pay", "checkout")
    ):
        if "upload" in action:
            return "browser_upload"
        if "payment" in action or "pay" in action or "checkout" in action:
            return "payment"
        return "browser_submit"
    if destination.startswith(("http://", "https://")) and action in {"post", "send", "upload"}:
        return "network_write"
    if tool_name == "account.publish_post":
        return "network_write"
    return policy.action_category


def _required_capabilities(tool_name: str) -> list[str]:
    if tool_name.startswith("file."):
        return ["task_artifact.read_write"]
    if tool_name.startswith("browser."):
        return ["browser.read"]
    if tool_name.startswith("terminal."):
        return ["terminal.sandboxed"]
    if tool_name.startswith("project."):
        return ["project_deployment.execute"]
    if tool_name.startswith("runtime."):
        return ["toolchain.prepare"]
    if tool_name == "host.fs.list":
        return ["host_filesystem.read_metadata"]
    if tool_name.startswith("host."):
        return ["host_install.execute"]
    if tool_name.startswith("knowledge."):
        return ["knowledge.read"]
    if tool_name.startswith("memory."):
        return ["memory.read_write"]
    if tool_name.startswith("asset."):
        return ["asset_broker.resolve"]
    return []


def _required_asset_kinds(tool_name: str) -> list[str]:
    if tool_name.startswith("knowledge."):
        return ["knowledge_base"]
    if tool_name.startswith("account."):
        return ["account"]
    if tool_name.startswith("hardware."):
        return ["hardware"]
    return []


def _contains_sensitive_path(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in _SENSITIVE_ARG_PATTERNS)


def _contains_path_traversal(text: str) -> bool:
    return bool(re.search(r"(^|[\\/\s'\"`])\.\.([\\/]|$)", text))


def _contains_denied_pattern(text: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        try:
            if re.search(pattern, text):
                return True
        except re.error:
            if pattern in text:
                return True
    return False


def _scan_text(text: str) -> tuple[list[dict[str, Any]], RiskLevel, str]:
    findings: list[dict[str, Any]] = []
    redacted_text = str(redact(text))
    risk = RiskLevel.R1
    for name, pattern, replacement in (
        (item[0], item[2], item[3]) for item in _DLP_PATTERNS
    ):
        matches = list(pattern.finditer(redacted_text))
        if not matches:
            continue
        pattern_risk = next(item[1] for item in _DLP_PATTERNS if item[0] == name)
        risk = _max_risk(risk, pattern_risk)
        findings.append(
            {
                "finding_type": name,
                "severity": pattern_risk.value,
                "count": len(matches),
                "redacted": True,
            }
        )
        redacted_text = pattern.sub(replacement, redacted_text)
    return findings, risk, redacted_text


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return _scan_text(value)[2]
    return redact(value)


def _stringify(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _risk(value: str) -> RiskLevel:
    return _RISK_VALUES.get(value.upper(), RiskLevel.R1)


def _risk_from_class(command_class: str) -> RiskLevel:
    return _risk(command_class)


def _risk_order(risk: RiskLevel) -> int:
    return int(risk.value.removeprefix("R"))


def _max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
    return left if _risk_order(left) >= _risk_order(right) else right


def _safe_policy_id(tool_name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", tool_name).strip("_") or "tool"
