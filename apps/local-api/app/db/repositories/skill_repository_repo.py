from __future__ import annotations

import hashlib
import json
from typing import Any

from app.db.session import Database


class SkillRepositoryRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_repository(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_repositories (
              repository_id, organization_id, display_name, provider, index_uri, base_uri,
              auth_json, priority, is_default, trust_level, status, config_json,
              last_refresh_at, last_error_code, last_error_summary, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repository_id) DO UPDATE SET
              display_name = excluded.display_name,
              provider = excluded.provider,
              index_uri = excluded.index_uri,
              base_uri = excluded.base_uri,
              auth_json = excluded.auth_json,
              priority = excluded.priority,
              is_default = excluded.is_default,
              trust_level = excluded.trust_level,
              status = excluded.status,
              config_json = excluded.config_json,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["repository_id"],
                data["organization_id"],
                data["display_name"],
                data["provider"],
                data.get("index_uri"),
                data.get("base_uri"),
                _json(data.get("auth", {})),
                int(data.get("priority", 100)),
                1 if data.get("is_default") else 0,
                data.get("trust_level", "restricted"),
                data.get("status", "enabled"),
                _json(data.get("config", {})),
                data.get("last_refresh_at"),
                data.get("last_error_code"),
                data.get("last_error_summary"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_repository(self, repository_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "auth": "auth_json",
                "config": "config_json",
                "is_default": "is_default",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE skill_repositories SET {assignments} WHERE repository_id = ?",
            (*values.values(), repository_id),
        )

    async def clear_default(self, *, organization_id: str = "org_default") -> None:
        await self._db.execute(
            "UPDATE skill_repositories SET is_default = 0 WHERE organization_id = ?",
            (organization_id,),
        )

    async def get_repository(self, repository_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM skill_repositories WHERE repository_id = ?",
            (repository_id,),
        )
        return _repository_from_row(dict(row)) if row else None

    async def get_default_repository(self) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_repositories
            WHERE organization_id = 'org_default'
              AND is_default = 1
              AND status = 'enabled'
            ORDER BY priority ASC, repository_id ASC
            LIMIT 1
            """,
        )
        if row:
            return _repository_from_row(dict(row))
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_repositories
            WHERE organization_id = 'org_default'
              AND status = 'enabled'
            ORDER BY priority ASC, repository_id ASC
            LIMIT 1
            """,
        )
        return _repository_from_row(dict(row)) if row else None

    async def list_repositories(self, *, include_disabled: bool = True) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        if not include_disabled:
            where.append("status = 'enabled'")
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM skill_repositories
            WHERE {' AND '.join(where)}
            ORDER BY priority ASC, repository_id ASC
            """
        )
        return [_repository_from_row(dict(row)) for row in rows]

    async def replace_entries(self, repository_id: str, entries: list[dict[str, Any]]) -> None:
        await self._db.execute(
            "UPDATE skill_repository_entries SET status = 'stale' WHERE repository_id = ?",
            (repository_id,),
        )
        for data in entries:
            await self._db.execute(
                """
                INSERT INTO skill_repository_entries (
                  entry_id, organization_id, repository_id, package_ref, bundle_id,
                  display_name, description, version, author, tags_json, keywords_json,
                  source_json, checksum, trust_level, search_text, status, health_status,
                  quality_score, install_count, compatibility_json, dependency_summary_json,
                  latest_eval_status, last_health_check_at, health_reason,
                  package_metadata_json, indexed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repository_id, package_ref) DO UPDATE SET
                  bundle_id = excluded.bundle_id,
                  display_name = excluded.display_name,
                  description = excluded.description,
                  version = excluded.version,
                  author = excluded.author,
                  tags_json = excluded.tags_json,
                  keywords_json = excluded.keywords_json,
                  source_json = excluded.source_json,
                  checksum = excluded.checksum,
                  trust_level = excluded.trust_level,
                  search_text = excluded.search_text,
                  status = excluded.status,
                  compatibility_json = excluded.compatibility_json,
                  dependency_summary_json = excluded.dependency_summary_json,
                  package_metadata_json = excluded.package_metadata_json,
                  indexed_at = excluded.indexed_at,
                  updated_at = excluded.updated_at
                """,
                (
                    data["entry_id"],
                    data["organization_id"],
                    data["repository_id"],
                    data["package_ref"],
                    data["bundle_id"],
                    data["display_name"],
                    data.get("description"),
                    data.get("version"),
                    data.get("author"),
                    _json(data.get("tags", [])),
                    _json(data.get("keywords", [])),
                    _json(data.get("source", {})),
                    data.get("checksum"),
                    data.get("trust_level", "restricted"),
                    data["search_text"],
                    data.get("status", "active"),
                    data.get("health_status", "unknown"),
                    float(data.get("quality_score", 0.5)),
                    int(data.get("install_count", 0)),
                    _json(data.get("compatibility", {})),
                    _json(data.get("dependency_summary", {})),
                    data.get("latest_eval_status"),
                    data.get("last_health_check_at"),
                    data.get("health_reason"),
                    _json(data.get("package_metadata", {})),
                    data["indexed_at"],
                    data["updated_at"],
                ),
            )
            await self.upsert_package_version(data)

    async def search_entries(
        self,
        *,
        query: str | None,
        repository_id: str | None,
        tag: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        where = ["e.organization_id = 'org_default'", "e.status = 'active'", "r.status = 'enabled'"]
        params: list[Any] = []
        if repository_id:
            where.append("e.repository_id = ?")
            params.append(repository_id)
        if query:
            where.append("lower(e.search_text) LIKE ?")
            params.append(f"%{query.lower()}%")
        if tag:
            where.append("lower(e.tags_json) LIKE ?")
            params.append(f"%{tag.lower()}%")
        rows = await self._db.fetch_all(
            f"""
            SELECT e.*
            FROM skill_repository_entries e
            JOIN skill_repositories r ON r.repository_id = e.repository_id
            WHERE {' AND '.join(where)}
            ORDER BY r.priority ASC, e.display_name ASC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_entry_from_row(dict(row)) for row in rows]

    async def get_entry(self, repository_id: str, package_ref: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_repository_entries
            WHERE repository_id = ? AND package_ref = ? AND status = 'active'
            """,
            (repository_id, package_ref),
        )
        return _entry_from_row(dict(row)) if row else None

    async def update_entry_marketplace(
        self,
        repository_id: str,
        package_ref: str,
        fields: dict[str, Any],
    ) -> None:
        values = _json_update_fields(
            fields,
            {
                "compatibility": "compatibility_json",
                "dependency_summary": "dependency_summary_json",
                "package_metadata": "package_metadata_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"""
            UPDATE skill_repository_entries
            SET {assignments}
            WHERE repository_id = ? AND package_ref = ?
            """,
            (*values.values(), repository_id, package_ref),
        )

    async def upsert_package_version(self, data: dict[str, Any]) -> None:
        version = data.get("version") or "unversioned"
        checksum = data.get("checksum") or ""
        version_id = (
            f"skver_"
            f"{_hash_text(data['repository_id'] + ':' + data['package_ref'] + ':' + version + ':' + checksum)[:24]}"
        )
        await self._db.execute(
            """
            INSERT INTO skill_marketplace_package_versions (
              version_id, organization_id, repository_id, package_ref, bundle_id,
              version, checksum, source_uri_hash, dependency_summary_json,
              compatibility_json, quality_score, status, indexed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(version_id) DO UPDATE SET
              organization_id = excluded.organization_id,
              repository_id = excluded.repository_id,
              package_ref = excluded.package_ref,
              bundle_id = excluded.bundle_id,
              version = excluded.version,
              checksum = excluded.checksum,
              source_uri_hash = excluded.source_uri_hash,
              dependency_summary_json = excluded.dependency_summary_json,
              compatibility_json = excluded.compatibility_json,
              quality_score = excluded.quality_score,
              status = excluded.status,
              indexed_at = excluded.indexed_at,
              updated_at = excluded.updated_at
            """,
            (
                version_id,
                data["organization_id"],
                data["repository_id"],
                data["package_ref"],
                data["bundle_id"],
                version,
                checksum,
                _source_uri_hash(data.get("source", {})),
                _json(data.get("dependency_summary", {})),
                _json(data.get("compatibility", {})),
                float(data.get("quality_score", 0.5)),
                data.get("status", "active"),
                data["indexed_at"],
                data["indexed_at"],
                data["updated_at"],
            ),
        )

    async def list_package_versions(
        self,
        *,
        repository_id: str,
        package_ref: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM skill_marketplace_package_versions
            WHERE repository_id = ? AND package_ref = ?
            ORDER BY indexed_at DESC
            LIMIT ?
            """,
            (repository_id, package_ref, limit),
        )
        return [_package_version_from_row(dict(row)) for row in rows]

    async def insert_health_record(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_marketplace_health_records (
              health_record_id, organization_id, repository_id, package_ref, bundle_id,
              health_status, provider_status, quality_score, reason_codes_json,
              evidence_json, trace_id, checked_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["health_record_id"],
                data["organization_id"],
                data["repository_id"],
                data.get("package_ref"),
                data.get("bundle_id"),
                data["health_status"],
                data.get("provider_status", "unknown"),
                float(data.get("quality_score", 0.5)),
                _json(data.get("reason_codes", [])),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["checked_at"],
                data["created_at"],
            ),
        )

    async def latest_health_record(
        self,
        *,
        repository_id: str,
        package_ref: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_marketplace_health_records
            WHERE repository_id = ? AND package_ref = ?
            ORDER BY checked_at DESC
            LIMIT 1
            """,
            (repository_id, package_ref),
        )
        return _health_from_row(dict(row)) if row else None

    async def insert_install_record(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_marketplace_install_records (
              install_record_id, organization_id, repository_id, package_ref, bundle_id,
              installed_bundle_id, skill_id, version, status, gate_status, eval_status,
              blocked_reason, source_uri_hash, requested_by_member_id, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["install_record_id"],
                data["organization_id"],
                data.get("repository_id"),
                data.get("package_ref"),
                data.get("bundle_id"),
                data.get("installed_bundle_id"),
                data.get("skill_id"),
                data.get("version"),
                data["status"],
                data["gate_status"],
                data.get("eval_status"),
                data.get("blocked_reason"),
                data.get("source_uri_hash"),
                data.get("requested_by_member_id"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )
        if data.get("repository_id") and data.get("package_ref"):
            await self._db.execute(
                """
                UPDATE skill_repository_entries
                SET install_count = install_count + 1,
                    latest_eval_status = COALESCE(?, latest_eval_status),
                    updated_at = ?
                WHERE repository_id = ? AND package_ref = ?
                """,
                (
                    data.get("eval_status"),
                    data["created_at"],
                    data["repository_id"],
                    data["package_ref"],
                ),
            )

    async def list_install_records(
        self,
        *,
        repository_id: str | None = None,
        package_ref: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if repository_id:
            where.append("repository_id = ?")
            params.append(repository_id)
        if package_ref:
            where.append("package_ref = ?")
            params.append(package_ref)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM skill_marketplace_install_records
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_install_from_row(dict(row)) for row in rows]

    async def upsert_dependency_edge(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_dependency_edges (
              edge_id, organization_id, source_type, source_id, target_type, target_id,
              dependency_kind, required_action, risk_level, status, fail_closed_reason,
              evidence_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_type, source_id, target_type, target_id, dependency_kind, required_action) DO UPDATE SET
              risk_level = excluded.risk_level,
              status = excluded.status,
              fail_closed_reason = excluded.fail_closed_reason,
              evidence_json = excluded.evidence_json,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["edge_id"],
                data["organization_id"],
                data["source_type"],
                data["source_id"],
                data["target_type"],
                data["target_id"],
                data["dependency_kind"],
                str(data.get("required_action") or ""),
                data.get("risk_level", "R1"),
                data["status"],
                data.get("fail_closed_reason"),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_dependency_edges(
        self,
        *,
        source_type: str | None = None,
        source_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if source_type:
            where.append("source_type = ?")
            params.append(source_type)
        if source_id:
            where.append("source_id = ?")
            params.append(source_id)
        if status:
            where.append("status = ?")
            params.append(status)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM skill_dependency_edges
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_dependency_from_row(dict(row)) for row in rows]

    async def list_growth_experience_sources(
        self,
        *,
        task_id: str | None = None,
        experience_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if task_id:
            where.append("task_id = ?")
            params.append(task_id)
        if experience_id:
            where.append("experience_id = ?")
            params.append(experience_id)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM memory_experience_records
            WHERE {' AND '.join(where)}
              AND status = 'recorded'
            ORDER BY reuse_score DESC, confidence_score DESC, created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_experience_source_from_row(dict(row)) for row in rows]

    async def insert_growth_evidence(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_growth_candidate_evidence (
              evidence_id, organization_id, candidate_id, source_type, source_id,
              experience_id, task_id, memory_id, outcome, reuse_score, decision,
              evidence_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["evidence_id"],
                data["organization_id"],
                data.get("candidate_id"),
                data["source_type"],
                data["source_id"],
                data.get("experience_id"),
                data.get("task_id"),
                data.get("memory_id"),
                data.get("outcome"),
                float(data.get("reuse_score", 0)),
                data["decision"],
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_growth_evidence(
        self,
        *,
        candidate_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if candidate_id:
            where.append("candidate_id = ?")
            params.append(candidate_id)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM skill_growth_candidate_evidence
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_growth_from_row(dict(row)) for row in rows]

    async def insert_sync_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_repository_sync_runs (
              sync_run_id, organization_id, repository_id, status, indexed_count,
              error_code, error_summary, trace_id, started_at, completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["sync_run_id"],
                data["organization_id"],
                data["repository_id"],
                data["status"],
                int(data.get("indexed_count", 0)),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("trace_id"),
                data["started_at"],
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def get_sync_run(self, sync_run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM skill_repository_sync_runs WHERE sync_run_id = ?",
            (sync_run_id,),
        )
        return dict(row) if row else None


def _repository_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["auth"] = json.loads(row.pop("auth_json") or "{}")
    row["config"] = json.loads(row.pop("config_json") or "{}")
    row["is_default"] = bool(row["is_default"])
    return row


def _entry_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["tags"] = json.loads(row.pop("tags_json") or "[]")
    row["keywords"] = json.loads(row.pop("keywords_json") or "[]")
    row["source"] = json.loads(row.pop("source_json") or "{}")
    row["compatibility"] = json.loads(row.pop("compatibility_json", "{}") or "{}")
    row["dependency_summary"] = json.loads(
        row.pop("dependency_summary_json", "{}") or "{}"
    )
    row["package_metadata"] = json.loads(row.pop("package_metadata_json", "{}") or "{}")
    row["quality_score"] = float(row.get("quality_score", 0.5) or 0.5)
    row["install_count"] = int(row.get("install_count") or 0)
    row.pop("search_text", None)
    return row


def _package_version_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["dependency_summary"] = json.loads(
        row.pop("dependency_summary_json", "{}") or "{}"
    )
    row["compatibility"] = json.loads(row.pop("compatibility_json", "{}") or "{}")
    row["quality_score"] = float(row.get("quality_score", 0.5) or 0.5)
    return row


def _health_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["reason_codes"] = json.loads(row.pop("reason_codes_json", "[]") or "[]")
    row["evidence"] = json.loads(row.pop("evidence_json", "{}") or "{}")
    row["quality_score"] = float(row.get("quality_score", 0.5) or 0.5)
    return row


def _install_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return row


def _dependency_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json", "{}") or "{}")
    return row


def _experience_source_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json", "{}") or "{}")
    return row


def _growth_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json", "{}") or "{}")
    row["reuse_score"] = float(row.get("reuse_score", 0.0) or 0.0)
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_uri_hash(source: dict[str, Any]) -> str | None:
    uri = source.get("uri") or source.get("url") or source.get("path")
    if uri:
        return _hash_text(str(uri))
    if source:
        return _hash_text(json.dumps(source, ensure_ascii=False, sort_keys=True))
    return None


def _json_update_fields(fields: dict[str, Any], json_columns: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = json_columns.get(key, key)
        if key == "is_default":
            values[column] = 1 if value else 0
        elif key in json_columns and key != "is_default":
            values[column] = _json(value)
        else:
            values[column] = value
    return values
