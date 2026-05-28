CREATE TABLE IF NOT EXISTS goal_intakes (
  intake_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  domain_label TEXT NOT NULL DEFAULT 'general',
  status TEXT NOT NULL DEFAULT 'collecting',
  current_level TEXT,
  target_level TEXT,
  target_date TEXT,
  available_time_json TEXT NOT NULL DEFAULT '{}',
  constraints_json TEXT NOT NULL DEFAULT '{}',
  motivation_json TEXT NOT NULL DEFAULT '{}',
  missing_fields_json TEXT NOT NULL DEFAULT '[]',
  raw_answers_json TEXT NOT NULL DEFAULT '{}',
  confirmed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_intakes_goal
ON goal_intakes(goal_id, created_at);

CREATE TABLE IF NOT EXISTS goal_milestones (
  milestone_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  goal_plan_id TEXT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planned',
  target_date TEXT,
  acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id),
  FOREIGN KEY(goal_plan_id) REFERENCES goal_plans(goal_plan_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_milestones_goal_order
ON goal_milestones(goal_id, sort_order);

CREATE TABLE IF NOT EXISTS goal_routines (
  routine_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  goal_plan_id TEXT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  cadence_json TEXT NOT NULL DEFAULT '{}',
  estimated_minutes INTEGER,
  difficulty TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'active',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id),
  FOREIGN KEY(goal_plan_id) REFERENCES goal_plans(goal_plan_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_routines_goal_status
ON goal_routines(goal_id, status, sort_order);

CREATE TABLE IF NOT EXISTS goal_interventions (
  intervention_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  trigger_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'suggested',
  summary TEXT NOT NULL,
  suggestion_json TEXT NOT NULL DEFAULT '{}',
  shown_at TEXT,
  user_feedback_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_interventions_goal_time
ON goal_interventions(goal_id, created_at);

CREATE TABLE IF NOT EXISTS goal_model_calls (
  model_call_id TEXT PRIMARY KEY,
  goal_id TEXT,
  call_type TEXT NOT NULL,
  status TEXT NOT NULL,
  model_route_json TEXT NOT NULL DEFAULT '{}',
  input_redacted_json TEXT NOT NULL DEFAULT '{}',
  output_redacted_json TEXT NOT NULL DEFAULT '{}',
  fallback_reason TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_model_calls_goal_time
ON goal_model_calls(goal_id, created_at);
