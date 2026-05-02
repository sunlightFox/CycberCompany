CREATE TABLE IF NOT EXISTS external_platform_adapters (
  adapter_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  platform_key TEXT NOT NULL,
  action_type TEXT NOT NULL,
  adapter_type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  supported_actions_json TEXT NOT NULL DEFAULT '[]',
  required_asset_types_json TEXT NOT NULL DEFAULT '[]',
  allowed_domains_json TEXT NOT NULL DEFAULT '[]',
  manifest_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(organization_id, platform_key, action_type, adapter_type, display_name)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapters_lookup
ON external_platform_adapters(organization_id, platform_key, action_type, adapter_type, status);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapters_status
ON external_platform_adapters(status, updated_at);

CREATE TABLE IF NOT EXISTS external_platform_adapter_versions (
  adapter_version_id TEXT PRIMARY KEY,
  adapter_id TEXT NOT NULL,
  version TEXT NOT NULL,
  manifest_json TEXT NOT NULL DEFAULT '{}',
  manifest_checksum TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(adapter_id) REFERENCES external_platform_adapters(adapter_id),
  UNIQUE(adapter_id, version)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapter_versions_adapter
ON external_platform_adapter_versions(adapter_id, status);

CREATE TABLE IF NOT EXISTS external_platform_adapter_steps (
  step_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  adapter_version_id TEXT NOT NULL,
  step_name TEXT NOT NULL,
  executor TEXT NOT NULL,
  tool_name TEXT,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  requires_approval INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'planned',
  input_redacted_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  approval_id TEXT,
  tool_call_id TEXT,
  mcp_call_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES external_platform_action_plans(plan_id),
  FOREIGN KEY(adapter_id) REFERENCES external_platform_adapters(adapter_id),
  FOREIGN KEY(adapter_version_id) REFERENCES external_platform_adapter_versions(adapter_version_id),
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id),
  FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id),
  FOREIGN KEY(mcp_call_id) REFERENCES mcp_calls(mcp_call_id)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapter_steps_plan
ON external_platform_adapter_steps(plan_id, adapter_id, status);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapter_steps_approval
ON external_platform_adapter_steps(approval_id);

CREATE TABLE IF NOT EXISTS external_platform_adapter_executions (
  adapter_execution_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  adapter_version_id TEXT NOT NULL,
  status TEXT NOT NULL,
  executor TEXT NOT NULL,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_summary TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES external_platform_action_plans(plan_id),
  FOREIGN KEY(adapter_id) REFERENCES external_platform_adapters(adapter_id),
  FOREIGN KEY(adapter_version_id) REFERENCES external_platform_adapter_versions(adapter_version_id)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapter_executions_plan
ON external_platform_adapter_executions(plan_id, created_at);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapter_executions_status
ON external_platform_adapter_executions(status, updated_at);

CREATE TABLE IF NOT EXISTS external_platform_adapter_drift_events (
  drift_event_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  adapter_id TEXT NOT NULL,
  step_id TEXT,
  drift_type TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES external_platform_action_plans(plan_id),
  FOREIGN KEY(adapter_id) REFERENCES external_platform_adapters(adapter_id),
  FOREIGN KEY(step_id) REFERENCES external_platform_adapter_steps(step_id)
);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapter_drift_plan
ON external_platform_adapter_drift_events(plan_id, created_at);

CREATE INDEX IF NOT EXISTS idx_external_platform_adapter_drift_adapter
ON external_platform_adapter_drift_events(adapter_id, drift_type, status);
