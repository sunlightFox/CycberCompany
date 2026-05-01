CREATE TABLE IF NOT EXISTS media_assets (
  media_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  source_artifact_id TEXT NOT NULL,
  media_type TEXT NOT NULL,
  display_name TEXT NOT NULL,
  uri TEXT NOT NULL,
  content_type TEXT,
  size_bytes INTEGER,
  checksum TEXT,
  duration_ms INTEGER,
  width INTEGER,
  height INTEGER,
  frame_rate REAL,
  audio_streams INTEGER NOT NULL DEFAULT 0,
  video_streams INTEGER NOT NULL DEFAULT 0,
  sensitivity TEXT NOT NULL DEFAULT 'low',
  status TEXT NOT NULL DEFAULT 'ready',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(source_artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_media_assets_task
ON media_assets(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_media_assets_source
ON media_assets(source_artifact_id);

CREATE INDEX IF NOT EXISTS idx_media_assets_type_status
ON media_assets(media_type, status);

CREATE TABLE IF NOT EXISTS media_derivatives (
  derivative_id TEXT PRIMARY KEY,
  media_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  derivative_type TEXT NOT NULL,
  time_ms INTEGER,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_media_derivatives_media
ON media_derivatives(media_id, derivative_type, created_at);

CREATE INDEX IF NOT EXISTS idx_media_derivatives_artifact
ON media_derivatives(artifact_id);

CREATE TABLE IF NOT EXISTS media_analysis (
  analysis_id TEXT PRIMARY KEY,
  media_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  analysis_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'completed',
  model_route TEXT,
  segments_json TEXT NOT NULL DEFAULT '[]',
  transcript_artifact_id TEXT,
  evidence_artifact_ids_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(transcript_artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_media_analysis_media
ON media_analysis(media_id, analysis_type, created_at);

CREATE TABLE IF NOT EXISTS media_edit_plans (
  edit_plan_id TEXT PRIMARY KEY,
  media_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  goal TEXT NOT NULL,
  output_profile_json TEXT NOT NULL DEFAULT '{}',
  operations_json TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'planned',
  risk_level TEXT NOT NULL DEFAULT 'R3',
  requires_approval INTEGER NOT NULL DEFAULT 1,
  artifact_id TEXT,
  rendered_media_id TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(artifact_id) REFERENCES task_artifacts(artifact_id),
  FOREIGN KEY(rendered_media_id) REFERENCES media_assets(media_id)
);

CREATE INDEX IF NOT EXISTS idx_media_edit_plans_media
ON media_edit_plans(media_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_media_edit_plans_task
ON media_edit_plans(task_id, created_at);
