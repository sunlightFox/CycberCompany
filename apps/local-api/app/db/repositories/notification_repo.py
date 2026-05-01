from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

CHANNEL_UPDATE_COLUMNS = {
    "display_name",
    "status",
    "sensitivity",
    "policy_json",
    "provider_config_json",
    "last_health_status",
    "last_error",
    "updated_at",
}

MESSAGE_UPDATE_COLUMNS = {
    "status",
    "provider_message_id",
    "retry_count",
    "next_retry_at",
    "failure_reason",
    "metadata_json",
    "updated_at",
    "sent_at",
}


class NotificationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_channel(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO notification_channels (
              channel_id, organization_id, asset_id, provider, display_name,
              channel_type, status, sensitivity, policy_json, provider_config_json,
              last_health_status, last_error, created_by_member_id, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["channel_id"],
                data["organization_id"],
                data.get("asset_id"),
                data["provider"],
                data["display_name"],
                data["channel_type"],
                data["status"],
                data.get("sensitivity", "medium"),
                _json(data.get("policy", {})),
                _json(data.get("provider_config", {})),
                data.get("last_health_status"),
                data.get("last_error"),
                data.get("created_by_member_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_channel(self, channel_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {"policy": "policy_json", "provider_config": "provider_config_json"},
        )
        values = {key: value for key, value in values.items() if key in CHANNEL_UPDATE_COLUMNS}
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE notification_channels SET {assignments} WHERE channel_id = ?",
            (*values.values(), channel_id),
        )

    async def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM notification_channels WHERE channel_id = ?",
            (channel_id,),
        )
        return _channel_from_row(dict(row)) if row else None

    async def list_channels(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if status:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM notification_channels
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM notification_channels
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [_channel_from_row(dict(row)) for row in rows]

    async def insert_message(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO notification_messages (
              notification_id, organization_id, channel_id, task_id, scheduled_task_id,
              scheduled_run_id, approval_id, message_type, recipient, status,
              subject_redacted, body_redacted, dlp_summary_json, provider_message_id,
              retry_count, max_retries, next_retry_at, failure_reason, metadata_json,
              trace_id, created_at, updated_at, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["notification_id"],
                data["organization_id"],
                data["channel_id"],
                data.get("task_id"),
                data.get("scheduled_task_id"),
                data.get("scheduled_run_id"),
                data.get("approval_id"),
                data["message_type"],
                data["recipient"],
                data["status"],
                data.get("subject_redacted"),
                data["body_redacted"],
                _json(data.get("dlp_summary", {})),
                data.get("provider_message_id"),
                data.get("retry_count", 0),
                data.get("max_retries", 3),
                data.get("next_retry_at"),
                data.get("failure_reason"),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("sent_at"),
            ),
        )

    async def update_message(self, notification_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(fields, {"metadata": "metadata_json"})
        values = {key: value for key, value in values.items() if key in MESSAGE_UPDATE_COLUMNS}
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE notification_messages SET {assignments} WHERE notification_id = ?",
            (*values.values(), notification_id),
        )

    async def get_message(self, notification_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM notification_messages WHERE notification_id = ?",
            (notification_id,),
        )
        return _message_from_row(dict(row)) if row else None

    async def list_messages(
        self,
        *,
        channel_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if channel_id:
            where.append("channel_id = ?")
            params.append(channel_id)
        if status:
            where.append("status = ?")
            params.append(status)
        sql_where = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM notification_messages
            {sql_where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_message_from_row(dict(row)) for row in rows]

    async def list_retryable_messages(
        self,
        *,
        now: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM notification_messages
            WHERE status IN ('queued', 'failed')
              AND retry_count < max_retries
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY updated_at ASC, created_at ASC
            LIMIT ?
            """,
            (now, limit),
        )
        return [_message_from_row(dict(row)) for row in rows]

    async def insert_attempt(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO notification_delivery_attempts (
              attempt_id, organization_id, notification_id, channel_id, provider,
              attempt_index, status, request_summary_json, response_summary_json,
              error_code, error_summary, latency_ms, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["attempt_id"],
                data["organization_id"],
                data["notification_id"],
                data["channel_id"],
                data["provider"],
                data["attempt_index"],
                data["status"],
                _json(data.get("request_summary", {})),
                _json(data.get("response_summary", {})),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("latency_ms", 0),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_attempts(self, notification_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM notification_delivery_attempts
            WHERE notification_id = ?
            ORDER BY created_at ASC
            """,
            (notification_id,),
        )
        return [_attempt_from_row(dict(row)) for row in rows]

    async def insert_inbound(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO inbound_messages (
              inbound_message_id, organization_id, channel_id, sender_ref,
              provider_message_id, received_at, content_redacted, parsed_intent,
              binding_status, matched_approval_id, matched_task_id, action_result_json,
              risk_summary_json, untrusted_external_content, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["inbound_message_id"],
                data["organization_id"],
                data["channel_id"],
                data["sender_ref"],
                data.get("provider_message_id"),
                data["received_at"],
                data["content_redacted"],
                data["parsed_intent"],
                data["binding_status"],
                data.get("matched_approval_id"),
                data.get("matched_task_id"),
                _json(data.get("action_result", {})),
                _json(data.get("risk_summary", {})),
                1 if data.get("untrusted_external_content", True) else 0,
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_inbound(self, inbound_message_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM inbound_messages WHERE inbound_message_id = ?",
            (inbound_message_id,),
        )
        return _inbound_from_row(dict(row)) if row else None

    async def insert_inbound_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO inbound_message_events (
              event_id, inbound_message_id, organization_id, event_type,
              payload_json, payload_redacted_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["inbound_message_id"],
                data["organization_id"],
                data["event_type"],
                _json(data.get("payload", {})),
                _json(data.get("payload_redacted", data.get("payload", {}))),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def pending_approvals_for_channel(self, channel_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT DISTINCT a.*
            FROM approvals AS a
            JOIN notification_messages AS m ON m.approval_id = a.approval_id
            WHERE m.channel_id = ?
              AND m.message_type = 'approval_required'
              AND a.status = 'pending'
            ORDER BY a.created_at ASC
            """,
            (channel_id,),
        )
        return [_approval_from_row(dict(row)) for row in rows]


def _channel_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["policy"] = json.loads(row.pop("policy_json") or "{}")
    row["provider_config"] = json.loads(row.pop("provider_config_json") or "{}")
    return row


def _message_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["dlp_summary"] = json.loads(row.pop("dlp_summary_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _attempt_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["request_summary"] = json.loads(row.pop("request_summary_json") or "{}")
    row["response_summary"] = json.loads(row.pop("response_summary_json") or "{}")
    return row


def _inbound_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["action_result"] = json.loads(row.pop("action_result_json") or "{}")
    row["risk_summary"] = json.loads(row.pop("risk_summary_json") or "{}")
    row["untrusted_external_content"] = bool(row["untrusted_external_content"])
    return row


def _approval_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload_redacted"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row["options"] = json.loads(row.pop("options_json") or "[]")
    edited = row.pop("edited_payload_json", None)
    row["edited_payload"] = json.loads(edited) if edited else None
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
