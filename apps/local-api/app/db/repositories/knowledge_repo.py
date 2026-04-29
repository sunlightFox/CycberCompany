from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class KnowledgeRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_source(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO knowledge_sources (
              source_id, organization_id, asset_id, source_type, source_uri, display_name,
              status, sensitivity, content_hash, last_scanned_at, last_indexed_at,
              metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["source_id"],
                data["organization_id"],
                data["asset_id"],
                data["source_type"],
                data["source_uri"],
                data["display_name"],
                data["status"],
                data["sensitivity"],
                data.get("content_hash"),
                data.get("last_scanned_at"),
                data.get("last_indexed_at"),
                _json(data.get("metadata", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_source(self, source_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM knowledge_sources WHERE source_id = ?",
            (source_id,),
        )
        return _source_from_row(dict(row)) if row else None

    async def update_source(self, source_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sql_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "metadata":
                sql_fields["metadata_json"] = _json(value)
            else:
                sql_fields[key] = value
        assignments = ", ".join(f"{column} = ?" for column in sql_fields)
        await self._db.execute(
            f"UPDATE knowledge_sources SET {assignments} WHERE source_id = ?",
            (*sql_fields.values(), source_id),
        )

    async def insert_index_job(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO knowledge_index_jobs (
              job_id, organization_id, asset_id, source_id, job_type, idempotency_key,
              status, attempt_count, max_attempts, next_run_at, error_code, error_summary,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
              status = excluded.status,
              attempt_count = knowledge_index_jobs.attempt_count + 1,
              error_code = excluded.error_code,
              error_summary = excluded.error_summary,
              updated_at = excluded.updated_at
            """,
            (
                data["job_id"],
                data["organization_id"],
                data["asset_id"],
                data.get("source_id"),
                data["job_type"],
                data["idempotency_key"],
                data["status"],
                data.get("attempt_count", 0),
                data.get("max_attempts", 3),
                data.get("next_run_at"),
                data.get("error_code"),
                data.get("error_summary"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def delete_chunks_for_source(self, source_id: str) -> None:
        rows = await self._db.fetch_all(
            "SELECT chunk_id FROM knowledge_chunks WHERE source_id = ?",
            (source_id,),
        )
        for row in rows:
            await self._db.execute(
                "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?",
                (row["chunk_id"],),
            )
        await self._db.execute(
            "DELETE FROM knowledge_vector_refs WHERE source_id = ?",
            (source_id,),
        )
        await self._db.execute("DELETE FROM knowledge_chunks WHERE source_id = ?", (source_id,))

    async def insert_chunk(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO knowledge_chunks (
              chunk_id, organization_id, asset_id, source_id, chunk_index, content_text,
              summary_text, token_estimate, sensitivity, content_hash, metadata_json,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["chunk_id"],
                data["organization_id"],
                data["asset_id"],
                data["source_id"],
                data["chunk_index"],
                data["content_text"],
                data.get("summary_text"),
                data.get("token_estimate"),
                data["sensitivity"],
                data["content_hash"],
                _json(data.get("metadata", {})),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )
        await self._db.execute(
            """
            INSERT INTO knowledge_chunks_fts (chunk_id, content_text, summary_text)
            VALUES (?, ?, ?)
            """,
            (data["chunk_id"], data["content_text"], data.get("summary_text")),
        )

    async def insert_vector_ref(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO knowledge_vector_refs (
              vector_ref_id, organization_id, asset_id, source_id, chunk_id,
              collection_name, vector_id, embedding_provider, embedding_model,
              content_hash, status, last_synced_at, error_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["vector_ref_id"],
                data["organization_id"],
                data["asset_id"],
                data["source_id"],
                data["chunk_id"],
                data["collection_name"],
                data["vector_id"],
                data["embedding_provider"],
                data["embedding_model"],
                data["content_hash"],
                data["status"],
                data.get("last_synced_at"),
                data.get("error_code"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def search_chunks(
        self,
        *,
        organization_id: str,
        asset_id: str | None,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [organization_id]
        asset_clause = ""
        if asset_id:
            asset_clause = "AND kc.asset_id = ?"
            params.append(asset_id)
        match_query = _fts_query(query)
        rows: list[Any] = []
        if match_query:
            rows = await self._db.fetch_all(
                f"""
                SELECT kc.*, ks.display_name AS source_display_name,
                       bm25(knowledge_chunks_fts) * -1 AS rank_score
                FROM knowledge_chunks_fts
                JOIN knowledge_chunks kc ON kc.chunk_id = knowledge_chunks_fts.chunk_id
                JOIN knowledge_sources ks ON ks.source_id = kc.source_id
                WHERE knowledge_chunks_fts MATCH ?
                  AND kc.organization_id = ?
                  AND kc.status = 'active'
                  {asset_clause}
                ORDER BY rank_score DESC, kc.chunk_index ASC
                LIMIT ?
                """,
                (match_query, *params, limit),
            )
        if not rows:
            like_terms = _query_terms(query)
            where_like = " OR ".join("kc.content_text LIKE ?" for _ in like_terms)
            rows = await self._db.fetch_all(
                f"""
                SELECT kc.*, ks.display_name AS source_display_name,
                       kc.chunk_index AS rank_score
                FROM knowledge_chunks kc
                JOIN knowledge_sources ks ON ks.source_id = kc.source_id
                WHERE kc.organization_id = ?
                  AND kc.status = 'active'
                  {asset_clause}
                  AND ({where_like})
                ORDER BY kc.chunk_index ASC
                LIMIT ?
                """,
                (*params, *(f"%{term}%" for term in like_terms), limit),
            )
        return [_chunk_from_row(dict(row)) for row in rows]

    async def get_chunk(self, chunk_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM knowledge_chunks WHERE chunk_id = ?",
            (chunk_id,),
        )
        return _chunk_from_row(dict(row)) if row else None

    async def insert_access_log(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO knowledge_access_logs (
              access_id, organization_id, asset_id, source_id, subject_type, subject_id,
              action, decision_id, trace_id, query_hash, selected_chunk_ids_json,
              filtered_chunk_ids_json, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["access_id"],
                data["organization_id"],
                data["asset_id"],
                data.get("source_id"),
                data["subject_type"],
                data["subject_id"],
                data["action"],
                data.get("decision_id"),
                data.get("trace_id"),
                data.get("query_hash"),
                _json(data.get("selected_chunk_ids", [])),
                _json(data.get("filtered_chunk_ids", [])),
                data.get("reason"),
                data["created_at"],
            ),
        )

    async def list_access_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM knowledge_access_logs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [_access_log_from_row(dict(row)) for row in rows]


def _source_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _chunk_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    if "rank_score" in row:
        try:
            row["rank_score"] = float(row["rank_score"])
        except (TypeError, ValueError):
            row["rank_score"] = 0.0
    return row


def _access_log_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["selected_chunk_ids"] = json.loads(row.pop("selected_chunk_ids_json") or "[]")
    row["filtered_chunk_ids"] = json.loads(row.pop("filtered_chunk_ids_json") or "[]")
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _query_terms(query: str) -> list[str]:
    terms = [term.strip() for term in query.replace("：", " ").replace(":", " ").split()]
    return [term for term in terms if term] or [query.strip()]


def _fts_query(query: str) -> str:
    terms = [term.replace('"', "") for term in _query_terms(query) if term.replace('"', "")]
    return " OR ".join(f'"{term}"' for term in terms)
