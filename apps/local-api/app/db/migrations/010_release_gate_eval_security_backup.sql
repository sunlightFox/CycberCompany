CREATE TABLE IF NOT EXISTS release_gates (
  release_gate_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  status TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  required_checks_json TEXT NOT NULL DEFAULT '[]',
  summary_json TEXT NOT NULL DEFAULT '{}',
  blocker_count INTEGER NOT NULL DEFAULT 0,
  high_count INTEGER NOT NULL DEFAULT 0,
  medium_count INTEGER NOT NULL DEFAULT 0,
  low_count INTEGER NOT NULL DEFAULT 0,
  created_by_member_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_release_gates_org_status
ON release_gates(organization_id, status, created_at);

CREATE TABLE IF NOT EXISTS release_evidence (
  evidence_id TEXT PRIMARY KEY,
  release_gate_id TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  checksum TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(release_gate_id) REFERENCES release_gates(release_gate_id)
);

CREATE INDEX IF NOT EXISTS idx_release_evidence_gate_type
ON release_evidence(release_gate_id, evidence_type, status);

CREATE TABLE IF NOT EXISTS release_findings (
  finding_id TEXT PRIMARY KEY,
  release_gate_id TEXT NOT NULL,
  severity TEXT NOT NULL,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  affected_module TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  owner TEXT,
  accepted_reason TEXT,
  accepted_until TEXT,
  verification_run_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(release_gate_id) REFERENCES release_gates(release_gate_id)
);

CREATE INDEX IF NOT EXISTS idx_release_findings_gate_severity
ON release_findings(release_gate_id, severity, status);

CREATE TABLE IF NOT EXISTS eval_suites (
  suite_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT,
  required INTEGER NOT NULL DEFAULT 1,
  threshold_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eval_suites_category_status
ON eval_suites(category, status);

CREATE TABLE IF NOT EXISTS eval_cases (
  case_id TEXT PRIMARY KEY,
  suite_id TEXT NOT NULL,
  case_key TEXT NOT NULL,
  title TEXT NOT NULL,
  input_json TEXT NOT NULL DEFAULT '{}',
  expected_json TEXT NOT NULL DEFAULT '{}',
  tags_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(suite_id) REFERENCES eval_suites(suite_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_cases_suite_key
ON eval_cases(suite_id, case_key);

CREATE TABLE IF NOT EXISTS eval_runs (
  eval_run_id TEXT PRIMARY KEY,
  release_gate_id TEXT,
  suite_id TEXT,
  status TEXT NOT NULL,
  total_cases INTEGER NOT NULL DEFAULT 0,
  passed_cases INTEGER NOT NULL DEFAULT 0,
  failed_cases INTEGER NOT NULL DEFAULT 0,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  summary_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_summary TEXT,
  trace_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(release_gate_id) REFERENCES release_gates(release_gate_id),
  FOREIGN KEY(suite_id) REFERENCES eval_suites(suite_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_runs_gate_status
ON eval_runs(release_gate_id, status, created_at);

CREATE TABLE IF NOT EXISTS eval_results (
  eval_result_id TEXT PRIMARY KEY,
  eval_run_id TEXT NOT NULL,
  suite_id TEXT NOT NULL,
  case_id TEXT,
  case_key TEXT NOT NULL,
  status TEXT NOT NULL,
  score REAL NOT NULL DEFAULT 0,
  expected_json TEXT NOT NULL DEFAULT '{}',
  actual_json TEXT NOT NULL DEFAULT '{}',
  assertion_summary TEXT,
  finding_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(eval_run_id) REFERENCES eval_runs(eval_run_id),
  FOREIGN KEY(suite_id) REFERENCES eval_suites(suite_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run_status
ON eval_results(eval_run_id, status);

CREATE TABLE IF NOT EXISTS red_team_scenarios (
  scenario_id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  attack_input_json TEXT NOT NULL DEFAULT '{}',
  expected_block_json TEXT NOT NULL DEFAULT '{}',
  severity_if_failed TEXT NOT NULL,
  tags_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_red_team_scenarios_category_status
ON red_team_scenarios(category, status);

CREATE TABLE IF NOT EXISTS security_audit_runs (
  audit_run_id TEXT PRIMARY KEY,
  release_gate_id TEXT,
  status TEXT NOT NULL,
  total_scenarios INTEGER NOT NULL DEFAULT 0,
  passed_scenarios INTEGER NOT NULL DEFAULT 0,
  failed_scenarios INTEGER NOT NULL DEFAULT 0,
  critical_failures INTEGER NOT NULL DEFAULT 0,
  high_failures INTEGER NOT NULL DEFAULT 0,
  result_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(release_gate_id) REFERENCES release_gates(release_gate_id)
);

CREATE INDEX IF NOT EXISTS idx_security_audit_runs_gate
ON security_audit_runs(release_gate_id, created_at);

CREATE TABLE IF NOT EXISTS integrity_check_runs (
  integrity_run_id TEXT PRIMARY KEY,
  release_gate_id TEXT,
  check_type TEXT NOT NULL,
  status TEXT NOT NULL,
  checked_count INTEGER NOT NULL DEFAULT 0,
  failed_count INTEGER NOT NULL DEFAULT 0,
  threshold_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(release_gate_id) REFERENCES release_gates(release_gate_id)
);

CREATE INDEX IF NOT EXISTS idx_integrity_check_runs_gate_type
ON integrity_check_runs(release_gate_id, check_type, status);

CREATE TABLE IF NOT EXISTS backup_jobs (
  backup_job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  status TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  output_uri TEXT,
  manifest_json TEXT NOT NULL DEFAULT '{}',
  checksum TEXT,
  size_bytes INTEGER,
  error_code TEXT,
  error_summary TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_backup_jobs_org_status
ON backup_jobs(organization_id, status, created_at);

CREATE TABLE IF NOT EXISTS restore_jobs (
  restore_job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  backup_job_id TEXT,
  status TEXT NOT NULL,
  input_uri TEXT NOT NULL,
  restore_plan_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  checksum_verified INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  error_summary TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(backup_job_id) REFERENCES backup_jobs(backup_job_id)
);

CREATE INDEX IF NOT EXISTS idx_restore_jobs_org_status
ON restore_jobs(organization_id, status, created_at);

CREATE TABLE IF NOT EXISTS benchmark_runs (
  benchmark_run_id TEXT PRIMARY KEY,
  release_gate_id TEXT,
  benchmark_type TEXT NOT NULL,
  status TEXT NOT NULL,
  scenario_json TEXT NOT NULL DEFAULT '{}',
  metrics_json TEXT NOT NULL DEFAULT '{}',
  resource_summary_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(release_gate_id) REFERENCES release_gates(release_gate_id)
);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_gate_type
ON benchmark_runs(release_gate_id, benchmark_type, status);

CREATE TABLE IF NOT EXISTS diagnostic_bundles (
  bundle_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  redaction_policy_json TEXT NOT NULL DEFAULT '{}',
  output_uri TEXT,
  checksum TEXT,
  size_bytes INTEGER,
  status TEXT NOT NULL,
  created_by_member_id TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_diagnostic_bundles_org_status
ON diagnostic_bundles(organization_id, status, created_at);

CREATE TABLE IF NOT EXISTS release_reports (
  report_id TEXT PRIMARY KEY,
  release_gate_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  evidence_summary_json TEXT NOT NULL DEFAULT '{}',
  findings_summary_json TEXT NOT NULL DEFAULT '{}',
  output_uri TEXT,
  checksum TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(release_gate_id) REFERENCES release_gates(release_gate_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_release_reports_gate
ON release_reports(release_gate_id);
