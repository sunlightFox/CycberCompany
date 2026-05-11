from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class ChannelRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_bind_session(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO channel_bind_sessions (
              bind_session_id, organization_id, provider, requested_by_member_id,
              display_name_hint, status, qr_format, qr_payload_ref, qr_artifact_id,
              expires_at, confirmed_at, bound_asset_id, bound_channel_id,
              provider_account_ref_redacted, provider_state_ref, risk_level,
              policy_snapshot_json, provider_status_json, failure_reason,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["bind_session_id"],
                data["organization_id"],
                data["provider"],
                data["requested_by_member_id"],
                data.get("display_name_hint"),
                data["status"],
                data.get("qr_format"),
                data.get("qr_payload_ref"),
                data.get("qr_artifact_id"),
                data["expires_at"],
                data.get("confirmed_at"),
                data.get("bound_asset_id"),
                data.get("bound_channel_id"),
                data.get("provider_account_ref_redacted"),
                data.get("provider_state_ref"),
                data["risk_level"],
                _json(data.get("policy_snapshot", {})),
                _json(data.get("provider_status", {})),
                data.get("failure_reason"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_bind_session(self, bind_session_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "policy_snapshot": "policy_snapshot_json",
                "provider_status": "provider_status_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE channel_bind_sessions SET {assignments} WHERE bind_session_id = ?",
            (*values.values(), bind_session_id),
        )

    async def get_bind_session(self, bind_session_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM channel_bind_sessions WHERE bind_session_id = ?",
            (bind_session_id,),
        )
        return _bind_session_from_row(dict(row)) if row else None

    async def list_bind_session_events(self, bind_session_id: str) -> list[dict[str, Any]]:
        row = await self.get_bind_session(bind_session_id)
        if row is None:
            return []
        return [
            {
                "event": row["status"],
                "created_at": row["updated_at"],
                "failure_reason": row.get("failure_reason"),
            }
        ]

    async def insert_account(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO channel_accounts (
              channel_account_id, organization_id, asset_id, channel_id, bind_session_id,
              provider, account_ref_redacted, display_name, status, capabilities_json,
              provider_state_ref, policy_json, last_seen_at, last_verified_at,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["channel_account_id"],
                data["organization_id"],
                data["asset_id"],
                data.get("channel_id"),
                data.get("bind_session_id"),
                data["provider"],
                data["account_ref_redacted"],
                data["display_name"],
                data["status"],
                _json(data.get("capabilities", [])),
                data["provider_state_ref"],
                _json(data.get("policy", {})),
                data.get("last_seen_at"),
                data.get("last_verified_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_accounts(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if provider:
            where.append("provider = ?")
            params.append(provider)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM channel_accounts
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_account_from_row(dict(row)) for row in rows]

    async def get_account(self, channel_account_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM channel_accounts WHERE channel_account_id = ?",
            (channel_account_id,),
        )
        return _account_from_row(dict(row)) if row else None

    async def get_account_by_channel(self, channel_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM channel_accounts WHERE channel_id = ? ORDER BY created_at DESC LIMIT 1",
            (channel_id,),
        )
        return _account_from_row(dict(row)) if row else None

    async def get_account_by_provider_state_ref(
        self,
        provider_state_ref: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM channel_accounts
            WHERE provider_state_ref = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (provider_state_ref,),
        )
        return _account_from_row(dict(row)) if row else None

    async def update_account(self, channel_account_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {"capabilities": "capabilities_json", "policy": "policy_json"},
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE channel_accounts SET {assignments} WHERE channel_account_id = ?",
            (*values.values(), channel_account_id),
        )

    async def upsert_peer(self, data: dict[str, Any]) -> dict[str, Any]:
        existing = await self._db.fetch_one(
            """
            SELECT *
            FROM channel_peers
            WHERE channel_account_id = ? AND peer_ref_redacted = ?
            """,
            (data["channel_account_id"], data["peer_ref_redacted"]),
        )
        if existing:
            row = dict(existing)
            policy_update = bool(data.get("update_policy"))
            pairing_status = (
                data.get("pairing_status") if policy_update else row["pairing_status"]
            )
            allow_inbound = (
                1 if data.get("allow_inbound") else 0
            ) if policy_update else row["allow_inbound"]
            allow_outbound = (
                1 if data.get("allow_outbound") else 0
            ) if policy_update else row["allow_outbound"]
            await self._db.execute(
                """
                UPDATE channel_peers
                SET display_name_redacted = ?,
                    peer_type = ?,
                    pairing_status = ?,
                    allow_inbound = ?,
                    allow_outbound = ?,
                    metadata_json = ?,
                    updated_at = ?
                WHERE channel_peer_id = ?
                """,
                (
                    data.get("display_name_redacted"),
                    data["peer_type"],
                    pairing_status,
                    allow_inbound,
                    allow_outbound,
                    _json(data.get("metadata", {})),
                    data["updated_at"],
                    row["channel_peer_id"],
                ),
            )
            updated = await self._db.fetch_one(
                "SELECT * FROM channel_peers WHERE channel_peer_id = ?",
                (row["channel_peer_id"],),
            )
            return _peer_from_row(dict(updated)) if updated else _peer_from_row(row)
        await self._db.execute(
            """
            INSERT INTO channel_peers (
              channel_peer_id, organization_id, channel_account_id, provider,
              peer_ref_redacted, peer_type, display_name_redacted, pairing_status,
              allow_inbound, allow_outbound, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["channel_peer_id"],
                data["organization_id"],
                data["channel_account_id"],
                data["provider"],
                data["peer_ref_redacted"],
                data["peer_type"],
                data.get("display_name_redacted"),
                data["pairing_status"],
                1 if data.get("allow_inbound") else 0,
                1 if data.get("allow_outbound") else 0,
                _json(data.get("metadata", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )
        return data

    async def insert_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO channel_events (
              channel_event_id, organization_id, provider, channel_account_id, channel_id,
              event_type, provider_event_id_redacted, payload_redacted_json,
              normalized_event_json, status, trace_id, received_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["channel_event_id"],
                data["organization_id"],
                data["provider"],
                data.get("channel_account_id"),
                data.get("channel_id"),
                data["event_type"],
                data.get("provider_event_id_redacted"),
                _json(data.get("payload_redacted", {})),
                _json(data.get("normalized_event", {})),
                data["status"],
                data.get("trace_id"),
                data["received_at"],
                data["created_at"],
            ),
        )

    async def update_event(self, channel_event_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "payload_redacted": "payload_redacted_json",
                "normalized_event": "normalized_event_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE channel_events SET {assignments} WHERE channel_event_id = ?",
            (*values.values(), channel_event_id),
        )

    async def get_event_by_offset(
        self,
        *,
        channel_account_id: str,
        provider_event_id_redacted: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT ce.*
            FROM channel_event_offsets off
            LEFT JOIN channel_events ce ON ce.channel_event_id = off.channel_event_id
            WHERE off.channel_account_id = ? AND off.provider_event_id_redacted = ?
            """,
            (channel_account_id, provider_event_id_redacted),
        )
        return _event_from_row(dict(row)) if row and row["channel_event_id"] else None

    async def insert_event_offset(self, data: dict[str, Any]) -> bool:
        rowcount = await self._db.execute(
            """
            INSERT OR IGNORE INTO channel_event_offsets (
              offset_id, organization_id, channel_account_id, provider,
              provider_event_id_redacted, channel_event_id, status,
              received_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["offset_id"],
                data["organization_id"],
                data["channel_account_id"],
                data["provider"],
                data["provider_event_id_redacted"],
                data.get("channel_event_id"),
                data["status"],
                data["received_at"],
                data["created_at"],
                data["updated_at"],
            ),
        )
        return rowcount == 1

    async def update_event_offset(
        self,
        *,
        channel_account_id: str,
        provider_event_id_redacted: str,
        fields: dict[str, Any],
    ) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        await self._db.execute(
            f"""
            UPDATE channel_event_offsets
            SET {assignments}
            WHERE channel_account_id = ? AND provider_event_id_redacted = ?
            """,
            (*fields.values(), channel_account_id, provider_event_id_redacted),
        )

    async def get_peer_session(
        self,
        channel_peer_session_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM channel_peer_sessions WHERE channel_peer_session_id = ?",
            (channel_peer_session_id,),
        )
        return _peer_session_from_row(dict(row)) if row else None

    async def get_peer_session_by_peer_ref(
        self,
        *,
        channel_account_id: str,
        peer_ref_redacted: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM channel_peer_sessions
            WHERE channel_account_id = ? AND peer_ref_redacted = ?
            """,
            (channel_account_id, peer_ref_redacted),
        )
        return _peer_session_from_row(dict(row)) if row else None

    async def get_peer_session_by_conversation_id(
        self,
        *,
        channel_account_id: str,
        conversation_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM channel_peer_sessions
            WHERE channel_account_id = ? AND conversation_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (channel_account_id, conversation_id),
        )
        return _peer_session_from_row(dict(row)) if row else None

    async def list_peer_sessions(
        self,
        *,
        provider: str | None = None,
        pairing_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if provider:
            where.append("provider = ?")
            params.append(provider)
        if pairing_status:
            where.append("pairing_status = ?")
            params.append(pairing_status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM channel_peer_sessions
            {clause}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_peer_session_from_row(dict(row)) for row in rows]

    async def upsert_peer_session(self, data: dict[str, Any]) -> dict[str, Any]:
        existing = await self.get_peer_session_by_peer_ref(
            channel_account_id=data["channel_account_id"],
            peer_ref_redacted=data["peer_ref_redacted"],
        )
        if existing is None:
            await self._db.execute(
                """
                INSERT INTO channel_peer_sessions (
                  channel_peer_session_id, organization_id, channel_account_id,
                  channel_peer_id, channel_id, provider, peer_ref_redacted, peer_type,
                  conversation_id, session_id, member_id, peer_state_ref, pairing_status,
                  allow_inbound, allow_outbound, policy_snapshot_json,
                  last_inbound_at, last_outbound_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["channel_peer_session_id"],
                    data["organization_id"],
                    data["channel_account_id"],
                    data.get("channel_peer_id"),
                    data.get("channel_id"),
                    data["provider"],
                    data["peer_ref_redacted"],
                    data["peer_type"],
                    data.get("conversation_id"),
                    data["session_id"],
                    data["member_id"],
                    data.get("peer_state_ref"),
                    data["pairing_status"],
                    1 if data.get("allow_inbound") else 0,
                    1 if data.get("allow_outbound") else 0,
                    _json(data.get("policy_snapshot", {})),
                    data.get("last_inbound_at"),
                    data.get("last_outbound_at"),
                    data["created_at"],
                    data["updated_at"],
                ),
            )
            return data
        updates = {
            "channel_peer_id": data.get("channel_peer_id") or existing.get("channel_peer_id"),
            "channel_id": data.get("channel_id") or existing.get("channel_id"),
            "peer_type": data["peer_type"],
            "conversation_id": data.get("conversation_id") or existing.get("conversation_id"),
            "member_id": data.get("member_id") or existing.get("member_id"),
            "peer_state_ref": data.get("peer_state_ref") or existing.get("peer_state_ref"),
            "pairing_status": data.get("pairing_status") or existing.get("pairing_status"),
            "allow_inbound": data.get("allow_inbound", existing.get("allow_inbound")),
            "allow_outbound": data.get("allow_outbound", existing.get("allow_outbound")),
            "policy_snapshot": data.get("policy_snapshot", existing.get("policy_snapshot", {})),
            "last_inbound_at": data.get("last_inbound_at") or existing.get("last_inbound_at"),
            "last_outbound_at": data.get("last_outbound_at") or existing.get("last_outbound_at"),
            "updated_at": data["updated_at"],
        }
        await self.update_peer_session(existing["channel_peer_session_id"], updates)
        return await self.get_peer_session(existing["channel_peer_session_id"]) or existing

    async def update_peer_session(
        self,
        channel_peer_session_id: str,
        fields: dict[str, Any],
    ) -> None:
        values = _json_update_fields(fields, {"policy_snapshot": "policy_snapshot_json"})
        if "allow_inbound" in values:
            values["allow_inbound"] = 1 if values["allow_inbound"] else 0
        if "allow_outbound" in values:
            values["allow_outbound"] = 1 if values["allow_outbound"] else 0
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE channel_peer_sessions SET {assignments} WHERE channel_peer_session_id = ?",
            (*values.values(), channel_peer_session_id),
        )

    async def insert_pairing_request(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._db.execute(
            """
            INSERT OR IGNORE INTO channel_pairing_requests (
              pairing_request_id, organization_id, channel_account_id, channel_peer_id,
              provider, peer_ref_redacted, peer_type, display_name_redacted, peer_state_ref, status,
              requested_member_id, decision_by_member_id, decision_reason, expires_at,
              trace_id, created_at, updated_at, decided_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["pairing_request_id"],
                data["organization_id"],
                data["channel_account_id"],
                data.get("channel_peer_id"),
                data["provider"],
                data["peer_ref_redacted"],
                data["peer_type"],
                data.get("display_name_redacted"),
                data.get("peer_state_ref"),
                data["status"],
                data["requested_member_id"],
                data.get("decision_by_member_id"),
                data.get("decision_reason"),
                data.get("expires_at"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("decided_at"),
            ),
        )
        existing = await self.pending_pairing_request(
            channel_account_id=data["channel_account_id"],
            peer_ref_redacted=data["peer_ref_redacted"],
        )
        return existing or data

    async def pending_pairing_request(
        self,
        *,
        channel_account_id: str,
        peer_ref_redacted: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM channel_pairing_requests
            WHERE channel_account_id = ? AND peer_ref_redacted = ? AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (channel_account_id, peer_ref_redacted),
        )
        return _pairing_request_from_row(dict(row)) if row else None

    async def get_pairing_request(self, pairing_request_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM channel_pairing_requests WHERE pairing_request_id = ?",
            (pairing_request_id,),
        )
        return _pairing_request_from_row(dict(row)) if row else None

    async def list_pairing_requests(
        self,
        *,
        status: str | None = None,
        provider: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if provider:
            where.append("provider = ?")
            params.append(provider)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM channel_pairing_requests
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_pairing_request_from_row(dict(row)) for row in rows]

    async def update_pairing_request(
        self,
        pairing_request_id: str,
        fields: dict[str, Any],
    ) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        await self._db.execute(
            f"UPDATE channel_pairing_requests SET {assignments} WHERE pairing_request_id = ?",
            (*fields.values(), pairing_request_id),
        )

    async def insert_attachment(self, data: dict[str, Any]) -> None:
        if data.get("channel_event_id") and data.get("provider_attachment_ref_redacted"):
            rowcount = await self._db.execute(
                """
                INSERT INTO channel_attachments (
                  channel_attachment_id, organization_id, channel_event_id,
                  channel_account_id, channel_peer_session_id, provider,
                  provider_attachment_ref_redacted, attachment_type, display_name_redacted,
                  content_type, size_bytes, artifact_id, blob_ref, media_id, status,
                  failure_reason, metadata_json, trace_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel_event_id, provider_attachment_ref_redacted)
                WHERE provider_attachment_ref_redacted IS NOT NULL
                DO UPDATE SET
                  channel_peer_session_id = excluded.channel_peer_session_id,
                  attachment_type = excluded.attachment_type,
                  display_name_redacted = excluded.display_name_redacted,
                  content_type = excluded.content_type,
                  size_bytes = excluded.size_bytes,
                  artifact_id = excluded.artifact_id,
                  blob_ref = excluded.blob_ref,
                  media_id = excluded.media_id,
                  status = excluded.status,
                  failure_reason = excluded.failure_reason,
                  metadata_json = excluded.metadata_json,
                  trace_id = excluded.trace_id,
                  updated_at = excluded.updated_at
                """,
                _attachment_insert_values(data),
            )
            if rowcount >= 0:
                return
            existing = await self.get_attachment_by_provider_ref(
                channel_event_id=data["channel_event_id"],
                provider_attachment_ref_redacted=data["provider_attachment_ref_redacted"],
            )
            if existing is not None:
                await self.update_attachment(
                    existing["channel_attachment_id"],
                    _attachment_update_fields(data),
                )
                return
        await self._db.execute(
            """
            INSERT INTO channel_attachments (
              channel_attachment_id, organization_id, channel_event_id,
              channel_account_id, channel_peer_session_id, provider,
              provider_attachment_ref_redacted, attachment_type, display_name_redacted,
              content_type, size_bytes, artifact_id, blob_ref, media_id, status,
              failure_reason, metadata_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _attachment_insert_values(data),
        )

    async def update_attachment(
        self,
        channel_attachment_id: str,
        fields: dict[str, Any],
    ) -> None:
        values = _json_update_fields(fields, {"metadata": "metadata_json"})
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE channel_attachments SET {assignments} WHERE channel_attachment_id = ?",
            (*values.values(), channel_attachment_id),
        )

    async def list_attachments(
        self,
        *,
        channel_event_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if channel_event_id:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM channel_attachments
                WHERE channel_event_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (channel_event_id, limit),
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM channel_attachments
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [_attachment_from_row(dict(row)) for row in rows]

    async def get_attachment_by_provider_ref(
        self,
        *,
        channel_event_id: str,
        provider_attachment_ref_redacted: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM channel_attachments
            WHERE channel_event_id = ? AND provider_attachment_ref_redacted = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (channel_event_id, provider_attachment_ref_redacted),
        )
        return _attachment_from_row(dict(row)) if row else None

    async def insert_delivery_binding(self, data: dict[str, Any]) -> bool:
        rowcount = await self._db.execute(
            """
            INSERT OR IGNORE INTO channel_delivery_bindings (
              channel_delivery_binding_id, organization_id, channel_account_id,
              channel_peer_session_id, channel_event_id, turn_id, message_id,
              notification_id, provider, provider_message_id_redacted, status,
              attempts, failure_reason, trace_id, created_at, updated_at, sent_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["channel_delivery_binding_id"],
                data["organization_id"],
                data["channel_account_id"],
                data.get("channel_peer_session_id"),
                data.get("channel_event_id"),
                data.get("turn_id"),
                data.get("message_id"),
                data.get("notification_id"),
                data["provider"],
                data.get("provider_message_id_redacted"),
                data["status"],
                data.get("attempts", 0),
                data.get("failure_reason"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("sent_at"),
            ),
        )
        return rowcount == 1

    async def update_delivery_binding(
        self,
        channel_delivery_binding_id: str,
        fields: dict[str, Any],
    ) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{column} = ?" for column in fields)
        await self._db.execute(
            f"""
            UPDATE channel_delivery_bindings
            SET {assignments}
            WHERE channel_delivery_binding_id = ?
            """,
            (*fields.values(), channel_delivery_binding_id),
        )

    async def upsert_feishu_connection(self, data: dict[str, Any]) -> dict[str, Any]:
        existing = await self._db.fetch_one(
            """
            SELECT *
            FROM feishu_connections
            WHERE channel_account_id = ?
            """,
            (data["channel_account_id"],),
        )
        fields = {
            "channel_id": data.get("channel_id"),
            "app_id_redacted": data["app_id_redacted"],
            "tenant_key_redacted": data.get("tenant_key_redacted"),
            "bot_open_id_redacted": data.get("bot_open_id_redacted"),
            "transport_mode": data.get("transport_mode", "websocket"),
            "status": data.get("status", "configured"),
            "connection_state": data.get("connection_state", "disconnected"),
            "permission_snapshot": data.get("permission_snapshot", {}),
            "capability_snapshot": data.get("capability_snapshot", {}),
            "last_event_id_redacted": data.get("last_event_id_redacted"),
            "last_heartbeat_at": data.get("last_heartbeat_at"),
            "last_connected_at": data.get("last_connected_at"),
            "last_disconnected_at": data.get("last_disconnected_at"),
            "last_error_code": data.get("last_error_code"),
            "last_error_summary": data.get("last_error_summary"),
            "trace_id": data.get("trace_id"),
            "updated_at": data["updated_at"],
        }
        if existing:
            values = _json_update_fields(
                fields,
                {
                    "permission_snapshot": "permission_snapshot_json",
                    "capability_snapshot": "capability_snapshot_json",
                },
            )
            assignments = ", ".join(f"{column} = ?" for column in values)
            await self._db.execute(
                f"UPDATE feishu_connections SET {assignments} WHERE channel_account_id = ?",
                (*values.values(), data["channel_account_id"]),
            )
            updated = await self.get_feishu_connection_by_account(data["channel_account_id"])
            return updated or _feishu_connection_from_row(dict(existing))
        await self._db.execute(
            """
            INSERT INTO feishu_connections (
              feishu_connection_id, organization_id, channel_account_id, channel_id,
              app_id_redacted, tenant_key_redacted, bot_open_id_redacted, transport_mode,
              status, connection_state, permission_snapshot_json, capability_snapshot_json,
              last_event_id_redacted, last_heartbeat_at, last_connected_at,
              last_disconnected_at, last_error_code, last_error_summary,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["feishu_connection_id"],
                data["organization_id"],
                data["channel_account_id"],
                data.get("channel_id"),
                data["app_id_redacted"],
                data.get("tenant_key_redacted"),
                data.get("bot_open_id_redacted"),
                data.get("transport_mode", "websocket"),
                data.get("status", "configured"),
                data.get("connection_state", "disconnected"),
                _json(data.get("permission_snapshot", {})),
                _json(data.get("capability_snapshot", {})),
                data.get("last_event_id_redacted"),
                data.get("last_heartbeat_at"),
                data.get("last_connected_at"),
                data.get("last_disconnected_at"),
                data.get("last_error_code"),
                data.get("last_error_summary"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )
        return data

    async def get_feishu_connection_by_account(
        self,
        channel_account_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM feishu_connections WHERE channel_account_id = ?",
            (channel_account_id,),
        )
        return _feishu_connection_from_row(dict(row)) if row else None

    async def list_feishu_connections(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if status:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM feishu_connections
                WHERE status = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            rows = await self._db.fetch_all(
                """
                SELECT *
                FROM feishu_connections
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [_feishu_connection_from_row(dict(row)) for row in rows]

    async def update_feishu_connection(
        self,
        channel_account_id: str,
        fields: dict[str, Any],
    ) -> None:
        values = _json_update_fields(
            fields,
            {
                "permission_snapshot": "permission_snapshot_json",
                "capability_snapshot": "capability_snapshot_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE feishu_connections SET {assignments} WHERE channel_account_id = ?",
            (*values.values(), channel_account_id),
        )

    async def insert_feishu_event_record(self, data: dict[str, Any]) -> bool:
        rowcount = await self._db.execute(
            """
            INSERT OR IGNORE INTO feishu_event_records (
              feishu_event_record_id, organization_id, channel_account_id,
              channel_event_id, provider_event_id_redacted, event_type, message_type,
              chat_id_redacted, sender_id_redacted, message_id_redacted,
              payload_redacted_json, normalized_event_json, status, trace_id,
              received_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["feishu_event_record_id"],
                data["organization_id"],
                data["channel_account_id"],
                data.get("channel_event_id"),
                data["provider_event_id_redacted"],
                data["event_type"],
                data.get("message_type"),
                data.get("chat_id_redacted"),
                data.get("sender_id_redacted"),
                data.get("message_id_redacted"),
                _json(data.get("payload_redacted", {})),
                _json(data.get("normalized_event", {})),
                data["status"],
                data.get("trace_id"),
                data["received_at"],
                data["created_at"],
                data["updated_at"],
            ),
        )
        return rowcount == 1

    async def insert_feishu_message_operation(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO feishu_message_operations (
              feishu_operation_id, organization_id, channel_account_id, channel_id,
              provider_message_id_redacted, operation, request_summary_json,
              response_summary_json, status, error_code, error_summary,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["feishu_operation_id"],
                data["organization_id"],
                data["channel_account_id"],
                data.get("channel_id"),
                data.get("provider_message_id_redacted"),
                data["operation"],
                _json(data.get("request_summary", {})),
                _json(data.get("response_summary", {})),
                data["status"],
                data.get("error_code"),
                data.get("error_summary"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_feishu_message_operations(
        self,
        *,
        channel_account_id: str | None = None,
        operation: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if channel_account_id:
            where.append("channel_account_id = ?")
            params.append(channel_account_id)
        if operation:
            where.append("operation = ?")
            params.append(operation)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM feishu_message_operations
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_feishu_operation_from_row(dict(row)) for row in rows]

    async def get_delivery_binding_by_turn(
        self,
        *,
        turn_id: str,
        channel_peer_session_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM channel_delivery_bindings
            WHERE turn_id = ? AND channel_peer_session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (turn_id, channel_peer_session_id),
        )
        return _delivery_binding_from_row(dict(row)) if row else None

    async def get_delivery_binding(
        self,
        channel_delivery_binding_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM channel_delivery_bindings
            WHERE channel_delivery_binding_id = ?
            LIMIT 1
            """,
            (channel_delivery_binding_id,),
        )
        return _delivery_binding_from_row(dict(row)) if row else None

    async def list_delivery_bindings(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        turn_id: str | None = None,
        channel_event_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if provider:
            where.append("provider = ?")
            params.append(provider)
        if status:
            where.append("status = ?")
            params.append(status)
        if turn_id:
            where.append("turn_id = ?")
            params.append(turn_id)
        if channel_event_id:
            where.append("channel_event_id = ?")
            params.append(channel_event_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM channel_delivery_bindings
            {clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_delivery_binding_from_row(dict(row)) for row in rows]

    async def count_pending_pairing_requests(self, *, provider: str = "wechat") -> int:
        row = await self._db.fetch_one(
            """
            SELECT COUNT(*) AS count
            FROM channel_pairing_requests
            WHERE provider = ? AND status = 'pending'
            """,
            (provider,),
        )
        return int(row["count"] if row else 0)

    async def count_delivery_bindings(self, *, provider: str = "wechat", status: str) -> int:
        row = await self._db.fetch_one(
            """
            SELECT COUNT(*) AS count
            FROM channel_delivery_bindings
            WHERE provider = ? AND status = ?
            """,
            (provider, status),
        )
        return int(row["count"] if row else 0)

    async def list_events(
        self,
        *,
        provider: str | None = None,
        status: str | None = None,
        channel_event_id: str | None = None,
        trace_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if provider:
            where.append("provider = ?")
            params.append(provider)
        if status:
            where.append("status = ?")
            params.append(status)
        if channel_event_id:
            where.append("channel_event_id = ?")
            params.append(channel_event_id)
        if trace_id:
            where.append("trace_id = ?")
            params.append(trace_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM channel_events
            {clause}
            ORDER BY received_at DESC, created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_event_from_row(dict(row)) for row in rows]

    async def raw_execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        return await self._db.execute(sql, params)


def _bind_session_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json") or "{}")
    row["provider_status"] = json.loads(row.pop("provider_status_json") or "{}")
    return row


def _account_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["capabilities"] = json.loads(row.pop("capabilities_json") or "[]")
    row["policy"] = json.loads(row.pop("policy_json") or "{}")
    return row


def _peer_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allow_inbound"] = bool(row["allow_inbound"])
    row["allow_outbound"] = bool(row["allow_outbound"])
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload_redacted"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row["normalized_event"] = json.loads(row.pop("normalized_event_json") or "{}")
    return row


def _peer_session_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allow_inbound"] = bool(row["allow_inbound"])
    row["allow_outbound"] = bool(row["allow_outbound"])
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json") or "{}")
    return row


def _pairing_request_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return row


def _attachment_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    row["metadata"].setdefault(
        "understanding_status",
        _attachment_understanding_status_fallback(row),
    )
    row["metadata"].setdefault(
        "memory_candidate_ids",
        _attachment_memory_candidate_ids_fallback(row),
    )
    row["metadata"].setdefault("memory_ids", [])
    return row


def _attachment_insert_values(data: dict[str, Any]) -> tuple[Any, ...]:
    return (
        data["channel_attachment_id"],
        data["organization_id"],
        data.get("channel_event_id"),
        data["channel_account_id"],
        data.get("channel_peer_session_id"),
        data["provider"],
        data.get("provider_attachment_ref_redacted"),
        data["attachment_type"],
        data.get("display_name_redacted"),
        data.get("content_type"),
        data.get("size_bytes"),
        data.get("artifact_id"),
        data.get("blob_ref"),
        data.get("media_id"),
        data["status"],
        data.get("failure_reason"),
        _json(data.get("metadata", {})),
        data.get("trace_id"),
        data["created_at"],
        data["updated_at"],
    )


def _attachment_update_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "attachment_type": data.get("attachment_type"),
            "display_name_redacted": data.get("display_name_redacted"),
            "content_type": data.get("content_type"),
            "size_bytes": data.get("size_bytes"),
            "artifact_id": data.get("artifact_id"),
            "blob_ref": data.get("blob_ref"),
            "media_id": data.get("media_id"),
            "status": data.get("status"),
            "failure_reason": data.get("failure_reason"),
            "metadata": data.get("metadata", {}),
            "updated_at": data.get("updated_at"),
        }.items()
        if value is not None
    }


def _delivery_binding_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return row


def _feishu_connection_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["permission_snapshot"] = json.loads(row.pop("permission_snapshot_json") or "{}")
    row["capability_snapshot"] = json.loads(row.pop("capability_snapshot_json") or "{}")
    return row


def _feishu_operation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["request_summary"] = json.loads(row.pop("request_summary_json") or "{}")
    row["response_summary"] = json.loads(row.pop("response_summary_json") or "{}")
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _attachment_understanding_status_fallback(row: dict[str, Any]) -> str:
    metadata = dict(row.get("metadata") or {})
    if metadata.get("understanding_status"):
        return str(metadata["understanding_status"])
    attachment_type = str(row.get("attachment_type") or "")
    content_type = str(row.get("content_type") or "").lower()
    display_name = str(row.get("display_name_redacted") or "").lower()
    if attachment_type == "image":
        return "degraded"
    if attachment_type == "audio":
        return "degraded"
    if attachment_type == "file":
        if any(
            marker in content_type or marker in display_name
            for marker in [
                "zip",
                "rar",
                "7z",
                "tar",
                "gzip",
                ".zip",
                ".rar",
                ".7z",
            ]
        ):
            return "degraded"
        return "understood"
    return "degraded"


def _attachment_memory_candidate_ids_fallback(row: dict[str, Any]) -> list[str]:
    metadata = dict(row.get("metadata") or {})
    if metadata.get("memory_candidate_ids"):
        return list(metadata["memory_candidate_ids"])
    if _attachment_understanding_status_fallback(row) != "understood":
        return []
    attachment_type = str(row.get("attachment_type") or "")
    if attachment_type != "file":
        return []
    content_type = str(row.get("content_type") or "").lower()
    display_name = str(row.get("display_name_redacted") or "").lower()
    if any(
        marker in content_type or marker in display_name
        for marker in [
            "zip",
            "rar",
            "7z",
            "tar",
            "gzip",
            ".zip",
            ".rar",
            ".7z",
        ]
    ):
        return []
    channel_attachment_id = str(row.get("channel_attachment_id") or "")
    return [f"memcand:{channel_attachment_id}:fallback"] if channel_attachment_id else []
