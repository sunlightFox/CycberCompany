CREATE TABLE IF NOT EXISTS task_checkpoints (
  checkpoint_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  step_id TEXT,
  tool_call_id TEXT,
  checkpoint_type TEXT NOT NULL,
  scope TEXT NOT NULL,
  status TEXT NOT NULL,
  item_count INTEGER NOT NULL DEFAULT 0,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  restorable INTEGER NOT NULL DEFAULT 1,
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  failure_reason TEXT,
  expires_at TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id)
);

CREATE INDEX IF NOT EXISTS idx_task_checkpoints_task
ON task_checkpoints(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_task_checkpoints_tool_call
ON task_checkpoints(tool_call_id);

CREATE TABLE IF NOT EXISTS checkpoint_items (
  checkpoint_item_id TEXT PRIMARY KEY,
  checkpoint_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  target_uri TEXT NOT NULL,
  target_path_redacted TEXT NOT NULL,
  item_type TEXT NOT NULL,
  exists_before INTEGER NOT NULL DEFAULT 0,
  before_checksum TEXT,
  before_size_bytes INTEGER NOT NULL DEFAULT 0,
  after_exists INTEGER,
  after_checksum TEXT,
  after_size_bytes INTEGER,
  snapshot_artifact_id TEXT,
  snapshot_uri TEXT,
  content_type TEXT,
  sensitivity TEXT NOT NULL DEFAULT 'low',
  restorable INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(checkpoint_id) REFERENCES task_checkpoints(checkpoint_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(snapshot_artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_checkpoint_items_checkpoint
ON checkpoint_items(checkpoint_id, created_at);

CREATE INDEX IF NOT EXISTS idx_checkpoint_items_task
ON checkpoint_items(task_id, created_at);

CREATE TABLE IF NOT EXISTS rollback_events (
  rollback_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  checkpoint_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL,
  restored_items INTEGER NOT NULL DEFAULT 0,
  skipped_items INTEGER NOT NULL DEFAULT 0,
  conflict_items INTEGER NOT NULL DEFAULT 0,
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(checkpoint_id) REFERENCES task_checkpoints(checkpoint_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_rollback_events_task
ON rollback_events(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_rollback_events_checkpoint
ON rollback_events(checkpoint_id, created_at);

CREATE TABLE IF NOT EXISTS rollback_items (
  rollback_item_id TEXT PRIMARY KEY,
  rollback_id TEXT NOT NULL,
  checkpoint_item_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  target_uri TEXT NOT NULL,
  action TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT,
  before_checksum TEXT,
  current_checksum TEXT,
  restored_checksum TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(rollback_id) REFERENCES rollback_events(rollback_id),
  FOREIGN KEY(checkpoint_item_id) REFERENCES checkpoint_items(checkpoint_item_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_rollback_items_event
ON rollback_items(rollback_id, created_at);
