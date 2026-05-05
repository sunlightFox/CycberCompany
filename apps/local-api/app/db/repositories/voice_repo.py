from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


PROFILE_UPDATE_COLUMNS = {
    "display_name",
    "provider_voice_id",
    "output_format",
    "sample_text",
    "sample_audio_uri",
    "config_json",
    "secret_ref",
    "status",
    "updated_at",
}

RENDER_UPDATE_COLUMNS = {
    "message_id",
    "status",
    "output_uri",
    "output_content_type",
    "output_size_bytes",
    "checksum",
    "provider_job_id",
    "provider_response_json",
    "degraded_reason",
    "updated_at",
    "completed_at",
}


class VoiceRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_profile(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO voice_profiles (
              voice_profile_id, organization_id, display_name, provider, provider_voice_id,
              output_format, sample_text, sample_audio_uri, config_json, secret_ref, status,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["voice_profile_id"],
                data.get("organization_id") or "org_default",
                data["display_name"],
                data["provider"],
                data["provider_voice_id"],
                data.get("output_format") or "wav",
                data.get("sample_text"),
                data.get("sample_audio_uri"),
                _json(data.get("config", {})),
                data.get("secret_ref"),
                data.get("status") or "active",
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_profiles(self, organization_id: str = "org_default") -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM voice_profiles
            WHERE organization_id = ?
            ORDER BY created_at ASC
            """,
            (organization_id,),
        )
        return [_profile_from_row(dict(row)) for row in rows]

    async def get_profile(self, voice_profile_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM voice_profiles WHERE voice_profile_id = ?",
            (voice_profile_id,),
        )
        return _profile_from_row(dict(row)) if row else None

    async def get_default_profile(
        self,
        *,
        organization_id: str = "org_default",
        provider: str = "edge",
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM voice_profiles
            WHERE organization_id = ?
              AND provider = ?
              AND status = 'active'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (organization_id, provider),
        )
        return _profile_from_row(dict(row)) if row else None

    async def update_profile(self, voice_profile_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "voice_profiles",
            "voice_profile_id",
            voice_profile_id,
            _json_update_fields(fields, {"config": "config_json"}),
            PROFILE_UPDATE_COLUMNS,
        )

    async def upsert_member_binding(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._db.execute(
            """
            UPDATE member_voice_bindings
            SET status = 'archived', updated_at = ?
            WHERE member_id = ? AND binding_scope = ? AND status = 'active'
            """,
            (
                data["updated_at"],
                data["member_id"],
                data.get("binding_scope") or "default",
            ),
        )
        await self._db.execute(
            """
            INSERT INTO member_voice_bindings (
              binding_id, organization_id, member_id, voice_profile_id, binding_scope,
              reply_mode, priority, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["binding_id"],
                data.get("organization_id") or "org_default",
                data["member_id"],
                data["voice_profile_id"],
                data.get("binding_scope") or "default",
                data.get("reply_mode") or "explicit_request_only",
                int(data.get("priority") or 0),
                data.get("status") or "active",
                data["created_at"],
                data["updated_at"],
            ),
        )
        row = await self.get_member_binding(data["member_id"], data.get("binding_scope") or "default")
        if row is None:
            raise RuntimeError("member voice binding upsert failed")
        return row

    async def get_member_binding(
        self,
        member_id: str,
        binding_scope: str = "default",
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT b.*, p.display_name AS voice_display_name, p.provider, p.provider_voice_id,
                   p.output_format, p.config_json, p.secret_ref
            FROM member_voice_bindings b
            JOIN voice_profiles p ON p.voice_profile_id = b.voice_profile_id
            WHERE b.member_id = ?
              AND b.binding_scope = ?
              AND b.status = 'active'
              AND p.status = 'active'
            ORDER BY b.priority DESC, b.created_at DESC
            LIMIT 1
            """,
            (member_id, binding_scope),
        )
        return _binding_from_row(dict(row)) if row else None

    async def insert_render_job(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO voice_render_jobs (
              render_job_id, organization_id, member_id, conversation_id, turn_id, message_id,
              voice_profile_id, provider, provider_voice_id, status, source_text_hash,
              source_text_preview, voice_style_plan_json, output_uri, output_content_type,
              output_size_bytes, checksum, provider_job_id, provider_response_json,
              degraded_reason, trace_id, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["render_job_id"],
                data.get("organization_id") or "org_default",
                data["member_id"],
                data.get("conversation_id"),
                data.get("turn_id"),
                data.get("message_id"),
                data["voice_profile_id"],
                data["provider"],
                data["provider_voice_id"],
                data["status"],
                data["source_text_hash"],
                data.get("source_text_preview") or "",
                _json(data.get("voice_style_plan", {})),
                data.get("output_uri"),
                data.get("output_content_type"),
                data.get("output_size_bytes"),
                data.get("checksum"),
                data.get("provider_job_id"),
                _json(data.get("provider_response", {})),
                data.get("degraded_reason"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("completed_at"),
            ),
        )

    async def update_render_job(self, render_job_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "voice_render_jobs",
            "render_job_id",
            render_job_id,
            _json_update_fields(fields, {"provider_response": "provider_response_json"}),
            RENDER_UPDATE_COLUMNS,
        )

    async def get_render_job(self, render_job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM voice_render_jobs WHERE render_job_id = ?",
            (render_job_id,),
        )
        return _render_from_row(dict(row)) if row else None

    async def list_render_jobs_for_turn(self, turn_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM voice_render_jobs
            WHERE turn_id = ?
            ORDER BY created_at ASC
            """,
            (turn_id,),
        )
        return [_render_from_row(dict(row)) for row in rows]

    async def _update(
        self,
        table: str,
        key_column: str,
        key_value: str,
        fields: dict[str, Any],
        allowed: set[str],
    ) -> None:
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        await self._db.execute(
            f"UPDATE {table} SET {assignments} WHERE {key_column} = ?",
            (*updates.values(), key_value),
        )


def _profile_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["config"] = json.loads(row.pop("config_json") or "{}")
    row["has_secret"] = bool(row.get("secret_ref"))
    return row


def _binding_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["voice_config"] = json.loads(row.pop("config_json") or "{}")
    row["has_secret"] = bool(row.get("secret_ref"))
    return row


def _render_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["voice_style_plan"] = json.loads(row.pop("voice_style_plan_json") or "{}")
    row["provider_response"] = json.loads(row.pop("provider_response_json") or "{}")
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_update_fields(fields: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in fields.items():
        column = mapping.get(key, key)
        result[column] = _json(value) if column.endswith("_json") else value
    return result
