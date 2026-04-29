CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  conversation_id TEXT,
  owner_member_id TEXT NOT NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  success_criteria_json TEXT NOT NULL DEFAULT '[]',
  plan_json TEXT NOT NULL DEFAULT '{}',
  budget_json TEXT NOT NULL DEFAULT '{}',
  preflight_json TEXT NOT NULL DEFAULT '{}',
  artifact_plan_json TEXT NOT NULL DEFAULT '{}',
  retry_policy_json TEXT NOT NULL DEFAULT '{}',
  progress_json TEXT NOT NULL DEFAULT '{}',
  current_approval_id TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  client_request_id TEXT,
  cancellation_reason TEXT,
  failure_reason TEXT,
  trace_id TEXT,
  archived_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(owner_member_id) REFERENCES members(member_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_client_request
ON tasks(client_request_id)
WHERE client_request_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_org_status ON tasks(organization_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_member_id);
CREATE INDEX IF NOT EXISTS idx_tasks_trace ON tasks(trace_id);

CREATE TABLE IF NOT EXISTS task_steps (
  step_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  step_key TEXT NOT NULL,
  idempotency_key TEXT,
  sequence INTEGER NOT NULL,
  step_type TEXT NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT NOT NULL DEFAULT '{}',
  retry_count INTEGER NOT NULL DEFAULT 0,
  max_retries INTEGER NOT NULL DEFAULT 2,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  approval_id TEXT,
  tool_call_id TEXT,
  error_code TEXT,
  error_summary TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_task_steps_idempotency
ON task_steps(idempotency_key)
WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_task_steps_task_status ON task_steps(task_id, status);

CREATE TABLE IF NOT EXISTS task_events (
  event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  step_id TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_time ON task_events(task_id, created_at);

CREATE TABLE IF NOT EXISTS task_jobs (
  job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  step_id TEXT,
  job_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'normal',
  payload_json TEXT NOT NULL DEFAULT '{}',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  next_run_at TEXT,
  locked_by TEXT,
  locked_at TEXT,
  error_code TEXT,
  error_summary TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_task_jobs_idempotency ON task_jobs(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_task_jobs_status_next ON task_jobs(status, next_run_at);

CREATE TABLE IF NOT EXISTS task_artifacts (
  artifact_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  step_id TEXT,
  tool_call_id TEXT,
  artifact_type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  uri TEXT NOT NULL,
  content_type TEXT,
  size_bytes INTEGER,
  checksum TEXT,
  sensitivity TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_task ON task_artifacts(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_artifacts_task_type
ON task_artifacts(task_id, artifact_type);

CREATE TABLE IF NOT EXISTS tool_registry (
  tool_name TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL,
  source TEXT NOT NULL,
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  output_schema_json TEXT NOT NULL DEFAULT '{}',
  risk_policy_json TEXT NOT NULL DEFAULT '{}',
  required_handle_types_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
  tool_call_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT,
  step_id TEXT,
  tool_name TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL,
  idempotency_key TEXT,
  args_redacted_json TEXT NOT NULL DEFAULT '{}',
  result_redacted_json TEXT NOT NULL DEFAULT '{}',
  handle_ids_json TEXT NOT NULL DEFAULT '[]',
  capability_decision_id TEXT,
  safety_decision_json TEXT NOT NULL DEFAULT '{}',
  risk_level TEXT NOT NULL DEFAULT 'R1',
  approval_id TEXT,
  timeout_seconds INTEGER,
  artifact_ids_json TEXT NOT NULL DEFAULT '[]',
  error_code TEXT,
  error_summary TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tool_calls_idempotency
ON tool_calls(idempotency_key)
WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tool_calls_task_status ON tool_calls(task_id, status);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  step_id TEXT,
  tool_call_id TEXT,
  approval_type TEXT NOT NULL DEFAULT 'action',
  requested_action TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  options_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  expires_at TEXT,
  decision_reason TEXT,
  edited_payload_json TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  resolved_at TEXT,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_approvals_task_status ON approvals(task_id, status);

CREATE TABLE IF NOT EXISTS approval_events (
  event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  approval_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  actor_type TEXT,
  actor_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id)
);
