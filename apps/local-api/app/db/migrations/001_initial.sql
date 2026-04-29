CREATE TABLE IF NOT EXISTS shells (
  shell_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  version TEXT NOT NULL,
  config_json TEXT NOT NULL,
  is_enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organizations (
  organization_id TEXT PRIMARY KEY,
  shell_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  owner_title TEXT NOT NULL,
  settings_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(shell_id) REFERENCES shells(shell_id)
);

CREATE TABLE IF NOT EXISTS departments (
  department_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  parent_department_id TEXT,
  key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_departments_org ON departments(organization_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_departments_org_key ON departments(organization_id, key);

CREATE TABLE IF NOT EXISTS roles (
  role_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  key TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  default_department_id TEXT,
  default_skills_json TEXT NOT NULL,
  authority_level INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(default_department_id) REFERENCES departments(department_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_roles_org_key ON roles(organization_id, key);

CREATE TABLE IF NOT EXISTS brains (
  brain_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  provider TEXT NOT NULL,
  endpoint TEXT,
  model_name TEXT NOT NULL,
  api_key_ref TEXT,
  is_local INTEGER NOT NULL DEFAULT 0,
  context_window INTEGER,
  supports_tools INTEGER NOT NULL DEFAULT 0,
  supports_vision INTEGER NOT NULL DEFAULT 0,
  supports_audio INTEGER NOT NULL DEFAULT 0,
  cost_policy_json TEXT NOT NULL,
  privacy_policy_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
  member_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  department_id TEXT,
  role_id TEXT,
  display_name TEXT NOT NULL,
  avatar_uri TEXT,
  status TEXT NOT NULL,
  default_brain_id TEXT,
  persona_profile_id TEXT NOT NULL,
  heart_profile_json TEXT NOT NULL,
  memory_policy_json TEXT NOT NULL,
  created_from_shell_id TEXT,
  created_from_template_id TEXT,
  metadata_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(department_id) REFERENCES departments(department_id),
  FOREIGN KEY(role_id) REFERENCES roles(role_id),
  FOREIGN KEY(default_brain_id) REFERENCES brains(brain_id)
);

CREATE INDEX IF NOT EXISTS idx_members_org ON members(organization_id);
CREATE INDEX IF NOT EXISTS idx_members_department ON members(department_id);

CREATE TABLE IF NOT EXISTS conversations (
  conversation_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  title TEXT,
  conversation_type TEXT NOT NULL,
  primary_member_id TEXT,
  participant_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(organization_id) REFERENCES organizations(organization_id),
  FOREIGN KEY(primary_member_id) REFERENCES members(member_id)
);

CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  turn_id TEXT,
  author_type TEXT NOT NULL,
  author_id TEXT,
  content_type TEXT NOT NULL,
  content_text TEXT,
  content_json TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
  ON messages(conversation_id, created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
  USING fts5(content_text, message_id UNINDEXED);

CREATE TABLE IF NOT EXISTS app_settings (
  setting_key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS traces (
  trace_id TEXT PRIMARY KEY,
  conversation_id TEXT,
  turn_id TEXT,
  task_id TEXT,
  root_span_id TEXT,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE TABLE IF NOT EXISTS trace_spans (
  span_id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,
  parent_span_id TEXT,
  span_type TEXT NOT NULL,
  name TEXT NOT NULL,
  input_json TEXT,
  output_json TEXT,
  metadata_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL,
  FOREIGN KEY(trace_id) REFERENCES traces(trace_id)
);

CREATE INDEX IF NOT EXISTS idx_trace_spans_trace ON trace_spans(trace_id);

CREATE TABLE IF NOT EXISTS audit_events (
  audit_id TEXT PRIMARY KEY,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT,
  risk_level TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_redacted_json TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_trace ON audit_events(trace_id);

