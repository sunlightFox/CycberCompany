CREATE TABLE IF NOT EXISTS tool_action_policies (
  policy_id TEXT PRIMARY KEY,
  tool_name TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL,
  action_category TEXT NOT NULL,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  allowed_scopes_json TEXT NOT NULL DEFAULT '[]',
  required_capabilities_json TEXT NOT NULL DEFAULT '[]',
  required_asset_kinds_json TEXT NOT NULL DEFAULT '[]',
  requires_task_binding INTEGER NOT NULL DEFAULT 0,
  requires_approval_from TEXT,
  deny_patterns_json TEXT NOT NULL DEFAULT '[]',
  output_dlp_policy_json TEXT NOT NULL DEFAULT '{}',
  audit_level TEXT NOT NULL DEFAULT 'standard',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_action_policies_category
ON tool_action_policies(action_category, status);

CREATE TABLE IF NOT EXISTS tool_policy_decisions (
  decision_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  tool_call_id TEXT,
  task_id TEXT,
  member_id TEXT,
  tool_name TEXT NOT NULL,
  policy_id TEXT,
  source TEXT NOT NULL,
  action_category TEXT NOT NULL,
  requested_risk_level TEXT NOT NULL,
  effective_risk_level TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  required_controls_json TEXT NOT NULL DEFAULT '[]',
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  sandbox_profile_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id),
  FOREIGN KEY(policy_id) REFERENCES tool_action_policies(policy_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_policy_decisions_tool_call
ON tool_policy_decisions(tool_call_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tool_policy_decisions_decision
ON tool_policy_decisions(decision, action_category, created_at);

CREATE TABLE IF NOT EXISTS terminal_sandbox_profiles (
  profile_id TEXT PRIMARY KEY,
  working_dir_policy TEXT NOT NULL,
  allowed_executables_json TEXT NOT NULL DEFAULT '[]',
  denied_executables_json TEXT NOT NULL DEFAULT '[]',
  env_policy_json TEXT NOT NULL DEFAULT '{}',
  network_policy TEXT NOT NULL,
  filesystem_policy_json TEXT NOT NULL DEFAULT '{}',
  timeout_seconds INTEGER NOT NULL DEFAULT 30,
  max_output_bytes INTEGER NOT NULL DEFAULT 200000,
  os_sandbox_backend TEXT NOT NULL,
  degraded_reason TEXT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_output_dlp_reports (
  dlp_report_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  tool_call_id TEXT,
  mcp_call_id TEXT,
  task_id TEXT,
  source_type TEXT NOT NULL,
  source_id TEXT,
  scan_target TEXT NOT NULL,
  findings_json TEXT NOT NULL DEFAULT '[]',
  redaction_count INTEGER NOT NULL DEFAULT 0,
  blocked INTEGER NOT NULL DEFAULT 0,
  manual_review_required INTEGER NOT NULL DEFAULT 0,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  redacted_preview TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_output_dlp_reports_tool_call
ON tool_output_dlp_reports(tool_call_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tool_output_dlp_reports_blocked
ON tool_output_dlp_reports(blocked, manual_review_required, created_at);

CREATE TABLE IF NOT EXISTS mcp_process_policy_checks (
  check_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT,
  display_name TEXT,
  command TEXT,
  command_allowed INTEGER NOT NULL DEFAULT 0,
  args_schema_valid INTEGER NOT NULL DEFAULT 0,
  env_refs_only INTEGER NOT NULL DEFAULT 0,
  no_inline_secret INTEGER NOT NULL DEFAULT 0,
  server_scope_valid INTEGER NOT NULL DEFAULT 0,
  member_scope_valid INTEGER NOT NULL DEFAULT 0,
  network_policy TEXT NOT NULL DEFAULT 'local_stdio_only',
  safety_preflight TEXT NOT NULL DEFAULT 'not_required',
  decision TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_process_policy_checks_server
ON mcp_process_policy_checks(server_id, created_at);

CREATE TABLE IF NOT EXISTS execution_boundary_diagnostics (
  diagnostic_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_boundary_diagnostics_subject
ON execution_boundary_diagnostics(subject_type, subject_id, created_at);

INSERT OR IGNORE INTO terminal_sandbox_profiles (
  profile_id, working_dir_policy, allowed_executables_json, denied_executables_json,
  env_policy_json, network_policy, filesystem_policy_json, timeout_seconds,
  max_output_bytes, os_sandbox_backend, degraded_reason, status, created_at, updated_at
) VALUES (
  'task_artifact_policy_guard',
  'task_artifact_sandbox_only',
  '["echo","dir","ls","pwd","type","cat","findstr","rg","git","python"]',
  '["powershell","cmd","pwsh","bash","sh","rm","del","format","diskpart"]',
  '{"inherit":"minimal","secret_env":"deny","inline_secret":"deny"}',
  'deny_external_network_by_default',
  '{"root":"task_artifact_sandbox","sensitive_paths":"deny","system_paths":"deny"}',
  30,
  200000,
  'none_with_policy_guard',
  'os_level_sandbox_not_enabled',
  'active',
  CURRENT_TIMESTAMP,
  CURRENT_TIMESTAMP
);
