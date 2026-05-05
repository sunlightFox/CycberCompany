from __future__ import annotations

from typing import Any

from app.db.session import Database


class MemberRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_members(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT member_id, organization_id, department_id, role_id, display_name, avatar_uri,
                   status, default_brain_id, persona_profile_id, heart_profile_json,
                   memory_policy_json, created_from_shell_id, created_from_template_id,
                   metadata_json, created_at, updated_at
            FROM members
            ORDER BY created_at ASC
            """
        )
        return [dict(row) for row in rows]

    async def get_member(self, member_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT member_id, organization_id, department_id, role_id, display_name, avatar_uri,
                   status, default_brain_id, persona_profile_id, heart_profile_json,
                   memory_policy_json, created_from_shell_id, created_from_template_id,
                   metadata_json, created_at, updated_at
            FROM members
            WHERE member_id = ?
            """,
            (member_id,),
        )
        return dict(row) if row else None

    async def get_member_by_persona_profile_id(self, persona_profile_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT member_id, organization_id, department_id, role_id, display_name, avatar_uri,
                   status, default_brain_id, persona_profile_id, heart_profile_json,
                   memory_policy_json, created_from_shell_id, created_from_template_id,
                   metadata_json, created_at, updated_at
            FROM members
            WHERE persona_profile_id = ?
            LIMIT 1
            """,
            (persona_profile_id,),
        )
        return dict(row) if row else None

    async def update_default_brain(
        self,
        *,
        member_id: str,
        brain_id: str,
        updated_at: str,
    ) -> None:
        await self._db.execute(
            """
            UPDATE members
            SET default_brain_id = ?, updated_at = ?
            WHERE member_id = ?
            """,
            (brain_id, updated_at, member_id),
        )
