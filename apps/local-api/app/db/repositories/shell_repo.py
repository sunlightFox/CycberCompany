from __future__ import annotations

import json
from typing import Any

from app.core.time import utc_now_iso
from app.db.session import Database


class ShellRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_shell(
        self,
        shell_id: str,
        display_name: str,
        version: str,
        config: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        await self._db.execute(
            """
            INSERT INTO shells (
              shell_id, display_name, version, config_json, is_enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(shell_id) DO UPDATE SET
              display_name = excluded.display_name,
              version = excluded.version,
              config_json = excluded.config_json,
              is_enabled = 1,
              updated_at = excluded.updated_at
            """,
            (shell_id, display_name, version, json.dumps(config, ensure_ascii=False), now, now),
        )

    async def list_shells(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT shell_id, display_name, version, is_enabled, created_at, updated_at FROM shells"
        )
        return [dict(row) for row in rows]

    async def get_shell(self, shell_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT shell_id, display_name, version, config_json, is_enabled, created_at, updated_at
            FROM shells
            WHERE shell_id = ?
            """,
            (shell_id,),
        )
        if row is None:
            return None
        data = dict(row)
        data["config"] = json.loads(data.pop("config_json") or "{}")
        return data
