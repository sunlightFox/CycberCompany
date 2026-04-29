from __future__ import annotations

import json
from typing import Any

from app.core.time import utc_now_iso
from app.db.session import Database

SETTING_KEY = "model_routing"


class ModelRoutingService:
    def __init__(self, db: Database, default_config: dict[str, Any]) -> None:
        self._db = db
        self._default_config = default_config

    async def get_config(self) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT value_json FROM app_settings WHERE setting_key = ?",
            (SETTING_KEY,),
        )
        if row is None:
            return self._default_config
        return json.loads(row["value_json"])

    async def update_config(self, config: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        await self._db.execute(
            """
            INSERT INTO app_settings (setting_key, value_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
              value_json = excluded.value_json,
              updated_at = excluded.updated_at
            """,
            (SETTING_KEY, json.dumps(config, ensure_ascii=False), now, now),
        )
        return config
