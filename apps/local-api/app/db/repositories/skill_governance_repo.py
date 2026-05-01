from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class SkillGovernanceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_source(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_bundle_sources (
              source_id, organization_id, bundle_id, source_type, source_uri_redacted,
              source_uri_hash, signature_status, checksum, trust_level, metadata_json,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
              source_uri_redacted = excluded.source_uri_redacted,
              source_uri_hash = excluded.source_uri_hash,
              signature_status = excluded.signature_status,
              checksum = excluded.checksum,
              trust_level = excluded.trust_level,
              metadata_json = excluded.metadata_json,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["source_id"],
                data["organization_id"],
                data["bundle_id"],
                data["source_type"],
                data.get("source_uri_redacted"),
                data.get("source_uri_hash"),
                data.get("signature_status", "unsigned"),
                data.get("checksum"),
                data["trust_level"],
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def latest_source(self, bundle_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_bundle_sources
            WHERE bundle_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (bundle_id,),
        )
        return _source_from_row(dict(row)) if row else None

    async def upsert_version(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_bundle_versions (
              version_id, organization_id, bundle_id, bundle_revision, manifest_hash,
              signature_status, trust_level, permission_summary_json, risk_summary_json,
              manifest_redacted_json, status, installed_by_member_id, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bundle_id, bundle_revision, manifest_hash) DO UPDATE SET
              signature_status = excluded.signature_status,
              trust_level = excluded.trust_level,
              permission_summary_json = excluded.permission_summary_json,
              risk_summary_json = excluded.risk_summary_json,
              manifest_redacted_json = excluded.manifest_redacted_json,
              status = excluded.status,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["version_id"],
                data["organization_id"],
                data["bundle_id"],
                data["bundle_revision"],
                data["manifest_hash"],
                data.get("signature_status", "unsigned"),
                data["trust_level"],
                _json(data.get("permission_summary", {})),
                _json(data.get("risk_summary", {})),
                _json(data.get("manifest_redacted", {})),
                data["status"],
                data.get("installed_by_member_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def latest_version(self, bundle_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_bundle_versions
            WHERE bundle_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (bundle_id,),
        )
        return _version_from_row(dict(row)) if row else None

    async def insert_permission_preview(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_permission_previews (
              preview_id, organization_id, bundle_id, bundle_revision, manifest_hash,
              trust_level, risk_level, permission_summary_json, blocked_reasons_json,
              requires_user_grant, unattended_allowed, preview_hash, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["preview_id"],
                data["organization_id"],
                data.get("bundle_id"),
                data.get("bundle_revision"),
                data["manifest_hash"],
                data["trust_level"],
                data["risk_level"],
                _json(data.get("permission_summary", {})),
                _json(data.get("blocked_reasons", [])),
                1 if data.get("requires_user_grant", True) else 0,
                1 if data.get("unattended_allowed", False) else 0,
                data["preview_hash"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def latest_permission_preview(self, bundle_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_permission_previews
            WHERE bundle_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (bundle_id,),
        )
        return _preview_from_row(dict(row)) if row else None

    async def insert_static_analysis(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_static_analysis_reports (
              analysis_report_id, organization_id, bundle_id, bundle_revision,
              manifest_hash, status, risk_level, trust_level, reason_codes_json,
              blocked_reasons_json, warnings_json, remediation_hints_json,
              sensitive_findings_json, manifest_summary_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["analysis_report_id"],
                data["organization_id"],
                data.get("bundle_id"),
                data.get("bundle_revision"),
                data["manifest_hash"],
                data["status"],
                data["risk_level"],
                data["trust_level"],
                _json(data.get("reason_codes", [])),
                _json(data.get("blocked_reasons", [])),
                _json(data.get("warnings", [])),
                _json(data.get("remediation_hints", [])),
                _json(data.get("sensitive_findings", [])),
                _json(data.get("manifest_summary", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_static_analysis(self, bundle_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM skill_static_analysis_reports
            WHERE bundle_id = ?
            ORDER BY created_at DESC
            """,
            (bundle_id,),
        )
        return [_analysis_from_row(dict(row)) for row in rows]

    async def insert_grant(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_grants (
              skill_grant_id, organization_id, skill_id, bundle_id, subject_type,
              subject_id, allowed_tools_json, allowed_asset_actions_json,
              allowed_mcp_tools_json, denied_actions_json, approval_policy_json,
              status, grant_scope, created_by_member_id, revoked_by_member_id,
              revoke_reason, expires_at, trace_id, created_at, updated_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["skill_grant_id"],
                data["organization_id"],
                data["skill_id"],
                data["bundle_id"],
                data["subject_type"],
                data["subject_id"],
                _json(data.get("allowed_tools", [])),
                _json(data.get("allowed_asset_actions", [])),
                _json(data.get("allowed_mcp_tools", [])),
                _json(data.get("denied_actions", [])),
                _json(data.get("approval_policy", {})),
                data["status"],
                data.get("grant_scope", "explicit"),
                data.get("created_by_member_id"),
                data.get("revoked_by_member_id"),
                data.get("revoke_reason"),
                data.get("expires_at"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("revoked_at"),
            ),
        )

    async def list_grants(
        self,
        skill_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["skill_id = ?"]
        params: list[Any] = [skill_id]
        if status:
            where.append("status = ?")
            params.append(status)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM skill_grants
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            """,
            params,
        )
        return [_grant_from_row(dict(row)) for row in rows]

    async def active_grant(
        self,
        skill_id: str,
        subject_type: str,
        subject_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_grants
            WHERE skill_id = ?
              AND subject_type = ?
              AND subject_id = ?
              AND status = 'active'
              AND (expires_at IS NULL OR expires_at > datetime('now'))
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (skill_id, subject_type, subject_id),
        )
        return _grant_from_row(dict(row)) if row else None

    async def revoke_grants(
        self,
        skill_id: str,
        *,
        actor_member_id: str,
        reason: str | None,
        revoked_at: str,
    ) -> int:
        rowcount = await self._db.execute(
            """
            UPDATE skill_grants
            SET status = 'revoked',
                revoked_by_member_id = ?,
                revoke_reason = ?,
                revoked_at = ?,
                updated_at = ?
            WHERE skill_id = ? AND status = 'active'
            """,
            (actor_member_id, reason, revoked_at, revoked_at, skill_id),
        )
        return int(rowcount or 0)

    async def insert_eval_binding(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_eval_bindings (
              binding_id, organization_id, skill_id, bundle_id, bundle_revision,
              manifest_hash, eval_run_id, capability_scope_json, risk_level,
              status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["binding_id"],
                data["organization_id"],
                data["skill_id"],
                data["bundle_id"],
                data["bundle_revision"],
                data["manifest_hash"],
                data["eval_run_id"],
                _json(data.get("capability_scope", {})),
                data["risk_level"],
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_eval_bindings(self, skill_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM skill_eval_bindings
            WHERE skill_id = ?
            ORDER BY created_at DESC
            """,
            (skill_id,),
        )
        return [_eval_binding_from_row(dict(row)) for row in rows]

    async def insert_rollback_point(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_rollback_points (
              rollback_point_id, organization_id, skill_id, bundle_id, from_revision,
              manifest_hash, skill_snapshot_json, bundle_snapshot_json, reason,
              created_by_member_id, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["rollback_point_id"],
                data["organization_id"],
                data["skill_id"],
                data["bundle_id"],
                data["from_revision"],
                data["manifest_hash"],
                _json(data.get("skill_snapshot", {})),
                _json(data.get("bundle_snapshot", {})),
                data.get("reason"),
                data.get("created_by_member_id"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def latest_rollback_point(self, skill_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_rollback_points
            WHERE skill_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (skill_id,),
        )
        return _rollback_from_row(dict(row)) if row else None

    async def get_rollback_point(self, rollback_point_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM skill_rollback_points WHERE rollback_point_id = ?",
            (rollback_point_id,),
        )
        return _rollback_from_row(dict(row)) if row else None

    async def insert_output_taint(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_output_taint_records (
              taint_record_id, organization_id, skill_id, bundle_id, skill_run_id,
              task_id, taint_source, output_hash, output_preview,
              untrusted_external_content, dlp_findings_json, redaction_summary_json,
              guard_decision, policy_snapshot_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["taint_record_id"],
                data["organization_id"],
                data["skill_id"],
                data["bundle_id"],
                data.get("skill_run_id"),
                data.get("task_id"),
                data["taint_source"],
                data["output_hash"],
                data.get("output_preview"),
                1 if data.get("untrusted_external_content", True) else 0,
                _json(data.get("dlp_findings", [])),
                _json(data.get("redaction_summary", {})),
                data["guard_decision"],
                _json(data.get("policy_snapshot", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_output_taints(self, skill_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM skill_output_taint_records
            WHERE skill_id = ?
            ORDER BY created_at DESC
            """,
            (skill_id,),
        )
        return [_taint_from_row(dict(row)) for row in rows]


def _source_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _version_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["permission_summary"] = json.loads(row.pop("permission_summary_json") or "{}")
    row["risk_summary"] = json.loads(row.pop("risk_summary_json") or "{}")
    row["manifest_redacted"] = json.loads(row.pop("manifest_redacted_json") or "{}")
    return row


def _preview_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["permission_summary"] = json.loads(row.pop("permission_summary_json") or "{}")
    row["blocked_reasons"] = json.loads(row.pop("blocked_reasons_json") or "[]")
    row["requires_user_grant"] = bool(row["requires_user_grant"])
    row["unattended_allowed"] = bool(row["unattended_allowed"])
    return row


def _analysis_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    row["blocked_reasons"] = json.loads(row.pop("blocked_reasons_json") or "[]")
    row["warnings"] = json.loads(row.pop("warnings_json") or "[]")
    row["remediation_hints"] = json.loads(row.pop("remediation_hints_json") or "[]")
    row["sensitive_findings"] = json.loads(row.pop("sensitive_findings_json") or "[]")
    row["manifest_summary"] = json.loads(row.pop("manifest_summary_json") or "{}")
    return row


def _grant_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allowed_tools"] = json.loads(row.pop("allowed_tools_json") or "[]")
    row["allowed_asset_actions"] = json.loads(row.pop("allowed_asset_actions_json") or "[]")
    row["allowed_mcp_tools"] = json.loads(row.pop("allowed_mcp_tools_json") or "[]")
    row["denied_actions"] = json.loads(row.pop("denied_actions_json") or "[]")
    row["approval_policy"] = json.loads(row.pop("approval_policy_json") or "{}")
    return row


def _eval_binding_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["capability_scope"] = json.loads(row.pop("capability_scope_json") or "{}")
    return row


def _rollback_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["skill_snapshot"] = json.loads(row.pop("skill_snapshot_json") or "{}")
    row["bundle_snapshot"] = json.loads(row.pop("bundle_snapshot_json") or "{}")
    return row


def _taint_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["untrusted_external_content"] = bool(row["untrusted_external_content"])
    row["dlp_findings"] = json.loads(row.pop("dlp_findings_json") or "[]")
    row["redaction_summary"] = json.loads(row.pop("redaction_summary_json") or "{}")
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json") or "{}")
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
