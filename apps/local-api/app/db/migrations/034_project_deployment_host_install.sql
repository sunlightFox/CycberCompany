CREATE TABLE IF NOT EXISTS project_workspaces (
  workspace_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT,
  owner_member_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_uri TEXT,
  root_uri TEXT NOT NULL,
  backend_type TEXT NOT NULL,
  status TEXT NOT NULL,
  stack_summary_json TEXT NOT NULL DEFAULT '{}',
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_project_workspaces_owner
ON project_workspaces(organization_id, owner_member_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_project_workspaces_task
ON project_workspaces(task_id);

CREATE TABLE IF NOT EXISTS project_deployments (
  deployment_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  workspace_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  status TEXT NOT NULL,
  backend_type TEXT NOT NULL,
  plan_json TEXT NOT NULL DEFAULT '{}',
  current_step_key TEXT,
  endpoint_json TEXT NOT NULL DEFAULT '{}',
  health_json TEXT NOT NULL DEFAULT '{}',
  failure_reason TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(workspace_id) REFERENCES project_workspaces(workspace_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id)
);

CREATE INDEX IF NOT EXISTS idx_project_deployments_workspace
ON project_deployments(workspace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_project_deployments_task
ON project_deployments(task_id);

CREATE INDEX IF NOT EXISTS idx_project_deployments_status
ON project_deployments(organization_id, status, updated_at);

CREATE TABLE IF NOT EXISTS toolchain_installs (
  toolchain_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  runtime_name TEXT NOT NULL,
  version TEXT NOT NULL,
  install_mode TEXT NOT NULL,
  root_uri TEXT NOT NULL,
  source_uri TEXT,
  checksum TEXT,
  status TEXT NOT NULL,
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(organization_id, runtime_name, version, install_mode)
);

CREATE INDEX IF NOT EXISTS idx_toolchain_installs_lookup
ON toolchain_installs(organization_id, runtime_name, version, status);

CREATE TABLE IF NOT EXISTS host_install_plans (
  host_install_plan_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  requested_software TEXT NOT NULL,
  install_source_json TEXT NOT NULL DEFAULT '{}',
  command_preview_json TEXT NOT NULL DEFAULT '{}',
  impact_summary_json TEXT NOT NULL DEFAULT '{}',
  risk_level TEXT NOT NULL,
  status TEXT NOT NULL,
  approval_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(approval_id) REFERENCES approvals(approval_id)
);

CREATE INDEX IF NOT EXISTS idx_host_install_plans_task
ON host_install_plans(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_host_install_plans_status
ON host_install_plans(organization_id, status, updated_at);

CREATE TABLE IF NOT EXISTS host_install_executions (
  host_install_execution_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  host_install_plan_id TEXT NOT NULL,
  task_id TEXT NOT NULL,
  status TEXT NOT NULL,
  exit_code INTEGER,
  log_artifact_id TEXT,
  version_detected TEXT,
  install_path_summary TEXT,
  failure_reason TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(host_install_plan_id) REFERENCES host_install_plans(host_install_plan_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(log_artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_host_install_executions_plan
ON host_install_executions(host_install_plan_id, created_at);

CREATE TABLE IF NOT EXISTS managed_processes (
  managed_process_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  deployment_id TEXT,
  task_id TEXT NOT NULL,
  workspace_id TEXT,
  process_kind TEXT NOT NULL,
  command_redacted_json TEXT NOT NULL DEFAULT '{}',
  backend_type TEXT NOT NULL,
  status TEXT NOT NULL,
  port INTEGER,
  endpoint_url TEXT,
  log_artifact_id TEXT,
  started_at TEXT,
  stopped_at TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(deployment_id) REFERENCES project_deployments(deployment_id),
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(workspace_id) REFERENCES project_workspaces(workspace_id),
  FOREIGN KEY(log_artifact_id) REFERENCES task_artifacts(artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_managed_processes_deployment
ON managed_processes(deployment_id, status);

CREATE INDEX IF NOT EXISTS idx_managed_processes_task
ON managed_processes(task_id, status);

CREATE TABLE IF NOT EXISTS port_leases (
  port_lease_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  task_id TEXT,
  deployment_id TEXT,
  port INTEGER NOT NULL,
  protocol TEXT NOT NULL,
  status TEXT NOT NULL,
  leased_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(task_id),
  FOREIGN KEY(deployment_id) REFERENCES project_deployments(deployment_id)
);

CREATE INDEX IF NOT EXISTS idx_port_leases_port
ON port_leases(organization_id, port, status);

CREATE INDEX IF NOT EXISTS idx_port_leases_deployment
ON port_leases(deployment_id, status);
