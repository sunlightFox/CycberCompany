CREATE TABLE IF NOT EXISTS embedding_provider_configs (
  provider_id TEXT PRIMARY KEY,
  provider_type TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  embedding_model TEXT NOT NULL,
  embedding_dim INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  privacy_policy TEXT NOT NULL DEFAULT 'local_only',
  allow_cloud INTEGER NOT NULL DEFAULT 0,
  secret_ref TEXT,
  fallback_policy TEXT NOT NULL DEFAULT 'fts',
  degraded_reason TEXT,
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_embedding_provider_configs_status
ON embedding_provider_configs(provider_type, status);

CREATE TABLE IF NOT EXISTS retrieval_rerank_runs (
  rerank_run_id TEXT PRIMARY KEY,
  retrieval_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  provider TEXT,
  scoring_policy_json TEXT NOT NULL DEFAULT '{}',
  input_count INTEGER NOT NULL DEFAULT 0,
  selected_count INTEGER NOT NULL DEFAULT 0,
  suppressed_count INTEGER NOT NULL DEFAULT 0,
  fallback_used INTEGER NOT NULL DEFAULT 0,
  latency_ms REAL NOT NULL DEFAULT 0,
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_rerank_runs_retrieval
ON retrieval_rerank_runs(retrieval_id, target_type);

CREATE TABLE IF NOT EXISTS retrieval_suppressed_items (
  suppressed_id TEXT PRIMARY KEY,
  retrieval_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  sensitivity TEXT,
  selection_score REAL NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_suppressed_items_retrieval
ON retrieval_suppressed_items(retrieval_id, target_type);

CREATE INDEX IF NOT EXISTS idx_retrieval_suppressed_items_reason
ON retrieval_suppressed_items(reason, created_at);

CREATE TABLE IF NOT EXISTS knowledge_retrieval_logs (
  retrieval_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  trace_id TEXT,
  conversation_id TEXT,
  task_id TEXT,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  asset_id TEXT,
  query_text_hash TEXT NOT NULL,
  selected_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
  filtered_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
  ranking_json TEXT NOT NULL DEFAULT '[]',
  retrieval_sources_json TEXT NOT NULL DEFAULT '[]',
  degraded INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_knowledge_retrieval_logs_trace
ON knowledge_retrieval_logs(trace_id);

CREATE INDEX IF NOT EXISTS idx_knowledge_retrieval_logs_subject
ON knowledge_retrieval_logs(subject_type, subject_id, created_at);

CREATE TABLE IF NOT EXISTS retrieval_quality_reports (
  report_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  target_type TEXT NOT NULL,
  retrieval_id TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retrieval_quality_reports_target
ON retrieval_quality_reports(target_type, created_at);

INSERT OR IGNORE INTO embedding_provider_configs (
  provider_id, provider_type, provider_name, embedding_model, embedding_dim,
  status, privacy_policy, allow_cloud, secret_ref, fallback_policy,
  degraded_reason, config_json, created_at, updated_at
) VALUES (
  'local_hash_v1', 'local_hash', 'local', 'local_hash_v1', 64,
  'active', 'local_only', 0, NULL, 'fts',
  NULL, '{"deterministic":true,"quality":"smoke"}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO embedding_provider_configs (
  provider_id, provider_type, provider_name, embedding_model, embedding_dim,
  status, privacy_policy, allow_cloud, secret_ref, fallback_policy,
  degraded_reason, config_json, created_at, updated_at
) VALUES (
  'local_model_default', 'local_model', 'local_model', 'not_configured', 0,
  'disabled', 'local_only', 0, NULL, 'fts',
  'local_model_not_configured', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO embedding_provider_configs (
  provider_id, provider_type, provider_name, embedding_model, embedding_dim,
  status, privacy_policy, allow_cloud, secret_ref, fallback_policy,
  degraded_reason, config_json, created_at, updated_at
) VALUES (
  'chroma_default', 'chroma', 'chroma', 'optional_chroma', 64,
  'disabled', 'local_only', 0, NULL, 'fts',
  'chromadb_not_installed_or_unavailable', '{"optional":true}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO embedding_provider_configs (
  provider_id, provider_type, provider_name, embedding_model, embedding_dim,
  status, privacy_policy, allow_cloud, secret_ref, fallback_policy,
  degraded_reason, config_json, created_at, updated_at
) VALUES (
  'external_compatible_default', 'external_compatible', 'external_compatible',
  'not_configured', 0, 'disabled', 'no_cloud_by_default', 0, NULL, 'fts',
  'external_embedding_provider_disabled', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO embedding_provider_configs (
  provider_id, provider_type, provider_name, embedding_model, embedding_dim,
  status, privacy_policy, allow_cloud, secret_ref, fallback_policy,
  degraded_reason, config_json, created_at, updated_at
) VALUES (
  'disabled', 'disabled', 'disabled', 'none', 0,
  'disabled', 'none', 0, NULL, 'fts',
  'provider_disabled', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
);
