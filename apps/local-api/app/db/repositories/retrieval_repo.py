from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class RetrievalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_embedding_provider_config(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO embedding_provider_configs (
              provider_id, provider_type, provider_name, embedding_model, embedding_dim,
              status, privacy_policy, allow_cloud, secret_ref, fallback_policy,
              degraded_reason, config_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
              provider_type = excluded.provider_type,
              provider_name = excluded.provider_name,
              embedding_model = excluded.embedding_model,
              embedding_dim = excluded.embedding_dim,
              status = excluded.status,
              privacy_policy = excluded.privacy_policy,
              allow_cloud = excluded.allow_cloud,
              secret_ref = excluded.secret_ref,
              fallback_policy = excluded.fallback_policy,
              degraded_reason = excluded.degraded_reason,
              config_json = excluded.config_json,
              updated_at = excluded.updated_at
            """,
            (
                data["provider_id"],
                data["provider_type"],
                data["provider_name"],
                data["embedding_model"],
                data.get("embedding_dim", 0),
                data["status"],
                data.get("privacy_policy", "local_only"),
                1 if data.get("allow_cloud") else 0,
                data.get("secret_ref"),
                data.get("fallback_policy", "fts"),
                data.get("degraded_reason"),
                _json(data.get("config", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_embedding_provider_config(
        self,
        provider_id: str,
        *,
        include_secret_ref: bool = False,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM embedding_provider_configs WHERE provider_id = ?",
            (provider_id,),
        )
        return _provider_from_row(dict(row), include_secret_ref=include_secret_ref) if row else None

    async def list_embedding_provider_configs(
        self,
        *,
        include_secret_ref: bool = False,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM embedding_provider_configs
            ORDER BY
              CASE provider_type
                WHEN 'local_hash' THEN 0
                WHEN 'local_model' THEN 1
                WHEN 'chroma' THEN 2
                WHEN 'external_compatible' THEN 3
                ELSE 4
              END,
              provider_id ASC
            """
        )
        return [
            _provider_from_row(dict(row), include_secret_ref=include_secret_ref)
            for row in rows
        ]

    async def insert_rerank_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO retrieval_rerank_runs (
              rerank_run_id, retrieval_id, organization_id, target_type, provider,
              scoring_policy_json, input_count, selected_count, suppressed_count,
              fallback_used, latency_ms, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["rerank_run_id"],
                data["retrieval_id"],
                data["organization_id"],
                data["target_type"],
                data.get("provider"),
                _json(data.get("scoring_policy", {})),
                data.get("input_count", 0),
                data.get("selected_count", 0),
                data.get("suppressed_count", 0),
                1 if data.get("fallback_used") else 0,
                data.get("latency_ms", 0),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def insert_suppressed_item(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO retrieval_suppressed_items (
              suppressed_id, retrieval_id, organization_id, target_type, target_id,
              reason, sensitivity, selection_score, metadata_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["suppressed_id"],
                data["retrieval_id"],
                data["organization_id"],
                data["target_type"],
                data["target_id"],
                data["reason"],
                data.get("sensitivity"),
                data.get("selection_score", 0),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def insert_knowledge_retrieval_log(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO knowledge_retrieval_logs (
              retrieval_id, organization_id, trace_id, conversation_id, task_id,
              subject_type, subject_id, asset_id, query_text_hash,
              selected_chunk_ids_json, filtered_chunk_ids_json, ranking_json,
              retrieval_sources_json, degraded, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["retrieval_id"],
                data["organization_id"],
                data.get("trace_id"),
                data.get("conversation_id"),
                data.get("task_id"),
                data["subject_type"],
                data["subject_id"],
                data.get("asset_id"),
                data["query_text_hash"],
                _json(data.get("selected_chunk_ids", [])),
                _json(data.get("filtered_chunk_ids", [])),
                _json(data.get("ranking", [])),
                _json(data.get("retrieval_sources", [])),
                1 if data.get("degraded") else 0,
                data["created_at"],
            ),
        )

    async def insert_quality_report(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO retrieval_quality_reports (
              report_id, organization_id, target_type, retrieval_id, summary_json,
              metrics_json, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["report_id"],
                data["organization_id"],
                data["target_type"],
                data.get("retrieval_id"),
                _json(data.get("summary", {})),
                _json(data.get("metrics", {})),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def diagnostics(self, retrieval_id: str) -> dict[str, Any] | None:
        memory_log = await self._db.fetch_one(
            "SELECT * FROM memory_retrieval_logs WHERE retrieval_id = ?",
            (retrieval_id,),
        )
        knowledge_log = await self._db.fetch_one(
            "SELECT * FROM knowledge_retrieval_logs WHERE retrieval_id = ?",
            (retrieval_id,),
        )
        target_type = "memory" if memory_log else "knowledge" if knowledge_log else None
        if target_type is None:
            return None
        if memory_log is not None:
            log = _memory_log_from_row(dict(memory_log))
        elif knowledge_log is not None:
            log = _knowledge_log_from_row(dict(knowledge_log))
        else:
            return None
        rerank_rows = await self._db.fetch_all(
            """
            SELECT *
            FROM retrieval_rerank_runs
            WHERE retrieval_id = ?
            ORDER BY created_at ASC
            """,
            (retrieval_id,),
        )
        suppressed_rows = await self._db.fetch_all(
            """
            SELECT *
            FROM retrieval_suppressed_items
            WHERE retrieval_id = ?
            ORDER BY created_at ASC
            """,
            (retrieval_id,),
        )
        report_rows = await self._db.fetch_all(
            """
            SELECT *
            FROM retrieval_quality_reports
            WHERE retrieval_id = ?
            ORDER BY created_at ASC
            """,
            (retrieval_id,),
        )
        return {
            "retrieval_id": retrieval_id,
            "target_type": target_type,
            "log": log,
            "rerank_runs": [_rerank_from_row(dict(row)) for row in rerank_rows],
            "suppressed_items": [
                _suppressed_from_row(dict(row)) for row in suppressed_rows
            ],
            "quality_reports": [_report_from_row(dict(row)) for row in report_rows],
        }


def _provider_from_row(
    row: dict[str, Any],
    *,
    include_secret_ref: bool = False,
) -> dict[str, Any]:
    row["allow_cloud"] = bool(row.pop("allow_cloud"))
    row["secret_ref_present"] = bool(row.get("secret_ref"))
    if not include_secret_ref:
        row.pop("secret_ref", None)
    row["config"] = json.loads(row.pop("config_json") or "{}")
    return row


def _memory_log_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["selected_memory_ids"] = json.loads(row.pop("selected_memory_ids_json") or "[]")
    row["filtered_memory_ids"] = json.loads(row.pop("filtered_memory_ids_json") or "[]")
    row["ranking"] = json.loads(row.pop("ranking_json") or "[]")
    row["token_budget"] = json.loads(row.pop("token_budget_json") or "{}")
    row["degraded"] = bool(row["degraded"])
    return row


def _knowledge_log_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["selected_chunk_ids"] = json.loads(row.pop("selected_chunk_ids_json") or "[]")
    row["filtered_chunk_ids"] = json.loads(row.pop("filtered_chunk_ids_json") or "[]")
    row["ranking"] = json.loads(row.pop("ranking_json") or "[]")
    row["retrieval_sources"] = json.loads(row.pop("retrieval_sources_json") or "[]")
    row["degraded"] = bool(row["degraded"])
    return row


def _rerank_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["scoring_policy"] = json.loads(row.pop("scoring_policy_json") or "{}")
    row["fallback_used"] = bool(row["fallback_used"])
    return row


def _suppressed_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _report_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["summary"] = json.loads(row.pop("summary_json") or "{}")
    row["metrics"] = json.loads(row.pop("metrics_json") or "{}")
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
