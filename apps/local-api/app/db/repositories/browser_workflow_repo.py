from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

INTENT_UPDATE_COLUMNS = {
    "status",
    "target_url",
    "target_key",
    "missing_fields_json",
    "resolver_evidence_json",
    "trace_id",
    "updated_at",
}

PLAN_UPDATE_COLUMNS = {
    "task_id",
    "approval_id",
    "status",
    "risk_level",
    "current_url",
    "form_data_json",
    "file_refs_json",
    "steps_json",
    "approval_binding_json",
    "evidence_json",
    "metadata_json",
    "failure_reason",
    "trace_id",
    "updated_at",
}

STEP_UPDATE_COLUMNS = {
    "status",
    "input_redacted_json",
    "output_redacted_json",
    "evidence_refs_json",
    "approval_id",
    "tool_call_id",
    "trace_id",
    "updated_at",
}

EXECUTION_UPDATE_COLUMNS = {
    "status",
    "approval_id",
    "result_json",
    "evidence_refs_json",
    "failure_reason",
    "user_visible_message",
    "completed_at",
    "trace_id",
    "updated_at",
}


class BrowserWorkflowRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_intent(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_workflow_intents (
              intent_id, organization_id, member_id, conversation_id, turn_id, trace_id,
              natural_language_goal, action_type, target_url, target_key, content_summary,
              constraints_json, missing_fields_json, status, confidence,
              resolver_evidence_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["intent_id"],
                data.get("organization_id", "org_default"),
                data.get("member_id", "mem_xiaoyao"),
                data.get("conversation_id"),
                data.get("turn_id"),
                data.get("trace_id"),
                data["natural_language_goal"],
                data["action_type"],
                data.get("target_url"),
                data.get("target_key"),
                data.get("content_summary"),
                _json(data.get("constraints", {})),
                _json(data.get("missing_fields", [])),
                data["status"],
                data.get("confidence", 0.0),
                _json(data.get("resolver_evidence", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM browser_workflow_intents WHERE intent_id = ?",
            (intent_id,),
        )
        return _intent_from_row(dict(row)) if row else None

    async def update_intent(self, intent_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "browser_workflow_intents",
            "intent_id",
            intent_id,
            _json_update_fields(
                fields,
                {
                    "constraints": "constraints_json",
                    "missing_fields": "missing_fields_json",
                    "resolver_evidence": "resolver_evidence_json",
                },
            ),
            INTENT_UPDATE_COLUMNS,
        )

    async def insert_plan(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_workflow_plans (
              plan_id, intent_id, organization_id, member_id, conversation_id,
              task_id, approval_id, trace_id, action_type, target_url, target_key,
              goal, status, risk_level, current_url, content_summary,
              form_data_json, file_refs_json, steps_json, approval_binding_json,
              evidence_json, metadata_json, failure_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["plan_id"],
                data["intent_id"],
                data.get("organization_id", "org_default"),
                data.get("member_id", "mem_xiaoyao"),
                data.get("conversation_id"),
                data.get("task_id"),
                data.get("approval_id"),
                data.get("trace_id"),
                data["action_type"],
                data.get("target_url"),
                data.get("target_key"),
                data["goal"],
                data["status"],
                data.get("risk_level", "R1"),
                data.get("current_url"),
                data.get("content_summary"),
                _json(data.get("form_data", {})),
                _json(data.get("file_refs", [])),
                _json(data.get("steps", [])),
                _json(data.get("approval_binding", {})),
                _json(data.get("evidence", {})),
                _json(data.get("metadata", {})),
                data.get("failure_reason"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM browser_workflow_plans WHERE plan_id = ?",
            (plan_id,),
        )
        return _plan_from_row(dict(row)) if row else None

    async def update_plan(self, plan_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "browser_workflow_plans",
            "plan_id",
            plan_id,
            _json_update_fields(
                fields,
                {
                    "form_data": "form_data_json",
                    "file_refs": "file_refs_json",
                    "steps": "steps_json",
                    "approval_binding": "approval_binding_json",
                    "evidence": "evidence_json",
                    "metadata": "metadata_json",
                },
            ),
            PLAN_UPDATE_COLUMNS,
        )

    async def insert_step(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_workflow_steps (
              step_id, plan_id, step_order, step_type, tool_name, selector, label,
              status, risk_level, requires_approval, input_redacted_json,
              output_redacted_json, evidence_refs_json, approval_id, tool_call_id,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["step_id"],
                data["plan_id"],
                data.get("step_order", 0),
                data["step_type"],
                data.get("tool_name"),
                data.get("selector"),
                data.get("label"),
                data.get("status", "planned"),
                data.get("risk_level", "R1"),
                1 if data.get("requires_approval") else 0,
                _json(data.get("input_redacted", {})),
                _json(data.get("output_redacted", {})),
                _json(data.get("evidence_refs", [])),
                data.get("approval_id"),
                data.get("tool_call_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_step(self, step_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "browser_workflow_steps",
            "step_id",
            step_id,
            _json_update_fields(
                fields,
                {
                    "input_redacted": "input_redacted_json",
                    "output_redacted": "output_redacted_json",
                    "evidence_refs": "evidence_refs_json",
                },
            ),
            STEP_UPDATE_COLUMNS,
        )

    async def list_steps(self, plan_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM browser_workflow_steps
            WHERE plan_id = ?
            ORDER BY step_order ASC, created_at ASC
            """,
            (plan_id,),
        )
        return [_step_from_row(dict(row)) for row in rows]

    async def insert_execution(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_workflow_executions (
              execution_id, plan_id, organization_id, member_id, action_type, status,
              approval_id, result_json, evidence_refs_json, failure_reason,
              user_visible_message, trace_id, started_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["execution_id"],
                data["plan_id"],
                data.get("organization_id", "org_default"),
                data.get("member_id", "mem_xiaoyao"),
                data["action_type"],
                data["status"],
                data.get("approval_id"),
                _json(data.get("result", {})),
                _json(data.get("evidence_refs", [])),
                data.get("failure_reason"),
                data.get("user_visible_message"),
                data.get("trace_id"),
                data["started_at"],
                data.get("completed_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM browser_workflow_executions WHERE execution_id = ?",
            (execution_id,),
        )
        return _execution_from_row(dict(row)) if row else None

    async def latest_execution(self, plan_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM browser_workflow_executions
            WHERE plan_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (plan_id,),
        )
        return _execution_from_row(dict(row)) if row else None

    async def update_execution(self, execution_id: str, fields: dict[str, Any]) -> None:
        await self._update(
            "browser_workflow_executions",
            "execution_id",
            execution_id,
            _json_update_fields(
                fields,
                {
                    "result": "result_json",
                    "evidence_refs": "evidence_refs_json",
                },
            ),
            EXECUTION_UPDATE_COLUMNS,
        )

    async def list_executions(self, plan_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM browser_workflow_executions
            WHERE plan_id = ?
            ORDER BY created_at ASC
            """,
            (plan_id,),
        )
        return [_execution_from_row(dict(row)) for row in rows]

    async def insert_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO browser_workflow_events (
              event_id, plan_id, organization_id, execution_id, event_type,
              payload_redacted_json, evidence_refs_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["plan_id"],
                data.get("organization_id", "org_default"),
                data.get("execution_id"),
                data["event_type"],
                _json(data.get("payload_redacted", {})),
                _json(data.get("evidence_refs", [])),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_events(self, plan_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM browser_workflow_events
            WHERE plan_id = ?
            ORDER BY created_at ASC
            """,
            (plan_id,),
        )
        return [_event_from_row(dict(row)) for row in rows]

    async def find_candidate(
        self,
        *,
        organization_id: str,
        host: str,
        action_type: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM browser_workflow_candidates
            WHERE organization_id = ?
              AND host = ?
              AND action_type = ?
              AND status IN ('active', 'test_only')
            ORDER BY
              CASE status WHEN 'active' THEN 0 ELSE 1 END,
              success_count DESC,
              updated_at DESC
            LIMIT 1
            """,
            (organization_id, host, action_type),
        )
        return _candidate_from_row(dict(row)) if row else None

    async def upsert_candidate(self, data: dict[str, Any]) -> dict[str, Any]:
        await self._db.execute(
            """
            INSERT INTO browser_workflow_candidates (
              candidate_id, organization_id, target_key, host, action_type, status,
              source, manifest_json, evidence_refs_json, success_count, failure_count,
              confidence, recommended, last_plan_id, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id, host, action_type, source) DO UPDATE SET
              target_key = excluded.target_key,
              status = CASE
                WHEN browser_workflow_candidates.status = 'active' THEN 'active'
                ELSE excluded.status
              END,
              manifest_json = excluded.manifest_json,
              evidence_refs_json = excluded.evidence_refs_json,
              success_count = browser_workflow_candidates.success_count + excluded.success_count,
              failure_count = browser_workflow_candidates.failure_count + excluded.failure_count,
              confidence = MAX(browser_workflow_candidates.confidence, excluded.confidence),
              recommended = CASE
                WHEN browser_workflow_candidates.success_count + excluded.success_count >= 2
                THEN 1 ELSE excluded.recommended
              END,
              last_plan_id = excluded.last_plan_id,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["candidate_id"],
                data.get("organization_id", "org_default"),
                data.get("target_key"),
                data["host"],
                data["action_type"],
                data.get("status", "test_only"),
                data.get("source", "autonomous_browser_workflow"),
                _json(data.get("manifest", {})),
                _json(data.get("evidence_refs", [])),
                data.get("success_count", 0),
                data.get("failure_count", 0),
                data.get("confidence", 0.0),
                1 if data.get("recommended") else 0,
                data.get("last_plan_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )
        found = await self.find_candidate(
            organization_id=data.get("organization_id", "org_default"),
            host=data["host"],
            action_type=data["action_type"],
        )
        if found is None:
            raise RuntimeError("browser workflow candidate upsert failed")
        return found

    async def list_candidates_for_plan(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        host = str(plan.get("target_key") or "")
        if not host:
            return []
        candidate = await self.find_candidate(
            organization_id=str(plan.get("organization_id") or "org_default"),
            host=host,
            action_type=str(plan.get("action_type") or ""),
        )
        return [candidate] if candidate else []

    async def _update(
        self,
        table: str,
        key_column: str,
        key_value: str,
        fields: dict[str, Any],
        allowed: set[str],
    ) -> None:
        if not fields:
            return
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported update fields for {table}: {sorted(unknown)}")
        assignments = ", ".join(f"{column} = ?" for column in fields)
        await self._db.execute(
            f"UPDATE {table} SET {assignments} WHERE {key_column} = ?",
            (*fields.values(), key_value),
        )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _json_update_fields(fields: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in fields.items():
        column = mapping.get(key, key)
        normalized[column] = _json(value) if column.endswith("_json") else value
    return normalized


def _intent_from_row(row: dict[str, Any]) -> dict[str, Any]:
    constraints = _loads(row.pop("constraints_json", None), {})
    missing_fields = _loads(row.pop("missing_fields_json", None), [])
    resolver_evidence = _loads(row.pop("resolver_evidence_json", None), {})
    return {
        **row,
        "constraints": constraints,
        "missing_fields": missing_fields,
        "resolver_evidence": resolver_evidence,
    }


def _plan_from_row(row: dict[str, Any]) -> dict[str, Any]:
    form_data = _loads(row.pop("form_data_json", None), {})
    file_refs = _loads(row.pop("file_refs_json", None), [])
    steps = _loads(row.pop("steps_json", None), [])
    approval_binding = _loads(row.pop("approval_binding_json", None), {})
    evidence = _loads(row.pop("evidence_json", None), {})
    metadata = _loads(row.pop("metadata_json", None), {})
    return {
        **row,
        "form_data": form_data,
        "file_refs": file_refs,
        "steps": steps,
        "approval_binding": approval_binding,
        "evidence": evidence,
        "metadata": metadata,
    }


def _step_from_row(row: dict[str, Any]) -> dict[str, Any]:
    input_redacted = _loads(row.pop("input_redacted_json", None), {})
    output_redacted = _loads(row.pop("output_redacted_json", None), {})
    evidence_refs = _loads(row.pop("evidence_refs_json", None), [])
    return {
        **row,
        "requires_approval": bool(row.get("requires_approval")),
        "input_redacted": input_redacted,
        "output_redacted": output_redacted,
        "evidence_refs": evidence_refs,
    }


def _execution_from_row(row: dict[str, Any]) -> dict[str, Any]:
    result = _loads(row.pop("result_json", None), {})
    evidence_refs = _loads(row.pop("evidence_refs_json", None), [])
    return {
        **row,
        "result": result,
        "evidence_refs": evidence_refs,
    }


def _event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    payload_redacted = _loads(row.pop("payload_redacted_json", None), {})
    evidence_refs = _loads(row.pop("evidence_refs_json", None), [])
    return {
        **row,
        "payload_redacted": payload_redacted,
        "evidence_refs": evidence_refs,
    }


def _candidate_from_row(row: dict[str, Any]) -> dict[str, Any]:
    manifest = _loads(row.pop("manifest_json", None), {})
    evidence_refs = _loads(row.pop("evidence_refs_json", None), [])
    return {
        **row,
        "recommended": bool(row.get("recommended")),
        "manifest": manifest,
        "evidence_refs": evidence_refs,
    }
