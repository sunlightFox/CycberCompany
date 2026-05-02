from __future__ import annotations

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
                  source_json, checksum, trust_level, search_text, status, indexed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data["indexed_at"],
                    data["updated_at"],
                ),
            )

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
    row.pop("search_text", None)
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


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
