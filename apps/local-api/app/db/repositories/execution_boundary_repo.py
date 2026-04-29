from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class ExecutionBoundaryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_tool_action_policy(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO tool_action_policies (
              policy_id, tool_name, source, action_category, risk_level,
              allowed_scopes_json, required_capabilities_json, required_asset_kinds_json,
              requires_task_binding, requires_approval_from, deny_patterns_json,
              output_dlp_policy_json, audit_level, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tool_name) DO UPDATE SET
              source = excluded.source,
              action_category = excluded.action_category,
              risk_level = excluded.risk_level,
              allowed_scopes_json = excluded.allowed_scopes_json,
              required_capabilities_json = excluded.required_capabilities_json,
              required_asset_kinds_json = excluded.required_asset_kinds_json,
              requires_task_binding = excluded.requires_task_binding,
              requires_approval_from = excluded.requires_approval_from,
              deny_patterns_json = excluded.deny_patterns_json,
              output_dlp_policy_json = excluded.output_dlp_policy_json,
              audit_level = excluded.audit_level,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["policy_id"],
                data["tool_name"],
                data["source"],
                data["action_category"],
                data.get("risk_level", "R1"),
                _json(data.get("allowed_scopes", [])),
                _json(data.get("required_capabilities", [])),
                _json(data.get("required_asset_kinds", [])),
                1 if data.get("requires_task_binding") else 0,
                data.get("requires_approval_from"),
                _json(data.get("deny_patterns", [])),
                _json(data.get("output_dlp_policy", {})),
                data.get("audit_level", "standard"),
                data.get("status", "active"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_tool_action_policy(self, tool_name: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM tool_action_policies WHERE tool_name = ?",
            (tool_name,),
        )
        return _policy_from_row(dict(row)) if row else None

    async def list_tool_action_policies(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM tool_action_policies ORDER BY tool_name ASC"
        )
        return [_policy_from_row(dict(row)) for row in rows]

    async def insert_tool_policy_decision(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO tool_policy_decisions (
              decision_id, organization_id, tool_call_id, task_id, member_id,
              tool_name, policy_id, source, action_category, requested_risk_level,
              effective_risk_level, decision, reason_codes_json, required_controls_json,
              policy_snapshot_json, sandbox_profile_id, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["decision_id"],
                data["organization_id"],
                data.get("tool_call_id"),
                data.get("task_id"),
                data.get("member_id"),
                data["tool_name"],
                data.get("policy_id"),
                data["source"],
                data["action_category"],
                data.get("requested_risk_level", "R1"),
                data.get("effective_risk_level", "R1"),
                data["decision"],
                _json(data.get("reason_codes", [])),
                _json(data.get("required_controls", [])),
                _json(data.get("policy_snapshot", {})),
                data.get("sandbox_profile_id"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_tool_policy_decisions(self, tool_call_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM tool_policy_decisions
            WHERE tool_call_id = ?
            ORDER BY created_at ASC
            """,
            (tool_call_id,),
        )
        return [_decision_from_row(dict(row)) for row in rows]

    async def get_terminal_sandbox_profile(self, profile_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM terminal_sandbox_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        return _sandbox_profile_from_row(dict(row)) if row else None

    async def upsert_terminal_sandbox_profile(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO terminal_sandbox_profiles (
              profile_id, working_dir_policy, allowed_executables_json,
              denied_executables_json, env_policy_json, network_policy,
              filesystem_policy_json, timeout_seconds, max_output_bytes,
              os_sandbox_backend, degraded_reason, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
              working_dir_policy = excluded.working_dir_policy,
              allowed_executables_json = excluded.allowed_executables_json,
              denied_executables_json = excluded.denied_executables_json,
              env_policy_json = excluded.env_policy_json,
              network_policy = excluded.network_policy,
              filesystem_policy_json = excluded.filesystem_policy_json,
              timeout_seconds = excluded.timeout_seconds,
              max_output_bytes = excluded.max_output_bytes,
              os_sandbox_backend = excluded.os_sandbox_backend,
              degraded_reason = excluded.degraded_reason,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["profile_id"],
                data["working_dir_policy"],
                _json(data.get("allowed_executables", [])),
                _json(data.get("denied_executables", [])),
                _json(data.get("env_policy", {})),
                data["network_policy"],
                _json(data.get("filesystem_policy", {})),
                data.get("timeout_seconds", 30),
                data.get("max_output_bytes", 200000),
                data.get("os_sandbox_backend", "none_with_policy_guard"),
                data.get("degraded_reason"),
                data.get("status", "active"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def insert_dlp_report(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO tool_output_dlp_reports (
              dlp_report_id, organization_id, tool_call_id, mcp_call_id, task_id,
              source_type, source_id, scan_target, findings_json, redaction_count,
              blocked, manual_review_required, risk_level, redacted_preview,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["dlp_report_id"],
                data["organization_id"],
                data.get("tool_call_id"),
                data.get("mcp_call_id"),
                data.get("task_id"),
                data["source_type"],
                data.get("source_id"),
                data["scan_target"],
                _json(data.get("findings", [])),
                data.get("redaction_count", 0),
                1 if data.get("blocked") else 0,
                1 if data.get("manual_review_required") else 0,
                data.get("risk_level", "R1"),
                data.get("redacted_preview"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_dlp_reports_for_tool_call(self, tool_call_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM tool_output_dlp_reports
            WHERE tool_call_id = ?
            ORDER BY created_at ASC
            """,
            (tool_call_id,),
        )
        return [_dlp_report_from_row(dict(row)) for row in rows]

    async def insert_mcp_process_policy_check(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_process_policy_checks (
              check_id, organization_id, server_id, display_name, command,
              command_allowed, args_schema_valid, env_refs_only, no_inline_secret,
              server_scope_valid, member_scope_valid, network_policy, safety_preflight,
              decision, reason_codes_json, policy_snapshot_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["check_id"],
                data["organization_id"],
                data.get("server_id"),
                data.get("display_name"),
                data.get("command"),
                1 if data.get("command_allowed") else 0,
                1 if data.get("args_schema_valid") else 0,
                1 if data.get("env_refs_only") else 0,
                1 if data.get("no_inline_secret") else 0,
                1 if data.get("server_scope_valid") else 0,
                1 if data.get("member_scope_valid") else 0,
                data.get("network_policy", "local_stdio_only"),
                data.get("safety_preflight", "not_required"),
                data["decision"],
                _json(data.get("reason_codes", [])),
                _json(data.get("policy_snapshot", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_mcp_process_policy_checks(self, server_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM mcp_process_policy_checks
            WHERE server_id = ?
            ORDER BY created_at ASC
            """,
            (server_id,),
        )
        return [_mcp_check_from_row(dict(row)) for row in rows]

    async def insert_execution_boundary_diagnostic(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO execution_boundary_diagnostics (
              diagnostic_id, organization_id, subject_type, subject_id,
              summary_json, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["diagnostic_id"],
                data["organization_id"],
                data["subject_type"],
                data.get("subject_id"),
                _json(data.get("summary", {})),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_execution_boundary_diagnostic(
        self,
        diagnostic_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM execution_boundary_diagnostics WHERE diagnostic_id = ?",
            (diagnostic_id,),
        )
        return _diagnostic_from_row(dict(row)) if row else None

    async def latest_execution_boundary_diagnostic(
        self,
        *,
        subject_type: str | None = None,
    ) -> dict[str, Any] | None:
        if subject_type is None:
            row = await self._db.fetch_one(
                """
                SELECT *
                FROM execution_boundary_diagnostics
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        else:
            row = await self._db.fetch_one(
                """
                SELECT *
                FROM execution_boundary_diagnostics
                WHERE subject_type = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (subject_type,),
            )
        return _diagnostic_from_row(dict(row)) if row else None


def _policy_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allowed_scopes"] = json.loads(row.pop("allowed_scopes_json") or "[]")
    row["required_capabilities"] = json.loads(row.pop("required_capabilities_json") or "[]")
    row["required_asset_kinds"] = json.loads(row.pop("required_asset_kinds_json") or "[]")
    row["requires_task_binding"] = bool(row.pop("requires_task_binding"))
    row["deny_patterns"] = json.loads(row.pop("deny_patterns_json") or "[]")
    row["output_dlp_policy"] = json.loads(row.pop("output_dlp_policy_json") or "{}")
    return row


def _decision_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    row["required_controls"] = json.loads(row.pop("required_controls_json") or "[]")
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json") or "{}")
    return row


def _sandbox_profile_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allowed_executables"] = json.loads(row.pop("allowed_executables_json") or "[]")
    row["denied_executables"] = json.loads(row.pop("denied_executables_json") or "[]")
    row["env_policy"] = json.loads(row.pop("env_policy_json") or "{}")
    row["filesystem_policy"] = json.loads(row.pop("filesystem_policy_json") or "{}")
    return row


def _dlp_report_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["findings"] = json.loads(row.pop("findings_json") or "[]")
    row["blocked"] = bool(row.pop("blocked"))
    row["manual_review_required"] = bool(row.pop("manual_review_required"))
    return row


def _mcp_check_from_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "command_allowed",
        "args_schema_valid",
        "env_refs_only",
        "no_inline_secret",
        "server_scope_valid",
        "member_scope_valid",
    ):
        row[key] = bool(row[key])
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json") or "{}")
    return row


def _diagnostic_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["summary"] = json.loads(row.pop("summary_json") or "{}")
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
