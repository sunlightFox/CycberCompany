CREATE TABLE IF NOT EXISTS soul_manifests (
  soul_manifest_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  member_id TEXT NOT NULL,
  file_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  compiled_profile_id TEXT,
  compiled_snapshot_json TEXT NOT NULL DEFAULT '{}',
  validation_status TEXT NOT NULL DEFAULT 'unknown',
  validation_errors_json TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT 'file',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  compiled_at TEXT,
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(compiled_profile_id) REFERENCES persona_profiles(persona_profile_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_soul_manifests_member
ON soul_manifests(member_id);

CREATE INDEX IF NOT EXISTS idx_soul_manifests_hash
ON soul_manifests(content_hash, validation_status);

CREATE INDEX IF NOT EXISTS idx_soul_manifests_compiled_profile
ON soul_manifests(compiled_profile_id);
