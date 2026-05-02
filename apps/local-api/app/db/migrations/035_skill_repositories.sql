CREATE TABLE IF NOT EXISTS skill_repositories (
  repository_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  provider TEXT NOT NULL,
  index_uri TEXT,
  base_uri TEXT,
  auth_json TEXT NOT NULL DEFAULT '{}',
  priority INTEGER NOT NULL DEFAULT 100,
  is_default INTEGER NOT NULL DEFAULT 0,
  trust_level TEXT NOT NULL DEFAULT 'restricted',
  status TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  last_refresh_at TEXT,
  last_error_code TEXT,
  last_error_summary TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_repositories_org_status
ON skill_repositories(organization_id, status, priority);

CREATE TABLE IF NOT EXISTS skill_repository_entries (
  entry_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  repository_id TEXT NOT NULL,
  package_ref TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  version TEXT,
  author TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  keywords_json TEXT NOT NULL DEFAULT '[]',
  source_json TEXT NOT NULL DEFAULT '{}',
  checksum TEXT,
  trust_level TEXT NOT NULL DEFAULT 'restricted',
  search_text TEXT NOT NULL,
  status TEXT NOT NULL,
  indexed_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(repository_id) REFERENCES skill_repositories(repository_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_repository_entries_ref
ON skill_repository_entries(repository_id, package_ref);

CREATE INDEX IF NOT EXISTS idx_skill_repository_entries_search
ON skill_repository_entries(organization_id, status, repository_id);

CREATE TABLE IF NOT EXISTS skill_repository_sync_runs (
  sync_run_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  repository_id TEXT NOT NULL,
  status TEXT NOT NULL,
  indexed_count INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_summary TEXT,
  trace_id TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(repository_id) REFERENCES skill_repositories(repository_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_repository_sync_runs_repo
ON skill_repository_sync_runs(repository_id, created_at);
