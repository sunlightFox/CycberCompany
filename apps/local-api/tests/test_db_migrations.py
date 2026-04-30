from __future__ import annotations

from pathlib import Path

import pytest
from app.db.migrator import MigrationError, run_migrations
from app.db.session import Database

ROOT_DIR = Path(__file__).resolve().parents[3]
MIGRATIONS_DIR = ROOT_DIR / "apps" / "local-api" / "app" / "db" / "migrations"


@pytest.mark.asyncio
async def test_db_001_empty_database_migrates_and_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    await db.connect()
    try:
        first = await run_migrations(db, MIGRATIONS_DIR)
        second = await run_migrations(db, MIGRATIONS_DIR)
        tables = await db.fetch_all(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
        )
    finally:
        await db.close()

    table_names = {row["name"] for row in tables}
    assert "001_initial.sql" in first
    assert "002_chat_model_routing.sql" in first
    assert "003_chat_runtime_hardening.sql" in first
    assert "004_memory_system.sql" in first
    assert "005_memory_runtime_hardening.sql" in first
    assert "006_asset_capability_knowledge.sql" in first
    assert "007_task_engine_tool_runtime.sql" in first
    assert "008_skill_mcp_plugin_system.sql" in first
    assert "009_multimember_collaboration_shell_contract.sql" in first
    assert "010_release_gate_eval_security_backup.sql" in first
    assert "011_design_alignment_runtime_contracts.sql" in first
    assert "012_settings_contract.sql" in first
    assert "013_chat_experience_state.sql" in first
    assert "014_brain_decision_chain.sql" in first
    assert "015_memory_knowledge_semantic_retrieval.sql" in first
    assert "016_agent_task_planning_skill_mcp_coordination.sql" in first
    assert "017_dialogue_semantics_low_confidence.sql" in first
    assert "018_model_planner_agent_execution.sql" in first
    assert "019_memory_knowledge_retrieval_quality.sql" in first
    assert "020_execution_boundary_hardening.sql" in first
    assert "021_persona_heart_experience_quality.sql" in first
    assert "022_model_semantic_verifier.sql" in first
    assert "023_mcp_runtime_isolation_protocol_hardening.sql" in first
    assert "024_scheduled_tasks.sql" in first
    assert "025_browser_sessions.sql" in first
    assert second == []
    assert {
        "shells",
        "organizations",
        "departments",
        "roles",
        "brains",
        "members",
        "conversations",
        "messages",
        "app_settings",
        "traces",
        "trace_spans",
        "audit_events",
        "chat_turns",
        "conversation_summaries",
        "secret_refs",
        "chat_events",
        "memory_items",
        "memory_candidates",
        "memory_relations",
        "memory_vector_refs",
        "memory_retrieval_logs",
        "memory_jobs",
        "memory_items_fts",
        "assets",
        "asset_policies",
        "asset_handles",
        "asset_handle_events",
        "capability_edges",
        "capability_decision_logs",
        "knowledge_sources",
        "knowledge_chunks",
        "knowledge_index_jobs",
        "knowledge_vector_refs",
        "knowledge_access_logs",
        "knowledge_chunks_fts",
        "tasks",
        "task_steps",
        "task_events",
        "task_jobs",
        "task_artifacts",
        "tool_registry",
        "tool_calls",
        "approvals",
        "approval_events",
        "plugin_bundles",
        "plugin_files",
        "skills",
        "skill_runs",
        "skill_candidates",
        "skill_eval_cases",
        "skill_eval_runs",
        "mcp_servers",
        "mcp_tools",
        "mcp_resources",
        "mcp_prompts",
        "mcp_calls",
        "plugin_install_jobs",
        "plugin_events",
        "task_participants",
        "task_subtasks",
        "collaboration_plans",
        "collaboration_rounds",
        "collaboration_outputs",
        "host_decisions",
        "member_availability",
        "member_skill_policies",
        "shell_switch_events",
        "shell_template_applications",
        "release_gates",
        "release_evidence",
        "release_findings",
        "eval_suites",
        "eval_cases",
        "eval_runs",
        "eval_results",
        "red_team_scenarios",
        "security_audit_runs",
        "integrity_check_runs",
        "backup_jobs",
        "restore_jobs",
        "benchmark_runs",
        "diagnostic_bundles",
        "release_reports",
        "runtime_contracts",
        "design_gaps",
        "safety_decisions",
        "persona_profiles",
        "persona_modes",
        "heart_state_snapshots",
        "member_interaction_preferences",
        "vector_store_collections",
        "vector_sync_jobs",
        "runtime_settings",
        "conversation_working_states",
        "chat_clarification_decisions",
        "brain_decision_logs",
        "local_vector_embeddings",
        "task_planner_decisions",
        "agent_loop_iterations",
        "task_observations",
        "task_retry_plans",
        "task_reflection_candidates",
        "dialogue_states",
        "semantic_intent_candidates",
        "low_confidence_decision_reviews",
        "model_plan_candidates",
        "plan_verification_results",
        "plan_policy_prunes",
        "planner_capability_candidates",
        "agent_next_action_decisions",
        "tool_failure_recovery_plans",
        "embedding_provider_configs",
        "retrieval_rerank_runs",
        "retrieval_suppressed_items",
        "knowledge_retrieval_logs",
        "retrieval_quality_reports",
        "tool_action_policies",
        "tool_policy_decisions",
        "terminal_sandbox_profiles",
        "tool_output_dlp_reports",
        "mcp_process_policy_checks",
        "execution_boundary_diagnostics",
        "persona_consistency_profiles",
        "heart_state_transitions",
        "tone_policy_resolutions",
        "response_quality_evaluations",
        "persona_heart_replay_runs",
        "semantic_review_requests",
        "semantic_review_suggestions",
        "semantic_review_model_calls",
        "semantic_review_merge_results",
        "mcp_runtime_profiles",
        "mcp_lifecycle_events",
        "mcp_protocol_validation_reports",
        "mcp_content_sanitization_reports",
        "mcp_output_taint_records",
        "scheduled_tasks",
        "scheduled_task_runs",
        "scheduled_task_events",
        "browser_profiles",
        "browser_sessions",
        "browser_profile_events",
        "browser_evidence",
        "browser_network_events",
        "browser_console_events",
    }.issubset(table_names)

    db = Database(tmp_path / "app.db")
    await db.connect()
    try:
        memory_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(memory_items)")
        }
        job_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(memory_jobs)")
        }
        secret_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(secret_refs)")
        }
        tool_registry_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(tool_registry)")
        }
        task_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(tasks)")
        }
        step_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(task_steps)")
        }
        release_gate_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(release_gates)")
        }
        tool_call_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(tool_calls)")
        }
        mcp_call_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(mcp_calls)")
        }
        skill_run_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(skill_runs)")
        }
        chat_turn_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(chat_turns)")
        }
        local_vector_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(local_vector_embeddings)")
        }
        planner_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(task_planner_decisions)")
        }
        agent_iteration_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(agent_loop_iterations)")
        }
        observation_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(task_observations)")
        }
        retry_plan_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(task_retry_plans)")
        }
        reflection_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(task_reflection_candidates)")
        }
        dialogue_state_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(dialogue_states)")
        }
        semantic_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(semantic_intent_candidates)")
        }
        low_confidence_columns = {
            row["name"]
            for row in await db.fetch_all(
                "PRAGMA table_info(low_confidence_decision_reviews)"
            )
        }
        model_candidate_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(model_plan_candidates)")
        }
        plan_verification_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(plan_verification_results)")
        }
        plan_prune_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(plan_policy_prunes)")
        }
        capability_candidate_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(planner_capability_candidates)")
        }
        next_action_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(agent_next_action_decisions)")
        }
        recovery_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(tool_failure_recovery_plans)")
        }
        provider_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(embedding_provider_configs)")
        }
        rerank_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(retrieval_rerank_runs)")
        }
        suppressed_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(retrieval_suppressed_items)")
        }
        knowledge_retrieval_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(knowledge_retrieval_logs)")
        }
        quality_report_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(retrieval_quality_reports)")
        }
        tool_policy_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(tool_action_policies)")
        }
        tool_decision_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(tool_policy_decisions)")
        }
        sandbox_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(terminal_sandbox_profiles)")
        }
        dlp_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(tool_output_dlp_reports)")
        }
        mcp_policy_check_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(mcp_process_policy_checks)")
        }
        execution_diagnostic_columns = {
            row["name"]
            for row in await db.fetch_all(
                "PRAGMA table_info(execution_boundary_diagnostics)"
            )
        }
        persona_consistency_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(persona_consistency_profiles)")
        }
        heart_transition_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(heart_state_transitions)")
        }
        tone_resolution_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(tone_policy_resolutions)")
        }
        response_quality_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(response_quality_evaluations)")
        }
        persona_heart_replay_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(persona_heart_replay_runs)")
        }
        semantic_review_request_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(semantic_review_requests)")
        }
        semantic_review_suggestion_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(semantic_review_suggestions)")
        }
        semantic_review_model_call_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(semantic_review_model_calls)")
        }
        semantic_review_merge_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(semantic_review_merge_results)")
        }
        mcp_server_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(mcp_servers)")
        }
        mcp_runtime_profile_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(mcp_runtime_profiles)")
        }
        mcp_lifecycle_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(mcp_lifecycle_events)")
        }
        mcp_protocol_columns = {
            row["name"]
            for row in await db.fetch_all(
                "PRAGMA table_info(mcp_protocol_validation_reports)"
            )
        }
        mcp_sanitization_columns = {
            row["name"]
            for row in await db.fetch_all(
                "PRAGMA table_info(mcp_content_sanitization_reports)"
            )
        }
        mcp_taint_columns = {
            row["name"]
            for row in await db.fetch_all("PRAGMA table_info(mcp_output_taint_records)")
        }
    finally:
        await db.close()

    assert {"normalized_summary", "content_hash"}.issubset(memory_columns)
    assert {"max_attempts", "next_run_at", "locked_by", "locked_at"}.issubset(job_columns)
    assert {
        "secret_ref",
        "kind",
        "label",
        "storage_uri",
        "organization_id",
        "ref_uri",
        "secret_type",
        "provider",
        "status",
        "metadata_json",
        "expires_at",
    }.issubset(secret_columns)
    assert {
        "bundle_id",
        "skill_id",
        "mcp_server_id",
        "mcp_tool_id",
        "adapter_config_json",
        "trust_level",
    }.issubset(tool_registry_columns)
    assert {
        "parent_task_id",
        "host_member_id",
        "collaboration_plan_id",
        "supervisor_mode",
    }.issubset(task_columns)
    assert {"subtask_id", "participant_id", "assigned_member_id"}.issubset(step_columns)
    assert {
        "release_gate_id",
        "status",
        "required_checks_json",
        "blocker_count",
    }.issubset(release_gate_columns)
    assert {
        "safety_decision_id",
        "policy_snapshot_json",
        "resolved_asset_refs_json",
    }.issubset(tool_call_columns)
    assert {
        "safety_decision_id",
        "policy_snapshot_json",
        "resolved_asset_refs_json",
    }.issubset(mcp_call_columns)
    assert {
        "safety_decision_id",
        "policy_snapshot_json",
        "resolved_asset_refs_json",
    }.issubset(skill_run_columns)
    assert {"experience_json", "brain_decision_id"}.issubset(chat_turn_columns)
    assert {
        "embedding_id",
        "collection_name",
        "target_type",
        "target_id",
        "embedding_json",
        "embedding_dim",
        "provider",
        "embedding_model",
        "metadata_json",
        "status",
    }.issubset(local_vector_columns)
    assert {
        "planner_decision_id",
        "planner_type",
        "selected_mode",
        "reason_codes_json",
        "capability_snapshot_json",
    }.issubset(planner_columns)
    assert {
        "iteration_id",
        "loop_index",
        "observation_id",
        "selected_action_json",
        "budget_snapshot_json",
    }.issubset(agent_iteration_columns)
    assert {
        "observation_id",
        "source_type",
        "trusted_level",
        "payload_redacted_json",
    }.issubset(observation_columns)
    assert {
        "retry_plan_id",
        "reason",
        "suggested_actions_json",
        "budget_delta_json",
    }.issubset(retry_plan_columns)
    assert {
        "candidate_id",
        "candidate_type",
        "status",
        "source_refs_json",
    }.issubset(reflection_columns)
    assert {
        "dialogue_state_id",
        "conversation_id",
        "goal_history_json",
        "hard_constraints_json",
        "topic_shift",
        "source_turn_id",
    }.issubset(dialogue_state_columns)
    assert {
        "semantic_candidate_id",
        "brain_decision_id",
        "actionable_intents_json",
        "risk_intents_json",
        "conflicts_json",
    }.issubset(semantic_columns)
    assert {
        "review_id",
        "brain_decision_id",
        "trigger_reasons_json",
        "verifier_suggestion_json",
        "fallback_used",
        "semantic_review_id",
        "model_assist_attempted",
        "schema_valid",
        "fallback_reason",
        "risk_guard_applied",
    }.issubset(low_confidence_columns)
    assert {
        "candidate_id",
        "planner_type",
        "recommended_mode",
        "steps_json",
        "model_assist_json",
    }.issubset(model_candidate_columns)
    assert {
        "verification_id",
        "candidate_id",
        "schema_valid",
        "no_direct_shell_command_from_model",
        "issues_json",
    }.issubset(plan_verification_columns)
    assert {
        "prune_id",
        "candidate_id",
        "prune_type",
        "original_step_json",
        "reason_codes_json",
    }.issubset(plan_prune_columns)
    assert {
        "capability_candidate_id",
        "capability_type",
        "policy_status",
        "metadata_json",
    }.issubset(capability_candidate_columns)
    assert {
        "decision_id",
        "next_action_type",
        "selected_step_key",
        "budget_snapshot_json",
    }.issubset(next_action_columns)
    assert {
        "recovery_plan_id",
        "failure_type",
        "recovery_action",
        "bypass_controls",
    }.issubset(recovery_columns)
    assert {
        "provider_id",
        "provider_type",
        "embedding_model",
        "privacy_policy",
        "allow_cloud",
        "fallback_policy",
    }.issubset(provider_columns)
    assert {
        "rerank_run_id",
        "retrieval_id",
        "target_type",
        "scoring_policy_json",
        "fallback_used",
        "latency_ms",
    }.issubset(rerank_columns)
    assert {
        "suppressed_id",
        "retrieval_id",
        "target_type",
        "target_id",
        "reason",
        "metadata_json",
    }.issubset(suppressed_columns)
    assert {
        "retrieval_id",
        "subject_type",
        "subject_id",
        "selected_chunk_ids_json",
        "retrieval_sources_json",
    }.issubset(knowledge_retrieval_columns)
    assert {
        "report_id",
        "target_type",
        "retrieval_id",
        "summary_json",
        "metrics_json",
    }.issubset(quality_report_columns)
    assert {
        "policy_id",
        "tool_name",
        "action_category",
        "risk_level",
        "output_dlp_policy_json",
    }.issubset(tool_policy_columns)
    assert {
        "decision_id",
        "tool_call_id",
        "action_category",
        "effective_risk_level",
        "policy_snapshot_json",
        "sandbox_profile_id",
    }.issubset(tool_decision_columns)
    assert {
        "profile_id",
        "working_dir_policy",
        "os_sandbox_backend",
        "degraded_reason",
    }.issubset(sandbox_columns)
    assert {
        "dlp_report_id",
        "tool_call_id",
        "mcp_call_id",
        "findings_json",
        "redaction_count",
        "redacted_preview",
    }.issubset(dlp_columns)
    assert {
        "check_id",
        "server_id",
        "command_allowed",
        "env_refs_only",
        "no_inline_secret",
        "policy_snapshot_json",
    }.issubset(mcp_policy_check_columns)
    assert {"diagnostic_id", "subject_type", "summary_json", "status"}.issubset(
        execution_diagnostic_columns
    )
    assert {
        "consistency_profile_id",
        "persona_profile_id",
        "style_principles_json",
        "forbidden_claims_json",
        "mode_switch_rules_json",
    }.issubset(persona_consistency_columns)
    assert {
        "transition_id",
        "previous_snapshot_id",
        "current_snapshot_id",
        "source_turn_id",
        "transition_factors_json",
        "state_delta_json",
    }.issubset(heart_transition_columns)
    assert {
        "resolution_id",
        "turn_id",
        "tone_mode",
        "anthropomorphic_level",
        "reason_codes_json",
        "policy_snapshot_json",
    }.issubset(tone_resolution_columns)
    assert {
        "evaluation_id",
        "turn_id",
        "response_plan_json",
        "quality_markers_json",
        "internal_leakage_count",
    }.issubset(response_quality_columns)
    assert {
        "run_id",
        "case_key",
        "metrics_json",
        "violation_counts_json",
        "evidence_json",
    }.issubset(persona_heart_replay_columns)
    assert {
        "semantic_review_id",
        "brain_decision_id",
        "turn_id",
        "trigger_reasons_json",
        "redacted_request_json",
        "privacy_policy",
        "status",
    }.issubset(semantic_review_request_columns)
    assert {
        "suggestion_id",
        "semantic_review_id",
        "source",
        "suggestion_json",
        "schema_valid",
        "rejected_reasons_json",
    }.issubset(semantic_review_suggestion_columns)
    assert {
        "model_call_id",
        "semantic_review_id",
        "adapter_name",
        "fallback_used",
        "fallback_reason",
        "latency_ms",
        "usage_json",
        "schema_valid",
    }.issubset(semantic_review_model_call_columns)
    assert {
        "merge_id",
        "semantic_review_id",
        "brain_decision_id",
        "merged_intent_json",
        "risk_monotonic_guard_applied",
        "unsafe_downgrade_count",
    }.issubset(semantic_review_merge_columns)
    assert {
        "runtime_profile_id",
        "lifecycle_status",
        "circuit_state",
        "last_health_check_at",
        "consecutive_failure_count",
    }.issubset(mcp_server_columns)
    assert {
        "profile_id",
        "server_id",
        "command_policy_json",
        "env_policy_json",
        "sandbox_backend",
        "resource_trust_policy",
    }.issubset(mcp_runtime_profile_columns)
    assert {
        "lifecycle_event_id",
        "server_id",
        "event_type",
        "current_status",
        "circuit_state",
    }.issubset(mcp_lifecycle_columns)
    assert {
        "validation_report_id",
        "server_id",
        "operation",
        "schema_valid",
        "validation_status",
        "issue_codes_json",
    }.issubset(mcp_protocol_columns)
    assert {
        "sanitization_report_id",
        "server_id",
        "source_type",
        "trust_level",
        "injection_detected",
        "sanitized_preview",
    }.issubset(mcp_sanitization_columns)
    assert {
        "taint_record_id",
        "server_id",
        "mcp_call_id",
        "guard_decision",
        "reason_codes_json",
    }.issubset(mcp_taint_columns)


