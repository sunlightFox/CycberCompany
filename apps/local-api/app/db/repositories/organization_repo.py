from __future__ import annotations

from typing import Any

from app.db.session import Database


class OrganizationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_current(self) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT organization_id, shell_id, display_name, owner_user_id, owner_title,
                   settings_json, created_at, updated_at
            FROM organizations
            ORDER BY created_at ASC
            LIMIT 1
            """
        )
        return dict(row) if row else None

    async def update_shell(
        self,
        organization_id: str,
        *,
        shell_id: str,
        updated_at: str,
    ) -> None:
        await self._db.execute(
            """
            UPDATE organizations
            SET shell_id = ?, updated_at = ?
            WHERE organization_id = ?
            """,
            (shell_id, updated_at, organization_id),
        )
