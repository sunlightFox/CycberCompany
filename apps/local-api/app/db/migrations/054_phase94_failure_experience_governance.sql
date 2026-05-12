CREATE TABLE IF NOT EXISTS failure_experience_records (
  failure_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT,
  conversation_id TEXT,
  turn_id TEXT,
  task_id TEXT,
  trace_id TEXT,
  memory_id TEXT,
  failure_class TEXT NOT NULL,
  reason_code TEXT,
  impact_scope TEXT,
  severity TEXT NOT NULL DEFAULT 'medium',
  summary_text TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  evidence_summary TEXT,
  source_payload_json TEXT NOT NULL DEFAULT '{}',
  recurrence_key TEXT NOT NULL,
  recurrence_count INTEGER NOT NULL DEFAULT 1,
  memory_decision TEXT NOT NULL DEFAULT 'not_written',
  review_status TEXT NOT NULL DEFAULT 'not_required',
  advisory_status TEXT NOT NULL DEFAULT 'inactive',
  human_review_required INTEGER NOT NULL DEFAULT 0,
  tombstone_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(memory_id) REFERENCES memory_items(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_failure_experience_member_created
  ON failure_experience_records(member_id, created_at);

CREATE INDEX IF NOT EXISTS idx_failure_experience_recurrence
  ON failure_experience_records(recurrence_key, created_at);

CREATE INDEX IF NOT EXISTS idx_failure_experience_review
  ON failure_experience_records(review_status, advisory_status, created_at);

CREATE TABLE IF NOT EXISTS regression_candidates (
  candidate_id TEXT PRIMARY KEY,
  failure_id TEXT NOT NULL,
  source_turn_id TEXT,
  source_trace_id TEXT,
  candidate_type TEXT NOT NULL DEFAULT 'chat_regression',
  status TEXT NOT NULL DEFAULT 'open',
  recurrence_key TEXT NOT NULL,
  recurrence_count INTEGER NOT NULL DEFAULT 1,
  failure_class TEXT NOT NULL,
  reason_code TEXT,
  summary_text TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  release_gate_id TEXT,
  accepted_into_suite TEXT,
  accepted_case_key TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(failure_id) REFERENCES failure_experience_records(failure_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_regression_candidates_recurrence
  ON regression_candidates(recurrence_key);

CREATE INDEX IF NOT EXISTS idx_regression_candidates_status
  ON regression_candidates(status, updated_at);
