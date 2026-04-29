from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

ASSET_UPDATE_COLUMNS = {
    "display_name",
    "provider",
    "status",
    "sensitivity",
    "config_json",
    "secret_ref",
    "expires_at",
    "last_verified_at",
    "owner_scope_type",
    "owner_scope_id",
    "visibility",
    "risk_level",
    "summary_text",
    "capabilities_json",
    "policy_json",
    "metadata_json",
    "archived_at",
    "updated_at",
}

EDGE_UPDATE_COLUMNS = {
    "effect",
    "risk_level",
    "approval_policy_json",
    "condition_json",
    "priority",
    "status",
    "valid_from",
    "valid_to",
    "updated_at",
}


class AssetRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_asset(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO assets (
              asset_id, organization_id, asset_type, display_name, provider, status,
              sensitivity, config_json, secret_ref, expires_at, last_verified_at,
              owner_scope_type, owner_scope_id, visibility, risk_level, summary_text,
              capabilities_json, policy_json, metadata_json, archived_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["asset_id"],
                data["organization_id"],
                data["asset_type"],
                data["display_name"],
                data.get("provider"),
                data["status"],
                data["sensitivity"],
                _json(data.get("config", {})),
                data.get("secret_ref"),
                data.get("expires_at"),
                data.get("last_verified_at"),
                data.get("owner_scope_type", "member"),
                data.get("owner_scope_id"),
                data.get("visibility", "private"),
                data.get("risk_level", "R1"),
                data.get("summary_text"),
                _json(data.get("capabilities", [])),
                _json(data.get("policy", {})),
                _json(data.get("metadata", {})),
                data.get("archived_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_assets(
        self,
        *,
        organization_id: str | None = None,
        asset_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if organization_id:
            where.append("organization_id = ?")
            params.append(organization_id)
        if asset_type:
            where.append("asset_type = ?")
            params.append(asset_type)
        if status:
            where.append("status = ?")
            params.append(status)
        else:
            where.append("status != 'deleted'")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM assets
            {clause}
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_asset_from_row(dict(row)) for row in rows]

    async def get_asset(self, asset_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM assets WHERE asset_id = ?", (asset_id,))
        return _asset_from_row(dict(row)) if row else None

    async def update_asset(self, asset_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sql_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "config":
                sql_fields["config_json"] = _json(value)
            elif key == "capabilities":
                sql_fields["capabilities_json"] = _json(value)
            elif key == "policy":
                sql_fields["policy_json"] = _json(value)
            elif key == "metadata":
                sql_fields["metadata_json"] = _json(value)
            else:
                sql_fields[key] = value
        unsupported = set(sql_fields) - ASSET_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported assets update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in sql_fields)
        await self._db.execute(
            f"UPDATE assets SET {assignments} WHERE asset_id = ?",
            (*sql_fields.values(), asset_id),
        )

    async def upsert_secret_ref(
        self,
        *,
        secret_ref: str,
        organization_id: str,
        kind: str,
        label: str,
        storage_uri: str,
        secret_type: str,
        provider: str,
        metadata: dict[str, Any],
        now: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO secret_refs (
              secret_ref, kind, label, storage_uri, organization_id, ref_uri,
              secret_type, provider, status, metadata_json, created_at, updated_at, rotated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, NULL)
            ON CONFLICT(secret_ref) DO UPDATE SET
              label = excluded.label,
              storage_uri = excluded.storage_uri,
              organization_id = excluded.organization_id,
              ref_uri = excluded.ref_uri,
              secret_type = excluded.secret_type,
              provider = excluded.provider,
              metadata_json = excluded.metadata_json,
              status = 'active',
              updated_at = excluded.updated_at,
              rotated_at = excluded.updated_at
            """,
            (
                secret_ref,
                kind,
                label,
                storage_uri,
                organization_id,
                storage_uri,
                secret_type,
                provider,
                _json(metadata),
                now,
                now,
            ),
        )

    async def insert_policy(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO asset_policies (
              policy_id, organization_id, asset_id, policy_type, action, effect, risk_level,
              approval_policy_json, condition_json, priority, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["policy_id"],
                data["organization_id"],
                data["asset_id"],
                data["policy_type"],
                data["action"],
                data["effect"],
                data["risk_level"],
                _json(data.get("approval_policy", {})),
                _json(data.get("condition", {})),
                data.get("priority", 0),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_policies(
        self,
        *,
        asset_id: str,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [asset_id]
        action_clause = ""
        if action:
            action_clause = "AND action IN (?, '*')"
            params.append(action)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM asset_policies
            WHERE asset_id = ?
              AND status = 'active'
              {action_clause}
            ORDER BY priority DESC, created_at ASC
            """,
            params,
        )
        return [_policy_from_row(dict(row)) for row in rows]

    async def insert_capability_edge(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO capability_edges (
              edge_id, organization_id, subject_type, subject_id, object_type, object_id,
              action, effect, risk_level, approval_policy_json, condition_json,
              source_type, source_id, priority, status, valid_from, valid_to,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["edge_id"],
                data["organization_id"],
                data["subject_type"],
                data["subject_id"],
                data["object_type"],
                data["object_id"],
                data["action"],
                data["effect"],
                data["risk_level"],
                _json(data.get("approval_policy", {})),
                _json(data.get("condition", {})),
                data["source_type"],
                data.get("source_id"),
                data.get("priority", 0),
                data["status"],
                data.get("valid_from"),
                data.get("valid_to"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_capability_edges(
        self,
        *,
        organization_id: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        action: str | None = None,
        include_inactive: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if organization_id:
            where.append("organization_id = ?")
            params.append(organization_id)
        if subject_type:
            where.append("subject_type = ?")
            params.append(subject_type)
        if subject_id:
            where.append("subject_id = ?")
            params.append(subject_id)
        if object_type:
            where.append("object_type = ?")
            params.append(object_type)
        if object_id:
            where.append("object_id = ?")
            params.append(object_id)
        if action:
            where.append("action IN (?, '*')")
            params.append(action)
        if not include_inactive:
            where.append("status = 'active'")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM capability_edges
            {clause}
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_edge_from_row(dict(row)) for row in rows]

    async def get_capability_edge(self, edge_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM capability_edges WHERE edge_id = ?",
            (edge_id,),
        )
        return _edge_from_row(dict(row)) if row else None

    async def update_capability_edge(self, edge_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sql_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "approval_policy":
                sql_fields["approval_policy_json"] = _json(value)
            elif key == "condition":
                sql_fields["condition_json"] = _json(value)
            else:
                sql_fields[key] = value
        unsupported = set(sql_fields) - EDGE_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported capability_edges update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in sql_fields)
        await self._db.execute(
            f"UPDATE capability_edges SET {assignments} WHERE edge_id = ?",
            (*sql_fields.values(), edge_id),
        )

    async def insert_decision_log(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO capability_decision_logs (
              decision_id, organization_id, trace_id, subject_type, subject_id,
              object_type, object_id, action, context_hash, decision, risk_level,
              approval_required, reason, policy_sources_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["decision_id"],
                data["organization_id"],
                data.get("trace_id"),
                data["subject_type"],
                data["subject_id"],
                data["object_type"],
                data["object_id"],
                data["action"],
                data["context_hash"],
                data["decision"],
                data["risk_level"],
                1 if data.get("approval_required") else 0,
                data["reason"],
                _json(data.get("policy_sources", [])),
                data["created_at"],
            ),
        )

    async def insert_handle(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO asset_handles (
              handle_id, organization_id, asset_id, subject_type, subject_id,
              conversation_id, task_id, allowed_actions_json, blocked_actions_json,
              approval_required_actions_json, risk_level, summary_text, policy_sources_json,
              status, issued_at, expires_at, revoked_at, trace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                data["handle_id"],
                data["organization_id"],
                data["asset_id"],
                data["subject_type"],
                data["subject_id"],
                data.get("conversation_id"),
                data.get("task_id"),
                _json(data.get("allowed_actions", [])),
                _json(data.get("blocked_actions", [])),
                _json(data.get("approval_required_actions", [])),
                data["risk_level"],
                data["summary_text"],
                _json(data.get("policy_sources", [])),
                data["status"],
                data["issued_at"],
                data["expires_at"],
                data.get("trace_id"),
            ),
        )

    async def get_handle(self, handle_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT ah.*, a.asset_type
            FROM asset_handles ah
            JOIN assets a ON a.asset_id = ah.asset_id
            WHERE ah.handle_id = ?
            """,
            (handle_id,),
        )
        return _handle_from_row(dict(row)) if row else None

    async def list_active_handles(
        self,
        *,
        asset_id: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where = ["ah.status = 'active'"]
        params: list[Any] = []
        if asset_id:
            where.append("ah.asset_id = ?")
            params.append(asset_id)
        if subject_type:
            where.append("ah.subject_type = ?")
            params.append(subject_type)
        if subject_id:
            where.append("ah.subject_id = ?")
            params.append(subject_id)
        rows = await self._db.fetch_all(
            f"""
            SELECT ah.*, a.asset_type
            FROM asset_handles ah
            JOIN assets a ON a.asset_id = ah.asset_id
            WHERE {' AND '.join(where)}
            ORDER BY ah.issued_at ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_handle_from_row(dict(row)) for row in rows]

    async def update_handle_status(
        self,
        handle_id: str,
        *,
        status: str,
        revoked_at: str | None = None,
    ) -> None:
        await self._db.execute(
            "UPDATE asset_handles SET status = ?, revoked_at = ? WHERE handle_id = ?",
            (status, revoked_at, handle_id),
        )

    async def revoke_handles_for_asset(self, asset_id: str, *, revoked_at: str) -> list[str]:
        rows = await self._db.fetch_all(
            """
            SELECT handle_id
            FROM asset_handles
            WHERE asset_id = ?
              AND status = 'active'
            """,
            (asset_id,),
        )
        handle_ids = [str(row["handle_id"]) for row in rows]
        for handle_id in handle_ids:
            await self.update_handle_status(handle_id, status="revoked", revoked_at=revoked_at)
        return handle_ids

    async def insert_handle_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO asset_handle_events (
              event_id, organization_id, handle_id, event_type, reason, actor_type, actor_id,
              trace_id, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["organization_id"],
                data["handle_id"],
                data["event_type"],
                data.get("reason"),
                data.get("actor_type"),
                data.get("actor_id"),
                data.get("trace_id"),
                _json(data.get("metadata", {})),
                data["created_at"],
            ),
        )

    async def list_handle_events(self, handle_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM asset_handle_events
            WHERE handle_id = ?
            ORDER BY created_at ASC
            """,
            (handle_id,),
        )
        return [_handle_event_from_row(dict(row)) for row in rows]


def _asset_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["config"] = json.loads(row.pop("config_json") or "{}")
    row["capabilities"] = json.loads(row.pop("capabilities_json") or "[]")
    row["policy"] = json.loads(row.pop("policy_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    row["has_secret"] = bool(row.get("secret_ref"))
    return row


def _policy_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["approval_policy"] = json.loads(row.pop("approval_policy_json") or "{}")
    row["condition"] = json.loads(row.pop("condition_json") or "{}")
    return row


def _edge_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["approval_policy"] = json.loads(row.pop("approval_policy_json") or "{}")
    row["condition"] = json.loads(row.pop("condition_json") or "{}")
    return row


def _handle_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allowed_actions"] = json.loads(row.pop("allowed_actions_json") or "[]")
    row["blocked_actions"] = json.loads(row.pop("blocked_actions_json") or "[]")
    row["approval_required_actions"] = json.loads(
        row.pop("approval_required_actions_json") or "[]"
    )
    row["policy_sources"] = json.loads(row.pop("policy_sources_json") or "[]")
    row["summary"] = row["summary_text"]
    return row


def _handle_event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
