ALTER TABLE browser_profiles ADD COLUMN health_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE browser_profiles ADD COLUMN last_probe_at TEXT;
ALTER TABLE browser_profiles ADD COLUMN recovery_hint TEXT;
ALTER TABLE browser_profiles ADD COLUMN reuse_policy_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE browser_sessions ADD COLUMN health_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE browser_sessions ADD COLUMN login_state TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE browser_sessions ADD COLUMN last_probe_at TEXT;
ALTER TABLE browser_sessions ADD COLUMN invalidation_reason TEXT;
ALTER TABLE browser_sessions ADD COLUMN recovery_hint TEXT;
ALTER TABLE browser_sessions ADD COLUMN reuse_policy_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE browser_sessions ADD COLUMN restore_context_ref TEXT;

CREATE TABLE IF NOT EXISTS browser_session_health_probes (
  probe_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  browser_profile_id TEXT NOT NULL,
  browser_session_id TEXT NOT NULL,
  probe_type TEXT NOT NULL,
  health_status TEXT NOT NULL,
  login_state TEXT NOT NULL,
  provider_status TEXT,
  failure_reason TEXT,
  recovery_hint TEXT,
  evidence_redacted_json TEXT NOT NULL DEFAULT '{}',
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  probed_at TEXT NOT NULL,
  FOREIGN KEY(browser_profile_id) REFERENCES browser_profiles(browser_profile_id),
  FOREIGN KEY(browser_session_id) REFERENCES browser_sessions(browser_session_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_session_health_probes_session
ON browser_session_health_probes(browser_session_id, probed_at);

CREATE INDEX IF NOT EXISTS idx_browser_session_health_probes_status
ON browser_session_health_probes(health_status, probed_at);

CREATE TABLE IF NOT EXISTS browser_page_states (
  page_state_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  task_id TEXT,
  tool_call_id TEXT,
  browser_profile_id TEXT,
  browser_session_id TEXT,
  browser_evidence_id TEXT,
  page_key TEXT NOT NULL,
  action TEXT NOT NULL,
  action_status TEXT NOT NULL,
  current_url TEXT,
  title TEXT,
  http_status INTEGER,
  dom_summary_json TEXT NOT NULL DEFAULT '{}',
  network_summary_json TEXT NOT NULL DEFAULT '{}',
  console_summary_json TEXT NOT NULL DEFAULT '{}',
  task_checkpoint_json TEXT NOT NULL DEFAULT '{}',
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(tool_call_id) REFERENCES tool_calls(tool_call_id),
  FOREIGN KEY(browser_profile_id) REFERENCES browser_profiles(browser_profile_id),
  FOREIGN KEY(browser_session_id) REFERENCES browser_sessions(browser_session_id),
  FOREIGN KEY(browser_evidence_id) REFERENCES browser_evidence(browser_evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_browser_page_states_session
ON browser_page_states(browser_session_id, created_at);

CREATE INDEX IF NOT EXISTS idx_browser_page_states_task
ON browser_page_states(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_browser_page_states_page_key
ON browser_page_states(page_key, created_at);
