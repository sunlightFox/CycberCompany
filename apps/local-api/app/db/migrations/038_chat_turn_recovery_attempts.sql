CREATE TABLE IF NOT EXISTS chat_turn_recovery_attempts (
  recovery_attempt_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  turn_id TEXT NOT NULL,
  task_id TEXT,
  attempt_index INTEGER NOT NULL,
  failure_type TEXT NOT NULL,
  root_cause TEXT NOT NULL,
  recovery_action TEXT NOT NULL,
  status TEXT NOT NULL,
  diagnostic_payload_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_turn_recovery_attempts_turn
ON chat_turn_recovery_attempts(turn_id, attempt_index);

CREATE INDEX IF NOT EXISTS idx_chat_turn_recovery_attempts_task
ON chat_turn_recovery_attempts(task_id, started_at);
