from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_runtime_settings(self, organization_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM runtime_settings
            WHERE organization_id = ?
            """,
            (organization_id,),
        )
        return _runtime_settings_from_row(dict(row)) if row else None

    async def upsert_runtime_settings(
        self,
        *,
        setting_id: str,
        organization_id: str,
        settings: dict[str, Any],
        updated_by_member_id: str | None,
        trace_id: str | None,
        now: str,
    ) -> dict[str, Any]:
        current = await self.get_runtime_settings(organization_id)
        version = int(current["version"]) + 1 if current else 1
        created_at = str(current["created_at"]) if current else now
        await self._db.execute(
            """
            INSERT INTO runtime_settings (
              setting_id, organization_id, settings_json, version, updated_by_member_id,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id) DO UPDATE SET
              settings_json = excluded.settings_json,
              version = excluded.version,
              updated_by_member_id = excluded.updated_by_member_id,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                setting_id,
                organization_id,
                json.dumps(settings, ensure_ascii=False, separators=(",", ":")),
                version,
                updated_by_member_id,
                trace_id,
                created_at,
                now,
            ),
        )
        saved = await self.get_runtime_settings(organization_id)
        if saved is None:
            raise RuntimeError("runtime settings upsert failed")
        return saved

    async def upsert_app_setting(self, setting_key: str, value: dict[str, Any], now: str) -> None:
        await self._db.execute(
            """
            INSERT INTO app_settings (setting_key, value_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
              value_json = excluded.value_json,
              updated_at = excluded.updated_at
            """,
            (
                setting_key,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                now,
                now,
            ),
        )


def _runtime_settings_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["settings"] = json.loads(row.pop("settings_json"))
    return row
