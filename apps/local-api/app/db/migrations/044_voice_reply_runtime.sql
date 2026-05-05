CREATE TABLE IF NOT EXISTS voice_profiles (
  voice_profile_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  display_name TEXT NOT NULL,
  provider TEXT NOT NULL,
  provider_voice_id TEXT NOT NULL,
  output_format TEXT NOT NULL DEFAULT 'wav',
  sample_text TEXT,
  sample_audio_uri TEXT,
  config_json TEXT NOT NULL DEFAULT '{}',
  secret_ref TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_voice_profiles_org_provider
ON voice_profiles(organization_id, provider, status);

CREATE TABLE IF NOT EXISTS member_voice_bindings (
  binding_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  member_id TEXT NOT NULL,
  voice_profile_id TEXT NOT NULL,
  binding_scope TEXT NOT NULL DEFAULT 'default',
  reply_mode TEXT NOT NULL DEFAULT 'explicit_request_only',
  priority INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(voice_profile_id) REFERENCES voice_profiles(voice_profile_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_member_voice_bindings_active_scope
ON member_voice_bindings(member_id, binding_scope, status)
WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_member_voice_bindings_profile
ON member_voice_bindings(voice_profile_id, status);

CREATE TABLE IF NOT EXISTS voice_render_jobs (
  render_job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  member_id TEXT NOT NULL,
  conversation_id TEXT,
  turn_id TEXT,
  message_id TEXT,
  voice_profile_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  provider_voice_id TEXT NOT NULL,
  status TEXT NOT NULL,
  source_text_hash TEXT NOT NULL,
  source_text_preview TEXT NOT NULL DEFAULT '',
  voice_style_plan_json TEXT NOT NULL DEFAULT '{}',
  output_uri TEXT,
  output_content_type TEXT,
  output_size_bytes INTEGER,
  checksum TEXT,
  provider_job_id TEXT,
  provider_response_json TEXT NOT NULL DEFAULT '{}',
  degraded_reason TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(voice_profile_id) REFERENCES voice_profiles(voice_profile_id)
);

CREATE INDEX IF NOT EXISTS idx_voice_render_jobs_turn
ON voice_render_jobs(turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_voice_render_jobs_member
ON voice_render_jobs(member_id, created_at);

ALTER TABLE messages ADD COLUMN voice_profile_id TEXT;
ALTER TABLE messages ADD COLUMN voice_render_job_id TEXT;
ALTER TABLE messages ADD COLUMN audio_uri TEXT;
ALTER TABLE messages ADD COLUMN audio_content_type TEXT;
ALTER TABLE messages ADD COLUMN voice_metadata_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_messages_voice_render
ON messages(voice_render_job_id);
