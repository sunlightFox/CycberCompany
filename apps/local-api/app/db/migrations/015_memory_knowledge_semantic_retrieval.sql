CREATE TABLE IF NOT EXISTS local_vector_embeddings (
  embedding_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  collection_name TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  embedding_json TEXT NOT NULL,
  embedding_dim INTEGER NOT NULL,
  provider TEXT NOT NULL,
  embedding_model TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(collection_name, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_local_vector_embeddings_collection_status
ON local_vector_embeddings(collection_name, target_type, status);

CREATE INDEX IF NOT EXISTS idx_local_vector_embeddings_target
ON local_vector_embeddings(target_type, target_id);
