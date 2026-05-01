from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.db.session import Database

TASK_UPDATE_COLUMNS = {
    "title",
    "goal",
    "status",
    "schedule",
    "schedule_json",
    "execution_policy",
    "execution_policy_json",
    "constraints",
    "constraints_json",
    "next_run_at",
    "last_run_at",
    "consecutive_failure_count",
    "max_consecutive_failures",
    "dead_letter_reason",
    "archived_at",
    "cancelled_at",
    "updated_at",
}

RUN_UPDATE_COLUMNS = {
    "task_id",
    "trace_id",
    "started_at",
    "completed_at",
    "status",
    "failure_reason",
    "missed_reason",
    "policy_decision",
    "policy_decision_json",
    "result",
    "result_json",
    "updated_at",
}


class ScheduledTaskRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._db.transaction():
            yield

    async def insert_task(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO scheduled_tasks (
              scheduled_task_id, organization_id, conversation_id, owner_member_id,
              title, goal, status, schedule_json, execution_policy_json,
              constraints_json, next_run_at, last_run_at, consecutive_failure_count,
              max_consecutive_failures, dead_letter_reason, created_by_member_id,
              trace_id, archived_at, cancelled_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["scheduled_task_id"],
                data["organization_id"],
                data.get("conversation_id"),
                data["owner_member_id"],
                data["title"],
                data["goal"],
                data["status"],
                _json(data.get("schedule", {})),
                _json(data.get("execution_policy", {})),
                _json(data.get("constraints", {})),
                data.get("next_run_at"),
                data.get("last_run_at"),
                data.get("consecutive_failure_count", 0),
                data.get("max_consecutive_failures", 3),
                data.get("dead_letter_reason"),
                data.get("created_by_member_id"),
                data.get("trace_id"),
                data.get("archived_at"),
                data.get("cancelled_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_task(self, scheduled_task_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in TASK_UPDATE_COLUMNS},
            {
                "schedule": "schedule_json",
                "execution_policy": "execution_policy_json",
                "constraints": "constraints_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE scheduled_tasks SET {assignments} WHERE scheduled_task_id = ?",
            (*values.values(), scheduled_task_id),
        )

    async def get_task(self, scheduled_task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM scheduled_tasks WHERE scheduled_task_id = ?",
            (scheduled_task_id,),
        )
        return _task_from_row(dict(row)) if row else None

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        owner_member_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        else:
            where.append("status != 'archived'")
        if owner_member_id:
            where.append("owner_member_id = ?")
            params.append(owner_member_id)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM scheduled_tasks
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_task_from_row(dict(row)) for row in rows]

    async def due_tasks(self, *, now: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM scheduled_tasks
            WHERE organization_id = 'org_default'
              AND status = 'active'
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            ORDER BY next_run_at ASC, created_at ASC
            LIMIT ?
            """,
            (now, limit),
        )
        return [_task_from_row(dict(row)) for row in rows]

    async def insert_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO scheduled_task_runs (
              run_id, scheduled_task_id, organization_id, task_id, trace_id,
              trigger_type, idempotency_key, scheduled_for, started_at, completed_at,
              status, failure_reason, missed_reason, policy_decision_json,
              result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["run_id"],
                data["scheduled_task_id"],
                data["organization_id"],
                data.get("task_id"),
                data.get("trace_id"),
                data["trigger_type"],
                data["idempotency_key"],
                data["scheduled_for"],
                data.get("started_at"),
                data.get("completed_at"),
                data["status"],
                data.get("failure_reason"),
                data.get("missed_reason"),
                _json(data.get("policy_decision", {})),
                _json(data.get("result", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in RUN_UPDATE_COLUMNS},
            {
                "policy_decision": "policy_decision_json",
                "result": "result_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE scheduled_task_runs SET {assignments} WHERE run_id = ?",
            (*values.values(), run_id),
        )

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM scheduled_task_runs WHERE run_id = ?",
            (run_id,),
        )
        return _run_from_row(dict(row)) if row else None

    async def get_run_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM scheduled_task_runs WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return _run_from_row(dict(row)) if row else None

    async def recover_stale_runs(self, *, stale_before: str, updated_at: str) -> int:
        return await self._db.execute(
            """
            UPDATE scheduled_task_runs
            SET status = 'failed',
                failure_reason = 'worker_recovered_stale_scheduled_run',
                completed_at = ?,
                updated_at = ?
            WHERE status IN ('created', 'running')
              AND started_at IS NOT NULL
              AND started_at < ?
            """,
            (updated_at, updated_at, stale_before),
        )

    async def list_runs(self, scheduled_task_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM scheduled_task_runs
            WHERE scheduled_task_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (scheduled_task_id, limit),
        )
        return [_run_from_row(dict(row)) for row in rows]

    async def insert_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO scheduled_task_events (
              event_id, scheduled_task_id, organization_id, run_id, event_type,
              payload_json, payload_redacted_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["scheduled_task_id"],
                data["organization_id"],
                data.get("run_id"),
                data["event_type"],
                _json(data.get("payload", {})),
                _json(data.get("payload_redacted", data.get("payload", {}))),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_events(self, scheduled_task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM scheduled_task_events
            WHERE scheduled_task_id = ?
            ORDER BY created_at ASC
            """,
            (scheduled_task_id,),
        )
        return [_event_from_row(dict(row)) for row in rows]


def _task_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["schedule"] = json.loads(row.pop("schedule_json") or "{}")
    row["execution_policy"] = json.loads(row.pop("execution_policy_json") or "{}")
    row["constraints"] = json.loads(row.pop("constraints_json") or "{}")
    return row


def _run_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["policy_decision"] = json.loads(row.pop("policy_decision_json") or "{}")
    row["result"] = json.loads(row.pop("result_json") or "{}")
    return row


def _event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row.pop("payload_json", None)
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
