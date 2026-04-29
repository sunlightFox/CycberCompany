ALTER TABLE low_confidence_decision_reviews ADD COLUMN semantic_review_id TEXT;
ALTER TABLE low_confidence_decision_reviews ADD COLUMN model_assist_attempted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE low_confidence_decision_reviews ADD COLUMN schema_valid INTEGER;
ALTER TABLE low_confidence_decision_reviews ADD COLUMN fallback_reason TEXT;
ALTER TABLE low_confidence_decision_reviews ADD COLUMN risk_guard_applied INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS semantic_review_requests (
  semantic_review_id TEXT PRIMARY KEY,
  brain_decision_id TEXT,
  turn_id TEXT,
  conversation_id TEXT,
  member_id TEXT NOT NULL,
  privacy_level TEXT NOT NULL DEFAULT 'medium',
  privacy_policy TEXT NOT NULL DEFAULT 'local_only',
  trigger_reasons_json TEXT NOT NULL DEFAULT '[]',
  redacted_request_json TEXT NOT NULL DEFAULT '{}',
  capability_boundary_summary_json TEXT NOT NULL DEFAULT '{}',
  risk_signal_summary_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(brain_decision_id) REFERENCES brain_decision_logs(brain_decision_id),
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_review_requests_turn
ON semantic_review_requests(turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_semantic_review_requests_decision
ON semantic_review_requests(brain_decision_id);

CREATE INDEX IF NOT EXISTS idx_semantic_review_requests_status
ON semantic_review_requests(status, created_at);

CREATE TABLE IF NOT EXISTS semantic_review_suggestions (
  suggestion_id TEXT PRIMARY KEY,
  semantic_review_id TEXT NOT NULL,
  source TEXT NOT NULL,
  suggestion_json TEXT NOT NULL DEFAULT '{}',
  confidence REAL NOT NULL DEFAULT 0.0,
  schema_valid INTEGER NOT NULL DEFAULT 0,
  rejected_reasons_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  FOREIGN KEY(semantic_review_id) REFERENCES semantic_review_requests(semantic_review_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_review_suggestions_review
ON semantic_review_suggestions(semantic_review_id, created_at);

CREATE TABLE IF NOT EXISTS semantic_review_model_calls (
  model_call_id TEXT PRIMARY KEY,
  semantic_review_id TEXT NOT NULL,
  brain_id TEXT,
  provider TEXT,
  model_name TEXT,
  adapter_name TEXT NOT NULL,
  status TEXT NOT NULL,
  fallback_used INTEGER NOT NULL DEFAULT 1,
  fallback_reason TEXT,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  usage_json TEXT NOT NULL DEFAULT '{}',
  schema_valid INTEGER NOT NULL DEFAULT 0,
  error_code TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(semantic_review_id) REFERENCES semantic_review_requests(semantic_review_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_review_model_calls_review
ON semantic_review_model_calls(semantic_review_id, created_at);

CREATE INDEX IF NOT EXISTS idx_semantic_review_model_calls_status
ON semantic_review_model_calls(status, created_at);

CREATE TABLE IF NOT EXISTS semantic_review_merge_results (
  merge_id TEXT PRIMARY KEY,
  semantic_review_id TEXT NOT NULL,
  brain_decision_id TEXT,
  merged_intent_json TEXT NOT NULL DEFAULT '{}',
  merged_mode_json TEXT NOT NULL DEFAULT '{}',
  merged_context_json TEXT NOT NULL DEFAULT '{}',
  merged_clarification_json TEXT NOT NULL DEFAULT '{}',
  reason_codes_json TEXT NOT NULL DEFAULT '[]',
  risk_monotonic_guard_applied INTEGER NOT NULL DEFAULT 0,
  unsafe_downgrade_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(semantic_review_id) REFERENCES semantic_review_requests(semantic_review_id),
  FOREIGN KEY(brain_decision_id) REFERENCES brain_decision_logs(brain_decision_id)
);

CREATE INDEX IF NOT EXISTS idx_semantic_review_merge_results_review
ON semantic_review_merge_results(semantic_review_id, created_at);

CREATE INDEX IF NOT EXISTS idx_semantic_review_merge_results_decision
ON semantic_review_merge_results(brain_decision_id);
