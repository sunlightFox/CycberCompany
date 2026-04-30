from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

PROFILE_UPDATE_COLUMNS = {
    "display_name",
    "status",
    "sensitivity",
    "allowed_domains",
    "allowed_domains_json",
    "blocked_domains",
    "blocked_domains_json",
    "policy",
    "policy_json",
    "metadata",
    "metadata_json",
    "updated_at",
    "revoked_at",
    "cleared_at",
    "expires_at",
}

SESSION_UPDATE_COLUMNS = {
    "asset_id",
    "status",
    "sensitivity",
    "session_metadata",
    "session_metadata_json",
    "secret_ref",
    "last_used_at",
    "updated_at",
    "expires_at",
    "revoked_at",
}


class BrowserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_profile(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_profiles (
              browser_profile_id, organization_id, display_name, profile_type,
              storage_backend, status, sensitivity, allowed_domains_json,
              blocked_domains_json, policy_json, metadata_json, created_by_member_id,
              trace_id, created_at, updated_at, revoked_at, cleared_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["browser_profile_id"],
                data["organization_id"],
                data["display_name"],
                data["profile_type"],
                data["storage_backend"],
                data["status"],
                data["sensitivity"],
                _json(data.get("allowed_domains", [])),
                _json(data.get("blocked_domains", [])),
                _json(data.get("policy", {})),
                _json(data.get("metadata", {})),
                data.get("created_by_member_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("revoked_at"),
                data.get("cleared_at"),
                data.get("expires_at"),
            ),
        )

    async def update_profile(self, browser_profile_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in PROFILE_UPDATE_COLUMNS},
            {
                "allowed_domains": "allowed_domains_json",
                "blocked_domains": "blocked_domains_json",
                "policy": "policy_json",
                "metadata": "metadata_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE browser_profiles SET {assignments} WHERE browser_profile_id = ?",
            (*values.values(), browser_profile_id),
        )

    async def get_profile(self, browser_profile_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM browser_profiles WHERE browser_profile_id = ?",
            (browser_profile_id,),
        )
        return _profile_from_row(dict(row)) if row else None

    async def list_profiles(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM browser_profiles
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_profile_from_row(dict(row)) for row in rows]

    async def insert_session(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_sessions (
              browser_session_id, organization_id, browser_profile_id, asset_id,
              login_domain, auth_type, status, sensitivity, session_metadata_json,
              secret_ref, created_by_member_id, trace_id, created_at, updated_at,
              last_used_at, expires_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["browser_session_id"],
                data["organization_id"],
                data["browser_profile_id"],
                data.get("asset_id"),
                data["login_domain"],
                data["auth_type"],
                data["status"],
                data["sensitivity"],
                _json(data.get("session_metadata", {})),
                data.get("secret_ref"),
                data.get("created_by_member_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("last_used_at"),
                data.get("expires_at"),
                data.get("revoked_at"),
            ),
        )

    async def update_session(self, browser_session_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in SESSION_UPDATE_COLUMNS},
            {"session_metadata": "session_metadata_json"},
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE browser_sessions SET {assignments} WHERE browser_session_id = ?",
            (*values.values(), browser_session_id),
        )

    async def get_session(self, browser_session_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM browser_sessions WHERE browser_session_id = ?",
            (browser_session_id,),
        )
        return _session_from_row(dict(row)) if row else None

    async def list_sessions(
        self,
        *,
        browser_profile_id: str | None = None,
        asset_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if browser_profile_id:
            where.append("browser_profile_id = ?")
            params.append(browser_profile_id)
        if asset_id:
            where.append("asset_id = ?")
            params.append(asset_id)
        if status:
            where.append("status = ?")
            params.append(status)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM browser_sessions
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_session_from_row(dict(row)) for row in rows]

    async def insert_profile_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_profile_events (
              event_id, organization_id, browser_profile_id, browser_session_id,
              event_type, payload_json, payload_redacted_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["organization_id"],
                data["browser_profile_id"],
                data.get("browser_session_id"),
                data["event_type"],
                _json(data.get("payload", {})),
                _json(data.get("payload_redacted", data.get("payload", {}))),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_profile_events(self, browser_profile_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM browser_profile_events
            WHERE browser_profile_id = ?
            ORDER BY created_at ASC
            """,
            (browser_profile_id,),
        )
        return [_profile_event_from_row(dict(row)) for row in rows]

    async def insert_evidence(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_evidence (
              browser_evidence_id, organization_id, task_id, tool_call_id,
              browser_profile_id, browser_session_id, action, action_status,
              url, title, http_status, evidence_summary, snapshot_preview,
              screenshot_artifact_id, download_artifact_id, artifact_ids_json,
              network_summary_json, console_summary_json, redaction_summary_json,
              safety_decision_json, untrusted_external_content, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["browser_evidence_id"],
                data["organization_id"],
                data.get("task_id"),
                data.get("tool_call_id"),
                data.get("browser_profile_id"),
                data.get("browser_session_id"),
                data["action"],
                data["action_status"],
                data.get("url"),
                data.get("title"),
                data.get("http_status"),
                data["evidence_summary"],
                data.get("snapshot_preview"),
                data.get("screenshot_artifact_id"),
                data.get("download_artifact_id"),
                _json(data.get("artifact_ids", [])),
                _json(data.get("network_summary", {})),
                _json(data.get("console_summary", {})),
                _json(data.get("redaction_summary", {})),
                _json(data.get("safety_decision", {})),
                1 if data.get("untrusted_external_content", True) else 0,
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_evidence(self, browser_evidence_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM browser_evidence WHERE browser_evidence_id = ?",
            (browser_evidence_id,),
        )
        return _evidence_from_row(dict(row)) if row else None

    async def list_evidence_for_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM browser_evidence
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_evidence_from_row(dict(row)) for row in rows]

    async def insert_network_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_network_events (
              network_event_id, browser_evidence_id, organization_id, request_url,
              method, status_code, resource_type, redaction_summary_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["network_event_id"],
                data["browser_evidence_id"],
                data["organization_id"],
                data["request_url"],
                data.get("method", "GET"),
                data.get("status_code"),
                data.get("resource_type"),
                _json(data.get("redaction_summary", {})),
                data["created_at"],
            ),
        )

    async def insert_console_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_console_events (
              console_event_id, browser_evidence_id, organization_id, level,
              message_preview, redaction_summary_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["console_event_id"],
                data["browser_evidence_id"],
                data["organization_id"],
                data["level"],
                data["message_preview"],
                _json(data.get("redaction_summary", {})),
                data["created_at"],
            ),
        )


def _profile_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allowed_domains"] = json.loads(row.pop("allowed_domains_json") or "[]")
    row["blocked_domains"] = json.loads(row.pop("blocked_domains_json") or "[]")
    row["policy"] = json.loads(row.pop("policy_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _session_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["session_metadata"] = json.loads(row.pop("session_metadata_json") or "{}")
    return row


def _profile_event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row.pop("payload_json", None)
    return row


def _evidence_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["artifact_ids"] = json.loads(row.pop("artifact_ids_json") or "[]")
    row["network_summary"] = json.loads(row.pop("network_summary_json") or "{}")
    row["console_summary"] = json.loads(row.pop("console_summary_json") or "{}")
    row["redaction_summary"] = json.loads(row.pop("redaction_summary_json") or "{}")
    row["safety_decision"] = json.loads(row.pop("safety_decision_json") or "{}")
    row["untrusted_external_content"] = bool(row["untrusted_external_content"])
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
