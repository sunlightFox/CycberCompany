CREATE TABLE IF NOT EXISTS browser_profiles (
  browser_profile_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  profile_type TEXT NOT NULL,
  storage_backend TEXT NOT NULL,
  status TEXT NOT NULL,
  sensitivity TEXT NOT NULL DEFAULT 'medium',
  allowed_domains_json TEXT NOT NULL DEFAULT '[]',
  blocked_domains_json TEXT NOT NULL DEFAULT '[]',
  policy_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_by_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revoked_at TEXT,
  cleared_at TEXT,
  expires_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_browser_profiles_org_status
ON browser_profiles(organization_id, status, created_at);

CREATE TABLE IF NOT EXISTS browser_sessions (
  browser_session_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  browser_profile_id TEXT NOT NULL,
  asset_id TEXT,
  login_domain TEXT NOT NULL,
  auth_type TEXT NOT NULL,
  status TEXT NOT NULL,
  sensitivity TEXT NOT NULL DEFAULT 'high',
  session_metadata_json TEXT NOT NULL DEFAULT '{}',
  secret_ref TEXT,
  created_by_member_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT,
  expires_at TEXT,
  revoked_at TEXT,
  FOREIGN KEY(browser_profile_id) REFERENCES browser_profiles(browser_profile_id),
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_sessions_profile_status
ON browser_sessions(browser_profile_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_browser_sessions_asset
ON browser_sessions(asset_id, status);

CREATE TABLE IF NOT EXISTS browser_profile_events (
  event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  browser_profile_id TEXT NOT NULL,
  browser_session_id TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(browser_profile_id) REFERENCES browser_profiles(browser_profile_id),
  FOREIGN KEY(browser_session_id) REFERENCES browser_sessions(browser_session_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_profile_events_profile
ON browser_profile_events(browser_profile_id, created_at);

CREATE TABLE IF NOT EXISTS browser_evidence (
  browser_evidence_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT,
  tool_call_id TEXT,
  browser_profile_id TEXT,
  browser_session_id TEXT,
  action TEXT NOT NULL,
  action_status TEXT NOT NULL,
  url TEXT,
  title TEXT,
  http_status INTEGER,
  evidence_summary TEXT NOT NULL,
  snapshot_preview TEXT,
  screenshot_artifact_id TEXT,
  download_artifact_id TEXT,
  artifact_ids_json TEXT NOT NULL DEFAULT '[]',
  network_summary_json TEXT NOT NULL DEFAULT '{}',
  console_summary_json TEXT NOT NULL DEFAULT '{}',
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  safety_decision_json TEXT NOT NULL DEFAULT '{}',
  untrusted_external_content INTEGER NOT NULL DEFAULT 1,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id),
  FOREIGN KEY(browser_profile_id) REFERENCES browser_profiles(browser_profile_id),
  FOREIGN KEY(browser_session_id) REFERENCES browser_sessions(browser_session_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_evidence_task
ON browser_evidence(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_browser_evidence_tool_call
ON browser_evidence(tool_call_id);
CREATE INDEX IF NOT EXISTS idx_browser_evidence_profile
ON browser_evidence(browser_profile_id, created_at);

CREATE TABLE IF NOT EXISTS browser_network_events (
  network_event_id TEXT PRIMARY KEY,
  browser_evidence_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  request_url TEXT NOT NULL,
  method TEXT NOT NULL DEFAULT 'GET',
  status_code INTEGER,
  resource_type TEXT,
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(browser_evidence_id) REFERENCES browser_evidence(browser_evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_network_events_evidence
ON browser_network_events(browser_evidence_id, created_at);

CREATE TABLE IF NOT EXISTS browser_console_events (
  console_event_id TEXT PRIMARY KEY,
  browser_evidence_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  level TEXT NOT NULL,
  message_preview TEXT NOT NULL,
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(browser_evidence_id) REFERENCES browser_evidence(browser_evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_console_events_evidence
ON browser_console_events(browser_evidence_id, created_at);
