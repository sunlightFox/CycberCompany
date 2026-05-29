from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.db.session import Database

GOAL_UPDATE_COLUMNS = {
    "title",
    "description",
    "domain_label",
    "status",
    "success_criteria",
    "success_criteria_json",
    "constraints",
    "constraints_json",
    "motivation",
    "motivation_json",
    "active_plan_id",
    "archived_at",
    "cancelled_at",
    "completed_at",
    "updated_at",
}

PLAN_UPDATE_COLUMNS = {"status", "summary", "assumptions", "risk_notes", "updated_at"}
PLAN_ITEM_UPDATE_COLUMNS = {"status", "updated_at"}
POLICY_UPDATE_COLUMNS = {
    "status",
    "mode",
    "frequency",
    "quiet_hours",
    "tone_policy",
    "next_checkin_at",
    "scheduled_task_id",
    "updated_at",
}
CHECKIN_UPDATE_COLUMNS = {
    "user_reply_text_redacted",
    "parsed_status",
    "progress_delta",
    "advice",
    "encouragement_text",
    "trace_id",
    "replied_at",
}
INTAKE_UPDATE_COLUMNS = {
    "domain_label",
    "status",
    "current_level",
    "target_level",
    "target_date",
    "available_time",
    "constraints",
    "motivation",
    "missing_fields",
    "raw_answers",
    "confirmed_at",
    "updated_at",
}
MILESTONE_UPDATE_COLUMNS = {"status", "target_date", "updated_at"}
ROUTINE_UPDATE_COLUMNS = {"status", "updated_at"}
INTERVENTION_UPDATE_COLUMNS = {
    "status",
    "shown_at",
    "user_feedback",
    "updated_at",
}


class GoalRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._db.transaction():
            yield

    async def insert_goal(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goals (
              goal_id, organization_id, owner_member_id, conversation_id, title,
              description, domain_label, status, success_criteria_json,
              constraints_json, motivation_json, active_plan_id, created_from_turn_id,
              trace_id, archived_at, cancelled_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["goal_id"],
                data.get("organization_id") or "org_default",
                data["owner_member_id"],
                data.get("conversation_id"),
                data["title"],
                data.get("description") or "",
                data.get("domain_label") or "general",
                data["status"],
                _json(data.get("success_criteria", [])),
                _json(data.get("constraints", {})),
                _json(data.get("motivation", {})),
                data.get("active_plan_id"),
                data.get("created_from_turn_id"),
                data.get("trace_id"),
                data.get("archived_at"),
                data.get("cancelled_at"),
                data.get("completed_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_goal(self, goal_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in GOAL_UPDATE_COLUMNS},
            {
                "success_criteria": "success_criteria_json",
                "constraints": "constraints_json",
                "motivation": "motivation_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goals SET {assignments} WHERE goal_id = ?",
            (*values.values(), goal_id),
        )

    async def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM goals WHERE goal_id = ?", (goal_id,))
        return _goal_from_row(dict(row)) if row else None

    async def list_goals(
        self,
        *,
        owner_member_id: str | None = None,
        conversation_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if owner_member_id:
            where.append("owner_member_id = ?")
            params.append(owner_member_id)
        if conversation_id:
            where.append("conversation_id = ?")
            params.append(conversation_id)
        if status:
            where.append("status = ?")
            params.append(status)
        else:
            where.append("status != 'archived'")
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM goals
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_goal_from_row(dict(row)) for row in rows]

    async def insert_plan(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_plans (
              goal_plan_id, goal_id, version, status, summary,
              assumptions_json, risk_notes_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["goal_plan_id"],
                data["goal_id"],
                int(data.get("version") or 1),
                data["status"],
                data["summary"],
                _json(data.get("assumptions", [])),
                _json(data.get("risk_notes", [])),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_plan(self, goal_plan_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in PLAN_UPDATE_COLUMNS},
            {"assumptions": "assumptions_json", "risk_notes": "risk_notes_json"},
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goal_plans SET {assignments} WHERE goal_plan_id = ?",
            (*values.values(), goal_plan_id),
        )

    async def get_plan(self, goal_plan_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM goal_plans WHERE goal_plan_id = ?",
            (goal_plan_id,),
        )
        return _plan_from_row(dict(row)) if row else None

    async def list_plans(self, goal_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM goal_plans
            WHERE goal_id = ?
            ORDER BY version DESC, created_at DESC
            """,
            (goal_id,),
        )
        return [_plan_from_row(dict(row)) for row in rows]

    async def insert_plan_item(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_plan_items (
              goal_plan_item_id, goal_plan_id, goal_id, title, description,
              item_type, cadence_json, success_metric_json, status, sort_order,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["goal_plan_item_id"],
                data["goal_plan_id"],
                data["goal_id"],
                data["title"],
                data.get("description") or "",
                data.get("item_type") or "routine",
                _json(data.get("cadence", {})),
                _json(data.get("success_metric", {})),
                data.get("status") or "planned",
                int(data.get("sort_order") or 0),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_plan_items(self, goal_plan_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM goal_plan_items
            WHERE goal_plan_id = ?
            ORDER BY sort_order ASC, created_at ASC
            """,
            (goal_plan_id,),
        )
        return [_plan_item_from_row(dict(row)) for row in rows]

    async def update_plan_item(self, goal_plan_item_id: str, fields: dict[str, Any]) -> None:
        values = {
            key: value for key, value in fields.items() if key in PLAN_ITEM_UPDATE_COLUMNS
        }
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goal_plan_items SET {assignments} WHERE goal_plan_item_id = ?",
            (*values.values(), goal_plan_item_id),
        )

    async def insert_policy(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_supervision_policies (
              policy_id, goal_id, status, mode, frequency_json, quiet_hours_json,
              tone_policy_json, next_checkin_at, scheduled_task_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["policy_id"],
                data["goal_id"],
                data["status"],
                data.get("mode") or "scheduled_checkin",
                _json(data.get("frequency", {})),
                _json(data.get("quiet_hours", {})),
                _json(data.get("tone_policy", {})),
                data.get("next_checkin_at"),
                data.get("scheduled_task_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_policy(self, policy_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in POLICY_UPDATE_COLUMNS},
            {
                "frequency": "frequency_json",
                "quiet_hours": "quiet_hours_json",
                "tone_policy": "tone_policy_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goal_supervision_policies SET {assignments} WHERE policy_id = ?",
            (*values.values(), policy_id),
        )

    async def get_policy(self, policy_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM goal_supervision_policies WHERE policy_id = ?",
            (policy_id,),
        )
        return _policy_from_row(dict(row)) if row else None

    async def latest_policy_for_goal(self, goal_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM goal_supervision_policies
            WHERE goal_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (goal_id,),
        )
        return _policy_from_row(dict(row)) if row else None

    async def insert_checkin(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_checkins (
              checkin_id, goal_id, policy_id, scheduled_task_id, scheduled_run_id,
              prompt_text, user_reply_text_redacted, parsed_status, progress_delta_json,
              advice_json, encouragement_text, trace_id, created_at, replied_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["checkin_id"],
                data["goal_id"],
                data.get("policy_id"),
                data.get("scheduled_task_id"),
                data.get("scheduled_run_id"),
                data["prompt_text"],
                data.get("user_reply_text_redacted"),
                data.get("parsed_status") or "pending",
                _json(data.get("progress_delta", {})),
                _json(data.get("advice", {})),
                data.get("encouragement_text") or "",
                data.get("trace_id"),
                data["created_at"],
                data.get("replied_at"),
            ),
        )

    async def update_checkin(self, checkin_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in CHECKIN_UPDATE_COLUMNS},
            {"progress_delta": "progress_delta_json", "advice": "advice_json"},
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goal_checkins SET {assignments} WHERE checkin_id = ?",
            (*values.values(), checkin_id),
        )

    async def get_checkin(self, checkin_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM goal_checkins WHERE checkin_id = ?",
            (checkin_id,),
        )
        return _checkin_from_row(dict(row)) if row else None

    async def latest_open_checkin(
        self,
        *,
        owner_member_id: str,
        conversation_id: str | None = None,
    ) -> dict[str, Any] | None:
        params: list[Any] = [owner_member_id]
        where = [
            "g.owner_member_id = ?",
            "g.status IN ('active', 'paused')",
            "c.parsed_status = 'pending'",
        ]
        if conversation_id:
            where.append("g.conversation_id = ?")
            params.append(conversation_id)
        row = await self._db.fetch_one(
            f"""
            SELECT c.*
            FROM goal_checkins c
            JOIN goals g ON g.goal_id = c.goal_id
            WHERE {' AND '.join(where)}
            ORDER BY c.created_at DESC
            LIMIT 1
            """,
            params,
        )
        return _checkin_from_row(dict(row)) if row else None

    async def list_checkins(self, goal_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM goal_checkins
            WHERE goal_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (goal_id, limit),
        )
        return [_checkin_from_row(dict(row)) for row in rows]

    async def insert_snapshot(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_progress_snapshots (
              snapshot_id, goal_id, progress_percent, completed_count, partial_count,
              missed_count, blocked_count, streak_days, summary, blockers_json,
              next_focus_json, source_checkin_id, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["snapshot_id"],
                data["goal_id"],
                int(data.get("progress_percent") or 0),
                int(data.get("completed_count") or 0),
                int(data.get("partial_count") or 0),
                int(data.get("missed_count") or 0),
                int(data.get("blocked_count") or 0),
                int(data.get("streak_days") or 0),
                data["summary"],
                _json(data.get("blockers", [])),
                _json(data.get("next_focus", [])),
                data.get("source_checkin_id"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def latest_snapshot(self, goal_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM goal_progress_snapshots
            WHERE goal_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (goal_id,),
        )
        return _snapshot_from_row(dict(row)) if row else None

    async def insert_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_events (
              event_id, goal_id, event_type, payload_json, payload_redacted_json,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["goal_id"],
                data["event_type"],
                _json(data.get("payload", {})),
                _json(data.get("payload_redacted", data.get("payload", {}))),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_events(self, goal_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM goal_events
            WHERE goal_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (goal_id, limit),
        )
        return [_event_from_row(dict(row)) for row in rows]

    async def insert_intake(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_intakes (
              intake_id, goal_id, domain_label, status, current_level, target_level,
              target_date, available_time_json, constraints_json, motivation_json,
              missing_fields_json, raw_answers_json, confirmed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["intake_id"],
                data["goal_id"],
                data.get("domain_label") or "general",
                data.get("status") or "collecting",
                data.get("current_level"),
                data.get("target_level"),
                data.get("target_date"),
                _json(data.get("available_time", {})),
                _json(data.get("constraints", {})),
                _json(data.get("motivation", {})),
                _json(data.get("missing_fields", [])),
                _json(data.get("raw_answers", {})),
                data.get("confirmed_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_intake(self, intake_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in INTAKE_UPDATE_COLUMNS},
            {
                "available_time": "available_time_json",
                "constraints": "constraints_json",
                "motivation": "motivation_json",
                "missing_fields": "missing_fields_json",
                "raw_answers": "raw_answers_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goal_intakes SET {assignments} WHERE intake_id = ?",
            (*values.values(), intake_id),
        )

    async def latest_intake_for_goal(self, goal_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM goal_intakes
            WHERE goal_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (goal_id,),
        )
        return _intake_from_row(dict(row)) if row else None

    async def insert_milestone(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_milestones (
              milestone_id, goal_id, goal_plan_id, title, description, status,
              target_date, acceptance_criteria_json, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["milestone_id"],
                data["goal_id"],
                data.get("goal_plan_id"),
                data["title"],
                data.get("description") or "",
                data.get("status") or "planned",
                data.get("target_date"),
                _json(data.get("acceptance_criteria", [])),
                int(data.get("sort_order") or 0),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_milestone(self, milestone_id: str, fields: dict[str, Any]) -> None:
        values = {
            key: value for key, value in fields.items() if key in MILESTONE_UPDATE_COLUMNS
        }
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goal_milestones SET {assignments} WHERE milestone_id = ?",
            (*values.values(), milestone_id),
        )

    async def list_milestones(self, goal_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM goal_milestones
            WHERE goal_id = ?
            ORDER BY sort_order ASC, created_at ASC
            """,
            (goal_id,),
        )
        return [_milestone_from_row(dict(row)) for row in rows]

    async def insert_routine(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_routines (
              routine_id, goal_id, goal_plan_id, title, description, cadence_json,
              estimated_minutes, difficulty, status, sort_order, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["routine_id"],
                data["goal_id"],
                data.get("goal_plan_id"),
                data["title"],
                data.get("description") or "",
                _json(data.get("cadence", {})),
                data.get("estimated_minutes"),
                data.get("difficulty") or "medium",
                data.get("status") or "active",
                int(data.get("sort_order") or 0),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_routine(self, routine_id: str, fields: dict[str, Any]) -> None:
        values = {key: value for key, value in fields.items() if key in ROUTINE_UPDATE_COLUMNS}
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goal_routines SET {assignments} WHERE routine_id = ?",
            (*values.values(), routine_id),
        )

    async def list_routines(self, goal_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM goal_routines
            WHERE goal_id = ?
            ORDER BY sort_order ASC, created_at ASC
            """,
            (goal_id,),
        )
        return [_routine_from_row(dict(row)) for row in rows]

    async def insert_intervention(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_interventions (
              intervention_id, goal_id, trigger_type, status, summary, suggestion_json,
              shown_at, user_feedback_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["intervention_id"],
                data["goal_id"],
                data["trigger_type"],
                data.get("status") or "suggested",
                data["summary"],
                _json(data.get("suggestion", {})),
                data.get("shown_at"),
                _json(data.get("user_feedback", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_intervention(self, intervention_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            {key: value for key, value in fields.items() if key in INTERVENTION_UPDATE_COLUMNS},
            {"user_feedback": "user_feedback_json"},
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE goal_interventions SET {assignments} WHERE intervention_id = ?",
            (*values.values(), intervention_id),
        )

    async def latest_intervention_for_goal(self, goal_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM goal_interventions
            WHERE goal_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (goal_id,),
        )
        return _intervention_from_row(dict(row)) if row else None

    async def list_interventions(self, goal_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM goal_interventions
            WHERE goal_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (goal_id, limit),
        )
        return [_intervention_from_row(dict(row)) for row in rows]

    async def insert_model_call(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO goal_model_calls (
              model_call_id, goal_id, call_type, status, model_route_json,
              input_redacted_json, output_redacted_json, fallback_reason, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["model_call_id"],
                data.get("goal_id"),
                data["call_type"],
                data["status"],
                _json(data.get("model_route", {})),
                _json(data.get("input_redacted", {})),
                _json(data.get("output_redacted", {})),
                data.get("fallback_reason"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_model_calls(self, goal_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM goal_model_calls
            WHERE goal_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (goal_id, limit),
        )
        return [_model_call_from_row(dict(row)) for row in rows]


def _goal_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["success_criteria"] = json.loads(row.pop("success_criteria_json") or "[]")
    row["constraints"] = json.loads(row.pop("constraints_json") or "{}")
    row["motivation"] = json.loads(row.pop("motivation_json") or "{}")
    return row


def _plan_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["assumptions"] = json.loads(row.pop("assumptions_json") or "[]")
    row["risk_notes"] = json.loads(row.pop("risk_notes_json") or "[]")
    return row


def _plan_item_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["cadence"] = json.loads(row.pop("cadence_json") or "{}")
    row["success_metric"] = json.loads(row.pop("success_metric_json") or "{}")
    return row


def _policy_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["frequency"] = json.loads(row.pop("frequency_json") or "{}")
    row["quiet_hours"] = json.loads(row.pop("quiet_hours_json") or "{}")
    row["tone_policy"] = json.loads(row.pop("tone_policy_json") or "{}")
    return row


def _checkin_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["progress_delta"] = json.loads(row.pop("progress_delta_json") or "{}")
    row["advice"] = json.loads(row.pop("advice_json") or "{}")
    return row


def _snapshot_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["blockers"] = json.loads(row.pop("blockers_json") or "[]")
    row["next_focus"] = json.loads(row.pop("next_focus_json") or "[]")
    return row


def _event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row.pop("payload_json", None)
    return row


def _intake_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["available_time"] = json.loads(row.pop("available_time_json") or "{}")
    row["constraints"] = json.loads(row.pop("constraints_json") or "{}")
    row["motivation"] = json.loads(row.pop("motivation_json") or "{}")
    row["missing_fields"] = json.loads(row.pop("missing_fields_json") or "[]")
    row["raw_answers"] = json.loads(row.pop("raw_answers_json") or "{}")
    return row


def _milestone_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["acceptance_criteria"] = json.loads(row.pop("acceptance_criteria_json") or "[]")
    return row


def _routine_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["cadence"] = json.loads(row.pop("cadence_json") or "{}")
    return row


def _intervention_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["suggestion"] = json.loads(row.pop("suggestion_json") or "{}")
    row["user_feedback"] = json.loads(row.pop("user_feedback_json") or "{}")
    return row


def _model_call_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["model_route"] = json.loads(row.pop("model_route_json") or "{}")
    row["input_redacted"] = json.loads(row.pop("input_redacted_json") or "{}")
    row["output_redacted"] = json.loads(row.pop("output_redacted_json") or "{}")
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
