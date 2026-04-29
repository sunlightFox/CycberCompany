ALTER TABLE tasks ADD COLUMN parent_task_id TEXT;
ALTER TABLE tasks ADD COLUMN host_member_id TEXT;
ALTER TABLE tasks ADD COLUMN collaboration_plan_id TEXT;
ALTER TABLE tasks ADD COLUMN supervisor_mode TEXT;

ALTER TABLE task_steps ADD COLUMN subtask_id TEXT;
ALTER TABLE task_steps ADD COLUMN participant_id TEXT;
ALTER TABLE task_steps ADD COLUMN assigned_member_id TEXT;

ALTER TABLE conversations ADD COLUMN host_member_id TEXT;
ALTER TABLE conversations ADD COLUMN participant_policy_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS task_participants (
  participant_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  role_in_task TEXT NOT NULL,
  participant_type TEXT NOT NULL,
  status TEXT NOT NULL,
  selection_reason TEXT NOT NULL,
  context_scope_json TEXT NOT NULL DEFAULT '{}',
  allowed_skills_json TEXT NOT NULL DEFAULT '[]',
  allowed_mcp_tools_json TEXT NOT NULL DEFAULT '[]',
  capability_decision_id TEXT,
  output_summary_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_summary TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  removed_at TEXT,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_task_participants_task
ON task_participants(task_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_participants_task_member
ON task_participants(task_id, member_id)
WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS task_subtasks (
  subtask_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  parent_task_id TEXT NOT NULL,
  participant_id TEXT NOT NULL,
  assigned_member_id TEXT NOT NULL,
  title TEXT NOT NULL,
  objective TEXT NOT NULL,
  status TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  context_scope_json TEXT NOT NULL DEFAULT '{}',
  allowed_skills_json TEXT NOT NULL DEFAULT '[]',
  allowed_mcp_tools_json TEXT NOT NULL DEFAULT '[]',
  output_summary_json TEXT NOT NULL DEFAULT '{}',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  error_code TEXT,
  error_summary TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(parent_task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(participant_id) REFERENCES task_participants(participant_id),
  FOREIGN KEY(assigned_member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_task_subtasks_parent_status
ON task_subtasks(parent_task_id, status);
CREATE INDEX IF NOT EXISTS idx_task_subtasks_member
ON task_subtasks(assigned_member_id, status);

CREATE TABLE IF NOT EXISTS collaboration_plans (
  collaboration_plan_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  host_member_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  max_rounds INTEGER NOT NULL DEFAULT 4,
  participant_policy_json TEXT NOT NULL DEFAULT '{}',
  success_criteria_json TEXT NOT NULL DEFAULT '[]',
  risk_summary_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(host_member_id) REFERENCES members(member_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_collaboration_plans_task
ON collaboration_plans(task_id);

CREATE TABLE IF NOT EXISTS collaboration_rounds (
  round_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  collaboration_plan_id TEXT NOT NULL,
  round_index INTEGER NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  participant_ids_json TEXT NOT NULL DEFAULT '[]',
  max_turns INTEGER NOT NULL DEFAULT 1,
  max_outputs INTEGER NOT NULL DEFAULT 10,
  prompt_summary TEXT,
  round_summary_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(collaboration_plan_id) REFERENCES collaboration_plans(collaboration_plan_id)
);

CREATE INDEX IF NOT EXISTS idx_collaboration_rounds_task
ON collaboration_rounds(task_id, round_index);

CREATE TABLE IF NOT EXISTS collaboration_outputs (
  output_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  collaboration_plan_id TEXT NOT NULL,
  round_id TEXT NOT NULL,
  subtask_id TEXT NOT NULL,
  participant_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  output_type TEXT NOT NULL,
  status TEXT NOT NULL,
  content_redacted TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  artifact_ids_json TEXT NOT NULL DEFAULT '[]',
  trace_id TEXT,
  error_code TEXT,
  error_summary TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(collaboration_plan_id) REFERENCES collaboration_plans(collaboration_plan_id),
  FOREIGN KEY(round_id) REFERENCES collaboration_rounds(round_id),
  FOREIGN KEY(subtask_id) REFERENCES task_subtasks(subtask_id),
  FOREIGN KEY(participant_id) REFERENCES task_participants(participant_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_collaboration_outputs_task
ON collaboration_outputs(task_id, created_at);

CREATE TABLE IF NOT EXISTS host_decisions (
  decision_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  collaboration_plan_id TEXT NOT NULL,
  host_member_id TEXT NOT NULL,
  decision_type TEXT NOT NULL,
  status TEXT NOT NULL,
  summary TEXT NOT NULL,
  rationale TEXT,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  payload_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(collaboration_plan_id) REFERENCES collaboration_plans(collaboration_plan_id),
  FOREIGN KEY(host_member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_host_decisions_task
ON host_decisions(task_id, created_at);

CREATE TABLE IF NOT EXISTS member_availability (
  member_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  status TEXT NOT NULL,
  capacity INTEGER NOT NULL DEFAULT 1,
  current_load INTEGER NOT NULL DEFAULT 0,
  unavailable_reason TEXT,
  schedule_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT 'shell_template',
  updated_at TEXT NOT NULL,
  FOREIGN KEY(member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_member_availability_org_status
ON member_availability(organization_id, status);

CREATE TABLE IF NOT EXISTS member_skill_policies (
  policy_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  allowed_skills_json TEXT NOT NULL DEFAULT '[]',
  denied_skills_json TEXT NOT NULL DEFAULT '[]',
  allowed_mcp_tools_json TEXT NOT NULL DEFAULT '[]',
  denied_mcp_tools_json TEXT NOT NULL DEFAULT '[]',
  risk_policy_json TEXT NOT NULL DEFAULT '{}',
  source TEXT NOT NULL DEFAULT 'shell_template',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_member_skill_policies_subject
ON member_skill_policies(organization_id, subject_type, subject_id);

CREATE TABLE IF NOT EXISTS shell_switch_events (
  event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  from_shell_id TEXT NOT NULL,
  to_shell_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  preview_json TEXT NOT NULL DEFAULT '{}',
  blocked_mutations_json TEXT NOT NULL DEFAULT '[]',
  business_values_unchanged INTEGER NOT NULL DEFAULT 1,
  actor_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_shell_switch_events_org_time
ON shell_switch_events(organization_id, created_at);

CREATE TABLE IF NOT EXISTS shell_template_applications (
  application_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  shell_id TEXT NOT NULL,
  template_type TEXT NOT NULL,
  template_key TEXT NOT NULL,
  object_type TEXT,
  object_id TEXT,
  status TEXT NOT NULL,
  result_json TEXT NOT NULL DEFAULT '{}',
  actor_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_shell_template_applications_template
ON shell_template_applications(organization_id, shell_id, template_type, template_key);
