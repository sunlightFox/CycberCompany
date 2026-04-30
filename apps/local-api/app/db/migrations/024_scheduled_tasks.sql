CREATE TABLE IF NOT EXISTS scheduled_tasks (
  scheduled_task_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  conversation_id TEXT,
  owner_member_id TEXT NOT NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  schedule_json TEXT NOT NULL DEFAULT '{}',
  execution_policy_json TEXT NOT NULL DEFAULT '{}',
  constraints_json TEXT NOT NULL DEFAULT '{}',
  next_run_at TEXT,
  last_run_at TEXT,
  consecutive_failure_count INTEGER NOT NULL DEFAULT 0,
  max_consecutive_failures INTEGER NOT NULL DEFAULT 3,
  dead_letter_reason TEXT,
  created_by_member_id TEXT,
  trace_id TEXT,
  archived_at TEXT,
  cancelled_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(owner_member_id) REFERENCES members(member_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_org_status_due
ON scheduled_tasks(organization_id, status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_owner
ON scheduled_tasks(owner_member_id, status);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_conversation
ON scheduled_tasks(conversation_id, status);

CREATE TABLE IF NOT EXISTS scheduled_task_runs (
  run_id TEXT PRIMARY KEY,
  scheduled_task_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  task_id TEXT,
  trace_id TEXT,
  trigger_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  scheduled_for TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  status TEXT NOT NULL,
  failure_reason TEXT,
  missed_reason TEXT,
  policy_decision_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(scheduled_task_id) REFERENCES scheduled_tasks(scheduled_task_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduled_task_runs_idempotency
ON scheduled_task_runs(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_task
ON scheduled_task_runs(scheduled_task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_task_runs_status
ON scheduled_task_runs(status, scheduled_for);

CREATE TABLE IF NOT EXISTS scheduled_task_events (
  event_id TEXT PRIMARY KEY,
  scheduled_task_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  run_id TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(scheduled_task_id) REFERENCES scheduled_tasks(scheduled_task_id),
  FOREIGN KEY(run_id) REFERENCES scheduled_task_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_task_events_subject
ON scheduled_task_events(scheduled_task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_task_events_run
ON scheduled_task_events(run_id, created_at);
