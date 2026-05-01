CREATE TABLE IF NOT EXISTS external_platform_targets (
  target_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  platform_key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  aliases_json TEXT NOT NULL DEFAULT '[]',
  supported_actions_json TEXT NOT NULL DEFAULT '[]',
  required_asset_types_json TEXT NOT NULL DEFAULT '[]',
  execution_modes_json TEXT NOT NULL DEFAULT '[]',
  risk_defaults_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'active',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(organization_id, platform_key)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_targets_org_status
ON external_platform_targets(organization_id, status);

CREATE INDEX IF NOT EXISTS idx_external_platform_targets_platform
ON external_platform_targets(platform_key);

CREATE TABLE IF NOT EXISTS external_platform_action_intents (
  intent_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  conversation_id TEXT,
  turn_id TEXT,
  trace_id TEXT,
  platform_hint TEXT,
  platform_key TEXT,
  action_type TEXT NOT NULL,
  content_redacted TEXT,
  content_summary TEXT,
  target_hint TEXT,
  constraints_json TEXT NOT NULL DEFAULT '{}',
  confidence REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  missing_fields_json TEXT NOT NULL DEFAULT '[]',
  resolver_evidence_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_external_platform_intents_conversation
ON external_platform_action_intents(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_external_platform_intents_platform
ON external_platform_action_intents(platform_key, action_type, status);

CREATE TABLE IF NOT EXISTS external_platform_action_plans (
  plan_id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  conversation_id TEXT,
  task_id TEXT,
  approval_id TEXT,
  trace_id TEXT,
  platform_key TEXT,
  target_id TEXT,
  selected_asset_id TEXT,
  selected_handle_id TEXT,
  action_type TEXT NOT NULL,
  execution_mode TEXT NOT NULL,
  steps_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  content_summary TEXT,
  failure_reason TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(intent_id) REFERENCES external_platform_action_intents(intent_id),
  FOREIGN KEY(target_id) REFERENCES external_platform_targets(target_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id),
  FOREIGN KEY(selected_asset_id) REFERENCES assets(asset_id),
  FOREIGN KEY(selected_handle_id) REFERENCES asset_handles(handle_id)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_plans_intent
ON external_platform_action_plans(intent_id, created_at);

CREATE INDEX IF NOT EXISTS idx_external_platform_plans_status
ON external_platform_action_plans(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_external_platform_plans_task
ON external_platform_action_plans(task_id);

CREATE INDEX IF NOT EXISTS idx_external_platform_plans_approval
ON external_platform_action_plans(approval_id);

CREATE TABLE IF NOT EXISTS external_platform_executions (
  execution_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  executor TEXT NOT NULL,
  step_type TEXT NOT NULL,
  status TEXT NOT NULL,
  request_summary_json TEXT NOT NULL DEFAULT '{}',
  response_summary_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_summary TEXT,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  trace_id TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES external_platform_action_plans(plan_id)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_executions_plan
ON external_platform_executions(plan_id, created_at);

CREATE TABLE IF NOT EXISTS external_platform_plan_events (
  event_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES external_platform_action_plans(plan_id)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_plan_events_plan
ON external_platform_plan_events(plan_id, created_at);
