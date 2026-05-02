from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

ADAPTER_UPDATE_COLUMNS = {
    "display_name",
    "status",
    "supported_actions_json",
    "required_asset_types_json",
    "allowed_domains_json",
    "manifest_json",
    "metadata_json",
    "trace_id",
    "updated_at",
}

STEP_UPDATE_COLUMNS = {
    "status",
    "input_redacted_json",
    "evidence_json",
    "approval_id",
    "tool_call_id",
    "mcp_call_id",
    "trace_id",
    "updated_at",
}

EXECUTION_UPDATE_COLUMNS = {
    "status",
    "completed_at",
    "evidence_json",
    "error_code",
    "error_summary",
    "trace_id",
    "updated_at",
}


class ExternalPlatformAdapterRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_adapter(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_adapters (
              adapter_id, organization_id, platform_key, action_type, adapter_type,
              display_name, status, supported_actions_json, required_asset_types_json,
              allowed_domains_json, manifest_json, metadata_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id, platform_key, action_type, adapter_type, display_name)
            DO UPDATE SET
              status = excluded.status,
              supported_actions_json = excluded.supported_actions_json,
              required_asset_types_json = excluded.required_asset_types_json,
              allowed_domains_json = excluded.allowed_domains_json,
              manifest_json = excluded.manifest_json,
              metadata_json = excluded.metadata_json,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["adapter_id"],
                data["organization_id"],
                data["platform_key"],
                data["action_type"],
                data["adapter_type"],
                data["display_name"],
                data.get("status", "active"),
                _json(data.get("supported_actions", [])),
                _json(data.get("required_asset_types", [])),
                _json(data.get("allowed_domains", [])),
                _json(data.get("manifest", {})),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_adapter(self, adapter_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM external_platform_adapters WHERE adapter_id = ?",
            (adapter_id,),
        )
        return _adapter_from_row(dict(row)) if row else None

    async def get_adapter_by_key(
        self,
        *,
        organization_id: str,
        platform_key: str,
        action_type: str,
        adapter_type: str,
        display_name: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM external_platform_adapters
            WHERE organization_id = ?
              AND platform_key = ?
              AND action_type = ?
              AND adapter_type = ?
              AND display_name = ?
            """,
            (organization_id, platform_key, action_type, adapter_type, display_name),
        )
        return _adapter_from_row(dict(row)) if row else None

    async def update_adapter(self, adapter_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "external_platform_adapters",
            "adapter_id",
            adapter_id,
            _json_update_fields(
                fields,
                {
                    "supported_actions": "supported_actions_json",
                    "required_asset_types": "required_asset_types_json",
                    "allowed_domains": "allowed_domains_json",
                    "manifest": "manifest_json",
                    "metadata": "metadata_json",
                },
            ),
            ADAPTER_UPDATE_COLUMNS,
        )

    async def find_active_adapter(
        self,
        *,
        organization_id: str,
        platform_key: str,
        action_type: str,
        adapter_type: str | None,
    ) -> dict[str, Any] | None:
        if adapter_type:
            row = await self._db.fetch_one(
                """
                SELECT *
                FROM external_platform_adapters
                WHERE organization_id = ?
                  AND platform_key = ?
                  AND action_type = ?
                  AND adapter_type = ?
                  AND status IN ('active', 'test_only')
                ORDER BY
                  CASE status WHEN 'active' THEN 0 ELSE 1 END,
                  updated_at DESC
                LIMIT 1
                """,
                (organization_id, platform_key, action_type, adapter_type),
            )
        else:
            row = await self._db.fetch_one(
                """
                SELECT *
                FROM external_platform_adapters
                WHERE organization_id = ?
                  AND platform_key = ?
                  AND action_type = ?
                  AND status IN ('active', 'test_only')
                ORDER BY
                  CASE adapter_type WHEN 'browser' THEN 0 ELSE 1 END,
                  CASE status WHEN 'active' THEN 0 ELSE 1 END,
                  updated_at DESC
                LIMIT 1
                """,
                (organization_id, platform_key, action_type),
            )
        return _adapter_from_row(dict(row)) if row else None

    async def list_adapters(
        self,
        *,
        organization_id: str = "org_default",
        platform_key: str | None = None,
        adapter_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = ?"]
        params: list[Any] = [organization_id]
        if platform_key:
            where.append("platform_key = ?")
            params.append(platform_key)
        if adapter_type:
            where.append("adapter_type = ?")
            params.append(adapter_type)
        if status:
            where.append("status = ?")
            params.append(status)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM external_platform_adapters
            WHERE {' AND '.join(where)}
            ORDER BY platform_key ASC, action_type ASC, adapter_type ASC, display_name ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_adapter_from_row(dict(row)) for row in rows]

    async def upsert_version(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_adapter_versions (
              adapter_version_id, adapter_id, version, manifest_json, manifest_checksum,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(adapter_id, version) DO UPDATE SET
              manifest_json = excluded.manifest_json,
              manifest_checksum = excluded.manifest_checksum,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["adapter_version_id"],
                data["adapter_id"],
                data["version"],
                _json(data.get("manifest", {})),
                data["manifest_checksum"],
                data.get("status", "active"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_version(self, adapter_version_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM external_platform_adapter_versions
            WHERE adapter_version_id = ?
            """,
            (adapter_version_id,),
        )
        return _version_from_row(dict(row)) if row else None

    async def latest_version(self, adapter_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM external_platform_adapter_versions
            WHERE adapter_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (adapter_id,),
        )
        return _version_from_row(dict(row)) if row else None

    async def insert_step(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_adapter_steps (
              step_id, plan_id, adapter_id, adapter_version_id, step_name, executor,
              tool_name, risk_level, requires_approval, status, input_redacted_json,
              evidence_json, approval_id, tool_call_id, mcp_call_id, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["step_id"],
                data["plan_id"],
                data["adapter_id"],
                data["adapter_version_id"],
                data["step_name"],
                data["executor"],
                data.get("tool_name"),
                data.get("risk_level", "R1"),
                1 if data.get("requires_approval") else 0,
                data.get("status", "planned"),
                _json(data.get("input_redacted", {})),
                _json(data.get("evidence", {})),
                data.get("approval_id"),
                data.get("tool_call_id"),
                data.get("mcp_call_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def delete_steps_for_plan_adapter(self, plan_id: str, adapter_id: str) -> None:
        await self._db.execute(
            """
            DELETE FROM external_platform_adapter_steps
            WHERE plan_id = ? AND adapter_id = ?
            """,
            (plan_id, adapter_id),
        )

    async def update_step(self, step_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "external_platform_adapter_steps",
            "step_id",
            step_id,
            _json_update_fields(
                fields,
                {
                    "input_redacted": "input_redacted_json",
                    "evidence": "evidence_json",
                },
            ),
            STEP_UPDATE_COLUMNS,
        )

    async def list_steps(
        self,
        plan_id: str,
        *,
        adapter_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if adapter_id:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM external_platform_adapter_steps
                WHERE plan_id = ? AND adapter_id = ?
                ORDER BY created_at ASC
                """,
                (plan_id, adapter_id),
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM external_platform_adapter_steps
                WHERE plan_id = ?
                ORDER BY created_at ASC
                """,
                (plan_id,),
            )
        return [_step_from_row(dict(row)) for row in rows]

    async def insert_execution(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_adapter_executions (
              adapter_execution_id, plan_id, adapter_id, adapter_version_id, status,
              executor, started_at, completed_at, evidence_json, error_code,
              error_summary, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["adapter_execution_id"],
                data["plan_id"],
                data["adapter_id"],
                data["adapter_version_id"],
                data["status"],
                data["executor"],
                data["started_at"],
                data.get("completed_at"),
                _json(data.get("evidence", {})),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_execution(self, execution_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "external_platform_adapter_executions",
            "adapter_execution_id",
            execution_id,
            _json_update_fields(fields, {"evidence": "evidence_json"}),
            EXECUTION_UPDATE_COLUMNS,
        )

    async def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM external_platform_adapter_executions
            WHERE adapter_execution_id = ?
            """,
            (execution_id,),
        )
        return _execution_from_row(dict(row)) if row else None

    async def list_executions(self, plan_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM external_platform_adapter_executions
            WHERE plan_id = ?
            ORDER BY created_at ASC
            """,
            (plan_id,),
        )
        return [_execution_from_row(dict(row)) for row in rows]

    async def insert_drift_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_adapter_drift_events (
              drift_event_id, plan_id, adapter_id, step_id, drift_type, status,
              evidence_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["drift_event_id"],
                data["plan_id"],
                data["adapter_id"],
                data.get("step_id"),
                data["drift_type"],
                data["status"],
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_drift_events(self, plan_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM external_platform_adapter_drift_events
            WHERE plan_id = ?
            ORDER BY created_at ASC
            """,
            (plan_id,),
        )
        return [_drift_from_row(dict(row)) for row in rows]

    async def _update(
        self,
        table: str,
        key_column: str,
        key_value: str,
        fields: dict[str, Any],
        allowed: set[str],
    ) -> None:
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE {table} SET {assignments} WHERE {key_column} = ?",
            (*values.values(), key_value),
        )


def _adapter_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["supported_actions"] = json.loads(row.pop("supported_actions_json") or "[]")
    row["required_asset_types"] = json.loads(row.pop("required_asset_types_json") or "[]")
    row["allowed_domains"] = json.loads(row.pop("allowed_domains_json") or "[]")
    row["manifest"] = json.loads(row.pop("manifest_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _version_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["manifest"] = json.loads(row.pop("manifest_json") or "{}")
    return row


def _step_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["requires_approval"] = bool(row.get("requires_approval"))
    row["input_redacted"] = json.loads(row.pop("input_redacted_json") or "{}")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _execution_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _drift_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
