from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from core_types import Trace, TraceSpan, TraceSpanStatus, TraceSpanType, TraceStatus

from trace_service.redaction import redact


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class TraceService:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def start_trace(
        self,
        *,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        task_id: str | None = None,
    ) -> str:
        trace_id = new_id("trc")
        now = utc_now().isoformat()
        await self._db.execute(
            """
            INSERT INTO traces (
              trace_id, conversation_id, turn_id, task_id, root_span_id, status,
              started_at, ended_at
            ) VALUES (?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (trace_id, conversation_id, turn_id, task_id, TraceStatus.RUNNING.value, now),
        )
        return trace_id

    async def start_span(
        self,
        trace_id: str,
        *,
        span_type: TraceSpanType | str,
        name: str,
        parent_span_id: str | None = None,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        span_id = new_id("spn")
        now = utc_now().isoformat()
        span_type_value = span_type.value if isinstance(span_type, TraceSpanType) else span_type
        await self._db.execute(
            """
            INSERT INTO trace_spans (
              span_id, trace_id, parent_span_id, span_type, name, input_json, output_json,
              metadata_json, started_at, ended_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?)
            """,
            (
                span_id,
                trace_id,
                parent_span_id,
                span_type_value,
                name,
                _json_or_none(redact(input_data)),
                _json(redact(metadata or {})),
                now,
                TraceSpanStatus.RUNNING.value,
            ),
        )
        await self._db.execute(
            "UPDATE traces SET root_span_id = COALESCE(root_span_id, ?) WHERE trace_id = ?",
            (span_id, trace_id),
        )
        return span_id

    async def end_span(
        self,
        span_id: str,
        *,
        status: TraceSpanStatus = TraceSpanStatus.COMPLETED,
        output_data: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        ended_at = utc_now().isoformat()
        row = await self._db.fetch_one(
            "SELECT started_at FROM trace_spans WHERE span_id = ?",
            (span_id,),
        )
        latency_ms = None
        if row is not None:
            started_at = datetime.fromisoformat(row["started_at"])
            ended_dt = datetime.fromisoformat(ended_at)
            latency_ms = max(0, int((ended_dt - started_at).total_seconds() * 1000))
        if error_code is None and output_data:
            value = output_data.get("error_code")
            error_code = str(value) if value else None
        await self._db.execute(
            """
            UPDATE trace_spans
            SET ended_at = ?, status = ?, output_json = ?, latency_ms = ?, error_code = ?
            WHERE span_id = ?
            """,
            (
                ended_at,
                status.value,
                _json_or_none(redact(output_data)),
                latency_ms,
                error_code,
                span_id,
            ),
        )

    async def end_trace(
        self,
        trace_id: str,
        *,
        status: TraceStatus = TraceStatus.COMPLETED,
    ) -> None:
        await self._db.execute(
            "UPDATE traces SET ended_at = ?, status = ? WHERE trace_id = ?",
            (utc_now().isoformat(), status.value, trace_id),
        )

    async def get_trace(self, trace_id: str) -> Trace | None:
        trace_row = await self._db.fetch_one("SELECT * FROM traces WHERE trace_id = ?", (trace_id,))
        if trace_row is None:
            return None
        span_rows = await self._db.fetch_all(
            "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY started_at ASC",
            (trace_id,),
        )
        spans = [
            TraceSpan(
                span_id=row["span_id"],
                trace_id=row["trace_id"],
                parent_span_id=row["parent_span_id"],
                span_type=row["span_type"],
                name=row["name"],
                input=_json_load(row["input_json"]),
                output=_json_load(row["output_json"]),
                metadata=_json_load(row["metadata_json"]) or {},
                started_at=datetime.fromisoformat(row["started_at"]),
                ended_at=_parse_datetime(row["ended_at"]),
                latency_ms=row["latency_ms"],
                error_code=row["error_code"],
                status=TraceSpanStatus(row["status"]),
            )
            for row in span_rows
        ]
        return Trace(
            trace_id=trace_row["trace_id"],
            conversation_id=trace_row["conversation_id"],
            turn_id=trace_row["turn_id"],
            task_id=trace_row["task_id"],
            root_span_id=trace_row["root_span_id"],
            status=TraceStatus(trace_row["status"]),
            started_at=datetime.fromisoformat(trace_row["started_at"]),
            ended_at=_parse_datetime(trace_row["ended_at"]),
            spans=spans,
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return _json(value)


def _json_load(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)
