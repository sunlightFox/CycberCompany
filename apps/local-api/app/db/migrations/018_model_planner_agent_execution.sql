CREATE TABLE IF NOT EXISTS model_plan_candidates (
  candidate_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  planner_type TEXT NOT NULL,
  source TEXT NOT NULL,
  recommended_mode TEXT NOT NULL,
  steps_json TEXT NOT NULL DEFAULT '[]',
  success_criteria_json TEXT NOT NULL DEFAULT '[]',
  assumptions_json TEXT NOT NULL DEFAULT '[]',
  missing_information_json TEXT NOT NULL DEFAULT '[]',
  risk_hints_json TEXT NOT NULL DEFAULT '[]',
  required_capabilities_json TEXT NOT NULL DEFAULT '[]',
  required_assets_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0,
  reasoning_summary TEXT NOT NULL,
  status TEXT NOT NULL,
  model_assist_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_model_plan_candidates_task
ON model_plan_candidates(task_id, created_at);

CREATE TABLE IF NOT EXISTS plan_verification_results (
  verification_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  schema_valid INTEGER NOT NULL DEFAULT 0,
  mode_allowed INTEGER NOT NULL DEFAULT 0,
  step_type_allowed INTEGER NOT NULL DEFAULT 0,
  capability_available INTEGER NOT NULL DEFAULT 0,
  asset_handle_allowed INTEGER NOT NULL DEFAULT 0,
  risk_level_acceptable INTEGER NOT NULL DEFAULT 0,
  approval_strategy_present INTEGER NOT NULL DEFAULT 0,
  budget_within_limit INTEGER NOT NULL DEFAULT 0,
  no_direct_secret INTEGER NOT NULL DEFAULT 0,
  no_direct_shell_command_from_model INTEGER NOT NULL DEFAULT 0,
  issues_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(candidate_id) REFERENCES model_plan_candidates(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_plan_verification_results_task
ON plan_verification_results(task_id, candidate_id, created_at);

CREATE TABLE IF NOT EXISTS plan_policy_prunes (
  prune_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  prune_type TEXT NOT NULL,
  original_step_json TEXT NOT NULL DEFAULT '{}',
  pruned_step_json TEXT NOT NULL DEFAULT '{}',
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(candidate_id) REFERENCES model_plan_candidates(candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_plan_policy_prunes_task
ON plan_policy_prunes(task_id, prune_type, created_at);

CREATE TABLE IF NOT EXISTS planner_capability_candidates (
  capability_candidate_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  capability_type TEXT NOT NULL,
  capability_id TEXT,
  name TEXT,
  match_score REAL NOT NULL DEFAULT 0,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  policy_status TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_planner_capability_candidates_task
ON planner_capability_candidates(task_id, capability_type, policy_status);

CREATE TABLE IF NOT EXISTS agent_next_action_decisions (
  decision_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  iteration_id TEXT,
  loop_index INTEGER NOT NULL,
  next_action_type TEXT NOT NULL,
  selected_step_id TEXT,
  selected_step_key TEXT,
  plan_delta_json TEXT NOT NULL DEFAULT '{}',
  needs_user_input INTEGER NOT NULL DEFAULT 0,
  needs_approval INTEGER NOT NULL DEFAULT 0,
  stop_reason TEXT,
  confidence REAL NOT NULL DEFAULT 0,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  budget_snapshot_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(iteration_id) REFERENCES agent_loop_iterations(iteration_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_next_action_decisions_task
ON agent_next_action_decisions(task_id, loop_index);

CREATE TABLE IF NOT EXISTS tool_failure_recovery_plans (
  recovery_plan_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  step_id TEXT,
  tool_call_id TEXT,
  failure_type TEXT NOT NULL,
  recovery_action TEXT NOT NULL,
  suggested_actions_json TEXT NOT NULL DEFAULT '[]',
  retry_allowed INTEGER NOT NULL DEFAULT 0,
  bypass_controls INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(step_id) REFERENCES task_steps(step_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_failure_recovery_plans_task
ON tool_failure_recovery_plans(task_id, failure_type, created_at);
