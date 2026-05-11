from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

MEMORY_UPDATE_COLUMNS = {
    "summary_text",
    "payload_json",
    "confidence",
    "importance",
    "sensitivity",
    "memory_class",
    "scope_policy",
    "durability",
    "freshness_state",
    "valid_to",
    "supersedes",
    "superseded_by",
    "status",
    "last_accessed_at",
    "access_count",
    "quality_score",
    "quality_breakdown_json",
    "version_index",
    "conflict_group_id",
    "conflict_status",
    "reuse_score",
    "reuse_count",
    "last_reused_at",
    "retention_policy",
    "retention_reason",
    "expires_reason",
    "expires_at",
    "stale_after",
    "evidence_strength",
    "review_required",
    "embedding_status",
    "metadata_json",
    "normalized_summary",
    "content_hash",
    "updated_at",
}


class MemoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_candidate(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_candidates (
              candidate_id, organization_id, member_id, user_id, source_json,
              proposed_layer, proposed_kind, proposed_scope_type, proposed_scope_id,
              summary_text, payload_json, score_json, final_score, sensitivity,
              decision, decision_reason, decided_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["candidate_id"],
                data["organization_id"],
                data.get("member_id"),
                data["user_id"],
                _json(data["source"]),
                data["proposed_layer"],
                data["proposed_kind"],
                data["proposed_scope_type"],
                data.get("proposed_scope_id"),
                data["summary_text"],
                _json(data.get("payload", {})),
                _json(data.get("score", {})),
                data["final_score"],
                data["sensitivity"],
                data["decision"],
                data.get("decision_reason"),
                data.get("decided_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,),
        )
        return _candidate_from_row(dict(row)) if row else None

    async def list_candidates(
        self,
        *,
        member_id: str | None = None,
        decision: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if member_id:
            where.append("member_id = ?")
            params.append(member_id)
        if decision:
            where.append("decision = ?")
            params.append(decision)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM memory_candidates
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_candidate_from_row(dict(row)) for row in rows]

    async def update_candidate_decision(
        self,
        candidate_id: str,
        *,
        decision: str,
        decision_reason: str | None,
        decided_at: str,
        updated_at: str,
    ) -> None:
        await self._db.execute(
            """
            UPDATE memory_candidates
            SET decision = ?, decision_reason = ?, decided_at = ?, updated_at = ?
            WHERE candidate_id = ?
            """,
            (decision, decision_reason, decided_at, updated_at, candidate_id),
        )

    async def insert_memory_item(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_items (
              memory_id, organization_id, member_id, user_id, layer, kind, scope_type,
              scope_id, memory_class, scope_policy, summary_text, payload_json, source_json,
              confidence, importance, sensitivity, durability, freshness_state, valid_from,
              valid_to, supersedes, superseded_by, status, last_accessed_at,
              access_count, quality_score, quality_breakdown_json, version_index,
              conflict_group_id, conflict_status, reuse_score, reuse_count, last_reused_at,
              retention_policy, retention_reason, expires_reason, expires_at, stale_after,
              evidence_strength, review_required,
              embedding_status, metadata_json, created_at, updated_at, normalized_summary,
              content_hash
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                data["memory_id"],
                data["organization_id"],
                data.get("member_id"),
                data["user_id"],
                data["layer"],
                data["kind"],
                data["scope_type"],
                data.get("scope_id"),
                data.get("memory_class", "fact"),
                data.get("scope_policy", "member_cross_session"),
                data["summary_text"],
                _json(data.get("payload", {})),
                _json(data["source"]),
                data["confidence"],
                data.get("importance", 0.5),
                data["sensitivity"],
                data.get("durability", "durable"),
                data.get("freshness_state", "fresh"),
                data.get("valid_from"),
                data.get("valid_to"),
                data.get("supersedes"),
                data.get("superseded_by"),
                data["status"],
                data.get("last_accessed_at"),
                data.get("access_count", 0),
                data.get("quality_score", 0.5),
                _json(data.get("quality_breakdown", {})),
                data.get("version_index", 1),
                data.get("conflict_group_id"),
                data.get("conflict_status", "clear"),
                data.get("reuse_score", 0),
                data.get("reuse_count", 0),
                data.get("last_reused_at"),
                data.get("retention_policy", "standard"),
                data.get("retention_reason"),
                data.get("expires_reason"),
                data.get("expires_at"),
                data.get("stale_after"),
                data.get("evidence_strength", 0.5),
                1 if bool(data.get("review_required", False)) else 0,
                data.get("embedding_status", "pending"),
                _json(data.get("metadata", {})),
                data["created_at"],
                data["updated_at"],
                data.get("normalized_summary"),
                data.get("content_hash"),
            ),
        )
        if data["status"] == "active" and data["sensitivity"] not in {
            "high",
            "secret",
            "credential",
            "wallet",
        }:
            await self._upsert_fts(data["memory_id"], data["summary_text"])

    async def get_memory_item(self, memory_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM memory_items WHERE memory_id = ?",
            (memory_id,),
        )
        return _memory_from_row(dict(row)) if row else None

    async def list_memory_items(
        self,
        *,
        member_id: str | None = None,
        status: str | None = None,
        layer: str | None = None,
        kind: str | None = None,
        memory_class: str | None = None,
        durability: str | None = None,
        freshness_state: str | None = None,
        sensitivity: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if member_id:
            where.append("(member_id = ? OR scope_type IN ('user', 'organization'))")
            params.append(member_id)
        if status:
            where.append("status = ?")
            params.append(status)
        if layer:
            where.append("layer = ?")
            params.append(layer)
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if memory_class:
            where.append("memory_class = ?")
            params.append(memory_class)
        if durability:
            where.append("durability = ?")
            params.append(durability)
        if freshness_state:
            where.append("freshness_state = ?")
            params.append(freshness_state)
        if sensitivity:
            where.append("sensitivity = ?")
            params.append(sensitivity)
        if query:
            where.append("(summary_text LIKE ? OR normalized_summary LIKE ?)")
            params.extend((f"%{query}%", f"%{_normalize(query)}%"))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM memory_items
            {clause}
            ORDER BY importance DESC, updated_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_memory_from_row(dict(row)) for row in rows]

    async def update_memory_item(self, memory_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sql_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "payload":
                sql_fields["payload_json"] = _json(value)
            elif key == "metadata":
                sql_fields["metadata_json"] = _json(value)
            elif key == "quality_breakdown":
                sql_fields["quality_breakdown_json"] = _json(value)
            elif key == "review_required":
                sql_fields[key] = 1 if bool(value) else 0
            else:
                sql_fields[key] = value
        unsupported = set(sql_fields) - MEMORY_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported memory_items update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in sql_fields)
        await self._db.execute(
            f"UPDATE memory_items SET {assignments} WHERE memory_id = ?",
            (*sql_fields.values(), memory_id),
        )
        updated = await self.get_memory_item(memory_id)
        if updated is not None:
            await self._sync_fts_for_memory(updated)

    async def find_duplicate(
        self,
        *,
        organization_id: str,
        member_id: str | None,
        normalized_summary: str,
    ) -> dict[str, Any] | None:
        rows = await self.list_memory_items(
            member_id=member_id,
            status="active",
            limit=200,
        )
        for row in rows:
            if row["organization_id"] != organization_id:
                continue
            row_summary = row.get("normalized_summary") or _normalize(row["summary_text"])
            if row_summary == normalized_summary:
                return row
        return None

    async def search_memory_items(
        self,
        *,
        organization_id: str,
        member_id: str,
        query: str,
        limit: int = 10,
        exclude_conversation_id: str | None = None,
        include_cross_session: bool = True,
        memory_classes: list[str] | None = None,
        durability_filter: list[str] | None = None,
        include_archived: bool = False,
        include_sensitive: bool = False,
        include_asset_scoped: bool = False,
        asset_scope_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        statuses = ("'active'", "'archived'") if include_archived else ("'active'",)
        sensitivity_clause = (
            ""
            if include_sensitive
            else "AND sensitivity NOT IN ('high', 'secret', 'credential', 'wallet')"
        )
        asset_clause, asset_params = _asset_scope_clause(
            "mi",
            include_asset_scoped=include_asset_scoped,
            asset_scope_ids=asset_scope_ids or [],
        )
        cross_session_clause = ""
        if not include_cross_session and exclude_conversation_id:
            cross_session_clause = (
                "AND (json_extract(mi.source_json, '$.conversation_id') = ? "
                "OR json_extract(mi.source_json, '$.conversation_id') IS NULL)"
            )
        class_clause, class_params = _in_clause("mi.memory_class", memory_classes or [])
        durability_clause, durability_params = _in_clause("mi.durability", durability_filter or [])
        base_params: tuple[Any, ...] = (organization_id, member_id, member_id)
        match_query = _fts_query(query)
        rows: list[Any] = []
        if match_query:
            rows = await self._db.fetch_all(
                f"""
                SELECT mi.*, bm25(memory_items_fts) * -1 AS rank_score
                FROM memory_items_fts
                JOIN memory_items mi ON mi.memory_id = memory_items_fts.memory_id
                WHERE memory_items_fts MATCH ?
                  AND mi.organization_id = ?
                  AND mi.status IN ({",".join(statuses)})
                  AND (
                    mi.scope_type IN ('user', 'organization')
                    OR mi.scope_id = ?
                  OR mi.member_id = ?
                  )
                  {sensitivity_clause}
                  {asset_clause}
                  {cross_session_clause}
                  {class_clause}
                  {durability_clause}
                ORDER BY rank_score DESC, mi.importance DESC, mi.updated_at DESC
                LIMIT ?
                """,
                (
                    match_query,
                    *base_params,
                    *asset_params,
                    *((exclude_conversation_id,) if cross_session_clause else ()),
                    *class_params,
                    *durability_params,
                    limit,
                ),
            )
        if not rows:
            like_terms = _query_terms(query)
            where_like = " OR ".join("mi.summary_text LIKE ?" for _ in like_terms) or "1 = 1"
            rows = await self._db.fetch_all(
                f"""
                SELECT mi.*, mi.importance AS rank_score
                FROM memory_items mi
                WHERE mi.organization_id = ?
                  AND mi.status IN ({",".join(statuses)})
                  AND (
                    mi.scope_type IN ('user', 'organization')
                    OR mi.scope_id = ?
                    OR mi.member_id = ?
                  )
                  {asset_clause}
                  {cross_session_clause}
                  {class_clause}
                  {durability_clause}
                  AND ({where_like})
                  {sensitivity_clause}
                ORDER BY mi.importance DESC, mi.confidence DESC, mi.updated_at DESC
                LIMIT ?
                """,
                (
                    *base_params,
                    *asset_params,
                    *((exclude_conversation_id,) if cross_session_clause else ()),
                    *class_params,
                    *durability_params,
                    *(f"%{term}%" for term in like_terms),
                    limit,
                ),
            )
        return [_memory_from_row(dict(row)) for row in rows]

    async def list_context_candidates(
        self,
        *,
        organization_id: str,
        member_id: str,
        limit: int = 200,
        include_asset_scoped: bool = False,
        asset_scope_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        asset_clause, asset_params = _asset_scope_clause(
            "",
            include_asset_scoped=include_asset_scoped,
            asset_scope_ids=asset_scope_ids or [],
        )
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM memory_items
            WHERE organization_id = ?
              AND (
                scope_type IN ('user', 'organization')
                OR scope_id = ?
                OR member_id = ?
              )
              {asset_clause}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (organization_id, member_id, member_id, *asset_params, limit),
        )
        return [_memory_from_row(dict(row)) for row in rows]

    async def touch_accessed(self, memory_ids: list[str], accessed_at: str) -> None:
        for memory_id in memory_ids:
            await self._db.execute(
                """
                UPDATE memory_items
                SET last_accessed_at = ?,
                    access_count = access_count + 1,
                    last_reused_at = ?,
                    reuse_count = reuse_count + 1,
                    reuse_score = MIN(1.0, reuse_score + 0.03),
                    updated_at = ?
                WHERE memory_id = ?
                """,
                (accessed_at, accessed_at, accessed_at, memory_id),
            )

    async def insert_experience_record(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_experience_records (
              experience_id, organization_id, member_id, task_id, conversation_id,
              memory_id, conflict_group_id, layer, kind, outcome, summary_text,
              source_json, evidence_json, score_json, confidence_score, reuse_score,
              decision, status, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["experience_id"],
                data["organization_id"],
                data.get("member_id"),
                data.get("task_id"),
                data.get("conversation_id"),
                data.get("memory_id"),
                data.get("conflict_group_id"),
                data["layer"],
                data["kind"],
                data["outcome"],
                data["summary_text"],
                _json(data.get("source", {})),
                _json(data.get("evidence", {})),
                _json(data.get("score", {})),
                data.get("confidence_score", 0),
                data.get("reuse_score", 0),
                data["decision"],
                data.get("status", "recorded"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_experience_records(
        self,
        *,
        member_id: str | None = None,
        task_id: str | None = None,
        outcome: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if member_id:
            where.append("member_id = ?")
            params.append(member_id)
        if task_id:
            where.append("task_id = ?")
            params.append(task_id)
        if outcome:
            where.append("outcome = ?")
            params.append(outcome)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM memory_experience_records
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_experience_from_row(dict(row)) for row in rows]

    async def insert_conflict_record(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_conflict_records (
              conflict_id, organization_id, member_id, memory_id, related_memory_id,
              candidate_id, conflict_group_id, conflict_type, status, resolution,
              summary_text, source_json, evidence_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["conflict_id"],
                data["organization_id"],
                data.get("member_id"),
                data.get("memory_id"),
                data.get("related_memory_id"),
                data.get("candidate_id"),
                data["conflict_group_id"],
                data["conflict_type"],
                data["status"],
                data.get("resolution"),
                data["summary_text"],
                _json(data.get("source", {})),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_conflict_records(
        self,
        *,
        member_id: str | None = None,
        status: str | None = None,
        conflict_group_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if member_id:
            where.append("member_id = ?")
            params.append(member_id)
        if status:
            where.append("status = ?")
            params.append(status)
        if conflict_group_id:
            where.append("conflict_group_id = ?")
            params.append(conflict_group_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM memory_conflict_records
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_conflict_from_row(dict(row)) for row in rows]

    async def insert_reuse_feedback(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_reuse_feedback (
              feedback_id, organization_id, member_id, retrieval_id, memory_id,
              task_id, feedback_type, rating, source_json, evidence_json, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["feedback_id"],
                data["organization_id"],
                data.get("member_id"),
                data["retrieval_id"],
                data["memory_id"],
                data.get("task_id"),
                data["feedback_type"],
                data.get("rating", 0),
                _json(data.get("source", {})),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_reuse_feedback(
        self,
        *,
        retrieval_id: str | None = None,
        memory_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if retrieval_id:
            where.append("retrieval_id = ?")
            params.append(retrieval_id)
        if memory_id:
            where.append("memory_id = ?")
            params.append(memory_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM memory_reuse_feedback
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_reuse_feedback_from_row(dict(row)) for row in rows]

    async def insert_relation(
        self,
        *,
        relation_id: str,
        organization_id: str,
        source_memory_id: str,
        target_memory_id: str,
        relation_type: str,
        evidence: dict[str, Any],
        created_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_relations (
              relation_id, organization_id, source_memory_id, target_memory_id,
              relation_type, evidence_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                relation_id,
                organization_id,
                source_memory_id,
                target_memory_id,
                relation_type,
                _json(evidence),
                created_at,
            ),
        )

    async def list_relations(self, memory_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT relation_id, organization_id, source_memory_id, target_memory_id,
                   relation_type, evidence_json, created_at
            FROM memory_relations
            WHERE source_memory_id = ? OR target_memory_id = ?
            ORDER BY created_at DESC
            """,
            (memory_id, memory_id),
        )
        return [_relation_from_row(dict(row)) for row in rows]

    async def insert_vector_ref(
        self,
        *,
        vector_ref_id: str,
        organization_id: str,
        memory_id: str,
        collection_name: str,
        vector_id: str,
        embedding_provider: str,
        embedding_model: str,
        content_hash: str,
        status: str,
        last_synced_at: str | None,
        error_code: str | None,
        created_at: str,
        updated_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_vector_refs (
              vector_ref_id, organization_id, memory_id, collection_name, vector_id,
              embedding_provider, embedding_model, content_hash, status, last_synced_at,
              error_code, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id, collection_name) DO UPDATE SET
              content_hash = excluded.content_hash,
              status = excluded.status,
              last_synced_at = excluded.last_synced_at,
              error_code = excluded.error_code,
              updated_at = excluded.updated_at
            """,
            (
                vector_ref_id,
                organization_id,
                memory_id,
                collection_name,
                vector_id,
                embedding_provider,
                embedding_model,
                content_hash,
                status,
                last_synced_at,
                error_code,
                created_at,
                updated_at,
            ),
        )

    async def insert_retrieval_log(
        self,
        *,
        retrieval_id: str,
        organization_id: str,
        trace_id: str | None,
        turn_id: str | None,
        conversation_id: str | None,
        member_id: str | None,
        query_text_hash: str,
        intent: str | None,
        selected_memory_ids: list[str],
        filtered_memory_ids: list[str],
        ranking: list[dict[str, Any]],
        token_budget: dict[str, Any],
        recall_scope_applied: str,
        request_filters: dict[str, Any],
        degraded: bool,
        created_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_retrieval_logs (
              retrieval_id, organization_id, trace_id, turn_id, conversation_id,
              member_id, query_text_hash, intent, selected_memory_ids_json,
              filtered_memory_ids_json, ranking_json, token_budget_json, recall_scope_applied,
              request_filters_json, degraded, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                retrieval_id,
                organization_id,
                trace_id,
                turn_id,
                conversation_id,
                member_id,
                query_text_hash,
                intent,
                _json(selected_memory_ids),
                _json(filtered_memory_ids),
                _json(ranking),
                _json(token_budget),
                recall_scope_applied,
                _json(request_filters),
                1 if degraded else 0,
                created_at,
            ),
        )

    async def insert_job(
        self,
        *,
        job_id: str,
        organization_id: str,
        turn_id: str | None,
        idempotency_key: str,
        job_type: str,
        status: str,
        payload: dict[str, Any],
        max_attempts: int = 3,
        next_run_at: str | None = None,
        created_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO memory_jobs (
              job_id, organization_id, turn_id, idempotency_key, job_type, status,
              payload_json, max_attempts, next_run_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (
                job_id,
                organization_id,
                turn_id,
                idempotency_key,
                job_type,
                status,
                _json(payload),
                max_attempts,
                next_run_at,
                created_at,
                created_at,
            ),
        )

    async def update_job_status(
        self,
        *,
        idempotency_key: str,
        status: str,
        error_code: str | None,
        error_message: str | None,
        updated_at: str,
        completed_at: str | None = None,
    ) -> None:
        await self._db.execute(
            """
            UPDATE memory_jobs
            SET status = ?,
                error_code = ?,
                error_message = ?,
                updated_at = ?,
                completed_at = ?,
                locked_by = NULL,
                locked_at = NULL
            WHERE idempotency_key = ?
            """,
            (status, error_code, error_message, updated_at, completed_at, idempotency_key),
        )

    async def get_job_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM memory_jobs WHERE idempotency_key = ?",
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
            FROM memory_jobs
            {clause}
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_job_from_row(dict(row)) for row in rows]

    async def restore_stale_jobs(self, *, stale_before: str, updated_at: str) -> int:
        return await self._db.execute(
            """
            UPDATE memory_jobs
            SET status = 'pending',
                locked_by = NULL,
                locked_at = NULL,
                updated_at = ?
            WHERE status = 'running'
              AND locked_at IS NOT NULL
              AND locked_at < ?
              AND attempts < max_attempts
            """,
            (updated_at, stale_before),
        )

    async def claim_next_job(
        self,
        *,
        worker_id: str,
        now: str,
        job_type: str = "extract_after_turn",
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM memory_jobs
            WHERE job_type = ?
              AND status = 'pending'
              AND attempts < max_attempts
              AND (next_run_at IS NULL OR next_run_at <= ?)
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (job_type, now),
        )
        if row is None:
            return None
        job = _job_from_row(dict(row))
        rowcount = await self._db.execute(
            """
            UPDATE memory_jobs
            SET status = 'running',
                attempts = attempts + 1,
                locked_by = ?,
                locked_at = ?,
                updated_at = ?
            WHERE job_id = ?
              AND status = 'pending'
            """,
            (worker_id, now, now, job["job_id"]),
        )
        if rowcount != 1:
            return None
        latest = await self._db.fetch_one(
            "SELECT * FROM memory_jobs WHERE job_id = ?",
            (job["job_id"],),
        )
        return _job_from_row(dict(latest)) if latest else None

    async def _sync_fts_for_memory(self, memory: dict[str, Any]) -> None:
        await self._db.execute(
            "DELETE FROM memory_items_fts WHERE memory_id = ?",
            (memory["memory_id"],),
        )
        if memory["status"] == "active" and memory["sensitivity"] not in {
            "high",
            "secret",
            "credential",
            "wallet",
        }:
            await self._upsert_fts(memory["memory_id"], memory["summary_text"])

    async def _upsert_fts(self, memory_id: str, summary_text: str) -> None:
        await self._db.execute(
            "DELETE FROM memory_items_fts WHERE memory_id = ?",
            (memory_id,),
        )
        await self._db.execute(
            "INSERT INTO memory_items_fts (summary_text, memory_id) VALUES (?, ?)",
            (summary_text, memory_id),
        )


def _candidate_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["source"] = json.loads(row.pop("source_json") or "{}")
    row["payload"] = json.loads(row.pop("payload_json") or "{}")
    row["score"] = json.loads(row.pop("score_json") or "{}")
    return row


def _memory_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_json") or "{}")
    row["source"] = json.loads(row.pop("source_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    row["quality_breakdown"] = json.loads(row.pop("quality_breakdown_json", "{}") or "{}")
    row["review_required"] = bool(row["review_required"])
    row["version_index"] = int(row.get("version_index") or 1)
    row["reuse_count"] = int(row.get("reuse_count") or 0)
    if "rank_score" in row:
        try:
            row["rank_score"] = float(row["rank_score"])
        except (TypeError, ValueError):
            row["rank_score"] = 0.0
    return row


def _in_clause(column: str, values: list[str]) -> tuple[str, tuple[str, ...]]:
    if not values:
        return "", ()
    placeholders = ",".join("?" for _ in values)
    return (f"AND {column} IN ({placeholders})", tuple(values))


def _job_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_json") or "{}")
    return row


def _relation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _experience_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["source"] = json.loads(row.pop("source_json") or "{}")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    row["score"] = json.loads(row.pop("score_json") or "{}")
    return row


def _conflict_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["source"] = json.loads(row.pop("source_json") or "{}")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _reuse_feedback_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["source"] = json.loads(row.pop("source_json") or "{}")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _asset_scope_clause(
    alias: str,
    *,
    include_asset_scoped: bool,
    asset_scope_ids: list[str],
) -> tuple[str, tuple[str, ...]]:
    prefix = f"{alias}." if alias else ""
    if not include_asset_scoped or not asset_scope_ids:
        return f"AND {prefix}scope_type != 'asset'", ()
    if "*" in asset_scope_ids:
        return "", ()
    placeholders = ",".join("?" for _ in asset_scope_ids)
    return (
        f"AND ({prefix}scope_type != 'asset' OR {prefix}scope_id IN ({placeholders}))",
        tuple(asset_scope_ids),
    )


def _normalize(value: str) -> str:
    return "".join(value.lower().split())


def _query_terms(query: str) -> list[str]:
    terms = [term.strip() for term in query.replace("：", " ").replace(":", " ").split()]
    return [term for term in terms if term] or [query.strip()]


def _fts_query(query: str) -> str:
    terms = [term.replace('"', "") for term in _query_terms(query) if term.replace('"', "")]
    return " OR ".join(f'"{term}"' for term in terms)
