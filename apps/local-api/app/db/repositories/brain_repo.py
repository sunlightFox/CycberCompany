from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

JSON_COLUMNS = {"cost_policy_json", "privacy_policy_json", "verify_capabilities_json"}
BOOL_COLUMNS = {
    "is_local",
    "supports_tools",
    "supports_vision",
    "supports_audio",
    "allow_fallback",
    "allow_cloud",
    "streaming_supported",
    "supports_stream",
}
UPDATE_COLUMNS = {
    "display_name",
    "provider",
    "endpoint",
    "model_name",
    "api_key_ref",
    "is_local",
    "context_window",
    "supports_tools",
    "supports_vision",
    "supports_audio",
    "cost_policy_json",
    "privacy_policy_json",
    "status",
    "default_temperature",
    "default_top_p",
    "default_max_output_tokens",
    "timeout_seconds",
    "retry_count",
    "allow_fallback",
    "allow_cloud",
    "streaming_supported",
    "protocol_family",
    "request_format",
    "response_format",
    "supports_stream",
    "verify_capabilities_json",
    "last_verified_at",
    "last_error_code",
    "last_error_message",
    "latency_ms",
    "updated_at",
}


class BrainRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_brains(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM brains
            ORDER BY created_at ASC
            """
        )
        return [_brain_from_row(dict(row)) for row in rows]

    async def list_routable_brains(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM brains
            WHERE status IN ('configured', 'healthy')
              AND endpoint IS NOT NULL
              AND model_name IS NOT NULL
            ORDER BY is_local DESC, created_at ASC
            """
        )
        return [_brain_from_row(dict(row)) for row in rows]

    async def get_brain(self, brain_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM brains WHERE brain_id = ?", (brain_id,))
        return _brain_from_row(dict(row)) if row else None

    async def insert_brain(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO brains (
              brain_id, display_name, provider, endpoint, model_name, api_key_ref, is_local,
              context_window, supports_tools, supports_vision, supports_audio, cost_policy_json,
              privacy_policy_json, status, default_temperature, default_top_p,
              default_max_output_tokens, timeout_seconds, retry_count, allow_fallback,
              allow_cloud, streaming_supported, protocol_family, request_format,
              response_format, supports_stream, verify_capabilities_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["brain_id"],
                data["display_name"],
                data["provider"],
                data.get("endpoint"),
                data["model_name"],
                data.get("api_key_ref"),
                _bool(data.get("is_local", True)),
                data.get("context_window"),
                _bool(data.get("supports_tools", False)),
                _bool(data.get("supports_vision", False)),
                _bool(data.get("supports_audio", False)),
                json.dumps(data.get("cost_policy", {}), ensure_ascii=False),
                json.dumps(data.get("privacy_policy", {}), ensure_ascii=False),
                data["status"],
                data.get("default_temperature", 0.3),
                data.get("default_top_p", 0.9),
                data.get("default_max_output_tokens", 1024),
                data.get("timeout_seconds", 180),
                data.get("retry_count", 1),
                _bool(data.get("allow_fallback", True)),
                _bool(data.get("allow_cloud", False)),
                _bool(data.get("streaming_supported", True)),
                data.get("protocol_family", "auto"),
                data.get("request_format", "chat_completions"),
                data.get("response_format", "auto"),
                _bool(data.get("supports_stream", data.get("streaming_supported", True))),
                json.dumps(data.get("verify_capabilities", {}), ensure_ascii=False),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_brain(self, brain_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        sql_fields: dict[str, Any] = {}
        for key, value in fields.items():
            if key == "cost_policy":
                sql_fields["cost_policy_json"] = json.dumps(value, ensure_ascii=False)
            elif key == "privacy_policy":
                sql_fields["privacy_policy_json"] = json.dumps(value, ensure_ascii=False)
            elif key == "verify_capabilities":
                sql_fields["verify_capabilities_json"] = json.dumps(value, ensure_ascii=False)
            elif key in BOOL_COLUMNS:
                sql_fields[key] = _bool(value)
            else:
                sql_fields[key] = value
        unsupported = set(sql_fields) - UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported brains update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in sql_fields)
        await self._db.execute(
            f"UPDATE brains SET {assignments} WHERE brain_id = ?",
            (*sql_fields.values(), brain_id),
        )

    async def insert_secret_ref(
        self,
        *,
        secret_ref: str,
        kind: str,
        label: str,
        storage_uri: str,
        created_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO secret_refs (
              secret_ref, kind, label, storage_uri, organization_id, ref_uri, secret_type,
              provider, status, metadata_json, created_at, updated_at, rotated_at
            ) VALUES (?, ?, ?, ?, 'org_default', ?, ?, 'local', 'active', '{}', ?, ?, NULL)
            ON CONFLICT(secret_ref) DO UPDATE SET
              label = excluded.label,
              storage_uri = excluded.storage_uri,
              ref_uri = excluded.ref_uri,
              secret_type = excluded.secret_type,
              updated_at = excluded.updated_at,
              rotated_at = excluded.updated_at
            """,
            (secret_ref, kind, label, storage_uri, storage_uri, kind, created_at, created_at),
        )


def _brain_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["cost_policy"] = json.loads(row.pop("cost_policy_json") or "{}")
    row["privacy_policy"] = json.loads(row.pop("privacy_policy_json") or "{}")
    row["verify_capabilities"] = json.loads(row.pop("verify_capabilities_json", None) or "{}")
    for column in BOOL_COLUMNS:
        if column in row:
            row[column] = bool(row[column])
    row.setdefault("protocol_family", "auto")
    row.setdefault("request_format", "chat_completions")
    row.setdefault("response_format", "auto")
    row.setdefault("supports_stream", row.get("streaming_supported", True))
    row["has_api_key"] = bool(row.get("api_key_ref"))
    return row


def _bool(value: Any) -> int:
    return 1 if bool(value) else 0
