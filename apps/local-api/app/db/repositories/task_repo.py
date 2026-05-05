from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.db.session import Database

TASK_UPDATE_COLUMNS = {
    "title",
    "goal",
    "mode",
    "status",
    "risk_level",
    "success_criteria_json",
    "plan_json",
    "budget_json",
    "preflight_json",
    "artifact_plan_json",
    "retry_policy_json",
    "progress_json",
    "current_approval_id",
    "result_json",
    "cancellation_reason",
    "failure_reason",
    "parent_task_id",
    "host_member_id",
    "collaboration_plan_id",
    "supervisor_mode",
    "archived_at",
    "updated_at",
}

STEP_UPDATE_COLUMNS = {
    "status",
    "output_json",
    "idempotency_key",
    "retry_count",
    "approval_id",
    "tool_call_id",
    "subtask_id",
    "participant_id",
    "assigned_member_id",
    "error_code",
    "error_summary",
    "metadata_json",
    "input_json",
    "updated_at",
}

APPROVAL_UPDATE_COLUMNS = {
    "status",
    "decision_reason",
    "edited_payload_json",
    "updated_at",
    "resolved_at",
}

TOOL_CALL_UPDATE_COLUMNS = {
    "status",
    "args_redacted_json",
    "result_redacted_json",
    "handle_ids_json",
    "capability_decision_id",
    "safety_decision_id",
    "safety_decision_json",
    "policy_snapshot_json",
    "resolved_asset_refs_json",
    "risk_level",
    "approval_id",
    "timeout_seconds",
    "artifact_ids_json",
    "error_code",
    "error_summary",
    "updated_at",
}

JOB_UPDATE_COLUMNS = {
    "status",
    "payload_json",
    "attempt_count",
    "max_attempts",
    "next_run_at",
    "locked_by",
    "locked_at",
    "error_code",
    "error_summary",
    "updated_at",
}

PARTICIPANT_UPDATE_COLUMNS = {
    "status",
    "selection_reason",
    "context_scope_json",
    "allowed_skills_json",
    "allowed_mcp_tools_json",
    "capability_decision_id",
    "output_summary_json",
    "error_code",
    "error_summary",
    "trace_id",
    "updated_at",
    "removed_at",
}

SUBTASK_UPDATE_COLUMNS = {
    "participant_id",
    "assigned_member_id",
    "status",
    "context_scope_json",
    "allowed_skills_json",
    "allowed_mcp_tools_json",
    "output_summary_json",
    "source_refs_json",
    "trace_id",
    "error_code",
    "error_summary",
    "updated_at",
    "completed_at",
}

ROUND_UPDATE_COLUMNS = {
    "status",
    "participant_ids_json",
    "round_summary_json",
    "trace_id",
    "updated_at",
    "completed_at",
}


class TaskRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        async with self._db.transaction():
            yield

    async def insert_task(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO tasks (
              task_id, organization_id, conversation_id, owner_member_id, title, goal,
              mode, status, risk_level, success_criteria_json, plan_json, budget_json,
              preflight_json, artifact_plan_json, retry_policy_json, progress_json,
              current_approval_id, result_json, client_request_id, cancellation_reason,
              failure_reason, trace_id, parent_task_id, host_member_id, collaboration_plan_id,
              supervisor_mode, archived_at, created_at, updated_at
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                data["task_id"],
                data["organization_id"],
                data.get("conversation_id"),
                data["owner_member_id"],
                data["title"],
                data["goal"],
                data["mode"],
                data["status"],
                data["risk_level"],
                _json(data.get("success_criteria", [])),
                _json(data.get("plan", {})),
                _json(data.get("budget", {})),
                _json(data.get("preflight", {})),
                _json(data.get("artifact_plan", {})),
                _json(data.get("retry_policy", {})),
                _json(data.get("progress", {})),
                data.get("current_approval_id"),
                _json(data.get("result", {})),
                data.get("client_request_id"),
                data.get("cancellation_reason"),
                data.get("failure_reason"),
                data.get("trace_id"),
                data.get("parent_task_id"),
                data.get("host_member_id"),
                data.get("collaboration_plan_id"),
                data.get("supervisor_mode"),
                data.get("archived_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        return _task_from_row(dict(row)) if row else None

    async def get_task_by_client_request_id(self, client_request_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM tasks WHERE client_request_id = ?",
            (client_request_id,),
        )
        return _task_from_row(dict(row)) if row else None

    async def list_tasks(
        self,
        *,
        status: str | None = None,
        owner_member_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        else:
            where.append("status != 'archived'")
        if owner_member_id:
            where.append("owner_member_id = ?")
            params.append(owner_member_id)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM tasks
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_task_from_row(dict(row)) for row in rows]

    async def update_task(self, task_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        values = _json_update_fields(
            fields,
            {
                "success_criteria": "success_criteria_json",
                "plan": "plan_json",
                "budget": "budget_json",
                "preflight": "preflight_json",
                "artifact_plan": "artifact_plan_json",
                "retry_policy": "retry_policy_json",
                "progress": "progress_json",
                "result": "result_json",
            },
        )
        unsupported = set(values) - TASK_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported tasks update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE tasks SET {assignments} WHERE task_id = ?",
            (*values.values(), task_id),
        )

    async def insert_step(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_steps (
              step_id, organization_id, task_id, subtask_id, participant_id, assigned_member_id,
              step_key, idempotency_key, sequence,
              step_type, title, status, input_json, output_json, retry_count, max_retries,
              risk_level, approval_id, tool_call_id, error_code, error_summary, metadata_json,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["step_id"],
                data["organization_id"],
                data["task_id"],
                data.get("subtask_id"),
                data.get("participant_id"),
                data.get("assigned_member_id"),
                data["step_key"],
                data.get("idempotency_key"),
                data["sequence"],
                data["step_type"],
                data["title"],
                data["status"],
                _json(data.get("input", {})),
                _json(data.get("output", {})),
                data.get("retry_count", 0),
                data.get("max_retries", 2),
                data.get("risk_level", "R1"),
                data.get("approval_id"),
                data.get("tool_call_id"),
                data.get("error_code"),
                data.get("error_summary"),
                _json(data.get("metadata", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_steps(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM task_steps WHERE task_id = ? ORDER BY sequence ASC",
            (task_id,),
        )
        return [_step_from_row(dict(row)) for row in rows]

    async def get_step(self, step_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM task_steps WHERE step_id = ?", (step_id,))
        return _step_from_row(dict(row)) if row else None

    async def update_step(self, step_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        values = _json_update_fields(
            fields,
            {"input": "input_json", "output": "output_json", "metadata": "metadata_json"},
        )
        unsupported = set(values) - STEP_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported task_steps update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE task_steps SET {assignments} WHERE step_id = ?",
            (*values.values(), step_id),
        )

    async def insert_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_events (
              event_id, organization_id, task_id, step_id, event_type, payload_json,
              payload_redacted_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["organization_id"],
                data["task_id"],
                data.get("step_id"),
                data["event_type"],
                _json(data.get("payload", {})),
                _json(data.get("payload_redacted", data.get("payload", {}))),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def insert_planner_decision(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_planner_decisions (
              planner_decision_id, organization_id, task_id, planner_type, selected_mode,
              reason_codes_json, capability_snapshot_json, skill_match_refs_json,
              mcp_tool_refs_json, model_hint_json, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["planner_decision_id"],
                data["organization_id"],
                data["task_id"],
                data["planner_type"],
                data["selected_mode"],
                _json(data.get("reason_codes", [])),
                _json(data.get("capability_snapshot", {})),
                _json(data.get("skill_match_refs", [])),
                _json(data.get("mcp_tool_refs", [])),
                _json(data.get("model_hint", {})),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_planner_decisions(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM task_planner_decisions
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_planner_decision_from_row(dict(row)) for row in rows]

    async def insert_agent_loop_iteration(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO agent_loop_iterations (
              iteration_id, organization_id, task_id, loop_index, observation_id,
              observation_summary, plan_delta_json, selected_action_json,
              tool_call_refs_json, safety_decision_refs_json, evaluation_result_json,
              next_step_key, stop_reason, budget_snapshot_json, status, trace_id,
              started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, loop_index) DO UPDATE SET
              observation_id = excluded.observation_id,
              observation_summary = excluded.observation_summary,
              plan_delta_json = excluded.plan_delta_json,
              selected_action_json = excluded.selected_action_json,
              tool_call_refs_json = excluded.tool_call_refs_json,
              safety_decision_refs_json = excluded.safety_decision_refs_json,
              evaluation_result_json = excluded.evaluation_result_json,
              next_step_key = excluded.next_step_key,
              stop_reason = excluded.stop_reason,
              budget_snapshot_json = excluded.budget_snapshot_json,
              status = excluded.status,
              trace_id = excluded.trace_id,
              completed_at = excluded.completed_at
            """,
            (
                data["iteration_id"],
                data["organization_id"],
                data["task_id"],
                data["loop_index"],
                data.get("observation_id"),
                data.get("observation_summary"),
                _json(data.get("plan_delta", {})),
                _json(data.get("selected_action", {})),
                _json(data.get("tool_call_refs", [])),
                _json(data.get("safety_decision_refs", [])),
                _json(data.get("evaluation_result", {})),
                data.get("next_step_key"),
                data.get("stop_reason"),
                _json(data.get("budget_snapshot", {})),
                data["status"],
                data.get("trace_id"),
                data["started_at"],
                data.get("completed_at"),
            ),
        )

    async def list_agent_loop_iterations(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM agent_loop_iterations
            WHERE task_id = ?
            ORDER BY loop_index ASC
            """,
            (task_id,),
        )
        return [_agent_loop_iteration_from_row(dict(row)) for row in rows]

    async def insert_task_observation(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_observations (
              observation_id, organization_id, task_id, step_id, source_type, source_ref_json,
              trusted_level, summary, key_facts_json, errors_json, artifact_refs_json,
              sensitivity, untrusted_instructions_detected, payload_redacted_json,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["observation_id"],
                data["organization_id"],
                data["task_id"],
                data.get("step_id"),
                data["source_type"],
                _json(data.get("source_ref", {})),
                data["trusted_level"],
                data["summary"],
                _json(data.get("key_facts", [])),
                _json(data.get("errors", [])),
                _json(data.get("artifact_refs", [])),
                data.get("sensitivity", "low"),
                1 if data.get("untrusted_instructions_detected") else 0,
                _json(data.get("payload_redacted", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_task_observations(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM task_observations
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_task_observation_from_row(dict(row)) for row in rows]

    async def insert_task_retry_plan(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_retry_plans (
              retry_plan_id, organization_id, task_id, reason, suggested_actions_json,
              resumable_from_step_key, budget_delta_json, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["retry_plan_id"],
                data["organization_id"],
                data["task_id"],
                data["reason"],
                _json(data.get("suggested_actions", [])),
                data.get("resumable_from_step_key"),
                _json(data.get("budget_delta", {})),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_task_retry_plans(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM task_retry_plans
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_task_retry_plan_from_row(dict(row)) for row in rows]

    async def insert_task_reflection_candidate(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_reflection_candidates (
              candidate_id, organization_id, task_id, candidate_type, status, confidence,
              summary, payload_json, source_refs_json, risk_level, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["candidate_id"],
                data["organization_id"],
                data["task_id"],
                data["candidate_type"],
                data["status"],
                data.get("confidence", 0.0),
                data["summary"],
                _json(data.get("payload", {})),
                _json(data.get("source_refs", [])),
                data.get("risk_level", "R1"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_task_reflection_candidates(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM task_reflection_candidates
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_task_reflection_candidate_from_row(dict(row)) for row in rows]

    async def insert_model_plan_candidate(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO model_plan_candidates (
              candidate_id, organization_id, task_id, planner_type, source,
              recommended_mode, steps_json, success_criteria_json, assumptions_json,
              missing_information_json, risk_hints_json, required_capabilities_json,
              required_assets_json, confidence, reasoning_summary, status,
              model_assist_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["candidate_id"],
                data["organization_id"],
                data["task_id"],
                data["planner_type"],
                data["source"],
                data["recommended_mode"],
                _json(data.get("steps", [])),
                _json(data.get("success_criteria", [])),
                _json(data.get("assumptions", [])),
                _json(data.get("missing_information", [])),
                _json(data.get("risk_hints", [])),
                _json(data.get("required_capabilities", [])),
                _json(data.get("required_assets", [])),
                data.get("confidence", 0.0),
                data["reasoning_summary"],
                data["status"],
                _json(data.get("model_assist", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_model_plan_candidates(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM model_plan_candidates
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_model_plan_candidate_from_row(dict(row)) for row in rows]

    async def insert_plan_verification_result(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO plan_verification_results (
              verification_id, organization_id, task_id, candidate_id, schema_valid,
              mode_allowed, step_type_allowed, capability_available,
              asset_handle_allowed, risk_level_acceptable, approval_strategy_present,
              budget_within_limit, no_direct_secret, no_direct_shell_command_from_model,
              issues_json, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["verification_id"],
                data["organization_id"],
                data["task_id"],
                data["candidate_id"],
                1 if data.get("schema_valid") else 0,
                1 if data.get("mode_allowed") else 0,
                1 if data.get("step_type_allowed") else 0,
                1 if data.get("capability_available") else 0,
                1 if data.get("asset_handle_allowed") else 0,
                1 if data.get("risk_level_acceptable") else 0,
                1 if data.get("approval_strategy_present") else 0,
                1 if data.get("budget_within_limit") else 0,
                1 if data.get("no_direct_secret") else 0,
                1 if data.get("no_direct_shell_command_from_model") else 0,
                _json(data.get("issues", [])),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_plan_verification_results(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM plan_verification_results
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_plan_verification_result_from_row(dict(row)) for row in rows]

    async def insert_plan_policy_prune(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO plan_policy_prunes (
              prune_id, organization_id, task_id, candidate_id, prune_type,
              original_step_json, pruned_step_json, reason_codes_json, status,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["prune_id"],
                data["organization_id"],
                data["task_id"],
                data["candidate_id"],
                data["prune_type"],
                _json(data.get("original_step", {})),
                _json(data.get("pruned_step", {})),
                _json(data.get("reason_codes", [])),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_plan_policy_prunes(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM plan_policy_prunes
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_plan_policy_prune_from_row(dict(row)) for row in rows]

    async def insert_planner_capability_candidate(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO planner_capability_candidates (
              capability_candidate_id, organization_id, task_id, capability_type,
              capability_id, name, match_score, risk_level, policy_status,
              reason_codes_json, metadata_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["capability_candidate_id"],
                data["organization_id"],
                data["task_id"],
                data["capability_type"],
                data.get("capability_id"),
                data.get("name"),
                data.get("match_score", 0.0),
                data.get("risk_level", "R1"),
                data["policy_status"],
                _json(data.get("reason_codes", [])),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_planner_capability_candidates(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM planner_capability_candidates
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_planner_capability_candidate_from_row(dict(row)) for row in rows]

    async def insert_agent_next_action_decision(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO agent_next_action_decisions (
              decision_id, organization_id, task_id, iteration_id, loop_index,
              next_action_type, selected_step_id, selected_step_key, plan_delta_json,
              needs_user_input, needs_approval, stop_reason, confidence,
              reason_codes_json, budget_snapshot_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["decision_id"],
                data["organization_id"],
                data["task_id"],
                data.get("iteration_id"),
                data["loop_index"],
                data["next_action_type"],
                data.get("selected_step_id"),
                data.get("selected_step_key"),
                _json(data.get("plan_delta", {})),
                1 if data.get("needs_user_input") else 0,
                1 if data.get("needs_approval") else 0,
                data.get("stop_reason"),
                data.get("confidence", 0.0),
                _json(data.get("reason_codes", [])),
                _json(data.get("budget_snapshot", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_agent_next_action_decisions(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM agent_next_action_decisions
            WHERE task_id = ?
            ORDER BY loop_index ASC, created_at ASC
            """,
            (task_id,),
        )
        return [_agent_next_action_decision_from_row(dict(row)) for row in rows]

    async def insert_tool_failure_recovery_plan(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO tool_failure_recovery_plans (
              recovery_plan_id, organization_id, task_id, step_id, tool_call_id,
              failure_type, recovery_action, suggested_actions_json, retry_allowed,
              bypass_controls, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["recovery_plan_id"],
                data["organization_id"],
                data["task_id"],
                data.get("step_id"),
                data.get("tool_call_id"),
                data["failure_type"],
                data["recovery_action"],
                _json(data.get("suggested_actions", [])),
                1 if data.get("retry_allowed") else 0,
                1 if data.get("bypass_controls") else 0,
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_tool_failure_recovery_plans(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM tool_failure_recovery_plans
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_tool_failure_recovery_plan_from_row(dict(row)) for row in rows]

    async def list_events(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
        return [_event_from_row(dict(row)) for row in rows]

    async def upsert_job(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_jobs (
              job_id, organization_id, task_id, step_id, job_type, idempotency_key, status,
              priority, payload_json, attempt_count, max_attempts, next_run_at, locked_by,
              locked_at, error_code, error_summary, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
              status = excluded.status,
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            """,
            (
                data["job_id"],
                data["organization_id"],
                data["task_id"],
                data.get("step_id"),
                data["job_type"],
                data["idempotency_key"],
                data["status"],
                data.get("priority", "normal"),
                _json(data.get("payload", {})),
                data.get("attempt_count", 0),
                data.get("max_attempts", 3),
                data.get("next_run_at"),
                data.get("locked_by"),
                data.get("locked_at"),
                data.get("error_code"),
                data.get("error_summary"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_recoverable_jobs(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM task_jobs
            WHERE status IN ('pending', 'running')
            ORDER BY created_at ASC
            """
        )
        return [_job_from_row(dict(row)) for row in rows]

    async def update_job_by_idempotency(
        self,
        idempotency_key: str,
        fields: dict[str, Any],
    ) -> None:
        if not fields:
            return
        values = _json_update_fields(fields, {"payload": "payload_json"})
        unsupported = set(values) - JOB_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported task_jobs update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE task_jobs SET {assignments} WHERE idempotency_key = ?",
            (*values.values(), idempotency_key),
        )

    async def upsert_tool(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO tool_registry (
              tool_name, display_name, description, source, input_schema_json,
              output_schema_json, risk_policy_json, required_handle_types_json,
              status, bundle_id, skill_id, mcp_server_id, mcp_tool_id,
              adapter_config_json, trust_level, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tool_name) DO UPDATE SET
              display_name = excluded.display_name,
              description = excluded.description,
              source = excluded.source,
              input_schema_json = excluded.input_schema_json,
              output_schema_json = excluded.output_schema_json,
              risk_policy_json = excluded.risk_policy_json,
              required_handle_types_json = excluded.required_handle_types_json,
              status = excluded.status,
              bundle_id = excluded.bundle_id,
              skill_id = excluded.skill_id,
              mcp_server_id = excluded.mcp_server_id,
              mcp_tool_id = excluded.mcp_tool_id,
              adapter_config_json = excluded.adapter_config_json,
              trust_level = excluded.trust_level,
              updated_at = excluded.updated_at
            """,
            (
                data["tool_name"],
                data["display_name"],
                data["description"],
                data.get("source", "builtin"),
                _json(data.get("input_schema", {})),
                _json(data.get("output_schema", {})),
                _json(data.get("risk_policy", {})),
                _json(data.get("required_handle_types", [])),
                data["status"],
                data.get("bundle_id"),
                data.get("skill_id"),
                data.get("mcp_server_id"),
                data.get("mcp_tool_id"),
                _json(data.get("adapter_config", {})),
                data.get("trust_level", "local"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_tool(self, tool_name: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM tool_registry WHERE tool_name = ?",
            (tool_name,),
        )
        return _tool_from_row(dict(row)) if row else None

    async def list_tools(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM tool_registry ORDER BY tool_name ASC"
        )
        return [_tool_from_row(dict(row)) for row in rows]

    async def insert_tool_call(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO tool_calls (
              tool_call_id, organization_id, task_id, step_id, tool_name, source, status,
              idempotency_key, args_redacted_json, result_redacted_json, handle_ids_json,
              capability_decision_id, safety_decision_id, safety_decision_json,
              policy_snapshot_json, resolved_asset_refs_json, risk_level, approval_id,
              timeout_seconds, artifact_ids_json, error_code, error_summary, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            ,
            (
                data["tool_call_id"],
                data["organization_id"],
                data.get("task_id"),
                data.get("step_id"),
                data["tool_name"],
                data.get("source", "builtin"),
                data["status"],
                data.get("idempotency_key"),
                _json(data.get("args_redacted", {})),
                _json(data.get("result_redacted", {})),
                _json(data.get("handle_ids", [])),
                data.get("capability_decision_id"),
                data.get("safety_decision_id"),
                _json(data.get("safety_decision", {})),
                _json(data.get("policy_snapshot", {})),
                _json(data.get("resolved_asset_refs", [])),
                data.get("risk_level", "R1"),
                data.get("approval_id"),
                data.get("timeout_seconds"),
                _json(data.get("artifact_ids", [])),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_tool_call(self, tool_call_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        values = _json_update_fields(
            fields,
            {
                "args_redacted": "args_redacted_json",
                "result_redacted": "result_redacted_json",
                "handle_ids": "handle_ids_json",
                "safety_decision": "safety_decision_json",
                "policy_snapshot": "policy_snapshot_json",
                "resolved_asset_refs": "resolved_asset_refs_json",
                "artifact_ids": "artifact_ids_json",
            },
        )
        unsupported = set(values) - TOOL_CALL_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported tool_calls update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE tool_calls SET {assignments} WHERE tool_call_id = ?",
            (*values.values(), tool_call_id),
        )

    async def get_tool_call(self, tool_call_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM tool_calls WHERE tool_call_id = ?",
            (tool_call_id,),
        )
        return _tool_call_from_row(dict(row)) if row else None

    async def get_tool_call_by_idempotency(
        self,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM tool_calls WHERE idempotency_key = ?",
            (idempotency_key,),
        )
        return _tool_call_from_row(dict(row)) if row else None

    async def list_tool_calls(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM tool_calls WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
        return [_tool_call_from_row(dict(row)) for row in rows]

    async def insert_approval(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO approvals (
              approval_id, organization_id, task_id, step_id, tool_call_id, approval_type,
              requested_action, risk_level, summary, payload_redacted_json, options_json,
              status, expires_at, decision_reason, edited_payload_json, trace_id,
              created_at, updated_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["approval_id"],
                data["organization_id"],
                data["task_id"],
                data.get("step_id"),
                data.get("tool_call_id"),
                data.get("approval_type", "action"),
                data["requested_action"],
                data["risk_level"],
                data["summary"],
                _json(data.get("payload_redacted", {})),
                _json(data.get("options", [])),
                data["status"],
                data.get("expires_at"),
                data.get("decision_reason"),
                _json(data["edited_payload"]) if data.get("edited_payload") is not None else None,
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("resolved_at"),
            ),
        )

    async def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM approvals WHERE approval_id = ?",
            (approval_id,),
        )
        return _approval_from_row(dict(row)) if row else None

    async def list_approvals(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM approvals WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
        return [_approval_from_row(dict(row)) for row in rows]

    async def update_approval(self, approval_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        values = _json_update_fields(fields, {"edited_payload": "edited_payload_json"})
        unsupported = set(values) - APPROVAL_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported approvals update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE approvals SET {assignments} WHERE approval_id = ?",
            (*values.values(), approval_id),
        )

    async def insert_approval_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO approval_events (
              event_id, organization_id, approval_id, event_type, actor_type, actor_id,
              payload_json, payload_redacted_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["organization_id"],
                data["approval_id"],
                data["event_type"],
                data.get("actor_type"),
                data.get("actor_id"),
                _json(data.get("payload", {})),
                _json(data.get("payload_redacted", data.get("payload", {}))),
                data["created_at"],
            ),
        )

    async def insert_artifact(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_artifacts (
              artifact_id, organization_id, task_id, step_id, tool_call_id, artifact_type,
              display_name, uri, content_type, size_bytes, checksum, sensitivity,
              metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["artifact_id"],
                data["organization_id"],
                data["task_id"],
                data.get("step_id"),
                data.get("tool_call_id"),
                data["artifact_type"],
                data["display_name"],
                data["uri"],
                data.get("content_type"),
                data.get("size_bytes"),
                data.get("checksum"),
                data["sensitivity"],
                _json(data.get("metadata", {})),
                data["created_at"],
            ),
        )

    async def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM task_artifacts WHERE artifact_id = ?",
            (artifact_id,),
        )
        return _artifact_from_row(dict(row)) if row else None

    async def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
        return [_artifact_from_row(dict(row)) for row in rows]

    async def list_artifacts_by_ids(self, artifact_ids: list[str]) -> list[dict[str, Any]]:
        if not artifact_ids:
            return []
        placeholders = ", ".join("?" for _ in artifact_ids)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM task_artifacts
            WHERE artifact_id IN ({placeholders})
            ORDER BY created_at ASC
            """,
            artifact_ids,
        )
        return [_artifact_from_row(dict(row)) for row in rows]

    async def upsert_collaboration_plan(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO collaboration_plans (
              collaboration_plan_id, organization_id, task_id, host_member_id, mode, max_rounds,
              participant_policy_json, success_criteria_json, risk_summary_json, status,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
              host_member_id = excluded.host_member_id,
              mode = excluded.mode,
              max_rounds = excluded.max_rounds,
              participant_policy_json = excluded.participant_policy_json,
              success_criteria_json = excluded.success_criteria_json,
              risk_summary_json = excluded.risk_summary_json,
              status = excluded.status,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["collaboration_plan_id"],
                data["organization_id"],
                data["task_id"],
                data["host_member_id"],
                data["mode"],
                data.get("max_rounds", 4),
                _json(data.get("participant_policy", {})),
                _json(data.get("success_criteria", [])),
                _json(data.get("risk_summary", {})),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_collaboration_plan(self, task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM collaboration_plans WHERE task_id = ?",
            (task_id,),
        )
        return _collaboration_plan_from_row(dict(row)) if row else None

    async def insert_participant(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_participants (
              participant_id, organization_id, task_id, member_id, role_in_task,
              participant_type, status, selection_reason, context_scope_json,
              allowed_skills_json, allowed_mcp_tools_json, capability_decision_id,
              output_summary_json, error_code, error_summary, trace_id, created_at,
              updated_at, removed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["participant_id"],
                data["organization_id"],
                data["task_id"],
                data["member_id"],
                data["role_in_task"],
                data.get("participant_type", "member"),
                data["status"],
                data["selection_reason"],
                _json(data.get("context_scope", {})),
                _json(data.get("allowed_skills", [])),
                _json(data.get("allowed_mcp_tools", [])),
                data.get("capability_decision_id"),
                _json(data.get("output_summary", {})),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("removed_at"),
            ),
        )

    async def list_participants(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM task_participants
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_participant_from_row(dict(row)) for row in rows]

    async def get_participant(self, participant_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM task_participants WHERE participant_id = ?",
            (participant_id,),
        )
        return _participant_from_row(dict(row)) if row else None

    async def update_participant(self, participant_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        values = _json_update_fields(
            fields,
            {
                "context_scope": "context_scope_json",
                "allowed_skills": "allowed_skills_json",
                "allowed_mcp_tools": "allowed_mcp_tools_json",
                "output_summary": "output_summary_json",
            },
        )
        unsupported = set(values) - PARTICIPANT_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported task_participants update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE task_participants SET {assignments} WHERE participant_id = ?",
            (*values.values(), participant_id),
        )

    async def insert_subtask(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO task_subtasks (
              subtask_id, organization_id, parent_task_id, participant_id, assigned_member_id,
              title, objective, status, sequence, context_scope_json, allowed_skills_json,
              allowed_mcp_tools_json, output_summary_json, source_refs_json, trace_id,
              error_code, error_summary, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["subtask_id"],
                data["organization_id"],
                data["parent_task_id"],
                data["participant_id"],
                data["assigned_member_id"],
                data["title"],
                data["objective"],
                data["status"],
                data["sequence"],
                _json(data.get("context_scope", {})),
                _json(data.get("allowed_skills", [])),
                _json(data.get("allowed_mcp_tools", [])),
                _json(data.get("output_summary", {})),
                _json(data.get("source_refs", [])),
                data.get("trace_id"),
                data.get("error_code"),
                data.get("error_summary"),
                data["created_at"],
                data["updated_at"],
                data.get("completed_at"),
            ),
        )

    async def list_subtasks(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM task_subtasks
            WHERE parent_task_id = ?
            ORDER BY sequence ASC
            """,
            (task_id,),
        )
        return [_subtask_from_row(dict(row)) for row in rows]

    async def get_subtask(self, subtask_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM task_subtasks WHERE subtask_id = ?",
            (subtask_id,),
        )
        return _subtask_from_row(dict(row)) if row else None

    async def update_subtask(self, subtask_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        values = _json_update_fields(
            fields,
            {
                "context_scope": "context_scope_json",
                "allowed_skills": "allowed_skills_json",
                "allowed_mcp_tools": "allowed_mcp_tools_json",
                "output_summary": "output_summary_json",
                "source_refs": "source_refs_json",
            },
        )
        unsupported = set(values) - SUBTASK_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(f"Unsupported task_subtasks update columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE task_subtasks SET {assignments} WHERE subtask_id = ?",
            (*values.values(), subtask_id),
        )

    async def insert_round(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO collaboration_rounds (
              round_id, organization_id, task_id, collaboration_plan_id, round_index, mode,
              status, participant_ids_json, max_turns, max_outputs, prompt_summary,
              round_summary_json, trace_id, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["round_id"],
                data["organization_id"],
                data["task_id"],
                data["collaboration_plan_id"],
                data["round_index"],
                data["mode"],
                data["status"],
                _json(data.get("participant_ids", [])),
                data.get("max_turns", 1),
                data.get("max_outputs", 10),
                data.get("prompt_summary"),
                _json(data.get("round_summary", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("completed_at"),
            ),
        )

    async def list_rounds(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM collaboration_rounds
            WHERE task_id = ?
            ORDER BY round_index ASC
            """,
            (task_id,),
        )
        return [_round_from_row(dict(row)) for row in rows]

    async def update_round(self, round_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return
        values = _json_update_fields(
            fields,
            {
                "participant_ids": "participant_ids_json",
                "round_summary": "round_summary_json",
            },
        )
        unsupported = set(values) - ROUND_UPDATE_COLUMNS
        if unsupported:
            raise ValueError(
                f"Unsupported collaboration_rounds update columns: {sorted(unsupported)}"
            )
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE collaboration_rounds SET {assignments} WHERE round_id = ?",
            (*values.values(), round_id),
        )

    async def insert_collaboration_output(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO collaboration_outputs (
              output_id, organization_id, task_id, collaboration_plan_id, round_id, subtask_id,
              participant_id, member_id, output_type, status, content_redacted, summary_json,
              source_refs_json, artifact_ids_json, trace_id, error_code, error_summary,
              created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["output_id"],
                data["organization_id"],
                data["task_id"],
                data["collaboration_plan_id"],
                data["round_id"],
                data["subtask_id"],
                data["participant_id"],
                data["member_id"],
                data.get("output_type", "analysis"),
                data["status"],
                data["content_redacted"],
                _json(data.get("summary", {})),
                _json(data.get("source_refs", [])),
                _json(data.get("artifact_ids", [])),
                data.get("trace_id"),
                data.get("error_code"),
                data.get("error_summary"),
                data["created_at"],
            ),
        )

    async def list_collaboration_outputs(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM collaboration_outputs
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_collaboration_output_from_row(dict(row)) for row in rows]

    async def insert_host_decision(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO host_decisions (
              decision_id, organization_id, task_id, collaboration_plan_id, host_member_id,
              decision_type, status, summary, rationale, source_refs_json, payload_json,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["decision_id"],
                data["organization_id"],
                data["task_id"],
                data["collaboration_plan_id"],
                data["host_member_id"],
                data["decision_type"],
                data["status"],
                data["summary"],
                data.get("rationale"),
                _json(data.get("source_refs", [])),
                _json(data.get("payload", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_host_decisions(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM host_decisions
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_host_decision_from_row(dict(row)) for row in rows]

    async def insert_routing_decision(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO collaboration_routing_decisions (
              routing_decision_id, organization_id, task_id, collaboration_plan_id,
              host_member_id, mode, status, selected_member_ids_json,
              rejected_candidates_json, routing_factors_json, risk_summary_json,
              boundary_summary_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["routing_decision_id"],
                data["organization_id"],
                data["task_id"],
                data.get("collaboration_plan_id"),
                data["host_member_id"],
                data["mode"],
                data["status"],
                _json(data.get("selected_member_ids", [])),
                _json(data.get("rejected_candidates", [])),
                _json(data.get("routing_factors", {})),
                _json(data.get("risk_summary", {})),
                _json(data.get("boundary_summary", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_routing_decisions(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM collaboration_routing_decisions
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_routing_decision_from_row(dict(row)) for row in rows]

    async def insert_handoff_record(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO collaboration_handoff_records (
              handoff_id, organization_id, task_id, collaboration_plan_id, subtask_id,
              from_participant_id, from_member_id, to_participant_id, to_member_id, reason,
              status, context_summary_json, boundary_summary_json, source_refs_json, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["handoff_id"],
                data["organization_id"],
                data["task_id"],
                data.get("collaboration_plan_id"),
                data["subtask_id"],
                data.get("from_participant_id"),
                data.get("from_member_id"),
                data.get("to_participant_id"),
                data["to_member_id"],
                data["reason"],
                data["status"],
                _json(data.get("context_summary", {})),
                _json(data.get("boundary_summary", {})),
                _json(data.get("source_refs", [])),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_handoff_records(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM collaboration_handoff_records
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_handoff_record_from_row(dict(row)) for row in rows]

    async def insert_context_boundary(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO collaboration_context_boundaries (
              boundary_id, organization_id, task_id, collaboration_plan_id, participant_id,
              member_id, context_scope_json, allowed_context_json, excluded_context_json,
              asset_scope_json, memory_scope, redaction_summary_json, status, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["boundary_id"],
                data["organization_id"],
                data["task_id"],
                data.get("collaboration_plan_id"),
                data.get("participant_id"),
                data["member_id"],
                _json(data.get("context_scope", {})),
                _json(data.get("allowed_context", [])),
                _json(data.get("excluded_context", [])),
                _json(data.get("asset_scope", [])),
                data.get("memory_scope", "member_private_only"),
                _json(data.get("redaction_summary", {})),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_context_boundaries(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM collaboration_context_boundaries
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task_id,),
        )
        return [_context_boundary_from_row(dict(row)) for row in rows]

    async def get_availability(self, member_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM member_availability WHERE member_id = ?",
            (member_id,),
        )
        return _availability_from_row(dict(row)) if row else None

    async def upsert_availability(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO member_availability (
              member_id, organization_id, status, capacity, current_load,
              unavailable_reason, schedule_json, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(member_id) DO UPDATE SET
              status = excluded.status,
              capacity = excluded.capacity,
              current_load = excluded.current_load,
              unavailable_reason = excluded.unavailable_reason,
              schedule_json = excluded.schedule_json,
              source = excluded.source,
              updated_at = excluded.updated_at
            """,
            (
                data["member_id"],
                data["organization_id"],
                data.get("status", "available"),
                data.get("capacity", 1),
                data.get("current_load", 0),
                data.get("unavailable_reason"),
                _json(data.get("schedule", {})),
                data.get("source", "manual"),
                data["updated_at"],
            ),
        )

    async def get_skill_policy(self, subject_type: str, subject_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM member_skill_policies
            WHERE organization_id = 'org_default' AND subject_type = ? AND subject_id = ?
            """,
            (subject_type, subject_id),
        )
        return _skill_policy_from_row(dict(row)) if row else None

    async def upsert_skill_policy(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO member_skill_policies (
              policy_id, organization_id, subject_type, subject_id, allowed_skills_json,
              denied_skills_json, allowed_mcp_tools_json, denied_mcp_tools_json,
              risk_policy_json, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id, subject_type, subject_id) DO UPDATE SET
              allowed_skills_json = excluded.allowed_skills_json,
              denied_skills_json = excluded.denied_skills_json,
              allowed_mcp_tools_json = excluded.allowed_mcp_tools_json,
              denied_mcp_tools_json = excluded.denied_mcp_tools_json,
              risk_policy_json = excluded.risk_policy_json,
              source = excluded.source,
              updated_at = excluded.updated_at
            """,
            (
                data["policy_id"],
                data["organization_id"],
                data["subject_type"],
                data["subject_id"],
                _json(data.get("allowed_skills", [])),
                _json(data.get("denied_skills", [])),
                _json(data.get("allowed_mcp_tools", [])),
                _json(data.get("denied_mcp_tools", [])),
                _json(data.get("risk_policy", {})),
                data.get("source", "manual"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def insert_shell_switch_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO shell_switch_events (
              event_id, organization_id, from_shell_id, to_shell_id, event_type,
              preview_json, blocked_mutations_json, business_values_unchanged,
              actor_member_id, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["organization_id"],
                data["from_shell_id"],
                data["to_shell_id"],
                data["event_type"],
                _json(data.get("preview", {})),
                _json(data.get("blocked_mutations", [])),
                1 if data.get("business_values_unchanged", True) else 0,
                data.get("actor_member_id"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def insert_shell_template_application(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO shell_template_applications (
              application_id, organization_id, shell_id, template_type, template_key,
              object_type, object_id, status, result_json, actor_member_id, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["application_id"],
                data["organization_id"],
                data["shell_id"],
                data["template_type"],
                data["template_key"],
                data.get("object_type"),
                data.get("object_id"),
                data["status"],
                _json(data.get("result", {})),
                data.get("actor_member_id"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )


def _task_from_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "success_criteria",
        "plan",
        "budget",
        "preflight",
        "artifact_plan",
        "retry_policy",
        "progress",
        "result",
    ):
        row[key] = json.loads(row.pop(f"{key}_json") or "{}")
    if not isinstance(row["success_criteria"], list):
        row["success_criteria"] = []
    return row


def _step_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["input"] = json.loads(row.pop("input_json") or "{}")
    row["output"] = json.loads(row.pop("output_json") or "{}")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row.pop("payload_json", None)
    return row


def _planner_decision_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    row["capability_snapshot"] = json.loads(row.pop("capability_snapshot_json") or "{}")
    row["skill_match_refs"] = json.loads(row.pop("skill_match_refs_json") or "[]")
    row["mcp_tool_refs"] = json.loads(row.pop("mcp_tool_refs_json") or "[]")
    row["model_hint"] = json.loads(row.pop("model_hint_json") or "{}")
    return row


def _agent_loop_iteration_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["plan_delta"] = json.loads(row.pop("plan_delta_json") or "{}")
    row["selected_action"] = json.loads(row.pop("selected_action_json") or "{}")
    row["tool_call_refs"] = json.loads(row.pop("tool_call_refs_json") or "[]")
    row["safety_decision_refs"] = json.loads(row.pop("safety_decision_refs_json") or "[]")
    row["evaluation_result"] = json.loads(row.pop("evaluation_result_json") or "{}")
    row["budget_snapshot"] = json.loads(row.pop("budget_snapshot_json") or "{}")
    return row


def _task_observation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["source_ref"] = json.loads(row.pop("source_ref_json") or "{}")
    row["key_facts"] = json.loads(row.pop("key_facts_json") or "[]")
    row["errors"] = json.loads(row.pop("errors_json") or "[]")
    row["artifact_refs"] = json.loads(row.pop("artifact_refs_json") or "[]")
    row["payload_redacted"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row["untrusted_instructions_detected"] = bool(row["untrusted_instructions_detected"])
    return row


def _task_retry_plan_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["suggested_actions"] = json.loads(row.pop("suggested_actions_json") or "[]")
    row["budget_delta"] = json.loads(row.pop("budget_delta_json") or "{}")
    return row


def _task_reflection_candidate_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_json") or "{}")
    row["source_refs"] = json.loads(row.pop("source_refs_json") or "[]")
    return row


def _model_plan_candidate_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["steps"] = json.loads(row.pop("steps_json") or "[]")
    row["success_criteria"] = json.loads(row.pop("success_criteria_json") or "[]")
    row["assumptions"] = json.loads(row.pop("assumptions_json") or "[]")
    row["missing_information"] = json.loads(row.pop("missing_information_json") or "[]")
    row["risk_hints"] = json.loads(row.pop("risk_hints_json") or "[]")
    row["required_capabilities"] = json.loads(row.pop("required_capabilities_json") or "[]")
    row["required_assets"] = json.loads(row.pop("required_assets_json") or "[]")
    row["model_assist"] = json.loads(row.pop("model_assist_json") or "{}")
    return row


def _plan_verification_result_from_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in (
        "schema_valid",
        "mode_allowed",
        "step_type_allowed",
        "capability_available",
        "asset_handle_allowed",
        "risk_level_acceptable",
        "approval_strategy_present",
        "budget_within_limit",
        "no_direct_secret",
        "no_direct_shell_command_from_model",
    ):
        row[key] = bool(row[key])
    row["issues"] = json.loads(row.pop("issues_json") or "[]")
    return row


def _plan_policy_prune_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["original_step"] = json.loads(row.pop("original_step_json") or "{}")
    row["pruned_step"] = json.loads(row.pop("pruned_step_json") or "{}")
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    return row


def _planner_capability_candidate_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _agent_next_action_decision_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["plan_delta"] = json.loads(row.pop("plan_delta_json") or "{}")
    row["needs_user_input"] = bool(row["needs_user_input"])
    row["needs_approval"] = bool(row["needs_approval"])
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    row["budget_snapshot"] = json.loads(row.pop("budget_snapshot_json") or "{}")
    return row


def _tool_failure_recovery_plan_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["suggested_actions"] = json.loads(row.pop("suggested_actions_json") or "[]")
    row["retry_allowed"] = bool(row["retry_allowed"])
    row["bypass_controls"] = bool(row["bypass_controls"])
    return row


def _job_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_json") or "{}")
    return row


def _tool_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["input_schema"] = json.loads(row.pop("input_schema_json") or "{}")
    row["output_schema"] = json.loads(row.pop("output_schema_json") or "{}")
    row["risk_policy"] = json.loads(row.pop("risk_policy_json") or "{}")
    row["required_handle_types"] = json.loads(row.pop("required_handle_types_json") or "[]")
    adapter_config = row.pop("adapter_config_json", None)
    row["adapter_config"] = json.loads(adapter_config or "{}")
    row.setdefault("trust_level", "local")
    row.setdefault("bundle_id", None)
    row.setdefault("skill_id", None)
    row.setdefault("mcp_server_id", None)
    row.setdefault("mcp_tool_id", None)
    return row


def _tool_call_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["args_redacted"] = json.loads(row.pop("args_redacted_json") or "{}")
    row["result_redacted"] = json.loads(row.pop("result_redacted_json") or "{}")
    row["handle_ids"] = json.loads(row.pop("handle_ids_json") or "[]")
    row["safety_decision"] = json.loads(row.pop("safety_decision_json") or "{}")
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json", None) or "{}")
    row["resolved_asset_refs"] = json.loads(row.pop("resolved_asset_refs_json", None) or "[]")
    row["artifact_ids"] = json.loads(row.pop("artifact_ids_json") or "[]")
    return row


def _approval_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload_redacted"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row["options"] = json.loads(row.pop("options_json") or "[]")
    edited = row.pop("edited_payload_json")
    row["edited_payload"] = json.loads(edited) if edited else None
    return row


def _artifact_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _collaboration_plan_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["participant_policy"] = json.loads(row.pop("participant_policy_json") or "{}")
    row["success_criteria"] = json.loads(row.pop("success_criteria_json") or "[]")
    row["risk_summary"] = json.loads(row.pop("risk_summary_json") or "{}")
    return row


def _participant_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["context_scope"] = json.loads(row.pop("context_scope_json") or "{}")
    row["allowed_skills"] = json.loads(row.pop("allowed_skills_json") or "[]")
    row["allowed_mcp_tools"] = json.loads(row.pop("allowed_mcp_tools_json") or "[]")
    row["output_summary"] = json.loads(row.pop("output_summary_json") or "{}")
    return row


def _subtask_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["context_scope"] = json.loads(row.pop("context_scope_json") or "{}")
    row["allowed_skills"] = json.loads(row.pop("allowed_skills_json") or "[]")
    row["allowed_mcp_tools"] = json.loads(row.pop("allowed_mcp_tools_json") or "[]")
    row["output_summary"] = json.loads(row.pop("output_summary_json") or "{}")
    row["source_refs"] = json.loads(row.pop("source_refs_json") or "[]")
    return row


def _round_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["participant_ids"] = json.loads(row.pop("participant_ids_json") or "[]")
    row["round_summary"] = json.loads(row.pop("round_summary_json") or "{}")
    return row


def _collaboration_output_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["summary"] = json.loads(row.pop("summary_json") or "{}")
    row["source_refs"] = json.loads(row.pop("source_refs_json") or "[]")
    row["artifact_ids"] = json.loads(row.pop("artifact_ids_json") or "[]")
    return row


def _host_decision_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["source_refs"] = json.loads(row.pop("source_refs_json") or "[]")
    row["payload"] = json.loads(row.pop("payload_json") or "{}")
    return row


def _routing_decision_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["selected_member_ids"] = json.loads(row.pop("selected_member_ids_json") or "[]")
    row["rejected_candidates"] = json.loads(row.pop("rejected_candidates_json") or "[]")
    row["routing_factors"] = json.loads(row.pop("routing_factors_json") or "{}")
    row["risk_summary"] = json.loads(row.pop("risk_summary_json") or "{}")
    row["boundary_summary"] = json.loads(row.pop("boundary_summary_json") or "{}")
    return row


def _handoff_record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["context_summary"] = json.loads(row.pop("context_summary_json") or "{}")
    row["boundary_summary"] = json.loads(row.pop("boundary_summary_json") or "{}")
    row["source_refs"] = json.loads(row.pop("source_refs_json") or "[]")
    return row


def _context_boundary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["context_scope"] = json.loads(row.pop("context_scope_json") or "{}")
    row["allowed_context"] = json.loads(row.pop("allowed_context_json") or "[]")
    row["excluded_context"] = json.loads(row.pop("excluded_context_json") or "[]")
    row["asset_scope"] = json.loads(row.pop("asset_scope_json") or "[]")
    row["redaction_summary"] = json.loads(row.pop("redaction_summary_json") or "{}")
    return row


def _availability_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["schedule"] = json.loads(row.pop("schedule_json") or "{}")
    return row


def _skill_policy_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["allowed_skills"] = json.loads(row.pop("allowed_skills_json") or "[]")
    row["denied_skills"] = json.loads(row.pop("denied_skills_json") or "[]")
    row["allowed_mcp_tools"] = json.loads(row.pop("allowed_mcp_tools_json") or "[]")
    row["denied_mcp_tools"] = json.loads(row.pop("denied_mcp_tools_json") or "[]")
    row["risk_policy"] = json.loads(row.pop("risk_policy_json") or "{}")
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
