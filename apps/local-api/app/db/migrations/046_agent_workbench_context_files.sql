CREATE TABLE IF NOT EXISTS agent_workbench_jobs (
  job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  turn_id TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  next_run_at TEXT,
  locked_by TEXT,
  locked_at TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_message TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_workbench_jobs_status
  ON agent_workbench_jobs(status, next_run_at, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_workbench_jobs_turn
  ON agent_workbench_jobs(turn_id, job_type, created_at);

CREATE TABLE IF NOT EXISTS agent_workbench_context_packs (
  context_pack_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  member_id TEXT NOT NULL,
  conversation_id TEXT,
  turn_id TEXT,
  summary_text TEXT NOT NULL,
  memory_refs_json TEXT NOT NULL DEFAULT '[]',
  skill_refs_json TEXT NOT NULL DEFAULT '[]',
  context_file_refs_json TEXT NOT NULL DEFAULT '[]',
  working_state_json TEXT NOT NULL DEFAULT '{}',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  token_estimate INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_workbench_context_packs_member
  ON agent_workbench_context_packs(member_id, conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_agent_workbench_context_packs_turn
  ON agent_workbench_context_packs(turn_id, created_at);

CREATE TABLE IF NOT EXISTS agent_context_file_versions (
  version_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  member_id TEXT NOT NULL,
  conversation_id TEXT,
  context_file_key TEXT NOT NULL,
  version_index INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  summary_text TEXT NOT NULL,
  artifact_uri TEXT NOT NULL,
  artifact_checksum TEXT NOT NULL,
  artifact_size_bytes INTEGER NOT NULL DEFAULT 0,
  source_turn_id TEXT,
  source_trace_id TEXT,
  context_pack_id TEXT,
  diff_base_version_id TEXT,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  memory_refs_json TEXT NOT NULL DEFAULT '[]',
  skill_refs_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(source_turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(context_pack_id) REFERENCES agent_workbench_context_packs(context_pack_id),
  FOREIGN KEY(diff_base_version_id) REFERENCES agent_context_file_versions(version_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_context_file_versions_key_version
  ON agent_context_file_versions(context_file_key, version_index);

CREATE INDEX IF NOT EXISTS idx_agent_context_file_versions_member
  ON agent_context_file_versions(member_id, conversation_id, status, version_index);

CREATE INDEX IF NOT EXISTS idx_agent_context_file_versions_turn
  ON agent_context_file_versions(source_turn_id, created_at);
