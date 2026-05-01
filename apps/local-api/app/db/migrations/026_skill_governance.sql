CREATE TABLE IF NOT EXISTS skill_bundle_sources (
  source_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_uri_redacted TEXT,
  source_uri_hash TEXT,
  signature_status TEXT NOT NULL DEFAULT 'unsigned',
  checksum TEXT,
  trust_level TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_bundle_sources_bundle
ON skill_bundle_sources(bundle_id, created_at);

CREATE TABLE IF NOT EXISTS skill_bundle_versions (
  version_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  bundle_revision TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  signature_status TEXT NOT NULL DEFAULT 'unsigned',
  trust_level TEXT NOT NULL,
  permission_summary_json TEXT NOT NULL DEFAULT '{}',
  risk_summary_json TEXT NOT NULL DEFAULT '{}',
  manifest_redacted_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  installed_by_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_bundle_versions_unique
ON skill_bundle_versions(bundle_id, bundle_revision, manifest_hash);

CREATE TABLE IF NOT EXISTS skill_permission_previews (
  preview_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  bundle_revision TEXT,
  manifest_hash TEXT NOT NULL,
  trust_level TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  permission_summary_json TEXT NOT NULL DEFAULT '{}',
  blocked_reasons_json TEXT NOT NULL DEFAULT '[]',
  requires_user_grant INTEGER NOT NULL DEFAULT 1,
  unattended_allowed INTEGER NOT NULL DEFAULT 0,
  preview_hash TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_permission_previews_bundle
ON skill_permission_previews(bundle_id, created_at);

CREATE TABLE IF NOT EXISTS skill_grants (
  skill_grant_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  allowed_tools_json TEXT NOT NULL DEFAULT '[]',
  allowed_asset_actions_json TEXT NOT NULL DEFAULT '[]',
  allowed_mcp_tools_json TEXT NOT NULL DEFAULT '[]',
  denied_actions_json TEXT NOT NULL DEFAULT '[]',
  approval_policy_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  grant_scope TEXT NOT NULL DEFAULT 'explicit',
  created_by_member_id TEXT,
  revoked_by_member_id TEXT,
  revoke_reason TEXT,
  expires_at TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revoked_at TEXT,
  FOREIGN KEY(skill_id) REFERENCES skills(skill_id),
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_grants_skill_subject
ON skill_grants(skill_id, subject_type, subject_id, status);

CREATE TABLE IF NOT EXISTS skill_static_analysis_reports (
  analysis_report_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  bundle_revision TEXT,
  manifest_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  trust_level TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  blocked_reasons_json TEXT NOT NULL DEFAULT '[]',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  remediation_hints_json TEXT NOT NULL DEFAULT '[]',
  sensitive_findings_json TEXT NOT NULL DEFAULT '[]',
  manifest_summary_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skill_static_analysis_bundle
ON skill_static_analysis_reports(bundle_id, created_at);

CREATE TABLE IF NOT EXISTS skill_eval_bindings (
  binding_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  bundle_revision TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  eval_run_id TEXT NOT NULL,
  capability_scope_json TEXT NOT NULL DEFAULT '{}',
  risk_level TEXT NOT NULL,
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(skill_id) REFERENCES skills(skill_id),
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id),
  FOREIGN KEY(eval_run_id) REFERENCES skill_eval_runs(eval_run_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_eval_bindings_skill
ON skill_eval_bindings(skill_id, created_at);

CREATE TABLE IF NOT EXISTS skill_rollback_points (
  rollback_point_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  from_revision TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  skill_snapshot_json TEXT NOT NULL DEFAULT '{}',
  bundle_snapshot_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT,
  created_by_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(skill_id) REFERENCES skills(skill_id),
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_rollback_points_skill
ON skill_rollback_points(skill_id, created_at);

CREATE TABLE IF NOT EXISTS skill_output_taint_records (
  taint_record_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  skill_run_id TEXT,
  task_id TEXT,
  taint_source TEXT NOT NULL,
  output_hash TEXT NOT NULL,
  output_preview TEXT,
  untrusted_external_content INTEGER NOT NULL DEFAULT 1,
  dlp_findings_json TEXT NOT NULL DEFAULT '[]',
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  guard_decision TEXT NOT NULL,
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(skill_id) REFERENCES skills(skill_id),
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id),
  FOREIGN KEY(skill_run_id) REFERENCES skill_runs(skill_run_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_skill_output_taint_skill
ON skill_output_taint_records(skill_id, created_at);
CREATE INDEX IF NOT EXISTS idx_skill_output_taint_run
ON skill_output_taint_records(skill_run_id);
