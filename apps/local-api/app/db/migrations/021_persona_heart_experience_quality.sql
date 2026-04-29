CREATE TABLE IF NOT EXISTS persona_consistency_profiles (
  consistency_profile_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  persona_profile_id TEXT NOT NULL,
  member_id TEXT,
  style_principles_json TEXT NOT NULL DEFAULT '[]',
  forbidden_claims_json TEXT NOT NULL DEFAULT '[]',
  mode_switch_rules_json TEXT NOT NULL DEFAULT '[]',
  consistency_markers_json TEXT NOT NULL DEFAULT '[]',
  disabled_patterns_json TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT 'phase22_default',
  status TEXT NOT NULL DEFAULT 'active',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(persona_profile_id) REFERENCES persona_profiles(persona_profile_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_persona_consistency_profiles_profile
ON persona_consistency_profiles(persona_profile_id);

CREATE INDEX IF NOT EXISTS idx_persona_consistency_profiles_member
ON persona_consistency_profiles(member_id, status);

CREATE TABLE IF NOT EXISTS heart_state_transitions (
  transition_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  previous_snapshot_id TEXT,
  current_snapshot_id TEXT NOT NULL,
  source_turn_id TEXT,
  transition_factors_json TEXT NOT NULL DEFAULT '[]',
  state_delta_json TEXT NOT NULL DEFAULT '{}',
  confidence REAL NOT NULL DEFAULT 0.6,
  status TEXT NOT NULL DEFAULT 'active',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(previous_snapshot_id) REFERENCES heart_state_snapshots(snapshot_id),
  FOREIGN KEY(current_snapshot_id) REFERENCES heart_state_snapshots(snapshot_id),
  FOREIGN KEY(source_turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_heart_state_transitions_member
ON heart_state_transitions(member_id, created_at);

CREATE INDEX IF NOT EXISTS idx_heart_state_transitions_turn
ON heart_state_transitions(source_turn_id);

CREATE TABLE IF NOT EXISTS tone_policy_resolutions (
  resolution_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  turn_id TEXT,
  member_id TEXT,
  persona_profile_id TEXT,
  heart_snapshot_id TEXT,
  scenario TEXT NOT NULL,
  risk_level TEXT NOT NULL DEFAULT 'R1',
  tone_mode TEXT NOT NULL,
  conciseness REAL NOT NULL DEFAULT 0.72,
  warmth REAL NOT NULL DEFAULT 0.68,
  directness REAL NOT NULL DEFAULT 0.78,
  technical_depth REAL NOT NULL DEFAULT 0.66,
  anthropomorphic_level REAL NOT NULL DEFAULT 0.35,
  disclosure_required INTEGER NOT NULL DEFAULT 0,
  safety_notice_required INTEGER NOT NULL DEFAULT 0,
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(persona_profile_id) REFERENCES persona_profiles(persona_profile_id),
  FOREIGN KEY(heart_snapshot_id) REFERENCES heart_state_snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_tone_policy_resolutions_turn
ON tone_policy_resolutions(turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_tone_policy_resolutions_mode
ON tone_policy_resolutions(tone_mode, risk_level, created_at);

CREATE TABLE IF NOT EXISTS response_quality_evaluations (
  evaluation_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  turn_id TEXT,
  response_plan_json TEXT NOT NULL DEFAULT '{}',
  rubric_json TEXT NOT NULL DEFAULT '{}',
  quality_markers_json TEXT NOT NULL DEFAULT '{}',
  violations_json TEXT NOT NULL DEFAULT '[]',
  score REAL NOT NULL DEFAULT 0.0,
  passed INTEGER NOT NULL DEFAULT 0,
  internal_leakage_count INTEGER NOT NULL DEFAULT 0,
  high_risk_boundary_violation_count INTEGER NOT NULL DEFAULT 0,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_response_quality_evaluations_turn
ON response_quality_evaluations(turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_response_quality_evaluations_passed
ON response_quality_evaluations(passed, created_at);

CREATE TABLE IF NOT EXISTS persona_heart_replay_runs (
  run_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  suite_id TEXT NOT NULL,
  case_key TEXT NOT NULL,
  status TEXT NOT NULL,
  turn_count INTEGER NOT NULL DEFAULT 0,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  violation_counts_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_persona_heart_replay_runs_case
ON persona_heart_replay_runs(case_key, status, created_at);
