ALTER TABLE extension_compatibility_reports ADD COLUMN compatibility_tier TEXT NOT NULL DEFAULT 'manifest_compatible';
ALTER TABLE extension_compatibility_reports ADD COLUMN smoke_check_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE extension_compatibility_reports ADD COLUMN package_compatibility_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE extension_compatibility_reports ADD COLUMN blocked_reasons_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS extension_runtime_contributions (
  contribution_id TEXT PRIMARY KEY,
  extension_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  contribution_type TEXT NOT NULL,
  runtime_kind TEXT NOT NULL,
  name TEXT,
  status TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(extension_id) REFERENCES extension_packages(extension_id)
);

CREATE INDEX IF NOT EXISTS idx_extension_runtime_contributions_extension_id
ON extension_runtime_contributions(extension_id, status);

CREATE INDEX IF NOT EXISTS idx_extension_runtime_contributions_type
ON extension_runtime_contributions(contribution_type, runtime_kind, status);

CREATE TABLE IF NOT EXISTS extension_diagnostics (
  diagnostic_id TEXT PRIMARY KEY,
  extension_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  status TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  compatibility_json TEXT NOT NULL DEFAULT '{}',
  binding_json TEXT NOT NULL DEFAULT '{}',
  mcp_json TEXT NOT NULL DEFAULT '{}',
  config_json TEXT NOT NULL DEFAULT '{}',
  secrets_json TEXT NOT NULL DEFAULT '{}',
  env_json TEXT NOT NULL DEFAULT '{}',
  contributions_json TEXT NOT NULL DEFAULT '[]',
  health_json TEXT NOT NULL DEFAULT '{}',
  next_actions_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(extension_id) REFERENCES extension_packages(extension_id)
);

CREATE INDEX IF NOT EXISTS idx_extension_diagnostics_extension_id
ON extension_diagnostics(extension_id, updated_at DESC);
