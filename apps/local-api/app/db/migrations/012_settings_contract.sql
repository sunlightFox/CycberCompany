CREATE TABLE IF NOT EXISTS runtime_settings (
  setting_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  settings_json TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  updated_by_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_settings_org
  ON runtime_settings(organization_id);

CREATE INDEX IF NOT EXISTS idx_runtime_settings_updated
  ON runtime_settings(updated_at);
