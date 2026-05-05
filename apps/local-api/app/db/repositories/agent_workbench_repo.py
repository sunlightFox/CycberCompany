from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class AgentWorkbenchRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_job(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO agent_workbench_jobs (
              job_id, organization_id, turn_id, idempotency_key, job_type, status,
              attempts, max_attempts, next_run_at, locked_by, locked_at, payload_json,
              error_code, error_message, trace_id, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
              payload_json = excluded.payload_json,
              trace_id = COALESCE(excluded.trace_id, agent_workbench_jobs.trace_id),
              updated_at = excluded.updated_at
            """,
            (
                data["job_id"],
                data.get("organization_id", "org_default"),
                data.get("turn_id"),
                data["idempotency_key"],
                data["job_type"],
                data["status"],
                int(data.get("attempts", 0)),
                int(data.get("max_attempts", 3)),
                data.get("next_run_at"),
                data.get("locked_by"),
                data.get("locked_at"),
                _json(data.get("payload", {})),
                data.get("error_code"),
                data.get("error_message"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("completed_at"),
            ),
        )

    async def get_job_by_idempotency_key(self, idempotency_key: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM agent_workbench_jobs WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return _job_from_row(dict(row)) if row else None

    async def list_jobs(
        self,
        *,
        status: str | None = None,
        job_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if job_type:
            where.append("job_type = ?")
            params.append(job_type)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM agent_workbench_jobs
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_job_from_row(dict(row)) for row in rows]

    async def claim_next_job(self, *, worker_id: str, now: str) -> dict[str, Any] | None:
        async with self._db.transaction():
            row = await self._db.fetch_one(
                """
                SELECT *
                FROM agent_workbench_jobs
                WHERE status = 'pending'
                  AND (next_run_at IS NULL OR next_run_at <= ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            )
            if row is None:
                return None
            data = dict(row)
            await self._db.execute(
                """
                UPDATE agent_workbench_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    locked_by = ?,
                    locked_at = ?,
                    updated_at = ?
                WHERE job_id = ? AND status = 'pending'
                """,
                (worker_id, now, now, data["job_id"]),
            )
        claimed = await self._db.fetch_one(
            "SELECT * FROM agent_workbench_jobs WHERE job_id = ?",
            (data["job_id"],),
        )
        return _job_from_row(dict(claimed)) if claimed else None

    async def update_job_status(
        self,
        *,
        idempotency_key: str,
        status: str,
        updated_at: str,
        error_code: str | None = None,
        error_message: str | None = None,
        completed_at: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        await self._db.execute(
            """
            UPDATE agent_workbench_jobs
            SET status = ?,
                locked_by = NULL,
                locked_at = NULL,
                error_code = ?,
                error_message = ?,
                completed_at = ?,
                trace_id = COALESCE(?, trace_id),
                updated_at = ?
            WHERE idempotency_key = ?
            """,
            (
                status,
                error_code,
                error_message,
                completed_at,
                trace_id,
                updated_at,
                idempotency_key,
            ),
        )

    async def restore_stale_jobs(self, *, stale_before: str, updated_at: str) -> int:
        return await self._db.execute(
            """
            UPDATE agent_workbench_jobs
            SET status = 'pending',
                locked_by = NULL,
                locked_at = NULL,
                updated_at = ?
            WHERE status = 'running' AND locked_at IS NOT NULL AND locked_at < ?
            """,
            (updated_at, stale_before),
        )

    async def insert_context_pack(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO agent_workbench_context_packs (
              context_pack_id, organization_id, member_id, conversation_id, turn_id,
              summary_text, memory_refs_json, skill_refs_json, context_file_refs_json,
              working_state_json, source_refs_json, token_estimate, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["context_pack_id"],
                data.get("organization_id", "org_default"),
                data["member_id"],
                data.get("conversation_id"),
                data.get("turn_id"),
                data["summary_text"],
                _json(data.get("memory_refs", [])),
                _json(data.get("skill_refs", [])),
                _json(data.get("context_file_refs", [])),
                _json(data.get("working_state", {})),
                _json(data.get("source_refs", [])),
                int(data.get("token_estimate", 0)),
                data.get("status", "active"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def latest_context_pack(
        self,
        *,
        member_id: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        if conversation_id is not None:
            row = await self._db.fetch_one(
                """
                SELECT *
                FROM agent_workbench_context_packs
                WHERE member_id = ? AND conversation_id = ? AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (member_id, conversation_id),
            )
        else:
            row = await self._db.fetch_one(
                """
                SELECT *
                FROM agent_workbench_context_packs
                WHERE member_id = ? AND status = 'active'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (member_id,),
            )
        return _context_pack_from_row(dict(row)) if row else None

    async def insert_context_file_version(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO agent_context_file_versions (
              version_id, organization_id, member_id, conversation_id, context_file_key,
              version_index, status, summary_text, artifact_uri, artifact_checksum,
              artifact_size_bytes, source_turn_id, source_trace_id, context_pack_id,
              diff_base_version_id, source_refs_json, memory_refs_json, skill_refs_json,
              metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["version_id"],
                data.get("organization_id", "org_default"),
                data["member_id"],
                data.get("conversation_id"),
                data["context_file_key"],
                int(data["version_index"]),
                data.get("status", "active"),
                data["summary_text"],
                data["artifact_uri"],
                data["artifact_checksum"],
                int(data.get("artifact_size_bytes", 0)),
                data.get("source_turn_id"),
                data.get("source_trace_id"),
                data.get("context_pack_id"),
                data.get("diff_base_version_id"),
                _json(data.get("source_refs", [])),
                _json(data.get("memory_refs", [])),
                _json(data.get("skill_refs", [])),
                _json(data.get("metadata", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def link_context_file_pack(
        self,
        *,
        version_id: str,
        context_pack_id: str,
        updated_at: str,
    ) -> None:
        await self._db.execute(
            """
            UPDATE agent_context_file_versions
            SET context_pack_id = ?, updated_at = ?
            WHERE version_id = ?
            """,
            (context_pack_id, updated_at, version_id),
        )

    async def latest_context_file_version(
        self,
        *,
        member_id: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        where = "member_id = ? AND status = 'active'"
        params: list[Any] = [member_id]
        if conversation_id is not None:
            where += " AND conversation_id = ?"
            params.append(conversation_id)
        row = await self._db.fetch_one(
            f"""
            SELECT *
            FROM agent_context_file_versions
            WHERE {where}
            ORDER BY version_index DESC, created_at DESC
            LIMIT 1
            """,
            tuple(params),
        )
        return _context_file_from_row(dict(row)) if row else None

    async def get_context_file_version(self, version_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM agent_context_file_versions WHERE version_id = ?",
            (version_id,),
        )
        return _context_file_from_row(dict(row)) if row else None

    async def list_context_file_versions(
        self,
        *,
        member_id: str | None = None,
        conversation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if member_id:
            where.append("member_id = ?")
            params.append(member_id)
        if conversation_id:
            where.append("conversation_id = ?")
            params.append(conversation_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM agent_context_file_versions
            {clause}
            ORDER BY version_index DESC, created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_context_file_from_row(dict(row)) for row in rows]

    async def next_context_file_version_index(self, context_file_key: str) -> int:
        row = await self._db.fetch_one(
            """
            SELECT COALESCE(MAX(version_index), 0) AS max_version
            FROM agent_context_file_versions
            WHERE context_file_key = ?
            """,
            (context_file_key,),
        )
        return int(row["max_version"] if row else 0) + 1


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _job_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = _json_load(row.pop("payload_json", "{}"), {})
    return row


def _context_pack_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["memory_refs"] = _json_load(row.pop("memory_refs_json", "[]"), [])
    row["skill_refs"] = _json_load(row.pop("skill_refs_json", "[]"), [])
    row["context_file_refs"] = _json_load(row.pop("context_file_refs_json", "[]"), [])
    row["working_state"] = _json_load(row.pop("working_state_json", "{}"), {})
    row["source_refs"] = _json_load(row.pop("source_refs_json", "[]"), [])
    return row


def _context_file_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["source_refs"] = _json_load(row.pop("source_refs_json", "[]"), [])
    row["memory_refs"] = _json_load(row.pop("memory_refs_json", "[]"), [])
    row["skill_refs"] = _json_load(row.pop("skill_refs_json", "[]"), [])
    row["metadata"] = _json_load(row.pop("metadata_json", "{}"), {})
    return row
