ALTER TABLE plugin_bundles ADD COLUMN extension_id TEXT;
ALTER TABLE plugin_bundles ADD COLUMN package_kind TEXT NOT NULL DEFAULT 'plugin_bundle';
ALTER TABLE plugin_bundles ADD COLUMN source_format TEXT NOT NULL DEFAULT 'cycber_bundle_v1';
ALTER TABLE plugin_bundles ADD COLUMN canonical_version TEXT NOT NULL DEFAULT 'canonical.skill.v1';
ALTER TABLE plugin_bundles ADD COLUMN compatibility_status TEXT NOT NULL DEFAULT 'compatible';
ALTER TABLE plugin_bundles ADD COLUMN compatibility_notes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE plugin_bundles ADD COLUMN binding_status TEXT NOT NULL DEFAULT 'not_required';
ALTER TABLE plugin_bundles ADD COLUMN binding_summary_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE plugin_bundles ADD COLUMN canonical_snapshot_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE skills ADD COLUMN extension_id TEXT;
ALTER TABLE skills ADD COLUMN runtime_kind TEXT NOT NULL DEFAULT 'workflow_bound';
ALTER TABLE skills ADD COLUMN source_format TEXT NOT NULL DEFAULT 'cycber_bundle_v1';
ALTER TABLE skills ADD COLUMN canonical_version TEXT NOT NULL DEFAULT 'canonical.skill.v1';
ALTER TABLE skills ADD COLUMN compatibility_status TEXT NOT NULL DEFAULT 'compatible';
ALTER TABLE skills ADD COLUMN compatibility_notes_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE skills ADD COLUMN binding_status TEXT NOT NULL DEFAULT 'not_required';
ALTER TABLE skills ADD COLUMN binding_summary_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE skills ADD COLUMN instruction_spec_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE skills ADD COLUMN execution_binding_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_plugin_bundles_extension_id
ON plugin_bundles(extension_id);

CREATE INDEX IF NOT EXISTS idx_skills_extension_id
ON skills(extension_id);

CREATE TABLE IF NOT EXISTS extension_packages (
  extension_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  package_kind TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_format TEXT NOT NULL,
  source_uri TEXT,
  manifest_format TEXT,
  canonical_version TEXT NOT NULL,
  compatibility_status TEXT NOT NULL,
  compatibility_notes_json TEXT NOT NULL DEFAULT '[]',
  trust_level TEXT NOT NULL,
  version TEXT,
  permission_envelope_json TEXT NOT NULL DEFAULT '{}',
  manifest_json TEXT NOT NULL DEFAULT '{}',
  canonical_snapshot_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_extension_packages_bundle_id
ON extension_packages(bundle_id);

CREATE TABLE IF NOT EXISTS extension_sources (
  source_id TEXT PRIMARY KEY,
  extension_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_uri TEXT NOT NULL,
  repository_id TEXT,
  package_ref TEXT,
  github_ref TEXT,
  manifest_checksum TEXT,
  source_descriptor_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(extension_id) REFERENCES extension_packages(extension_id)
);

CREATE INDEX IF NOT EXISTS idx_extension_sources_extension_id
ON extension_sources(extension_id);

CREATE TABLE IF NOT EXISTS extension_compatibility_reports (
  report_id TEXT PRIMARY KEY,
  extension_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  source_format TEXT NOT NULL,
  canonical_version TEXT NOT NULL,
  compatibility_status TEXT NOT NULL,
  compatibility_notes_json TEXT NOT NULL DEFAULT '[]',
  missing_items_json TEXT NOT NULL DEFAULT '[]',
  warnings_json TEXT NOT NULL DEFAULT '[]',
  stage TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(extension_id) REFERENCES extension_packages(extension_id)
);

CREATE INDEX IF NOT EXISTS idx_extension_compat_reports_extension_id
ON extension_compatibility_reports(extension_id, created_at DESC);

CREATE TABLE IF NOT EXISTS extension_binding_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  extension_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  skill_id TEXT,
  binding_status TEXT NOT NULL,
  binding_summary_json TEXT NOT NULL DEFAULT '{}',
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(extension_id) REFERENCES extension_packages(extension_id)
);

CREATE INDEX IF NOT EXISTS idx_extension_binding_snapshots_extension_id
ON extension_binding_snapshots(extension_id, created_at DESC);
