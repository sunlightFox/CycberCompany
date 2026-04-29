CREATE TABLE IF NOT EXISTS memory_items (
  memory_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT,
  user_id TEXT NOT NULL,
  layer TEXT NOT NULL,
  kind TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_id TEXT,
  summary_text TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  source_json TEXT NOT NULL,
  confidence REAL NOT NULL,
  importance REAL NOT NULL DEFAULT 0.5,
  sensitivity TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT,
  supersedes TEXT,
  status TEXT NOT NULL,
  last_accessed_at TEXT,
  access_count INTEGER NOT NULL DEFAULT 0,
  review_required INTEGER NOT NULL DEFAULT 0,
  embedding_status TEXT NOT NULL DEFAULT 'pending',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(supersedes) REFERENCES memory_items(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_member_layer
  ON memory_items(member_id, layer);

CREATE INDEX IF NOT EXISTS idx_memory_items_kind
  ON memory_items(kind);

CREATE INDEX IF NOT EXISTS idx_memory_items_scope
  ON memory_items(scope_type, scope_id);

CREATE INDEX IF NOT EXISTS idx_memory_items_status
  ON memory_items(status, updated_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_items_fts
  USING fts5(summary_text, memory_id UNINDEXED);

CREATE TABLE IF NOT EXISTS memory_candidates (
  candidate_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT,
  user_id TEXT NOT NULL,
  source_json TEXT NOT NULL,
  proposed_layer TEXT NOT NULL,
  proposed_kind TEXT NOT NULL,
  proposed_scope_type TEXT NOT NULL,
  proposed_scope_id TEXT,
  summary_text TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  score_json TEXT NOT NULL,
  final_score REAL NOT NULL,
  sensitivity TEXT NOT NULL,
  decision TEXT NOT NULL,
  decision_reason TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_member_decision
  ON memory_candidates(member_id, decision);

CREATE INDEX IF NOT EXISTS idx_memory_candidates_created
  ON memory_candidates(created_at);

CREATE TABLE IF NOT EXISTS memory_relations (
  relation_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  source_memory_id TEXT NOT NULL,
  target_memory_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(source_memory_id) REFERENCES memory_items(memory_id),
  FOREIGN KEY(target_memory_id) REFERENCES memory_items(memory_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_relations_source
  ON memory_relations(source_memory_id);

CREATE INDEX IF NOT EXISTS idx_memory_relations_target
  ON memory_relations(target_memory_id);

CREATE TABLE IF NOT EXISTS memory_vector_refs (
  vector_ref_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  memory_id TEXT NOT NULL,
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
  FOREIGN KEY(memory_id) REFERENCES memory_items(memory_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_vector_refs_memory_collection
  ON memory_vector_refs(memory_id, collection_name);

CREATE TABLE IF NOT EXISTS memory_retrieval_logs (
  retrieval_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  trace_id TEXT,
  turn_id TEXT,
  conversation_id TEXT,
  member_id TEXT,
  query_text_hash TEXT NOT NULL,
  intent TEXT,
  selected_memory_ids_json TEXT NOT NULL,
  filtered_memory_ids_json TEXT NOT NULL,
  ranking_json TEXT NOT NULL,
  token_budget_json TEXT NOT NULL,
  degraded INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_retrieval_logs_trace
  ON memory_retrieval_logs(trace_id);

CREATE TABLE IF NOT EXISTS memory_jobs (
  job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  turn_id TEXT,
  idempotency_key TEXT NOT NULL,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_message TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_jobs_idempotency
  ON memory_jobs(idempotency_key);
