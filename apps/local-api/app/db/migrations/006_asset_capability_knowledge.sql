ALTER TABLE secret_refs ADD COLUMN organization_id TEXT NOT NULL DEFAULT 'org_default';
ALTER TABLE secret_refs ADD COLUMN ref_uri TEXT;
ALTER TABLE secret_refs ADD COLUMN secret_type TEXT NOT NULL DEFAULT 'generic';
ALTER TABLE secret_refs ADD COLUMN provider TEXT NOT NULL DEFAULT 'local';
ALTER TABLE secret_refs ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE secret_refs ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE secret_refs ADD COLUMN expires_at TEXT;

UPDATE secret_refs
SET ref_uri = COALESCE(ref_uri, storage_uri),
    secret_type = CASE WHEN secret_type = 'generic' THEN kind ELSE secret_type END
WHERE ref_uri IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_secret_refs_uri
  ON secret_refs(ref_uri)
  WHERE ref_uri IS NOT NULL;

CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  provider TEXT,
  status TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  config_json TEXT NOT NULL,
  secret_ref TEXT,
  expires_at TEXT,
  last_verified_at TEXT,
  owner_scope_type TEXT NOT NULL DEFAULT 'member',
  owner_scope_id TEXT,
  visibility TEXT NOT NULL DEFAULT 'private',
  risk_level TEXT NOT NULL DEFAULT 'R1',
  summary_text TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  policy_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  archived_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_assets_org_type_status
  ON assets(organization_id, asset_type, status);

CREATE INDEX IF NOT EXISTS idx_assets_visibility
  ON assets(organization_id, visibility, status);

CREATE TABLE IF NOT EXISTS asset_policies (
  policy_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  policy_type TEXT NOT NULL,
  action TEXT NOT NULL,
  effect TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  approval_policy_json TEXT NOT NULL,
  condition_json TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_policies_asset_action
  ON asset_policies(asset_id, action, status);

CREATE TABLE IF NOT EXISTS asset_handles (
  handle_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  conversation_id TEXT,
  task_id TEXT,
  allowed_actions_json TEXT NOT NULL,
  blocked_actions_json TEXT NOT NULL,
  approval_required_actions_json TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  policy_sources_json TEXT NOT NULL,
  status TEXT NOT NULL,
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  trace_id TEXT,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_handles_subject
  ON asset_handles(subject_type, subject_id, status);

CREATE INDEX IF NOT EXISTS idx_asset_handles_expiry
  ON asset_handles(status, expires_at);

CREATE INDEX IF NOT EXISTS idx_asset_handles_asset
  ON asset_handles(asset_id, status);

CREATE TABLE IF NOT EXISTS asset_handle_events (
  event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  handle_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  reason TEXT,
  actor_type TEXT,
  actor_id TEXT,
  trace_id TEXT,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(handle_id) REFERENCES asset_handles(handle_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_handle_events_handle
  ON asset_handle_events(handle_id, created_at);

CREATE TABLE IF NOT EXISTS capability_edges (
  edge_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  action TEXT NOT NULL,
  effect TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  approval_policy_json TEXT NOT NULL,
  condition_json TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT,
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_capability_edges_subject
  ON capability_edges(subject_type, subject_id, status);

CREATE INDEX IF NOT EXISTS idx_capability_edges_object
  ON capability_edges(object_type, object_id, action, status);

CREATE TABLE IF NOT EXISTS capability_decision_logs (
  decision_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  trace_id TEXT,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  action TEXT NOT NULL,
  context_hash TEXT NOT NULL,
  decision TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  approval_required INTEGER NOT NULL,
  reason TEXT NOT NULL,
  policy_sources_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_capability_decision_logs_subject
  ON capability_decision_logs(subject_type, subject_id, created_at);

CREATE INDEX IF NOT EXISTS idx_capability_decision_logs_object
  ON capability_decision_logs(object_type, object_id, action, created_at);

CREATE TABLE IF NOT EXISTS knowledge_sources (
  source_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  content_hash TEXT,
  last_scanned_at TEXT,
  last_indexed_at TEXT,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_sources_asset
  ON knowledge_sources(asset_id, status);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  chunk_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  content_text TEXT NOT NULL,
  summary_text TEXT,
  token_estimate INTEGER,
  sensitivity TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id),
  FOREIGN KEY(source_id) REFERENCES knowledge_sources(source_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_asset
  ON knowledge_chunks(asset_id, status);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source
  ON knowledge_chunks(source_id, chunk_index);

CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts
  USING fts5(chunk_id UNINDEXED, content_text, summary_text, tokenize = 'unicode61');

CREATE TABLE IF NOT EXISTS knowledge_index_jobs (
  job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  source_id TEXT,
  job_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  next_run_at TEXT,
  error_code TEXT,
  error_summary TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id),
  FOREIGN KEY(source_id) REFERENCES knowledge_sources(source_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_jobs_idempotency
  ON knowledge_index_jobs(idempotency_key);

CREATE TABLE IF NOT EXISTS knowledge_vector_refs (
  vector_ref_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  collection_name TEXT NOT NULL,
  vector_id TEXT NOT NULL,
  embedding_provider TEXT NOT NULL,
  embedding_model TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  last_synced_at TEXT,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id),
  FOREIGN KEY(source_id) REFERENCES knowledge_sources(source_id),
  FOREIGN KEY(chunk_id) REFERENCES knowledge_chunks(chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_vector_refs_chunk
  ON knowledge_vector_refs(chunk_id, status);

CREATE TABLE IF NOT EXISTS knowledge_access_logs (
  access_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  source_id TEXT,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  action TEXT NOT NULL,
  decision_id TEXT,
  trace_id TEXT,
  query_hash TEXT,
  selected_chunk_ids_json TEXT NOT NULL,
  filtered_chunk_ids_json TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id),
  FOREIGN KEY(source_id) REFERENCES knowledge_sources(source_id)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_access_asset
  ON knowledge_access_logs(asset_id, created_at);

CREATE INDEX IF NOT EXISTS idx_knowledge_access_subject
  ON knowledge_access_logs(subject_type, subject_id, created_at);
