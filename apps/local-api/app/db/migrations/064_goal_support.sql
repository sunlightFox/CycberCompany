CREATE TABLE IF NOT EXISTS goals (
  goal_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  owner_member_id TEXT NOT NULL,
  conversation_id TEXT,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  domain_label TEXT NOT NULL DEFAULT 'general',
  status TEXT NOT NULL,
  success_criteria_json TEXT NOT NULL DEFAULT '[]',
  constraints_json TEXT NOT NULL DEFAULT '{}',
  motivation_json TEXT NOT NULL DEFAULT '{}',
  active_plan_id TEXT,
  created_from_turn_id TEXT,
  trace_id TEXT,
  archived_at TEXT,
  cancelled_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(owner_member_id) REFERENCES members(member_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_goals_owner_status
ON goals(owner_member_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_goals_conversation_status
ON goals(conversation_id, status, created_at);

CREATE TABLE IF NOT EXISTS goal_plans (
  goal_plan_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  assumptions_json TEXT NOT NULL DEFAULT '[]',
  risk_notes_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_plans_goal_status
ON goal_plans(goal_id, status, version);

CREATE TABLE IF NOT EXISTS goal_plan_items (
  goal_plan_item_id TEXT PRIMARY KEY,
  goal_plan_id TEXT NOT NULL,
  goal_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  item_type TEXT NOT NULL DEFAULT 'routine',
  cadence_json TEXT NOT NULL DEFAULT '{}',
  success_metric_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'planned',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(goal_plan_id) REFERENCES goal_plans(goal_plan_id),
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_plan_items_plan_order
ON goal_plan_items(goal_plan_id, sort_order);

CREATE TABLE IF NOT EXISTS goal_supervision_policies (
  policy_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  status TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'scheduled_checkin',
  frequency_json TEXT NOT NULL DEFAULT '{}',
  quiet_hours_json TEXT NOT NULL DEFAULT '{}',
  tone_policy_json TEXT NOT NULL DEFAULT '{}',
  next_checkin_at TEXT,
  scheduled_task_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id),
  FOREIGN KEY(scheduled_task_id) REFERENCES scheduled_tasks(scheduled_task_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_supervision_goal_status
ON goal_supervision_policies(goal_id, status);

CREATE TABLE IF NOT EXISTS goal_checkins (
  checkin_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  policy_id TEXT,
  scheduled_task_id TEXT,
  scheduled_run_id TEXT,
  prompt_text TEXT NOT NULL,
  user_reply_text_redacted TEXT,
  parsed_status TEXT NOT NULL DEFAULT 'pending',
  progress_delta_json TEXT NOT NULL DEFAULT '{}',
  advice_json TEXT NOT NULL DEFAULT '{}',
  encouragement_text TEXT NOT NULL DEFAULT '',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  replied_at TEXT,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id),
  FOREIGN KEY(policy_id) REFERENCES goal_supervision_policies(policy_id),
  FOREIGN KEY(scheduled_task_id) REFERENCES scheduled_tasks(scheduled_task_id),
  FOREIGN KEY(scheduled_run_id) REFERENCES scheduled_task_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_checkins_goal_time
ON goal_checkins(goal_id, created_at);

CREATE INDEX IF NOT EXISTS idx_goal_checkins_pending
ON goal_checkins(goal_id, parsed_status, created_at);

CREATE TABLE IF NOT EXISTS goal_progress_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  progress_percent INTEGER NOT NULL DEFAULT 0,
  completed_count INTEGER NOT NULL DEFAULT 0,
  partial_count INTEGER NOT NULL DEFAULT 0,
  missed_count INTEGER NOT NULL DEFAULT 0,
  blocked_count INTEGER NOT NULL DEFAULT 0,
  streak_days INTEGER NOT NULL DEFAULT 0,
  summary TEXT NOT NULL,
  blockers_json TEXT NOT NULL DEFAULT '[]',
  next_focus_json TEXT NOT NULL DEFAULT '[]',
  source_checkin_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id),
  FOREIGN KEY(source_checkin_id) REFERENCES goal_checkins(checkin_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_progress_goal_time
ON goal_progress_snapshots(goal_id, created_at);

CREATE TABLE IF NOT EXISTS goal_events (
  event_id TEXT PRIMARY KEY,
  goal_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(goal_id) REFERENCES goals(goal_id)
);

CREATE INDEX IF NOT EXISTS idx_goal_events_goal_time
ON goal_events(goal_id, created_at);
