from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class ReleaseRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_release_gate(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO release_gates (
              release_gate_id, organization_id, status, scope_json, required_checks_json,
              summary_json, blocker_count, high_count, medium_count, low_count,
              created_by_member_id, started_at, completed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["release_gate_id"],
                data["organization_id"],
                data["status"],
                _json(data.get("scope", {})),
                _json(data.get("required_checks", [])),
                _json(data.get("summary", {})),
                data.get("blocker_count", 0),
                data.get("high_count", 0),
                data.get("medium_count", 0),
                data.get("low_count", 0),
                data.get("created_by_member_id"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def update_release_gate(self, release_gate_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "scope": "scope_json",
                "required_checks": "required_checks_json",
                "summary": "summary_json",
            },
        )
        if not values:
            return
        allowed = {
            "status",
            "scope_json",
            "required_checks_json",
            "summary_json",
            "blocker_count",
            "high_count",
            "medium_count",
            "low_count",
            "started_at",
            "completed_at",
            "updated_at",
        }
        unsupported = set(values) - allowed
        if unsupported:
            raise ValueError(f"Unsupported release gate columns: {sorted(unsupported)}")
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE release_gates SET {assignments} WHERE release_gate_id = ?",
            (*values.values(), release_gate_id),
        )

    async def get_release_gate(self, release_gate_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM release_gates WHERE release_gate_id = ?",
            (release_gate_id,),
        )
        return _release_gate_from_row(dict(row)) if row else None

    async def list_release_gates(
        self,
        organization_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT * FROM release_gates
            WHERE organization_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (organization_id, limit),
        )
        return [_release_gate_from_row(dict(row)) for row in rows]

    async def insert_evidence(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO release_evidence (
              evidence_id, release_gate_id, evidence_type, source_type, source_id,
              checksum, summary_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["evidence_id"],
                data["release_gate_id"],
                data["evidence_type"],
                data["source_type"],
                data["source_id"],
                data.get("checksum"),
                _json(data.get("summary", {})),
                data["status"],
                data["created_at"],
            ),
        )

    async def list_evidence(self, release_gate_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT * FROM release_evidence
            WHERE release_gate_id = ?
            ORDER BY created_at ASC
            """,
            (release_gate_id,),
        )
        return [_evidence_from_row(dict(row)) for row in rows]

    async def list_design_gaps(self, *, status: str | None = None) -> list[dict[str, Any]]:
        where = ""
        params: tuple[Any, ...] = ()
        if status is not None:
            where = "WHERE status = ?"
            params = (status,)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM design_gaps
            {where}
            ORDER BY module_name ASC, gap_id ASC
            """,
            params,
        )
        return [_design_gap_from_row(dict(row)) for row in rows]

    async def list_failed_eval_results(
        self,
        *,
        release_gate_id: str | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        where = "WHERE status != 'passed'"
        params: list[Any] = []
        if release_gate_id is not None:
            where += (
                " AND eval_run_id IN ("
                "SELECT eval_run_id FROM eval_runs WHERE release_gate_id = ?"
                ")"
            )
            params.append(release_gate_id)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM eval_results
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        return [_eval_result_from_row(dict(row)) for row in rows]

    async def insert_finding(self, data: dict[str, Any]) -> str:
        await self._db.execute(
            """
            INSERT INTO release_findings (
              finding_id, release_gate_id, severity, category, title, description,
              affected_module, evidence_refs_json, status, owner, accepted_reason,
              accepted_until, verification_run_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["finding_id"],
                data["release_gate_id"],
                data["severity"],
                data["category"],
                data["title"],
                data["description"],
                data["affected_module"],
                _json(data.get("evidence_refs", [])),
                data["status"],
                data.get("owner"),
                data.get("accepted_reason"),
                data.get("accepted_until"),
                data.get("verification_run_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )
        return str(data["finding_id"])

    async def list_findings(self, release_gate_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT * FROM release_findings
            WHERE release_gate_id = ?
            ORDER BY
              CASE severity
                WHEN 'critical' THEN 0
                WHEN 'high' THEN 1
                WHEN 'medium' THEN 2
                WHEN 'low' THEN 3
                ELSE 4
              END,
              created_at ASC
            """,
            (release_gate_id,),
        )
        return [_finding_from_row(dict(row)) for row in rows]

    async def upsert_eval_suite(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO eval_suites (
              suite_id, name, category, description, required, threshold_json,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(suite_id) DO UPDATE SET
              name = excluded.name,
              category = excluded.category,
              description = excluded.description,
              required = excluded.required,
              threshold_json = excluded.threshold_json,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["suite_id"],
                data["name"],
                data["category"],
                data.get("description"),
                1 if data.get("required", True) else 0,
                _json(data.get("threshold", {})),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def upsert_eval_case(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO eval_cases (
              case_id, suite_id, case_key, title, input_json, expected_json,
              tags_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(suite_id, case_key) DO UPDATE SET
              title = excluded.title,
              input_json = excluded.input_json,
              expected_json = excluded.expected_json,
              tags_json = excluded.tags_json,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["case_id"],
                data["suite_id"],
                data["case_key"],
                data["title"],
                _json(data.get("input", {})),
                _json(data.get("expected", {})),
                _json(data.get("tags", [])),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_eval_suites(
        self,
        *,
        required: bool | None = None,
        status: str | None = "active",
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if required is not None:
            where.append("required = ?")
            params.append(1 if required else 0)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        rows = await self._db.fetch_all(
            f"SELECT * FROM eval_suites {clause} ORDER BY category ASC, suite_id ASC",
            params,
        )
        return [_eval_suite_from_row(dict(row)) for row in rows]

    async def list_eval_cases(self, suite_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT * FROM eval_cases
            WHERE suite_id = ? AND status = 'active'
            ORDER BY case_key ASC
            """,
            (suite_id,),
        )
        return [_eval_case_from_row(dict(row)) for row in rows]

    async def insert_eval_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO eval_runs (
              eval_run_id, release_gate_id, suite_id, status, total_cases,
              passed_cases, failed_cases, metrics_json, summary_json, error_code,
              error_summary, trace_id, started_at, completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["eval_run_id"],
                data.get("release_gate_id"),
                data.get("suite_id"),
                data["status"],
                data.get("total_cases", 0),
                data.get("passed_cases", 0),
                data.get("failed_cases", 0),
                _json(data.get("metrics", {})),
                _json(data.get("summary", {})),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("trace_id"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def update_eval_run(self, eval_run_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(fields, {"metrics": "metrics_json", "summary": "summary_json"})
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE eval_runs SET {assignments} WHERE eval_run_id = ?",
            (*values.values(), eval_run_id),
        )

    async def get_eval_run(self, eval_run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM eval_runs WHERE eval_run_id = ?",
            (eval_run_id,),
        )
        return _eval_run_from_row(dict(row)) if row else None

    async def insert_eval_result(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO eval_results (
              eval_result_id, eval_run_id, suite_id, case_id, case_key, status,
              score, expected_json, actual_json, assertion_summary, finding_id,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["eval_result_id"],
                data["eval_run_id"],
                data["suite_id"],
                data.get("case_id"),
                data["case_key"],
                data["status"],
                data.get("score", 0),
                _json(data.get("expected", {})),
                _json(data.get("actual", {})),
                data.get("assertion_summary"),
                data.get("finding_id"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def upsert_red_team_scenario(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO red_team_scenarios (
              scenario_id, category, title, attack_input_json, expected_block_json,
              severity_if_failed, tags_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scenario_id) DO UPDATE SET
              category = excluded.category,
              title = excluded.title,
              attack_input_json = excluded.attack_input_json,
              expected_block_json = excluded.expected_block_json,
              severity_if_failed = excluded.severity_if_failed,
              tags_json = excluded.tags_json,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["scenario_id"],
                data["category"],
                data["title"],
                _json(data.get("attack_input", {})),
                _json(data.get("expected_block", {})),
                data["severity_if_failed"],
                _json(data.get("tags", [])),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_red_team_scenarios(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT * FROM red_team_scenarios
            WHERE status = 'active'
            ORDER BY category, scenario_id
            """
        )
        return [_red_team_scenario_from_row(dict(row)) for row in rows]

    async def insert_security_audit_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO security_audit_runs (
              audit_run_id, release_gate_id, status, total_scenarios,
              passed_scenarios, failed_scenarios, critical_failures, high_failures,
              result_json, trace_id, started_at, completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["audit_run_id"],
                data.get("release_gate_id"),
                data["status"],
                data.get("total_scenarios", 0),
                data.get("passed_scenarios", 0),
                data.get("failed_scenarios", 0),
                data.get("critical_failures", 0),
                data.get("high_failures", 0),
                _json(data.get("result", {})),
                data.get("trace_id"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def get_security_audit_run(self, audit_run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM security_audit_runs WHERE audit_run_id = ?",
            (audit_run_id,),
        )
        return _security_audit_run_from_row(dict(row)) if row else None

    async def insert_integrity_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO integrity_check_runs (
              integrity_run_id, release_gate_id, check_type, status, checked_count,
              failed_count, threshold_json, result_json, trace_id, started_at,
              completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["integrity_run_id"],
                data.get("release_gate_id"),
                data["check_type"],
                data["status"],
                data.get("checked_count", 0),
                data.get("failed_count", 0),
                _json(data.get("threshold", {})),
                _json(data.get("result", {})),
                data.get("trace_id"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def insert_backup_job(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO backup_jobs (
              backup_job_id, organization_id, status, scope_json, output_uri,
              manifest_json, checksum, size_bytes, error_code, error_summary,
              created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["backup_job_id"],
                data["organization_id"],
                data["status"],
                _json(data.get("scope", {})),
                data.get("output_uri"),
                _json(data.get("manifest", {})),
                data.get("checksum"),
                data.get("size_bytes"),
                data.get("error_code"),
                data.get("error_summary"),
                data["created_at"],
                data.get("completed_at"),
            ),
        )

    async def get_backup_job(self, backup_job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM backup_jobs WHERE backup_job_id = ?",
            (backup_job_id,),
        )
        return _backup_job_from_row(dict(row)) if row else None

    async def insert_restore_job(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO restore_jobs (
              restore_job_id, organization_id, backup_job_id, status, input_uri,
              restore_plan_json, result_json, checksum_verified, error_code,
              error_summary, created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["restore_job_id"],
                data["organization_id"],
                data.get("backup_job_id"),
                data["status"],
                data["input_uri"],
                _json(data.get("restore_plan", {})),
                _json(data.get("result", {})),
                1 if data.get("checksum_verified", False) else 0,
                data.get("error_code"),
                data.get("error_summary"),
                data["created_at"],
                data.get("completed_at"),
            ),
        )

    async def get_restore_job(self, restore_job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM restore_jobs WHERE restore_job_id = ?",
            (restore_job_id,),
        )
        return _restore_job_from_row(dict(row)) if row else None

    async def insert_benchmark_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO benchmark_runs (
              benchmark_run_id, release_gate_id, benchmark_type, status,
              scenario_json, metrics_json, resource_summary_json, trace_id,
              started_at, completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["benchmark_run_id"],
                data.get("release_gate_id"),
                data["benchmark_type"],
                data["status"],
                _json(data.get("scenario", {})),
                _json(data.get("metrics", {})),
                _json(data.get("resource_summary", {})),
                data.get("trace_id"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def get_benchmark_run(self, benchmark_run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM benchmark_runs WHERE benchmark_run_id = ?",
            (benchmark_run_id,),
        )
        return _benchmark_run_from_row(dict(row)) if row else None

    async def insert_diagnostic_bundle(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO diagnostic_bundles (
              bundle_id, organization_id, scope_json, redaction_policy_json,
              output_uri, checksum, size_bytes, status, created_by_member_id,
              created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["bundle_id"],
                data["organization_id"],
                _json(data.get("scope", {})),
                _json(data.get("redaction_policy", {})),
                data.get("output_uri"),
                data.get("checksum"),
                data.get("size_bytes"),
                data["status"],
                data.get("created_by_member_id"),
                data["created_at"],
                data.get("completed_at"),
            ),
        )

    async def get_diagnostic_bundle(self, bundle_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM diagnostic_bundles WHERE bundle_id = ?",
            (bundle_id,),
        )
        return _diagnostic_bundle_from_row(dict(row)) if row else None

    async def upsert_release_report(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO release_reports (
              report_id, release_gate_id, organization_id, decision, summary_json,
              evidence_summary_json, findings_summary_json, output_uri, checksum, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(release_gate_id) DO UPDATE SET
              report_id = excluded.report_id,
              decision = excluded.decision,
              summary_json = excluded.summary_json,
              evidence_summary_json = excluded.evidence_summary_json,
              findings_summary_json = excluded.findings_summary_json,
              output_uri = excluded.output_uri,
              checksum = excluded.checksum,
              created_at = excluded.created_at
            """,
            (
                data["report_id"],
                data["release_gate_id"],
                data["organization_id"],
                data["decision"],
                _json(data.get("summary", {})),
                _json(data.get("evidence_summary", {})),
                _json(data.get("findings_summary", {})),
                data.get("output_uri"),
                data.get("checksum"),
                data["created_at"],
            ),
        )

    async def get_release_report(self, release_gate_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM release_reports WHERE release_gate_id = ?",
            (release_gate_id,),
        )
        return _release_report_from_row(dict(row)) if row else None

    async def count_rows(
        self,
        table_name: str,
        where_sql: str = "",
        params: tuple[Any, ...] = (),
    ) -> int:
        if not table_name.replace("_", "").isalnum():
            raise ValueError("Invalid table name")
        row = await self._db.fetch_one(
            f"SELECT COUNT(*) AS count FROM {table_name} {where_sql}",
            params,
        )
        return int(row["count"]) if row else 0

    async def latest_schema_migration(self) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT version, name, checksum, applied_at, status
            FROM schema_migrations
            ORDER BY version DESC
            LIMIT 1
            """
        )
        return dict(row) if row else None

    async def table_names(self) -> list[str]:
        rows = await self._db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
        return [str(row["name"]) for row in rows]

    async def table_columns(self, table_name: str) -> list[str]:
        if not table_name.replace("_", "").isalnum():
            raise ValueError("Invalid table name")
        rows = await self._db.fetch_all(f"PRAGMA table_info({table_name})")
        return [str(row["name"]) for row in rows]

    async def numeric_values(
        self,
        table_name: str,
        column_name: str,
        where_sql: str = "",
        params: tuple[Any, ...] = (),
    ) -> list[float]:
        if not table_name.replace("_", "").isalnum():
            raise ValueError("Invalid table name")
        if not column_name.replace("_", "").isalnum():
            raise ValueError("Invalid column name")
        rows = await self._db.fetch_all(
            f"SELECT {column_name} AS value FROM {table_name} {where_sql}",
            params,
        )
        values: list[float] = []
        for row in rows:
            try:
                values.append(float(row["value"]))
            except (TypeError, ValueError):
                continue
        return values

    async def scan_redacted_text_sources(self) -> list[dict[str, str]]:
        sources = [
            ("audit_events", "audit_id", "payload_redacted_json"),
            ("trace_spans", "span_id", "input_json"),
            ("trace_spans", "span_id", "output_json"),
            ("trace_spans", "span_id", "metadata_json"),
            ("messages", "message_id", "content_text"),
            ("messages", "message_id", "content_json"),
            ("task_events", "event_id", "payload_redacted_json"),
            ("task_artifacts", "artifact_id", "uri"),
            ("task_artifacts", "artifact_id", "metadata_json"),
            ("tool_calls", "tool_call_id", "args_redacted_json"),
            ("tool_calls", "tool_call_id", "result_redacted_json"),
            ("tool_calls", "tool_call_id", "safety_decision_json"),
            ("tool_calls", "tool_call_id", "policy_snapshot_json"),
            ("tool_action_policies", "policy_id", "output_dlp_policy_json"),
            ("tool_policy_decisions", "decision_id", "policy_snapshot_json"),
            ("tool_policy_decisions", "decision_id", "reason_codes_json"),
            ("tool_output_dlp_reports", "dlp_report_id", "findings_json"),
            ("tool_output_dlp_reports", "dlp_report_id", "redacted_preview"),
            ("mcp_process_policy_checks", "check_id", "policy_snapshot_json"),
            ("mcp_process_policy_checks", "check_id", "reason_codes_json"),
            ("execution_boundary_diagnostics", "diagnostic_id", "summary_json"),
            ("mcp_calls", "mcp_call_id", "request_redacted_json"),
            ("mcp_calls", "mcp_call_id", "response_redacted_json"),
            ("mcp_calls", "mcp_call_id", "policy_snapshot_json"),
            ("skill_runs", "skill_run_id", "input_redacted_json"),
            ("skill_runs", "skill_run_id", "output_redacted_json"),
            ("memory_items", "memory_id", "summary_text"),
            ("memory_items", "memory_id", "payload_json"),
            ("dialogue_states", "dialogue_state_id", "goal_history_json"),
            ("dialogue_states", "dialogue_state_id", "known_constraints_json"),
            ("dialogue_states", "dialogue_state_id", "decisions_made_json"),
            ("semantic_intent_candidates", "semantic_candidate_id", "reason_codes_json"),
            ("semantic_intent_candidates", "semantic_candidate_id", "conflicts_json"),
            ("low_confidence_decision_reviews", "review_id", "rule_decision_json"),
            ("low_confidence_decision_reviews", "review_id", "verifier_suggestion_json"),
            ("semantic_review_requests", "semantic_review_id", "redacted_request_json"),
            ("semantic_review_suggestions", "suggestion_id", "suggestion_json"),
            ("semantic_review_model_calls", "model_call_id", "usage_json"),
            ("semantic_review_merge_results", "merge_id", "merged_clarification_json"),
            ("embedding_provider_configs", "provider_id", "config_json"),
            ("retrieval_rerank_runs", "rerank_run_id", "scoring_policy_json"),
            ("retrieval_suppressed_items", "suppressed_id", "metadata_json"),
            ("knowledge_retrieval_logs", "retrieval_id", "ranking_json"),
            ("knowledge_retrieval_logs", "retrieval_id", "retrieval_sources_json"),
            ("retrieval_quality_reports", "report_id", "summary_json"),
            ("retrieval_quality_reports", "report_id", "metrics_json"),
            ("safety_decisions", "safety_decision_id", "payload_summary_json"),
            ("collaboration_outputs", "output_id", "content_redacted"),
        ]
        results: list[dict[str, str]] = []
        for table, id_column, text_column in sources:
            rows = await self._db.fetch_all(
                f"""
                SELECT {id_column} AS row_id, {text_column} AS value
                FROM {table}
                WHERE {text_column} IS NOT NULL
                LIMIT 500
                """
            )
            for row in rows:
                results.append(
                    {
                        "table": table,
                        "column": text_column,
                        "row_id": str(row["row_id"]),
                        "value": str(row["value"]),
                    }
                )
        return results


def _release_gate_from_row(row: dict[str, Any]) -> dict[str, Any]:
    scope = _load(row.pop("scope_json"))
    required_checks = _load(row.pop("required_checks_json"))
    summary = _load(row.pop("summary_json"))
    return {**row, "scope": scope, "required_checks": required_checks, "summary": summary}


def _evidence_from_row(row: dict[str, Any]) -> dict[str, Any]:
    summary = _load(row.pop("summary_json"))
    return {**row, "summary": summary}


def _finding_from_row(row: dict[str, Any]) -> dict[str, Any]:
    evidence_refs = _load(row.pop("evidence_refs_json"))
    return {**row, "evidence_refs": evidence_refs}


def _design_gap_from_row(row: dict[str, Any]) -> dict[str, Any]:
    acceptance_tests = _load(row.pop("acceptance_tests_json"))
    return {**row, "acceptance_tests": acceptance_tests}


def _eval_suite_from_row(row: dict[str, Any]) -> dict[str, Any]:
    threshold = _load(row.pop("threshold_json"))
    required = bool(row.pop("required"))
    return {**row, "required": required, "threshold": threshold}


def _eval_case_from_row(row: dict[str, Any]) -> dict[str, Any]:
    input_value = _load(row.pop("input_json"))
    expected = _load(row.pop("expected_json"))
    tags = _load(row.pop("tags_json"))
    return {**row, "input": input_value, "expected": expected, "tags": tags}


def _eval_run_from_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = _load(row.pop("metrics_json"))
    summary = _load(row.pop("summary_json"))
    return {**row, "metrics": metrics, "summary": summary}


def _eval_result_from_row(row: dict[str, Any]) -> dict[str, Any]:
    expected = _load(row.pop("expected_json"))
    actual = _load(row.pop("actual_json"))
    return {**row, "expected": expected, "actual": actual}


def _red_team_scenario_from_row(row: dict[str, Any]) -> dict[str, Any]:
    attack_input = _load(row.pop("attack_input_json"))
    expected_block = _load(row.pop("expected_block_json"))
    tags = _load(row.pop("tags_json"))
    return {
        **row,
        "attack_input": attack_input,
        "expected_block": expected_block,
        "tags": tags,
    }


def _security_audit_run_from_row(row: dict[str, Any]) -> dict[str, Any]:
    result = _load(row.pop("result_json"))
    return {**row, "result": result}


def _backup_job_from_row(row: dict[str, Any]) -> dict[str, Any]:
    scope = _load(row.pop("scope_json"))
    manifest = _load(row.pop("manifest_json"))
    return {**row, "scope": scope, "manifest": manifest}


def _restore_job_from_row(row: dict[str, Any]) -> dict[str, Any]:
    restore_plan = _load(row.pop("restore_plan_json"))
    result = _load(row.pop("result_json"))
    checksum_verified = bool(row.pop("checksum_verified"))
    return {
        **row,
        "restore_plan": restore_plan,
        "result": result,
        "checksum_verified": checksum_verified,
    }


def _benchmark_run_from_row(row: dict[str, Any]) -> dict[str, Any]:
    scenario = _load(row.pop("scenario_json"))
    metrics = _load(row.pop("metrics_json"))
    resource_summary = _load(row.pop("resource_summary_json"))
    return {
        **row,
        "scenario": scenario,
        "metrics": metrics,
        "resource_summary": resource_summary,
    }


def _diagnostic_bundle_from_row(row: dict[str, Any]) -> dict[str, Any]:
    scope = _load(row.pop("scope_json"))
    redaction_policy = _load(row.pop("redaction_policy_json"))
    return {**row, "scope": scope, "redaction_policy": redaction_policy}


def _release_report_from_row(row: dict[str, Any]) -> dict[str, Any]:
    summary = _load(row.pop("summary_json"))
    evidence_summary = _load(row.pop("evidence_summary_json"))
    findings_summary = _load(row.pop("findings_summary_json"))
    return {
        **row,
        "summary": summary,
        "evidence_summary": evidence_summary,
        "findings_summary": findings_summary,
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: str | None) -> Any:
    if value is None or value == "":
        return None
    return json.loads(value)


def _json_update_fields(fields: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = mapping.get(key, key)
        values[column] = _json(value) if column.endswith("_json") else value
    return values
