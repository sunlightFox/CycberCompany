ALTER TABLE mcp_servers ADD COLUMN runtime_profile_id TEXT;
ALTER TABLE mcp_servers ADD COLUMN lifecycle_status TEXT NOT NULL DEFAULT 'created';
ALTER TABLE mcp_servers ADD COLUMN circuit_state TEXT NOT NULL DEFAULT 'closed';
ALTER TABLE mcp_servers ADD COLUMN last_health_check_at TEXT;
ALTER TABLE mcp_servers ADD COLUMN consecutive_failure_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS mcp_runtime_profiles (
  profile_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  transport TEXT NOT NULL,
  command_policy_json TEXT NOT NULL DEFAULT '{}',
  args_policy_json TEXT NOT NULL DEFAULT '{}',
  env_policy_json TEXT NOT NULL DEFAULT '{}',
  member_scope_policy_json TEXT NOT NULL DEFAULT '{}',
  network_policy TEXT NOT NULL,
  filesystem_policy_json TEXT NOT NULL DEFAULT '{}',
  sandbox_backend TEXT NOT NULL,
  timeout_policy_json TEXT NOT NULL DEFAULT '{}',
  resource_trust_policy TEXT NOT NULL,
  prompt_trust_policy TEXT NOT NULL,
  status TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_runtime_profiles_server
ON mcp_runtime_profiles(server_id, created_at);

CREATE TABLE IF NOT EXISTS mcp_lifecycle_events (
  lifecycle_event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  profile_id TEXT,
  event_type TEXT NOT NULL,
  previous_status TEXT,
  current_status TEXT NOT NULL,
  circuit_state TEXT NOT NULL DEFAULT 'closed',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_lifecycle_events_server
ON mcp_lifecycle_events(server_id, created_at);

CREATE TABLE IF NOT EXISTS mcp_protocol_validation_reports (
  validation_report_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  mcp_call_id TEXT,
  operation TEXT NOT NULL,
  protocol_version TEXT,
  schema_valid INTEGER NOT NULL DEFAULT 0,
  capability_valid INTEGER NOT NULL DEFAULT 0,
  validation_status TEXT NOT NULL,
  issue_codes_json TEXT NOT NULL DEFAULT '[]',
  sanitized_payload_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_protocol_reports_server
ON mcp_protocol_validation_reports(server_id, operation, created_at);

CREATE TABLE IF NOT EXISTS mcp_content_sanitization_reports (
  sanitization_report_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT,
  trust_level TEXT NOT NULL,
  content_hash TEXT,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  mime_type TEXT,
  injection_detected INTEGER NOT NULL DEFAULT 0,
  dlp_report_id TEXT,
  sanitized_preview TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_sanitization_reports_source
ON mcp_content_sanitization_reports(source_type, source_id, created_at);

CREATE TABLE IF NOT EXISTS mcp_output_taint_records (
  taint_record_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  mcp_call_id TEXT,
  tool_call_id TEXT,
  taint_source TEXT NOT NULL,
  target_action TEXT,
  target_risk_level TEXT NOT NULL DEFAULT 'R1',
  guard_decision TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_output_taint_records_call
ON mcp_output_taint_records(mcp_call_id, created_at);
