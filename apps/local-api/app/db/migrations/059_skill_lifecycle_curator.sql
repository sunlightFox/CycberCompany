CREATE TABLE IF NOT EXISTS skill_lifecycle_records (
  skill_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'system',
  provenance TEXT NOT NULL DEFAULT 'unknown',
  use_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  last_used_at TEXT,
  last_success_at TEXT,
  last_failure_at TEXT,
  pinned INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'active',
  archived_at TEXT,
  archive_reason TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(skill_id) REFERENCES skills(skill_id),
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_state
ON skill_lifecycle_records(organization_id, state, updated_at);

CREATE INDEX IF NOT EXISTS idx_skill_lifecycle_created_by
ON skill_lifecycle_records(organization_id, created_by, pinned, state);
