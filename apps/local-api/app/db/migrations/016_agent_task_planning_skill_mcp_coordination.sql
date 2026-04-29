CREATE TABLE IF NOT EXISTS task_planner_decisions (
  planner_decision_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  planner_type TEXT NOT NULL,
  selected_mode TEXT NOT NULL,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  capability_snapshot_json TEXT NOT NULL DEFAULT '{}',
  skill_match_refs_json TEXT NOT NULL DEFAULT '[]',
  mcp_tool_refs_json TEXT NOT NULL DEFAULT '[]',
  model_hint_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_planner_decisions_task
ON task_planner_decisions(task_id, created_at);

CREATE TABLE IF NOT EXISTS agent_loop_iterations (
  iteration_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  loop_index INTEGER NOT NULL,
  observation_id TEXT,
  observation_summary TEXT,
  plan_delta_json TEXT NOT NULL DEFAULT '{}',
  selected_action_json TEXT NOT NULL DEFAULT '{}',
  tool_call_refs_json TEXT NOT NULL DEFAULT '[]',
  safety_decision_refs_json TEXT NOT NULL DEFAULT '[]',
  evaluation_result_json TEXT NOT NULL DEFAULT '{}',
  next_step_key TEXT,
  stop_reason TEXT,
  budget_snapshot_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_loop_iterations_task_index
ON agent_loop_iterations(task_id, loop_index);

CREATE TABLE IF NOT EXISTS task_observations (
  observation_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  step_id TEXT,
  source_type TEXT NOT NULL,
  source_ref_json TEXT NOT NULL DEFAULT '{}',
  trusted_level TEXT NOT NULL,
  summary TEXT NOT NULL,
  key_facts_json TEXT NOT NULL DEFAULT '[]',
  errors_json TEXT NOT NULL DEFAULT '[]',
  artifact_refs_json TEXT NOT NULL DEFAULT '[]',
  sensitivity TEXT NOT NULL DEFAULT 'low',
  untrusted_instructions_detected INTEGER NOT NULL DEFAULT 0,
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(step_id) REFERENCES task_steps(step_id)
);

CREATE INDEX IF NOT EXISTS idx_task_observations_task
ON task_observations(task_id, created_at);

CREATE TABLE IF NOT EXISTS task_retry_plans (
  retry_plan_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  suggested_actions_json TEXT NOT NULL DEFAULT '[]',
  resumable_from_step_key TEXT,
  budget_delta_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_retry_plans_task
ON task_retry_plans(task_id, created_at);

CREATE TABLE IF NOT EXISTS task_reflection_candidates (
  candidate_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  candidate_type TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 0,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  risk_level TEXT NOT NULL DEFAULT 'R1',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_task_reflection_candidates_task
ON task_reflection_candidates(task_id, candidate_type, status);
