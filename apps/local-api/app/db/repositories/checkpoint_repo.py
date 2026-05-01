from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class CheckpointRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_checkpoint(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_checkpoints (
              checkpoint_id, organization_id, task_id, step_id, tool_call_id,
              checkpoint_type, scope, status, item_count, size_bytes, restorable,
              policy_snapshot_json, metadata_json, failure_reason, expires_at, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["checkpoint_id"],
                data["organization_id"],
                data["task_id"],
                data.get("step_id"),
                data.get("tool_call_id"),
                data["checkpoint_type"],
                data["scope"],
                data["status"],
                data.get("item_count", 0),
                data.get("size_bytes", 0),
                1 if data.get("restorable", True) else 0,
                _json(data.get("policy_snapshot", {})),
                _json(data.get("metadata", {})),
                data.get("failure_reason"),
                data.get("expires_at"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_checkpoint(self, checkpoint_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "policy_snapshot": "policy_snapshot_json",
                "metadata": "metadata_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE task_checkpoints SET {assignments} WHERE checkpoint_id = ?",
            (*values.values(), checkpoint_id),
        )

    async def get_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM task_checkpoints WHERE checkpoint_id = ?",
            (checkpoint_id,),
        )
        return _checkpoint_from_row(dict(row)) if row else None

    async def list_checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM task_checkpoints
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_checkpoint_from_row(dict(row)) for row in rows]

    async def expire_due_checkpoints(self, *, now: str, updated_at: str, limit: int = 100) -> int:
        rows = await self._db.fetch_all(
            """
            SELECT checkpoint_id
            FROM task_checkpoints
            WHERE expires_at IS NOT NULL
              AND expires_at <= ?
              AND status IN ('ready', 'partial', 'rolled_back')
            ORDER BY expires_at ASC
            LIMIT ?
            """,
            (now, limit),
        )
        for row in rows:
            await self.update_checkpoint(
                str(row["checkpoint_id"]),
                {
                    "status": "expired",
                    "failure_reason": "checkpoint_ttl_expired",
                    "updated_at": updated_at,
                },
            )
        return len(rows)

    async def insert_item(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO checkpoint_items (
              checkpoint_item_id, checkpoint_id, organization_id, task_id, target_uri,
              target_path_redacted, item_type, exists_before, before_checksum,
              before_size_bytes, after_exists, after_checksum, after_size_bytes,
              snapshot_artifact_id, snapshot_uri, content_type, sensitivity, restorable,
              metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["checkpoint_item_id"],
                data["checkpoint_id"],
                data["organization_id"],
                data["task_id"],
                data["target_uri"],
                data["target_path_redacted"],
                data["item_type"],
                1 if data.get("exists_before") else 0,
                data.get("before_checksum"),
                data.get("before_size_bytes", 0),
                _bool_or_none(data.get("after_exists")),
                data.get("after_checksum"),
                data.get("after_size_bytes"),
                data.get("snapshot_artifact_id"),
                data.get("snapshot_uri"),
                data.get("content_type"),
                data.get("sensitivity", "low"),
                1 if data.get("restorable", True) else 0,
                _json(data.get("metadata", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_item(self, checkpoint_item_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(fields, {"metadata": "metadata_json"})
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE checkpoint_items SET {assignments} WHERE checkpoint_item_id = ?",
            (*values.values(), checkpoint_item_id),
        )

    async def list_items(self, checkpoint_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM checkpoint_items
            WHERE checkpoint_id = ?
            ORDER BY created_at ASC
            """,
            (checkpoint_id,),
        )
        return [_item_from_row(dict(row)) for row in rows]

    async def insert_rollback_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO rollback_events (
              rollback_id, organization_id, checkpoint_id, task_id, requested_by,
              reason, status, restored_items, skipped_items, conflict_items,
              policy_snapshot_json, trace_id, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["rollback_id"],
                data["organization_id"],
                data["checkpoint_id"],
                data["task_id"],
                data["requested_by"],
                data.get("reason"),
                data["status"],
                data.get("restored_items", 0),
                data.get("skipped_items", 0),
                data.get("conflict_items", 0),
                _json(data.get("policy_snapshot", {})),
                data.get("trace_id"),
                data["created_at"],
                data.get("completed_at"),
            ),
        )

    async def update_rollback_event(self, rollback_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(fields, {"policy_snapshot": "policy_snapshot_json"})
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE rollback_events SET {assignments} WHERE rollback_id = ?",
            (*values.values(), rollback_id),
        )

    async def list_rollback_events(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM rollback_events
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_rollback_event_from_row(dict(row)) for row in rows]

    async def get_rollback_event(self, rollback_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM rollback_events WHERE rollback_id = ?",
            (rollback_id,),
        )
        return _rollback_event_from_row(dict(row)) if row else None

    async def insert_rollback_item(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO rollback_items (
              rollback_item_id, rollback_id, checkpoint_item_id, organization_id, task_id,
              target_uri, action, status, reason, before_checksum, current_checksum,
              restored_checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["rollback_item_id"],
                data["rollback_id"],
                data["checkpoint_item_id"],
                data["organization_id"],
                data["task_id"],
                data["target_uri"],
                data["action"],
                data["status"],
                data.get("reason"),
                data.get("before_checksum"),
                data.get("current_checksum"),
                data.get("restored_checksum"),
                data["created_at"],
            ),
        )

    async def list_rollback_items(self, rollback_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM rollback_items
            WHERE rollback_id = ?
            ORDER BY created_at ASC
            """,
            (rollback_id,),
        )
        return [dict(row) for row in rows]


def _checkpoint_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["restorable"] = bool(row["restorable"])
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _item_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["exists_before"] = bool(row["exists_before"])
    row["after_exists"] = None if row["after_exists"] is None else bool(row["after_exists"])
    row["restorable"] = bool(row["restorable"])
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _rollback_event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json") or "{}")
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _bool_or_none(value: Any) -> int | None:
    return None if value is None else 1 if value else 0


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
