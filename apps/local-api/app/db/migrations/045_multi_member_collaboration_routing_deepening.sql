CREATE TABLE IF NOT EXISTS collaboration_routing_decisions (
  routing_decision_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  collaboration_plan_id TEXT,
  host_member_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  selected_member_ids_json TEXT NOT NULL DEFAULT '[]',
  rejected_candidates_json TEXT NOT NULL DEFAULT '[]',
  routing_factors_json TEXT NOT NULL DEFAULT '{}',
  risk_summary_json TEXT NOT NULL DEFAULT '{}',
  boundary_summary_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(collaboration_plan_id) REFERENCES collaboration_plans(collaboration_plan_id),
  FOREIGN KEY(host_member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_collaboration_routing_decisions_task
ON collaboration_routing_decisions(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_collaboration_routing_decisions_host
ON collaboration_routing_decisions(host_member_id, status, created_at);

CREATE TABLE IF NOT EXISTS collaboration_handoff_records (
  handoff_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  collaboration_plan_id TEXT,
  subtask_id TEXT NOT NULL,
  from_participant_id TEXT,
  from_member_id TEXT,
  to_participant_id TEXT,
  to_member_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL,
  context_summary_json TEXT NOT NULL DEFAULT '{}',
  boundary_summary_json TEXT NOT NULL DEFAULT '{}',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(collaboration_plan_id) REFERENCES collaboration_plans(collaboration_plan_id),
  FOREIGN KEY(subtask_id) REFERENCES task_subtasks(subtask_id),
  FOREIGN KEY(from_participant_id) REFERENCES task_participants(participant_id),
  FOREIGN KEY(to_participant_id) REFERENCES task_participants(participant_id),
  FOREIGN KEY(from_member_id) REFERENCES members(member_id),
  FOREIGN KEY(to_member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_collaboration_handoff_records_task
ON collaboration_handoff_records(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_collaboration_handoff_records_subtask
ON collaboration_handoff_records(subtask_id, created_at);

CREATE TABLE IF NOT EXISTS collaboration_context_boundaries (
  boundary_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  collaboration_plan_id TEXT,
  participant_id TEXT,
  member_id TEXT NOT NULL,
  context_scope_json TEXT NOT NULL DEFAULT '{}',
  allowed_context_json TEXT NOT NULL DEFAULT '[]',
  excluded_context_json TEXT NOT NULL DEFAULT '[]',
  asset_scope_json TEXT NOT NULL DEFAULT '[]',
  memory_scope TEXT NOT NULL DEFAULT 'member_private_only',
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(collaboration_plan_id) REFERENCES collaboration_plans(collaboration_plan_id),
  FOREIGN KEY(participant_id) REFERENCES task_participants(participant_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_collaboration_context_boundaries_task
ON collaboration_context_boundaries(task_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_collaboration_context_boundaries_participant
ON collaboration_context_boundaries(participant_id, created_at);

