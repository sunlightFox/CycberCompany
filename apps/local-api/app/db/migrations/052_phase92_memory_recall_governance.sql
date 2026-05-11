ALTER TABLE memory_items ADD COLUMN memory_class TEXT NOT NULL DEFAULT 'fact';
ALTER TABLE memory_items ADD COLUMN scope_policy TEXT NOT NULL DEFAULT 'member_cross_session';
ALTER TABLE memory_items ADD COLUMN durability TEXT NOT NULL DEFAULT 'durable';
ALTER TABLE memory_items ADD COLUMN freshness_state TEXT NOT NULL DEFAULT 'fresh';
ALTER TABLE memory_items ADD COLUMN superseded_by TEXT;
ALTER TABLE memory_items ADD COLUMN expires_at TEXT;
ALTER TABLE memory_items ADD COLUMN stale_after TEXT;
ALTER TABLE memory_items ADD COLUMN evidence_strength REAL NOT NULL DEFAULT 0.5;

ALTER TABLE memory_retrieval_logs ADD COLUMN recall_scope_applied TEXT NOT NULL DEFAULT 'member_cross_session';
ALTER TABLE memory_retrieval_logs ADD COLUMN request_filters_json TEXT NOT NULL DEFAULT '{}';

UPDATE memory_items
SET memory_class = CASE
      WHEN kind IN ('preference', 'correction') THEN 'preference'
      WHEN kind IN ('project_fact', 'knowledge_fact', 'semantic_note') THEN 'fact'
      WHEN kind IN ('task_experience', 'episodic_experience', 'task_failure_experience') THEN 'experience'
      WHEN kind IN ('skill_candidate', 'procedural_experience') THEN 'experience'
      ELSE CASE
        WHEN layer IN ('working', 'session', 'temporal') THEN 'transient_working_state'
        WHEN layer IN ('episodic', 'procedural') THEN 'experience'
        ELSE 'fact'
      END
    END,
    scope_policy = CASE
      WHEN scope_type = 'asset' THEN 'asset_scoped'
      WHEN scope_type = 'organization' THEN 'organization_shared'
      WHEN scope_type = 'conversation' THEN 'current_conversation'
      ELSE 'member_cross_session'
    END,
    durability = CASE
      WHEN layer IN ('working', 'session', 'temporal') THEN 'transient'
      WHEN kind = 'correction' THEN 'durable'
      WHEN retention_policy IN ('persistent', 'review_required') THEN 'durable'
      WHEN status IN ('superseded', 'archived') OR valid_to IS NOT NULL THEN 'expiring'
      ELSE 'durable'
    END,
    freshness_state = CASE
      WHEN status = 'superseded' THEN 'superseded'
      WHEN valid_to IS NOT NULL AND valid_to <= updated_at THEN 'expired'
      WHEN status IN ('archived', 'deleted') THEN 'stale'
      ELSE 'fresh'
    END,
    superseded_by = json_extract(metadata_json, '$.superseded_by'),
    expires_at = valid_to,
    stale_after = CASE
      WHEN layer IN ('working', 'session', 'temporal') THEN updated_at
      ELSE NULL
    END,
    evidence_strength = MIN(
      1.0,
      MAX(
        0.05,
        COALESCE(quality_score, importance, 0.5) * 0.65 + COALESCE(confidence, 0.5) * 0.35
      )
    );

CREATE INDEX IF NOT EXISTS idx_memory_items_phase92_class_scope
  ON memory_items(memory_class, scope_policy, freshness_state);

CREATE INDEX IF NOT EXISTS idx_memory_items_phase92_member_freshness
  ON memory_items(member_id, durability, freshness_state, updated_at);

CREATE INDEX IF NOT EXISTS idx_memory_items_phase92_superseded_by
  ON memory_items(superseded_by);
