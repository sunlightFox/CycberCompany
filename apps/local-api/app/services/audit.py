from __future__ import annotations

import json
from typing import Any

from core_types import AuditEventListResponse, AuditEventResponse, RiskLevel
from trace_service import redact

from app.core.time import new_id, utc_now_iso
from app.db.session import Database


class AuditEventService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def write_event(
        self,
        *,
        actor_type: str,
        action: str,
        object_type: str,
        summary: str,
        risk_level: RiskLevel = RiskLevel.R0,
        actor_id: str | None = None,
        object_id: str | None = None,
        payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> str:
        audit_id = new_id("aud")
        await self._db.execute(
            """
            INSERT INTO audit_events (
              audit_id, actor_type, actor_id, action, object_type, object_id, risk_level,
              summary, payload_redacted_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                actor_type,
                actor_id,
                action,
                object_type,
                object_id,
                risk_level.value,
                summary,
                json.dumps(redact(payload or {}), ensure_ascii=False),
                trace_id,
                utc_now_iso(),
            ),
        )
        return audit_id

    async def list_events(self, limit: int = 50) -> AuditEventListResponse:
        rows = await self._db.fetch_all(
            """
            SELECT audit_id, actor_type, actor_id, action, object_type, object_id, risk_level,
                   summary, payload_redacted_json, trace_id, created_at
            FROM audit_events
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return AuditEventListResponse(
            items=[
                AuditEventResponse(
                    audit_id=row["audit_id"],
                    actor_type=row["actor_type"],
                    actor_id=row["actor_id"],
                    action=row["action"],
                    object_type=row["object_type"],
                    object_id=row["object_id"],
                    risk_level=row["risk_level"],
                    summary=row["summary"],
                    payload_redacted=json.loads(row["payload_redacted_json"]),
                    trace_id=row["trace_id"],
                    created_at=row["created_at"],
                )
                for row in rows
            ]
        )
