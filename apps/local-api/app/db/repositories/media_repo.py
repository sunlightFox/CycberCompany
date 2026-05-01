from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

ASSET_UPDATE_COLUMNS = {
    "duration_ms",
    "width",
    "height",
    "frame_rate",
    "audio_streams",
    "video_streams",
    "sensitivity",
    "status",
    "metadata_json",
    "trace_id",
    "updated_at",
}

EDIT_PLAN_UPDATE_COLUMNS = {
    "status",
    "risk_level",
    "requires_approval",
    "artifact_id",
    "rendered_media_id",
    "evidence_json",
    "metadata_json",
    "trace_id",
    "updated_at",
}


class MediaRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_asset(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_assets (
              media_id, organization_id, task_id, source_artifact_id, media_type,
              display_name, uri, content_type, size_bytes, checksum, duration_ms,
              width, height, frame_rate, audio_streams, video_streams, sensitivity,
              status, metadata_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["media_id"],
                data["organization_id"],
                data["task_id"],
                data["source_artifact_id"],
                data["media_type"],
                data["display_name"],
                data["uri"],
                data.get("content_type"),
                data.get("size_bytes"),
                data.get("checksum"),
                data.get("duration_ms"),
                data.get("width"),
                data.get("height"),
                data.get("frame_rate"),
                data.get("audio_streams", 0),
                data.get("video_streams", 0),
                data.get("sensitivity", "low"),
                data.get("status", "ready"),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_asset(self, media_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM media_assets WHERE media_id = ?", (media_id,))
        return _asset_from_row(dict(row)) if row else None

    async def get_asset_by_source(self, source_artifact_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM media_assets
            WHERE source_artifact_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (source_artifact_id,),
        )
        return _asset_from_row(dict(row)) if row else None

    async def list_assets_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_assets
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_asset_from_row(dict(row)) for row in rows]

    async def update_asset(self, media_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "media_assets",
            "media_id",
            media_id,
            _json_update_fields(fields, {"metadata": "metadata_json"}),
            ASSET_UPDATE_COLUMNS,
        )

    async def insert_derivative(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_derivatives (
              derivative_id, media_id, organization_id, task_id, artifact_id,
              derivative_type, time_ms, metadata_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["derivative_id"],
                data["media_id"],
                data["organization_id"],
                data["task_id"],
                data["artifact_id"],
                data["derivative_type"],
                data.get("time_ms"),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_derivatives(self, media_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_derivatives
            WHERE media_id = ?
            ORDER BY created_at ASC
            """,
            (media_id,),
        )
        return [_derivative_from_row(dict(row)) for row in rows]

    async def list_derivatives_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_derivatives
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_derivative_from_row(dict(row)) for row in rows]

    async def insert_analysis(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_analysis (
              analysis_id, media_id, organization_id, task_id, analysis_type, status,
              model_route, segments_json, transcript_artifact_id, evidence_artifact_ids_json,
              metadata_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["analysis_id"],
                data["media_id"],
                data["organization_id"],
                data["task_id"],
                data["analysis_type"],
                data.get("status", "completed"),
                data.get("model_route"),
                _json(data.get("segments", [])),
                data.get("transcript_artifact_id"),
                _json(data.get("evidence_artifact_ids", [])),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_latest_analysis(
        self,
        media_id: str,
        analysis_type: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM media_analysis
            WHERE media_id = ? AND analysis_type = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (media_id, analysis_type),
        )
        return _analysis_from_row(dict(row)) if row else None

    async def list_analysis_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_analysis
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_analysis_from_row(dict(row)) for row in rows]

    async def insert_edit_plan(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_edit_plans (
              edit_plan_id, media_id, organization_id, task_id, goal, output_profile_json,
              operations_json, status, risk_level, requires_approval, artifact_id,
              rendered_media_id, evidence_json, metadata_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["edit_plan_id"],
                data["media_id"],
                data["organization_id"],
                data["task_id"],
                data["goal"],
                _json(data.get("output_profile", {})),
                _json(data.get("operations", [])),
                data.get("status", "planned"),
                data.get("risk_level", "R3"),
                1 if data.get("requires_approval", True) else 0,
                data.get("artifact_id"),
                data.get("rendered_media_id"),
                _json(data.get("evidence", {})),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_edit_plan(self, edit_plan_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM media_edit_plans WHERE edit_plan_id = ?",
            (edit_plan_id,),
        )
        return _edit_plan_from_row(dict(row)) if row else None

    async def list_edit_plans_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_edit_plans
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_edit_plan_from_row(dict(row)) for row in rows]

    async def update_edit_plan(self, edit_plan_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "media_edit_plans",
            "edit_plan_id",
            edit_plan_id,
            _json_update_fields(
                fields,
                {"evidence": "evidence_json", "metadata": "metadata_json"},
            ),
            EDIT_PLAN_UPDATE_COLUMNS,
        )

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


def _asset_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _derivative_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _analysis_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["segments"] = json.loads(row.pop("segments_json") or "[]")
    row["evidence_artifact_ids"] = json.loads(row.pop("evidence_artifact_ids_json") or "[]")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _edit_plan_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["output_profile"] = json.loads(row.pop("output_profile_json") or "{}")
    row["operations"] = json.loads(row.pop("operations_json") or "[]")
    row["requires_approval"] = bool(row.get("requires_approval"))
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_update_fields(fields: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in fields.items():
        column = mapping.get(key, key)
        if column.endswith("_json"):
            result[column] = _json(value)
        else:
            result[column] = value
    return result
