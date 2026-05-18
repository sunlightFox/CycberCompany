from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

TARGET_UPDATE_COLUMNS = {
    "display_name",
    "aliases_json",
    "supported_actions_json",
    "required_asset_types_json",
    "execution_modes_json",
    "risk_defaults_json",
    "status",
    "metadata_json",
    "trace_id",
    "updated_at",
}

INTENT_UPDATE_COLUMNS = {
    "platform_hint",
    "platform_key",
    "action_type",
    "content_redacted",
    "content_summary",
    "target_hint",
    "constraints_json",
    "confidence",
    "status",
    "missing_fields_json",
    "resolver_evidence_json",
    "trace_id",
    "updated_at",
}

PLAN_UPDATE_COLUMNS = {
    "task_id",
    "approval_id",
    "trace_id",
    "platform_key",
    "target_id",
    "selected_asset_id",
    "selected_handle_id",
    "execution_mode",
    "steps_json",
    "status",
    "risk_level",
    "content_summary",
    "failure_reason",
    "evidence_json",
    "metadata_json",
    "updated_at",
}


class ExternalPlatformRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_target(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_targets (
              target_id, organization_id, platform_key, display_name, aliases_json,
              supported_actions_json, required_asset_types_json, execution_modes_json,
              risk_defaults_json, status, metadata_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id, platform_key) DO UPDATE SET
              display_name = excluded.display_name,
              aliases_json = excluded.aliases_json,
              supported_actions_json = excluded.supported_actions_json,
              required_asset_types_json = excluded.required_asset_types_json,
              execution_modes_json = excluded.execution_modes_json,
              risk_defaults_json = excluded.risk_defaults_json,
              status = excluded.status,
              metadata_json = excluded.metadata_json,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["target_id"],
                data["organization_id"],
                data["platform_key"],
                data["display_name"],
                _json(data.get("aliases", [])),
                _json(data.get("supported_actions", [])),
                _json(data.get("required_asset_types", [])),
                _json(data.get("execution_modes", [])),
                _json(data.get("risk_defaults", {})),
                data.get("status", "active"),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_targets(
        self,
        *,
        organization_id: str = "org_default",
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if status:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM external_platform_targets
                WHERE organization_id = ? AND status = ?
                ORDER BY display_name ASC
                LIMIT ?
                """,
                (organization_id, status, limit),
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM external_platform_targets
                WHERE organization_id = ?
                ORDER BY display_name ASC
                LIMIT ?
                """,
                (organization_id, limit),
            )
        return [_target_from_row(dict(row)) for row in rows]

    async def get_target(self, target_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM external_platform_targets WHERE target_id = ?",
            (target_id,),
        )
        return _target_from_row(dict(row)) if row else None

    async def get_target_by_key(
        self,
        platform_key: str,
        *,
        organization_id: str = "org_default",
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM external_platform_targets
            WHERE organization_id = ? AND platform_key = ?
            """,
            (organization_id, platform_key),
        )
        return _target_from_row(dict(row)) if row else None

    async def insert_intent(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_action_intents (
              intent_id, organization_id, member_id, conversation_id, turn_id, trace_id,
              platform_hint, platform_key, action_type, content_redacted, content_summary,
              target_hint, constraints_json, confidence, status, missing_fields_json,
              resolver_evidence_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["intent_id"],
                data["organization_id"],
                data["member_id"],
                data.get("conversation_id"),
                data.get("turn_id"),
                data.get("trace_id"),
                data.get("platform_hint"),
                data.get("platform_key"),
                data["action_type"],
                data.get("content_redacted"),
                data.get("content_summary"),
                data.get("target_hint"),
                _json(data.get("constraints", {})),
                data.get("confidence", 0),
                data["status"],
                _json(data.get("missing_fields", [])),
                _json(data.get("resolver_evidence", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM external_platform_action_intents WHERE intent_id = ?",
            (intent_id,),
        )
        return _intent_from_row(dict(row)) if row else None

    async def update_intent(self, intent_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "external_platform_action_intents",
            "intent_id",
            intent_id,
            _json_update_fields(
                fields,
                {
                    "constraints": "constraints_json",
                    "missing_fields": "missing_fields_json",
                    "resolver_evidence": "resolver_evidence_json",
                },
            ),
            INTENT_UPDATE_COLUMNS,
        )

    async def insert_plan(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_action_plans (
              plan_id, intent_id, organization_id, member_id, conversation_id, task_id,
              approval_id, trace_id, platform_key, target_id, selected_asset_id,
              selected_handle_id, action_type, execution_mode, steps_json, status,
              risk_level, content_summary, failure_reason, evidence_json, metadata_json,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["plan_id"],
                data["intent_id"],
                data["organization_id"],
                data["member_id"],
                data.get("conversation_id"),
                data.get("task_id"),
                data.get("approval_id"),
                data.get("trace_id"),
                data.get("platform_key"),
                data.get("target_id"),
                data.get("selected_asset_id"),
                data.get("selected_handle_id"),
                data["action_type"],
                data["execution_mode"],
                _json(data.get("steps", [])),
                data["status"],
                data.get("risk_level", "R1"),
                data.get("content_summary"),
                data.get("failure_reason"),
                _json(data.get("evidence", {})),
                _json(data.get("metadata", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM external_platform_action_plans WHERE plan_id = ?",
            (plan_id,),
        )
        return _plan_from_row(dict(row)) if row else None

    async def get_plan_by_approval_id(self, approval_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM external_platform_action_plans
            WHERE approval_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (approval_id,),
        )
        return _plan_from_row(dict(row)) if row else None

    async def list_recent_plans(
        self,
        *,
        conversation_id: str | None = None,
        statuses: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if conversation_id is not None:
            where.append("conversation_id = ?")
            params.append(conversation_id)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM external_platform_action_plans
            {where_sql}
            ORDER BY updated_at DESC, created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_plan_from_row(dict(row)) for row in rows]

    async def update_plan(self, plan_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "external_platform_action_plans",
            "plan_id",
            plan_id,
            _json_update_fields(
                fields,
                {
                    "steps": "steps_json",
                    "evidence": "evidence_json",
                    "metadata": "metadata_json",
                },
            ),
            PLAN_UPDATE_COLUMNS,
        )

    async def insert_execution(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_executions (
              execution_id, plan_id, organization_id, member_id, executor, step_type,
              status, request_summary_json, response_summary_json, evidence_json,
              error_code, error_summary, latency_ms, trace_id, started_at, completed_at,
              created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["execution_id"],
                data["plan_id"],
                data["organization_id"],
                data["member_id"],
                data["executor"],
                data["step_type"],
                data["status"],
                _json(data.get("request_summary", {})),
                _json(data.get("response_summary", {})),
                _json(data.get("evidence", {})),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("latency_ms", 0),
                data.get("trace_id"),
                data["started_at"],
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def list_executions(self, plan_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM external_platform_executions
            WHERE plan_id = ?
            ORDER BY created_at ASC
            """,
            (plan_id,),
        )
        return [_execution_from_row(dict(row)) for row in rows]

    async def insert_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO external_platform_plan_events (
              event_id, plan_id, organization_id, event_type, payload_json,
              payload_redacted_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["plan_id"],
                data["organization_id"],
                data["event_type"],
                _json(data.get("payload", {})),
                _json(data.get("payload_redacted", data.get("payload", {}))),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_plan_events(self, plan_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM external_platform_plan_events
            WHERE plan_id = ?
            ORDER BY created_at ASC
            """,
            (plan_id,),
        )
        return [_event_from_row(dict(row)) for row in rows]

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


def _target_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["aliases"] = json.loads(row.pop("aliases_json") or "[]")
    row["supported_actions"] = json.loads(row.pop("supported_actions_json") or "[]")
    row["required_asset_types"] = json.loads(row.pop("required_asset_types_json") or "[]")
    row["execution_modes"] = json.loads(row.pop("execution_modes_json") or "[]")
    row["risk_defaults"] = json.loads(row.pop("risk_defaults_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _intent_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["constraints"] = json.loads(row.pop("constraints_json") or "{}")
    row["missing_fields"] = json.loads(row.pop("missing_fields_json") or "[]")
    row["resolver_evidence"] = json.loads(row.pop("resolver_evidence_json") or "{}")
    return row


def _plan_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["steps"] = json.loads(row.pop("steps_json") or "[]")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _execution_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["request_summary"] = json.loads(row.pop("request_summary_json") or "{}")
    row["response_summary"] = json.loads(row.pop("response_summary_json") or "{}")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_json") or "{}")
    row["payload_redacted"] = json.loads(row.pop("payload_redacted_json") or "{}")
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
