from __future__ import annotations

import json
from typing import Any

from app.db.session import Database


class SkillMcpRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_install_job(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO plugin_install_jobs (
              job_id, organization_id, bundle_id, idempotency_key, job_type, status,
              payload_json, result_json, rollback_result_json, error_code, error_summary,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_key) DO UPDATE SET
              status = excluded.status,
              result_json = excluded.result_json,
              error_code = excluded.error_code,
              error_summary = excluded.error_summary,
              updated_at = excluded.updated_at
            """,
            (
                data["job_id"],
                data["organization_id"],
                data.get("bundle_id"),
                data["idempotency_key"],
                data["job_type"],
                data["status"],
                _json(data.get("payload", {})),
                _json(data.get("result", {})),
                _json(data.get("rollback_result", {})),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def insert_bundle(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO plugin_bundles (
              bundle_id, organization_id, display_name, description, author, bundle_revision,
              source_type, source_uri, package_uri, manifest_hash, signature_status,
              trust_level, status, permission_summary_json, risk_summary_json, manifest_json,
              installed_by_member_id, installed_at, enabled_at, disabled_at, revoked_at,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bundle_id) DO UPDATE SET
              display_name = excluded.display_name,
              description = excluded.description,
              author = excluded.author,
              bundle_revision = excluded.bundle_revision,
              source_uri = excluded.source_uri,
              package_uri = excluded.package_uri,
              manifest_hash = excluded.manifest_hash,
              signature_status = excluded.signature_status,
              trust_level = excluded.trust_level,
              permission_summary_json = excluded.permission_summary_json,
              risk_summary_json = excluded.risk_summary_json,
              manifest_json = excluded.manifest_json,
              installed_by_member_id = excluded.installed_by_member_id,
              installed_at = excluded.installed_at,
              updated_at = excluded.updated_at
            """,
            (
                data["bundle_id"],
                data["organization_id"],
                data["display_name"],
                data.get("description"),
                data.get("author"),
                data["bundle_revision"],
                data["source_type"],
                data.get("source_uri"),
                data.get("package_uri"),
                data["manifest_hash"],
                data["signature_status"],
                data["trust_level"],
                data["status"],
                _json(data.get("permission_summary", {})),
                _json(data.get("risk_summary", {})),
                _json(data.get("manifest", {})),
                data.get("installed_by_member_id"),
                data.get("installed_at"),
                data.get("enabled_at"),
                data.get("disabled_at"),
                data.get("revoked_at"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_bundle(self, bundle_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM plugin_bundles WHERE bundle_id = ?",
            (bundle_id,),
        )
        return _bundle_from_row(dict(row)) if row else None

    async def list_bundles(self, status: str | None = None) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM plugin_bundles
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            """,
            params,
        )
        return [_bundle_from_row(dict(row)) for row in rows]

    async def update_bundle(self, bundle_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "permission_summary": "permission_summary_json",
                "risk_summary": "risk_summary_json",
                "manifest": "manifest_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE plugin_bundles SET {assignments} WHERE bundle_id = ?",
            (*values.values(), bundle_id),
        )

    async def delete_files_for_bundle(self, bundle_id: str) -> None:
        await self._db.execute("DELETE FROM plugin_files WHERE bundle_id = ?", (bundle_id,))

    async def insert_plugin_file(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO plugin_files (
              file_id, bundle_id, relative_path, file_type, size_bytes, checksum,
              sensitivity, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bundle_id, relative_path) DO UPDATE SET
              file_type = excluded.file_type,
              size_bytes = excluded.size_bytes,
              checksum = excluded.checksum,
              sensitivity = excluded.sensitivity,
              created_at = excluded.created_at
            """,
            (
                data["file_id"],
                data["bundle_id"],
                data["relative_path"],
                data["file_type"],
                data.get("size_bytes"),
                data["checksum"],
                data["sensitivity"],
                data["created_at"],
            ),
        )

    async def insert_skill(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skills (
              skill_id, organization_id, bundle_id, name, display_name, description,
              entrypoint_path, instructions, trigger_json, input_schema_json,
              output_schema_json, required_tools_json, required_assets_json,
              permission_json, risk_policy_json, eval_summary_json, steps_json,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_id) DO UPDATE SET
              display_name = excluded.display_name,
              description = excluded.description,
              entrypoint_path = excluded.entrypoint_path,
              instructions = excluded.instructions,
              trigger_json = excluded.trigger_json,
              input_schema_json = excluded.input_schema_json,
              output_schema_json = excluded.output_schema_json,
              required_tools_json = excluded.required_tools_json,
              required_assets_json = excluded.required_assets_json,
              permission_json = excluded.permission_json,
              risk_policy_json = excluded.risk_policy_json,
              eval_summary_json = excluded.eval_summary_json,
              steps_json = excluded.steps_json,
              updated_at = excluded.updated_at
            """,
            (
                data["skill_id"],
                data["organization_id"],
                data["bundle_id"],
                data["name"],
                data["display_name"],
                data.get("description"),
                data["entrypoint_path"],
                data["instructions"],
                _json(data.get("trigger", {})),
                _json(data.get("input_schema", {})),
                _json(data.get("output_schema", {})),
                _json(data.get("required_tools", [])),
                _json(data.get("required_assets", [])),
                _json(data.get("permission", {})),
                _json(data.get("risk_policy", {})),
                _json(data.get("eval_summary", {})),
                _json(data.get("steps", [])),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM skills WHERE skill_id = ?", (skill_id,))
        return _skill_from_row(dict(row)) if row else None

    async def list_skills(
        self,
        *,
        status: str | None = None,
        bundle_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if bundle_id:
            where.append("bundle_id = ?")
            params.append(bundle_id)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM skills
            WHERE {' AND '.join(where)}
            ORDER BY display_name ASC
            """,
            params,
        )
        return [_skill_from_row(dict(row)) for row in rows]

    async def update_skill(self, skill_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "trigger": "trigger_json",
                "input_schema": "input_schema_json",
                "output_schema": "output_schema_json",
                "required_tools": "required_tools_json",
                "required_assets": "required_assets_json",
                "permission": "permission_json",
                "risk_policy": "risk_policy_json",
                "eval_summary": "eval_summary_json",
                "steps": "steps_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE skills SET {assignments} WHERE skill_id = ?",
            (*values.values(), skill_id),
        )

    async def insert_skill_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_runs (
              skill_run_id, organization_id, skill_id, bundle_id, task_id, step_id,
              owner_member_id, status, input_redacted_json, output_redacted_json,
              matched_reason, confidence, capability_decision_id, approval_id,
              safety_decision_id, policy_snapshot_json, resolved_asset_refs_json,
              artifact_ids_json, trace_id, error_code, error_summary, started_at,
              completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["skill_run_id"],
                data["organization_id"],
                data["skill_id"],
                data["bundle_id"],
                data.get("task_id"),
                data.get("step_id"),
                data["owner_member_id"],
                data["status"],
                _json(data.get("input_redacted", {})),
                _json(data.get("output_redacted", {})),
                data.get("matched_reason"),
                data.get("confidence"),
                data.get("capability_decision_id"),
                data.get("approval_id"),
                data.get("safety_decision_id"),
                _json(data.get("policy_snapshot", {})),
                _json(data.get("resolved_asset_refs", [])),
                _json(data.get("artifact_ids", [])),
                data.get("trace_id"),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def update_skill_run(self, skill_run_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "input_redacted": "input_redacted_json",
                "output_redacted": "output_redacted_json",
                "artifact_ids": "artifact_ids_json",
                "policy_snapshot": "policy_snapshot_json",
                "resolved_asset_refs": "resolved_asset_refs_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE skill_runs SET {assignments} WHERE skill_run_id = ?",
            (*values.values(), skill_run_id),
        )

    async def get_skill_run(self, skill_run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM skill_runs WHERE skill_run_id = ?",
            (skill_run_id,),
        )
        return _skill_run_from_row(dict(row)) if row else None

    async def get_waiting_skill_run(
        self,
        *,
        task_id: str,
        step_id: str,
        skill_id: str,
        approval_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM skill_runs
            WHERE task_id = ?
              AND step_id = ?
              AND skill_id = ?
              AND approval_id = ?
              AND status = 'waiting_approval'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (task_id, step_id, skill_id, approval_id),
        )
        return _skill_run_from_row(dict(row)) if row else None

    async def list_skill_runs(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM skill_runs WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
        return [_skill_run_from_row(dict(row)) for row in rows]

    async def insert_candidate(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_candidates (
              candidate_id, organization_id, source_type, source_id, title, description,
              draft_manifest_json, draft_skill_md, proposed_permissions_json,
              proposed_eval_cases_json, status, reviewed_by_member_id, review_reason,
              promoted_bundle_id, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
              status = excluded.status,
              reviewed_by_member_id = excluded.reviewed_by_member_id,
              review_reason = excluded.review_reason,
              promoted_bundle_id = excluded.promoted_bundle_id,
              updated_at = excluded.updated_at
            """,
            (
                data["candidate_id"],
                data["organization_id"],
                data["source_type"],
                data["source_id"],
                data["title"],
                data.get("description"),
                _json(data.get("draft_manifest", {})),
                data["draft_skill_md"],
                _json(data.get("proposed_permissions", {})),
                _json(data.get("proposed_eval_cases", [])),
                data["status"],
                data.get("reviewed_by_member_id"),
                data.get("review_reason"),
                data.get("promoted_bundle_id"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM skill_candidates WHERE candidate_id = ?",
            (candidate_id,),
        )
        return _candidate_from_row(dict(row)) if row else None

    async def list_candidates(self, status: str | None = None) -> list[dict[str, Any]]:
        where = ["organization_id = 'org_default'"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM skill_candidates
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            """,
            params,
        )
        return [_candidate_from_row(dict(row)) for row in rows]

    async def insert_eval_case(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_eval_cases (
              eval_case_id, organization_id, skill_id, bundle_id, case_key, input_json,
              expected_json, forbidden_json, risk_assertions_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["eval_case_id"],
                data["organization_id"],
                data.get("skill_id"),
                data.get("bundle_id"),
                data["case_key"],
                _json(data.get("input", {})),
                _json(data.get("expected", {})),
                _json(data.get("forbidden", {})),
                _json(data.get("risk_assertions", {})),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_eval_cases(self, skill_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM skill_eval_cases
            WHERE skill_id = ? AND status = 'active'
            ORDER BY case_key ASC
            """,
            (skill_id,),
        )
        return [_eval_case_from_row(dict(row)) for row in rows]

    async def insert_eval_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO skill_eval_runs (
              eval_run_id, organization_id, skill_id, bundle_id, status, total_cases,
              passed_cases, failed_cases, security_failures, result_json, trace_id,
              started_at, completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["eval_run_id"],
                data["organization_id"],
                data.get("skill_id"),
                data.get("bundle_id"),
                data["status"],
                data["total_cases"],
                data["passed_cases"],
                data["failed_cases"],
                data["security_failures"],
                _json(data.get("result", {})),
                data.get("trace_id"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def upsert_mcp_server(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_servers (
              server_id, organization_id, display_name, description, transport, command,
              args_json, url, env_refs_json, allowed_skills_json, permission_json,
              risk_policy_json, trust_level, status, last_connected_at, last_sync_at,
              last_error_code, last_error_summary, runtime_profile_id, lifecycle_status,
              circuit_state, last_health_check_at, consecutive_failure_count,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_id) DO UPDATE SET
              display_name = excluded.display_name,
              description = excluded.description,
              transport = excluded.transport,
              command = excluded.command,
              args_json = excluded.args_json,
              url = excluded.url,
              env_refs_json = excluded.env_refs_json,
              allowed_skills_json = excluded.allowed_skills_json,
              permission_json = excluded.permission_json,
              risk_policy_json = excluded.risk_policy_json,
              trust_level = excluded.trust_level,
              status = excluded.status,
              runtime_profile_id = excluded.runtime_profile_id,
              lifecycle_status = excluded.lifecycle_status,
              circuit_state = excluded.circuit_state,
              last_health_check_at = excluded.last_health_check_at,
              consecutive_failure_count = excluded.consecutive_failure_count,
              updated_at = excluded.updated_at
            """,
            (
                data["server_id"],
                data["organization_id"],
                data["display_name"],
                data.get("description"),
                data["transport"],
                data.get("command"),
                _json(data.get("args", [])),
                data.get("url"),
                _json(data.get("env_refs", [])),
                _json(data.get("allowed_skills", [])),
                _json(data.get("permission", {})),
                _json(data.get("risk_policy", {})),
                data["trust_level"],
                data["status"],
                data.get("last_connected_at"),
                data.get("last_sync_at"),
                data.get("last_error_code"),
                data.get("last_error_summary"),
                data.get("runtime_profile_id"),
                data.get("lifecycle_status", "created"),
                data.get("circuit_state", "closed"),
                data.get("last_health_check_at"),
                data.get("consecutive_failure_count", 0),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_mcp_server(self, server_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM mcp_servers WHERE server_id = ?",
            (server_id,),
        )
        return _mcp_server_from_row(dict(row)) if row else None

    async def list_mcp_servers(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM mcp_servers
            WHERE organization_id = 'org_default'
            ORDER BY created_at DESC
            """
        )
        return [_mcp_server_from_row(dict(row)) for row in rows]

    async def update_mcp_server(self, server_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "args": "args_json",
                "env_refs": "env_refs_json",
                "allowed_skills": "allowed_skills_json",
                "permission": "permission_json",
                "risk_policy": "risk_policy_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE mcp_servers SET {assignments} WHERE server_id = ?",
            (*values.values(), server_id),
        )

    async def upsert_mcp_tool(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_tools (
              mcp_tool_id, organization_id, server_id, tool_name, registry_tool_name,
              description, input_schema_json, output_schema_json, risk_policy_json,
              required_handle_types_json, status, synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_id, tool_name) DO UPDATE SET
              registry_tool_name = excluded.registry_tool_name,
              description = excluded.description,
              input_schema_json = excluded.input_schema_json,
              output_schema_json = excluded.output_schema_json,
              risk_policy_json = excluded.risk_policy_json,
              required_handle_types_json = excluded.required_handle_types_json,
              status = excluded.status,
              synced_at = excluded.synced_at,
              updated_at = excluded.updated_at
            """,
            (
                data["mcp_tool_id"],
                data["organization_id"],
                data["server_id"],
                data["tool_name"],
                data["registry_tool_name"],
                data.get("description"),
                _json(data.get("input_schema", {})),
                _json(data.get("output_schema", {})),
                _json(data.get("risk_policy", {})),
                _json(data.get("required_handle_types", [])),
                data["status"],
                data["synced_at"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_mcp_tool_by_registry_name(
        self,
        registry_tool_name: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM mcp_tools WHERE registry_tool_name = ?",
            (registry_tool_name,),
        )
        return _mcp_tool_from_row(dict(row)) if row else None

    async def get_mcp_tool(self, mcp_tool_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM mcp_tools WHERE mcp_tool_id = ?",
            (mcp_tool_id,),
        )
        return _mcp_tool_from_row(dict(row)) if row else None

    async def list_mcp_tools(self, server_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM mcp_tools WHERE server_id = ? ORDER BY tool_name ASC",
            (server_id,),
        )
        return [_mcp_tool_from_row(dict(row)) for row in rows]

    async def disable_mcp_tools_absent(
        self,
        server_id: str,
        current_tool_names: set[str],
        now: str,
    ) -> list[dict[str, Any]]:
        where, params = _absent_where("tool_name", current_tool_names)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM mcp_tools
            WHERE server_id = ? AND status = 'active'{where}
            ORDER BY tool_name ASC
            """,
            (server_id, *params),
        )
        stale = [_mcp_tool_from_row(dict(row)) for row in rows]
        if stale:
            await self._db.execute(
                f"""
                UPDATE mcp_tools
                SET status = 'disabled', updated_at = ?
                WHERE server_id = ? AND status = 'active'{where}
                """,
                (now, server_id, *params),
            )
        return stale

    async def upsert_mcp_resource(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_resources (
              resource_id, organization_id, server_id, uri, name, description, mime_type,
              trust_level, sensitivity, metadata_json, status, synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_id, uri) DO UPDATE SET
              name = excluded.name,
              description = excluded.description,
              mime_type = excluded.mime_type,
              trust_level = excluded.trust_level,
              sensitivity = excluded.sensitivity,
              metadata_json = excluded.metadata_json,
              status = excluded.status,
              synced_at = excluded.synced_at,
              updated_at = excluded.updated_at
            """,
            (
                data["resource_id"],
                data["organization_id"],
                data["server_id"],
                data["uri"],
                data.get("name"),
                data.get("description"),
                data.get("mime_type"),
                data["trust_level"],
                data["sensitivity"],
                _json(data.get("metadata", {})),
                data["status"],
                data["synced_at"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_mcp_resources(self, server_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM mcp_resources WHERE server_id = ? ORDER BY uri ASC",
            (server_id,),
        )
        return [_mcp_resource_from_row(dict(row)) for row in rows]

    async def disable_mcp_resources_absent(
        self,
        server_id: str,
        current_uris: set[str],
        now: str,
    ) -> int:
        where, params = _absent_where("uri", current_uris)
        rows = await self._db.fetch_all(
            f"""
            SELECT resource_id
            FROM mcp_resources
            WHERE server_id = ? AND status = 'active'{where}
            """,
            (server_id, *params),
        )
        count = len(rows)
        if count:
            await self._db.execute(
                f"""
                UPDATE mcp_resources
                SET status = 'disabled', updated_at = ?
                WHERE server_id = ? AND status = 'active'{where}
                """,
                (now, server_id, *params),
            )
        return count

    async def upsert_mcp_prompt(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_prompts (
              prompt_id, organization_id, server_id, name, description, arguments_schema_json,
              prompt_template_redacted, trust_level, status, synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_id, name) DO UPDATE SET
              description = excluded.description,
              arguments_schema_json = excluded.arguments_schema_json,
              prompt_template_redacted = excluded.prompt_template_redacted,
              trust_level = excluded.trust_level,
              status = excluded.status,
              synced_at = excluded.synced_at,
              updated_at = excluded.updated_at
            """,
            (
                data["prompt_id"],
                data["organization_id"],
                data["server_id"],
                data["name"],
                data.get("description"),
                _json(data.get("arguments_schema", {})),
                data.get("prompt_template_redacted"),
                data["trust_level"],
                data["status"],
                data["synced_at"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_mcp_prompts(self, server_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM mcp_prompts WHERE server_id = ? ORDER BY name ASC",
            (server_id,),
        )
        return [_mcp_prompt_from_row(dict(row)) for row in rows]

    async def disable_mcp_prompts_absent(
        self,
        server_id: str,
        current_names: set[str],
        now: str,
    ) -> int:
        where, params = _absent_where("name", current_names)
        rows = await self._db.fetch_all(
            f"""
            SELECT prompt_id
            FROM mcp_prompts
            WHERE server_id = ? AND status = 'active'{where}
            """,
            (server_id, *params),
        )
        count = len(rows)
        if count:
            await self._db.execute(
                f"""
                UPDATE mcp_prompts
                SET status = 'disabled', updated_at = ?
                WHERE server_id = ? AND status = 'active'{where}
                """,
                (now, server_id, *params),
            )
        return count

    async def insert_mcp_call(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_calls (
              mcp_call_id, organization_id, server_id, mcp_tool_id, task_id, step_id,
              tool_call_id, status, request_redacted_json, response_redacted_json,
              capability_decision_id, approval_id, safety_decision_id,
              policy_snapshot_json, resolved_asset_refs_json, trace_id, error_code,
              error_summary, started_at, completed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["mcp_call_id"],
                data["organization_id"],
                data["server_id"],
                data.get("mcp_tool_id"),
                data.get("task_id"),
                data.get("step_id"),
                data.get("tool_call_id"),
                data["status"],
                _json(data.get("request_redacted", {})),
                _json(data.get("response_redacted", {})),
                data.get("capability_decision_id"),
                data.get("approval_id"),
                data.get("safety_decision_id"),
                _json(data.get("policy_snapshot", {})),
                _json(data.get("resolved_asset_refs", [])),
                data.get("trace_id"),
                data.get("error_code"),
                data.get("error_summary"),
                data.get("started_at"),
                data.get("completed_at"),
                data["created_at"],
            ),
        )

    async def update_mcp_call(self, mcp_call_id: str, fields: dict[str, Any]) -> None:
        values = _json_update_fields(
            fields,
            {
                "request_redacted": "request_redacted_json",
                "response_redacted": "response_redacted_json",
                "policy_snapshot": "policy_snapshot_json",
                "resolved_asset_refs": "resolved_asset_refs_json",
            },
        )
        if not values:
            return
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE mcp_calls SET {assignments} WHERE mcp_call_id = ?",
            (*values.values(), mcp_call_id),
        )

    async def list_mcp_calls(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM mcp_calls WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
        return [_mcp_call_from_row(dict(row)) for row in rows]

    async def upsert_mcp_runtime_profile(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_runtime_profiles (
              profile_id, organization_id, server_id, transport, command_policy_json,
              args_policy_json, env_policy_json, member_scope_policy_json,
              network_policy, filesystem_policy_json, sandbox_backend,
              timeout_policy_json, resource_trust_policy, prompt_trust_policy,
              status, reason_codes_json, trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
              command_policy_json = excluded.command_policy_json,
              args_policy_json = excluded.args_policy_json,
              env_policy_json = excluded.env_policy_json,
              member_scope_policy_json = excluded.member_scope_policy_json,
              network_policy = excluded.network_policy,
              filesystem_policy_json = excluded.filesystem_policy_json,
              sandbox_backend = excluded.sandbox_backend,
              timeout_policy_json = excluded.timeout_policy_json,
              resource_trust_policy = excluded.resource_trust_policy,
              prompt_trust_policy = excluded.prompt_trust_policy,
              status = excluded.status,
              reason_codes_json = excluded.reason_codes_json,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["profile_id"],
                data["organization_id"],
                data["server_id"],
                data["transport"],
                _json(data.get("command_policy", {})),
                _json(data.get("args_policy", {})),
                _json(data.get("env_policy", {})),
                _json(data.get("member_scope_policy", {})),
                data["network_policy"],
                _json(data.get("filesystem_policy", {})),
                data["sandbox_backend"],
                _json(data.get("timeout_policy", {})),
                data["resource_trust_policy"],
                data["prompt_trust_policy"],
                data["status"],
                _json(data.get("reason_codes", [])),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def latest_mcp_runtime_profile(self, server_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM mcp_runtime_profiles
            WHERE server_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (server_id,),
        )
        return _mcp_runtime_profile_from_row(dict(row)) if row else None

    async def insert_mcp_lifecycle_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_lifecycle_events (
              lifecycle_event_id, organization_id, server_id, profile_id, event_type,
              previous_status, current_status, circuit_state, payload_redacted_json,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["lifecycle_event_id"],
                data["organization_id"],
                data["server_id"],
                data.get("profile_id"),
                data["event_type"],
                data.get("previous_status"),
                data["current_status"],
                data.get("circuit_state", "closed"),
                _json(data.get("payload_redacted", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_mcp_lifecycle_events(self, server_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM mcp_lifecycle_events
            WHERE server_id = ?
            ORDER BY created_at ASC
            """,
            (server_id,),
        )
        return [_mcp_lifecycle_event_from_row(dict(row)) for row in rows]

    async def insert_mcp_protocol_validation_report(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_protocol_validation_reports (
              validation_report_id, organization_id, server_id, mcp_call_id,
              operation, protocol_version, schema_valid, capability_valid,
              validation_status, issue_codes_json, sanitized_payload_json,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["validation_report_id"],
                data["organization_id"],
                data["server_id"],
                data.get("mcp_call_id"),
                data["operation"],
                data.get("protocol_version"),
                1 if data.get("schema_valid") else 0,
                1 if data.get("capability_valid") else 0,
                data["validation_status"],
                _json(data.get("issue_codes", [])),
                _json(data.get("sanitized_payload", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_mcp_protocol_validation_reports(
        self,
        server_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM mcp_protocol_validation_reports
            WHERE server_id = ?
            ORDER BY created_at ASC
            """,
            (server_id,),
        )
        return [_mcp_protocol_report_from_row(dict(row)) for row in rows]

    async def insert_mcp_content_sanitization_report(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_content_sanitization_reports (
              sanitization_report_id, organization_id, server_id, source_type,
              source_id, trust_level, content_hash, size_bytes, mime_type,
              injection_detected, dlp_report_id, sanitized_preview,
              metadata_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["sanitization_report_id"],
                data["organization_id"],
                data["server_id"],
                data["source_type"],
                data.get("source_id"),
                data["trust_level"],
                data.get("content_hash"),
                data.get("size_bytes", 0),
                data.get("mime_type"),
                1 if data.get("injection_detected") else 0,
                data.get("dlp_report_id"),
                data.get("sanitized_preview"),
                _json(data.get("metadata", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_mcp_content_sanitization_reports(
        self,
        server_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM mcp_content_sanitization_reports
            WHERE server_id = ?
            ORDER BY created_at ASC
            """,
            (server_id,),
        )
        return [_mcp_sanitization_report_from_row(dict(row)) for row in rows]

    async def insert_mcp_output_taint_record(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO mcp_output_taint_records (
              taint_record_id, organization_id, server_id, mcp_call_id,
              tool_call_id, taint_source, target_action, target_risk_level,
              guard_decision, reason_codes_json, source_refs_json,
              policy_snapshot_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["taint_record_id"],
                data["organization_id"],
                data["server_id"],
                data.get("mcp_call_id"),
                data.get("tool_call_id"),
                data["taint_source"],
                data.get("target_action"),
                data.get("target_risk_level", "R1"),
                data["guard_decision"],
                _json(data.get("reason_codes", [])),
                _json(data.get("source_refs", [])),
                _json(data.get("policy_snapshot", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_mcp_output_taint_records(self, server_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM mcp_output_taint_records
            WHERE server_id = ?
            ORDER BY created_at ASC
            """,
            (server_id,),
        )
        return [_mcp_taint_record_from_row(dict(row)) for row in rows]

    async def insert_event(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO plugin_events (
              event_id, organization_id, bundle_id, skill_id, server_id, event_type,
              payload_json, payload_redacted_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["event_id"],
                data["organization_id"],
                data.get("bundle_id"),
                data.get("skill_id"),
                data.get("server_id"),
                data["event_type"],
                _json(data.get("payload", {})),
                _json(data.get("payload_redacted", data.get("payload", {}))),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_events(self, bundle_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM plugin_events WHERE bundle_id = ? ORDER BY created_at ASC",
            (bundle_id,),
        )
        return [_plugin_event_from_row(dict(row)) for row in rows]

    async def list_events_for_task_replay(self, task_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT pe.*
            FROM plugin_events pe
            LEFT JOIN skill_runs sr ON sr.skill_id = pe.skill_id
            LEFT JOIN mcp_calls mc ON mc.server_id = pe.server_id
            WHERE sr.task_id = ? OR mc.task_id = ?
            ORDER BY pe.created_at ASC
            """,
            (task_id, task_id),
        )
        return [_plugin_event_from_row(dict(row)) for row in rows]


def _bundle_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["permission_summary"] = json.loads(row.pop("permission_summary_json") or "{}")
    row["risk_summary"] = json.loads(row.pop("risk_summary_json") or "{}")
    row["manifest"] = json.loads(row.pop("manifest_json") or "{}")
    return row


def _skill_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["trigger"] = json.loads(row.pop("trigger_json") or "{}")
    row["input_schema"] = json.loads(row.pop("input_schema_json") or "{}")
    row["output_schema"] = json.loads(row.pop("output_schema_json") or "{}")
    row["required_tools"] = json.loads(row.pop("required_tools_json") or "[]")
    row["required_assets"] = json.loads(row.pop("required_assets_json") or "[]")
    row["permission"] = json.loads(row.pop("permission_json") or "{}")
    row["risk_policy"] = json.loads(row.pop("risk_policy_json") or "{}")
    row["eval_summary"] = json.loads(row.pop("eval_summary_json") or "{}")
    row["steps"] = json.loads(row.pop("steps_json") or "[]")
    return row


def _skill_run_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["input_redacted"] = json.loads(row.pop("input_redacted_json") or "{}")
    row["output_redacted"] = json.loads(row.pop("output_redacted_json") or "{}")
    row["artifact_ids"] = json.loads(row.pop("artifact_ids_json") or "[]")
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json", None) or "{}")
    row["resolved_asset_refs"] = json.loads(row.pop("resolved_asset_refs_json", None) or "[]")
    return row


def _candidate_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["draft_manifest"] = json.loads(row.pop("draft_manifest_json") or "{}")
    row["proposed_permissions"] = json.loads(row.pop("proposed_permissions_json") or "{}")
    row["proposed_eval_cases"] = json.loads(row.pop("proposed_eval_cases_json") or "[]")
    return row


def _eval_case_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["input"] = json.loads(row.pop("input_json") or "{}")
    row["expected"] = json.loads(row.pop("expected_json") or "{}")
    row["forbidden"] = json.loads(row.pop("forbidden_json") or "{}")
    row["risk_assertions"] = json.loads(row.pop("risk_assertions_json") or "{}")
    return row


def _mcp_server_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["args"] = json.loads(row.pop("args_json") or "[]")
    row["env_refs"] = json.loads(row.pop("env_refs_json") or "[]")
    row["allowed_skills"] = json.loads(row.pop("allowed_skills_json") or "[]")
    row["permission"] = json.loads(row.pop("permission_json") or "{}")
    row["risk_policy"] = json.loads(row.pop("risk_policy_json") or "{}")
    row["consecutive_failure_count"] = int(row.get("consecutive_failure_count") or 0)
    return row


def _mcp_tool_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["input_schema"] = json.loads(row.pop("input_schema_json") or "{}")
    row["output_schema"] = json.loads(row.pop("output_schema_json") or "{}")
    row["risk_policy"] = json.loads(row.pop("risk_policy_json") or "{}")
    row["required_handle_types"] = json.loads(row.pop("required_handle_types_json") or "[]")
    return row


def _mcp_resource_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _mcp_prompt_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["arguments_schema"] = json.loads(row.pop("arguments_schema_json") or "{}")
    return row


def _mcp_call_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["request_redacted"] = json.loads(row.pop("request_redacted_json") or "{}")
    row["response_redacted"] = json.loads(row.pop("response_redacted_json") or "{}")
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json", None) or "{}")
    row["resolved_asset_refs"] = json.loads(row.pop("resolved_asset_refs_json", None) or "[]")
    return row


def _plugin_event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = json.loads(row.pop("payload_redacted_json") or "{}")
    row.pop("payload_json", None)
    return row


def _mcp_runtime_profile_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["command_policy"] = json.loads(row.pop("command_policy_json") or "{}")
    row["args_policy"] = json.loads(row.pop("args_policy_json") or "{}")
    row["env_policy"] = json.loads(row.pop("env_policy_json") or "{}")
    row["member_scope_policy"] = json.loads(row.pop("member_scope_policy_json") or "{}")
    row["filesystem_policy"] = json.loads(row.pop("filesystem_policy_json") or "{}")
    row["timeout_policy"] = json.loads(row.pop("timeout_policy_json") or "{}")
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    return row


def _mcp_lifecycle_event_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload_redacted"] = json.loads(row.pop("payload_redacted_json") or "{}")
    return row


def _mcp_protocol_report_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["schema_valid"] = bool(row.pop("schema_valid"))
    row["capability_valid"] = bool(row.pop("capability_valid"))
    row["issue_codes"] = json.loads(row.pop("issue_codes_json") or "[]")
    row["sanitized_payload"] = json.loads(row.pop("sanitized_payload_json") or "{}")
    return row


def _mcp_sanitization_report_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["injection_detected"] = bool(row.pop("injection_detected"))
    row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
    return row


def _mcp_taint_record_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
    row["source_refs"] = json.loads(row.pop("source_refs_json") or "[]")
    row["policy_snapshot"] = json.loads(row.pop("policy_snapshot_json") or "{}")
    return row


def _json_update_fields(fields: dict[str, Any], aliases: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in fields.items():
        column = aliases.get(key, key)
        values[column] = _json(value) if key in aliases else value
    return values


def _absent_where(column: str, current_values: set[str]) -> tuple[str, tuple[str, ...]]:
    values = tuple(sorted(value for value in current_values if value))
    if not values:
        return "", ()
    placeholders = ", ".join("?" for _ in values)
    return f" AND {column} NOT IN ({placeholders})", values


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
