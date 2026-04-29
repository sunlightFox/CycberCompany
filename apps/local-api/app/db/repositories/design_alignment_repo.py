from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

_PERSONA_ENGINE_STORAGE_KEY = "_persona_engine"
_DEFAULT_RISK_TONE_POLICY = {
    "approval_scene_tone": "clear_and_calm",
    "security_block_scene_tone": "firm_and_explanatory",
    "failure_scene_tone": "accountable_and_actionable",
    "high_impact_scene_tone": "low_anthropomorphic",
}
_DEFAULT_ALLOWED_MODES = [
    "default",
    "concise",
    "deep_dialogue",
    "task_status",
    "safety_boundary",
]


class DesignAlignmentRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_runtime_contract(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO runtime_contracts (
              contract_key, module_name, status, implemented, description, details_json,
              evidence_json, blocker_level, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contract_key) DO UPDATE SET
              module_name = excluded.module_name,
              status = excluded.status,
              implemented = excluded.implemented,
              description = excluded.description,
              details_json = excluded.details_json,
              evidence_json = excluded.evidence_json,
              blocker_level = excluded.blocker_level,
              updated_at = excluded.updated_at
            """,
            (
                data["contract_key"],
                data["module_name"],
                data["status"],
                1 if data.get("implemented") else 0,
                data.get("description"),
                _json(data.get("details", {})),
                _json(data.get("evidence", [])),
                data.get("blocker_level", "none"),
                data["updated_at"],
            ),
        )

    async def list_runtime_contracts(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM runtime_contracts ORDER BY module_name ASC"
        )
        return [_runtime_contract_from_row(dict(row)) for row in rows]

    async def upsert_design_gap(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO design_gaps (
              gap_id, module_name, current_behavior, design_gap, blocker_level,
              fix_phase, acceptance_tests_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gap_id) DO UPDATE SET
              module_name = excluded.module_name,
              current_behavior = excluded.current_behavior,
              design_gap = excluded.design_gap,
              blocker_level = excluded.blocker_level,
              fix_phase = excluded.fix_phase,
              acceptance_tests_json = excluded.acceptance_tests_json,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["gap_id"],
                data["module_name"],
                data["current_behavior"],
                data["design_gap"],
                data["blocker_level"],
                data["fix_phase"],
                _json(data.get("acceptance_tests", [])),
                data.get("status", "open"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_design_gaps(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM design_gaps ORDER BY blocker_level DESC, module_name ASC"
        )
        return [_design_gap_from_row(dict(row)) for row in rows]

    async def insert_safety_decision(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO safety_decisions (
              safety_decision_id, organization_id, actor_type, actor_id, task_id,
              action_type, action, object_type, object_id, decision, allowed,
              approval_required, risk_level, reason, payload_summary_json,
              asset_handles_json, destination, redactions_json, required_controls_json,
              policy_sources_json, trace_refs_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["safety_decision_id"],
                data["organization_id"],
                data["actor_type"],
                data["actor_id"],
                data.get("task_id"),
                data["action_type"],
                data["action"],
                data["object_type"],
                data.get("object_id"),
                data["decision"],
                1 if data["allowed"] else 0,
                1 if data["approval_required"] else 0,
                data["risk_level"],
                data["reason"],
                _json(data.get("payload_summary", {})),
                _json(data.get("asset_handles", [])),
                data.get("destination"),
                _json(data.get("redactions", [])),
                _json(data.get("required_controls", [])),
                _json(data.get("policy_sources", [])),
                _json(data.get("trace_refs", [])),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_safety_decision(self, decision_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM safety_decisions WHERE safety_decision_id = ?",
            (decision_id,),
        )
        return _safety_decision_from_row(dict(row)) if row else None

    async def upsert_persona_profile(self, data: dict[str, Any]) -> None:
        disclosure_policy = dict(data.get("disclosure_policy", {}) or {})
        disclosure_policy[_PERSONA_ENGINE_STORAGE_KEY] = {
            "risk_tone_policy": data.get(
                "risk_tone_policy",
                _DEFAULT_RISK_TONE_POLICY,
            ),
            "allowed_modes": data.get("allowed_modes", _DEFAULT_ALLOWED_MODES),
            "default_mode": data.get("default_mode", "default"),
        }
        await self._db.execute(
            """
            INSERT INTO persona_profiles (
              persona_profile_id, organization_id, member_id, display_name, summary,
              tone_policy_json, disclosure_policy_json, shell_label_mapping_json,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(persona_profile_id) DO UPDATE SET
              display_name = excluded.display_name,
              summary = excluded.summary,
              tone_policy_json = excluded.tone_policy_json,
              disclosure_policy_json = excluded.disclosure_policy_json,
              shell_label_mapping_json = excluded.shell_label_mapping_json,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["persona_profile_id"],
                data.get("organization_id", "org_default"),
                data.get("member_id"),
                data["display_name"],
                data["summary"],
                _json(data.get("tone_policy", {})),
                _json(disclosure_policy),
                _json(data.get("shell_label_mapping", {})),
                data.get("status", "active"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_persona_profiles(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            "SELECT * FROM persona_profiles WHERE status != 'deleted' ORDER BY updated_at DESC"
        )
        return [_persona_profile_from_row(dict(row)) for row in rows]

    async def get_persona_profile(self, profile_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM persona_profiles WHERE persona_profile_id = ?",
            (profile_id,),
        )
        return _persona_profile_from_row(dict(row)) if row else None

    async def insert_heart_snapshot(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO heart_state_snapshots (
              snapshot_id, organization_id, member_id, mood, urgency,
              relationship_temperature, companionship_intensity, deescalation_boundary,
              summary, inputs_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["snapshot_id"],
                data.get("organization_id", "org_default"),
                data["member_id"],
                data["mood"],
                data["urgency"],
                data["relationship_temperature"],
                data["companionship_intensity"],
                data.get("deescalation_boundary"),
                data["summary"],
                _json(data.get("inputs", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def upsert_persona_consistency_profile(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO persona_consistency_profiles (
              consistency_profile_id, organization_id, persona_profile_id, member_id,
              style_principles_json, forbidden_claims_json, mode_switch_rules_json,
              consistency_markers_json, disabled_patterns_json, source, status,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(persona_profile_id) DO UPDATE SET
              member_id = excluded.member_id,
              style_principles_json = excluded.style_principles_json,
              forbidden_claims_json = excluded.forbidden_claims_json,
              mode_switch_rules_json = excluded.mode_switch_rules_json,
              consistency_markers_json = excluded.consistency_markers_json,
              disabled_patterns_json = excluded.disabled_patterns_json,
              source = excluded.source,
              status = excluded.status,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                data["consistency_profile_id"],
                data.get("organization_id", "org_default"),
                data["persona_profile_id"],
                data.get("member_id"),
                _json(data.get("style_principles", [])),
                _json(data.get("forbidden_claims", [])),
                _json(data.get("mode_switch_rules", [])),
                _json(data.get("consistency_markers", [])),
                _json(data.get("disabled_patterns", [])),
                data.get("source", "phase22_default"),
                data.get("status", "active"),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_persona_consistency_profile(
        self,
        profile_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM persona_consistency_profiles
            WHERE persona_profile_id = ?
            """,
            (profile_id,),
        )
        return _persona_consistency_from_row(dict(row)) if row else None

    async def insert_heart_transition(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO heart_state_transitions (
              transition_id, organization_id, member_id, previous_snapshot_id,
              current_snapshot_id, source_turn_id, transition_factors_json,
              state_delta_json, confidence, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["transition_id"],
                data.get("organization_id", "org_default"),
                data["member_id"],
                data.get("previous_snapshot_id"),
                data["current_snapshot_id"],
                data.get("source_turn_id"),
                _json(data.get("transition_factors", [])),
                _json(data.get("state_delta", {})),
                data.get("confidence", 0.6),
                data.get("status", "active"),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def list_heart_transitions(
        self,
        member_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM heart_state_transitions
            WHERE member_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (member_id, limit),
        )
        return [_heart_transition_from_row(dict(row)) for row in rows]

    async def insert_tone_policy_resolution(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO tone_policy_resolutions (
              resolution_id, organization_id, turn_id, member_id, persona_profile_id,
              heart_snapshot_id, scenario, risk_level, tone_mode, conciseness,
              warmth, directness, technical_depth, anthropomorphic_level,
              disclosure_required, safety_notice_required, reason_codes_json,
              policy_snapshot_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["resolution_id"],
                data.get("organization_id", "org_default"),
                data.get("turn_id"),
                data.get("member_id"),
                data.get("persona_profile_id"),
                data.get("heart_snapshot_id"),
                data["scenario"],
                data.get("risk_level", "R1"),
                data["tone_mode"],
                data.get("conciseness", 0.72),
                data.get("warmth", 0.68),
                data.get("directness", 0.78),
                data.get("technical_depth", 0.66),
                data.get("anthropomorphic_level", 0.35),
                1 if data.get("disclosure_required") else 0,
                1 if data.get("safety_notice_required") else 0,
                _json(data.get("reason_codes", [])),
                _json(data.get("policy_snapshot", {})),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_tone_policy_resolution_for_turn(
        self,
        turn_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM tone_policy_resolutions
            WHERE turn_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (turn_id,),
        )
        return _tone_policy_resolution_from_row(dict(row)) if row else None

    async def insert_response_quality_evaluation(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO response_quality_evaluations (
              evaluation_id, organization_id, turn_id, response_plan_json,
              rubric_json, quality_markers_json, violations_json, score, passed,
              internal_leakage_count, high_risk_boundary_violation_count,
              trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["evaluation_id"],
                data.get("organization_id", "org_default"),
                data.get("turn_id"),
                _json(data.get("response_plan", {})),
                _json(data.get("rubric", {})),
                _json(data.get("quality_markers", {})),
                _json(data.get("violations", [])),
                data.get("score", 0.0),
                1 if data.get("passed") else 0,
                data.get("internal_leakage_count", 0),
                data.get("high_risk_boundary_violation_count", 0),
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_response_quality_evaluation_for_turn(
        self,
        turn_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM response_quality_evaluations
            WHERE turn_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (turn_id,),
        )
        return _response_quality_evaluation_from_row(dict(row)) if row else None

    async def insert_persona_heart_replay_run(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO persona_heart_replay_runs (
              run_id, organization_id, suite_id, case_key, status, turn_count,
              metrics_json, violation_counts_json, evidence_json, trace_id,
              created_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["run_id"],
                data.get("organization_id", "org_default"),
                data.get("suite_id", "suite_phase22_persona_heart_experience"),
                data["case_key"],
                data["status"],
                data.get("turn_count", 0),
                _json(data.get("metrics", {})),
                _json(data.get("violation_counts", {})),
                _json(data.get("evidence", {})),
                data.get("trace_id"),
                data["created_at"],
                data.get("completed_at"),
            ),
        )

    async def get_persona_heart_replay_run(self, run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM persona_heart_replay_runs WHERE run_id = ?",
            (run_id,),
        )
        return _persona_heart_replay_run_from_row(dict(row)) if row else None

    async def get_latest_heart_snapshot(self, member_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM heart_state_snapshots
            WHERE member_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (member_id,),
        )
        return _heart_snapshot_from_row(dict(row)) if row else None

    async def upsert_vector_collection(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO vector_store_collections (
              collection_id, organization_id, collection_name, target_type, provider,
              provider_status, storage_uri, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(organization_id, collection_name) DO UPDATE SET
              provider = excluded.provider,
              provider_status = excluded.provider_status,
              storage_uri = excluded.storage_uri,
              metadata_json = excluded.metadata_json,
              updated_at = excluded.updated_at
            """,
            (
                data["collection_id"],
                data.get("organization_id", "org_default"),
                data["collection_name"],
                data["target_type"],
                data["provider"],
                data["provider_status"],
                data.get("storage_uri"),
                _json(data.get("metadata", {})),
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def get_vector_collection(self, collection_name: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM vector_store_collections
            WHERE organization_id = 'org_default' AND collection_name = ?
            """,
            (collection_name,),
        )
        return _vector_collection_from_row(dict(row)) if row else None

    async def list_vector_collections(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM vector_store_collections
            ORDER BY collection_name ASC
            """
        )
        return [_vector_collection_from_row(dict(row)) for row in rows]

    async def upsert_local_vector_embedding(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO local_vector_embeddings (
              embedding_id, organization_id, collection_name, target_type, target_id,
              content_hash, embedding_json, embedding_dim, provider, embedding_model,
              metadata_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(collection_name, target_type, target_id) DO UPDATE SET
              content_hash = excluded.content_hash,
              embedding_json = excluded.embedding_json,
              embedding_dim = excluded.embedding_dim,
              provider = excluded.provider,
              embedding_model = excluded.embedding_model,
              metadata_json = excluded.metadata_json,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                data["embedding_id"],
                data.get("organization_id", "org_default"),
                data["collection_name"],
                data["target_type"],
                data["target_id"],
                data["content_hash"],
                _json(data["embedding"]),
                data["embedding_dim"],
                data["provider"],
                data["embedding_model"],
                _json(data.get("metadata", {})),
                data["status"],
                data["created_at"],
                data["updated_at"],
            ),
        )

    async def list_local_vector_embeddings(
        self,
        *,
        collection_name: str,
        target_type: str | None = None,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        where = ["collection_name = ?", "status = ?"]
        params: list[Any] = [collection_name, status]
        if target_type:
            where.append("target_type = ?")
            params.append(target_type)
        rows = await self._db.fetch_all(
            f"""
            SELECT *
            FROM local_vector_embeddings
            WHERE {" AND ".join(where)}
            ORDER BY updated_at DESC
            """,
            params,
        )
        return [_local_vector_embedding_from_row(dict(row)) for row in rows]

    async def count_local_vector_embeddings(self) -> int:
        row = await self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM local_vector_embeddings WHERE status = 'active'"
        )
        return int(row["count"] if row else 0)

    async def insert_vector_sync_job(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO vector_sync_jobs (
              job_id, organization_id, target_type, target_id, collection_id, provider,
              status, degraded_reason, item_count, vector_ref_ids_json, payload_json,
              trace_id, created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["job_id"],
                data.get("organization_id", "org_default"),
                data["target_type"],
                data.get("target_id"),
                data.get("collection_id"),
                data["provider"],
                data["status"],
                data.get("degraded_reason"),
                data.get("item_count", 0),
                _json(data.get("vector_ref_ids", [])),
                _json(data.get("payload", {})),
                data.get("trace_id"),
                data["created_at"],
                data["updated_at"],
                data.get("completed_at"),
            ),
        )

    async def get_vector_sync_job(self, job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM vector_sync_jobs WHERE job_id = ?",
            (job_id,),
        )
        return _vector_sync_job_from_row(dict(row)) if row else None


def _runtime_contract_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row["module_name"],
        "status": row["status"],
        "implemented": bool(row["implemented"]),
        "description": row.get("description"),
        "details": _load(row.pop("details_json")),
        "evidence": _load(row.pop("evidence_json")),
        "blocker_level": row["blocker_level"],
    }


def _design_gap_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["acceptance_tests"] = _load(row.pop("acceptance_tests_json"))
    return row


def _safety_decision_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["safety_decision_id"] = row["safety_decision_id"]
    row["allowed"] = bool(row["allowed"])
    row["approval_required"] = bool(row["approval_required"])
    row["payload_summary"] = _load(row.pop("payload_summary_json"))
    row["asset_handles"] = _load(row.pop("asset_handles_json"))
    row["redactions"] = _load(row.pop("redactions_json"))
    row["required_controls"] = _load(row.pop("required_controls_json"))
    row["policy_sources"] = _load(row.pop("policy_sources_json"))
    row["trace_refs"] = _load(row.pop("trace_refs_json"))
    row["checks"] = row["policy_sources"]
    return row


def _persona_profile_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["tone_policy"] = _load(row.pop("tone_policy_json"))
    disclosure_policy = _load(row.pop("disclosure_policy_json"))
    if not isinstance(disclosure_policy, dict):
        disclosure_policy = {}
    engine_policy = disclosure_policy.pop(_PERSONA_ENGINE_STORAGE_KEY, {})
    if not isinstance(engine_policy, dict):
        engine_policy = {}
    row["disclosure_policy"] = disclosure_policy
    row["risk_tone_policy"] = engine_policy.get(
        "risk_tone_policy",
        _DEFAULT_RISK_TONE_POLICY,
    )
    row["allowed_modes"] = engine_policy.get("allowed_modes", _DEFAULT_ALLOWED_MODES)
    row["default_mode"] = engine_policy.get("default_mode", "default")
    row["shell_label_mapping"] = _load(row.pop("shell_label_mapping_json"))
    return row


def _heart_snapshot_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["inputs"] = _load(row.pop("inputs_json"))
    if isinstance(row["inputs"], dict):
        row["user_state"] = row["inputs"].get("user_state", "steady")
        row["preferred_pace"] = row["inputs"].get("preferred_pace", "normal")
        row["deescalation_required"] = bool(
            row["inputs"].get("deescalation_required", bool(row.get("deescalation_boundary")))
        )
        row["risk_tone_override"] = row["inputs"].get("risk_tone_override")
        row["confidence"] = float(row["inputs"].get("confidence", 0.6))
    else:
        row["user_state"] = "steady"
        row["preferred_pace"] = "normal"
        row["deescalation_required"] = bool(row.get("deescalation_boundary"))
        row["risk_tone_override"] = None
        row["confidence"] = 0.6
    return row


def _persona_consistency_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["style_principles"] = _load(row.pop("style_principles_json"))
    row["forbidden_claims"] = _load(row.pop("forbidden_claims_json"))
    row["mode_switch_rules"] = _load(row.pop("mode_switch_rules_json"))
    row["consistency_markers"] = _load(row.pop("consistency_markers_json"))
    row["disabled_patterns"] = _load(row.pop("disabled_patterns_json"))
    return row


def _heart_transition_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["transition_factors"] = _load(row.pop("transition_factors_json"))
    row["state_delta"] = _load(row.pop("state_delta_json"))
    return row


def _tone_policy_resolution_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["disclosure_required"] = bool(row["disclosure_required"])
    row["safety_notice_required"] = bool(row["safety_notice_required"])
    row["reason_codes"] = _load(row.pop("reason_codes_json"))
    row["policy_snapshot"] = _load(row.pop("policy_snapshot_json"))
    return row


def _response_quality_evaluation_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["response_plan"] = _load(row.pop("response_plan_json"))
    row["rubric"] = _load(row.pop("rubric_json"))
    row["quality_markers"] = _load(row.pop("quality_markers_json"))
    row["violations"] = _load(row.pop("violations_json"))
    row["passed"] = bool(row["passed"])
    return row


def _persona_heart_replay_run_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metrics"] = _load(row.pop("metrics_json"))
    row["violation_counts"] = _load(row.pop("violation_counts_json"))
    row["evidence"] = _load(row.pop("evidence_json"))
    return row


def _vector_collection_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = _load(row.pop("metadata_json"))
    return row


def _local_vector_embedding_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["embedding"] = _load(row.pop("embedding_json"))
    row["metadata"] = _load(row.pop("metadata_json"))
    return row


def _vector_sync_job_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["vector_ref_ids"] = _load(row.pop("vector_ref_ids_json"))
    row["payload"] = _load(row.pop("payload_json"))
    return row


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: str | None) -> Any:
    return json.loads(value or "{}")
