ALTER TABLE memory_items ADD COLUMN quality_score REAL NOT NULL DEFAULT 0.5;
ALTER TABLE memory_items ADD COLUMN quality_breakdown_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE memory_items ADD COLUMN version_index INTEGER NOT NULL DEFAULT 1;
ALTER TABLE memory_items ADD COLUMN conflict_group_id TEXT;
ALTER TABLE memory_items ADD COLUMN conflict_status TEXT NOT NULL DEFAULT 'clear';
ALTER TABLE memory_items ADD COLUMN reuse_score REAL NOT NULL DEFAULT 0;
ALTER TABLE memory_items ADD COLUMN reuse_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory_items ADD COLUMN last_reused_at TEXT;
ALTER TABLE memory_items ADD COLUMN retention_policy TEXT NOT NULL DEFAULT 'standard';
ALTER TABLE memory_items ADD COLUMN retention_reason TEXT;
ALTER TABLE memory_items ADD COLUMN expires_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_memory_items_quality_reuse
  ON memory_items(quality_score, reuse_score, updated_at);

CREATE INDEX IF NOT EXISTS idx_memory_items_conflict_group
  ON memory_items(conflict_group_id, conflict_status);

CREATE TABLE IF NOT EXISTS memory_experience_records (
  experience_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT,
  task_id TEXT,
  conversation_id TEXT,
  memory_id TEXT,
  conflict_group_id TEXT,
  layer TEXT NOT NULL,
  kind TEXT NOT NULL,
  outcome TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  source_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  score_json TEXT NOT NULL DEFAULT '{}',
  confidence_score REAL NOT NULL DEFAULT 0,
  reuse_score REAL NOT NULL DEFAULT 0,
  decision TEXT NOT NULL,
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(memory_id) REFERENCES memory_items(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_experience_records_task
  ON memory_experience_records(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_memory_experience_records_member
  ON memory_experience_records(member_id, outcome, created_at);

CREATE TABLE IF NOT EXISTS memory_conflict_records (
  conflict_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT,
  memory_id TEXT,
  related_memory_id TEXT,
  candidate_id TEXT,
  conflict_group_id TEXT NOT NULL,
  conflict_type TEXT NOT NULL,
  status TEXT NOT NULL,
  resolution TEXT,
  summary_text TEXT NOT NULL,
  source_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(memory_id) REFERENCES memory_items(memory_id),
  FOREIGN KEY(related_memory_id) REFERENCES memory_items(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_conflict_records_group
  ON memory_conflict_records(conflict_group_id, status);

CREATE INDEX IF NOT EXISTS idx_memory_conflict_records_member
  ON memory_conflict_records(member_id, status, created_at);

CREATE TABLE IF NOT EXISTS memory_reuse_feedback (
  feedback_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT,
  retrieval_id TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  task_id TEXT,
  feedback_type TEXT NOT NULL,
  rating REAL NOT NULL DEFAULT 0,
  source_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(memory_id) REFERENCES memory_items(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_reuse_feedback_retrieval
  ON memory_reuse_feedback(retrieval_id, memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_reuse_feedback_memory
  ON memory_reuse_feedback(memory_id, created_at);