@pytest.mark.asyncio
async def test_db_002_migration_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration = migrations_dir / "001_initial.sql"
    migration.write_text("CREATE TABLE sample (id TEXT PRIMARY KEY);", encoding="utf-8")

    db = Database(tmp_path / "app.db")
    await db.connect()
    try:
        await run_migrations(db, migrations_dir)
        migration.write_text(
            "CREATE TABLE sample (id TEXT PRIMARY KEY, name TEXT);",
            encoding="utf-8",
        )
        with pytest.raises(MigrationError):
            await run_migrations(db, migrations_dir)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_db_003_core_table_names_do_not_use_shell_terms(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    await db.connect()
    try:
        await run_migrations(db, MIGRATIONS_DIR)
        rows = await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    finally:
        await db.close()

    forbidden = {"company", "companies", "employee", "employees", "boss"}
    assert forbidden.isdisjoint({row["name"].lower() for row in rows})


@pytest.mark.asyncio
async def test_db_004_phase_six_skill_mcp_tables_are_created(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    await db.connect()
    try:
        await run_migrations(db, MIGRATIONS_DIR)
        rows = await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    finally:
        await db.close()

    phase_six_tables = {
        "plugin_bundles",
        "plugin_files",
        "skills",
        "skill_runs",
        "skill_candidates",
        "skill_eval_cases",
        "skill_eval_runs",
        "mcp_servers",
        "mcp_tools",
        "mcp_resources",
        "mcp_prompts",
        "mcp_calls",
        "plugin_install_jobs",
        "plugin_events",
    }
    assert phase_six_tables.issubset({row["name"] for row in rows})
