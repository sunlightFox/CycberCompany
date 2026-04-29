CREATE TABLE IF NOT EXISTS plugin_bundles (
  bundle_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  author TEXT,
  bundle_revision TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_uri TEXT,
  package_uri TEXT,
  manifest_hash TEXT NOT NULL,
  signature_status TEXT NOT NULL,
  trust_level TEXT NOT NULL,
  status TEXT NOT NULL,
  permission_summary_json TEXT NOT NULL DEFAULT '{}',
  risk_summary_json TEXT NOT NULL DEFAULT '{}',
  manifest_json TEXT NOT NULL DEFAULT '{}',
  installed_by_member_id TEXT,
  installed_at TEXT,
  enabled_at TEXT,
  disabled_at TEXT,
  revoked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plugin_bundles_org_status
ON plugin_bundles(organization_id, status);

CREATE TABLE IF NOT EXISTS plugin_files (
  file_id TEXT PRIMARY KEY,
  bundle_id TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  file_type TEXT NOT NULL,
  size_bytes INTEGER,
  checksum TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plugin_files_bundle_path
ON plugin_files(bundle_id, relative_path);

CREATE TABLE IF NOT EXISTS skills (
  skill_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  entrypoint_path TEXT NOT NULL,
  instructions TEXT NOT NULL,
  trigger_json TEXT NOT NULL DEFAULT '{}',
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  output_schema_json TEXT NOT NULL DEFAULT '{}',
  required_tools_json TEXT NOT NULL DEFAULT '[]',
  required_assets_json TEXT NOT NULL DEFAULT '[]',
  permission_json TEXT NOT NULL DEFAULT '{}',
  risk_policy_json TEXT NOT NULL DEFAULT '{}',
  eval_summary_json TEXT NOT NULL DEFAULT '{}',
  steps_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);

CREATE INDEX IF NOT EXISTS idx_skills_bundle_status ON skills(bundle_id, status);
CREATE INDEX IF NOT EXISTS idx_skills_org_status ON skills(organization_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_bundle_name ON skills(bundle_id, name);

CREATE TABLE IF NOT EXISTS skill_runs (
  skill_run_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  task_id TEXT,
  step_id TEXT,
  owner_member_id TEXT NOT NULL,
  status TEXT NOT NULL,
  input_redacted_json TEXT NOT NULL DEFAULT '{}',
  output_redacted_json TEXT NOT NULL DEFAULT '{}',
  matched_reason TEXT,
  confidence REAL,
  capability_decision_id TEXT,
  approval_id TEXT,
  artifact_ids_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  error_code TEXT,
  error_summary TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_runs_task ON skill_runs(task_id, created_at);

CREATE TABLE IF NOT EXISTS skill_candidates (
  candidate_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  draft_manifest_json TEXT NOT NULL DEFAULT '{}',
  draft_skill_md TEXT NOT NULL,
  proposed_permissions_json TEXT NOT NULL DEFAULT '{}',
  proposed_eval_cases_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  reviewed_by_member_id TEXT,
  review_reason TEXT,
  promoted_bundle_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_candidates_status
ON skill_candidates(organization_id, status);

CREATE TABLE IF NOT EXISTS skill_eval_cases (
  eval_case_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT,
  bundle_id TEXT,
  case_key TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}',
  expected_json TEXT NOT NULL DEFAULT '{}',
  forbidden_json TEXT NOT NULL DEFAULT '{}',
  risk_assertions_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_eval_cases_skill ON skill_eval_cases(skill_id, status);

CREATE TABLE IF NOT EXISTS skill_eval_runs (
  eval_run_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT,
  bundle_id TEXT,
  status TEXT NOT NULL,
  total_cases INTEGER NOT NULL,
  passed_cases INTEGER NOT NULL,
  failed_cases INTEGER NOT NULL,
  security_failures INTEGER NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_eval_runs_skill ON skill_eval_runs(skill_id, created_at);

CREATE TABLE IF NOT EXISTS mcp_servers (
  server_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  transport TEXT NOT NULL,
  command TEXT,
  args_json TEXT NOT NULL DEFAULT '[]',
  url TEXT,
  env_refs_json TEXT NOT NULL DEFAULT '[]',
  allowed_skills_json TEXT NOT NULL DEFAULT '[]',
  permission_json TEXT NOT NULL DEFAULT '{}',
  risk_policy_json TEXT NOT NULL DEFAULT '{}',
  trust_level TEXT NOT NULL,
  status TEXT NOT NULL,
  last_connected_at TEXT,
  last_sync_at TEXT,
  last_error_code TEXT,
  last_error_summary TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_servers_org_status ON mcp_servers(organization_id, status);

CREATE TABLE IF NOT EXISTS mcp_tools (
  mcp_tool_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  registry_tool_name TEXT NOT NULL,
  description TEXT,
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  output_schema_json TEXT NOT NULL DEFAULT '{}',
  risk_policy_json TEXT NOT NULL DEFAULT '{}',
  required_handle_types_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(server_id) REFERENCES mcp_servers(server_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_tools_server_tool
ON mcp_tools(server_id, tool_name);
CREATE INDEX IF NOT EXISTS idx_mcp_tools_server_status ON mcp_tools(server_id, status);

CREATE TABLE IF NOT EXISTS mcp_resources (
  resource_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  uri TEXT NOT NULL,
  name TEXT,
  description TEXT,
  mime_type TEXT,
  trust_level TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(server_id) REFERENCES mcp_servers(server_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_resources_server_uri
ON mcp_resources(server_id, uri);

CREATE TABLE IF NOT EXISTS mcp_prompts (
  prompt_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  arguments_schema_json TEXT NOT NULL DEFAULT '{}',
  prompt_template_redacted TEXT,
  trust_level TEXT NOT NULL,
  status TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(server_id) REFERENCES mcp_servers(server_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_prompts_server_name
ON mcp_prompts(server_id, name);

CREATE TABLE IF NOT EXISTS mcp_calls (
  mcp_call_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  mcp_tool_id TEXT,
  task_id TEXT,
  step_id TEXT,
  tool_call_id TEXT,
  status TEXT NOT NULL,
  request_redacted_json TEXT NOT NULL DEFAULT '{}',
  response_redacted_json TEXT NOT NULL DEFAULT '{}',
  capability_decision_id TEXT,
  approval_id TEXT,
  trace_id TEXT,
  error_code TEXT,
  error_summary TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_calls_task ON mcp_calls(task_id, created_at);

CREATE TABLE IF NOT EXISTS plugin_install_jobs (
  job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  idempotency_key TEXT NOT NULL,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  rollback_result_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_summary TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_plugin_install_jobs_idempotency
ON plugin_install_jobs(idempotency_key);

CREATE TABLE IF NOT EXISTS plugin_events (
  event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  skill_id TEXT,
  server_id TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plugin_events_bundle_time
ON plugin_events(bundle_id, created_at);

ALTER TABLE tool_registry ADD COLUMN bundle_id TEXT;
ALTER TABLE tool_registry ADD COLUMN skill_id TEXT;
ALTER TABLE tool_registry ADD COLUMN mcp_server_id TEXT;
ALTER TABLE tool_registry ADD COLUMN mcp_tool_id TEXT;
ALTER TABLE tool_registry ADD COLUMN adapter_config_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE tool_registry ADD COLUMN trust_level TEXT NOT NULL DEFAULT 'local';
