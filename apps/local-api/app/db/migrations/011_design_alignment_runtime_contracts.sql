CREATE TABLE IF NOT EXISTS runtime_contracts (
  contract_key TEXT PRIMARY KEY,
  module_name TEXT NOT NULL,
  status TEXT NOT NULL,
  implemented INTEGER NOT NULL DEFAULT 0,
  description TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '[]',
  blocker_level TEXT NOT NULL DEFAULT 'none',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runtime_contracts_status
ON runtime_contracts(status, blocker_level);

CREATE TABLE IF NOT EXISTS design_gaps (
  gap_id TEXT PRIMARY KEY,
  module_name TEXT NOT NULL,
  current_behavior TEXT NOT NULL,
  design_gap TEXT NOT NULL,
  blocker_level TEXT NOT NULL,
  fix_phase TEXT NOT NULL,
  acceptance_tests_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_design_gaps_module_status
ON design_gaps(module_name, status, blocker_level);

CREATE TABLE IF NOT EXISTS safety_decisions (
  safety_decision_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  task_id TEXT,
  action_type TEXT NOT NULL,
  action TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT,
  decision TEXT NOT NULL,
  allowed INTEGER NOT NULL,
  approval_required INTEGER NOT NULL,
  risk_level TEXT NOT NULL,
  reason TEXT NOT NULL,
  payload_summary_json TEXT NOT NULL DEFAULT '{}',
  asset_handles_json TEXT NOT NULL DEFAULT '[]',
  destination TEXT,
  redactions_json TEXT NOT NULL DEFAULT '[]',
  required_controls_json TEXT NOT NULL DEFAULT '[]',
  policy_sources_json TEXT NOT NULL DEFAULT '[]',
  trace_refs_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_safety_decisions_actor_time
ON safety_decisions(actor_type, actor_id, created_at);
CREATE INDEX IF NOT EXISTS idx_safety_decisions_task
ON safety_decisions(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_safety_decisions_decision
ON safety_decisions(decision, risk_level);

CREATE TABLE IF NOT EXISTS persona_profiles (
  persona_profile_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT,
  display_name TEXT NOT NULL,
  summary TEXT NOT NULL,
  tone_policy_json TEXT NOT NULL DEFAULT '{}',
  disclosure_policy_json TEXT NOT NULL DEFAULT '{}',
  shell_label_mapping_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_persona_profiles_member_status
ON persona_profiles(member_id, status);

CREATE TABLE IF NOT EXISTS persona_modes (
  mode_id TEXT PRIMARY KEY,
  persona_profile_id TEXT NOT NULL,
  mode_key TEXT NOT NULL,
  summary TEXT NOT NULL,
  tone_policy_json TEXT NOT NULL DEFAULT '{}',
  activation_rules_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(persona_profile_id) REFERENCES persona_profiles(persona_profile_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_modes_profile_key
ON persona_modes(persona_profile_id, mode_key);

CREATE TABLE IF NOT EXISTS heart_state_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  mood TEXT NOT NULL,
  urgency TEXT NOT NULL,
  relationship_temperature REAL NOT NULL,
  companionship_intensity REAL NOT NULL,
  deescalation_boundary TEXT,
  summary TEXT NOT NULL,
  inputs_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_heart_state_member_time
ON heart_state_snapshots(member_id, created_at);

CREATE TABLE IF NOT EXISTS member_interaction_preferences (
  preference_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  preference_key TEXT NOT NULL,
  preference_value_json TEXT NOT NULL DEFAULT '{}',
  source_type TEXT NOT NULL,
  source_id TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_member_interaction_preferences_key
ON member_interaction_preferences(member_id, preference_key, status);

CREATE TABLE IF NOT EXISTS vector_store_collections (
  collection_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  collection_name TEXT NOT NULL,
  target_type TEXT NOT NULL,
  provider TEXT NOT NULL,
  provider_status TEXT NOT NULL,
  storage_uri TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_vector_store_collections_name
ON vector_store_collections(organization_id, collection_name);

CREATE TABLE IF NOT EXISTS vector_sync_jobs (
  job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT,
  collection_id TEXT,
  provider TEXT NOT NULL,
  status TEXT NOT NULL,
  degraded_reason TEXT,
  item_count INTEGER NOT NULL DEFAULT 0,
  vector_ref_ids_json TEXT NOT NULL DEFAULT '[]',
  payload_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_vector_sync_jobs_target_status
ON vector_sync_jobs(target_type, target_id, status);

ALTER TABLE tool_calls ADD COLUMN safety_decision_id TEXT;
ALTER TABLE tool_calls ADD COLUMN policy_snapshot_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE tool_calls ADD COLUMN resolved_asset_refs_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE mcp_calls ADD COLUMN safety_decision_id TEXT;
ALTER TABLE mcp_calls ADD COLUMN policy_snapshot_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE mcp_calls ADD COLUMN resolved_asset_refs_json TEXT NOT NULL DEFAULT '[]';

ALTER TABLE skill_runs ADD COLUMN safety_decision_id TEXT;
ALTER TABLE skill_runs ADD COLUMN policy_snapshot_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE skill_runs ADD COLUMN resolved_asset_refs_json TEXT NOT NULL DEFAULT '[]';
