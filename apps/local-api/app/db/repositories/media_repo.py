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
    "io_role",
    "source_kind",
    "privacy_level",
    "provider_status",
    "replay_summary_json",
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

IO_REQUEST_UPDATE_COLUMNS = {
    "status",
    "degraded_reason",
    "output_artifact_id",
    "summary_json",
    "evidence_json",
    "redaction_summary_json",
    "trace_id",
    "updated_at",
}

VIDEO_WORKFLOW_UPDATE_COLUMNS = {
    "status",
    "profile_json",
    "edit_plan_id",
    "approval_id",
    "result_json",
    "evidence_json",
    "trace_id",
    "updated_at",
}

VIDEO_WORKFLOW_STEP_UPDATE_COLUMNS = {
    "status",
    "attempt",
    "input_json",
    "output_json",
    "error_code",
    "error_summary",
    "evidence_json",
    "trace_id",
    "started_at",
    "completed_at",
    "updated_at",
}

VIDEO_WORKFLOW_BENCHMARK_UPDATE_COLUMNS = {
    "workflow_id",
    "task_id",
    "expected_result_json",
    "observed_result_json",
    "status",
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
              status, io_role, source_kind, privacy_level, provider_status,
              replay_summary_json, metadata_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                data.get("io_role", "input"),
                data.get("source_kind", "task_artifact"),
                data.get("privacy_level", "standard"),
                data.get("provider_status", "local"),
                _json(data.get("replay_summary", {})),
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

    async def insert_video_workflow(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_video_workflows (
              workflow_id, organization_id, task_id, media_id, goal, status,
              profile_json, edit_plan_id, approval_id, result_json, evidence_json,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["workflow_id"],
                data.get("organization_id", "org_default"),
                data["task_id"],
                data["media_id"],
                data["goal"],
                data.get("status", "planned"),
                _json(data.get("profile", {})),
                data.get("edit_plan_id"),
                data.get("approval_id"),
                _json(data.get("result", {})),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_video_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM media_video_workflows WHERE workflow_id = ?",
            (workflow_id,),
        )
        return _video_workflow_from_row(dict(row)) if row else None

    async def list_video_workflows_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_video_workflows
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_video_workflow_from_row(dict(row)) for row in rows]

    async def list_video_workflows_by_media(self, media_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_video_workflows
            WHERE media_id = ?
            ORDER BY created_at ASC
            """,
            (media_id,),
        )
        return [_video_workflow_from_row(dict(row)) for row in rows]

    async def update_video_workflow(self, workflow_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "media_video_workflows",
            "workflow_id",
            workflow_id,
            _json_update_fields(
                fields,
                {
                    "profile": "profile_json",
                    "result": "result_json",
                    "evidence": "evidence_json",
                },
            ),
            VIDEO_WORKFLOW_UPDATE_COLUMNS,
        )

    async def insert_video_workflow_step(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_video_workflow_steps (
              step_id, workflow_id, organization_id, task_id, media_id, step_key,
              status, attempt, input_json, output_json, error_code, error_summary,
              evidence_json, trace_id, started_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["step_id"],
                data["workflow_id"],
                data.get("organization_id", "org_default"),
                data["task_id"],
                data["media_id"],
                data["step_key"],
                data.get("status", "pending"),
                data.get("attempt", 1),
                _json(data.get("input", {})),
                _json(data.get("output", {})),
                data.get("error_code"),
                data.get("error_summary"),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_video_workflow_steps(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_video_workflow_steps
            WHERE workflow_id = ?
            ORDER BY created_at ASC
            """,
            (workflow_id,),
        )
        return [_video_workflow_step_from_row(dict(row)) for row in rows]

    async def update_video_workflow_step(self, step_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "media_video_workflow_steps",
            "step_id",
            step_id,
            _json_update_fields(
                fields,
                {
                    "input": "input_json",
                    "output": "output_json",
                    "evidence": "evidence_json",
                },
            ),
            VIDEO_WORKFLOW_STEP_UPDATE_COLUMNS,
        )

    async def insert_video_workflow_benchmark(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_video_workflow_benchmarks (
              benchmark_id, workflow_id, organization_id, task_id, scenario_key, layer,
              expected_result_json, observed_result_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["benchmark_id"],
                data.get("workflow_id"),
                data.get("organization_id", "org_default"),
                data.get("task_id"),
                data["scenario_key"],
                data["layer"],
                _json(data.get("expected_result", {})),
                _json(data.get("observed_result", {})),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_video_workflow_benchmarks_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_video_workflow_benchmarks
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_video_workflow_benchmark_from_row(dict(row)) for row in rows]

    async def insert_provider_health(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_provider_health_records (
              health_record_id, organization_id, provider_name, capability, provider_type,
              status, degraded_reason, evidence_json, redaction_summary_json, trace_id,
              checked_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["health_record_id"],
                data.get("organization_id", "org_default"),
                data["provider_name"],
                data["capability"],
                data.get("provider_type", "local"),
                data["status"],
                data.get("degraded_reason"),
                _json(data.get("evidence", {})),
                _json(data.get("redaction_summary", {})),
                data.get("trace_id"),
                data["checked_at"],
                data["created_at"],
            ),
        )

    async def list_provider_health(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_provider_health_records
            ORDER BY checked_at DESC
            LIMIT 50
            """
        )
        return [_provider_health_from_row(dict(row)) for row in rows]

    async def insert_io_request(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_io_requests (
              io_request_id, organization_id, task_id, media_id, operation, direction,
              provider_name, status, degraded_reason, input_artifact_id, output_artifact_id,
              summary_json, evidence_json, redaction_summary_json, idempotency_key,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["io_request_id"],
                data.get("organization_id", "org_default"),
                data.get("task_id"),
                data.get("media_id"),
                data["operation"],
                data["direction"],
                data["provider_name"],
                data["status"],
                data.get("degraded_reason"),
                data.get("input_artifact_id"),
                data.get("output_artifact_id"),
                _json(data.get("summary", {})),
                _json(data.get("evidence", {})),
                _json(data.get("redaction_summary", {})),
                data.get("idempotency_key"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_io_request(self, io_request_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "media_io_requests",
            "io_request_id",
            io_request_id,
            _json_update_fields(
                fields,
                {
                    "summary": "summary_json",
                    "evidence": "evidence_json",
                    "redaction_summary": "redaction_summary_json",
                },
            ),
            IO_REQUEST_UPDATE_COLUMNS,
        )

    async def get_io_request_by_idempotency(
        self,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM media_io_requests WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return _io_request_from_row(dict(row)) if row else None

    async def list_io_records(self, media_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_io_requests
            WHERE media_id = ?
            ORDER BY created_at ASC
            """,
            (media_id,),
        )
        return [_io_request_from_row(dict(row)) for row in rows]

    async def list_io_records_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_io_requests
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_io_request_from_row(dict(row)) for row in rows]

    async def insert_speech_transcript(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_speech_transcripts (
              transcript_id, io_request_id, organization_id, task_id, media_id, artifact_id,
              provider_name, language, status, transcript_preview, summary_text,
              confidence, evidence_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["transcript_id"],
                data["io_request_id"],
                data.get("organization_id", "org_default"),
                data["task_id"],
                data["media_id"],
                data.get("artifact_id"),
                data["provider_name"],
                data.get("language"),
                data["status"],
                data.get("transcript_preview", ""),
                data.get("summary_text", ""),
                data.get("confidence", 0),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_speech_transcripts_for_io_request(
        self,
        io_request_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_speech_transcripts
            WHERE io_request_id = ?
            ORDER BY created_at ASC
            """,
            (io_request_id,),
        )
        return [_speech_transcript_from_row(dict(row)) for row in rows]

    async def list_speech_transcripts_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_speech_transcripts
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_speech_transcript_from_row(dict(row)) for row in rows]

    async def insert_speech_render(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_speech_renders (
              render_id, io_request_id, organization_id, task_id, media_id, artifact_id,
              provider_name, voice, output_format, status, source_text_hash, duration_ms,
              evidence_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["render_id"],
                data["io_request_id"],
                data.get("organization_id", "org_default"),
                data["task_id"],
                data.get("media_id"),
                data.get("artifact_id"),
                data["provider_name"],
                data.get("voice"),
                data.get("output_format", "wav"),
                data["status"],
                data["source_text_hash"],
                data.get("duration_ms"),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_speech_renders_for_io_request(
        self,
        io_request_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_speech_renders
            WHERE io_request_id = ?
            ORDER BY created_at ASC
            """,
            (io_request_id,),
        )
        return [_speech_render_from_row(dict(row)) for row in rows]

    async def list_speech_renders_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_speech_renders
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_speech_render_from_row(dict(row)) for row in rows]

    async def insert_multimodal_summary(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_multimodal_summaries (
              summary_id, io_request_id, organization_id, task_id, media_id, provider_name,
              summary_type, status, summary_text, summary_json, evidence_artifact_ids_json,
              evidence_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["summary_id"],
                data["io_request_id"],
                data.get("organization_id", "org_default"),
                data["task_id"],
                data["media_id"],
                data["provider_name"],
                data["summary_type"],
                data["status"],
                data["summary_text"],
                _json(data.get("summary", {})),
                _json(data.get("evidence_artifact_ids", [])),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_multimodal_summaries_for_io_request(
        self,
        io_request_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_multimodal_summaries
            WHERE io_request_id = ?
            ORDER BY created_at ASC
            """,
            (io_request_id,),
        )
        return [_multimodal_summary_from_row(dict(row)) for row in rows]

    async def list_multimodal_summaries_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM media_multimodal_summaries
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_multimodal_summary_from_row(dict(row)) for row in rows]

    async def insert_chat_binding(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO media_chat_bindings (
              binding_id, organization_id, media_id, io_request_id, channel, conversation_id,
              turn_id, message_id, channel_event_id, channel_attachment_id, binding_type,
              status, evidence_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["binding_id"],
                data.get("organization_id", "org_default"),
                data.get("media_id"),
                data.get("io_request_id"),
                data.get("channel"),
                data.get("conversation_id"),
                data.get("turn_id"),
                data.get("message_id"),
                data.get("channel_event_id"),
                data.get("channel_attachment_id"),
                data["binding_type"],
                data["status"],
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_chat_bindings_by_task(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT b.*
            FROM media_chat_bindings b
            LEFT JOIN media_assets a ON a.media_id = b.media_id
            LEFT JOIN media_io_requests r ON r.io_request_id = b.io_request_id
            WHERE a.task_id = ? OR r.task_id = ?
            ORDER BY b.created_at ASC
            """,
            (task_id, task_id),
        )
        return [_chat_binding_from_row(dict(row)) for row in rows]

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
    row["replay_summary"] = json.loads(row.pop("replay_summary_json") or "{}")
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


def _provider_health_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    row["redaction_summary"] = json.loads(row.pop("redaction_summary_json") or "{}")
    return row


def _io_request_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["summary"] = json.loads(row.pop("summary_json") or "{}")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    row["redaction_summary"] = json.loads(row.pop("redaction_summary_json") or "{}")
    return row


def _speech_transcript_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _speech_render_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _multimodal_summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["summary"] = json.loads(row.pop("summary_json") or "{}")
    row["evidence_artifact_ids"] = json.loads(row.pop("evidence_artifact_ids_json") or "[]")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _chat_binding_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _video_workflow_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["profile"] = json.loads(row.pop("profile_json") or "{}")
    row["result"] = json.loads(row.pop("result_json") or "{}")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _video_workflow_step_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["input"] = json.loads(row.pop("input_json") or "{}")
    row["output"] = json.loads(row.pop("output_json") or "{}")
    row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
    return row


def _video_workflow_benchmark_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["expected_result"] = json.loads(row.pop("expected_result_json") or "{}")
    row["observed_result"] = json.loads(row.pop("observed_result_json") or "{}")
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
