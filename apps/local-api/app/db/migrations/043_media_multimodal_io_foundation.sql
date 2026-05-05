ALTER TABLE media_assets ADD COLUMN io_role TEXT NOT NULL DEFAULT 'input';
ALTER TABLE media_assets ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'task_artifact';
ALTER TABLE media_assets ADD COLUMN privacy_level TEXT NOT NULL DEFAULT 'standard';
ALTER TABLE media_assets ADD COLUMN provider_status TEXT NOT NULL DEFAULT 'local';
ALTER TABLE media_assets ADD COLUMN replay_summary_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_media_assets_io_role
ON media_assets(task_id, io_role, media_type, created_at);

CREATE TABLE IF NOT EXISTS media_provider_health_records (
  health_record_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  provider_name TEXT NOT NULL,
  capability TEXT NOT NULL,
  provider_type TEXT NOT NULL DEFAULT 'local',
  status TEXT NOT NULL,
  degraded_reason TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  checked_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_media_provider_health_capability
ON media_provider_health_records(capability, provider_name, checked_at);

CREATE TABLE IF NOT EXISTS media_io_requests (
  io_request_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  task_id TEXT,
  media_id TEXT,
  operation TEXT NOT NULL,
  direction TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  status TEXT NOT NULL,
  degraded_reason TEXT,
  input_artifact_id TEXT,
  output_artifact_id TEXT,
  summary_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  redaction_summary_json TEXT NOT NULL DEFAULT '{}',
  idempotency_key TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id),
  FOREIGN KEY(input_artifact_id) REFERENCES task_artifacts(artifact_id),
  FOREIGN KEY(output_artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_media_io_requests_idempotency
ON media_io_requests(idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_media_io_requests_media
ON media_io_requests(media_id, operation, created_at);

CREATE INDEX IF NOT EXISTS idx_media_io_requests_task
ON media_io_requests(task_id, created_at);

CREATE TABLE IF NOT EXISTS media_speech_transcripts (
  transcript_id TEXT PRIMARY KEY,
  io_request_id TEXT NOT NULL,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  task_id TEXT NOT NULL,
  media_id TEXT NOT NULL,
  artifact_id TEXT,
  provider_name TEXT NOT NULL,
  language TEXT,
  status TEXT NOT NULL,
  transcript_preview TEXT NOT NULL DEFAULT '',
  summary_text TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(io_request_id) REFERENCES media_io_requests(io_request_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id),
  FOREIGN KEY(artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_media_speech_transcripts_media
ON media_speech_transcripts(media_id, created_at);

CREATE TABLE IF NOT EXISTS media_speech_renders (
  render_id TEXT PRIMARY KEY,
  io_request_id TEXT NOT NULL,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  task_id TEXT NOT NULL,
  media_id TEXT,
  artifact_id TEXT,
  provider_name TEXT NOT NULL,
  voice TEXT,
  output_format TEXT NOT NULL DEFAULT 'wav',
  status TEXT NOT NULL,
  source_text_hash TEXT NOT NULL,
  duration_ms INTEGER,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(io_request_id) REFERENCES media_io_requests(io_request_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id),
  FOREIGN KEY(artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_media_speech_renders_task
ON media_speech_renders(task_id, created_at);

CREATE TABLE IF NOT EXISTS media_multimodal_summaries (
  summary_id TEXT PRIMARY KEY,
  io_request_id TEXT NOT NULL,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  task_id TEXT NOT NULL,
  media_id TEXT NOT NULL,
  provider_name TEXT NOT NULL,
  summary_type TEXT NOT NULL,
  status TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  summary_json TEXT NOT NULL DEFAULT '{}',
  evidence_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(io_request_id) REFERENCES media_io_requests(io_request_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id)
);

CREATE INDEX IF NOT EXISTS idx_media_multimodal_summaries_media
ON media_multimodal_summaries(media_id, summary_type, created_at);

CREATE TABLE IF NOT EXISTS media_chat_bindings (
  binding_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  media_id TEXT,
  io_request_id TEXT,
  channel TEXT,
  conversation_id TEXT,
  turn_id TEXT,
  message_id TEXT,
  channel_event_id TEXT,
  channel_attachment_id TEXT,
  binding_type TEXT NOT NULL,
  status TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id),
  FOREIGN KEY(io_request_id) REFERENCES media_io_requests(io_request_id)
);

CREATE INDEX IF NOT EXISTS idx_media_chat_bindings_turn
ON media_chat_bindings(conversation_id, turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_media_chat_bindings_media
ON media_chat_bindings(media_id, created_at);
