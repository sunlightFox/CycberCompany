CREATE TABLE IF NOT EXISTS task_closure_records (
  closure_record_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  release_gate_id TEXT,
  source_eval_run_id TEXT,
  domain TEXT NOT NULL,
  task_tier TEXT NOT NULL,
  delivery_status TEXT NOT NULL,
  delivery_blockers_json TEXT NOT NULL DEFAULT '[]',
  handoff_reason TEXT,
  approval_interruption INTEGER NOT NULL DEFAULT 0,
  recovery_summary_json TEXT NOT NULL DEFAULT '{}',
  verification_status TEXT NOT NULL,
  once_success INTEGER NOT NULL DEFAULT 0,
  final_deliverable INTEGER NOT NULL DEFAULT 0,
  human_handoff INTEGER NOT NULL DEFAULT 0,
  error_recovered INTEGER NOT NULL DEFAULT 0,
  round_count INTEGER NOT NULL DEFAULT 0,
  tool_call_count INTEGER NOT NULL DEFAULT 0,
  replan_count INTEGER NOT NULL DEFAULT 0,
  stop_reason TEXT,
  untrusted_observation_triggered INTEGER NOT NULL DEFAULT 0,
  residual_risk_present INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(release_gate_id) REFERENCES release_gates(release_gate_id),
  FOREIGN KEY(source_eval_run_id) REFERENCES eval_runs(eval_run_id)
);

CREATE INDEX IF NOT EXISTS idx_task_closure_records_gate_domain
ON task_closure_records(release_gate_id, domain, created_at);

CREATE INDEX IF NOT EXISTS idx_task_closure_records_task
ON task_closure_records(task_id, domain, created_at);
