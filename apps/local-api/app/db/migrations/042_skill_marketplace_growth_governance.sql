ALTER TABLE skill_repository_entries ADD COLUMN health_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE skill_repository_entries ADD COLUMN quality_score REAL NOT NULL DEFAULT 0.5;
ALTER TABLE skill_repository_entries ADD COLUMN install_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE skill_repository_entries ADD COLUMN compatibility_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE skill_repository_entries ADD COLUMN dependency_summary_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE skill_repository_entries ADD COLUMN latest_eval_status TEXT;
ALTER TABLE skill_repository_entries ADD COLUMN last_health_check_at TEXT;
ALTER TABLE skill_repository_entries ADD COLUMN health_reason TEXT;
ALTER TABLE skill_repository_entries ADD COLUMN package_metadata_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_skill_repository_entries_marketplace
ON skill_repository_entries(organization_id, health_status, quality_score, install_count);

CREATE TABLE IF NOT EXISTS skill_marketplace_package_versions (
  version_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  repository_id TEXT NOT NULL,
  package_ref TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  version TEXT,
  checksum TEXT,
  source_uri_hash TEXT,
  dependency_summary_json TEXT NOT NULL DEFAULT '{}',
  compatibility_json TEXT NOT NULL DEFAULT '{}',
  quality_score REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL,
  indexed_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(repository_id) REFERENCES skill_repositories(repository_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_marketplace_versions_unique
ON skill_marketplace_package_versions(repository_id, package_ref, version, checksum);

CREATE TABLE IF NOT EXISTS skill_marketplace_health_records (
  health_record_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  repository_id TEXT NOT NULL,
  package_ref TEXT,
  bundle_id TEXT,
  health_status TEXT NOT NULL,
  provider_status TEXT NOT NULL DEFAULT 'unknown',
  quality_score REAL NOT NULL DEFAULT 0.5,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  checked_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(repository_id) REFERENCES skill_repositories(repository_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_marketplace_health_repo
ON skill_marketplace_health_records(repository_id, package_ref, checked_at);

CREATE TABLE IF NOT EXISTS skill_marketplace_install_records (
  install_record_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  repository_id TEXT,
  package_ref TEXT,
  bundle_id TEXT,
  installed_bundle_id TEXT,
  skill_id TEXT,
  version TEXT,
  status TEXT NOT NULL,
  gate_status TEXT NOT NULL,
  eval_status TEXT,
  blocked_reason TEXT,
  source_uri_hash TEXT,
  requested_by_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_marketplace_installs_package
ON skill_marketplace_install_records(repository_id, package_ref, created_at);

CREATE TABLE IF NOT EXISTS skill_dependency_edges (
  edge_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  dependency_kind TEXT NOT NULL,
  required_action TEXT NOT NULL DEFAULT '',
  risk_level TEXT NOT NULL DEFAULT 'R1',
  status TEXT NOT NULL,
  fail_closed_reason TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_dependency_edges_unique
ON skill_dependency_edges(source_type, source_id, target_type, target_id, dependency_kind, required_action);

CREATE INDEX IF NOT EXISTS idx_skill_dependency_edges_source
ON skill_dependency_edges(organization_id, source_type, source_id, status);

CREATE TABLE IF NOT EXISTS skill_growth_candidate_evidence (
  evidence_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  candidate_id TEXT,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  experience_id TEXT,
  task_id TEXT,
  memory_id TEXT,
  outcome TEXT,
  reuse_score REAL NOT NULL DEFAULT 0,
  decision TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_growth_evidence_candidate
ON skill_growth_candidate_evidence(candidate_id, created_at);

CREATE INDEX IF NOT EXISTS idx_skill_growth_evidence_source
ON skill_growth_candidate_evidence(source_type, source_id);
