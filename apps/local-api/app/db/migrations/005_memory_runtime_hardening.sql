ALTER TABLE memory_jobs ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE memory_jobs ADD COLUMN next_run_at TEXT;
ALTER TABLE memory_jobs ADD COLUMN locked_by TEXT;
ALTER TABLE memory_jobs ADD COLUMN locked_at TEXT;

ALTER TABLE memory_items ADD COLUMN normalized_summary TEXT;
ALTER TABLE memory_items ADD COLUMN content_hash TEXT;

UPDATE memory_items
SET normalized_summary = lower(replace(replace(summary_text, ' ', ''), char(10), '')),
    content_hash = memory_id
WHERE normalized_summary IS NULL OR content_hash IS NULL;

CREATE INDEX IF NOT EXISTS idx_memory_jobs_status_next_run
  ON memory_jobs(status, next_run_at);

CREATE INDEX IF NOT EXISTS idx_memory_jobs_org_status
  ON memory_jobs(organization_id, status);

CREATE INDEX IF NOT EXISTS idx_memory_jobs_locked
  ON memory_jobs(locked_at, status);

CREATE INDEX IF NOT EXISTS idx_memory_items_member_status
  ON memory_items(member_id, status);

CREATE INDEX IF NOT EXISTS idx_memory_items_status_layer
  ON memory_items(status, layer);

CREATE INDEX IF NOT EXISTS idx_memory_items_valid_time
  ON memory_items(valid_from, valid_to);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_items_org_scope_hash_active
  ON memory_items(organization_id, scope_type, scope_id, content_hash)
  WHERE status = 'active';
