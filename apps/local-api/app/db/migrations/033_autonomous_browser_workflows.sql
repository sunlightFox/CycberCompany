CREATE TABLE IF NOT EXISTS browser_workflow_intents (
  intent_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  conversation_id TEXT,
  turn_id TEXT,
  trace_id TEXT,
  natural_language_goal TEXT NOT NULL,
  action_type TEXT NOT NULL,
  target_url TEXT,
  target_key TEXT,
  content_summary TEXT,
  constraints_json TEXT NOT NULL DEFAULT '{}',
  missing_fields_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  resolver_evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_intents_conversation
ON browser_workflow_intents(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_intents_target
ON browser_workflow_intents(organization_id, target_key, action_type, status);

CREATE TABLE IF NOT EXISTS browser_workflow_plans (
  plan_id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  conversation_id TEXT,
  task_id TEXT,
  approval_id TEXT,
  trace_id TEXT,
  action_type TEXT NOT NULL,
  target_url TEXT,
  target_key TEXT,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  current_url TEXT,
  content_summary TEXT,
  form_data_json TEXT NOT NULL DEFAULT '{}',
  file_refs_json TEXT NOT NULL DEFAULT '[]',
  steps_json TEXT NOT NULL DEFAULT '[]',
  approval_binding_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  failure_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(intent_id) REFERENCES browser_workflow_intents(intent_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_plans_intent
ON browser_workflow_plans(intent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_plans_status
ON browser_workflow_plans(organization_id, status, updated_at);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_plans_task
ON browser_workflow_plans(task_id);

CREATE TABLE IF NOT EXISTS browser_workflow_steps (
  step_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  step_order INTEGER NOT NULL DEFAULT 0,
  step_type TEXT NOT NULL,
  tool_name TEXT,
  selector TEXT,
  label TEXT,
  status TEXT NOT NULL DEFAULT 'planned',
  risk_level TEXT NOT NULL DEFAULT 'R1',
  requires_approval INTEGER NOT NULL DEFAULT 0,
  input_redacted_json TEXT NOT NULL DEFAULT '{}',
  output_redacted_json TEXT NOT NULL DEFAULT '{}',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  approval_id TEXT,
  tool_call_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES browser_workflow_plans(plan_id),
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id),
  FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_steps_plan
ON browser_workflow_steps(plan_id, step_order);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_steps_status
ON browser_workflow_steps(plan_id, status);

CREATE TABLE IF NOT EXISTS browser_workflow_executions (
  execution_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  status TEXT NOT NULL,
  approval_id TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  failure_reason TEXT,
  user_visible_message TEXT,
  trace_id TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES browser_workflow_plans(plan_id),
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_executions_plan
ON browser_workflow_executions(plan_id, created_at);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_executions_status
ON browser_workflow_executions(status, updated_at);

CREATE TABLE IF NOT EXISTS browser_workflow_events (
  event_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  execution_id TEXT,
  event_type TEXT NOT NULL,
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES browser_workflow_plans(plan_id),
  FOREIGN KEY(execution_id) REFERENCES browser_workflow_executions(execution_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_events_plan
ON browser_workflow_events(plan_id, created_at);

CREATE TABLE IF NOT EXISTS browser_workflow_candidates (
  candidate_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  target_key TEXT,
  host TEXT NOT NULL,
  action_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'test_only',
  source TEXT NOT NULL DEFAULT 'autonomous_browser_workflow',
  manifest_json TEXT NOT NULL DEFAULT '{}',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  success_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  confidence REAL NOT NULL DEFAULT 0,
  recommended INTEGER NOT NULL DEFAULT 0,
  last_plan_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(last_plan_id) REFERENCES browser_workflow_plans(plan_id),
  UNIQUE(organization_id, host, action_type, source)
);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_candidates_lookup
ON browser_workflow_candidates(organization_id, host, action_type, status);

CREATE INDEX IF NOT EXISTS idx_browser_workflow_candidates_recommended
ON browser_workflow_candidates(recommended, updated_at);
