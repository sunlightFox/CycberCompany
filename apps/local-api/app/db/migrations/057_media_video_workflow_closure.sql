CREATE TABLE IF NOT EXISTS media_video_workflows (
  workflow_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  media_id TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  profile_json TEXT NOT NULL DEFAULT '{}',
  edit_plan_id TEXT,
  approval_id TEXT,
  result_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id),
  FOREIGN KEY(edit_plan_id) REFERENCES media_edit_plans(edit_plan_id)
);

CREATE INDEX IF NOT EXISTS idx_media_video_workflows_task
ON media_video_workflows(task_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_media_video_workflows_media
ON media_video_workflows(media_id, status, created_at);

CREATE TABLE IF NOT EXISTS media_video_workflow_steps (
  step_id TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  media_id TEXT NOT NULL,
  step_key TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 1,
  input_json TEXT NOT NULL DEFAULT '{}',
  output_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_summary TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(workflow_id) REFERENCES media_video_workflows(workflow_id),
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id)
);

CREATE INDEX IF NOT EXISTS idx_media_video_workflow_steps_workflow
ON media_video_workflow_steps(workflow_id, step_key, attempt);

CREATE TABLE IF NOT EXISTS media_video_workflow_benchmarks (
  benchmark_id TEXT PRIMARY KEY,
  workflow_id TEXT,
  organization_id TEXT NOT NULL,
  task_id TEXT,
  scenario_key TEXT NOT NULL,
  layer TEXT NOT NULL,
  expected_result_json TEXT NOT NULL DEFAULT '{}',
  observed_result_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(workflow_id) REFERENCES media_video_workflows(workflow_id)
);

CREATE INDEX IF NOT EXISTS idx_media_video_workflow_benchmarks_scenario
ON media_video_workflow_benchmarks(scenario_key, status, created_at);
